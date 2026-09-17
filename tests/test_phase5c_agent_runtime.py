"""
Phase 5C Dedicated Test Suite: Coherent End-to-End Local Agent Runtime.

Covers:
- End-to-End Natural Language -> Planner -> Task -> Execution -> Evidence -> Honest Response
- Empty steps ([]) vs None API semantics in POST /api/agent/tasks
- Honest user-facing response derivation:
    COMPLETED_VERIFIED, COMPLETED_INFERRED, UNKNOWN_UNRESOLVED,
    WAITING_FOR_CONFIRMATION, WAITING_FOR_CLARIFICATION, WAITING_FOR_SYNTHESIS,
    FAILED, SYNTHESIS_FAILED, CANCELLED
- Granular status reporting (status_detail)
- Task status inspection APIs: GET steps, GET clarifications
- POST /api/agent/intent durable task integration
- Asynchronous task continuation across HTTP return
- Postcondition verification enforcement (verification_required without probe/evidence fails)
- Android LaunchApp postcondition probe verification via bridge
- Self-extension recursion bounding per task
- Crash and restart recovery with real Python subprocesses
- Cross-process database race atomicity
"""

import json
import os
import subprocess
import sys
import threading
import time
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agent.task_runtime import (
    CompoundTaskPlanner,
    DurableConfirmation,
    DurableClarification,
    DurableStep,
    DurableTask,
    StepStatus,
    TaskPlan,
    TaskRuntime,
    TaskStatus,
    format_task_user_response,
    redact_arguments,
)
from core.capabilities import registry as cap_registry, Capability
from memory.models import (
    DurableConfirmationRecord,
    DurableClarificationRecord,
    DurableStepRecord,
    DurableTaskRecord,
)
from memory.sqlite import init_task_tables
from server.routes.agent import (
    CreateTaskRequest,
    ConfirmTaskRequest,
    ClarifyTaskRequest,
    IntentRequest,
    create_durable_task,
    get_durable_task,
    list_task_steps,
    list_task_clarifications,
    confirm_durable_task,
    clarify_durable_task,
    agent_intent,
    configure_task_runtime,
    configure_device_registry,
)
from tools.base import Parameter, Tool, ToolResult, ToolRisk
from tools.executor import ToolExecutor, ToolPolicy
from tools.outcome import Evidence, EvidenceKind, SideEffect, ToolStatus as OutcomeToolStatus
from tools.registry import ToolRegistry
from tools.providers.android_bridge import LoopbackDeviceBridge
from tools.providers.android_provider import AndroidProvider, LaunchApp


def ensure_test_capabilities():
    cap_registry._capabilities.pop("test.fibonacci", None)
    for cid, cname in [
        ("test.echo", "Echo Tool"),
        ("test.mock_fib", "Mock Algo Fib"),
        ("test.square", "Square Tool"),
        ("test.dangerous_op", "Dangerous Op Tool"),
        ("test.probe_mutating", "Probe Mutating Tool"),
        ("test.no_probe_mutating", "No Probe Mutating Tool"),
    ]:
        if not cap_registry.get(cid):
            cap_registry.register(Capability(capability_id=cid, name=cname, description=cname, category="test"))


ensure_test_capabilities()


class MockEchoTool(Tool):
    name = "mock_echo"
    capability = "test.echo"
    risk = ToolRisk.SAFE
    side_effect = SideEffect.READ_ONLY
    parameters = (
        Parameter(name="message", type="string", required=True),
    )

    def execute(self, message: str = "") -> ToolResult:
        ev = Evidence(
            kind=EvidenceKind.OBSERVATION,
            source="mock_echo",
            verified=True,
            detail=message,
        )
        return ToolResult(
            ok=True,
            output=message,
            data={"echo": message},
            evidence=(ev,),
            status=OutcomeToolStatus.SUCCESS.value,
        )


class MockFibTool(Tool):
    name = "mock_fib"
    capability = "test.mock_fib"
    risk = ToolRisk.SAFE
    side_effect = SideEffect.READ_ONLY
    parameters = (
        Parameter(name="n", type="integer", required=True),
    )

    def execute(self, n: int = 10) -> ToolResult:
        n = int(n)
        a, b = 0, 1
        for _ in range(n):
            a, b = b, a + b
        ev = Evidence(
            kind=EvidenceKind.RETURN_VALUE,
            source="mock_fib",
            verified=True,
            detail=str(a),
        )
        return ToolResult(
            ok=True,
            output=str(a),
            data={"n": n, "result": a},
            evidence=(ev,),
            status=OutcomeToolStatus.SUCCESS.value,
        )


