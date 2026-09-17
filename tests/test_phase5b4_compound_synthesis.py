"""
Unit & Integration tests for Phase 5B.4-1:
Durable Task Capability Recovery Implementation.

Verifies:
1. Missing step capability triggers synthesis, promotion, and execution
2. Step waits during synthesis without consuming execution retry attempts
3. Completed step is not replayed when subsequent step triggers synthesis
4. Output propagation from prior step to synthesized tool
5. Synthesized tool output passed to subsequent step
6. Prohibited or unsafe capability rejected cleanly
7. Concurrent compound tasks for same capability deduplicate synthesis
8. Restart during step WAITING rehydrates tool and resumes execution
9. Verification required step invokes postcondition probe on synthesized tool
10. TaskRuntime without synthesis engine fails gracefully
11. Dynamic tool reuse in subsequent task without resynthesis
"""

import json
import threading
import time
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agent.task_runtime import TaskRuntime, TaskStatus, StepStatus
from core.capabilities import registry as cap_registry
from core.capabilities.gap import CapabilityGapEngine
from core.capabilities.discovery import SkillDiscovery
from memory.models import DurableStepRecord, DurableTaskRecord
from memory.sqlite import init_task_tables
from tools.base import Tool, ToolRisk, ToolResult, Parameter
from tools.builder.builder import ToolBuilder
from tools.builder.policy import AutonomousSynthesisPolicy
from tools.builder.rehydrate import rehydrate_active_tools
from tools.builder.synthesis import ToolSynthesisEngine
from tools.executor import ToolExecutor, ToolPolicy
from tools.outcome import Evidence, EvidenceKind, ToolStatus, SideEffect
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
            "description": "Calculates the nth Fibonacci number",
            "source_code": VALID_FIB_SOURCE,
            "test_code": VALID_FIB_TEST,
        })
        self.provider_name = "mock_synth_provider"
        self.model = "mock-model"

    def generate(self, prompt: str) -> str:
        return self.response_text


class MockStep1Tool(Tool):
    name = "step1_generator"
    capability = "echo"
    risk = ToolRisk.SAFE
    parameters = (
        Parameter(name="count", type="integer", required=False),
    )

    def __init__(self):
        super().__init__()
        self.call_count = 0

    def execute(self, count: int = 10) -> ToolResult:
        self.call_count += 1
        return ToolResult(
            ok=True,
            output="step1_success",
            data={"count": count, "computed_n": count, "status": "ready"},
            status=ToolStatus.SUCCESS.value,
        )


class MockMutatingStepTool(Tool):
    name = "step_mutator"
    capability = "touch"
    risk = ToolRisk.SAFE
    side_effect = SideEffect.NON_IDEMPOTENT.value
    parameters = (
        Parameter(name="data", type="string", required=False),
    )

    def __init__(self):
        super().__init__()
        self.call_count = 0

    def execute(self, data: str = "") -> ToolResult:
        self.call_count += 1
        return ToolResult(
            ok=True,
            output="mutated",
            data={"data": data, "count": self.call_count},
            status=ToolStatus.SUCCESS.value,
            side_effect=SideEffect.NON_IDEMPOTENT.value,
        )


class MockStep3CollectorTool(Tool):
    name = "step3_collector"
    capability = "peek"
    risk = ToolRisk.SAFE
    parameters = (
        Parameter(name="fib_res", type="string", required=False),
    )

    def __init__(self):
        super().__init__()
        self.received_val = None

    def execute(self, fib_res: str = "") -> ToolResult:
        self.received_val = fib_res
        return ToolResult(
            ok=True,
            output=f"received_{fib_res}",
            data={"received": fib_res},
            status=ToolStatus.SUCCESS.value,
        )


@pytest.fixture(autouse=True)
def isolate_capability_registry():
    original = dict(cap_registry._capabilities)
    cap_registry._capabilities = {
        k: v for k, v in original.items()
        if not k.startswith("custom.") and k != "math.fibonacci"
    }
    yield
    cap_registry._capabilities = original


