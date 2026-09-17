"""
Crash Recovery Worker for Phase 5B.4-2.
Executed as an independent OS process to simulate real process crashes,
abrupt exits via os._exit(), and external termination via proc.kill().
"""

import argparse
import json
import os
from pathlib import Path
import sys
import time

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

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


def setup_db(db_path: str):
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    init_task_tables(engine)
    return sessionmaker(bind=engine)


def mode_scenario_a_step0(db_path: str, task_id: str, counter_file: str, marker_file: str):
    """Executes step 0, writes counter, records in DB, writes marker and exits abruptly."""
    Session = setup_db(db_path)
    registry = ToolRegistry()

    class CounterTool(Tool):
        name = "step0_counter"
        capability = "test.counter"
        risk = ToolRisk.SAFE
        parameters = ()

        def execute(self) -> ToolResult:
            val = 0
            if os.path.exists(counter_file):
                with open(counter_file, "r") as f:
                    val = int(f.read().strip() or "0")
            val += 1
            with open(counter_file, "w") as f:
                f.write(str(val))
            return ToolResult(ok=True, output=f"count={val}", data={"count": val}, status=ToolStatus.SUCCESS.value)

    class Step1Tool(Tool):
        name = "step1_dummy"
        capability = "test.dummy"
        risk = ToolRisk.SAFE
        parameters = ()

        def execute(self) -> ToolResult:
            return ToolResult(ok=True, output="step1_done", status=ToolStatus.SUCCESS.value)

    registry.register(CounterTool())
    registry.register(Step1Tool())

    executor = ToolExecutor(registry=registry, policy=ToolPolicy(enabled=True, allowed=frozenset({"step0_counter", "step1_dummy"})))
    runtime = TaskRuntime(session_factory=Session)

    runtime.create_task(
        goal="Two-step compound task with crash after step 0",
        task_id=task_id,
        steps=[
            {"step_id": f"{task_id}_s0", "name": "step_0", "tool": "step0_counter", "sequential": True},
            {"step_id": f"{task_id}_s1", "name": "step_1", "tool": "step1_dummy", "sequential": True},
        ],
    )

    task = runtime.get_task(task_id)
    step0 = runtime.get_step(f"{task_id}_s0")
    runtime.update_task_status(task_id, TaskStatus.RUNNING.value, current_step_id=step0.step_id)
    runtime.update_step(step0.step_id, StepStatus.RUNNING.value, attempt=1)
    res0 = executor.execute("step0_counter", {})
    assert res0.ok is True, f"step0_counter failed: {res0.error}"
    runtime.record_step_result(task_id, step0.step_id, res0)

    # Write marker and abrupt OS exit!
    with open(marker_file, "w") as f:
        f.write(f"pid={os.getpid()};step0_completed=true")

    # Real OS process termination!
    os._exit(42)


def mode_scenario_b_promote_and_crash(db_path: str, task_id: str, marker_file: str):
    """Synthesizes and promotes tool into SQLite provenance, then crashes before step execution."""
    Session = setup_db(db_path)
    registry = ToolRegistry()
    llm = MockLLMForSynthesis()
    builder = ToolBuilder(registry=registry, session_factory=Session)
    engine = ToolSynthesisEngine(llm=llm, builder=builder)
    policy = AutonomousSynthesisPolicy()
    gap_engine = CapabilityGapEngine(discovery=SkillDiscovery(), registry=registry)

    executor = ToolExecutor(registry=registry, policy=ToolPolicy(enabled=True))
    runtime = TaskRuntime(
        session_factory=Session,
        synthesis_engine=engine,
        synthesis_policy=policy,
        gap_engine=gap_engine,
    )

    runtime.create_task(
        goal="Task requiring dynamic math_fibonacci",
        task_id=task_id,
        steps=[
            {
                "step_id": f"{task_id}_s0",
                "name": "calc_fib",
                "tool": "math_fibonacci",
                "capability": "custom.math_fibonacci",
                "arguments": {"n": 10},
            }
        ],
    )

    task = runtime.get_task(task_id)
    step = runtime.get_step(f"{task_id}_s0")

    # Call capability recovery directly: promotes tool to SQLite!
    recovered, reason = runtime._maybe_recover_step_capability(task, step, executor)
    assert recovered is True, f"Recovery failed: {reason}"

    # Verify tool provenance was committed to SQLite
    with Session() as session:
        rec = session.query(ToolProvenanceRecord).filter_by(name="math_fibonacci").first()
        assert rec is not None and rec.status == "ACTIVE"

    # Write marker and crash!
    with open(marker_file, "w") as f:
        f.write(f"pid={os.getpid()};promoted=true")

    os._exit(77)