class MockSquareTool(Tool):
    name = "mock_square"
    capability = "test.square"
    risk = ToolRisk.SAFE
    side_effect = SideEffect.READ_ONLY
    parameters = (
        Parameter(name="val", type="integer", required=True),
    )

    def execute(self, val: int = 1) -> ToolResult:
        val = int(val)
        res = val * val
        ev = Evidence(
            kind=EvidenceKind.RETURN_VALUE,
            source="mock_square",
            verified=True,
            detail=str(res),
        )
        return ToolResult(
            ok=True,
            output=str(res),
            data={"squared": res, "result": res},
            evidence=(ev,),
            status=OutcomeToolStatus.SUCCESS.value,
        )


class MockDangerousTool(Tool):
    name = "mock_dangerous_op"
    capability = "test.dangerous_op"
    risk = ToolRisk.DANGEROUS
    side_effect = SideEffect.NON_IDEMPOTENT
    parameters = (
        Parameter(name="target", type="string", required=True),
    )

    def __init__(self):
        super().__init__()
        self.call_count = 0

    def execute(self, target: str = "") -> ToolResult:
        self.call_count += 1
        ev = Evidence(
            kind=EvidenceKind.POSTCONDITION,
            source="mock_dangerous_op",
            verified=True,
            detail=f"Target {target} modified",
        )
        return ToolResult(
            ok=True,
            output=f"Modified {target}",
            data={"target": target, "call_count": self.call_count},
            evidence=(ev,),
            status=OutcomeToolStatus.SUCCESS.value,
        )

    def verify(self, target: str = "") -> bool:
        return True


class MockProbeMutatingTool(Tool):
    name = "mock_probe_mutating"
    capability = "test.probe_mutating"
    risk = ToolRisk.SAFE
    side_effect = SideEffect.NON_IDEMPOTENT
    parameters = (
        Parameter(name="item", type="string", required=True),
    )

    def __init__(self):
        super().__init__()
        self.applied = False

    def execute(self, item: str = "") -> ToolResult:
        self.applied = True
        return ToolResult(
            ok=True,
            output=f"Applied {item}",
            data={"item": item},
            evidence=(
                Evidence(
                    kind=EvidenceKind.POSTCONDITION,
                    source="mock_probe_mutating",
                    verified=True,
                    detail=f"Applied {item}",
                ),
            ),
            status=OutcomeToolStatus.SUCCESS.value,
        )

    def verify(self, item: str = "") -> bool:
        return self.applied


class MockNoProbeMutatingTool(Tool):
    name = "mock_no_probe_mutating"
    capability = "test.no_probe_mutating"
    risk = ToolRisk.SAFE
    side_effect = SideEffect.NON_IDEMPOTENT
    parameters = (
        Parameter(name="data", type="string", required=True),
    )

    def execute(self, data: str = "") -> ToolResult:
        return ToolResult(
            ok=True,
            output=f"Wrote {data}",
            data={"data": data},
            status=OutcomeToolStatus.SUCCESS.value,
        )


@pytest.fixture
def test_db(tmp_path):
    db_file = tmp_path / "test_phase5c.db"
    engine = create_engine(f"sqlite:///{db_file}")
    init_task_tables(engine)
    session_factory = sessionmaker(bind=engine)
    return session_factory


@pytest.fixture
def test_registry():
    reg = ToolRegistry()
    reg.register(MockEchoTool())
    reg.register(MockFibTool())
    reg.register(MockSquareTool())
    reg.register(MockDangerousTool())
    reg.register(MockProbeMutatingTool())
    reg.register(MockNoProbeMutatingTool())
    return reg


@pytest.fixture
def test_executor(test_registry):
    return ToolExecutor(
        registry=test_registry,
        policy=ToolPolicy(
            enabled=True,
            allowed=frozenset(test_registry.names()),
            auto_approve=frozenset({ToolRisk.SAFE, ToolRisk.SENSITIVE}),
        ),
    )


@pytest.fixture
def task_runtime(test_db):
    rt = TaskRuntime(session_factory=test_db)
    configure_task_runtime(rt)
    return rt


# ===========================================================================
# 1. EMPTY STEPS VS NONE SEMANTICS (Check 1 Closeout Forensic Verification)
# ===========================================================================