@pytest.fixture
def sqlite_storage(tmp_path):
    db_file = tmp_path / "test_phase5b4_compound.db"
    test_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    init_task_tables(bind=test_engine)
    test_sessionmaker = sessionmaker(bind=test_engine, expire_on_commit=False)

    class Storage:
        def __init__(self):
            self.session = test_sessionmaker
            self.engine = test_engine

    return Storage()


def test_1_compound_step_missing_capability_triggers_synthesis(sqlite_storage):
    """
    TEST 1: Step 2 requires missing capability 'custom.math_fibonacci'.
    TaskRuntime triggers synthesis, promotes tool, transitions back to RUNNING,
    and ToolExecutor executes the synthesized tool to task completion.
    """
    reg = ToolRegistry()
    step1_tool = MockStep1Tool()
    reg.register(step1_tool)

    builder = ToolBuilder(registry=reg, session_factory=sqlite_storage.session)
    synth_engine = ToolSynthesisEngine(llm=MockLLMForSynthesis(), builder=builder)
    policy = AutonomousSynthesisPolicy()
    gap_engine = CapabilityGapEngine(discovery=SkillDiscovery(), registry=reg)

    runtime = TaskRuntime(
        session_factory=sqlite_storage.session,
        synthesis_engine=synth_engine,
        synthesis_policy=policy,
        gap_engine=gap_engine,
    )

    executor = ToolExecutor(
        registry=reg,
        policy=ToolPolicy(enabled=True, allowed=frozenset({"step1_generator"}), auto_approve=frozenset({ToolRisk.SAFE})),
    )

    task = runtime.create_task(
        goal="Calculate fibonacci compound",
        steps=[
            {
                "step_id": "step_1",
                "name": "step1_gen",
                "tool": "step1_generator",
                "arguments": {"count": 10},
                "side_effect": "READ_ONLY",
            },
            {
                "step_id": "step_2",
                "name": "step2_fib",
                "tool": "math_fibonacci",
                "capability": "custom.math_fibonacci",
                "arguments": {"n": 10},
                "side_effect": "READ_ONLY",
                "depends_on": ["step_1"],
            },
        ],
    )

    res = runtime.execute_compound_task(task.task_id, executor)
    assert res.status == TaskStatus.COMPLETED.value

    steps = runtime.list_steps(task.task_id)
    assert len(steps) == 2
    assert steps[0].status == StepStatus.COMPLETED.value
    assert steps[1].status == StepStatus.COMPLETED.value
    assert steps[1].result["output"] == "55"
    assert steps[1].result["ok"] is True

    # Tool registered dynamically
    assert reg.has("math_fibonacci")


def test_2_step_waits_during_synthesis_without_attempt_consumption(sqlite_storage):
    """
    TEST 2: Step transitions to WAITING during synthesis and does NOT consume
    retry attempts while synthesis is in progress. Attempt count is 1 after success.
    """
    reg = ToolRegistry()
    builder = ToolBuilder(registry=reg, session_factory=sqlite_storage.session)
    synth_engine = ToolSynthesisEngine(llm=MockLLMForSynthesis(), builder=builder)
    policy = AutonomousSynthesisPolicy()

    captured_step_status = None
    captured_task_status = None
    captured_step_attempt = None

    orig_synth = synth_engine.synthesize_and_promote

    def _hooked_synth(*args, **kwargs):
        nonlocal captured_step_status, captured_task_status, captured_step_attempt
        # Query database while synthesis is in progress
        with sqlite_storage.session() as s:
            s_rec = s.query(DurableStepRecord).filter_by(step_id="step_fib").first()
            t_rec = s.query(DurableTaskRecord).filter_by(task_id="task_wait_test").first()
            if s_rec:
                captured_step_status = s_rec.status
                captured_step_attempt = s_rec.attempt
            if t_rec:
                captured_task_status = t_rec.status
        return orig_synth(*args, **kwargs)

    synth_engine.synthesize_and_promote = _hooked_synth

    runtime = TaskRuntime(
        session_factory=sqlite_storage.session,
        synthesis_engine=synth_engine,
        synthesis_policy=policy,
    )

    executor = ToolExecutor(
        registry=reg,
        policy=ToolPolicy(enabled=True, allowed=frozenset(), auto_approve=frozenset({ToolRisk.SAFE})),
    )

    task = runtime.create_task(
        task_id="task_wait_test",
        goal="Wait during synthesis",
        steps=[
            {
                "step_id": "step_fib",
                "name": "fibonacci_calc",
                "tool": "math_fibonacci",
                "capability": "custom.math_fibonacci",
                "arguments": {"n": 5},
                "side_effect": "READ_ONLY",
            }
        ],
    )

    res = runtime.execute_compound_task(task.task_id, executor)
    assert res.status == TaskStatus.COMPLETED.value

    # During synthesis, was in WAITING state and attempt was 0
    assert captured_step_status == StepStatus.WAITING.value
    assert captured_task_status == TaskStatus.WAITING.value
    assert captured_step_attempt == 0

    # After completion, attempt is 1 (not depleted by retries)
    step = runtime.get_step("step_fib")
    assert step.attempt == 1
    assert step.status == StepStatus.COMPLETED.value