def mode_scenario_c_crash_during_synth(db_path: str, task_id: str, marker_file: str):
    """Crashes during synthesis after WAITING status is written to SQLite, before promotion."""
    Session = setup_db(db_path)
    registry = ToolRegistry()

    runtime = TaskRuntime(session_factory=Session)

    runtime.create_task(
        goal="Task requiring dynamic capability that crashes during synthesis",
        task_id=task_id,
        steps=[
            {
                "step_id": f"{task_id}_s0",
                "name": "calc_fib",
                "tool": "math_fibonacci",
                "capability": "custom.math_fibonacci",
                "arguments": {"n": 10},
            }
        ],
    )

    # Write WAITING checkpoint to SQLite to simulate in-flight synthesis
    runtime.update_step(f"{task_id}_s0", StepStatus.WAITING.value)
    runtime.update_task_status(
        task_id,
        TaskStatus.WAITING.value,
        recovery_state="SYNTHESIZING_CAPABILITY:custom.math_fibonacci",
        current_step_id=f"{task_id}_s0",
    )

    # Write marker and crash before any promotion to SQLite!
    with open(marker_file, "w") as f:
        f.write(f"pid={os.getpid()};waiting_persisted=true")

    os._exit(88)


def mode_scenario_d_mutating_crash(db_path: str, task_id: str, marker_file: str, with_verify: bool, side_effect_applied: bool, state_file: str):
    """Sets up a mutating step in RUNNING status and crashes."""
    Session = setup_db(db_path)
    runtime = TaskRuntime(session_factory=Session)

    runtime.create_task(
        goal="Mutating task that crashes in flight",
        task_id=task_id,
        steps=[
            {
                "step_id": f"{task_id}_s0",
                "name": "mutating_step",
                "tool": "mutating_writer",
                "capability": "test.mutating",
                "side_effect": SideEffect.NON_IDEMPOTENT.value,
                "arguments": {"filepath": state_file, "data": "persisted_value"},
            }
        ],
    )

    runtime.update_task_status(task_id, TaskStatus.RUNNING.value, current_step_id=f"{task_id}_s0")
    runtime.update_step(f"{task_id}_s0", StepStatus.RUNNING.value, attempt=1)

    if side_effect_applied:
        # Simulate that the physical mutation actually completed on the system
        with open(state_file, "w") as f:
            f.write("persisted_value")

    with open(marker_file, "w") as f:
        f.write(f"pid={os.getpid()};running_persisted=true;with_verify={with_verify};applied={side_effect_applied}")

    os._exit(99)


def mode_scenario_e_typed_param_step0(db_path: str, task_id: str, marker_file: str):
    """Executes Step 0 with typed structured data, commits to SQLite, and crashes."""
    Session = setup_db(db_path)
    registry = ToolRegistry()

    class TypedProducerTool(Tool):
        name = "typed_producer"
        capability = "test.typed"
        risk = ToolRisk.SAFE
        parameters = ()

        def execute(self) -> ToolResult:
            return ToolResult(
                ok=True,
                output="produced",
                data={"fib_val": 42, "is_valid": True, "meta": {"label": "batch_99"}},
                status=ToolStatus.SUCCESS.value,
            )

    registry.register(TypedProducerTool())
    executor = ToolExecutor(registry=registry, policy=ToolPolicy(enabled=True, allowed=frozenset({"typed_producer"})))
    runtime = TaskRuntime(session_factory=Session)

    runtime.create_task(
        goal="Typed parameter propagation compound task",
        task_id=task_id,
        steps=[
            {"step_id": f"{task_id}_s0", "name": "step_0", "tool": "typed_producer", "sequential": True},
            {
                "step_id": f"{task_id}_s1",
                "name": "step_1",
                "tool": "typed_consumer",
                "sequential": True,
                "arguments": {
                    "num": "${step_0.fib_val}",
                    "active": "${step_0.is_valid}",
                    "tag": "${step_0.meta.label}",
                },
            },
        ],
    )

    task = runtime.get_task(task_id)
    step0 = runtime.get_step(f"{task_id}_s0")
    runtime.update_task_status(task_id, TaskStatus.RUNNING.value, current_step_id=step0.step_id)
    runtime.update_step(step0.step_id, StepStatus.RUNNING.value, attempt=1)
    res0 = executor.execute("typed_producer", {})
    assert res0.ok is True, f"typed_producer failed: {res0.error}"
    runtime.record_step_result(task_id, step0.step_id, res0)

    with open(marker_file, "w") as f:
        f.write(f"pid={os.getpid()};step0_typed_done=true")

    os._exit(0)