@pytest.mark.asyncio
async def test_empty_steps_creates_empty_container(task_runtime, test_registry):
    configure_device_registry(test_registry)

    class MockPlanner:
        def __init__(self):
            self.planned = False

        def plan_from_goal(self, goal, llm, tools_catalogue=None, metadata=None):
            self.planned = True
            return TaskPlan(
                goal=goal,
                status="PLANNED",
                steps=[{
                    "step_id": "step_0",
                    "name": "echo_step",
                    "tool": "mock_echo",
                    "capability": "test.echo",
                    "arguments": {"message": "hello"},
                    "depends_on": [],
                    "side_effect": "READ_ONLY",
                    "timeout_seconds": 30.0,
                    "retry_policy": {"max_attempts": 3, "backoff": 1.0},
                    "idempotent": True,
                    "verification_required": False,
                }],
            )

    planner_mock = MockPlanner()
    task_runtime.planner = planner_mock

    # Calling with steps=[] creates empty container without calling planner
    req = CreateTaskRequest(
        goal="empty container",
        session_id="s1",
        steps=[],
        run_async=False,
    )
    res = await create_durable_task(req, token="valid_token")
    assert planner_mock.planned is False
    assert res["status"] in (TaskStatus.PENDING.value, TaskStatus.COMPLETED.value)
    assert len(res["steps"]) == 0


@pytest.mark.asyncio
async def test_none_steps_triggers_planner(task_runtime, test_registry):
    configure_device_registry(test_registry)

    class MockPlanner:
        def __init__(self):
            self.planned = False

        def plan_from_goal(self, goal, llm, tools_catalogue=None, metadata=None):
            self.planned = True
            return TaskPlan(
                goal=goal,
                status="PLANNED",
                steps=[{
                    "step_id": "step_0",
                    "name": "echo_step",
                    "tool": "mock_echo",
                    "capability": "test.echo",
                    "arguments": {"message": "from_none"},
                    "depends_on": [],
                    "side_effect": "READ_ONLY",
                    "timeout_seconds": 30.0,
                    "retry_policy": {"max_attempts": 3, "backoff": 1.0},
                    "idempotent": True,
                    "verification_required": False,
                }],
            )

    planner_mock = MockPlanner()
    task_runtime.planner = planner_mock

    # Calling with steps=None MUST trigger planner
    req = CreateTaskRequest(
        goal="echo from_none",
        session_id="s1",
        steps=None,
        run_async=False,
    )
    res = await create_durable_task(req, token="valid_token")
    assert planner_mock.planned is True
    assert res["status"] == TaskStatus.COMPLETED.value
    assert len(res["steps"]) == 1


@pytest.mark.asyncio
async def test_explicit_steps_bypasses_planner(task_runtime, test_registry):
    configure_device_registry(test_registry)

    class FailingPlanner:
        def plan_from_goal(self, *args, **kwargs):
            raise RuntimeError("Planner should NOT be called for explicit steps!")

    task_runtime.planner = FailingPlanner()

    # Calling with explicit non-empty steps must NOT invoke planner
    req = CreateTaskRequest(
        goal="manual task",
        session_id="s1",
        steps=[{
            "name": "manual_echo",
            "tool": "mock_echo",
            "arguments": {"message": "explicit"},
            "side_effect": "READ_ONLY",
        }],
        run_async=False,
    )
    res = await create_durable_task(req, token="valid_token")
    assert res["status"] == TaskStatus.COMPLETED.value
    assert res["steps"][0]["arguments"] == {"message": "explicit"}


# ===========================================================================
# 2. HONEST USER-FACING RESPONSES (Primary Objective Y)
# ===========================================================================

def test_honest_user_response_completed_verified(task_runtime, test_executor):
    task = task_runtime.create_task(
        goal="run probe mutating",
        session_id="s1",
        steps=[{
            "name": "mut_step",
            "tool": "mock_probe_mutating",
            "arguments": {"item": "widget"},
            "side_effect": "NON_IDEMPOTENT",
            "verification_required": True,
        }],
    )
    task = task_runtime.execute_compound_task(task.task_id, test_executor)
    assert task.status == TaskStatus.COMPLETED.value

    u_resp = task.user_response
    assert u_resp["state"] == "COMPLETED_VERIFIED"
    assert u_resp["verified"] is True
    assert "Task completed and verified successfully" in u_resp["text"]
    assert u_resp["evidence_count"] >= 1


