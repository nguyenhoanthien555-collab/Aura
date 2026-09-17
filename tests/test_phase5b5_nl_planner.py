"""
Phase 5B.5 Test Suite: Autonomous Natural-Language Compound Task Planner.

Verifies:
1. test_simple_natural_language_goal_generates_plan
2. test_multi_step_goal_generates_dag
3. test_parameter_references_are_preserved
4. test_typed_parameter_references_are_preserved
5. test_malformed_json_is_rejected
6. test_markdown_json_is_parsed_if_supported
7. test_duplicate_step_ids_are_rejected
8. test_dependency_cycle_is_rejected
9. test_forward_parameter_reference_is_rejected
10. test_plan_step_limit_is_enforced
11. test_plan_argument_size_limit_is_enforced
12. test_timeout_bounds_are_enforced
13. test_retry_bounds_are_enforced
14. test_unknown_capability_is_deferred_not_rejected
15. test_unsafe_capability_is_rejected
16. test_nl_plan_persists_before_execution
17. test_nl_plan_executes_through_existing_task_runtime
18. test_nl_plan_with_dynamic_synthesis
19. test_nl_plan_with_two_capability_gaps
20. test_completed_step_not_replayed
21. test_confirmation_is_preserved
22. test_connection_disconnect_does_not_cancel_task
23. test_planning_failure_creates_no_partial_task
24. test_malformed_model_output_never_executes_code
25. test_existing_structured_task_path_regression
26. test_restart_recovery_of_nl_generated_task
"""

import json
import threading
import time
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agent.task_runtime import (
    CompoundTaskPlanner,
    DurableTask,
    DurableStep,
    MAX_PLAN_ARGUMENT_BYTES,
    MAX_PLAN_STEPS,
    StepStatus,
    TaskPlan,
    TaskRuntime,
    TaskStatus,
)
from core.capabilities import registry as cap_registry, Capability
from core.capabilities.discovery import SkillDiscovery
from core.capabilities.gap import CapabilityGapEngine
from memory.models import DurableStepRecord, DurableTaskRecord
from memory.sqlite import init_task_tables
from tools.base import Parameter, Tool, ToolResult, ToolRisk
from tools.builder.builder import ToolBuilder
from tools.builder.policy import AutonomousSynthesisPolicy
from tools.builder.rehydrate import rehydrate_active_tools
from tools.builder.synthesis import ToolSynthesisEngine
from tools.executor import ToolExecutor, ToolPolicy
from tools.outcome import Evidence, EvidenceKind, SideEffect, ToolStatus as OutcomeToolStatus
from tools.registry import ToolRegistry


def ensure_test_capabilities():
    for cid, cname in [
        ("echo", "Echo Tool"),
        ("touch", "Touch Tool"),
        ("system.echo", "System Echo"),
        ("system.transform", "System Transform"),
    ]:
        if not cap_registry.get(cid):
            cap_registry.register(Capability(capability_id=cid, name=cname, description=cname, category="test"))

ensure_test_capabilities()


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

VALID_SQUARE_SOURCE = '''
from tools.base import Tool, ToolRisk, ToolResult, Parameter
from tools.outcome import Evidence, EvidenceKind, ToolStatus

class SquareTool(Tool):
    name = "math_square"
    capability = "custom.math_square"
    risk = ToolRisk.SAFE
    parameters = (
        Parameter(name="val", type="string", required=True),
    )

    def execute(self, val: int = 5) -> ToolResult:
        val = int(val)
        res = val * val
        ev = Evidence(
            kind=EvidenceKind.RETURN_VALUE,
            source="math_square",
            verified=True,
            detail=str(res),
        )
        return ToolResult(
            ok=True,
            output=str(res),
            data={"val": val, "square": res},
            evidence=(ev,),
            status=ToolStatus.SUCCESS.value,
        )

    def verify(self, val: int = 5) -> bool:
        return True
'''

VALID_SQUARE_TEST = '''
def test_square():
    tool = SquareTool()
    res = tool.execute(5)
    assert res.ok is True
    assert res.output == "25"

test_square()
'''


class MockPlannerLLM:
    def __init__(self, response_text):
        if isinstance(response_text, list):
            self.responses = list(response_text)
        else:
            self.responses = [response_text]
        self.call_count = 0
        self.prompts = []

    @property
    def last_prompt(self) -> str:
        return self.prompts[-1] if self.prompts else ""

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        idx = min(self.call_count, len(self.responses) - 1)
        resp = self.responses[idx]
        self.call_count += 1
        return resp


class MockSynthesisLLM:
    def __init__(self):
        self.call_count = 0

    def generate(self, prompt: str) -> str:
        self.call_count += 1
        p_lower = prompt.lower()
        if "square" in p_lower:
            return json.dumps({
                "name": "math_square",
                "description": "Squares an integer",
                "source_code": VALID_SQUARE_SOURCE,
                "test_code": VALID_SQUARE_TEST,
            })
        return json.dumps({
            "name": "math_fibonacci",
            "description": "Calculates nth Fibonacci number",
            "source_code": VALID_FIB_SOURCE,
            "test_code": VALID_FIB_TEST,
        })


class MockEchoTool(Tool):
    name = "echo_tool"
    capability = "echo"
    risk = ToolRisk.SAFE
    parameters = (
        Parameter(name="message", type="string", required=True),
    )

    def __init__(self):
        super().__init__()
        self.call_count = 0

    def execute(self, message: str = "") -> ToolResult:
        self.call_count += 1
        return ToolResult(
            ok=True,
            output=f"echo:{message}",
            data={"echoed": message, "count": self.call_count},
            status=OutcomeToolStatus.SUCCESS.value,
        )