def mode_scenario_f_all_steps_completed_crash(db_path: str, task_id: str, marker_file: str):
    """Executes all steps to COMPLETED in SQLite, but crashes before task status is marked COMPLETED."""
    Session = setup_db(db_path)
    runtime = TaskRuntime(session_factory=Session)

    runtime.create_task(
        goal="Task with all steps completed before crash",
        task_id=task_id,
        steps=[
            {"step_id": f"{task_id}_s0", "name": "step_0", "tool": "dummy", "sequential": True},
            {"step_id": f"{task_id}_s1", "name": "step_1", "tool": "dummy", "sequential": True},
        ],
    )

    # Set both steps to COMPLETED in SQLite
    runtime.update_step(
        f"{task_id}_s0",
        StepStatus.COMPLETED.value,
        result={"ok": True, "output": "s0_ok", "status": "SUCCESS"},
    )
    runtime.update_step(
        f"{task_id}_s1",
        StepStatus.COMPLETED.value,
        result={"ok": True, "output": "s1_ok", "status": "SUCCESS"},
    )
    # Leave task in RUNNING status!
    runtime.update_task_status(task_id, TaskStatus.RUNNING.value, current_step_id=f"{task_id}_s1")

    with open(marker_file, "w") as f:
        f.write(f"pid={os.getpid()};steps_completed=true")

    os._exit(0)


def mode_external_kill_target(marker_file: str):
    """Writes a marker and sleeps indefinitely so parent process can kill it via proc.kill()."""
    with open(marker_file, "w") as f:
        f.write(f"pid={os.getpid()};waiting_for_kill=true")
    print(f"CHILD_STARTED:{os.getpid()}", flush=True)
    while True:
        time.sleep(0.5)


def main():
    parser = argparse.ArgumentParser(description="Phase 5B.4-2 Crash Worker")
    parser.add_argument("--mode", required=True)
    parser.add_argument("--db-path", default="")
    parser.add_argument("--task-id", default="")
    parser.add_argument("--marker-file", default="")
    parser.add_argument("--counter-file", default="")
    parser.add_argument("--state-file", default="")
    parser.add_argument("--with-verify", action="store_true")
    parser.add_argument("--applied", action="store_true")

    args = parser.parse_args()

    if args.mode == "scenario_a":
        mode_scenario_a_step0(args.db_path, args.task_id, args.counter_file, args.marker_file)
    elif args.mode == "scenario_b":
        mode_scenario_b_promote_and_crash(args.db_path, args.task_id, args.marker_file)
    elif args.mode == "scenario_c":
        mode_scenario_c_crash_during_synth(args.db_path, args.task_id, args.marker_file)
    elif args.mode == "scenario_d":
        mode_scenario_d_mutating_crash(
            args.db_path, args.task_id, args.marker_file, args.with_verify, args.applied, args.state_file
        )
    elif args.mode == "scenario_e":
        mode_scenario_e_typed_param_step0(args.db_path, args.task_id, args.marker_file)
    elif args.mode == "scenario_live_llm":
        from brain.router import BrainRouter
        Session = setup_db(args.db_path)
        registry = ToolRegistry()
        router = BrainRouter()
        builder = ToolBuilder(registry=registry, session_factory=Session)
        engine = ToolSynthesisEngine(llm=router, builder=builder)
        policy = AutonomousSynthesisPolicy()
        gap_engine = CapabilityGapEngine(discovery=SkillDiscovery(), registry=registry)

        executor = ToolExecutor(registry=registry, policy=ToolPolicy(enabled=True))
        runtime = TaskRuntime(
            session_factory=Session,
            synthesis_engine=engine,
            synthesis_policy=policy,
            gap_engine=gap_engine,
        )

        runtime.create_task(
            goal="Task requiring live synthesized tool math_square",
            task_id=args.task_id,
            steps=[
                {
                    "step_id": f"{args.task_id}_s0",
                    "name": "calc_square",
                    "tool": "math_square",
                    "capability": "custom.math_square",
                    "arguments": {},
                }
            ],
        )

        task = runtime.get_task(args.task_id)
        step = runtime.get_step(f"{args.task_id}_s0")

        # Live Gemini code synthesis + AST validation + sandbox testing + SQLite promotion
        recovered, reason = runtime._maybe_recover_step_capability(task, step, executor)
        assert recovered is True, f"Live LLM capability recovery failed: {reason}"

        # Write marker and abrupt OS exit!
        with open(args.marker_file, "w") as f:
            f.write(f"pid={os.getpid()};live_promoted=true;reason={reason}")

        os._exit(101)
    elif args.mode == "scenario_f":
        mode_scenario_f_all_steps_completed_crash(args.db_path, args.task_id, args.marker_file)
    elif args.mode == "external_kill":
        mode_external_kill_target(args.marker_file)
    else:
        print(f"Unknown mode: {args.mode}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