def test_honest_user_response_completed_inferred(task_runtime, test_executor):
    task = task_runtime.create_task(
        goal="run unverified mutating",
        session_id="s1",
        steps=[{
            "name": "no_probe_step",
            "tool": "mock_no_probe_mutating",
            "arguments": {"data": "record1"},
            "side_effect": "NON_IDEMPOTENT",
            "verification_required": False,
        }],
    )
    task = task_runtime.execute_compound_task(task.task_id, test_executor)
    assert task.status == TaskStatus.COMPLETED.value

    u_resp = task.user_response
    assert u_resp["state"] == "COMPLETED_INFERRED"
    assert u_resp["verified"] is False
    assert "independent postcondition verification was not established" in u_resp["text"]


def test_honest_user_response_unknown_unresolved(task_runtime):
    task = task_runtime.create_task(
        goal="unknown task",
        session_id="s1",
        steps=[{
            "name": "timed_out_step",
            "tool": "mock_probe_mutating",
            "arguments": {"item": "lost"},
            "side_effect": "NON_IDEMPOTENT",
        }],
    )
    step = task.steps[0]
    task_runtime.update_step(step.step_id, StepStatus.UNKNOWN.value)
    task_runtime.update_task_status(
        task.task_id,
        TaskStatus.UNKNOWN.value,
        error="Step timed out during execution; outcome unknown",
    )
    t = task_runtime.get_task(task.task_id)
    u_resp = t.user_response
    assert u_resp["state"] == "UNKNOWN_UNRESOLVED"
    assert u_resp["verified"] is False
    assert "final state could not be verified" in u_resp["text"]


def test_honest_user_response_waiting_confirmation(task_runtime, test_executor):
    task = task_runtime.create_task(
        goal="dangerous operation",
        session_id="s1",
        steps=[{
            "name": "wipe_step",
            "tool": "mock_dangerous_op",
            "arguments": {"target": "data.bin"},
            "side_effect": "NON_IDEMPOTENT",
        }],
        metadata={"interactive_confirmation": True},
    )
    task = task_runtime.execute_compound_task(task.task_id, test_executor, interactive=True)
    assert task.status == TaskStatus.WAITING.value

    u_resp = task.user_response
    assert u_resp["state"] == "WAITING_FOR_CONFIRMATION"
    assert u_resp["verified"] is False
    assert "mock_dangerous_op" in u_resp["text"]
    assert "requires confirmation" in u_resp["text"]
    assert u_resp["confirmation_id"] is not None


def test_honest_user_response_waiting_clarification(task_runtime):
    task = task_runtime.create_task(
        goal="open the app",
        session_id="s1",
        steps=[],
    )
    task_runtime.update_task_status(task.task_id, TaskStatus.WAITING.value, error="Waiting for clarification")
    task_runtime.create_clarification_request(
        task_id=task.task_id,
        goal="open the app",
        questions=["Which app would you like me to open?"],
    )
    t = task_runtime.get_task(task.task_id)
    u_resp = t.user_response
    assert u_resp["state"] == "WAITING_FOR_CLARIFICATION"
    assert u_resp["verified"] is False
    assert "Which app would you like me to open?" in u_resp["text"]


def test_honest_user_response_waiting_synthesis(task_runtime):
    task = task_runtime.create_task(
        goal="compute novel math",
        session_id="s1",
        steps=[{
            "name": "synth_step",
            "tool": "novel_math_tool",
            "capability": "math.novel",
        }],
    )
    task_runtime.update_task_status(
        task.task_id,
        TaskStatus.WAITING.value,
        recovery_state="SYNTHESIZING_CAPABILITY:math.novel",
    )
    t = task_runtime.get_task(task.task_id)
    u_resp = t.user_response
    assert u_resp["state"] == "WAITING_FOR_SYNTHESIS"
    assert "math.novel" in u_resp["text"]


def test_honest_user_response_failed(task_runtime):
    task = task_runtime.create_task(
        goal="failing task",
        session_id="s1",
        steps=[{
            "name": "fail_step",
            "tool": "mock_echo",
        }],
    )
    task_runtime.update_step(
        task.steps[0].step_id,
        StepStatus.FAILED.value,
        result={"error": "Connection reset by peer"},
    )
    task_runtime.update_task_status(
        task.task_id,
        TaskStatus.FAILED.value,
        error="Step fail_step failed: Connection reset by peer",
    )
    t = task_runtime.get_task(task.task_id)
    u_resp = t.user_response
    assert u_resp["state"] == "FAILED"
    assert "Connection reset by peer" in u_resp["text"]


# ===========================================================================
# 3. VERIFICATION ENFORCEMENT & ANDROID POSTCONDITION PROBING (Objectives G & I)
# ===========================================================================