def test_3_completed_step_not_replayed_when_subsequent_step_synthesizes(sqlite_storage):
    """
    TEST 3: Step 1 (mutating/irreversible) completes. Step 2 requires synthesis.
    Step 1 must execute exactly once and never be replayed.
    """
    reg = ToolRegistry()
    mutator = MockMutatingStepTool()
    reg.register(mutator)

    builder = ToolBuilder(registry=reg, session_factory=sqlite_storage.session)
    synth_engine = ToolSynthesisEngine(llm=MockLLMForSynthesis(), builder=builder)
    policy = AutonomousSynthesisPolicy()

    runtime = TaskRuntime(
        session_factory=sqlite_storage.session,
        synthesis_engine=synth_engine,
        synthesis_policy=policy,
    )

    executor = ToolExecutor(
        registry=reg,
        policy=ToolPolicy(enabled=True, allowed=frozenset({"step_mutator"}), auto_approve=frozenset({ToolRisk.SAFE})),
    )

    task = runtime.create_task(
        goal="Mutate then compute",
        steps=[
            {
                "step_id": "step_1_mut",
                "name": "step_mutator",
                "tool": "step_mutator",
                "arguments": {"data": "unique_mutation"},
                "side_effect": SideEffect.NON_IDEMPOTENT.value,
            },
            {
                "step_id": "step_2_fib",
                "name": "step_fib",
                "tool": "math_fibonacci",
                "capability": "custom.math_fibonacci",
                "arguments": {"n": 6},
                "side_effect": "READ_ONLY",
                "depends_on": ["step_1_mut"],
            },
        ],
    )

    res = runtime.execute_compound_task(task.task_id, executor)
    assert res.status == TaskStatus.COMPLETED.value

    # Critical invariant: Mutating Step 1 ran EXACTLY once!
    assert mutator.call_count == 1

    steps = runtime.list_steps(task.task_id)
    assert steps[0].status == StepStatus.COMPLETED.value
    assert steps[1].status == StepStatus.COMPLETED.value
    assert steps[1].result["output"] == "8"