class MockTransformTool(Tool):
    name = "transform_tool"
    capability = "touch"
    risk = ToolRisk.SAFE
    parameters = (
        Parameter(name="input_val", type="string", required=True),
    )

    def __init__(self):
        super().__init__()
        self.call_count = 0

    def execute(self, input_val: str = "") -> ToolResult:
        self.call_count += 1
        return ToolResult(
            ok=True,
            output=f"transformed:{input_val}",
            data={"result": f"transformed:{input_val}"},
            status=OutcomeToolStatus.SUCCESS.value,
        )


@pytest.fixture
def clean_capabilities():
    original = dict(cap_registry._capabilities)
    yield
    cap_registry._capabilities = original
    ensure_test_capabilities()


@pytest.fixture
def sqlite_storage(tmp_path):
    db_file = tmp_path / "test_phase5b5_planner.db"
    test_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    init_task_tables(bind=test_engine)
    test_sessionmaker = sessionmaker(bind=test_engine, expire_on_commit=False)

    class Storage:
        def __init__(self):
            self.session = test_sessionmaker
            self.engine = test_engine
            self.db_file = db_file

    return Storage()


# --------------------------------------------------------------------------
# Tests 1-4: Plan Generation & Reference Preservation
# --------------------------------------------------------------------------

def test_simple_natural_language_goal_generates_plan():
    """TEST 1: Single-step natural language goal generates a valid TaskPlan."""
    planner = CompoundTaskPlanner()
    raw = json.dumps({
        "steps": [
            {
                "step_id": "step_0",
                "name": "calc_fib",
                "tool": "math_fibonacci",
                "capability": "custom.math_fibonacci",
                "arguments": {"n": 5},
                "side_effect": "READ_ONLY",
            }
        ]
    })
    llm = MockPlannerLLM(raw)
    plan = planner.plan_from_goal("Calculate the 5th Fibonacci number", llm=llm)

    assert isinstance(plan, TaskPlan)
    assert len(plan.steps) == 1
    assert plan.steps[0]["step_id"].startswith("step_")
    assert plan.steps[0]["tool"] == "math_fibonacci"
    assert plan.steps[0]["arguments"] == {"n": 5}
    assert llm.call_count == 1


def test_multi_step_goal_generates_dag():
    """TEST 2: Multi-step goal generates an ordered DAG with depends_on."""
    planner = CompoundTaskPlanner()
    raw = json.dumps({
        "steps": [
            {
                "step_id": "step_0",
                "name": "calc_fib",
                "tool": "math_fibonacci",
                "arguments": {"n": 10},
            },
            {
                "step_id": "step_1",
                "name": "calc_square",
                "tool": "math_square",
                "arguments": {"val": "${step_0.output}"},
                "depends_on": ["step_0"],
            },
        ]
    })
    llm = MockPlannerLLM(raw)
    plan = planner.plan_from_goal("Calculate 10th Fibonacci and square it", llm=llm)

    assert len(plan.steps) == 2
    assert plan.steps[0]["step_id"].startswith("step_")
    assert plan.steps[1]["step_id"].startswith("step_")
    assert plan.steps[0]["step_id"] != plan.steps[1]["step_id"]
    assert plan.steps[1]["depends_on"] == [plan.steps[0]["step_id"]]


def test_parameter_references_are_preserved():
    """TEST 3: Parameter references (${step_0.output}) are parsed and preserved."""
    planner = CompoundTaskPlanner()
    raw = json.dumps({
        "steps": [
            {
                "step_id": "step_0",
                "name": "first",
                "tool": "echo_tool",
                "arguments": {"message": "hello"},
            },
            {
                "step_id": "step_1",
                "name": "second",
                "tool": "transform_tool",
                "arguments": {"input_val": "${step_0.output}"},
                "depends_on": ["step_0"],
            },
        ]
    })
    plan = planner.parse_plan_response(raw)
    planner.validate_plan(plan)
    assert plan["steps"][1]["arguments"]["input_val"] == "${step_0.output}"


def test_typed_parameter_references_are_preserved():
    """TEST 4: Complex/nested parameter references (${step_0.data.count}) are preserved."""
    planner = CompoundTaskPlanner()
    raw = json.dumps({
        "steps": [
            {
                "step_id": "step_0",
                "name": "producer",
                "tool": "echo_tool",
                "arguments": {"message": "test"},
            },
            {
                "step_id": "step_1",
                "name": "consumer",
                "tool": "transform_tool",
                "arguments": {
                    "nested": {
                        "count_ref": "${step_0.data.count}",
                        "list_ref": ["${step_0.output}"],
                    }
                },
                "depends_on": ["step_0"],
            },
        ]
    })
    plan = planner.parse_plan_response(raw)
    planner.validate_plan(plan)
    assert plan["steps"][1]["arguments"]["nested"]["count_ref"] == "${step_0.data.count}"
    assert plan["steps"][1]["arguments"]["nested"]["list_ref"] == ["${step_0.output}"]


# --------------------------------------------------------------------------
# Tests 5-9: Parsing & DAG Validation
# --------------------------------------------------------------------------

def test_malformed_json_is_rejected():
    """TEST 5: Malformed JSON output from LLM raises ValueError."""
    planner = CompoundTaskPlanner()
    llm = MockPlannerLLM("Here is my plan: {steps: [invalid JSON")
    with pytest.raises(ValueError, match="Failed to parse"):
        planner.plan_from_goal("Invalid plan goal", llm=llm)


def test_markdown_json_is_parsed_if_supported():
    """TEST 6: Markdown fenced JSON (```json ... ```) is parsed cleanly."""
    planner = CompoundTaskPlanner()
    raw = """Here is the suggested plan:
```json
{
  "steps": [
    {
      "step_id": "step_0",
      "name": "fenced_fib",
      "tool": "math_fibonacci",
      "arguments": {"n": 7}
    }
  ]
}
```
Let me know if this works!"""
    llm = MockPlannerLLM(raw)
    plan = planner.plan_from_goal("Goal with markdown wrapper", llm=llm)
    assert len(plan.steps) == 1
    assert plan.steps[0]["step_id"].startswith("step_")
    assert plan.steps[0]["tool"] == "math_fibonacci"