def test_verification_required_fails_if_no_probe_and_no_evidence(task_runtime, test_executor):
    task = task_runtime.create_task(
        goal="strict verification",
        session_id="s1",
        steps=[{
            "name": "strict_step",
            "tool": "mock_no_probe_mutating",
            "arguments": {"data": "strict_val"},
            "verification_required": True,
        }],
    )
    task = task_runtime.execute_compound_task(task.task_id, test_executor)
    assert task.status == TaskStatus.FAILED.value
    step = task.steps[0]
    assert step.status == StepStatus.FAILED.value
    assert "required postcondition verification, but tool 'mock_no_probe_mutating' has no verification probe" in task.last_error


def test_android_launch_app_postcondition_probe_success():
    bridge = LoopbackDeviceBridge()
    launch_tool = LaunchApp(bridge)
    assert launch_tool is not None

    bridge.foreground_package = ""
    res = launch_tool.execute(package="com.example.calculator")
    assert res.ok is True

    bridge.foreground_package = "com.example.calculator"
    assert launch_tool.verify(package="com.example.calculator") is True
    assert launch_tool.verify(package="com.other.app") is False


# ===========================================================================
# 4. TASK STATUS & GRANULAR OBSERVABILITY APIS (Primary Objective Q)
# ===========================================================================

@pytest.mark.asyncio
async def test_api_list_task_steps(task_runtime, test_registry):
    configure_device_registry(test_registry)
    task = task_runtime.create_task(
        goal="inspect steps",
        session_id="s1",
        steps=[
            {"name": "step_1", "tool": "mock_echo", "arguments": {"message": "m1"}},
            {"name": "step_2", "tool": "mock_echo", "arguments": {"message": "m2"}},
        ],
    )
    steps_res = await list_task_steps(task.task_id, token="valid_token")
    assert len(steps_res) == 2
    assert steps_res[0]["name"] == "step_1"
    assert steps_res[1]["name"] == "step_2"


@pytest.mark.asyncio
async def test_api_list_task_clarifications(task_runtime, test_registry):
    configure_device_registry(test_registry)
    task = task_runtime.create_task(goal="clarify me", session_id="s1", steps=[])
    task_runtime.create_clarification_request(
        task_id=task.task_id,
        goal="clarify me",
        questions=["Which target?"],
    )
    clars = await list_task_clarifications(task.task_id, token="valid_token")
    assert len(clars) == 1
    assert clars[0]["questions"] == ["Which target?"]
    assert clars[0]["status"] == "PENDING"


# ===========================================================================
# 5. ASYNCHRONOUS TASK CONTINUATION ACROSS HTTP RETURN (Objective J & Flow 10)
# ===========================================================================

@pytest.mark.asyncio
async def test_async_task_continues_independent_of_http(task_runtime, test_registry):
    configure_device_registry(test_registry)

    req = CreateTaskRequest(
        goal="background computation",
        session_id="s1",
        steps=[
            {"name": "fib", "tool": "mock_fib", "arguments": {"n": 5}},
            {"name": "sq", "tool": "mock_square", "arguments": {"val": 5}},
        ],
        run_async=True,
    )
    res = await create_durable_task(req, token="valid_token")
    task_id = res["task_id"]
    assert task_id is not None

    worker_finished = task_runtime.workers.wait_for_task(task_id, timeout=5.0)
    assert worker_finished is True

    final_task = task_runtime.get_task(task_id)
    assert final_task.status == TaskStatus.COMPLETED.value
    assert len(final_task.steps) == 2
    assert final_task.steps[0].status == StepStatus.COMPLETED.value
    assert final_task.steps[1].status == StepStatus.COMPLETED.value
    assert final_task.user_response["state"] == "COMPLETED_VERIFIED"


# ===========================================================================
# 6. BOUNDED SYNTHESIS RECURSION (Primary Objective N)
# ===========================================================================

def test_synthesis_recursion_bounded_per_task(task_runtime, test_executor):
    task = task_runtime.create_task(
        goal="infinite synthesis attempt",
        session_id="s1",
        steps=[
            {"name": "s1", "tool": "missing_1", "capability": "custom.missing_1"},
            {"name": "s2", "tool": "missing_2", "capability": "custom.missing_2"},
            {"name": "s3", "tool": "missing_3", "capability": "custom.missing_3"},
            {"name": "s4", "tool": "missing_4", "capability": "custom.missing_4"},
        ],
        metadata={"_synthesis_count": 3},
    )

    class DummySynthesisEngine:
        pass

    task_runtime.synthesis_engine = DummySynthesisEngine()

    step = task.steps[0]
    recovered, reason = task_runtime._maybe_recover_step_capability(task, step, test_executor)
    assert recovered is False
    assert "Maximum capability synthesis limit reached" in reason


