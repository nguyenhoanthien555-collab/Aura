"""
Tests for Phase 5B.1-A/B: Dynamic Tool Runtime Integration.
Verifies:
1. Dynamic Tool Registration & Execution Gate (ToolExecutor recognizes promoted tools)
2. Disabled & Revoked Dynamic Tool rejection
3. Schema and Policy gate enforcement
4. SQLite Persistence & Rehydration across restarts
5. Digest Integrity & Tamper Rejection
6. Corrupt Record Failure Isolation
7. Multi-Version Rehydration Selection
8. Static Tool Regression Safety
9. Durable Compound Task Execution & Resumption with Dynamic Tools
"""

import hashlib
import json
import pytest
from typing import Any

from core.capabilities import Capability, registry as capability_registry
from core.capabilities.gap import CapabilityGap
from memory.models import ToolProvenanceRecord
from memory.sqlite import init_task_tables
from tools.base import Parameter, Tool, ToolProtocol, ToolResult, ToolRisk, ok
from tools.builder.builder import ToolBuilder, ToolLifecycleState
from tools.builder.manifest import ToolManifest
from tools.builder.rehydrate import rehydrate_active_tools
from tools.builder.validator import ToolValidator, ValidationReport
from tools.executor import ToolExecutor, ToolPolicy
from tools.outcome import ToolStatus
from tools.registry import ToolRegistry


SAMPLE_TOOL_SOURCE = """
from tools.base import Tool, ToolRisk, ToolResult, Parameter
from tools.outcome import Evidence, EvidenceKind, ToolStatus

class AdderTool(Tool):
    name = "math_adder"
    capability = "math.adder"
    risk = ToolRisk.SAFE
    parameters = (
        Parameter(name="a", type="integer", required=True),
        Parameter(name="b", type="integer", required=True),
    )

    def execute(self, a: int, b: int) -> ToolResult:
        res = a + b
        ev = Evidence(
            kind=EvidenceKind.RETURN_VALUE,
            source="math_adder",
            verified=True,
            detail=str(res),
        )
        return ToolResult(
            ok=True,
            output=str(res),
            status=ToolStatus.SUCCESS.value,
            data={"sum": res},
            evidence=(ev,),
        )
"""