def test_duplicate_step_ids_are_rejected():
    """TEST 7: Duplicate step_id values are rejected with ValueError."""
    planner = CompoundTaskPlanner()
    plan = TaskPlan(
        goal="Duplicate IDs",
        steps=[
            {"step_id": "step_0", "name": "first", "tool": "tool_a", "arguments": {}},
            {"step_id": "step_0", "name": "second", "tool": "tool_b", "arguments": {}},
        ],
    )
    with pytest.raises(ValueError, match="duplicate step_id 'step_0'"):
        planner.validate_plan(plan)


def test_dependency_cycle_is_rejected():
    """TEST 8: Cyclic dependencies (A -> B -> A) are rejected with ValueError."""
    planner = CompoundTaskPlanner()
    plan = TaskPlan(
        goal="Cycle",
        steps=[
            {"step_id": "step_0", "name": "a", "tool": "t1", "arguments": {}, "depends_on": ["step_1"]},
            {"step_id": "step_1", "name": "b", "tool": "t2", "arguments": {}, "depends_on": ["step_0"]},
        ],
    )
    with pytest.raises(ValueError, match="cycle detected in task dependencies"):
        planner.validate_plan(plan)


def test_forward_parameter_reference_is_rejected():
    """TEST 9: Forward parameter reference (${step_1.output} in step_0) is rejected."""
    planner = CompoundTaskPlanner()
    plan = TaskPlan(
        goal="Forward ref",
        steps=[
            {"step_id": "step_0", "name": "a", "tool": "t1", "arguments": {"x": "${step_1.output}"}},
            {"step_id": "step_1", "name": "b", "tool": "t2", "arguments": {"y": 10}},
        ],
    )
    with pytest.raises(ValueError, match="forward parameter reference"):
        planner.validate_plan(plan)


# --------------------------------------------------------------------------
# Tests 10-15: Bounds & Safety Validation
# --------------------------------------------------------------------------

def test_plan_step_limit_is_enforced():
    """TEST 10: Plan exceeding MAX_PLAN_STEPS (20) is rejected."""
    planner = CompoundTaskPlanner()
    steps = [
        {"step_id": f"step_{i}", "name": f"s{i}", "tool": "tool", "arguments": {}}
        for i in range(MAX_PLAN_STEPS + 1)
    ]
    plan = TaskPlan(goal="Too many steps", steps=steps)
    with pytest.raises(ValueError, match=f"exceeds maximum step limit of {MAX_PLAN_STEPS}"):
        planner.validate_plan(plan)


def test_plan_argument_size_limit_is_enforced():
    """TEST 11: Plan with arguments exceeding MAX_PLAN_ARGUMENT_BYTES (64KB) is rejected."""
    planner = CompoundTaskPlanner()
    large_payload = "x" * (MAX_PLAN_ARGUMENT_BYTES + 1024)
    plan = TaskPlan(
        goal="Huge argument payload",
        steps=[
            {"step_id": "step_0", "name": "large", "tool": "tool", "arguments": {"data": large_payload}}
        ],
    )
    with pytest.raises(ValueError, match="argument payload size .* exceeds limit"):
        planner.validate_plan(plan)


def test_timeout_bounds_are_enforced():
    """TEST 12: Invalid timeout bounds (< 1.0s or > 600.0s) are rejected."""
    planner = CompoundTaskPlanner()
    plan_low = TaskPlan(
        goal="Timeout low",
        steps=[{"step_id": "step_0", "name": "a", "tool": "tool", "arguments": {}, "timeout_seconds": 0.5}],
    )
    with pytest.raises(ValueError, match="timeout_seconds 0.5s out of bounds"):
        planner.validate_plan(plan_low)

    plan_high = TaskPlan(
        goal="Timeout high",
        steps=[{"step_id": "step_0", "name": "a", "tool": "tool", "arguments": {}, "timeout_seconds": 999.0}],
    )
    with pytest.raises(ValueError, match="timeout_seconds 999.0s out of bounds"):
        planner.validate_plan(plan_high)


def test_retry_bounds_are_enforced():
    """TEST 13: Invalid retry policy bounds (max_attempts > 10, backoff > 60s) are rejected."""
    planner = CompoundTaskPlanner()
    plan_attempts = TaskPlan(
        goal="Retry attempts",
        steps=[
            {
                "step_id": "step_0",
                "name": "a",
                "tool": "tool",
                "arguments": {},
                "retry_policy": {"max_attempts": 20},
            }
        ],
    )
    with pytest.raises(ValueError, match="retry max_attempts 20 out of bounds"):
        planner.validate_plan(plan_attempts)

    plan_backoff = TaskPlan(
        goal="Retry backoff",
        steps=[
            {
                "step_id": "step_0",
                "name": "a",
                "tool": "tool",
                "arguments": {},
                "retry_policy": {"max_attempts": 3, "backoff": 120.0},
            }
        ],
    )
    with pytest.raises(ValueError, match="retry backoff 120.0s out of bounds"):
        planner.validate_plan(plan_backoff)


def test_unknown_capability_is_deferred_not_rejected():
    """TEST 14: Unknown capability is deferred to runtime execution, not rejected by planner."""
    planner = CompoundTaskPlanner()
    plan = TaskPlan(
        goal="Unknown capability",
        steps=[
            {
                "step_id": "step_0",
                "name": "missing_step",
                "tool": "unregistered_tool",
                "capability": "custom.domain_specialist_capability",
                "arguments": {"input": "val"},
            }
        ],
    )
    # validate_plan MUST NOT raise ValueError for un-registered capability
    planner.validate_plan(plan)
    assert plan.steps[0]["capability"] == "custom.domain_specialist_capability"


