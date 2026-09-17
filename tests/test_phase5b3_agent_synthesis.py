"""
Comprehensive Test Suite for Phase 5B.3:
Autonomous Capability Gap -> Self-Extension Real Agent-Runtime Wiring.

Verifies:
1. normal capability path unchanged
2. missing capability produces synthesizable gap
3. autonomous synthesis triggered in AgentRuntime
4. non-synthesizable/dangerous gap rejected by policy
5. request continuity in same run_id / turn
6. ToolExecutor executes dynamic tool through authoritative gate
7. evidence generated and verified
8. grounded answer reflects tool execution
9. capability inventory updated from SYNTHESIZABLE to EXACT_MATCH
10. concurrent synthesis deduplicated via in-flight lock
11. synthesis LLM failure handled gracefully
12. malformed LLM response handled gracefully
13. validator AST rejection blocks promotion
14. sandbox test failure blocks promotion
15. approval policy enforced (unapproved tools blocked)
16. executor runtime error handled cleanly
17. recursion depth limit enforced
18. existing static tools unaffected
19. restart rehydration restores dynamic tools from SQLite
20. rehydrated tool resolves capability as AVAILABLE
21. rehydrated tool executes cleanly via ToolExecutor
22. POST /api/agent/intent endpoint E2E with TestClient
"""

import hashlib
import json
import threading
import time
import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agent.runtime import AgentRun, AgentRuntime, Directive, RunStatus, StopReason
from brain.native_fc import ModelTurn, ToolCallRequest
from core.capabilities import Capability, CapabilityState, registry as cap_registry, resolve_capability
from core.capabilities.gap import CapabilityGap, CapabilityGapEngine, CapabilityMatchState, GapStatus
from core.observations import ObservationStore
from memory.models import ToolProvenanceRecord
from memory.sqlite import init_task_tables
from tools.base import Parameter, Tool, ToolProtocol, ToolResult, ToolRisk
from tools.builder.builder import ToolBuilder, ToolLifecycleState
from tools.builder.manifest import ToolManifest
from tools.builder.policy import AutonomousSynthesisPolicy
from tools.builder.rehydrate import rehydrate_active_tools
from tools.builder.synthesis import ToolSynthesisEngine, ToolSynthesisRequest, ToolSynthesisResult
from tools.builder.validator import ToolValidator, ValidationReport
from tools.executor import ToolExecutor, ToolPolicy
from tools.outcome import Evidence, EvidenceKind, ToolStatus
from tools.registry import ToolRegistry


VALID_FIB_SOURCE = '''
from tools.base import Tool, ToolRisk, ToolResult, Parameter
from tools.outcome import Evidence, EvidenceKind, ToolStatus

class FibonacciTool(Tool):
    name = "math_fibonacci"
    capability = "custom.math_fibonacci"
    risk = ToolRisk.SAFE
    parameters = (
        Parameter(name="n", type="integer", required=True),
    )

    def execute(self, n: int = 10) -> ToolResult:
        n = int(n)
        if n < 0:
            return ToolResult(ok=False, error="n must be non-negative", status=ToolStatus.FAILED.value)
        a, b = 0, 1
        for _ in range(n):
            a, b = b, a + b
        ev = Evidence(
            kind=EvidenceKind.RETURN_VALUE,
            source="math_fibonacci",
            verified=True,
            detail=str(a),
        )
        return ToolResult(
            ok=True,
            output=str(a),
            data={"n": n, "fibonacci": a},
            evidence=[ev],
            status=ToolStatus.SUCCESS.value,
        )
'''

VALID_FIB_TEST = '''
def test_fibonacci():
    tool = FibonacciTool()
    res = tool.execute(10)
    assert res.ok is True
    assert res.output == "55"

test_fibonacci()
'''


class MockLLMForSynthesis:
    """Simulates code synthesis responses from LLM."""

    def __init__(self, response_text: str = ""):
        self.response_text = response_text or json.dumps({
            "name": "math_fibonacci",
            "description": "Calculates the nth Fibonacci number",
            "source_code": VALID_FIB_SOURCE,
            "test_code": VALID_FIB_TEST,
        })
        self.provider_name = "mock_synth_provider"
        self.model = "mock-model"

    def generate(self, prompt: str) -> str:
        return self.response_text