# ===========================================================================
# 7. COMPOUND TASK RESULT CHAINING & SUBSTITUTION (Flow 2)
# ===========================================================================

def test_flow_2_compound_result_chaining(task_runtime, test_executor):
    steps = [
        {
            "step_id": "step_0",
            "name": "fib_10",
            "tool": "mock_fib",
            "arguments": {"n": 10},
            "side_effect": "READ_ONLY",
        },
        {
            "step_id": "step_1",
            "name": "square_it",
            "tool": "mock_square",
            "arguments": {"val": "${step_0.result}"},
            "depends_on": ["step_0"],
            "side_effect": "READ_ONLY",
        },
    ]
    task = task_runtime.create_task(
        goal="calculate fibonacci of 10 and square the result",
        session_id="s1",
        steps=steps,
    )
    task = task_runtime.execute_compound_task(task.task_id, test_executor)
    assert task.status == TaskStatus.COMPLETED.value
    assert task.steps[0].result["output"] == "55"
    assert task.steps[1].result["output"] == "3025"
    assert task.user_response["state"] == "COMPLETED_VERIFIED"
    assert "3025" in task.user_response["text"]


# ===========================================================================
# 8. POST /api/agent/intent WITH DURABLE=TRUE (Objective A & B)
# ===========================================================================

@pytest.mark.asyncio
async def test_api_intent_durable_execution(task_runtime, test_registry):
    configure_device_registry(test_registry)

    class MockPlanner:
        def plan_from_goal(self, goal, llm, tools_catalogue=None, metadata=None):
            return TaskPlan(
                goal=goal,
                status="PLANNED",
                steps=[{
                    "step_id": "step_0",
                    "name": "echo_step",
                    "tool": "mock_echo",
                    "arguments": {"message": "intent_verified"},
                    "depends_on": [],
                    "side_effect": "READ_ONLY",
                }],
            )

    task_runtime.planner = MockPlanner()

    req = IntentRequest(
        intent="echo intent_verified",
        session_id="intent_sess",
        durable=True,
    )
    res = await agent_intent(req, token="valid_token")
    assert res["status"] == TaskStatus.COMPLETED.value
    assert res["grounded"] is True
    assert "intent_verified" in res["reply"]
    assert res["user_response"]["state"] == "COMPLETED_VERIFIED"


# ===========================================================================
# 9. REAL PROCESS CRASH / RESTART TESTS (Primary Objective E, F & Section 31)
# ===========================================================================

def test_real_process_crash_during_waiting_for_confirmation(tmp_path):
    db_file = (tmp_path / "crash_conf.db").as_posix()
    python_exe = sys.executable

    script_1 = f"""
import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from memory.sqlite import init_task_tables
from agent.task_runtime import TaskRuntime, TaskStatus
from tools.base import Tool, ToolRisk, ToolResult, Parameter
from tools.executor import ToolExecutor, ToolPolicy
from tools.outcome import SideEffect, ToolStatus as OutcomeToolStatus
from tools.registry import ToolRegistry

class DangerousWipe(Tool):
    name = "dangerous_wipe"
    risk = ToolRisk.DANGEROUS
    side_effect = SideEffect.NON_IDEMPOTENT
    parameters = (Parameter(name="target", type="string", required=True),)
    def execute(self, target=""):
        return ToolResult(ok=True, output="wiped", status=OutcomeToolStatus.SUCCESS.value)

engine = create_engine('sqlite:///{db_file}')
init_task_tables(engine)
sf = sessionmaker(bind=engine)
rt = TaskRuntime(session_factory=sf)
reg = ToolRegistry()
reg.register(DangerousWipe())
exc = ToolExecutor(registry=reg, policy=ToolPolicy(enabled=True, allowed=frozenset(["dangerous_wipe"]), auto_approve=frozenset()))

task = rt.create_task(
    goal="wipe disk",
    session_id="s_crash",
    steps=[{{"name": "wipe", "tool": "dangerous_wipe", "arguments": {{"target": "v1"}}, "side_effect": "NON_IDEMPOTENT"}}],
    metadata={{"interactive_confirmation": True}}
)
task = rt.execute_compound_task(task.task_id, exc, interactive=True)
assert task.status == TaskStatus.WAITING.value
sys.exit(0)
"""
    p1 = subprocess.run([python_exe, "-c", script_1], capture_output=True, text=True, cwd="D:/AURA", env=dict(os.environ))
    assert p1.returncode == 0, f"Process 1 failed: {p1.stderr}"

    script_2 = f"""
import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from memory.sqlite import init_task_tables
from agent.task_runtime import TaskRuntime, TaskStatus

engine = create_engine('sqlite:///{db_file}')
sf = sessionmaker(bind=engine)
rt = TaskRuntime(session_factory=sf)
tasks = rt.list_tasks()
assert len(tasks) == 1
task = tasks[0]
assert task.status == TaskStatus.WAITING.value
assert task.recovery_state == "WAITING_FOR_CONFIRMATION"

conf = rt.get_confirmation_for_step(task.steps[0].step_id)
assert conf is not None
assert conf.status == "PENDING"
assert conf.tool == "dangerous_wipe"
sys.exit(0)
"""
    p2 = subprocess.run([python_exe, "-c", script_2], capture_output=True, text=True, cwd="D:/AURA", env=dict(os.environ))
    assert p2.returncode == 0, f"Process 2 failed: {p2.stderr}"