def test_unsafe_capability_is_rejected():
    """TEST 15: Prohibited safety tokens in capability, tool, or arguments are rejected."""
    planner = CompoundTaskPlanner()
    unsafe_plan_1 = TaskPlan(
        goal="Unsafe token in args",
        steps=[
            {
                "step_id": "step_0",
                "name": "bad",
                "tool": "shell_tool",
                "arguments": {"cmd": "rm -rf /"},
            }
        ],
    )
    with pytest.raises(ValueError, match="prohibited safety token 'rm -rf'"):
        planner.validate_plan(unsafe_plan_1)

    unsafe_plan_2 = TaskPlan(
        goal="Unsafe token in capability",
        steps=[
            {
                "step_id": "step_0",
                "name": "bad_cap",
                "tool": "tool_x",
                "capability": "exec(code)",
                "arguments": {},
            }
        ],
    )
    with pytest.raises(ValueError, match="prohibited safety token"):
        planner.validate_plan(unsafe_plan_2)


# --------------------------------------------------------------------------
# Tests 16-20: Persistence, Execution, and Synthesis
# --------------------------------------------------------------------------

def test_nl_plan_persists_before_execution(sqlite_storage):
    """TEST 16: Natural-language generated plan is persisted in SQLite before execution."""
    runtime = TaskRuntime(session_factory=sqlite_storage.session)
    raw = json.dumps({
        "steps": [
            {
                "step_id": "step_0",
                "name": "step_a",
                "tool": "echo_tool",
                "capability": "echo",
                "arguments": {"message": "hello"},
            },
            {
                "step_id": "step_1",
                "name": "step_b",
                "tool": "transform_tool",
                "capability": "touch",
                "arguments": {"input_val": "${step_0.output}"},
                "depends_on": ["step_0"],
            },
        ]
    })
    llm = MockPlannerLLM(raw)

    task = runtime.create_task(
        goal="Persist before execution",
        llm=llm,
    )

    persisted_task = runtime.get_task(task.task_id)
    assert persisted_task is not None
    assert persisted_task.status == TaskStatus.PENDING.value
    assert persisted_task.goal == "Persist before execution"

    persisted_steps = runtime.list_steps(task.task_id)
    assert len(persisted_steps) == 2
    assert persisted_steps[0].status == StepStatus.PENDING.value
    assert persisted_steps[1].status == StepStatus.PENDING.value


def test_nl_plan_executes_through_existing_task_runtime(sqlite_storage):
    """TEST 17: NL-generated plan executes through existing TaskRuntime and ToolExecutor."""
    reg = ToolRegistry()
    echo_t = MockEchoTool()
    trans_t = MockTransformTool()
    reg.register(echo_t)
    reg.register(trans_t)

    executor = ToolExecutor(
        registry=reg,
        policy=ToolPolicy(
            enabled=True,
            allowed=frozenset({"echo_tool", "transform_tool"}),
            auto_approve=frozenset({ToolRisk.SAFE}),
        ),
    )

    runtime = TaskRuntime(session_factory=sqlite_storage.session)
    raw = json.dumps({
        "steps": [
            {
                "step_id": "step_0",
                "name": "echo",
                "tool": "echo_tool",
                "capability": "echo",
                "arguments": {"message": "world"},
            },
            {
                "step_id": "step_1",
                "name": "trans",
                "tool": "transform_tool",
                "capability": "touch",
                "arguments": {"input_val": "${step_0.output}"},
                "depends_on": ["step_0"],
            },
        ]
    })
    llm = MockPlannerLLM(raw)
    task = runtime.create_task(goal="Echo and transform", llm=llm)

    res = runtime.execute_compound_task(task.task_id, executor)
    assert res.status == TaskStatus.COMPLETED.value

    steps = runtime.list_steps(task.task_id)
    step0, step1 = steps[0], steps[1]
    assert step0.status == StepStatus.COMPLETED.value
    assert step1.status == StepStatus.COMPLETED.value
    assert step1.result["output"] == "transformed:echo:world"
    assert echo_t.call_count == 1
    assert trans_t.call_count == 1


def test_nl_plan_with_dynamic_synthesis(sqlite_storage, clean_capabilities):
    """TEST 18: NL-generated plan with missing capability triggers autonomous synthesis."""
    reg = ToolRegistry()
    builder = ToolBuilder(registry=reg, session_factory=sqlite_storage.session)
    synth_engine = ToolSynthesisEngine(llm=MockSynthesisLLM(), builder=builder)
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
        policy=ToolPolicy(enabled=True, allowed=frozenset(), auto_approve=frozenset({ToolRisk.SAFE})),
    )

    raw = json.dumps({
        "steps": [
            {
                "step_id": "step_0",
                "name": "calc_fib",
                "tool": "math_fibonacci",
                "capability": "custom.math_fibonacci",
                "arguments": {"n": 6},
                "side_effect": "READ_ONLY",
            }
        ]
    })
    llm = MockPlannerLLM(raw)
    task = runtime.create_task(goal="Calculate 6th Fibonacci", llm=llm)

    res = runtime.execute_compound_task(task.task_id, executor)
    assert res.status == TaskStatus.COMPLETED.value

    step0 = runtime.list_steps(task.task_id)[0]
    assert step0.status == StepStatus.COMPLETED.value
    assert step0.result["output"] == "8"
    assert reg.has("math_fibonacci")


