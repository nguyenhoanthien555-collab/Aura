"""
Unit & Integration tests for Phase 5B.4-2:
Real Process Crash / Restart Recovery for Durable Compound Tasks & Self-Extension.

Strict Invariants Verified:
1. Completed steps are NEVER replayed (call_count remains 1).
2. Promoted dynamic tools survive process death (rehydrated from SQLite provenance; zero second synthesis).
3. WAITING is not proof of synthesis success (truth determined from durable state).
4. Ambiguous mutating executions interrupted during flight are NEVER blindly retried (transition to UNKNOWN / manual verification unless postcondition probe verifies completion).
5. Safe/repeatable operations resume cleanly.
6. Downstream steps preserve their input context (${step_0.field}) with typed parameters.
7. Process death MUST BE REAL (via real Python child process termination: proc.kill() and os._exit()).
"""

import os
from pathlib import Path
import subprocess
import sys
import time
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agent.task_runtime import TaskRuntime, TaskStatus, StepStatus
from core.capabilities import registry as cap_registry, Capability
from core.capabilities.discovery import SkillDiscovery
from core.capabilities.gap import CapabilityGapEngine
from memory.models import DurableStepRecord, DurableTaskRecord, ToolProvenanceRecord
from memory.sqlite import init_task_tables
from tools.base import Tool, ToolRisk, ToolResult, Parameter
from tools.builder.builder import ToolBuilder
from tools.builder.policy import AutonomousSynthesisPolicy
from tools.builder.rehydrate import rehydrate_active_tools
from tools.builder.synthesis import ToolSynthesisEngine
from tools.executor import ToolExecutor, ToolPolicy
from tools.outcome import Evidence, EvidenceKind, ToolStatus, SideEffect
from tools.registry import ToolRegistry


def ensure_test_capabilities():
    for cid, cname in [
        ("test.counter", "Counter Tool"),
        ("test.dummy", "Dummy Tool"),
        ("test.mutating", "Mutating Tool"),
        ("test.typed", "Typed Tool"),
    ]:
        if not cap_registry.get(cid):
            cap_registry.register(Capability(capability_id=cid, name=cname, description=cname, category="test"))


ensure_test_capabilities()


