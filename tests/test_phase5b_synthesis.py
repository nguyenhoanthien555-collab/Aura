"""
Comprehensive Test Suite for Phase 5B.2:
Autonomous Tool Synthesis & Capability Gap Wiring.

Tests:
1. synthesis request construction
2. provider integration
3. malformed synthesis response
4. invalid manifest
5. invalid source
6. validator rejection (static AST)
7. sandbox rejection (failing tests)
8. successful candidate lifecycle (math.fibonacci)
9. CapabilityGap -> synthesis wiring
10. dynamic promotion
11. ToolExecutor execution
12. evidence generation
13. duplicate synthesis prevention
14. recursion limit
15. restart/re-hydration
16. tampered provenance
17. disabled/revoked persistence
18. provider timeout
19. provider failure
20. synthesis failure recovery
"""

import hashlib
import json
import pytest
from typing import Any, Dict, Optional

from core.capabilities import Capability, registry as capability_registry
from core.capabilities.gap import CapabilityGap, CapabilityGapEngine, CapabilityMatchState, GapStatus
from memory.models import ToolProvenanceRecord
from memory.sqlite import init_task_tables
from tools.base import ToolProtocol, ToolResult, ToolRisk
from tools.builder.builder import ToolBuilder, ToolLifecycleState, RecursiveSynthesisError
from tools.builder.manifest import ToolManifest
from tools.builder.rehydrate import rehydrate_active_tools
from tools.builder.synthesis import (
    ToolSynthesisEngine,
    ToolSynthesisRequest,
    ToolSynthesisResult,
    FORBIDDEN_SYNTHESIS_TOKENS,
)
from tools.builder.validator import ToolValidator, ValidationReport
from tools.executor import ToolExecutor, ToolPolicy
from tools.outcome import EvidenceKind, ToolStatus
from tools.registry import ToolRegistry


VALID_FIB_SOURCE = """
from tools.base import Tool, ToolRisk, ToolResult, Parameter
from tools.outcome import Evidence, EvidenceKind, ToolStatus

class FibonacciTool(Tool):
    name = "math_fibonacci"
    capability = "math.fibonacci"
    risk = ToolRisk.SAFE
    parameters = (
        Parameter(name="n", type="integer", required=True),
    )

    def execute(self, n: int) -> ToolResult:
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
            status=ToolStatus.SUCCESS.value,
            data={"fibonacci": a, "n": n},
            evidence=(ev,),
        )
"""

VALID_FIB_TEST = """
tool = FibonacciTool()
res = tool.execute(n=10)
assert res.ok is True
assert res.output == "55"
assert res.data["fibonacci"] == 55
"""


class MockLLM:
    """Configurable mock LLM for testing autonomous synthesis."""

    def __init__(self, response_text: str = "", should_raise: Optional[Exception] = None):
        self.response_text = response_text
        self.should_raise = should_raise
        self.provider_name = "mock_synthesis_llm"
        self.model = "mock-model-v1"
        self.calls = []

    def generate(self, prompt: str) -> str:
        self.calls.append(prompt)
        if self.should_raise:
            raise self.should_raise
        return self.response_text


@pytest.fixture(autouse=True)
def clean_capabilities():
    """Isolate core.capabilities registry between tests."""
    from core.capabilities import registry as cap_reg
    cap_reg._capabilities = {
        k: v for k, v in cap_reg._capabilities.items()
        if getattr(v, "category", "") != "custom"
        and not k.startswith("custom.")
        and not k.startswith("calc.")
        and k not in ("math.fibonacci", "math_fibonacci")
    }
    initial_keys = set(cap_reg._capabilities.keys())
    yield
    current_keys = set(cap_reg._capabilities.keys())
    for k in current_keys - initial_keys:
        cap_reg._capabilities.pop(k, None)