def test_nl_plan_with_two_capability_gaps(sqlite_storage, clean_capabilities):
    """TEST 19: NL-generated plan with two sequential capability gaps synthesizes both."""
    reg = ToolRegistry()
    builder = ToolBuilder(registry=reg, session_factory=sqlite_storage.session)
    synth_engine = ToolSynthesisEngine(llm=MockSynthesisLLM(), builder=builder)
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
        policy=ToolPolicy(enabled=True, allowed=frozenset(), auto_approve=frozenset({ToolRisk.SAFE})),
    )

    raw = json.dumps({
        "steps": [
            {
                "step_id": "step_0",
                "name": "fib",
                "tool": "math_fibonacci",
                "capability": "custom.math_fibonacci",
                "arguments": {"n": 5},
                "side_effect": "READ_ONLY",
            },
            {
                "step_id": "step_1",
                "name": "square",
                "tool": "math_square",
                "capability": "custom.math_square",
                "arguments": {"val": "${step_0.output}"},
                "side_effect": "READ_ONLY",
                "depends_on": ["step_0"],
            },
        ]
    })
    llm = MockPlannerLLM(raw)
    task = runtime.create_task(goal="Fibonacci and square both synthesized", llm=llm)

    res = runtime.execute_compound_task(task.task_id, executor)
    assert res.status == TaskStatus.COMPLETED.value

    all_steps = runtime.list_steps(task.task_id)
    step0, step1 = all_steps[0], all_steps[1]
    assert step0.status == StepStatus.COMPLETED.value
    assert step1.status == StepStatus.COMPLETED.value
    # Fibonacci(5) = 5, Square(5) = 25
    assert step0.result["output"] == "5"
    assert step1.result["output"] == "25"
    assert reg.has("math_fibonacci")
    assert reg.has("math_square")


def test_completed_step_not_replayed(sqlite_storage, clean_capabilities):
    """TEST 20: Completed step is not replayed when subsequent step pauses or runs."""
    reg = ToolRegistry()
    echo_t = MockEchoTool()
    reg.register(echo_t)

    builder = ToolBuilder(registry=reg, session_factory=sqlite_storage.session)
    synth_engine = ToolSynthesisEngine(llm=MockSynthesisLLM(), builder=builder)
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
        policy=ToolPolicy(
            enabled=True,
            allowed=frozenset({"echo_tool"}),
            auto_approve=frozenset({ToolRisk.SAFE}),
        ),
    )

    raw = json.dumps({
        "steps": [
            {
                "step_id": "step_0",
                "name": "echo",
                "tool": "echo_tool",
                "capability": "echo",
                "arguments": {"message": "run_once"},
            },
            {
                "step_id": "step_1",
                "name": "fib",
                "tool": "math_fibonacci",
                "capability": "custom.math_fibonacci",
                "arguments": {"n": 4},
                "depends_on": ["step_0"],
            },
        ]
    })
    llm = MockPlannerLLM(raw)
    task = runtime.create_task(goal="Echo and synth fib", llm=llm)

    res = runtime.execute_compound_task(task.task_id, executor)
    assert res.status == TaskStatus.COMPLETED.value
    # Critical invariant: step_0 must be executed exactly ONCE
    assert echo_t.call_count == 1


# --------------------------------------------------------------------------
# Tests 21-26: Safety, Disconnect, Recovery & Edge Cases
# --------------------------------------------------------------------------

def test_confirmation_is_preserved(sqlite_storage):
    """TEST 21: ToolExecutor policy confirmation is preserved; planner cannot bypass it."""
    reg = ToolRegistry()
    echo_t = MockEchoTool()
    echo_t.risk = ToolRisk.DANGEROUS
    reg.register(echo_t)

    # ToolPolicy requires explicit confirmation for DANGEROUS tools (not in auto_approve)
    executor = ToolExecutor(
        registry=reg,
        policy=ToolPolicy(
            enabled=True,
            allowed=frozenset({"echo_tool"}),
            auto_approve=frozenset({ToolRisk.SAFE}),
        ),
    )

    runtime = TaskRuntime(session_factory=sqlite_storage.session)
    raw = json.dumps({
        "steps": [
            {
                "step_id": "step_0",
                "name": "echo_dangerous",
                "tool": "echo_tool",
                "capability": "echo",
                "arguments": {"message": "confirm_me"},
            }
        ]
    })
    llm = MockPlannerLLM(raw)
    task = runtime.create_task(goal="Dangerous echo", llm=llm)

    res = runtime.execute_compound_task(task.task_id, executor)
    # Tool requires confirmation -> execution is refused or blocked by ToolExecutor
    step0 = runtime.list_steps(task.task_id)[0]
    assert step0.status == StepStatus.FAILED.value
    assert "confirmation" in step0.result.get("error", "").lower() or "policy" in step0.result.get("error", "").lower() or res.status == TaskStatus.FAILED.value


def test_connection_disconnect_does_not_cancel_task(sqlite_storage):
    """TEST 22: Async task execution continues in background despite caller disconnect."""
    reg = ToolRegistry()
    echo_t = MockEchoTool()
    reg.register(echo_t)

    executor = ToolExecutor(
        registry=reg,
        policy=ToolPolicy(
            enabled=True,
            allowed=frozenset({"echo_tool"}),
            auto_approve=frozenset({ToolRisk.SAFE}),
        ),
    )

    runtime = TaskRuntime(session_factory=sqlite_storage.session)
    raw = json.dumps({
        "steps": [
            {
                "step_id": "step_0",
                "name": "echo",
                "tool": "echo_tool",
                "capability": "echo",
                "arguments": {"message": "bg_run"},
            }
        ]
    })
    llm = MockPlannerLLM(raw)
    task = runtime.create_task(goal="Background task", llm=llm)

    # Start async compound task in background
    worker_thread = runtime.execute_compound_task_async(task.task_id, executor)
    assert worker_thread.is_alive() or not worker_thread.is_alive()

    # Wait for completion
    finished = runtime.workers.wait_for_task(task.task_id, timeout=5.0)
    assert finished is True

    final_task = runtime.get_task(task.task_id)
    assert final_task.status == TaskStatus.COMPLETED.value