VALID_FIB_SOURCE = '''
from tools.base import Tool, ToolRisk, ToolResult, Parameter
from tools.outcome import Evidence, EvidenceKind, ToolStatus, SideEffect

class FibonacciTool(Tool):
    name = "math_fibonacci"
    capability = "custom.math_fibonacci"
    risk = ToolRisk.SAFE
    side_effect = SideEffect.READ_ONLY
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
            evidence=(ev,),
            status=ToolStatus.SUCCESS.value,
        )

    def verify(self, n: int = 10) -> bool:
        return True
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
    def __init__(self, response_text: str = ""):
        import json
        self.response_text = response_text or json.dumps({
            "name": "math_fibonacci",
            "description": "Calculates nth Fibonacci number",
            "source_code": VALID_FIB_SOURCE,
            "test_code": VALID_FIB_TEST,
        })
        self.provider_name = "mock_synth_provider"
        self.model = "mock-model"

    def generate(self, prompt: str) -> str:
        return self.response_text


def run_worker_subproc(args_list, timeout=15):
    """Launches crash_worker.py as an independent OS subprocess."""
    worker_script = Path(__file__).parent / "crash_worker.py"
    cmd = [sys.executable, str(worker_script)] + args_list
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return proc


def test_crash_scenario_a_completed_step_never_replayed(tmp_path):
    """
    Scenario A: Step 0 completed in SQLite + real process kill/exit.
    On restart, Step 0 must NOT be replayed (call_count remains 1),
    and Step 1 executes cleanly to task completion.
    """
    ensure_test_capabilities()
    db_path = str(tmp_path / "test_scenario_a.db").replace("\\", "/")
    counter_file = str(tmp_path / "counter.txt").replace("\\", "/")
    marker_file = str(tmp_path / "marker.txt").replace("\\", "/")
    task_id = "task_crash_a"

    # 1. Run Child Process 1: executes step 0 and exits via os._exit(42)
    proc = run_worker_subproc([
        "--mode", "scenario_a",
        "--db-path", db_path,
        "--task-id", task_id,
        "--counter-file", counter_file,
        "--marker-file", marker_file,
    ])
    stdout, stderr = proc.communicate(timeout=10)
    assert proc.returncode == 42, f"Expected os._exit(42), got {proc.returncode}. Stderr: {stderr}"
    assert os.path.exists(marker_file)

    with open(counter_file, "r") as f:
        assert int(f.read().strip()) == 1

    # 2. Server Restart (Fresh Process Simulation)
    engine = create_engine(f"sqlite:///{db_path}")
    Session = sessionmaker(bind=engine)
    registry = ToolRegistry()

    # Re-register tools
    step0_calls = 0

    class CounterTool(Tool):
        name = "step0_counter"
        capability = "test.counter"
        risk = ToolRisk.SAFE
        parameters = ()

        def execute(self) -> ToolResult:
            nonlocal step0_calls
            step0_calls += 1
            val = 0
            if os.path.exists(counter_file):
                with open(counter_file, "r") as f:
                    val = int(f.read().strip() or "0")
            val += 1
            with open(counter_file, "w") as f:
                f.write(str(val))
            return ToolResult(ok=True, output=f"count={val}", data={"count": val}, status=ToolStatus.SUCCESS.value)

    step1_calls = 0

    class Step1Tool(Tool):
        name = "step1_dummy"
        capability = "test.dummy"
        risk = ToolRisk.SAFE
        parameters = ()

        def execute(self) -> ToolResult:
            nonlocal step1_calls
            step1_calls += 1
            return ToolResult(ok=True, output="step1_done", status=ToolStatus.SUCCESS.value)

    registry.register(CounterTool())
    registry.register(Step1Tool())

    executor = ToolExecutor(registry=registry, policy=ToolPolicy(enabled=True, allowed=frozenset({"step0_counter", "step1_dummy"})))
    runtime = TaskRuntime(session_factory=Session)

    # 3. Resume active tasks
    resumed = runtime.resume_all_active(executor)
    assert len(resumed) == 1
    task = resumed[0]
    assert task.status == TaskStatus.COMPLETED.value

    # STRICT INVARIANT 1: Step 0 call counter MUST still be 1!
    with open(counter_file, "r") as f:
        assert int(f.read().strip()) == 1, "VIOLATION: Step 0 was replayed after crash!"

    assert step0_calls == 0, "VIOLATION: Step 0 tool execute() was called in recovery runtime!"
    assert step1_calls == 1, "Step 1 should have been executed once during recovery!"

    step0 = runtime.get_step(f"{task_id}_s0")
    step1 = runtime.get_step(f"{task_id}_s1")
    assert step0.status == StepStatus.COMPLETED.value
    assert step1.status == StepStatus.COMPLETED.value


def test_crash_scenario_b_promoted_tool_rehydrated_zero_second_synthesis(tmp_path):
    """
    Scenario B: Dynamic tool synthesized and promoted to SQLite, real process crashes
    before step execution.
    On restart, tool is rehydrated from SQLite provenance; step executes with
    ZERO second synthesis.
    """
    ensure_test_capabilities()
    db_path = str(tmp_path / "test_scenario_b.db").replace("\\", "/")
    marker_file = str(tmp_path / "marker.txt").replace("\\", "/")
    task_id = "task_crash_b"

    # 1. Run Child Process 1: synthesizes, promotes to SQLite provenance, and exits via os._exit(77)
    proc = run_worker_subproc([
        "--mode", "scenario_b",
        "--db-path", db_path,
        "--task-id", task_id,
        "--marker-file", marker_file,
    ])
    stdout, stderr = proc.communicate(timeout=15)
    assert proc.returncode == 77, f"Expected os._exit(77), got {proc.returncode}. Stderr: {stderr}"
    assert os.path.exists(marker_file)

    # 2. Server Restart (Fresh Process Simulation)
    engine = create_engine(f"sqlite:///{db_path}")
    Session = sessionmaker(bind=engine)
    registry = ToolRegistry()

    # Rehydrate active tools from SQLite into fresh registry
    rehydrate_stats = rehydrate_active_tools(registry, session_factory=Session)
    assert any(item["name"] == "math_fibonacci" for item in rehydrate_stats["rehydrated"])
    assert registry.has("math_fibonacci")

    # Spy synthesis engine: must NEVER be called!
    synthesis_call_count = 0

    class SpySynthesisEngine:
        def synthesize_and_promote(self, gap, approver=""):
            nonlocal synthesis_call_count
            synthesis_call_count += 1
            raise AssertionError("Synthesis engine should not be called for rehydrated tool!")

    executor = ToolExecutor(registry=registry, policy=ToolPolicy(enabled=True))
    runtime = TaskRuntime(
        session_factory=Session,
        synthesis_engine=SpySynthesisEngine(),
        synthesis_policy=AutonomousSynthesisPolicy(),
    )

    # 3. Resume active tasks
    resumed = runtime.resume_all_active(executor)
    assert len(resumed) == 1
    task = resumed[0]
    assert task.status == TaskStatus.COMPLETED.value

    # STRICT INVARIANT 2: Zero second synthesis!
    assert synthesis_call_count == 0, "VIOLATION: Synthesis was triggered for already promoted dynamic tool!"

    step = runtime.get_step(f"{task_id}_s0")
    assert step.status == StepStatus.COMPLETED.value
    assert step.result.get("ok") is True
    assert step.result.get("output") == "55"


def test_crash_scenario_c_crash_during_synthesis_safe_resynthesis(tmp_path):
    """
    Scenario C: Crash while synthesis is in flight (status=WAITING in SQLite,
    tool NOT promoted).
    On restart, WAITING is NOT assumed to be success; runtime discovers tool
    is absent, safely initiates synthesis, promotes, and completes cleanly.
    """
    ensure_test_capabilities()
    db_path = str(tmp_path / "test_scenario_c.db").replace("\\", "/")
    marker_file = str(tmp_path / "marker.txt").replace("\\", "/")
    task_id = "task_crash_c"

    # 1. Run Child Process 1: sets WAITING in SQLite and crashes via os._exit(88)
    proc = run_worker_subproc([
        "--mode", "scenario_c",
        "--db-path", db_path,
        "--task-id", task_id,
        "--marker-file", marker_file,
    ])
    stdout, stderr = proc.communicate(timeout=10)
    assert proc.returncode == 88, f"Expected os._exit(88), got {proc.returncode}. Stderr: {stderr}"
    assert os.path.exists(marker_file)

    # 2. Server Restart (Fresh Process Simulation)
    engine = create_engine(f"sqlite:///{db_path}")
    Session = sessionmaker(bind=engine)
    registry = ToolRegistry()

    # Rehydrate finds nothing because tool was not promoted
    rehydrate_stats = rehydrate_active_tools(registry, session_factory=Session)
    assert len(rehydrate_stats["rehydrated"]) == 0
    assert not registry.has("math_fibonacci")

    # Set up real synthesis engine in restart runtime
    llm = MockLLMForSynthesis()
    builder = ToolBuilder(registry=registry, session_factory=Session)
    engine_synth = ToolSynthesisEngine(llm=llm, builder=builder)
    policy = AutonomousSynthesisPolicy()
    gap_engine = CapabilityGapEngine(discovery=SkillDiscovery(), registry=registry)

    executor = ToolExecutor(registry=registry, policy=ToolPolicy(enabled=True))
    runtime = TaskRuntime(
        session_factory=Session,
        synthesis_engine=engine_synth,
        synthesis_policy=policy,
        gap_engine=gap_engine,
    )

    # 3. Resume active tasks
    resumed = runtime.resume_all_active(executor)
    assert len(resumed) == 1
    task = resumed[0]
    assert task.status == TaskStatus.COMPLETED.value

    step = runtime.get_step(f"{task_id}_s0")
    assert step.status == StepStatus.COMPLETED.value
    assert step.result.get("output") == "55"

    # Verify tool provenance now exists in SQLite
    with Session() as session:
        rec = session.query(ToolProvenanceRecord).filter_by(name="math_fibonacci").first()
        assert rec is not None and rec.status == "ACTIVE"


def test_crash_scenario_d1_mutating_interrupted_no_verify_stays_unknown(tmp_path):
    """
    Scenario D1: Mutating non-idempotent step interrupted in flight without postcondition verify.
    On restart, step is NOT replayed and transitions to UNKNOWN (requires manual verification).
    """
    ensure_test_capabilities()
    db_path = str(tmp_path / "test_scenario_d1.db").replace("\\", "/")
    marker_file = str(tmp_path / "marker.txt").replace("\\", "/")
    state_file = str(tmp_path / "state.txt").replace("\\", "/")
    task_id = "task_crash_d1"

    # 1. Run Child Process 1: starts mutating step in RUNNING and crashes via os._exit(99)
    proc = run_worker_subproc([
        "--mode", "scenario_d",
        "--db-path", db_path,
        "--task-id", task_id,
        "--marker-file", marker_file,
        "--state-file", state_file,
    ])
    stdout, stderr = proc.communicate(timeout=10)
    assert proc.returncode == 99, f"Expected os._exit(99), got {proc.returncode}. Stderr: {stderr}"
    assert os.path.exists(marker_file)

    # 2. Server Restart (Fresh Process Simulation)
    engine = create_engine(f"sqlite:///{db_path}")
    Session = sessionmaker(bind=engine)
    registry = ToolRegistry()

    exec_count = 0

    class MutatingToolNoVerify(Tool):
        name = "mutating_writer"
        capability = "test.mutating"
        risk = ToolRisk.DANGEROUS
        side_effect = SideEffect.NON_IDEMPOTENT
        parameters = ()

        def execute(self, **kwargs) -> ToolResult:
            nonlocal exec_count
            exec_count += 1
            return ToolResult(ok=True, output="mutated", status=ToolStatus.SUCCESS.value)

    registry.register(MutatingToolNoVerify())
    executor = ToolExecutor(registry=registry, policy=ToolPolicy(enabled=True, allowed=frozenset({"mutating_writer"})))
    runtime = TaskRuntime(session_factory=Session)

    # 3. Resume active tasks
    resumed = runtime.resume_all_active(executor)
    assert len(resumed) == 1
    task = resumed[0]

    # STRICT INVARIANT 4: Mutating step must NOT be blindly retried!
    assert exec_count == 0, "VIOLATION: Mutating step was executed during recovery!"
    assert task.status == TaskStatus.UNKNOWN.value
    assert task.recovery_state == "REQUIRES_MANUAL_VERIFICATION"

    step = runtime.get_step(f"{task_id}_s0")
    assert step.status == StepStatus.UNKNOWN.value


def test_crash_scenario_d2_mutating_interrupted_probe_verified_completes(tmp_path):
    """
    Scenario D2: Mutating non-idempotent step applied side effect, but crashed
    before result persistence.
    On restart, postcondition probe verifies the side effect, marking step COMPLETED
    with EvidenceKind.POSTCONDITION without re-executing.
    """
    ensure_test_capabilities()
    db_path = str(tmp_path / "test_scenario_d2.db").replace("\\", "/")
    marker_file = str(tmp_path / "marker.txt").replace("\\", "/")
    state_file = str(tmp_path / "state.txt").replace("\\", "/")
    task_id = "task_crash_d2"

    # 1. Run Child Process 1: applies mutation to state_file, leaves RUNNING, crashes via os._exit(99)
    proc = run_worker_subproc([
        "--mode", "scenario_d",
        "--db-path", db_path,
        "--task-id", task_id,
        "--marker-file", marker_file,
        "--state-file", state_file,
        "--with-verify",
        "--applied",
    ])
    stdout, stderr = proc.communicate(timeout=10)
    assert proc.returncode == 99, f"Expected os._exit(99), got {proc.returncode}. Stderr: {stderr}"
    assert os.path.exists(marker_file)
    assert os.path.exists(state_file)

    # 2. Server Restart (Fresh Process Simulation)
    engine = create_engine(f"sqlite:///{db_path}")
    Session = sessionmaker(bind=engine)
    registry = ToolRegistry()

    exec_count = 0
    verify_count = 0

    class MutatingToolWithVerify(Tool):
        name = "mutating_writer"
        capability = "test.mutating"
        risk = ToolRisk.DANGEROUS
        side_effect = SideEffect.NON_IDEMPOTENT
        parameters = ()

        def execute(self, **kwargs) -> ToolResult:
            nonlocal exec_count
            exec_count += 1
            return ToolResult(ok=True, output="mutated", status=ToolStatus.SUCCESS.value)

        def verify(self, filepath: str = "", **kwargs) -> bool:
            nonlocal verify_count
            verify_count += 1
            return os.path.exists(filepath)

    registry.register(MutatingToolWithVerify())
    executor = ToolExecutor(registry=registry, policy=ToolPolicy(enabled=True, allowed=frozenset({"mutating_writer"})))
    runtime = TaskRuntime(session_factory=Session)

    # 3. Resume active tasks
    resumed = runtime.resume_all_active(executor)
    assert len(resumed) == 1
    task = resumed[0]

    # Verify probe verified completion without re-executing
    assert exec_count == 0, "VIOLATION: Mutating step execute() was called instead of verify() probe!"
    assert verify_count == 1, "verify() probe should have been called once!"
    assert task.status == TaskStatus.COMPLETED.value

    step = runtime.get_step(f"{task_id}_s0")
    assert step.status == StepStatus.COMPLETED.value
    assert step.result.get("verified_by_probe") is True
    assert any(e.get("kind") == EvidenceKind.POSTCONDITION.value for e in step.evidence)


def test_crash_scenario_e_typed_parameter_propagation_across_crash(tmp_path):
    """
    Scenario E: Step 0 completes with structured typed output and process crashes.
    On restart, downstream Step 1 receives exact typed parameters (${step_0.fib_val}, etc.)
    from the persisted SQLite record.
    """
    ensure_test_capabilities()
    db_path = str(tmp_path / "test_scenario_e.db").replace("\\", "/")
    marker_file = str(tmp_path / "marker.txt").replace("\\", "/")
    task_id = "task_crash_e"

    # 1. Run Child Process 1: executes step 0 with typed dict output and exits via os._exit(0)
    proc = run_worker_subproc([
        "--mode", "scenario_e",
        "--db-path", db_path,
        "--task-id", task_id,
        "--marker-file", marker_file,
    ])
    stdout, stderr = proc.communicate(timeout=10)
    assert proc.returncode == 0, f"Expected 0, got {proc.returncode}. Stderr: {stderr}"
    assert os.path.exists(marker_file)

    # 2. Server Restart (Fresh Process Simulation)
    engine = create_engine(f"sqlite:///{db_path}")
    Session = sessionmaker(bind=engine)
    registry = ToolRegistry()

    received_params = {}

    class ConsumerTool(Tool):
        name = "typed_consumer"
        capability = "test.typed"
        risk = ToolRisk.SAFE
        parameters = ()

        def execute(self, num=None, active=None, tag=None) -> ToolResult:
            nonlocal received_params
            received_params = {"num": num, "active": active, "tag": tag}
            # Verify exact types
            assert isinstance(num, int), f"Expected int, got {type(num)}"
            assert isinstance(active, bool), f"Expected bool, got {type(active)}"
            assert isinstance(tag, str), f"Expected str, got {type(tag)}"
            return ToolResult(
                ok=True,
                output=f"consumed num={num}, active={active}, tag={tag}",
                data={"verified": True},
                status=ToolStatus.SUCCESS.value,
            )

    registry.register(ConsumerTool())
    executor = ToolExecutor(registry=registry, policy=ToolPolicy(enabled=True, allowed=frozenset({"typed_consumer"})))
    runtime = TaskRuntime(session_factory=Session)

    # 3. Resume active tasks
    resumed = runtime.resume_all_active(executor)
    assert len(resumed) == 1
    task = resumed[0]
    assert task.status == TaskStatus.COMPLETED.value

    # STRICT INVARIANT 6: Typed parameter preservation across crash boundary
    assert received_params == {"num": 42, "active": True, "tag": "batch_99"}
    assert type(received_params["num"]) is int
    assert type(received_params["active"]) is bool
    assert type(received_params["tag"]) is str


def test_crash_scenario_f_all_steps_completed_reconciliation(tmp_path):
    """
    Scenario F: All steps completed in SQLite, but process crashed before
    task.status = COMPLETED was persisted.
    On restart, runtime reconciles task to COMPLETED without re-executing any steps.
    """
    ensure_test_capabilities()
    db_path = str(tmp_path / "test_scenario_f.db").replace("\\", "/")
    marker_file = str(tmp_path / "marker.txt").replace("\\", "/")
    task_id = "task_crash_f"

    # 1. Run Child Process 1: sets all steps to COMPLETED, leaves task in RUNNING, exits via os._exit(0)
    proc = run_worker_subproc([
        "--mode", "scenario_f",
        "--db-path", db_path,
        "--task-id", task_id,
        "--marker-file", marker_file,
    ])
    stdout, stderr = proc.communicate(timeout=10)
    assert proc.returncode == 0, f"Expected 0, got {proc.returncode}. Stderr: {stderr}"
    assert os.path.exists(marker_file)

    # 2. Server Restart (Fresh Process Simulation)
    engine = create_engine(f"sqlite:///{db_path}")
    Session = sessionmaker(bind=engine)
    registry = ToolRegistry()

    dummy_calls = 0

    class DummyTool(Tool):
        name = "dummy"
        capability = "test.dummy"
        risk = ToolRisk.SAFE
        parameters = ()

        def execute(self) -> ToolResult:
            nonlocal dummy_calls
            dummy_calls += 1
            return ToolResult(ok=True, output="dummy_ran", status=ToolStatus.SUCCESS.value)

    registry.register(DummyTool())
    executor = ToolExecutor(registry=registry, policy=ToolPolicy(enabled=True, allowed=frozenset({"dummy"})))
    runtime = TaskRuntime(session_factory=Session)

    # Verify task is currently RUNNING in SQLite before recovery
    task_before = runtime.get_task(task_id)
    assert task_before.status == TaskStatus.RUNNING.value

    # 3. Resume active tasks
    resumed = runtime.resume_all_active(executor)
    assert len(resumed) == 1
    task_after = resumed[0]

    # Task status must be cleanly reconciled to COMPLETED
    assert task_after.status == TaskStatus.COMPLETED.value
    assert task_after.recovery_state == "ALL_STEPS_COMPLETED"
    assert dummy_calls == 0, "VIOLATION: Steps were re-executed during completed task reconciliation!"


def test_crash_scenario_g_external_process_kill_via_os(tmp_path):
    """
    Scenario G: External real OS process termination via proc.kill() (Windows TerminateProcess).
    Proves that child process death is genuinely external and unhandled by Python exception handlers.
    """
    marker_file = str(tmp_path / "marker.txt").replace("\\", "/")

    # 1. Spawn child process
    proc = run_worker_subproc([
        "--mode", "external_kill",
        "--marker-file", marker_file,
    ])

    # 2. Wait until child announces itself
    for _ in range(50):
        if os.path.exists(marker_file) and proc.poll() is None:
            break
        time.sleep(0.1)

    assert proc.poll() is None, "Child process should be actively running in OS!"
    child_pid = proc.pid
    assert child_pid > 0

    # 3. Forcibly kill child process via OS
    proc.kill()
    exit_code = proc.wait(timeout=5)

    # On Windows, TerminateProcess results in non-zero exit code
    assert proc.poll() is not None, "Child process must be dead after proc.kill()!"
    assert exit_code != 0, f"Expected non-zero exit code from proc.kill(), got {exit_code}"