def test_real_process_crash_during_waiting_for_clarification(tmp_path):
    db_file = (tmp_path / "crash_clar.db").as_posix()
    python_exe = sys.executable

    script_1 = f"""
import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from memory.sqlite import init_task_tables
from agent.task_runtime import TaskRuntime, TaskStatus

engine = create_engine('sqlite:///{db_file}')
init_task_tables(engine)
sf = sessionmaker(bind=engine)
rt = TaskRuntime(session_factory=sf)

task = rt.create_task(goal="open app", session_id="s_clar", steps=[])
rt.update_task_status(task.task_id, TaskStatus.WAITING.value, error="Waiting for clarification", recovery_state="WAITING_FOR_CLARIFICATION")
clar = rt.create_clarification_request(task_id=task.task_id, goal="open app", questions=["Which app?"])
sys.exit(0)
"""
    p1 = subprocess.run([python_exe, "-c", script_1], capture_output=True, text=True, cwd="D:/AURA", env=dict(os.environ))
    assert p1.returncode == 0, f"Process 1 failed: {p1.stderr}"

    script_2 = f"""
import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from agent.task_runtime import TaskRuntime, TaskStatus

engine = create_engine('sqlite:///{db_file}')
sf = sessionmaker(bind=engine)
rt = TaskRuntime(session_factory=sf)
tasks = rt.list_tasks()
assert len(tasks) == 1
task = tasks[0]
assert task.status == TaskStatus.WAITING.value
assert task.recovery_state == "WAITING_FOR_CLARIFICATION"

clars = rt.list_clarifications_for_task(task.task_id)
assert len(clars) == 1
assert clars[0].status == "PENDING"
assert clars[0].questions == ["Which app?"]
sys.exit(0)
"""
    p2 = subprocess.run([python_exe, "-c", script_2], capture_output=True, text=True, cwd="D:/AURA", env=dict(os.environ))
    assert p2.returncode == 0, f"Process 2 failed: {p2.stderr}"


