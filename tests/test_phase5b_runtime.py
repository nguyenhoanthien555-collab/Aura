"""
Comprehensive integration tests for AURA 2.0 Phase 5B.

Tests:
1. Status truthfulness (timeouts are TIMEOUT/UNKNOWN, never blind FAILED; postcondition seams)
2. Idempotency & retry enforcement (retryability_of, side_effect constraints)
3. Durable task runtime & checkpointing (SQLite persistence, recovery, resumption)
4. Late device reports handling in DeviceGateway
5. Capability gap detection and live context explanation
6. Local model provider path (ProviderTimeoutError, keyless localhost)
7. Sandbox runner, secret scrubbing, and timeout enforcement
8. Deterministic tool validator (AST security checks, sandbox test execution)
9. Tool builder pipeline (gap -> manifest -> validation -> promotion -> execution -> rollback -> disable/activate)
"""

import os
import time
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from core.capabilities import health, registry as cap_registry
from core.capabilities.factory import register_core_capabilities
from core.capabilities.gap import CapabilityGap, CapabilityGapEngine, GapStatus
from core.capabilities.models import Capability, CapabilityState
from core.cognitive import CognitiveState
from core.ids import new_task_id, new_step_id, new_gap_id
from agent.task_runtime import TaskRuntime, TaskStatus, StepStatus, DurableTask, DurableStep
from brain.providers.custom import CustomProvider
from brain.providers.errors import ProviderTimeoutError, ProviderUnavailableError
from brain.providers.http_chat import HttpChatProvider
from brain.verify.ledger import EvidenceLedger
from memory.models import (
    Base,
    DurableTaskRecord,
    DurableStepRecord,
    ToolProvenanceRecord,
)
from server.device_gateway import DeviceGateway
from tools.base import Tool, ToolRisk, ToolResult, Parameter, ok, fail
from tools.builtins.commands import RunCommandTool, CODE_TIMEOUT
from tools.builder.builder import ToolBuilder, ToolLifecycleState
from tools.builder.manifest import ToolManifest
from tools.builder.validator import ToolValidator, ValidationReport
from tools.executor import ToolExecutor, ToolPolicy
from tools.outcome import Evidence, EvidenceKind, SideEffect, ToolStatus, retryability_of
from tools.providers.android_provider import AndroidProvider, tool_result_from_report
from tools.registry import ToolRegistry
from tools.sandbox.runner import SandboxRunner, SandboxResult