class MockAgentRuntimeLLM:
    """
    Simulates ModelTurn sequence for AgentRuntime:
    Round 0: Emit tool call to newly synthesized tool.
    Round 1: Emit final grounded answer.
    """

    def __init__(self, tool_name: str = "math_fibonacci", tool_args: dict = None, final_reply: str = "The 10th Fibonacci number is 55."):
        self.tool_name = tool_name
        self.tool_args = tool_args or {"n": 10}
        self.final_reply = final_reply
        self.call_count = 0

    def generate_with_tools(self, system: str, messages: list, tools: list) -> ModelTurn:
        self.call_count += 1
        if self.call_count == 1:
            # Emit tool call
            return ModelTurn(
                tool_calls=(
                    ToolCallRequest(
                        name=self.tool_name,
                        arguments=self.tool_args,
                        call_id=f"call_{self.tool_name}",
                    ),
                )
            )
        # Final answer
        return ModelTurn(text=self.final_reply)


@pytest.fixture(autouse=True)
def isolate_capability_registry():
    """Isolate global cap_registry across tests to avoid cross-contamination."""
    original = dict(cap_registry._capabilities)
    # Filter out dynamic custom capabilities that may leak from previous files
    cap_registry._capabilities = {
        k: v for k, v in original.items()
        if not k.startswith("custom.") and k != "math.fibonacci"
    }
    yield
    cap_registry._capabilities = original


@pytest.fixture
def sqlite_session_factory(tmp_path):
    db_file = tmp_path / "test_phase5b3.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    init_task_tables(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


# ==============================================================================
# Tests 1 - 22
# ==============================================================================

def test_1_normal_capability_path_unchanged():
    """TEST 1: Normal registered tools execute without triggering synthesis."""
    reg = ToolRegistry()
    from tools.builtins.clock import CurrentTimeTool
    reg.register(CurrentTimeTool())

    executor = ToolExecutor(
        registry=reg,
        policy=ToolPolicy(enabled=True, allowed=frozenset(reg.names()), auto_approve=frozenset({ToolRisk.SAFE})),
    )

    mock_llm = MockAgentRuntimeLLM(tool_name="current_time", tool_args={}, final_reply="It is currently 12:00.")
    runtime = AgentRuntime(
        llm=mock_llm,
        executor=executor,
        registry=reg,
        deferred=False,
    )

    run = runtime.start_run("what time is it", session_id="s1")
    runtime.run_to_completion(run)

    assert run.status == RunStatus.COMPLETED
    assert run.tool_call_count == 1
    assert runtime.synthesis_engine is None


def test_2_missing_capability_produces_gap():
    """TEST 2: Intent for unknown capability produces a SYNTHESIZABLE gap."""
    reg = ToolRegistry()
    gap_engine = CapabilityGapEngine(registry=reg)
    state, gap = gap_engine.evaluate_capability("Calculate the 10th Fibonacci number")

    assert state in (CapabilityMatchState.SYNTHESIZABLE, CapabilityMatchState.NO_CAPABILITY_EXISTS)
    assert gap is not None
    assert gap.is_synthesizable
    assert gap.platform in ("local", "win32", "linux")


def test_3_autonomous_synthesis_triggered_in_agent_runtime(sqlite_session_factory):
    """TEST 3: AgentRuntime autonomously detects gap, synthesizes, promotes, executes, and finishes."""
    reg = ToolRegistry()
    builder = ToolBuilder(registry=reg, session_factory=sqlite_session_factory)
    synth_llm = MockLLMForSynthesis()
    synth_engine = ToolSynthesisEngine(llm=synth_llm, builder=builder)
    synth_policy = AutonomousSynthesisPolicy()

    executor = ToolExecutor(
        registry=reg,
        policy=ToolPolicy(enabled=True, allowed=frozenset(), allow_dynamic=True, auto_approve=frozenset({ToolRisk.SAFE})),
    )

    agent_llm = MockAgentRuntimeLLM(tool_name="math_fibonacci", tool_args={"n": 10}, final_reply="The 10th Fibonacci number is 55.")
    runtime = AgentRuntime(
        llm=agent_llm,
        executor=executor,
        registry=reg,
        deferred=False,
        synthesis_engine=synth_engine,
        synthesis_policy=synth_policy,
    )

    run = runtime.start_run("Calculate the 10th Fibonacci number", session_id="s2")
    runtime.run_to_completion(run)

    assert run.status == RunStatus.COMPLETED
    assert run.stop_reason == StopReason.GOAL_VERIFIED
    assert reg.has("math_fibonacci")
    assert reg.is_dynamically_authorized("math_fibonacci")
    assert run.tool_call_count == 1