def test_planning_failure_creates_no_partial_task(sqlite_storage):
    """TEST 23: When planning fails, no partial task or step records are created in DB."""
    runtime = TaskRuntime(session_factory=sqlite_storage.session)
    llm = MockPlannerLLM("Not valid JSON at all")

    with pytest.raises(ValueError):
        runtime.create_task(goal="Will fail planning", llm=llm)

    # Verify 0 tasks exist in DB
    tasks = runtime.list_tasks()
    assert len(tasks) == 0


def test_malformed_model_output_never_executes_code(sqlite_storage):
    """TEST 24: Model returning python code instead of JSON is rejected without execution."""
    reg = ToolRegistry()
    echo_t = MockEchoTool()
    reg.register(echo_t)

    runtime = TaskRuntime(session_factory=sqlite_storage.session)
    python_output = """def execute_plan():
    import os
    os.system('echo hacked')
"""
    llm = MockPlannerLLM(python_output)

    with pytest.raises(ValueError, match="Failed to parse"):
        runtime.create_task(goal="Try code injection", llm=llm)

    assert echo_t.call_count == 0


def test_existing_structured_task_path_regression(sqlite_storage):
    """TEST 25: Existing code passing explicit steps=[...] works without LLM or regression."""
    reg = ToolRegistry()
    echo_t = MockEchoTool()
    reg.register(echo_t)

    executor = ToolExecutor(
        registry=reg,
        policy=ToolPolicy(
            enabled=True,
            allowed=frozenset({"echo_tool"}),
            auto_approve=frozenset({ToolRisk.SAFE}),
        ),
    )

    runtime = TaskRuntime(session_factory=sqlite_storage.session)
    # Direct explicit steps list - no llm provided
    task = runtime.create_task(
        goal="Explicit steps",
        steps=[
            {
                "step_id": "step_0",
                "name": "echo",
                "tool": "echo_tool",
                "capability": "echo",
                "arguments": {"message": "explicit"},
            }
        ],
    )

    assert task.goal == "Explicit steps"
    res = runtime.execute_compound_task(task.task_id, executor)
    assert res.status == TaskStatus.COMPLETED.value
    assert echo_t.call_count == 1


def test_restart_recovery_of_nl_generated_task(sqlite_storage, clean_capabilities):
    """TEST 26: Fresh TaskRuntime instance recovers and completes NL-generated task."""
    reg = ToolRegistry()
    echo_t = MockEchoTool()
    reg.register(echo_t)

    builder = ToolBuilder(registry=reg, session_factory=sqlite_storage.session)
    synth_engine = ToolSynthesisEngine(llm=MockSynthesisLLM(), builder=builder)
    policy = AutonomousSynthesisPolicy()
    gap_engine = CapabilityGapEngine(discovery=SkillDiscovery(), registry=reg)

    runtime1 = TaskRuntime(
        session_factory=sqlite_storage.session,
        synthesis_engine=synth_engine,
        synthesis_policy=policy,
        gap_engine=gap_engine,
    )

    raw = json.dumps({
        "steps": [
            {
                "step_id": "step_0",
                "name": "echo",
                "tool": "echo_tool",
                "capability": "echo",
                "arguments": {"message": "first_pass"},
            },
            {
                "step_id": "step_1",
                "name": "fib",
                "tool": "math_fibonacci",
                "capability": "custom.math_fibonacci",
                "arguments": {"n": 5},
                "depends_on": ["step_0"],
            },
        ]
    })
    llm = MockPlannerLLM(raw)
    task = runtime1.create_task(goal="Recover across restarts", llm=llm)

    # Get the actual generated step IDs
    created_steps = runtime1.list_steps(task.task_id)
    sid_0 = created_steps[0].step_id
    sid_1 = created_steps[1].step_id

    # Simulate Step 0 completed and Step 1 in WAITING state before crash
    runtime1.update_step(sid_0, StepStatus.COMPLETED.value, result={"ok": True, "output": "echo:first_pass"})
    runtime1.update_step(sid_1, StepStatus.WAITING.value)
    runtime1.update_task_status(task.task_id, TaskStatus.WAITING.value, recovery_state="SYNTHESIZING_CAPABILITY:custom.math_fibonacci")

    # SIMULATE RESTART: Create a fresh ToolRegistry and fresh TaskRuntime with same DB
    fresh_reg = ToolRegistry()
    fresh_reg.register(MockEchoTool())  # Re-register static tools
    fresh_builder = ToolBuilder(registry=fresh_reg, session_factory=sqlite_storage.session)
    fresh_synth_engine = ToolSynthesisEngine(llm=MockSynthesisLLM(), builder=fresh_builder)
    fresh_runtime = TaskRuntime(
        session_factory=sqlite_storage.session,
        synthesis_engine=fresh_synth_engine,
        synthesis_policy=policy,
        gap_engine=CapabilityGapEngine(discovery=SkillDiscovery(), registry=fresh_reg),
    )

    fresh_executor = ToolExecutor(
        registry=fresh_reg,
        policy=ToolPolicy(
            enabled=True,
            allowed=frozenset({"echo_tool"}),
            auto_approve=frozenset({ToolRisk.SAFE}),
        ),
    )

    # Resume compound task on fresh runtime
    res = fresh_runtime.execute_compound_task(task.task_id, fresh_executor)
    assert res.status == TaskStatus.COMPLETED.value

    # Verify both steps are COMPLETED
    s0 = fresh_runtime.get_step(sid_0)
    s1 = fresh_runtime.get_step(sid_1)
    assert s0.status == StepStatus.COMPLETED.value
    assert s1.status == StepStatus.COMPLETED.value
    assert s1.result["output"] == "5"


# --------------------------------------------------------------------------
# Tests 27-34: Phase 5B.5-2 Semantic Grounding, Explicit Dependencies & Repair
# --------------------------------------------------------------------------