@pytest.fixture
def sqlite_storage(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from memory.sqlite import init_task_tables

    db_file = tmp_path / "test_memory.db"
    test_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    init_task_tables(bind=test_engine)
    test_sessionmaker = sessionmaker(bind=test_engine, expire_on_commit=False)

    class StorageMock:
        def __init__(self):
            self.session = test_sessionmaker
            self.engine = test_engine

    return StorageMock()


# ==============================================================================
# 1. Status Truthfulness & Postconditions
# ==============================================================================

def test_tool_result_unknown_and_timeout_never_ok():
    r1 = ToolResult(ok=True, status=ToolStatus.TIMEOUT.value)
    assert r1.ok is False
    assert r1.status_enum == ToolStatus.TIMEOUT

    r2 = ToolResult(ok=True, status=ToolStatus.UNKNOWN.value)
    assert r2.ok is False
    assert r2.status_enum == ToolStatus.UNKNOWN


def test_android_provider_maps_timeout_and_cancelled():
    timeout_report = {
        "tool": "android.back",
        "ok": False,
        "error": {"code": "TIMEOUT", "message": "Timeout waiting for response"},
    }
    res_timeout = tool_result_from_report(timeout_report)
    assert res_timeout.status == ToolStatus.TIMEOUT.value
    assert res_timeout.error_code == "TIMEOUT"
    assert res_timeout.ok is False

    cancelled_report = {
        "tool": "android.back",
        "ok": False,
        "error": {"code": "CANCELLED", "message": "User cancelled"},
    }
    res_cancelled = tool_result_from_report(cancelled_report)
    assert res_cancelled.status == ToolStatus.CANCELLED.value
    assert res_cancelled.error_code == "CANCELLED"
    assert res_cancelled.ok is False


def test_command_tool_timeout_status_and_code():
    import sys
    tool = RunCommandTool(
        commands={
            "sleep_test": {
                "argv": [sys.executable, "-c", "import time; time.sleep(1)"],
                "description": "Sleeps for a second",
                "timeout": 0.05,
            }
        }
    )
    
    # Run declared sleep command that will definitely time out
    res = tool.execute("sleep_test")
    assert res.status == ToolStatus.TIMEOUT.value
    assert res.error_code == CODE_TIMEOUT
    assert res.ok is False
    assert any(w in res.error.lower() for w in ("stopped", "did not finish", "timed out"))


def test_postcondition_upgrade_on_timeout():
    cap_id = "test.timeout_verified"
    cap_registry.register(
        Capability(capability_id=cap_id, name="Test Timeout Verified", description="Test", category="test")
    )

    class TimeoutThenVerifiedTool(Tool):
        name = "timeout_verified"
        capability = cap_id
        risk = ToolRisk.SAFE
        side_effect = SideEffect.IDEMPOTENT.value

        def execute(self):
            return fail("Timed out waiting", status=ToolStatus.TIMEOUT.value, error_code="TIMEOUT")

        def verify(self):
            # Postcondition observer confirms the state was actually reached
            return True

    reg = ToolRegistry([TimeoutThenVerifiedTool()])
    executor = ToolExecutor(registry=reg, policy=ToolPolicy(enabled=True, allowed=frozenset({"timeout_verified"})))
    
    res = executor.execute("timeout_verified")
    # Verified postcondition upgrades TIMEOUT to SUCCESS with verified evidence
    assert res.status == ToolStatus.SUCCESS.value
    assert res.ok is True
    assert any(e.kind == EvidenceKind.POSTCONDITION and e.verified for e in res.evidence)


def test_postcondition_unverified_preserves_timeout():
    cap_id = "test.timeout_unverified"
    cap_registry.register(
        Capability(capability_id=cap_id, name="Test Timeout Unverified", description="Test", category="test")
    )

    class TimeoutUnverifiedTool(Tool):
        name = "timeout_unverified"
        capability = cap_id
        risk = ToolRisk.SAFE
        side_effect = SideEffect.IDEMPOTENT.value

        def execute(self):
            return fail("Timed out waiting", status=ToolStatus.TIMEOUT.value, error_code="TIMEOUT")

        def verify(self):
            # Observer cannot confirm
            return False

    reg = ToolRegistry([TimeoutUnverifiedTool()])
    executor = ToolExecutor(registry=reg, policy=ToolPolicy(enabled=True, allowed=frozenset({"timeout_unverified"})))
    
    res = executor.execute("timeout_unverified")
    assert res.status == ToolStatus.TIMEOUT.value
    assert res.ok is False
    assert any(e.kind == EvidenceKind.POSTCONDITION and not e.verified for e in res.evidence)


# ==============================================================================
# 2. Idempotency & Retry Enforcement
# ==============================================================================

def test_cognitive_retryability_guards():
    reg = CognitiveState()
    reg.begin_action("send_sms", "123")
    # Ambiguous outcome on non-idempotent action MUST NOT be retried
    assert not reg.should_retry(
        "send_sms",
        "123",
        limit=3,
        side_effect=SideEffect.NON_IDEMPOTENT.value,
        status=ToolStatus.TIMEOUT.value,
    )
    assert not reg.should_retry(
        "send_sms",
        "123",
        limit=3,
        side_effect=SideEffect.NON_IDEMPOTENT.value,
        status=ToolStatus.UNKNOWN.value,
    )

    # Read-only or idempotent can be retried on timeout
    reg.begin_action("read_weather", "hanoi")
    assert reg.should_retry(
        "read_weather",
        "hanoi",
        limit=3,
        side_effect=SideEffect.READ_ONLY.value,
        status=ToolStatus.TIMEOUT.value,
    )
    reg.begin_action("set_volume", "50")
    assert reg.should_retry(
        "set_volume",
        "50",
        limit=3,
        side_effect=SideEffect.IDEMPOTENT.value,
        status=ToolStatus.TIMEOUT.value,
    )

    # Policy denial or missing capability cannot be retried
    assert not reg.should_retry(
        "read_weather",
        "hanoi",
        limit=3,
        side_effect=SideEffect.READ_ONLY.value,
        status=ToolStatus.DENIED.value,
    )
    assert not reg.should_retry(
        "read_weather",
        "hanoi",
        limit=3,
        side_effect=SideEffect.READ_ONLY.value,
        status=ToolStatus.INVALID_ARGUMENTS.value,
    )


# ==============================================================================
# 3. Durable Task Runtime & Checkpointing
# ==============================================================================

def test_task_runtime_lifecycle_and_checkpointing(sqlite_storage):
    runtime = TaskRuntime(session_factory=sqlite_storage.session)
    
    task = runtime.create_task(
        intent="Download and process dataset",
        steps=[
            {"step_id": "step_1", "tool": "echo", "arguments": {"text": "fetch"}},
            {"step_id": "step_2", "tool": "echo", "arguments": {"text": "process"}},
        ],
    )
    assert task.status == TaskStatus.PENDING
    assert len(task.steps) == 2

    # Advance step 1
    next_step = runtime.get_next_step(task.task_id)
    assert next_step.step_id == "step_1"
    
    runtime.record_step_result(
        task.task_id,
        "step_1",
        ToolResult(ok=True, output="fetched", status=ToolStatus.SUCCESS.value),
    )

    # Verify step 1 is marked COMPLETED in database
    with sqlite_storage.session() as s:
        rec = s.query(DurableStepRecord).filter_by(step_id="step_1").first()
        assert rec is not None
        assert rec.status == StepStatus.COMPLETED.value
        assert "fetched" in rec.result_json

    # Step 2 is now pending
    next_step = runtime.get_next_step(task.task_id)
    assert next_step.step_id == "step_2"

    # Complete step 2
    runtime.record_step_result(
        task.task_id,
        "step_2",
        ToolResult(ok=True, output="processed", status=ToolStatus.SUCCESS.value),
    )

    # Task finishes
    task_after = runtime.get_task(task.task_id)
    assert task_after.status == TaskStatus.COMPLETED
    assert runtime.get_next_step(task.task_id) is None


def test_task_runtime_resumption_after_crash(sqlite_storage):
    # Process step 1 under runtime 1
    runtime1 = TaskRuntime(session_factory=sqlite_storage.session)
    task1 = runtime1.create_task(
        intent="Two-step pipeline",
        steps=[
            {"step_id": "s1", "tool": "tool1", "arguments": {}},
            {"step_id": "s2", "tool": "tool2", "arguments": {}},
        ],
    )
    runtime1.record_step_result(
        task1.task_id,
        "s1",
        ToolResult(ok=True, output="step 1 done", status=ToolStatus.SUCCESS.value),
    )

    # Simulate restart by instantiating new runtime instance
    runtime2 = TaskRuntime(session_factory=sqlite_storage.session)
    task_loaded = runtime2.get_task(task1.task_id)
    assert task_loaded is not None
    assert task_loaded.steps[0].status == StepStatus.COMPLETED
    assert task_loaded.steps[1].status == StepStatus.PENDING

    # Resumption skips s1 and returns s2
    next_step = runtime2.get_next_step(task1.task_id)
    assert next_step.step_id == "s2"


def test_task_runtime_cancellation(sqlite_storage):
    runtime = TaskRuntime(session_factory=sqlite_storage.session)
    task = runtime.create_task(
        intent="Long task",
        steps=[{"step_id": "step_a"}, {"step_id": "step_b"}],
    )
    success = runtime.cancel_task(task.task_id, reason="User abort")
    assert success is True

    cancelled = runtime.get_task(task.task_id)
    assert cancelled.status == TaskStatus.CANCELLED
    assert cancelled.steps[0].status == StepStatus.SKIPPED
    assert cancelled.steps[1].status == StepStatus.SKIPPED


# ==============================================================================
# 4. Device Gateway Late Reports
# ==============================================================================

def test_device_gateway_stores_late_reports():
    gateway = DeviceGateway()
    
    # Simulate an invocation that timed out
    gateway._timed_out["inv_expired_42"] = None

    # Client finally sends response after timeout
    late_report = {
        "invocation_id": "inv_expired_42",
        "action": "system.press",
        "status": "SUCCESS",
        "result": "Tapped screen",
    }
    handled = gateway.complete("inv_expired_42", late_report)
    assert handled is True

    # Check that late report was preserved as late evidence
    retrieved = gateway.get_late_report("inv_expired_42")
    assert retrieved == late_report


# ==============================================================================
# 5. Capability Gap Engine & Explanation
# ==============================================================================

def test_capability_gap_engine_detects_gap():
    from core.capabilities.discovery import SkillDiscovery
    discovery = SkillDiscovery()
    engine = CapabilityGapEngine(discovery=discovery)

    gap = engine.detect_gap(
        intent="Draw a 3D spline in blender",
    )
    assert isinstance(gap, CapabilityGap)
    assert "No matching capability" in gap.reason or "missing" in gap.reason.lower()
    assert gap.status == GapStatus.IDENTIFIED.value


def test_desktop_input_synthesizer_health_check():
    register_core_capabilities()
    with patch("tools.builtins.input.default_input_synthesizer", return_value=None):
        res = health.run_check("desktop.input")
        assert res["healthy"] is False
        assert res["state"] == "UNAVAILABLE"


# ==============================================================================
# 6. Local Model Provider Path
# ==============================================================================

def test_http_chat_provider_timeout_error():
    provider = CustomProvider(
        base_url="http://127.0.0.1:11434/v1",
        model="local-model",
        timeout=0.001,
    )
    # Raising TimeoutError from socket should be mapped to ProviderTimeoutError
    with patch("urllib.request.urlopen", side_effect=TimeoutError("Connection timed out")):
        with pytest.raises(ProviderTimeoutError) as exc_info:
            provider.generate("hello")
        assert "timed out" in str(exc_info.value).lower()


def test_http_chat_provider_allows_keyless_localhost():
    # Local loopback URLs should not fail if api_key is empty
    provider = CustomProvider(
        base_url="http://127.0.0.1:8000/v1",
        model="local-model",
    )
    assert provider.api_key == "local"


# ==============================================================================
# 7. Sandbox Runner
# ==============================================================================

def test_sandbox_runner_execution_and_scrubbing():
    runner = SandboxRunner(default_timeout=5.0)

    # Set secret in current env
    os.environ["SUPER_SECRET_KEY"] = "password123"
    try:
        env = runner.child_environment()
        assert "SUPER_SECRET_KEY" not in env

        res = runner.execute_code("import os; print(os.environ.get('SUPER_SECRET_KEY', 'CLEAN'))")
        assert res.ok is True
        assert "CLEAN" in res.stdout
    finally:
        os.environ.pop("SUPER_SECRET_KEY", None)


def test_sandbox_runner_timeout():
    runner = SandboxRunner(default_timeout=0.2)
    res = runner.execute_code("import time; time.sleep(1)")
    assert res.ok is False
    assert res.timed_out is True
    assert res.exit_code == -1
    assert "timed out" in res.error.lower()


# ==============================================================================
# 8. Deterministic Validator
# ==============================================================================

def test_validator_rejects_forbidden_imports_and_calls():
    validator = ToolValidator()

    # Disallowed ctypes
    bad_manifest1 = ToolManifest(
        name="bad_tool_1",
        source_code="import ctypes\nclass T:\n    risk='safe'\n    def execute(self): pass\n",
    )
    rep1 = validator.validate(bad_manifest1)
    assert rep1.passed is False
    assert any("ctypes" in err for err in rep1.errors)

    # Disallowed eval
    bad_manifest2 = ToolManifest(
        name="bad_tool_2",
        source_code="class T:\n    risk='safe'\n    def execute(self, cmd): eval(cmd)\n",
    )
    rep2 = validator.validate(bad_manifest2)
    assert rep2.passed is False
    assert any("eval" in err for err in rep2.errors)


def test_validator_runs_test_harness():
    validator = ToolValidator()

    code = """
from tools.base import Tool, ToolRisk
class MathMulTool(Tool):
    name = "math_mul"
    risk = ToolRisk.SAFE
    def execute(self, a: int, b: int) -> int:
        return int(a) * int(b)
"""
    test_code = """
tool = MathMulTool()
res = tool.execute(3, 4)
assert res == 12, f"Expected 12, got {res}"
print("TEST OK")
"""
    manifest = ToolManifest(
        name="math_mul",
        source_code=code,
        test_code=test_code,
        risk="safe",
        side_effect="READ_ONLY",
    )
    report = validator.validate(manifest)
    assert report.passed is True
    assert len(report.errors) == 0


# ==============================================================================
# 9. Tool Builder End-to-End Pipeline
# ==============================================================================

def test_tool_builder_pipeline_and_version_lifecycle(sqlite_storage):
    registry = ToolRegistry()
    builder = ToolBuilder(registry=registry, session_factory=sqlite_storage.session)

    gap = CapabilityGap(
        gap_id="gap_uuid_99",
        requested_capability="calc.factorial",
        intent="Calculate factorial of integer",
        reason="Missing factorial calculation",
        required_input={"n": {"type": "integer"}},
        risk="SAFE",
    )

    v1_source = """
from tools.base import Tool, ToolRisk, Parameter
import math

class FactorialTool(Tool):
    name = "calc_factorial"
    capability = "calc.factorial"
    risk = ToolRisk.SAFE
    parameters = (Parameter(name="n", type="integer"),)

    def execute(self, n: int = 1) -> str:
        return str(math.factorial(int(n)))
"""
    v1_manifest = builder.build_from_gap(gap, source_code=v1_source, version=1)
    rep1 = builder.validate(v1_manifest)
    assert rep1.passed is True

    # Promote v1
    builder.promote(v1_manifest, rep1)
    assert registry.has("calc_factorial")
    assert registry.is_active("calc_factorial")

    # Run tool via ToolExecutor
    executor = ToolExecutor(
        registry=registry,
        policy=ToolPolicy(enabled=True, allowed=frozenset({"calc_factorial"})),
    )
    res = executor.execute("calc_factorial", {"n": 5})
    assert res.ok is True
    assert res.output == "120"

    # Upgrade to v2 with a custom prefix
    v2_source = """
from tools.base import Tool, ToolRisk, Parameter
import math

class FactorialToolV2(Tool):
    name = "calc_factorial"
    capability = "calc.factorial"
    risk = ToolRisk.SAFE
    parameters = (Parameter(name="n", type="integer"),)

    def execute(self, n: int = 1) -> str:
        return f"FACT={math.factorial(int(n))}"
"""
    v2_manifest = builder.build_from_gap(gap, source_code=v2_source, version=2)
    rep2 = builder.validate(v2_manifest)
    assert rep2.passed is True
    builder.promote(v2_manifest, rep2, allow_upgrade=True)

    res_v2 = executor.execute("calc_factorial", {"n": 4})
    assert res_v2.ok is True
    assert res_v2.output == "FACT=24"

    # Test Rollback to v1
    rolled_back = builder.rollback("calc_factorial")
    assert rolled_back is True
    res_v1_again = executor.execute("calc_factorial", {"n": 4})
    assert res_v1_again.ok is True
    assert res_v1_again.output == "24"  # v1 output without 'FACT=' prefix

    # Test Disable
    builder.disable("calc_factorial")
    assert not registry.is_active("calc_factorial")
    assert "calc_factorial" not in executor.available()
    denied_res = executor.execute("calc_factorial", {"n": 4})
    assert denied_res.ok is False
    assert "disabled" in denied_res.error

    # Test Re-activate
    builder.activate("calc_factorial")
    assert registry.is_active("calc_factorial")
    assert "calc_factorial" in executor.available()
    reactivated_res = executor.execute("calc_factorial", {"n": 4})
    assert reactivated_res.ok is True


def test_self_extension_e2e_full_pipeline(sqlite_storage, tmp_path):
    """
    End-to-End Self-Extension Pipeline:
    1. Capability Gap Engine identifies missing capability.
    2. ToolBuilder synthesizes manifest and tool code.
    3. ToolValidator performs AST security check (rejection of malicious code)
       and isolated sandbox tests.
    4. Explicit approval gate validates promotion readiness.
    5. Tool promoted to ToolRegistry.
    6. Executed exclusively through ToolExecutor (authoritative single gate).
    7. Produces ToolResult with Evidence.
    8. Evidence Ledger registers and certifies the claim.
    """
    from core.capabilities.discovery import SkillDiscovery
    discovery = SkillDiscovery()
    gap_engine = CapabilityGapEngine(discovery=discovery)
    gap = gap_engine.detect_gap(
        "Generate a cryptographically safe token",
        required_capability="crypto.token_generator",
    )
    assert gap is not None
    assert gap.required_capability == "crypto.token_generator"

    tool_storage_dir = tmp_path / "dynamic_tools"
    registry = ToolRegistry()
    builder = ToolBuilder(
        registry=registry,
        session_factory=sqlite_storage.session,
    )

    token_tool_source = """
from tools.base import Tool, ToolRisk, ToolResult, Parameter
from tools.outcome import Evidence, EvidenceKind, ToolStatus
import hashlib
import time

class TokenGenTool(Tool):
    name = "crypto_token_generator"
    capability = "crypto.token_generator"
    risk = ToolRisk.SAFE
    parameters = (Parameter(name="seed", type="string", required=False),)

    def execute(self, seed: str = "default_seed") -> ToolResult:
        token = hashlib.sha256(f"{seed}_{time.time()}".encode("utf-8")).hexdigest()[:16]
        ev = Evidence(
            kind=EvidenceKind.OBSERVATION,
            source="crypto.token",
            verified=True,
            detail={"token_prefix": token[:4], "len": len(token)},
        )
        return ToolResult(
            ok=True,
            output=token,
            status=ToolStatus.SUCCESS.value,
            evidence=[ev],
        )
"""
    manifest = builder.build_from_gap(
        gap,
        source_code=token_tool_source,
        version=1,
    )
    assert manifest.name == "crypto_token_generator"
    assert manifest.risk == ToolRisk.SAFE.value

    # Static AST + Sandbox verification
    report = builder.validate(manifest)
    assert report.passed is True
    assert len(report.errors) == 0

    # Check approval state: safe tool is approved
    assert builder.is_approved(manifest) is True

    # Promote to registry
    builder.promote(manifest, report)
    assert registry.has("crypto_token_generator")
    assert registry.is_active("crypto_token_generator")

    # Authoritative execution via ToolExecutor
    executor = ToolExecutor(
        registry=registry,
        policy=ToolPolicy(enabled=True, allowed=frozenset({"crypto_token_generator"})),
    )
    result = executor.execute("crypto_token_generator", {"seed": "my_secret_seed"})
    assert result.ok is True
    assert len(result.output) == 16
    assert len(result.evidence) == 1
    assert result.evidence[0].verified is True

    # Verifier Evidence Ledger verification
    ledger = EvidenceLedger()
    ev_id = ledger.add_tool(tool=result.tool, status=result.status, evidence=result.evidence)
    assert len(ledger.tools) == 1
    assert ledger.tools[0].state == "VERIFIED"
    assert ledger.tools[0].succeeds is True


def test_compound_task_e2e_crash_resumption(sqlite_storage):
    """
    End-to-End Compound Task Resumption after Crash:
    Step 1 (read/safe): Completed
    Step 2 (mutate/unsafe): In flight (RUNNING) when server abruptly crashes.
    On restart:
    - resume_all_active discovers the interrupted mutating step.
    - Mutating step with ambiguous outcome is NOT blindly replayed.
    - Step is marked UNKNOWN and task is preserved in UNKNOWN/RECOVERING state.
    """
    runtime = TaskRuntime(session_factory=sqlite_storage.session)
    task = runtime.create_task(
        intent="Database migration and export",
        steps=[
            {
                "step_id": "step_fetch",
                "name": "fetch_data",
                "tool": "echo",
                "side_effect": SideEffect.READ_ONLY.value,
            },
            {
                "step_id": "step_mutate",
                "name": "apply_mutation",
                "tool": "echo",
                "side_effect": SideEffect.MUTATING.value,
            },
            {
                "step_id": "step_report",
                "name": "send_notification",
                "tool": "echo",
                "side_effect": SideEffect.IDEMPOTENT.value,
            },
        ],
    )

    # Step 1 completes normally
    runtime.record_step_result(
        task.task_id,
        "step_fetch",
        ToolResult(ok=True, output="data fetched", status=ToolStatus.SUCCESS.value),
    )

    # Step 2 begins running, then simulated process crash happens
    runtime.update_step("step_mutate", status=StepStatus.RUNNING.value)
    runtime.update_task_status(task.task_id, status=TaskStatus.RUNNING.value, current_step_id="step_mutate")

    # New runtime instance simulates daemon restart
    restart_runtime = TaskRuntime(session_factory=sqlite_storage.session)
    registry = ToolRegistry()
    executor = ToolExecutor(registry=registry, policy=ToolPolicy(enabled=True))

    resumed_tasks = restart_runtime.resume_all_active(executor)
    assert len(resumed_tasks) == 1
    restarted_task = resumed_tasks[0]

    # Crucial safety invariant: Mutating action is NOT blindly replayed!
    assert restarted_task.status == TaskStatus.UNKNOWN.value
    step2 = restart_runtime.get_step("step_mutate")
    assert step2.status == StepStatus.UNKNOWN.value
    assert "manual verification" in restarted_task.last_error.lower()


def test_ambiguous_mutation_timeout_postcondition_recovery(sqlite_storage):
    """
    End-to-End Postcondition Seam:
    A mutating tool execution times out (BRIDGE_TIMEOUT or MODEL_TIMEOUT).
    Postcondition seam inspects device/subsystem state.
    Because postcondition confirms state mutated successfully,
    result is upgraded to SUCCESS with verified Evidence,
    preventing duplicate destructive mutations.
    """
    class MutatingToolWithPostcondition(Tool):
        name = "toggle_bluetooth"
        capability = "echo"
        risk = ToolRisk.SAFE
        side_effect = SideEffect.MUTATING.value

        def execute(self, enable: bool = True) -> ToolResult:
            return ToolResult(
                ok=False,
                status=ToolStatus.TIMEOUT.value,
                error="Bridge timeout waiting for Bluetooth state confirmation",
                error_code="BRIDGE_TIMEOUT",
                side_effect=SideEffect.MUTATING.value,
            )

        def verify(self, **kwargs) -> bool:
            # Postcondition observer probe confirms mutation succeeded
            return True

    registry = ToolRegistry()
    registry.register(MutatingToolWithPostcondition())

    executor = ToolExecutor(
        registry=registry,
        policy=ToolPolicy(enabled=True, allowed=frozenset({"toggle_bluetooth"})),
    )

    res = executor.execute("toggle_bluetooth", {"enable": True})
    assert res.ok is True
    assert res.status == ToolStatus.SUCCESS.value
    assert any(e.kind == EvidenceKind.POSTCONDITION and e.verified for e in res.evidence)


# ==============================================================================
# 10. Phase 5B Production Hardening Integration Tests
# ==============================================================================

def test_compound_task_parameter_substitution_and_unresolved_error():
    from agent.task_runtime import CompoundTaskPlanner, UnresolvedParameterError

    # 1. Successful nested substitution
    params = {
        "text": "${step_0.output.summary}",
        "count": 5,
        "nested": {"token": "${step_1.output.auth_token}"},
    }
    step_context = {
        "step_0": {"output": {"summary": "AURA is online"}},
        "step_1": {"output": {"auth_token": "secret-token-xyz"}},
    }
    resolved = CompoundTaskPlanner.substitute_parameters(params, step_context)
    assert resolved["text"] == "AURA is online"
    assert resolved["count"] == 5
    assert resolved["nested"]["token"] == "secret-token-xyz"

    # 2. Missing step context raises UnresolvedParameterError
    bad_params = {"val": "${step_missing.output.data}"}
    with pytest.raises(UnresolvedParameterError) as exc_info:
        CompoundTaskPlanner.substitute_parameters(bad_params, step_context)
    assert "step_missing" in str(exc_info.value)


def test_recursive_synthesis_protection_depth_and_forbidden_tokens():
    from tools.builder.builder import RecursiveSynthesisError

    builder = ToolBuilder(registry=ToolRegistry())
    gap = CapabilityGap(
        gap_id=new_gap_id(),
        intent="test recursion",
        requested_capability="custom.tool",
        reason="testing recursion",
    )

    # 1. Synthesis depth exceeded raises RecursiveSynthesisError
    builder.current_depth = 2
    with pytest.raises(RecursiveSynthesisError) as exc:
        builder.build_from_gap(
            gap=gap,
            source_code="def run(): pass",
        )
    assert "depth" in str(exc.value).lower()

    # 2. Forbidden self-extension tokens raise RecursiveSynthesisError
    builder.current_depth = 0
    with pytest.raises(RecursiveSynthesisError) as exc:
        builder.build_from_gap(
            gap=gap,
            source_code="class MyTool:\n    def run(self):\n        return ToolBuilder()",
        )
    assert "forbidden" in str(exc.value).lower()


def test_ast_validator_advanced_security_rules():
    validator = ToolValidator()

    # Disallowed modules
    errors_cffi, _ = validator._inspect_ast("import cffi\ndef run(): pass")
    assert any("cffi" in e.lower() for e in errors_cffi)

    errors_winreg, _ = validator._inspect_ast("import winreg\ndef run(): pass")
    assert any("winreg" in e.lower() for e in errors_winreg)

    errors_pickle, _ = validator._inspect_ast("import pickle\ndef run(): pass")
    assert any("pickle" in e.lower() for e in errors_pickle)

    # Disallowed calls
    errors_compile, _ = validator._inspect_ast("def run():\n    compile('x=1', '', 'exec')")
    assert any("compile" in e.lower() for e in errors_compile)

    errors_import, _ = validator._inspect_ast("def run():\n    __import__('os')")
    assert any("__import__" in e.lower() for e in errors_import)

    # Disallowed attributes
    errors_subclasses, _ = validator._inspect_ast("def run(obj):\n    return obj.__subclasses__()")
    assert any("__subclasses__" in e.lower() for e in errors_subclasses)

    errors_globals, _ = validator._inspect_ast("def run(fn):\n    return fn.__globals__")
    assert any("__globals__" in e.lower() for e in errors_globals)

    errors_code, _ = validator._inspect_ast("def run(fn):\n    return fn.__code__")
    assert any("__code__" in e.lower() for e in errors_code)


def test_late_report_automatic_task_step_settlement(sqlite_storage):
    runtime = TaskRuntime(session_factory=sqlite_storage.session)
    gateway = DeviceGateway(task_runtime=runtime)

    task = runtime.create_task(
        goal="Test late report auto settlement",
        session_id="sess_late",
        steps=[{"step_id": "step_target", "name": "late_step", "tool": "echo"}],
    )
    runtime.update_step("step_target", status=StepStatus.UNKNOWN.value)
    runtime.update_task_status(task.task_id, status=TaskStatus.UNKNOWN.value, current_step_id="step_target")

    # Emulate timed-out invocation in gateway
    from server.device_gateway import PendingInvocation
    inv = PendingInvocation(
        invocation_id="inv_late_99",
        tool="android.tap",
        arguments={"x": 100, "y": 200},
        task_id=task.task_id,
        step_id="step_target",
    )
    gateway._timed_out["inv_late_99"] = inv

    # Complete the late invocation
    late_report = {"tool": "android.tap", "ok": True, "result": {"tapped": True}}
    res = gateway.complete("inv_late_99", late_report)
    assert res is True

    # Assert step was settled to COMPLETED in TaskRuntime
    updated_step = runtime.get_step("step_target")
    assert updated_step.status == StepStatus.COMPLETED.value
    assert updated_step.result.get("tapped") is True
    assert updated_step.result.get("ok") is True


def test_task_runtime_lifecycle_pause_resume_cancel(sqlite_storage):
    runtime = TaskRuntime(session_factory=sqlite_storage.session)
    task = runtime.create_task(
        goal="Lifecycle task",
        session_id="sess_life",
        steps=[
            {"step_id": "s1", "name": "step1", "tool": "echo"},
            {"step_id": "s2", "name": "step2", "tool": "echo"},
        ],
    )
    runtime.update_task_status(task.task_id, status=TaskStatus.RUNNING.value)

    # Pause
    assert runtime.pause_task(task.task_id) is True
    t_paused = runtime.get_task(task.task_id)
    assert t_paused.status == TaskStatus.PAUSED.value

    # Resume
    t_resumed, steps = runtime.resume_task(task.task_id)
    assert t_resumed.status == TaskStatus.READY.value

    # Cancel
    assert runtime.cancel_task(task.task_id, reason="Testing cancel") is True
    t_cancelled = runtime.get_task(task.task_id)
    assert t_cancelled.status == TaskStatus.CANCELLED.value

    # List tasks with filter
    cancelled_tasks = runtime.list_tasks(status=TaskStatus.CANCELLED.value)
    assert any(t.task_id == task.task_id for t in cancelled_tasks)


def test_tool_builder_lifecycle_events_emitted():
    from events.bus import EventBus
    from events.types import ToolEventType, ToolLifecycleEvent

    bus = EventBus()
    events_captured = []
    bus.subscribe(ToolLifecycleEvent, lambda e: events_captured.append(e))

    registry = ToolRegistry()
    builder = ToolBuilder(registry=registry, bus=bus)

    tool_code = """
from tools.base import Tool, ToolRisk, ToolResult, ok

class EventTestTool(Tool):
    name = "event_test_tool"
    capability = "custom"
    risk = ToolRisk.SAFE
    def execute(self) -> ToolResult:
        return ok("done")
"""
    manifest = ToolManifest(
        name="event_test_tool",
        description="tests events",
        version=1,
        source_code=tool_code,
        capability="custom",
        risk="safe",
        side_effect="IDEMPOTENT",
    )
    report = ValidationReport(passed=True)
    builder.promote(manifest, report)

    assert any(e.event_type == ToolEventType.TOOL_PROMOTED for e in events_captured)

    builder.disable("event_test_tool")
    assert any(e.event_type == ToolEventType.TOOL_DISABLED for e in events_captured)

    builder.revoke("event_test_tool", reason="security test")
    assert any(e.event_type == ToolEventType.TOOL_REVOKED for e in events_captured)


def test_agent_api_tasks_and_dynamic_tools_endpoints(sqlite_storage):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server.routes.agent import (
        router as agent_router,
        configure_task_runtime,
        configure_device_registry,
    )
    from server.auth import verify_token

    app = FastAPI()
    app.include_router(agent_router)
    app.dependency_overrides[verify_token] = lambda: "test"

    runtime = TaskRuntime(session_factory=sqlite_storage.session)
    configure_task_runtime(runtime)

    reg = ToolRegistry()
    configure_device_registry(reg)

    with TestClient(app) as client:
        # 1. Create task
        resp = client.post("/api/agent/tasks", json={
            "goal": "Test API Task",
            "session_id": "sess_api",
            "steps": [],
            "run_async": False,
        })
        assert resp.status_code == 200
        data = resp.json()
        task_id = data["task_id"]

        # 2. Get task
        resp_get = client.get(f"/api/agent/tasks/{task_id}")
        assert resp_get.status_code == 200
        assert resp_get.json()["goal"] == "Test API Task"

        # 3. List tasks
        resp_list = client.get("/api/agent/tasks")
        assert resp_list.status_code == 200
        assert any(t["task_id"] == task_id for t in resp_list.json())

        # 4. Pause task (must be RUNNING to pause)
        runtime.update_task_status(task_id, status=TaskStatus.RUNNING.value)
        resp_pause = client.post(f"/api/agent/tasks/{task_id}/pause")
        assert resp_pause.status_code == 200
        assert resp_pause.json()["paused"] is True

        # 5. Cancel task
        resp_cancel = client.post(f"/api/agent/tasks/{task_id}/cancel")
        assert resp_cancel.status_code == 200
        assert resp_cancel.json()["cancelled"] is True

        # 6. Dynamic tools endpoint
        resp_dyn = client.get("/api/agent/tools/dynamic")
        assert resp_dyn.status_code == 200
        assert "tools" in resp_dyn.json()


def test_step_lifecycle_waiting_and_verifying(sqlite_storage):
    from agent.task_runtime import TaskPlan, CompoundTaskPlanner
    runtime = TaskRuntime(session_factory=sqlite_storage.session)

    assert StepStatus.WAITING.value == "WAITING"
    assert StepStatus.VERIFYING.value == "VERIFYING"

    cap_id = "test.step_lifecycle"
    cap_registry.register(Capability(capability_id=cap_id, name="Lifecycle Test", description="Test description", category="test"))

    class LifecycleTool(Tool):
        name = "lifecycle_tool"
        capability = cap_id
        risk = ToolRisk.SAFE
        side_effect = SideEffect.READ_ONLY.value

        def execute(self):
            return ok("step ok")

        def verify(self):
            return True

    reg = ToolRegistry([LifecycleTool()])
    executor = ToolExecutor(registry=reg, policy=ToolPolicy(enabled=True, allowed=frozenset({"lifecycle_tool"})))

    task = runtime.create_task(
        goal="Test Step Lifecycle",
        steps=[{
            "step_id": "step_v1",
            "name": "verify_step",
            "tool": "lifecycle_tool",
            "verification_required": True,
        }],
    )

    final_task = runtime.execute_compound_task(task.task_id, executor)
    assert final_task.status == TaskStatus.COMPLETED.value
    step = runtime.get_step("step_v1")
    assert step.status == StepStatus.COMPLETED.value
    assert any(e["kind"] == EvidenceKind.POSTCONDITION.value for e in step.evidence)


def test_plan_cycle_detection_and_validation(sqlite_storage):
    from agent.task_runtime import TaskPlan, CompoundTaskPlanner

    # 1. Cycle detection: step1 -> step2 -> step1
    cyclic_plan = TaskPlan(
        goal="Cyclic Task",
        steps=[
            {"step_id": "s1", "name": "step1", "tool": "t1", "depends_on": ["s2"]},
            {"step_id": "s2", "name": "step2", "tool": "t2", "depends_on": ["s1"]},
        ]
    )
    with pytest.raises(ValueError, match="cycle detected in task dependencies"):
        CompoundTaskPlanner.validate_plan(cyclic_plan)

    # 2. Self-dependency
    self_dep_plan = TaskPlan(
        goal="Self Dep Task",
        steps=[{"step_id": "s1", "name": "step1", "tool": "t1", "depends_on": ["s1"]}],
    )
    with pytest.raises(ValueError, match="cannot depend on itself"):
        CompoundTaskPlanner.validate_plan(self_dep_plan)

    # 3. Missing dependency
    missing_dep_plan = TaskPlan(
        goal="Missing Dep Task",
        steps=[{"step_id": "s1", "name": "step1", "tool": "t1", "depends_on": ["non_existent"]}],
    )
    with pytest.raises(ValueError, match="depends on non-existent step"):
        CompoundTaskPlanner.validate_plan(missing_dep_plan)

    # 4. Duplicate step ID
    dup_id_plan = TaskPlan(
        goal="Duplicate ID Task",
        steps=[
            {"step_id": "s1", "name": "step1", "tool": "t1"},
            {"step_id": "s1", "name": "step1_again", "tool": "t2"},
        ]
    )
    with pytest.raises(ValueError, match="duplicate step_id"):
        CompoundTaskPlanner.validate_plan(dup_id_plan)

    # 5. Malformed parameter template
    malformed_tpl_plan = TaskPlan(
        goal="Malformed Template Task",
        steps=[{"step_id": "s1", "name": "step1", "tool": "t1", "arguments": {"param": "${bad token}"}}],
    )
    with pytest.raises(ValueError, match="malformed parameter template"):
        CompoundTaskPlanner.validate_plan(malformed_tpl_plan)


def test_task_worker_manager_panic_recovery(sqlite_storage):
    runtime = TaskRuntime(session_factory=sqlite_storage.session)

    class PanickingExecutor:
        def execute(self, tool, args):
            raise RuntimeError("Fatal hardware simulation panic")

    task = runtime.create_task(
        goal="Test Panic",
        steps=[{"step_id": "panic_s1", "name": "panic_step", "tool": "panic_tool"}],
    )

    with patch.object(runtime, "execute_compound_task", side_effect=RuntimeError("Fatal database/runtime crash")):
        thread = runtime.workers.submit(task.task_id, PanickingExecutor())
        thread.join(timeout=3.0)

    # Task status must be FAILED, recovery_state PANIC_RECOVERING, not stuck in RUNNING
    updated_task = runtime.get_task(task.task_id)
    assert updated_task.status == TaskStatus.FAILED.value
    assert "Worker panic" in updated_task.last_error
    assert updated_task.recovery_state == "PANIC_RECOVERING"
    assert not runtime.workers.is_running(task.task_id)


def test_startup_recovery_postcondition_probe(sqlite_storage):
    runtime = TaskRuntime(session_factory=sqlite_storage.session)

    cap_id = "test.recovery_probe"
    cap_registry.register(Capability(capability_id=cap_id, name="Probe Test", description="Test description", category="test"))

    class ProbeTool(Tool):
        name = "probe_tool"
        capability = cap_id
        risk = ToolRisk.DANGEROUS
        side_effect = SideEffect.MUTATING.value

        def __init__(self):
            super().__init__()
            self.state_applied = True

        def execute(self):
            return ok("mutated")

        def verify(self, **args):
            return self.state_applied

    tool_instance = ProbeTool()
    reg = ToolRegistry([tool_instance])
    executor = ToolExecutor(registry=reg, policy=ToolPolicy(enabled=True, allowed=frozenset({"probe_tool"})))

    task = runtime.create_task(
        goal="Test Interrupted Mutation",
        steps=[{"step_id": "probe_s1", "name": "probe_step", "tool": "probe_tool", "side_effect": SideEffect.MUTATING.value}],
    )

    # Simulate server crashed while probe_s1 was RUNNING
    runtime.update_step("probe_s1", status=StepStatus.RUNNING.value)
    runtime.update_task_status(task.task_id, status=TaskStatus.RUNNING.value, current_step_id="probe_s1")

    # On server restart: resume_all_active is called.
    # Because ProbeTool.verify() returns True, the interrupted step is recovered as COMPLETED!
    resumed = runtime.resume_all_active(executor)
    assert len(resumed) == 1
    recovered_task = runtime.get_task(task.task_id)
    assert recovered_task.status == TaskStatus.COMPLETED.value
    recovered_step = runtime.get_step("probe_s1")
    assert recovered_step.status == StepStatus.COMPLETED.value
    assert any(e["kind"] == EvidenceKind.POSTCONDITION.value for e in recovered_step.evidence)


def test_device_gateway_and_task_runtime_late_report_dedup(sqlite_storage):
    runtime = TaskRuntime(session_factory=sqlite_storage.session)
    gw = DeviceGateway()

    task = runtime.create_task(
        goal="Test Late Dedup",
        steps=[{"step_id": "late_s1", "name": "late_step", "tool": "remote_tool", "side_effect": SideEffect.MUTATING.value}],
    )

    # Simulate ambiguous timeout
    runtime.update_step("late_s1", status=StepStatus.UNKNOWN.value)
    runtime.update_task_status(task.task_id, status=TaskStatus.UNKNOWN.value, current_step_id="late_s1")

    # Add invocation to gateway timed_out
    from server.device_gateway import PendingInvocation
    inv = PendingInvocation(
        invocation_id="inv_dedup_1",
        run_id="run_1",
        tool="remote_tool",
        arguments={},
        task_id=task.task_id,
        step_id="late_s1",
    )
    with gw._condition:
        gw._timed_out[inv.invocation_id] = inv

    # First report settles the step
    gw._task_runtime = runtime
    report = {"ok": True, "result": {"data": "verified_late"}}
    res1 = gw.complete("inv_dedup_1", report)
    assert res1 is True
    step_after_first = runtime.get_step("late_s1")
    assert step_after_first.status == StepStatus.COMPLETED.value

    # Duplicate late report should be ignored and not re-settle
    res2 = gw.complete("inv_dedup_1", report)
    assert res2 is True
    step_after_second = runtime.get_step("late_s1")
    assert step_after_second.status == StepStatus.COMPLETED.value


def test_capability_gap_api_endpoint():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server.routes.capabilities import router as cap_router
    from server.auth import verify_token

    app = FastAPI()
    app.include_router(cap_router)
    app.dependency_overrides[verify_token] = lambda: "test"

    with TestClient(app) as client:
        resp = client.get("/api/capabilities/gap?intent=teleport_to_mars")
        assert resp.status_code == 200
        data = resp.json()
        assert "match_state" in data
        assert data["gap"] is not None
        assert "mars" in data["gap"]["requested_capability"] or "reason" in data["gap"]