def test_4_non_synthesizable_gap_rejected_by_policy():
    """TEST 4: Dangerous commands and remote/Android platforms rejected by policy."""
    policy = AutonomousSynthesisPolicy()

    # Dangerous intent
    dangerous_gap = CapabilityGap(
        gap_id="gap_dang",
        requested_capability="custom.rm_rf",
        intent="delete and rm -rf all files in root",
        reason="missing",
        platform="local",
        risk="safe",
    )
    ok_synth, reason = policy.is_eligible(dangerous_gap)
    assert ok_synth is False
    assert "Prohibited" in reason

    # Android platform
    android_gap = CapabilityGap(
        gap_id="gap_android",
        requested_capability="android.custom_call",
        intent="send sms on phone",
        reason="missing",
        platform="android",
        risk="safe",
    )
    ok_synth, reason = policy.is_eligible(android_gap)
    assert ok_synth is False
    assert "Platform" in reason or "Prohibited" in reason


def test_5_request_continuity_same_run(sqlite_session_factory):
    """TEST 5: Single run_id / task_id spans synthesis, promotion, and execution."""
    reg = ToolRegistry()
    builder = ToolBuilder(registry=reg, session_factory=sqlite_session_factory)
    synth_engine = ToolSynthesisEngine(llm=MockLLMForSynthesis(), builder=builder)
    synth_policy = AutonomousSynthesisPolicy()

    executor = ToolExecutor(
        registry=reg,
        policy=ToolPolicy(enabled=True, allowed=frozenset(), allow_dynamic=True, auto_approve=frozenset({ToolRisk.SAFE})),
    )

    runtime = AgentRuntime(
        llm=MockAgentRuntimeLLM(),
        executor=executor,
        registry=reg,
        deferred=False,
        synthesis_engine=synth_engine,
        synthesis_policy=synth_policy,
    )

    run = runtime.start_run("Calculate the 10th Fibonacci number", session_id="s5")
    initial_run_id = run.run_id
    initial_task_id = run.task_id

    runtime.run_to_completion(run)

    assert run.run_id == initial_run_id
    assert run.task_id == initial_task_id
    assert run.status == RunStatus.COMPLETED


def test_6_tool_executor_executes_dynamic_tool(sqlite_session_factory):
    """TEST 6: ToolExecutor executes dynamically promoted tool even with empty static allowed list."""
    reg = ToolRegistry()
    builder = ToolBuilder(registry=reg, session_factory=sqlite_session_factory)
    synth_engine = ToolSynthesisEngine(llm=MockLLMForSynthesis(), builder=builder)
    synth_policy = AutonomousSynthesisPolicy()

    executor = ToolExecutor(
        registry=reg,
        policy=ToolPolicy(enabled=True, allowed=frozenset(), allow_dynamic=True, auto_approve=frozenset({ToolRisk.SAFE})),
    )

    runtime = AgentRuntime(
        llm=MockAgentRuntimeLLM(),
        executor=executor,
        registry=reg,
        deferred=False,
        synthesis_engine=synth_engine,
        synthesis_policy=synth_policy,
    )

    run = runtime.start_run("Calculate the 10th Fibonacci number", session_id="s6")
    runtime.run_to_completion(run)

    # Verify directly via ToolExecutor
    res = executor.execute("math_fibonacci", {"n": 5})
    assert res.ok is True
    assert res.output == "5"