@pytest.fixture
def sqlite_storage(tmp_path):
    """Provides a fresh isolated SQLite database."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    db_file = tmp_path / "test_synthesis.db"
    engine = create_engine(f"sqlite:///{db_file}")
    init_task_tables(engine)
    session_factory = sessionmaker(bind=engine)

    class StorageMock:
        def __init__(self, sf):
            self.session = sf
    return StorageMock(session_factory)


def test_1_synthesis_request_construction():
    """TEST 1: Synthesis request construction and prompt rendering."""
    req = ToolSynthesisRequest(
        intent="Compute Fibonacci number",
        requested_capability="math.fibonacci",
        tool_name="math_fibonacci",
        input_requirements={"n": {"type": "integer", "description": "Index"}},
        risk_level="safe",
    )
    assert req.intent == "Compute Fibonacci number"
    assert req.requested_capability == "math.fibonacci"

    builder = ToolBuilder(registry=ToolRegistry())
    engine = ToolSynthesisEngine(llm=MockLLM(), builder=builder)
    prompt = engine.build_synthesis_prompt(req)
    assert "candidate will be statically validated" in prompt.lower()
    assert "math.fibonacci" in prompt
    assert "ToolResult" in prompt


def test_2_provider_integration_success():
    """TEST 2: LLM Provider integration producing structured candidate."""
    candidate_json = json.dumps({
        "name": "math_fibonacci",
        "description": "Calculates nth Fibonacci number",
        "source_code": VALID_FIB_SOURCE,
        "test_code": VALID_FIB_TEST,
        "reasoning": "Standard iterative dynamic programming approach.",
    })
    llm = MockLLM(response_text=candidate_json)
    builder = ToolBuilder(registry=ToolRegistry())
    engine = ToolSynthesisEngine(llm=llm, builder=builder)

    req = ToolSynthesisRequest(
        intent="Calculate Fibonacci for 10",
        requested_capability="math.fibonacci",
    )
    result = engine.synthesize(req)
    assert result.success is True
    assert result.manifest is not None
    assert result.manifest.name == "math_fibonacci"
    assert result.source_digest != ""
    assert len(llm.calls) == 1


def test_3_malformed_synthesis_response_handled():
    """TEST 3: Malformed / unparseable response handled gracefully."""
    llm = MockLLM(response_text="Sorry, I am just a language model and cannot write code.")
    builder = ToolBuilder(registry=ToolRegistry())
    engine = ToolSynthesisEngine(llm=llm, builder=builder)

    req = ToolSynthesisRequest(intent="Add numbers", requested_capability="math.adder")
    result = engine.synthesize(req)
    assert result.success is False
    assert "Failed to parse" in result.failure_reason


def test_4_invalid_manifest_rejected():
    """TEST 4: Candidate with invalid identifier or missing code rejected."""
    bad_json = json.dumps({
        "name": "123_invalid_identifier!",
        "description": "Bad tool",
        "source_code": "",
        "test_code": "",
    })
    llm = MockLLM(response_text=bad_json)
    builder = ToolBuilder(registry=ToolRegistry())
    engine = ToolSynthesisEngine(llm=llm, builder=builder)

    req = ToolSynthesisRequest(intent="Do something", requested_capability="bad.tool")
    result = engine.synthesize(req)
    assert result.success is False


def test_5_validator_rejection_forbidden_ast():
    """TEST 5: Static AST rejection of dangerous imports/calls."""
    malicious_source = """
from tools.base import Tool, ToolRisk, ToolResult, Parameter
import subprocess

class MaliciousTool(Tool):
    name = "malicious_tool"
    risk = ToolRisk.SAFE
    def execute(self) -> ToolResult:
        subprocess.run(["cmd.exe", "/c", "dir"])
        return ToolResult(ok=True)
"""
    manifest = ToolManifest(
        name="malicious_tool",
        source_code=malicious_source,
        test_code="",
    )
    validator = ToolValidator()
    report = validator.validate(manifest)
    assert report.passed is False
    assert any("Disallowed import" in err or "subprocess" in err for err in report.errors)


def test_6_sandbox_rejection_failing_unit_tests():
    """TEST 6: Subprocess sandbox rejection of candidate with failing tests."""
    failing_test_source = VALID_FIB_SOURCE
    failing_test_code = """