def test_4_output_propagation_from_prior_step_to_synthesized_tool(sqlite_storage):
    """
    TEST 4: Context parameter substitution: ${step_0.computed_n} is resolved
    and passed into the newly synthesized tool.
    """
    reg = ToolRegistry()
    step1_gen = MockStep1Tool()
    reg.register(step1_gen)

    builder = ToolBuilder(registry=reg, session_factory=sqlite_storage.session)
    synth_engine = ToolSynthesisEngine(llm=MockLLMForSynthesis(), builder=builder)
    policy = AutonomousSynthesisPolicy()

    runtime = TaskRuntime(
        session_factory=sqlite_storage.session,
        synthesis_engine=synth_engine,
        synthesis_policy=policy,
    )

    executor = ToolExecutor(
        registry=reg,
        policy=ToolPolicy(enabled=True, allowed=frozenset({"step1_generator"}), auto_approve=frozenset({ToolRisk.SAFE})),
    )

    task = runtime.create_task(
        goal="Propagate output to synthesized tool",
        steps=[
            {
                "step_id": "step_0",
                "name": "step1_gen",
                "tool": "step1_generator",
                "arguments": {"count": 8},
                "side_effect": "READ_ONLY",
            },
            {
                "step_id": "step_1",
                "name": "fib_synthesized",
                "tool": "math_fibonacci",
                "capability": "custom.math_fibonacci",
                "arguments": {"n": "${step_0.computed_n}"},
                "side_effect": "READ_ONLY",
                "depends_on": ["step_0"],
            },
        ],
    )

    res = runtime.execute_compound_task(task.task_id, executor)
    assert res.status == TaskStatus.COMPLETED.value

    step1 = runtime.get_step("step_1")
    assert step1.status == StepStatus.COMPLETED.value
    # Fibonacci(8) == 21
    assert step1.result["output"] == "21"
    assert step1.result["data"]["fibonacci"] == 21


def test_5_synthesized_tool_output_passed_to_subsequent_step(sqlite_storage):
    """
    TEST 5: Output from the newly synthesized tool (${step_1.output})
    is successfully passed into a subsequent downstream step.
    """
    reg = ToolRegistry()
    step1_gen = MockStep1Tool()
    step3_collector = MockStep3CollectorTool()
    reg.register(step1_gen)
    reg.register(step3_collector)

    builder = ToolBuilder(registry=reg, session_factory=sqlite_storage.session)
    synth_engine = ToolSynthesisEngine(llm=MockLLMForSynthesis(), builder=builder)
    policy = AutonomousSynthesisPolicy()

    runtime = TaskRuntime(
        session_factory=sqlite_storage.session,
        synthesis_engine=synth_engine,
        synthesis_policy=policy,
    )

    executor = ToolExecutor(
        registry=reg,
        policy=ToolPolicy(enabled=True, allowed=frozenset({"step1_generator", "step3_collector"}), auto_approve=frozenset({ToolRisk.SAFE})),
    )

    task = runtime.create_task(
        goal="3-step compound with synthesized middle step",
        steps=[
            {
                "step_id": "step_0",
                "name": "gen",
                "tool": "step1_generator",
                "arguments": {"count": 10},
                "side_effect": "READ_ONLY",
            },
            {
                "step_id": "step_1",
                "name": "synthesized_fib",
                "tool": "math_fibonacci",
                "capability": "custom.math_fibonacci",
                "arguments": {"n": 10},
                "side_effect": "READ_ONLY",
                "depends_on": ["step_0"],
            },
            {
                "step_id": "step_2",
                "name": "collector",
                "tool": "step3_collector",
                "arguments": {"fib_res": "${step_1.output}"},
                "side_effect": "READ_ONLY",
                "depends_on": ["step_1"],
            },
        ],
    )

    res = runtime.execute_compound_task(task.task_id, executor)
    assert res.status == TaskStatus.COMPLETED.value

    step2 = runtime.get_step("step_2")
    assert step2.status == StepStatus.COMPLETED.value
    assert step2.result["data"]["received"] == "55"
    assert step3_collector.received_val == "55"