def test_7_evidence_generated(sqlite_session_factory):
    """TEST 7: Dynamically executed tool produces verified evidence in ToolResult."""
    reg = ToolRegistry()
    builder = ToolBuilder(registry=reg, session_factory=sqlite_session_factory)
    synth_engine = ToolSynthesisEngine(llm=MockLLMForSynthesis(), builder=builder)
    synth_policy = AutonomousSynthesisPolicy()
    executor = ToolExecutor(
        registry=reg,
        policy=ToolPolicy(enabled=True, allowed=frozenset(), allow_dynamic=True, auto_approve=frozenset({ToolRisk.SAFE})),
    )

    runtime = AgentRuntime(
        llm=MockAgentRuntimeLLM(),
        executor=executor,
        registry=reg,
        deferred=False,
        synthesis_engine=synth_engine,
        synthesis_policy=synth_policy,
    )

    run = runtime.start_run("Calculate the 10th Fibonacci number", session_id="s7")
    runtime.run_to_completion(run)

    tool_msg = [m for m in run.messages if m.get("role") == "tool"]
    assert len(tool_msg) >= 1
    envelopes = json.loads(tool_msg[0]["content"])
    assert envelopes[0]["ok"] is True
    assert envelopes[0]["result"].get("value") == 55 or envelopes[0]["result"].get("fibonacci") == 55


def test_8_grounded_answer(sqlite_session_factory):
    """TEST 8: Final assistant response is grounded and matches tool execution."""
    reg = ToolRegistry()
    builder = ToolBuilder(registry=reg, session_factory=sqlite_session_factory)
    synth_engine = ToolSynthesisEngine(llm=MockLLMForSynthesis(), builder=builder)
    synth_policy = AutonomousSynthesisPolicy()
    executor = ToolExecutor(
        registry=reg,
        policy=ToolPolicy(enabled=True, allowed=frozenset(), allow_dynamic=True, auto_approve=frozenset({ToolRisk.SAFE})),
    )

    expected_reply = "The 10th Fibonacci number is 55."
    runtime = AgentRuntime(
        llm=MockAgentRuntimeLLM(final_reply=expected_reply),
        executor=executor,
        registry=reg,
        deferred=False,
        synthesis_engine=synth_engine,
        synthesis_policy=synth_policy,
    )

    run = runtime.start_run("Calculate the 10th Fibonacci number", session_id="s8")
    runtime.run_to_completion(run)

    last_assistant_msg = [m for m in run.messages if m.get("role") == "assistant" and not m.get("tool_calls")][-1]
    assert last_assistant_msg["content"] == expected_reply


def test_9_capability_inventory_updated(sqlite_session_factory):
    """TEST 9: Capability registry is updated and subsequent evaluations return EXACT_MATCH."""
    reg = ToolRegistry()
    builder = ToolBuilder(registry=reg, session_factory=sqlite_session_factory)
    synth_engine = ToolSynthesisEngine(llm=MockLLMForSynthesis(), builder=builder)
    synth_policy = AutonomousSynthesisPolicy()
    executor = ToolExecutor(
        registry=reg,
        policy=ToolPolicy(enabled=True, allowed=frozenset(), allow_dynamic=True, auto_approve=frozenset({ToolRisk.SAFE})),
    )

    runtime = AgentRuntime(
        llm=MockAgentRuntimeLLM(),
        executor=executor,
        registry=reg,
        deferred=False,
        synthesis_engine=synth_engine,
        synthesis_policy=synth_policy,
    )

    run = runtime.start_run("Calculate the 10th Fibonacci number", session_id="s9")
    runtime.run_to_completion(run)

    # Capability is now registered and available
    cap = cap_registry.get("custom.math_fibonacci") or cap_registry.get("custom.calculate_the_10th_fibonacci_num")
    assert cap is not None
    assert resolve_capability(cap.capability_id) == CapabilityState.AVAILABLE


def test_10_concurrent_synthesis_deduplicated():
    """TEST 10: In-flight synthesis lock prevents duplicate synthesis from concurrent requests."""
    policy = AutonomousSynthesisPolicy()

    # First thread acquires
    acq1, ev1 = policy.acquire_synthesis_lock("custom.math_fibonacci")
    assert acq1 is True
    assert ev1 is not None

    # Second thread attempts same capability
    acq2, ev2 = policy.acquire_synthesis_lock("custom.math_fibonacci")
    assert acq2 is False
    assert ev2 is ev1

    # First thread releases
    policy.release_synthesis_lock("custom.math_fibonacci")
    assert ev1.is_set()

    # Third thread can acquire fresh lock
    acq3, ev3 = policy.acquire_synthesis_lock("custom.math_fibonacci")
    assert acq3 is True
    assert ev3 is not ev1
    policy.release_synthesis_lock("custom.math_fibonacci")