tool = FibonacciTool()
res = tool.execute(n=10)
assert res.output == "999999"  # Deliberately false assertion!
"""
    manifest = ToolManifest(
        name="math_fibonacci",
        source_code=failing_test_source,
        test_code=failing_test_code,
    )
    validator = ToolValidator()
    report = validator.validate(manifest)
    assert report.passed is False
    assert any("Sandbox test failed" in err for err in report.errors)


def test_7_math_fibonacci_full_lifecycle(sqlite_storage):
    """
    TEST 7 / Phase 8: Canonical math.fibonacci E2E Self-Extension.
    Flow:
    CapabilityGap -> Synthesis -> AST Check -> Sandbox -> Promotion -> ToolExecutor -> Evidence.
    """
    candidate_json = json.dumps({
        "name": "math_fibonacci",
        "description": "Calculates nth Fibonacci number",
        "source_code": VALID_FIB_SOURCE,
        "test_code": VALID_FIB_TEST,
        "reasoning": "Standard iteration.",
    })
    llm = MockLLM(response_text=candidate_json)
    registry = ToolRegistry()
    builder = ToolBuilder(registry=registry, session_factory=sqlite_storage.session)
    engine = ToolSynthesisEngine(llm=llm, builder=builder)

    gap = CapabilityGap(
        gap_id="gap_fib_1",
        intent="Calculate Fibonacci for 10",
        requested_capability="math.fibonacci",
        reason="Missing fibonacci calculation",
        required_input={"n": {"type": "integer"}},
        risk="safe",
    )

    # Execute end-to-end synthesis and promotion
    tool_instance, report, synth_res = engine.synthesize_and_promote(gap, approver="operator_test")
    assert synth_res.success is True
    assert report.passed is True
    assert tool_instance is not None
    assert gap.status == GapStatus.RESOLVED.value

    # Verify registry registration and dynamic authorization
    assert registry.has("math_fibonacci")
    assert registry.is_active("math_fibonacci")
    assert registry.is_dynamically_authorized("math_fibonacci")

    # Verify authoritative execution through ToolExecutor gate
    executor = ToolExecutor(
        registry=registry,
        policy=ToolPolicy(enabled=True, allowed=frozenset()),  # Dynamic tool bypasses static allowlist
    )
    result = executor.execute("math_fibonacci", {"n": 10})
    assert isinstance(result, ToolResult)
    assert result.ok is True
    assert result.output == "55"
    assert result.data["fibonacci"] == 55
    assert len(result.evidence) == 1
    assert result.evidence[0].kind == EvidenceKind.RETURN_VALUE
    assert result.evidence[0].source == "math_fibonacci"


def test_8_duplicate_synthesis_prevented(sqlite_storage):
    """TEST 8: Attempting synthesis for an already registered, active tool is prevented."""
    candidate_json = json.dumps({
        "name": "math_fibonacci",
        "source_code": VALID_FIB_SOURCE,
        "test_code": VALID_FIB_TEST,
    })
    llm = MockLLM(response_text=candidate_json)
    registry = ToolRegistry()
    builder = ToolBuilder(registry=registry, session_factory=sqlite_storage.session)
    engine = ToolSynthesisEngine(llm=llm, builder=builder)

    gap = CapabilityGap(
        gap_id="gap_fib_dup",
        intent="Calculate Fibonacci",
        requested_capability="math.fibonacci",
        reason="Missing",
    )
    # First synthesis succeeds
    tool_inst, report, res = engine.synthesize_and_promote(gap)
    assert tool_inst is not None

    # Second synthesis MUST be rejected by eligibility
    gap2 = CapabilityGap(
        gap_id="gap_fib_dup_2",
        intent="Calculate Fibonacci again",
        requested_capability="math.fibonacci",
        reason="Missing",
    )
    tool_inst2, report2, res2 = engine.synthesize_and_promote(gap2)
    assert tool_inst2 is None
    assert "already exists" in report2.errors[0]


def test_9_recursion_protection_depth_limit(sqlite_storage):
    """TEST 9: Exceeding max_synthesis_depth blocks recursive self-extension."""
    registry = ToolRegistry()
    builder = ToolBuilder(registry=registry, session_factory=sqlite_storage.session, max_synthesis_depth=1)
    builder.current_depth = 1  # At depth limit

    engine = ToolSynthesisEngine(llm=MockLLM(), builder=builder)
    gap = CapabilityGap(
        gap_id="gap_recur",
        intent="Recursive tool",
        requested_capability="recursive.tool",
        reason="Test recursion",
    )
    eligible, reason = engine.is_eligible_for_synthesis(gap)
    assert eligible is False
    assert "Max synthesis depth" in reason


def test_10_restart_rehydration_of_synthesized_tool(sqlite_storage):
    """TEST 10: Synthesized tool survives server restart and rehydrates correctly."""
    candidate_json = json.dumps({
        "name": "math_fibonacci",
        "source_code": VALID_FIB_SOURCE,
        "test_code": VALID_FIB_TEST,
    })
    reg1 = ToolRegistry()
    builder1 = ToolBuilder(registry=reg1, session_factory=sqlite_storage.session)
    engine1 = ToolSynthesisEngine(llm=MockLLM(response_text=candidate_json), builder=builder1)

    gap = CapabilityGap(
        gap_id="gap_fib_persist",
        intent="Calculate Fibonacci",
        requested_capability="math.fibonacci",
        reason="Missing",
    )
    engine1.synthesize_and_promote(gap)

    # Destroy in-memory instances (simulate server crash)
    del reg1
    del builder1
    del engine1

    # Restart: Fresh registry and rehydration
    fresh_reg = ToolRegistry()
    stats = rehydrate_active_tools(registry=fresh_reg, session_factory=sqlite_storage.session)
    assert len(stats["rehydrated"]) == 1
    assert fresh_reg.has("math_fibonacci")
    assert fresh_reg.is_active("math_fibonacci")
    assert fresh_reg.is_dynamically_authorized("math_fibonacci")

    executor = ToolExecutor(registry=fresh_reg, policy=ToolPolicy(enabled=True, allowed=frozenset()))
    res = executor.execute("math_fibonacci", {"n": 7})
    assert res.ok is True
    assert res.output == "13"


def test_11_provider_failure_and_timeout_recovery(sqlite_storage):
    """TEST 11: LLM provider timeout/failure does not crash the system."""
    llm = MockLLM(should_raise=TimeoutError("Inference gateway timed out"))
    builder = ToolBuilder(registry=ToolRegistry(), session_factory=sqlite_storage.session)
    engine = ToolSynthesisEngine(llm=llm, builder=builder)

    gap = CapabilityGap(
        gap_id="gap_timeout",
        intent="Compute something",
        requested_capability="calc.something",
        reason="Missing",
    )
    tool_inst, report, res = engine.synthesize_and_promote(gap)
    assert tool_inst is None
    assert res.success is False
    assert "Inference gateway timed out" in res.failure_reason
    assert gap.status == GapStatus.REJECTED.value


def test_12_invalid_python_source_rejected():
    """TEST 12: Source with invalid Python syntax is rejected during validation."""
    manifest = ToolManifest(
        name="syntax_error_tool",
        source_code="def broken(x: return x +++",
        test_code="",
    )
    validator = ToolValidator()
    report = validator.validate(manifest)
    assert report.passed is False
    assert any("Syntax error" in err for err in report.errors)


def test_13_tampered_synthesized_tool_rejected_on_rehydrate(sqlite_storage):
    """TEST 13: Synthesized tool whose persisted source code is tampered is rejected."""
    candidate_json = json.dumps({
        "name": "math_fibonacci",
        "source_code": VALID_FIB_SOURCE,
        "test_code": VALID_FIB_TEST,
    })
    reg = ToolRegistry()
    builder = ToolBuilder(registry=reg, session_factory=sqlite_storage.session)
    engine = ToolSynthesisEngine(llm=MockLLM(response_text=candidate_json), builder=builder)

    gap = CapabilityGap(
        gap_id="gap_fib_tamper",
        intent="Calculate Fibonacci",
        requested_capability="math.fibonacci",
        reason="Missing",
    )
    engine.synthesize_and_promote(gap)

    # Tamper with the record in SQLite
    with sqlite_storage.session() as session:
        from sqlalchemy import select
        record = session.scalars(select(ToolProvenanceRecord).where(ToolProvenanceRecord.name == "math_fibonacci")).first()
        assert record is not None
        record.source_digest = "0" * 64
        session.commit()

    fresh_reg = ToolRegistry()
    stats = rehydrate_active_tools(registry=fresh_reg, session_factory=sqlite_storage.session)
    assert len(stats["rejected"]) == 1
    assert stats["rejected"][0]["reason"] == "DIGEST_MISMATCH"
    assert not fresh_reg.has("math_fibonacci")


def test_14_disabled_revoked_synthesized_tool_lifecycle(sqlite_storage):
    """TEST 14: Synthesized tool can be disabled and revoked through builder lifecycle."""
    candidate_json = json.dumps({
        "name": "math_fibonacci",
        "source_code": VALID_FIB_SOURCE,
        "test_code": VALID_FIB_TEST,
    })
    reg = ToolRegistry()
    builder = ToolBuilder(registry=reg, session_factory=sqlite_storage.session)
    engine = ToolSynthesisEngine(llm=MockLLM(response_text=candidate_json), builder=builder)

    gap = CapabilityGap(
        gap_id="gap_fib_life",
        intent="Calculate Fibonacci",
        requested_capability="math.fibonacci",
        reason="Missing",
    )
    tool_inst, report, res = engine.synthesize_and_promote(gap)
    assert tool_inst is not None

    executor = ToolExecutor(registry=reg, policy=ToolPolicy(enabled=True, allowed=frozenset()))
    assert executor.check("math_fibonacci") == ""

    # Disable
    builder.disable("math_fibonacci")
    assert not reg.is_active("math_fibonacci")
    denied = executor.execute("math_fibonacci", {"n": 5})
    assert denied.ok is False
    assert "disabled" in denied.error

    # Revoke
    builder.revoke("math_fibonacci", reason="Operator safety revocation")
    assert reg.is_revoked("math_fibonacci")
    revoked_res = executor.execute("math_fibonacci", {"n": 5})
    assert revoked_res.ok is False
    assert "revoked" in revoked_res.error


def test_15_forbidden_self_extension_tokens_rejected():
    """TEST 15: Candidate containing forbidden recursion token (e.g. ToolBuilder) is rejected."""
    bad_code = """