@pytest.fixture
def sqlite_storage(tmp_path):
    """Provides a fresh isolated SQLite database."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    db_path = tmp_path / "test_dynamic_tools.db"
    test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    init_task_tables(bind=test_engine)
    test_sessionmaker = sessionmaker(bind=test_engine, expire_on_commit=False)

    class StorageMock:
        def __init__(self):
            self.session = test_sessionmaker
            self.engine = test_engine

    return StorageMock()


def test_1_dynamic_tool_registration(sqlite_storage):
    """
    TEST 1 — Dynamic Tool Registration:
    Promote a minimal safe dynamic tool.
    Assert:
    - Tool exists in registry
    - Tool is ACTIVE
    - Executor can resolve it in available() and check()
    """
    registry = ToolRegistry()
    builder = ToolBuilder(registry=registry, session_factory=sqlite_storage.session)

    gap = CapabilityGap(
        gap_id="gap_adder_1",
        intent="Add two numbers",
        requested_capability="math.adder",
        reason="No math tool available",
        required_input={"a": {"type": "integer"}, "b": {"type": "integer"}},
    )

    manifest = builder.build_from_gap(
        gap=gap,
        source_code=SAMPLE_TOOL_SOURCE,
        version=1,
    )

    report = builder.validate(manifest)
    assert report.passed is True

    builder.promote(manifest, report)

    assert registry.has("math_adder")
    assert registry.is_active("math_adder")
    assert registry.is_dynamically_authorized("math_adder")

    # ToolExecutor with empty static allowed must still resolve math_adder because it was dynamically promoted
    executor = ToolExecutor(
        registry=registry,
        policy=ToolPolicy(enabled=True, allowed=frozenset()),
    )

    assert executor.check("math_adder") == ""
    assert "math_adder" in executor.available()


def test_2_dynamic_tool_execution(sqlite_storage):
    """
    TEST 2 — Dynamic Tool Execution:
    Execute through ToolExecutor.execute() (the single gate).
    Assert:
    - No NOT_ALLOWED caused by static allowlist mismatch
    - Schema validation still occurs
    - Result is a real ToolResult
    - Existing policy gates remain active
    """
    registry = ToolRegistry()
    builder = ToolBuilder(registry=registry, session_factory=sqlite_storage.session)

    gap = CapabilityGap(
        gap_id="gap_adder_2",
        intent="Add two numbers",
        requested_capability="math.adder",
        reason="No math tool available",
    )
    manifest = builder.build_from_gap(gap=gap, source_code=SAMPLE_TOOL_SOURCE, version=1)
    report = builder.validate(manifest)
    builder.promote(manifest, report)

    # Empty static allowed list!
    executor = ToolExecutor(
        registry=registry,
        policy=ToolPolicy(enabled=True, allowed=frozenset()),
    )

    # 1. Successful execution through gate
    res = executor.execute("math_adder", {"a": 10, "b": 25})
    assert isinstance(res, ToolResult)
    assert res.ok is True
    assert res.output == "35"
    assert res.data["sum"] == 35
    assert res.status == ToolStatus.SUCCESS.value
    assert len(res.evidence) == 1

    # 2. Schema validation enforcement: invalid argument type
    bad_res = executor.execute("math_adder", {"a": "not_an_int", "b": 25})
    assert bad_res.ok is False
    assert bad_res.status == ToolStatus.INVALID_ARGUMENTS.value
    assert "must be integer" in bad_res.error.lower()

    # 3. Global enablement enforcement
    disabled_executor = ToolExecutor(
        registry=registry,
        policy=ToolPolicy(enabled=False, allowed=frozenset()),
    )
    denied_res = disabled_executor.execute("math_adder", {"a": 1, "b": 2})
    assert denied_res.ok is False
    assert denied_res.status == ToolStatus.DENIED.value
    assert "tool use is disabled" in denied_res.error


def test_3_disabled_tool_rejected(sqlite_storage):
    """
    TEST 3 — Disabled Tool Rejected:
    Promote dynamic tool. Disable it. Attempt execution.
    Assert it is rejected.
    """
    registry = ToolRegistry()
    builder = ToolBuilder(registry=registry, session_factory=sqlite_storage.session)

    gap = CapabilityGap(gap_id="gap_adder_3", intent="Add numbers", requested_capability="math.adder", reason="No math tool available")
    manifest = builder.build_from_gap(gap=gap, source_code=SAMPLE_TOOL_SOURCE, version=1)
    report = builder.validate(manifest)
    builder.promote(manifest, report)

    executor = ToolExecutor(registry=registry, policy=ToolPolicy(enabled=True, allowed=frozenset()))
    assert executor.check("math_adder") == ""

    # Disable tool
    builder.disable("math_adder")
    assert registry.is_active("math_adder") is False

    assert executor.check("math_adder") == "tool is disabled: math_adder"
    res = executor.execute("math_adder", {"a": 1, "b": 2})
    assert res.ok is False
    assert "disabled" in res.error

    # Re-activate and verify it can run again
    builder.activate("math_adder")
    assert executor.check("math_adder") == ""
    res_reactivated = executor.execute("math_adder", {"a": 1, "b": 2})
    assert res_reactivated.ok is True


def test_4_revoked_tool_rejected(sqlite_storage):
    """
    TEST 4 — Revoked Tool Rejected:
    Promote dynamic tool. Revoke it. Attempt execution.
    Assert it is permanently rejected and cannot be activated.
    """
    registry = ToolRegistry()
    builder = ToolBuilder(registry=registry, session_factory=sqlite_storage.session)

    gap = CapabilityGap(gap_id="gap_adder_4", intent="Add numbers", requested_capability="math.adder", reason="No math tool available")
    manifest = builder.build_from_gap(gap=gap, source_code=SAMPLE_TOOL_SOURCE, version=1)
    report = builder.validate(manifest)
    builder.promote(manifest, report)

    executor = ToolExecutor(registry=registry, policy=ToolPolicy(enabled=True, allowed=frozenset()))
    assert executor.check("math_adder") == ""

    # Permanently revoke
    builder.revoke("math_adder", reason="Found security concern")
    assert registry.is_revoked("math_adder") is True
    assert registry.is_dynamically_authorized("math_adder") is False

    assert executor.check("math_adder") == "tool is revoked: math_adder"
    res = executor.execute("math_adder", {"a": 1, "b": 2})
    assert res.ok is False
    assert "revoked" in res.error

    # Attempting to re-activate a revoked tool MUST fail
    assert builder.activate("math_adder") is False
    assert executor.check("math_adder") == "tool is revoked: math_adder"


def test_5_persistence_and_rehydration(sqlite_storage):
    """
    TEST 5 — Persistence:
    Promote dynamic tool with SQLite persistence.
    Destroy in-memory registry.
    Create fresh registry and rehydrate.
    Assert:
    - Dynamic tool exists
    - Correct version exists
    - Correct lifecycle state exists
    - Provenance survives
    """
    # 1. Initial process: Promote tool
    reg1 = ToolRegistry()
    builder1 = ToolBuilder(registry=reg1, session_factory=sqlite_storage.session)

    gap = CapabilityGap(gap_id="gap_adder_5", intent="Add numbers", requested_capability="math.adder", reason="No math tool available")
    manifest = builder1.build_from_gap(gap=gap, source_code=SAMPLE_TOOL_SOURCE, version=1)
    report = builder1.validate(manifest)
    builder1.promote(manifest, report)

    # 2. Simulate server restart: destroy in-memory registry
    del reg1
    del builder1

    # 3. Fresh registry on server restart
    fresh_reg = ToolRegistry()
    assert not fresh_reg.has("math_adder")

    # Run rehydration
    stats = rehydrate_active_tools(registry=fresh_reg, session_factory=sqlite_storage.session)
    assert len(stats["rehydrated"]) == 1
    assert stats["rehydrated"][0]["name"] == "math_adder"

    # Assert presence, active status, provenance
    assert fresh_reg.has("math_adder")
    assert fresh_reg.is_active("math_adder")
    assert fresh_reg.is_dynamically_authorized("math_adder")
    assert fresh_reg.version_of("math_adder") == 1

    prov = fresh_reg.provenance_for("math_adder")
    assert prov is not None
    assert prov["name"] == "math_adder"
    assert prov["gap_id"] == "gap_adder_5"
    assert prov["status"] == "ACTIVE"

    # Verify execution after restart
    executor = ToolExecutor(registry=fresh_reg, policy=ToolPolicy(enabled=True, allowed=frozenset()))
    res = executor.execute("math_adder", {"a": 5, "b": 7})
    assert res.ok is True
    assert res.output == "12"
    assert res.data["sum"] == 12


def test_6_digest_integrity_tamper_rejection(sqlite_storage):
    """
    TEST 6 — Digest Integrity:
    Persist source code + digest.
    Simulate database tampering (mismatched digest).
    Attempt rehydration.
    Assert tool is NOT activated.
    """
    reg = ToolRegistry()
    builder = ToolBuilder(registry=reg, session_factory=sqlite_storage.session)

    gap = CapabilityGap(gap_id="gap_tamper", intent="Add numbers", requested_capability="math.adder", reason="No math tool available")
    manifest = builder.build_from_gap(gap=gap, source_code=SAMPLE_TOOL_SOURCE, version=1)
    report = builder.validate(manifest)
    builder.promote(manifest, report)

    # Tamper with the record in SQLite
    with sqlite_storage.session() as session:
        from sqlalchemy import select
        record = session.scalars(select(ToolProvenanceRecord).where(ToolProvenanceRecord.name == "math_adder")).first()
        assert record is not None
        # Corrupt the digest
        record.source_digest = "f" * 64
        session.commit()

    # Rehydrate in a fresh registry
    fresh_reg = ToolRegistry()
    stats = rehydrate_active_tools(registry=fresh_reg, session_factory=sqlite_storage.session)

    assert len(stats["rehydrated"]) == 0
    assert len(stats["rejected"]) == 1
    assert stats["rejected"][0]["reason"] == "DIGEST_MISMATCH"
    assert not fresh_reg.has("math_adder")


def test_7_malformed_record_isolation(sqlite_storage):
    """
    TEST 7 — Malformed Record Isolation:
    Persist:
    - One malformed/corrupted dynamic tool
    - One valid dynamic tool
    Rehydrate.
    Assert:
    - Malformed tool is rejected
    - Valid tool remains available and executable
    """
    # Insert a corrupt record manually
    with sqlite_storage.session() as session:
        corrupt_rec = ToolProvenanceRecord(
            name="corrupted_tool",
            version=1,
            gap_id="gap_corrupt",
            manifest_json="INVALID_JSON{",
            source_code="this is not python code !!!",
            source_digest=hashlib.sha256("this is not python code !!!".encode("utf-8")).hexdigest(),
            status="ACTIVE",
        )
        session.add(corrupt_rec)
        session.commit()

    # Promote a valid tool
    reg1 = ToolRegistry()
    builder1 = ToolBuilder(registry=reg1, session_factory=sqlite_storage.session)
    gap = CapabilityGap(gap_id="gap_valid", intent="Add numbers", requested_capability="math.adder", reason="No math tool available")
    manifest = builder1.build_from_gap(gap=gap, source_code=SAMPLE_TOOL_SOURCE, version=1)
    report = builder1.validate(manifest)
    builder1.promote(manifest, report)

    # Rehydrate into a fresh registry
    fresh_reg = ToolRegistry()
    stats = rehydrate_active_tools(registry=fresh_reg, session_factory=sqlite_storage.session)

    # Assert corrupted tool rejected, valid tool rehydrated
    assert any(r["name"] == "corrupted_tool" for r in stats["rejected"])
    assert any(r["name"] == "math_adder" for r in stats["rehydrated"])

    assert not fresh_reg.has("corrupted_tool")
    assert fresh_reg.has("math_adder")
    assert fresh_reg.is_active("math_adder")

    executor = ToolExecutor(registry=fresh_reg, policy=ToolPolicy(enabled=True, allowed=frozenset()))
    res = executor.execute("math_adder", {"a": 20, "b": 30})
    assert res.ok is True
    assert res.output == "50"
    assert res.data["sum"] == 50


def test_8_multi_version_selection(sqlite_storage):
    """
    TEST 8 — Version Selection:
    Create multiple versions:
    v1: ACTIVE
    v2: ROLLED_BACK
    Rehydrate.
    Assert:
    v1 is restored as the active version.
    """
    reg1 = ToolRegistry()
    builder1 = ToolBuilder(registry=reg1, session_factory=sqlite_storage.session)

    gap = CapabilityGap(gap_id="gap_v", intent="Add numbers", requested_capability="math.adder", reason="No math tool available")
    m1 = builder1.build_from_gap(gap=gap, source_code=SAMPLE_TOOL_SOURCE, version=1)
    r1 = builder1.validate(m1)
    builder1.promote(m1, r1)

    # Now create v2 with different behavior
    source_v2 = SAMPLE_TOOL_SOURCE.replace("res = a + b", "res = (a + b) * 2")
    m2 = builder1.build_from_gap(gap=gap, source_code=source_v2, version=2)
    r2 = builder1.validate(m2)
    builder1.promote(m2, r2, allow_upgrade=True)

    assert reg1.version_of("math_adder") == 2

    # Now rollback to v1
    builder1.rollback("math_adder")
    assert reg1.version_of("math_adder") == 1

    # Verify database status: v2 is ROLLED_BACK, v1 is ACTIVE
    with sqlite_storage.session() as session:
        from sqlalchemy import select
        v2_rec = session.scalars(
            select(ToolProvenanceRecord)
            .where(ToolProvenanceRecord.name == "math_adder", ToolProvenanceRecord.version == 2)
        ).first()
        assert v2_rec.status == "ROLLED_BACK"

    # Rehydrate in a fresh registry
    fresh_reg = ToolRegistry()
    stats = rehydrate_active_tools(registry=fresh_reg, session_factory=sqlite_storage.session)

    assert len(stats["rehydrated"]) == 1
    assert stats["rehydrated"][0]["name"] == "math_adder"
    assert stats["rehydrated"][0]["version"] == 1
    assert fresh_reg.version_of("math_adder") == 1

    executor = ToolExecutor(registry=fresh_reg, policy=ToolPolicy(enabled=True, allowed=frozenset()))
    res = executor.execute("math_adder", {"a": 2, "b": 3})
    assert res.ok is True
    # v1 produces a + b = 5, not (a + b)*2 = 10
    assert res.output == "5"
    assert res.data["sum"] == 5


def test_9_static_tool_regression():
    """
    TEST 9 — Static Tool Regression:
    Existing static tools without dynamic provenance must continue to work
    only when explicitly in policy.allowed.
    Unallowed static tools must remain blocked.
    """
    class StaticEchoTool(Tool):
        name = "static_echo"
        capability = "test.echo"
        risk = ToolRisk.SAFE
        parameters = (Parameter(name="msg", type="string", required=True),)
        def execute(self, msg: str) -> ToolResult:
            return ToolResult(ok=True, output=msg, data={"echo": msg})

    capability_registry.register(
        Capability(capability_id="test.echo", name="echo", description="Test echo", category="test")
    )

    reg = ToolRegistry()
    reg.register(StaticEchoTool())

    # 1. Not in policy.allowed -> REJECTED
    executor_unallowed = ToolExecutor(
        registry=reg,
        policy=ToolPolicy(enabled=True, allowed=frozenset()),
    )
    assert executor_unallowed.check("static_echo") == "tool not allowed by policy: static_echo"
    res_unallowed = executor_unallowed.execute("static_echo", {"msg": "hello"})
    assert res_unallowed.ok is False
    assert "not allowed" in res_unallowed.error

    # 2. In policy.allowed -> ACCEPTED
    executor_allowed = ToolExecutor(
        registry=reg,
        policy=ToolPolicy(enabled=True, allowed=frozenset({"static_echo"})),
    )
    assert executor_allowed.check("static_echo") == ""
    res_allowed = executor_allowed.execute("static_echo", {"msg": "hello"})
    assert res_allowed.ok is True
    assert res_allowed.output == "hello"
    assert res_allowed.data["echo"] == "hello"


def test_10_durable_task_resumption_with_dynamic_tool(sqlite_storage):
    """
    TEST 10 — Durable Task Resume:
    Create a compound durable task referencing a dynamic tool.
    Simulate server crash / restart:
    - Destroy in-memory runtime and registry
    - Fresh runtime + fresh registry with rehydration
    - Resume task and complete steps through ToolExecutor
    """
    from agent.task_runtime import TaskRuntime, TaskStatus, StepStatus

    # 1. Initial environment: promote dynamic tool
    reg1 = ToolRegistry()
    builder1 = ToolBuilder(registry=reg1, session_factory=sqlite_storage.session)
    gap = CapabilityGap(gap_id="gap_durable", intent="Add numbers", requested_capability="math.adder", reason="No math tool available")
    manifest = builder1.build_from_gap(gap=gap, source_code=SAMPLE_TOOL_SOURCE, version=1)
    report = builder1.validate(manifest)
    builder1.promote(manifest, report)

    # Create task with a step calling math_adder
    runtime1 = TaskRuntime(session_factory=sqlite_storage.session)
    task = runtime1.create_task(
        goal="Perform distributed addition",
        steps=[
            {
                "step_id": "step_add_1",
                "tool": "math_adder",
                "arguments": {"a": 100, "b": 250},
                "side_effect": "READ_ONLY",
            }
        ],
    )
    assert task.status == TaskStatus.PENDING.value

    # 2. Server crash: destroy runtime and registry
    del runtime1
    del reg1
    del builder1

    # 3. Server restart: rehydrate registry
    fresh_reg = ToolRegistry()
    stats = rehydrate_active_tools(registry=fresh_reg, session_factory=sqlite_storage.session)
    assert len(stats["rehydrated"]) == 1
    assert fresh_reg.has("math_adder")

    # Reconstruct TaskRuntime and ToolExecutor
    runtime2 = TaskRuntime(session_factory=sqlite_storage.session)
    executor = ToolExecutor(
        registry=fresh_reg,
        policy=ToolPolicy(enabled=True, allowed=frozenset()),  # Empty static allowed!
    )

    # Resume and execute compound task
    completed_task = runtime2.execute_compound_task(task.task_id, executor)
    assert completed_task.status == TaskStatus.COMPLETED.value

    steps = runtime2.list_steps(task.task_id)
    assert len(steps) == 1
    assert steps[0].status == StepStatus.COMPLETED.value
    assert steps[0].result["output"] == "350"
    assert steps[0].result["ok"] is True