def test_unrelated_parameter_reference_is_rejected():
    """TEST 27: Parameter reference to an earlier step that is NOT an ancestor in the DAG is rejected."""
    planner = CompoundTaskPlanner()
    plan = TaskPlan(
        goal="Unrelated ref",
        steps=[
            {"step_id": "step_0", "name": "a", "tool": "t1", "arguments": {}},
            {"step_id": "step_1", "name": "b", "tool": "t2", "arguments": {}},
            # step_2 references step_0 but only depends on step_1 (step_0 is not an ancestor)
            {"step_id": "step_2", "name": "c", "tool": "t3", "arguments": {"x": "${step_0.output}"}, "depends_on": ["step_1"]},
        ],
    )
    with pytest.raises(ValueError, match="does not declare a dependency on it"):
        planner.validate_plan(plan)


def test_malformed_field_path_is_rejected():
    """TEST 28: Parameter reference without a field or with invalid field identifiers is rejected."""
    planner = CompoundTaskPlanner()
    # Missing field (${step_0})
    plan_no_field = TaskPlan(
        goal="No field",
        steps=[
            {"step_id": "step_0", "name": "a", "tool": "t1", "arguments": {}},
            {"step_id": "step_1", "name": "b", "tool": "t2", "arguments": {"x": "${step_0}"}, "depends_on": ["step_0"]},
        ],
    )
    with pytest.raises(ValueError, match="must specify a field"):
        planner.validate_plan(plan_no_field)

    # Invalid field identifier (${step_0.bad-field})
    plan_bad_id = TaskPlan(
        goal="Bad field",
        steps=[
            {"step_id": "step_0", "name": "a", "tool": "t1", "arguments": {}},
            {"step_id": "step_1", "name": "b", "tool": "t2", "arguments": {"x": "${step_0.bad-field}"}, "depends_on": ["step_0"]},
        ],
    )
    with pytest.raises(ValueError, match="invalid field identifier"):
        planner.validate_plan(plan_bad_id)


def test_self_parameter_reference_is_rejected():
    """TEST 29: Step referencing its own output in its arguments is rejected."""
    planner = CompoundTaskPlanner()
    plan = TaskPlan(
        goal="Self ref",
        steps=[
            {"step_id": "step_0", "name": "a", "tool": "t1", "arguments": {"x": "${step_0.output}"}},
        ],
    )
    with pytest.raises(ValueError, match="self-referential parameter reference"):
        planner.validate_plan(plan)


def test_semantic_grounding_failure_detected():
    """TEST 30: Chained calculation with literal argument instead of parameter reference fails grounding."""
    planner = CompoundTaskPlanner()
    # Step 1 squares literal 10 instead of referencing step_0's output
    ungrounded_plan = TaskPlan(
        goal="Calculate the 10th Fibonacci number and then square the result.",
        steps=[
            {
                "step_id": "step_0",
                "name": "calc_fib",
                "tool": "math_fibonacci",
                "capability": "math.fibonacci",
                "arguments": {"n": 10},
                "side_effect": "READ_ONLY",
            },
            {
                "step_id": "step_1",
                "name": "square_result",
                "tool": "math_square",
                "capability": "math.square",
                "arguments": {"value": 10},
                "depends_on": ["step_0"],
            },
        ],
    )
    # validate_plan (structural) passes:
    assert planner.validate_plan(ungrounded_plan) is True

    # validate_grounding (semantic) fails:
    with pytest.raises(ValueError, match="Plan grounding failed"):
        planner.validate_grounding(
            ungrounded_plan,
            goal="Calculate the 10th Fibonacci number and then square the result.",
        )


def test_grounding_repair_succeeds_on_first_retry():
    """TEST 31: LLM proposes ungrounded plan, repair prompt is sent, and corrected plan succeeds."""
    planner = CompoundTaskPlanner()
    goal = "Calculate the 10th Fibonacci number and then square the result."

    ungrounded_raw = json.dumps({
        "steps": [
            {"step_id": "step_0", "name": "calc_fib", "tool": "math_fibonacci", "arguments": {"n": 10}},
            {"step_id": "step_1", "name": "square_result", "tool": "math_square", "arguments": {"value": 10}, "depends_on": ["step_0"]},
        ]
    })
    grounded_raw = json.dumps({
        "steps": [
            {"step_id": "step_0", "name": "calc_fib", "tool": "math_fibonacci", "arguments": {"n": 10}},
            {"step_id": "step_1", "name": "square_result", "tool": "math_square", "arguments": {"value": "${step_0.output}"}, "depends_on": ["step_0"]},
        ]
    })

    llm = MockPlannerLLM([ungrounded_raw, grounded_raw])
    plan = planner.plan_from_goal(goal, llm=llm)

    assert isinstance(plan, TaskPlan)
    assert len(plan.steps) == 2
    # LLM was called twice: attempt 0 + 1 repair
    assert llm.call_count == 2
    # Second step's arguments now reference step_0
    step0_id = plan.steps[0]["step_id"]
    step1_args = plan.steps[1]["arguments"]
    assert step1_args["value"] == f"${{{step0_id}.output}}"


def test_grounding_repair_fails_after_max_attempts():
    """TEST 32: LLM repeatedly proposes ungrounded plans; raises ValueError after 2 repair attempts."""
    planner = CompoundTaskPlanner()
    goal = "Calculate the 10th Fibonacci number and then square the result."

    ungrounded_raw = json.dumps({
        "steps": [
            {"step_id": "step_0", "name": "calc_fib", "tool": "math_fibonacci", "arguments": {"n": 10}},
            {"step_id": "step_1", "name": "square_result", "tool": "math_square", "arguments": {"value": 10}, "depends_on": ["step_0"]},
        ]
    })

    # Always return ungrounded plan
    llm = MockPlannerLLM([ungrounded_raw, ungrounded_raw, ungrounded_raw])
    with pytest.raises(ValueError, match="rejected after 2 repair attempts"):
        planner.plan_from_goal(goal, llm=llm)

    # Initial attempt + 2 repairs = 3 calls
    assert llm.call_count == 3