def test_real_process_crash_after_completed_step(tmp_path):
    db_file = (tmp_path / "crash_step0.db").as_posix()
    python_exe = sys.executable

    script_1 = f"""
import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from memory.sqlite import init_task_tables
from agent.task_runtime import TaskRuntime, TaskStatus, StepStatus
from tools.base import Tool, ToolRisk, ToolResult, Parameter
from tools.executor import ToolExecutor, ToolPolicy
from tools.outcome import SideEffect, ToolStatus as OutcomeToolStatus
from tools.registry import ToolRegistry
from core.capabilities import registry as cap_registry
from core.capabilities.models import Capability

if not cap_registry.get("test.counter"):
    cap_registry.register(Capability(capability_id="test.counter", name="Counter Tool", description="Counter Tool", category="test"))

class CounterTool(Tool):
    name = "counter_tool"
    capability = "test.counter"
    risk = ToolRisk.SAFE
    side_effect = SideEffect.READ_ONLY
    def __init__(self):
        super().__init__()
        self.count = 0
    def execute(self):
        self.count += 1
        return ToolResult(ok=True, output=str(self.count), status=OutcomeToolStatus.SUCCESS.value)

engine = create_engine('sqlite:///{db_file}')
init_task_tables(engine)
sf = sessionmaker(bind=engine)
rt = TaskRuntime(session_factory=sf)

task = rt.create_task(
    goal="two steps",
    session_id="s_step",
    steps=[
        {{"step_id": "st_0", "name": "step_0", "tool": "counter_tool"}},
        {{"step_id": "st_1", "name": "step_1", "tool": "counter_tool"}},
    ]
)
rt.update_step("st_0", StepStatus.COMPLETED.value, result={{"ok": True, "output": "1"}})
rt.update_task_status(task.task_id, TaskStatus.RUNNING.value, current_step_id="st_1")
sys.exit(0)
"""
    p1 = subprocess.run([python_exe, "-c", script_1], capture_output=True, text=True, cwd="D:/AURA", env=dict(os.environ))
    assert p1.returncode == 0, f"Process 1 failed: {p1.stderr}"

    script_2 = f"""
import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from agent.task_runtime import TaskRuntime, TaskStatus, StepStatus
from tools.base import Tool, ToolRisk, ToolResult
from tools.executor import ToolExecutor, ToolPolicy
from tools.outcome import SideEffect, ToolStatus as OutcomeToolStatus
from tools.registry import ToolRegistry
from core.capabilities import registry as cap_registry
from core.capabilities.models import Capability

if not cap_registry.get("test.counter"):
    cap_registry.register(Capability(capability_id="test.counter", name="Counter Tool", description="Counter Tool", category="test"))

call_records = []
class CounterTool(Tool):
    name = "counter_tool"
    capability = "test.counter"
    risk = ToolRisk.SAFE
    side_effect = SideEffect.READ_ONLY
    def execute(self):
        call_records.append(1)
        return ToolResult(ok=True, output="2", status=OutcomeToolStatus.SUCCESS.value)

engine = create_engine('sqlite:///{db_file}')
sf = sessionmaker(bind=engine)
rt = TaskRuntime(session_factory=sf)
reg = ToolRegistry()
reg.register(CounterTool())
exc = ToolExecutor(registry=reg, policy=ToolPolicy(enabled=True, allowed=frozenset(["counter_tool"]), auto_approve=frozenset([ToolRisk.SAFE])))

tasks = rt.list_tasks()
task = tasks[0]
recovered_task = rt.execute_compound_task(task.task_id, exc)
assert recovered_task.status == TaskStatus.COMPLETED.value
assert len(call_records) == 1
sys.exit(0)
"""
    p2 = subprocess.run([python_exe, "-c", script_2], capture_output=True, text=True, cwd="D:/AURA", env=dict(os.environ))
    assert p2.returncode == 0, f"Process 2 failed: {p2.stderr}"


# ===========================================================================
# 10. CROSS-PROCESS RACE ATOMICITY (Section 32)
# ===========================================================================

def test_cross_process_race_atomic_confirmation(tmp_path):
    db_file = (tmp_path / "race_conf.db").as_posix()
    python_exe = sys.executable

    engine = create_engine(f"sqlite:///{db_file}")
    init_task_tables(engine)
    sf = sessionmaker(bind=engine)
    rt = TaskRuntime(session_factory=sf)
    task = rt.create_task(
        goal="race task",
        session_id="s_race",
        steps=[{"step_id": "st_race", "name": "race_step", "tool": "mock_dangerous_op", "arguments": {"target": "data"}}],
    )
    conf = rt.create_confirmation_request(
        task_id=task.task_id,
        step_id="st_race",
        tool="mock_dangerous_op",
        arguments={"target": "data"},
    )
    conf_id = conf.confirmation_id

    proc_script = f"""
import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from agent.task_runtime import TaskRuntime

engine = create_engine('sqlite:///{db_file}')
sf = sessionmaker(bind=engine)
rt = TaskRuntime(session_factory=sf)
try:
    rt.resolve_confirmation(task_id='{task.task_id}', confirmation_id='{conf_id}', decision='APPROVED')
    print('APPROVED_OK')
    sys.exit(0)
except Exception as e:
    print('ERROR:', e)
    sys.exit(1)
"""

    p1 = subprocess.Popen([python_exe, "-c", proc_script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd="D:/AURA", env=dict(os.environ))
    p2 = subprocess.Popen([python_exe, "-c", proc_script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd="D:/AURA", env=dict(os.environ))

    out1, _ = p1.communicate()
    out2, _ = p2.communicate()

    successes = sum(1 for code in (p1.returncode, p2.returncode) if code == 0)
    assert successes == 1, f"Expected exactly 1 success in race, got {successes}. Out1: {out1}, Out2: {out2}"