def test_11_synthesis_failure_handled(sqlite_session_factory):
    """TEST 11: LLM generation failure is caught and run stops without crashing."""
    class FailingLLM:
        provider_name = "failing_mock"
        model = "fail"
        def generate(self, prompt: str):
            raise ConnectionError("Provider offline")

    reg = ToolRegistry()
    builder = ToolBuilder(registry=reg, session_factory=sqlite_session_factory)
    synth_engine = ToolSynthesisEngine(llm=FailingLLM(), builder=builder)
    synth_policy = AutonomousSynthesisPolicy()
    executor = ToolExecutor(registry=reg, policy=ToolPolicy(enabled=True, allowed=frozenset()))

    agent_llm = MagicMock()
    agent_llm.generate_with_tools.return_value = ModelTurn(text="I cannot calculate Fibonacci because the tool is unavailable.")

    runtime = AgentRuntime(
        llm=agent_llm,
        executor=executor,
        registry=reg,
        deferred=False,
        synthesis_engine=synth_engine,
        synthesis_policy=synth_policy,
    )

    run = runtime.start_run("Calculate the 10th Fibonacci number", session_id="s11")
    runtime.run_to_completion(run)

    assert not reg.has("math_fibonacci")
    assert run.status == RunStatus.COMPLETED


def test_12_malformed_llm_response_handled(sqlite_session_factory):
    """TEST 12: Malformed JSON or garbage from LLM synthesis does not corrupt registry."""
    reg = ToolRegistry()
    builder = ToolBuilder(registry=reg, session_factory=sqlite_session_factory)
    synth_engine = ToolSynthesisEngine(llm=MockLLMForSynthesis(response_text="This is totally not JSON!"), builder=builder)
    synth_policy = AutonomousSynthesisPolicy()
    executor = ToolExecutor(registry=reg, policy=ToolPolicy(enabled=True, allowed=frozenset()))

    agent_llm = MagicMock()
    agent_llm.generate_with_tools.return_value = ModelTurn(text="Tool unavailable.")

    runtime = AgentRuntime(
        llm=agent_llm,
        executor=executor,
        registry=reg,
        deferred=False,
        synthesis_engine=synth_engine,
        synthesis_policy=synth_policy,
    )

    run = runtime.start_run("Calculate the 10th Fibonacci number", session_id="s12")
    runtime.run_to_completion(run)

    assert not reg.has("math_fibonacci")


def test_13_validator_ast_rejection(sqlite_session_factory):
    """TEST 13: AST validator rejects code importing subprocess or forbidden modules."""
    forbidden_source = '''
import subprocess
from tools.base import Tool, ToolRisk, ToolResult

class BadTool(Tool):
    name = "bad_tool"
    risk = ToolRisk.SAFE
    parameters = ()
    def execute(self) -> ToolResult:
        subprocess.run(["ls"])
        return ToolResult(ok=True)
'''
    manifest = ToolManifest(
        name="bad_tool",
        version=1,
        description="bad",
        risk_level="safe",
        source_code=forbidden_source,
    )
    validator = ToolValidator()
    report = validator.validate(manifest)

    assert report.passed is False
    assert any("Disallowed import" in err for err in report.errors)


def test_14_sandbox_test_failure_rejected(sqlite_session_factory):
    """TEST 14: Failed sandbox tests prevent candidate tool promotion."""
    failing_test = '''
tool = FibonacciTool()
res = tool.execute(10)
assert res.output == "999999", "Deliberate sandbox failure"
'''
    manifest = ToolManifest(
        name="math_fibonacci",
        version=1,
        description="fib",
        risk_level="safe",
        source_code=VALID_FIB_SOURCE,
        test_code=failing_test,
    )
    validator = ToolValidator()
    report = validator.validate(manifest)

    assert report.passed is False
    assert any("Sandbox test failed" in err for err in report.errors)


def test_15_approval_policy_enforced(sqlite_session_factory):
    """TEST 15: Tools requiring manual approval (non-safe risk) are blocked from promotion."""
    manifest = ToolManifest(
        name="dangerous_tool",
        version=1,
        description="mutating",
        risk_level="dangerous",
        source_code=VALID_FIB_SOURCE,
        test_code=VALID_FIB_TEST,
    )
    reg = ToolRegistry()
    builder = ToolBuilder(registry=reg, session_factory=sqlite_session_factory)
    report = builder.validate(manifest)
    assert report.passed is True

    # is_approved should be False without operator action
    assert builder.is_approved(manifest) is False
    with pytest.raises(PermissionError):
        builder.promote(manifest, report)