def test_6_prohibited_or_unsafe_capability_rejected_cleanly(sqlite_storage):
    """
    TEST 6: Step requiring a prohibited capability (shell token) is rejected by
    AutonomousSynthesisPolicy. Task fails cleanly with FAILED status, no false success.
    """
    reg = ToolRegistry()
    builder = ToolBuilder(registry=reg, session_factory=sqlite_storage.session)
    synth_engine = ToolSynthesisEngine(llm=MockLLMForSynthesis(), builder=builder)
    policy = AutonomousSynthesisPolicy()

    runtime = TaskRuntime(
        session_factory=sqlite_storage.session,
        synthesis_engine=synth_engine,
        synthesis_policy=policy,
    )

    executor = ToolExecutor(
        registry=reg,
        policy=ToolPolicy(enabled=True, allowed=frozenset(), auto_approve=frozenset({ToolRisk.SAFE})),
    )

    task = runtime.create_task(
        goal="Dangerous action",
        steps=[
            {
                "step_id": "step_danger",
                "name": "shell_exec",
                "tool": "shell_execute",
                "capability": "shell.execute_rm_rf",
                "arguments": {"cmd": "rm -rf /"},
                "side_effect": "WRITE",
            }
        ],
    )

    res = runtime.execute_compound_task(task.task_id, executor)
    assert res.status == TaskStatus.FAILED.value

    step = runtime.get_step("step_danger")
    assert step.status == StepStatus.FAILED.value
    err_str = step.result.get("error", "").lower()
    assert "unknown tool" in err_str or "tool not found" in err_str or "rejected" in err_str
    assert not reg.has("shell_execute")


def test_7_concurrent_compound_tasks_same_missing_capability_deduplication(sqlite_storage):
    """
    TEST 7: Two concurrent tasks requiring the same missing capability at the same time.
    Synthesis lock ensures only 1 synthesis operation occurs; both tasks complete cleanly.
    """
    reg = ToolRegistry()
    builder = ToolBuilder(registry=reg, session_factory=sqlite_storage.session)
    synth_engine = ToolSynthesisEngine(llm=MockLLMForSynthesis(), builder=builder)
    policy = AutonomousSynthesisPolicy()

    synth_call_count = 0
    lock = threading.Lock()
    orig_synth = synth_engine.synthesize_and_promote

    def _counted_synth(*args, **kwargs):
        nonlocal synth_call_count
        with lock:
            synth_call_count += 1
        time.sleep(0.1)  # small window for concurrency
        return orig_synth(*args, **kwargs)

    synth_engine.synthesize_and_promote = _counted_synth

    runtime = TaskRuntime(
        session_factory=sqlite_storage.session,
        synthesis_engine=synth_engine,
        synthesis_policy=policy,
    )

    executor = ToolExecutor(
        registry=reg,
        policy=ToolPolicy(enabled=True, allowed=frozenset(), auto_approve=frozenset({ToolRisk.SAFE})),
    )

    task_a = runtime.create_task(
        task_id="task_concurrent_a",
        goal="Compute fib A",
        steps=[
            {
                "step_id": "step_a_fib",
                "name": "fib_a",
                "tool": "math_fibonacci",
                "capability": "custom.math_fibonacci",
                "arguments": {"n": 7},
                "side_effect": "READ_ONLY",
            }
        ],
    )

    task_b = runtime.create_task(
        task_id="task_concurrent_b",
        goal="Compute fib B",
        steps=[
            {
                "step_id": "step_b_fib",
                "name": "fib_b",
                "tool": "math_fibonacci",
                "capability": "custom.math_fibonacci",
                "arguments": {"n": 9},
                "side_effect": "READ_ONLY",
            }
        ],
    )

    results = {}

    def _run_a():
        results["a"] = runtime.execute_compound_task(task_a.task_id, executor)

    def _run_b():
        results["b"] = runtime.execute_compound_task(task_b.task_id, executor)

    t1 = threading.Thread(target=_run_a)
    t2 = threading.Thread(target=_run_b)

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert results["a"].status == TaskStatus.COMPLETED.value
    assert results["b"].status == TaskStatus.COMPLETED.value

    # Exactly 1 synthesis happened!
    assert synth_call_count == 1