from tools.base import Tool, ToolRisk, ToolResult
class RecursiveTool(Tool):
    name = "recursive_tool"
    risk = ToolRisk.SAFE
    def execute(self) -> ToolResult:
        from tools.builder.builder import ToolBuilder
        return ToolResult(ok=True)
"""
    req = ToolSynthesisRequest(intent="Exploit", requested_capability="exploit.recur")
    builder = ToolBuilder(registry=ToolRegistry())
    engine = ToolSynthesisEngine(llm=MockLLM(response_text=json.dumps({"source_code": bad_code})), builder=builder)
    res = engine.synthesize(req)
    assert res.success is False
    assert "forbidden" in res.failure_reason.lower()


def test_16_api_capabilities_synthesize_endpoint(sqlite_storage):
    """TEST 16: Test /api/capabilities/synthesize endpoint end-to-end with TestClient."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server.auth import verify_token
    from server.routes.capabilities import router as cap_router
    from server.routes.agent import configure_device_registry

    app = FastAPI()
    app.include_router(cap_router)
    app.dependency_overrides[verify_token] = lambda: "test_token"

    test_reg = ToolRegistry()
    configure_device_registry(test_reg)

    with TestClient(app) as client:
        # Evaluate gap
        resp_gap = client.get("/api/capabilities/gap?intent=Calculate+Fibonacci+number+sequence")
        assert resp_gap.status_code == 200
        gap_data = resp_gap.json()
        assert gap_data["gap"] is not None

        # Synthesize capability (mocking provider through BrainRouter or synthesis engine)
        candidate_json = json.dumps({
            "name": "math_fibonacci",
            "source_code": VALID_FIB_SOURCE,
            "test_code": VALID_FIB_TEST,
        })
        from brain.router import BrainRouter
        class FakeMockProvider:
            def generate(self, prompt: str) -> str:
                return candidate_json
        fake_router = BrainRouter(provider=FakeMockProvider(), provider_name="mock")

        # Mock runtime services LLM
        from unittest.mock import patch
        with patch("brain.router.BrainRouter", return_value=fake_router):
            resp_synth = client.post("/api/capabilities/synthesize", json={
                "intent": "Compute Fibonacci sequence for 10",
                "required_capability": "math.fibonacci",
                "approver": "live_operator",
            })
            assert resp_synth.status_code == 200
            data = resp_synth.json()
            assert data["synthesized"] is True
            assert data["tool_name"] == "math_fibonacci"
            assert data["status"] == "ACTIVE"
            assert test_reg.has("math_fibonacci")
            assert test_reg.is_active("math_fibonacci")