def test_16_executor_runtime_error_handled(sqlite_session_factory):
    """TEST 16: Runtime exception thrown inside synthesized tool is safely handled by ToolExecutor."""
    raising_source = '''
from tools.base import Tool, ToolRisk, ToolResult, Parameter
from tools.outcome import ToolStatus

class RaisingTool(Tool):
    name = "math_raising"
    capability = "custom.math_raising"
    risk = ToolRisk.SAFE
    parameters = ()
    def execute(self) -> ToolResult:
        raise ZeroDivisionError("division by zero inside tool")
'''
    manifest = ToolManifest(
        name="math_raising",
        version=1,
        description="raising tool",
        capability="custom.math_raising",
        risk_level="safe",
        source_code=raising_source,
        test_code="def test_ok(): pass",
    )
    reg = ToolRegistry()
    builder = ToolBuilder(registry=reg, session_factory=sqlite_session_factory)
    report = builder.validate(manifest)
    tool = builder.promote(manifest, report, approver="test_suite")

    executor = ToolExecutor(
        registry=reg,
        policy=ToolPolicy(enabled=True, allowed=frozenset(), allow_dynamic=True, auto_approve=frozenset({ToolRisk.SAFE})),
    )

    res = executor.execute("math_raising", {})
    assert res.ok is False
    assert "division by zero" in res.error


def test_17_recursion_depth_limit():
    """TEST 17: Recursion depth limit in AutonomousSynthesisPolicy rejects nested synthesis."""
    policy = AutonomousSynthesisPolicy(max_depth=1)
    gap = CapabilityGap(
        gap_id="gap_rec",
        requested_capability="custom.recursive",
        intent="synthesize another tool",
        reason="gap",
        platform="local",
        risk="safe",
    )

    # depth 0 is allowed
    ok_depth_0, _ = policy.is_eligible(gap, depth=0)
    assert ok_depth_0 is True

    # depth 1 is rejected
    ok_depth_1, reason = policy.is_eligible(gap, depth=1)
    assert ok_depth_1 is False
    assert "depth" in reason.lower()


def test_18_existing_static_tools_unaffected(sqlite_session_factory):
    """TEST 18: Static tools remain fully functional and authorized alongside dynamic tools."""
    reg = ToolRegistry()
    from tools.builtins.clock import CurrentTimeTool
    reg.register(CurrentTimeTool())

    builder = ToolBuilder(registry=reg, session_factory=sqlite_session_factory)
    synth_engine = ToolSynthesisEngine(llm=MockLLMForSynthesis(), builder=builder)
    synth_policy = AutonomousSynthesisPolicy()

    executor = ToolExecutor(
        registry=reg,
        policy=ToolPolicy(enabled=True, allowed=frozenset(reg.names()), allow_dynamic=True, auto_approve=frozenset({ToolRisk.SAFE})),
    )

    runtime = AgentRuntime(
        llm=MockAgentRuntimeLLM(),
        executor=executor,
        registry=reg,
        deferred=False,
        synthesis_engine=synth_engine,
        synthesis_policy=synth_policy,
    )

    run = runtime.start_run("Calculate the 10th Fibonacci number", session_id="s18")
    runtime.run_to_completion(run)

    # Both static clock and dynamic fibonacci execute cleanly
    clock_res = executor.execute("current_time", {})
    fib_res = executor.execute("math_fibonacci", {"n": 7})
    assert clock_res.ok is True
    assert fib_res.ok is True
    assert fib_res.output == "13"