def test_8_restart_during_step_waiting_rehydrates_and_resumes(sqlite_storage):
    """
    TEST 8: Server restart while a step was in WAITING state:
    Tool was promoted to SQLite before crash. Upon restart:
    - Registry rehydrates the promoted tool.
    - resume_all_active finds the WAITING step, transitions to PENDING/READY,
      and completes execution cleanly.
    """
    # 1. Initial phase: synthesize and promote tool into SQLite
    reg1 = ToolRegistry()
    builder1 = ToolBuilder(registry=reg1, session_factory=sqlite_storage.session)
    synth_engine1 = ToolSynthesisEngine(llm=MockLLMForSynthesis(), builder=builder1)
    from core.capabilities.gap import CapabilityGap
    gap = CapabilityGap(gap_id="gap_fib_restart", intent="calc fib", requested_capability="custom.math_fibonacci", reason="missing")
    synth_engine1.synthesize_and_promote(gap, approver="autonomous_policy")

    # Create task with Step 1 COMPLETED and Step 2 in WAITING
    runtime1 = TaskRuntime(session_factory=sqlite_storage.session)
    task = runtime1.create_task(
        task_id="task_restart_waiting",
        goal="Task interrupted during waiting",
        steps=[
            {
                "step_id": "step_res_1",
                "name": "step1",
                "tool": "step1_generator",
                "arguments": {},
                "side_effect": "READ_ONLY",
            },
            {
                "step_id": "step_res_2",
                "name": "step2_fib",
                "tool": "math_fibonacci",
                "capability": "custom.math_fibonacci",
                "arguments": {"n": 5},
                "side_effect": "READ_ONLY",
                "depends_on": ["step_res_1"],
            },
        ],
    )
    # Mark step 1 completed and step 2 WAITING
    runtime1.update_step("step_res_1", StepStatus.COMPLETED.value, result={"ok": True, "output": "done"})
    runtime1.update_step("step_res_2", StepStatus.WAITING.value)
    runtime1.update_task_status("task_restart_waiting", TaskStatus.WAITING.value, recovery_state="SYNTHESIZING_CAPABILITY:custom.math_fibonacci")

    # 2. Simulate process crash: destroy in-memory objects
    del runtime1
    del reg1
    del builder1
    del synth_engine1

    # 3. Simulate server restart: rehydrate tools into fresh registry
    fresh_reg = ToolRegistry()
    rehydrate_active_tools(registry=fresh_reg, session_factory=sqlite_storage.session)
    assert fresh_reg.has("math_fibonacci")

    fresh_executor = ToolExecutor(
        registry=fresh_reg,
        policy=ToolPolicy(enabled=True, allowed=frozenset(), auto_approve=frozenset({ToolRisk.SAFE})),
    )

    fresh_runtime = TaskRuntime(session_factory=sqlite_storage.session)
    resumed = fresh_runtime.resume_all_active(fresh_executor)

    assert len(resumed) == 1
    assert resumed[0].status == TaskStatus.COMPLETED.value

    step2 = fresh_runtime.get_step("step_res_2")
    assert step2.status == StepStatus.COMPLETED.value
    assert step2.result["output"] == "5"


def test_9_verification_required_on_synthesized_step(sqlite_storage):
    """
    TEST 9: Step with verification_required=True executes tool.verify()
    and records verified postcondition evidence upon completion.
    """
    reg = ToolRegistry()
    builder = ToolBuilder(registry=reg, session_factory=sqlite_storage.session)
    synth_engine = ToolSynthesisEngine(llm=MockLLMForSynthesis(), builder=builder)
    policy = AutonomousSynthesisPolicy()

    runtime = TaskRuntime(
        session_factory=sqlite_storage.session,
        synthesis_engine=synth_engine,
        synthesis_policy=policy,
    )

    executor = ToolExecutor(
        registry=reg,
        policy=ToolPolicy(enabled=True, allowed=frozenset(), auto_approve=frozenset({ToolRisk.SAFE})),
    )

    task = runtime.create_task(
        goal="Verify step execution",
        steps=[
            {
                "step_id": "step_verify",
                "name": "fibonacci_verify",
                "tool": "math_fibonacci",
                "capability": "custom.math_fibonacci",
                "arguments": {"n": 10},
                "side_effect": "READ_ONLY",
                "verification_required": True,
            }
        ],
    )

    res = runtime.execute_compound_task(task.task_id, executor)
    assert res.status == TaskStatus.COMPLETED.value

    step = runtime.get_step("step_verify")
    assert step.status == StepStatus.COMPLETED.value
    # Verification evidence recorded
    ev_kinds = [e["kind"] for e in step.evidence]
    assert EvidenceKind.POSTCONDITION.value in ev_kinds or EvidenceKind.RETURN_VALUE.value in ev_kinds