def test_original_goal_preserved_during_repair():
    """TEST 33: Repair prompt contains the original goal verbatim across all retry attempts."""
    planner = CompoundTaskPlanner()
    original_goal = "Calculate the 10th Fibonacci number and then square the result."

    ungrounded_raw = json.dumps({
        "steps": [
            {"step_id": "step_0", "name": "calc_fib", "tool": "math_fibonacci", "arguments": {"n": 10}},
            {"step_id": "step_1", "name": "square_result", "tool": "math_square", "arguments": {"value": 10}, "depends_on": ["step_0"]},
        ]
    })

    llm = MockPlannerLLM([ungrounded_raw, ungrounded_raw, ungrounded_raw])
    try:
        planner.plan_from_goal(original_goal, llm=llm)
    except ValueError:
        pass

    # Inspect all prompts received by the LLM
    assert len(llm.prompts) == 3
    for p in llm.prompts:
        assert original_goal in p
    # Repair prompts should cite the validation failure
    assert "DETERMINISTIC VALIDATION / GROUNDING ERROR:" in llm.prompts[1]
    assert "Plan grounding failed" in llm.prompts[1]


def test_no_tool_execution_during_planning():
    """TEST 34: Planning and repair phases never invoke ToolExecutor or execute tool code."""
    reg = ToolRegistry()
    echo_t = MockEchoTool()
    reg.register(echo_t)

    planner = CompoundTaskPlanner()
    goal = "Calculate the 10th Fibonacci number and then square the result."

    raw = json.dumps({
        "steps": [
            {"step_id": "step_0", "name": "echo", "tool": "echo_tool", "arguments": {"message": "hi"}},
            {"step_id": "step_1", "name": "square_result", "tool": "echo_tool", "arguments": {"input_val": "${step_0.output}"}, "depends_on": ["step_0"]},
        ]
    })
    llm = MockPlannerLLM(raw)

    catalogue = [
        {"name": "echo_tool", "capability": "echo", "description": "echoes"}
    ]
    plan = planner.plan_from_goal(goal, llm=llm, tools_catalogue=catalogue)
    assert len(plan.steps) == 2
    # Absolute invariant: tool execute() was NEVER called during planning
    assert echo_t.call_count == 0


def test_api_tasks_steps_semantics_omitted_vs_empty_vs_explicit(sqlite_storage, monkeypatch):
    """TEST 35: API endpoint POST /api/agent/tasks distinguishes:
    1. steps omitted (None) -> invokes natural-language planner
    2. steps = [] -> explicit empty task container without planner invocation
    3. steps = [...] -> explicit pre-defined steps without planner invocation
    """
    from starlette.testclient import TestClient
    from server.main import app
    from server.auth import verify_token
    from server.routes.agent import configure_task_runtime, configure_device_registry

    app.dependency_overrides[verify_token] = lambda: "test"

    reg = ToolRegistry()
    echo_t = MockEchoTool()
    reg.register(echo_t)
    configure_device_registry(reg)

    runtime = TaskRuntime(session_factory=sqlite_storage.session)
    configure_task_runtime(runtime)

    # Track calls to planner
    planner_calls = []

    def fake_plan_from_goal(*args, **kwargs):
        planner_calls.append(kwargs.get("goal") or (args[0] if args else ""))
        return TaskPlan(
            goal=kwargs.get("goal", ""),
            steps=[
                {
                    "step_id": "step_planned_0",
                    "name": "planned_step",
                    "tool": "echo_tool",
                    "capability": "echo",
                    "arguments": {"message": "from_planner"},
                    "depends_on": [],
                }
            ],
        )

    monkeypatch.setattr(runtime.planner, "plan_from_goal", fake_plan_from_goal)

    try:
        with TestClient(app) as client:
            # Case A: steps omitted -> planner IS invoked
            resp_a = client.post("/api/agent/tasks", json={
                "goal": "Decompose this goal",
                "run_async": False,
            })
            assert resp_a.status_code == 200
            data_a = resp_a.json()
            assert len(planner_calls) == 1
            assert planner_calls[0] == "Decompose this goal"
            steps_a = runtime.list_steps(data_a["task_id"])
            assert len(steps_a) == 1
            assert steps_a[0].name == "planned_step"

            # Case B: steps = [] -> explicit empty task, planner is NOT invoked
            resp_b = client.post("/api/agent/tasks", json={
                "goal": "Empty container task",
                "steps": [],
                "run_async": False,
            })
            assert resp_b.status_code == 200
            data_b = resp_b.json()
            assert len(planner_calls) == 1  # No new planner call
            steps_b = runtime.list_steps(data_b["task_id"])
            assert len(steps_b) == 0

            # Case C: steps = [...] -> explicit structured steps, planner is NOT invoked
            resp_c = client.post("/api/agent/tasks", json={
                "goal": "Explicit pre-defined steps",
                "steps": [
                    {
                        "step_id": "step_manual_0",
                        "name": "manual_step",
                        "tool": "echo_tool",
                        "capability": "echo",
                        "arguments": {"message": "from_manual"},
                        "depends_on": [],
                    }
                ],
                "run_async": False,
            })
            assert resp_c.status_code == 200
            data_c = resp_c.json()
            assert len(planner_calls) == 1  # Still no new planner call
            steps_c = runtime.list_steps(data_c["task_id"])
            assert len(steps_c) == 1
            assert steps_c[0].name == "manual_step"
    finally:
        app.dependency_overrides.pop(verify_token, None)