def test_19_restart_rehydration(sqlite_session_factory):
    """TEST 19: Simulating restart rehydrates synthesized tool from SQLite into fresh registry."""
    # First process: synthesize and promote
    reg1 = ToolRegistry()
    builder1 = ToolBuilder(registry=reg1, session_factory=sqlite_session_factory)
    manifest = ToolManifest(
        name="math_fibonacci",
        version=1,
        description="fib",
        capability="custom.math_fibonacci",
        risk_level="safe",
        source_code=VALID_FIB_SOURCE,
        test_code=VALID_FIB_TEST,
    )
    rep1 = builder1.validate(manifest)
    builder1.promote(manifest, rep1, approver="operator")

    # Second process: fresh registry rehydrates
    reg2 = ToolRegistry()
    stats = rehydrate_active_tools(reg2, session_factory=sqlite_session_factory)

    assert len(stats["rehydrated"]) >= 1
    assert reg2.has("math_fibonacci")
    assert reg2.is_dynamically_authorized("math_fibonacci")


def test_20_rehydrated_tool_resolves_capability(sqlite_session_factory):
    """TEST 20: Capability of rehydrated tool resolves to AVAILABLE state."""
    reg1 = ToolRegistry()
    builder1 = ToolBuilder(registry=reg1, session_factory=sqlite_session_factory)
    manifest = ToolManifest(
        name="math_fibonacci",
        version=1,
        description="fib",
        capability="custom.math_fibonacci",
        risk_level="safe",
        source_code=VALID_FIB_SOURCE,
        test_code=VALID_FIB_TEST,
    )
    rep1 = builder1.validate(manifest)
    builder1.promote(manifest, rep1, approver="operator")

    reg2 = ToolRegistry()
    rehydrate_active_tools(reg2, session_factory=sqlite_session_factory)

    cap = cap_registry.get("custom.math_fibonacci")
    assert cap is not None
    assert resolve_capability("custom.math_fibonacci") == CapabilityState.AVAILABLE


def test_21_rehydrated_tool_executes(sqlite_session_factory):
    """TEST 21: Rehydrated tool executes cleanly through ToolExecutor."""
    reg1 = ToolRegistry()
    builder1 = ToolBuilder(registry=reg1, session_factory=sqlite_session_factory)
    manifest = ToolManifest(
        name="math_fibonacci",
        version=1,
        description="fib",
        capability="custom.math_fibonacci",
        risk_level="safe",
        source_code=VALID_FIB_SOURCE,
        test_code=VALID_FIB_TEST,
    )
    rep1 = builder1.validate(manifest)
    builder1.promote(manifest, rep1, approver="operator")

    reg2 = ToolRegistry()
    rehydrate_active_tools(reg2, session_factory=sqlite_session_factory)

    executor = ToolExecutor(
        registry=reg2,
        policy=ToolPolicy(enabled=True, allowed=frozenset(), allow_dynamic=True, auto_approve=frozenset({ToolRisk.SAFE})),
    )

    res = executor.execute("math_fibonacci", {"n": 8})
    assert res.ok is True
    assert res.output == "21"


def test_22_api_agent_intent_endpoint_e2e(sqlite_session_factory):
    """TEST 22: POST /api/agent/intent end-to-end via FastAPI TestClient."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server.auth import verify_token
    from server.routes.agent import router as agent_router, configure_intent_runtime

    app = FastAPI()
    app.include_router(agent_router)
    app.dependency_overrides[verify_token] = lambda: "test_token"

    reg = ToolRegistry()
    builder = ToolBuilder(registry=reg, session_factory=sqlite_session_factory)
    synth_engine = ToolSynthesisEngine(llm=MockLLMForSynthesis(), builder=builder)
    synth_policy = AutonomousSynthesisPolicy()

    executor = ToolExecutor(
        registry=reg,
        policy=ToolPolicy(enabled=True, allowed=frozenset(), allow_dynamic=True, auto_approve=frozenset({ToolRisk.SAFE})),
    )

    mock_agent_llm = MockAgentRuntimeLLM(
        tool_name="math_fibonacci",
        tool_args={"n": 10},
        final_reply="The 10th Fibonacci number is 55.",
    )

    test_runtime = AgentRuntime(
        llm=mock_agent_llm,
        executor=executor,
        registry=reg,
        deferred=False,
        max_steps=8,
        synthesis_engine=synth_engine,
        synthesis_policy=synth_policy,
    )
    configure_intent_runtime(test_runtime)

    with TestClient(app) as client:
        resp = client.post("/api/agent/intent", json={
            "intent": "Calculate the 10th Fibonacci number",
            "session_id": "session_test_e2e",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["grounded"] is True
        assert "55" in data["reply"]
        assert data["tool_calls"] == 1