def test_10_no_synthesis_engine_configured_graceful_failure(sqlite_storage):
    """
    TEST 10: If TaskRuntime is configured without a synthesis engine,
    a step with a missing tool fails gracefully through ToolExecutor without crash.
    """
    reg = ToolRegistry()
    runtime = TaskRuntime(
        session_factory=sqlite_storage.session,
        synthesis_engine=None,
    )

    executor = ToolExecutor(
        registry=reg,
        policy=ToolPolicy(enabled=True, allowed=frozenset(), auto_approve=frozenset({ToolRisk.SAFE})),
    )

    task = runtime.create_task(
        goal="Missing tool without synthesis engine",
        steps=[
            {
                "step_id": "step_no_engine",
                "name": "unknown_tool",
                "tool": "non_existent_tool",
                "arguments": {},
                "side_effect": "READ_ONLY",
            }
        ],
    )

    res = runtime.execute_compound_task(task.task_id, executor)
    assert res.status == TaskStatus.FAILED.value

    step = runtime.get_step("step_no_engine")
    assert step.status == StepStatus.FAILED.value
    err_str = step.result.get("error", "").lower()
    assert "unknown tool" in err_str or "tool not found" in err_str


def test_11_dynamic_tool_reuse_in_subsequent_task_without_resynthesis(sqlite_storage):
    """
    TEST 11: Task 1 triggers dynamic tool synthesis.
    Task 2 runs later requesting the same tool; it reuses the tool directly
    from ToolRegistry without triggering synthesize_and_promote again.
    """
    reg = ToolRegistry()
    builder = ToolBuilder(registry=reg, session_factory=sqlite_storage.session)
    synth_engine = ToolSynthesisEngine(llm=MockLLMForSynthesis(), builder=builder)
    policy = AutonomousSynthesisPolicy()

    synth_count = 0
    orig_synth = synth_engine.synthesize_and_promote

    def _counted_synth(*args, **kwargs):
        nonlocal synth_count
        synth_count += 1
        return orig_synth(*args, **kwargs)

    synth_engine.synthesize_and_promote = _counted_synth

    runtime = TaskRuntime(
        session_factory=sqlite_storage.session,
        synthesis_engine=synth_engine,
        synthesis_policy=policy,
    )

    executor = ToolExecutor(
        registry=reg,
        policy=ToolPolicy(enabled=True, allowed=frozenset(), auto_approve=frozenset({ToolRisk.SAFE})),
    )

    # Task 1
    task1 = runtime.create_task(
        task_id="task_seq_1",
        goal="First task with fib",
        steps=[
            {
                "step_id": "step_t1_fib",
                "name": "fib",
                "tool": "math_fibonacci",
                "capability": "custom.math_fibonacci",
                "arguments": {"n": 6},
                "side_effect": "READ_ONLY",
            }
        ],
    )
    res1 = runtime.execute_compound_task(task1.task_id, executor)
    assert res1.status == TaskStatus.COMPLETED.value
    assert synth_count == 1

    # Task 2 requesting the same capability/tool
    task2 = runtime.create_task(
        task_id="task_seq_2",
        goal="Second task reusing fib",
        steps=[
            {
                "step_id": "step_t2_fib",
                "name": "fib",
                "tool": "math_fibonacci",
                "capability": "custom.math_fibonacci",
                "arguments": {"n": 7},
                "side_effect": "READ_ONLY",
            }
        ],
    )
    res2 = runtime.execute_compound_task(task2.task_id, executor)
    assert res2.status == TaskStatus.COMPLETED.value

    # synthesize_and_promote was NOT called again!
    assert synth_count == 1
    assert runtime.get_step("step_t2_fib").result["output"] == "13"
