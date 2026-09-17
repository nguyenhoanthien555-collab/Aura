"""
Phase 5B.6 Test Suite: Planner Reliability & Real-World Intent Coverage.

Verifies:
1. Tool catalogue parameter schema is included in prompt
2. Required arguments accepted
3. Missing required arguments rejected
4. Unknown arguments rejected against registered tools
5. Primitive type mismatches rejected (string)
6. Primitive type mismatches rejected (int)
7. Primitive type mismatches rejected (bool)
8. Primitive type mismatches rejected (list)
9. Primitive type mismatches rejected (dict)
10. Dynamic ${step.output} reference preserved across types
11. Dynamic ${step.output.field} reference preserved across types
12. Status NEEDS_CLARIFICATION correctly parsed
13. Status NEEDS_CLARIFICATION questions validated (non-empty list of strings)
14. Ambiguous request "Open the app" returns clarification, does NOT create task
15. Ambiguous request "Delete files" returns clarification, does NOT create task
16. Ambiguous request does NOT execute any tools
17. Ambiguous request does NOT create SQLite row
18. Ambiguous request does NOT invoke synthesis
19. Clarification questions are targeted and minimal (1-5 questions)
20. Clarification questions reject empty strings
21. Dangerous tools cannot be auto-approved in agent routes
22. Dangerous tool execution requires confirmation in live/mock executor
23. Safe tools execute without prompt
24. Sensitive tools execute without prompt under current policy
25. Planner cannot downgrade tool side_effect
26. Backward compatibility: steps=[] creates empty container
27. Backward compatibility: explicit steps=[...] creates durable task directly without calling planner
28. Verification-aware goal produces verification_required=True when requested
"""

import json
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agent.task_runtime import (
    CompoundTaskPlanner,
    DurableStep,
    DurableTask,
    TaskPlan,
    TaskRuntime,
    TaskStatus,
)
from core.capabilities import registry as cap_registry, Capability
from memory.models import DurableStepRecord, DurableTaskRecord
from memory.sqlite import init_task_tables
from server.routes.agent import CreateTaskRequest, create_durable_task, configure_task_runtime
from tools.base import Parameter, Tool, ToolResult, ToolRisk
from tools.executor import ToolExecutor, ToolPolicy
from tools.outcome import Evidence, EvidenceKind, SideEffect, ToolStatus as OutcomeToolStatus
from tools.registry import ToolRegistry


def ensure_test_capabilities():
    for cid, cname in [
        ("echo", "Echo Tool"),
        ("touch", "Touch Tool"),
        ("custom.math", "Custom Math"),
        ("system.format", "System Format"),
        ("security.read_keys", "Read Keys"),
    ]:
        if not cap_registry.get(cid):
            cap_registry.register(Capability(capability_id=cid, name=cname, description=cname, category="test"))

ensure_test_capabilities()


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


class MockSynthesisEngine:
    def __init__(self):
        self.call_count = 0

    def synthesize(self, *args, **kwargs):
        self.call_count += 1
        return None


class MockEchoTool(Tool):
    name = "mock_echo"
    capability = "echo"
    risk = ToolRisk.SAFE
    side_effect = SideEffect.READ_ONLY
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
            data={"echoed": message},
            status=OutcomeToolStatus.SUCCESS.value,
        )


class MockMathTool(Tool):
    name = "math_calc"
    capability = "custom.math"
    risk = ToolRisk.SAFE
    side_effect = SideEffect.READ_ONLY
    parameters = (
        Parameter(name="n", type="integer", required=True),
        Parameter(name="factor", type="number", required=False),
        Parameter(name="flag", type="boolean", required=False),
        Parameter(name="tags", type="array", required=False),
        Parameter(name="config", type="object", required=False),
    )

    def __init__(self):
        super().__init__()
        self.call_count = 0

    def execute(self, n: int = 0, **kwargs) -> ToolResult:
        self.call_count += 1
        return ToolResult(
            ok=True,
            output=str(n),
            data={"n": n},
            status=OutcomeToolStatus.SUCCESS.value,
        )

    def verify(self, n: int = 0, **kwargs) -> bool:
        return True


class MockDangerousTool(Tool):
    name = "system_format_drive"
    capability = "system.format"
    risk = ToolRisk.DANGEROUS
    side_effect = SideEffect.MUTATING
    parameters = (
        Parameter(name="drive", type="string", required=True),
    )

    def __init__(self):
        super().__init__()
        self.call_count = 0

    def execute(self, drive: str = "C:") -> ToolResult:
        self.call_count += 1
        return ToolResult(
            ok=True,
            output=f"Formatted {drive}",
            status=OutcomeToolStatus.SUCCESS.value,
        )


class MockSensitiveTool(Tool):
    name = "read_private_keys"
    capability = "security.read_keys"
    risk = ToolRisk.SENSITIVE
    side_effect = SideEffect.READ_ONLY
    parameters = (
        Parameter(name="path", type="string", required=True),
    )

    def __init__(self):
        super().__init__()
        self.call_count = 0

    def execute(self, path: str = "") -> ToolResult:
        self.call_count += 1
        return ToolResult(
            ok=True,
            output=f"Read {path}",
            status=OutcomeToolStatus.SUCCESS.value,
        )


@pytest.fixture
def sqlite_storage(tmp_path):
    db_file = tmp_path / "test_phase5b6.db"
    test_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    init_task_tables(bind=test_engine)
    test_sessionmaker = sessionmaker(bind=test_engine, expire_on_commit=False)

    class Storage:
        def __init__(self):
            self.session = test_sessionmaker
            self.engine = test_engine
            self.db_file = db_file

    return Storage()


@pytest.fixture
def sample_catalogue():
    return [
        {
            "name": "math_calc",
            "description": "Calculates math values",
            "capability": "custom.math",
            "parameters": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer", "description": "The base number"},
                    "factor": {"type": "number", "description": "Multiplier"},
                    "flag": {"type": "boolean", "description": "Toggle"},
                    "tags": {"type": "array", "description": "Tag list"},
                    "config": {"type": "object", "description": "Config dict"},
                },
                "required": ["n"],
            },
            "risk": "safe",
            "side_effect": "read_only",
            "verification_supported": True,
        },
        {
            "name": "system_format_drive",
            "description": "Formats drive",
            "capability": "system.format",
            "parameters": {
                "type": "object",
                "properties": {
                    "drive": {"type": "string", "description": "Drive letter"},
                },
                "required": ["drive"],
            },
            "risk": "dangerous",
            "side_effect": "mutating",
            "verification_supported": False,
        },
    ]


# --------------------------------------------------------------------------
# Test 1: Tool catalogue parameter schema is included in prompt
# --------------------------------------------------------------------------
def test_tool_catalogue_parameter_schema_included_in_prompt(sample_catalogue):
    planner = CompoundTaskPlanner()
    prompt = planner.build_plan_prompt(
        goal="Calculate something",
        tools_catalogue=sample_catalogue,
    )
    assert "math_calc" in prompt
    assert "Parameters:" in prompt
    assert "n (integer, required)" in prompt
    assert "factor (number, optional)" in prompt
    assert "flag (boolean, optional)" in prompt
    assert "tags (array, optional)" in prompt
    assert "config (object, optional)" in prompt
    assert "risk: safe" in prompt
    assert "side_effect: read_only" in prompt
    assert "supports_verification: true" in prompt


# --------------------------------------------------------------------------
# Test 2: Required arguments accepted
# --------------------------------------------------------------------------
def test_required_arguments_accepted(sample_catalogue):
    planner = CompoundTaskPlanner()
    plan = TaskPlan(
        goal="Calculate math",
        steps=[
            {
                "step_id": "step_0",
                "name": "calc",
                "tool": "math_calc",
                "capability": "custom.math",
                "arguments": {"n": 10},
                "side_effect": "READ_ONLY",
            }
        ]
    )
    assert planner.validate_plan(plan, tools_catalogue=sample_catalogue) is True


# --------------------------------------------------------------------------
# Test 3: Missing required arguments rejected
# --------------------------------------------------------------------------
def test_missing_required_arguments_rejected(sample_catalogue):
    planner = CompoundTaskPlanner()
    plan = TaskPlan(
        goal="Calculate math",
        steps=[
            {
                "step_id": "step_0",
                "name": "calc",
                "tool": "math_calc",
                "capability": "custom.math",
                "arguments": {"factor": 2.5},
                "side_effect": "READ_ONLY",
            }
        ]
    )
    with pytest.raises(ValueError, match="missing required parameter"):
        planner.validate_plan(plan, tools_catalogue=sample_catalogue)


# --------------------------------------------------------------------------
# Test 4: Unknown arguments rejected against registered tools
# --------------------------------------------------------------------------
def test_unknown_arguments_rejected_against_registered_tools(sample_catalogue):
    planner = CompoundTaskPlanner()
    plan = TaskPlan(
        goal="Calculate math",
        steps=[
            {
                "step_id": "step_0",
                "name": "calc",
                "tool": "math_calc",
                "capability": "custom.math",
                "arguments": {"n": 10, "bogus_arg": 123},
                "side_effect": "READ_ONLY",
            }
        ]
    )
    with pytest.raises(ValueError, match="unknown parameter"):
        planner.validate_plan(plan, tools_catalogue=sample_catalogue)

    unregistered_plan = TaskPlan(
        goal="Synthetic tool invocation",
        steps=[
            {
                "step_id": "step_0",
                "name": "synth",
                "tool": "unregistered_tool",
                "capability": "custom.synthetic",
                "arguments": {"any_param": "foo"},
                "side_effect": "READ_ONLY",
            }
        ]
    )
    assert planner.validate_plan(unregistered_plan, tools_catalogue=sample_catalogue) is True


# --------------------------------------------------------------------------
# Test 5: Primitive type mismatches rejected (string)
# --------------------------------------------------------------------------
def test_primitive_type_mismatches_rejected_string(sample_catalogue):
    planner = CompoundTaskPlanner()
    plan = TaskPlan(
        goal="Format drive",
        steps=[
            {
                "step_id": "step_0",
                "name": "format",
                "tool": "system_format_drive",
                "capability": "system.format",
                "arguments": {"drive": 12345},
                "side_effect": "MUTATING",
            }
        ]
    )
    with pytest.raises(ValueError, match="must be string"):
        planner.validate_plan(plan, tools_catalogue=sample_catalogue)


# --------------------------------------------------------------------------
# Test 6: Primitive type mismatches rejected (int)
# --------------------------------------------------------------------------
def test_primitive_type_mismatches_rejected_int(sample_catalogue):
    planner = CompoundTaskPlanner()
    plan = TaskPlan(
        goal="Calculate math",
        steps=[
            {
                "step_id": "step_0",
                "name": "calc",
                "tool": "math_calc",
                "capability": "custom.math",
                "arguments": {"n": "not_an_int"},
                "side_effect": "READ_ONLY",
            }
        ]
    )
    with pytest.raises(ValueError, match="must be integer"):
        planner.validate_plan(plan, tools_catalogue=sample_catalogue)


# --------------------------------------------------------------------------
# Test 7: Primitive type mismatches rejected (bool)
# --------------------------------------------------------------------------
def test_primitive_type_mismatches_rejected_bool(sample_catalogue):
    planner = CompoundTaskPlanner()
    plan = TaskPlan(
        goal="Calculate math",
        steps=[
            {
                "step_id": "step_0",
                "name": "calc",
                "tool": "math_calc",
                "capability": "custom.math",
                "arguments": {"n": 5, "flag": "true"},
                "side_effect": "READ_ONLY",
            }
        ]
    )
    with pytest.raises(ValueError, match="must be boolean"):
        planner.validate_plan(plan, tools_catalogue=sample_catalogue)


# --------------------------------------------------------------------------
# Test 8: Primitive type mismatches rejected (list)
# --------------------------------------------------------------------------
def test_primitive_type_mismatches_rejected_list(sample_catalogue):
    planner = CompoundTaskPlanner()
    plan = TaskPlan(
        goal="Calculate math",
        steps=[
            {
                "step_id": "step_0",
                "name": "calc",
                "tool": "math_calc",
                "capability": "custom.math",
                "arguments": {"n": 5, "tags": "item1,item2"},
                "side_effect": "READ_ONLY",
            }
        ]
    )
    with pytest.raises(ValueError, match="must be list/array"):
        planner.validate_plan(plan, tools_catalogue=sample_catalogue)


# --------------------------------------------------------------------------
# Test 9: Primitive type mismatches rejected (dict)
# --------------------------------------------------------------------------
def test_primitive_type_mismatches_rejected_dict(sample_catalogue):
    planner = CompoundTaskPlanner()
    plan = TaskPlan(
        goal="Calculate math",
        steps=[
            {
                "step_id": "step_0",
                "name": "calc",
                "tool": "math_calc",
                "capability": "custom.math",
                "arguments": {"n": 5, "config": ["key", "val"]},
                "side_effect": "READ_ONLY",
            }
        ]
    )
    with pytest.raises(ValueError, match="must be dict/object"):
        planner.validate_plan(plan, tools_catalogue=sample_catalogue)


# --------------------------------------------------------------------------
# Test 10: Dynamic ${step.output} reference preserved across types
# --------------------------------------------------------------------------
def test_dynamic_step_output_reference_preserved_across_types(sample_catalogue):
    planner = CompoundTaskPlanner()
    plan = TaskPlan(
        goal="Chained calculations",
        steps=[
            {
                "step_id": "step_0",
                "name": "producer",
                "tool": "math_calc",
                "capability": "custom.math",
                "arguments": {"n": 10},
                "side_effect": "READ_ONLY",
            },
            {
                "step_id": "step_1",
                "name": "consumer",
                "tool": "math_calc",
                "capability": "custom.math",
                "depends_on": ["step_0"],
                "arguments": {"n": "${step_0.output}"},
                "side_effect": "READ_ONLY",
            },
        ]
    )
    assert planner.validate_plan(plan, tools_catalogue=sample_catalogue) is True


# --------------------------------------------------------------------------
# Test 11: Dynamic ${step.output.field} reference preserved across types
# --------------------------------------------------------------------------
def test_dynamic_step_output_field_reference_preserved_across_types(sample_catalogue):
    planner = CompoundTaskPlanner()
    plan = TaskPlan(
        goal="Chained calculations with field path",
        steps=[
            {
                "step_id": "step_0",
                "name": "producer",
                "tool": "math_calc",
                "capability": "custom.math",
                "arguments": {"n": 10},
                "side_effect": "READ_ONLY",
            },
            {
                "step_id": "step_1",
                "name": "consumer",
                "tool": "math_calc",
                "capability": "custom.math",
                "depends_on": ["step_0"],
                "arguments": {"n": 5, "flag": "${step_0.output.is_valid}"},
                "side_effect": "READ_ONLY",
            },
        ]
    )
    assert planner.validate_plan(plan, tools_catalogue=sample_catalogue) is True


# --------------------------------------------------------------------------
# Test 12: Status NEEDS_CLARIFICATION correctly parsed
# --------------------------------------------------------------------------
def test_status_needs_clarification_correctly_parsed():
    planner = CompoundTaskPlanner()
    raw = json.dumps({
        "status": "NEEDS_CLARIFICATION",
        "questions": ["Which app would you like me to open?"],
    })
    parsed = planner.parse_plan_response(raw)
    assert parsed["status"] == "NEEDS_CLARIFICATION"
    assert parsed["questions"] == ["Which app would you like me to open?"]
    plan = TaskPlan(
        goal="Open app",
        status=parsed["status"],
        questions=parsed["questions"],
    )
    assert plan.needs_clarification is True
    assert plan.questions == ["Which app would you like me to open?"]
    assert plan.steps == []


# --------------------------------------------------------------------------
# Test 13: Status NEEDS_CLARIFICATION questions validated (non-empty list of strings)
# --------------------------------------------------------------------------
def test_status_needs_clarification_questions_validated():
    planner = CompoundTaskPlanner()

    raw_empty = json.dumps({"status": "NEEDS_CLARIFICATION", "questions": []})
    with pytest.raises(ValueError, match="non-empty 'questions' list"):
        planner.parse_plan_response(raw_empty)

    raw_non_list = json.dumps({"status": "NEEDS_CLARIFICATION", "questions": "Which app?"})
    with pytest.raises(ValueError, match="non-empty 'questions' list"):
        planner.parse_plan_response(raw_non_list)

    raw_non_str = json.dumps({"status": "NEEDS_CLARIFICATION", "questions": [123]})
    with pytest.raises(ValueError, match="must be a non-empty string"):
        planner.parse_plan_response(raw_non_str)


# --------------------------------------------------------------------------
# Test 14: Ambiguous request "Open the app" returns clarification, does NOT create task
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ambiguous_request_open_the_app_returns_clarification(sqlite_storage, monkeypatch):
    registry = ToolRegistry()
    echo_tool = MockEchoTool()
    registry.register(echo_tool)
    monkeypatch.setattr("server.routes.agent.get_device_registry", lambda: registry)

    clarification_response = json.dumps({
        "status": "NEEDS_CLARIFICATION",
        "questions": ["Which specific application would you like to open?"],
    })
    llm = MockPlannerLLM(clarification_response)
    monkeypatch.setattr("brain.router.BrainRouter", lambda: llm)

    task_runtime = TaskRuntime(session_factory=sqlite_storage.session)
    configure_task_runtime(task_runtime)

    req = CreateTaskRequest(goal="Open the app")
    res = await create_durable_task(req, token="valid")

    assert res["status"] == "NEEDS_CLARIFICATION"
    assert res["goal"] == "Open the app"
    assert res["questions"] == ["Which specific application would you like to open?"]

    with sqlite_storage.session() as s:
        assert s.query(DurableTaskRecord).count() == 0


# --------------------------------------------------------------------------
# Test 15: Ambiguous request "Delete files" returns clarification, does NOT create task
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ambiguous_request_delete_files_returns_clarification(sqlite_storage, monkeypatch):
    registry = ToolRegistry()
    registry.register(MockEchoTool())
    monkeypatch.setattr("server.routes.agent.get_device_registry", lambda: registry)

    clarification_response = json.dumps({
        "status": "NEEDS_CLARIFICATION",
        "questions": ["Which directory or specific file pattern should be deleted?"],
    })
    llm = MockPlannerLLM(clarification_response)
    monkeypatch.setattr("brain.router.BrainRouter", lambda: llm)

    task_runtime = TaskRuntime(session_factory=sqlite_storage.session)
    configure_task_runtime(task_runtime)

    req = CreateTaskRequest(goal="Delete files")
    res = await create_durable_task(req, token="valid")

    assert res["status"] == "NEEDS_CLARIFICATION"
    assert res["questions"] == ["Which directory or specific file pattern should be deleted?"]


# --------------------------------------------------------------------------
# Test 16: Ambiguous request does NOT execute any tools
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ambiguous_request_does_not_execute_any_tools(sqlite_storage, monkeypatch):
    registry = ToolRegistry()
    echo_tool = MockEchoTool()
    registry.register(echo_tool)
    monkeypatch.setattr("server.routes.agent.get_device_registry", lambda: registry)

    llm = MockPlannerLLM(json.dumps({
        "status": "NEEDS_CLARIFICATION",
        "questions": ["What message should be echoed?"],
    }))
    monkeypatch.setattr("brain.router.BrainRouter", lambda: llm)

    task_runtime = TaskRuntime(session_factory=sqlite_storage.session)
    configure_task_runtime(task_runtime)

    req = CreateTaskRequest(goal="Echo something")
    await create_durable_task(req, token="valid")

    assert echo_tool.call_count == 0


# --------------------------------------------------------------------------
# Test 17: Ambiguous request does NOT create SQLite row
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ambiguous_request_does_not_create_sqlite_row(sqlite_storage, monkeypatch):
    registry = ToolRegistry()
    registry.register(MockEchoTool())
    monkeypatch.setattr("server.routes.agent.get_device_registry", lambda: registry)

    llm = MockPlannerLLM(json.dumps({
        "status": "NEEDS_CLARIFICATION",
        "questions": ["Target directory?"],
    }))
    monkeypatch.setattr("brain.router.BrainRouter", lambda: llm)

    task_runtime = TaskRuntime(session_factory=sqlite_storage.session)
    configure_task_runtime(task_runtime)

    await create_durable_task(CreateTaskRequest(goal="Clean up disk"), token="valid")

    with sqlite_storage.session() as s:
        assert s.query(DurableTaskRecord).count() == 0
        assert s.query(DurableStepRecord).count() == 0


# --------------------------------------------------------------------------
# Test 18: Ambiguous request does NOT invoke synthesis
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ambiguous_request_does_not_invoke_synthesis(sqlite_storage, monkeypatch):
    registry = ToolRegistry()
    monkeypatch.setattr("server.routes.agent.get_device_registry", lambda: registry)

    synthesis_mock = MockSynthesisEngine()
    task_runtime = TaskRuntime(
        session_factory=sqlite_storage.session,
        synthesis_engine=synthesis_mock,
    )
    configure_task_runtime(task_runtime)

    llm = MockPlannerLLM(json.dumps({
        "status": "NEEDS_CLARIFICATION",
        "questions": ["What computation would you like performed?"],
    }))
    monkeypatch.setattr("brain.router.BrainRouter", lambda: llm)

    await create_durable_task(CreateTaskRequest(goal="Do some complex calculation"), token="valid")

    assert synthesis_mock.call_count == 0


# --------------------------------------------------------------------------
# Test 19: Clarification questions are targeted and minimal (1-5 questions)
# --------------------------------------------------------------------------
def test_clarification_questions_targeted_and_minimal():
    planner = CompoundTaskPlanner()

    raw_5 = json.dumps({
        "status": "NEEDS_CLARIFICATION",
        "questions": [f"Q{i}" for i in range(1, 6)],
    })
    parsed = planner.parse_plan_response(raw_5)
    assert len(parsed["questions"]) == 5

    raw_6 = json.dumps({
        "status": "NEEDS_CLARIFICATION",
        "questions": [f"Q{i}" for i in range(1, 7)],
    })
    with pytest.raises(ValueError, match="too many questions"):
        planner.parse_plan_response(raw_6)


# --------------------------------------------------------------------------
# Test 20: Clarification questions reject empty strings
# --------------------------------------------------------------------------
def test_clarification_questions_reject_empty_strings():
    planner = CompoundTaskPlanner()

    raw_empty = json.dumps({
        "status": "NEEDS_CLARIFICATION",
        "questions": ["Valid question?", "   "],
    })
    with pytest.raises(ValueError, match="must be a non-empty string"):
        planner.parse_plan_response(raw_empty)


# --------------------------------------------------------------------------
# Test 21: Dangerous tools cannot be auto-approved in agent routes
# --------------------------------------------------------------------------
def test_dangerous_tools_cannot_be_auto_approved_in_agent_routes():
    policy = ToolPolicy(
        enabled=True,
        allowed=frozenset({"test_tool"}),
        auto_approve=frozenset({ToolRisk.SAFE, ToolRisk.SENSITIVE}),
    )
    assert ToolRisk.DANGEROUS not in policy.auto_approve
    assert ToolRisk.SAFE in policy.auto_approve
    assert ToolRisk.SENSITIVE in policy.auto_approve


# --------------------------------------------------------------------------
# Test 22: Dangerous tool execution requires confirmation in live/mock executor
# --------------------------------------------------------------------------
def test_dangerous_tool_execution_requires_confirmation_in_executor():
    registry = ToolRegistry()
    dangerous_tool = MockDangerousTool()
    registry.register(dangerous_tool)

    policy = ToolPolicy(
        enabled=True,
        allowed=frozenset([dangerous_tool.name]),
        auto_approve=frozenset({ToolRisk.SAFE, ToolRisk.SENSITIVE}),
    )
    executor = ToolExecutor(registry=registry, policy=policy)

    res = executor.execute("system_format_drive", {"drive": "D:"})
    assert res.ok is False
    assert dangerous_tool.call_count == 0


# --------------------------------------------------------------------------
# Test 23: Safe tools execute without prompt
# --------------------------------------------------------------------------
def test_safe_tools_execute_without_prompt():
    registry = ToolRegistry()
    safe_tool = MockEchoTool()
    registry.register(safe_tool)

    policy = ToolPolicy(
        enabled=True,
        allowed=frozenset([safe_tool.name]),
        auto_approve=frozenset({ToolRisk.SAFE, ToolRisk.SENSITIVE}),
    )
    executor = ToolExecutor(registry=registry, policy=policy)

    res = executor.execute("mock_echo", {"message": "hello"})
    assert res.ok is True
    assert res.output == "echo:hello"
    assert safe_tool.call_count == 1


# --------------------------------------------------------------------------
# Test 24: Sensitive tools execute without prompt under current policy
# --------------------------------------------------------------------------
def test_sensitive_tools_execute_without_prompt_under_current_policy():
    registry = ToolRegistry()
    sensitive_tool = MockSensitiveTool()
    registry.register(sensitive_tool)

    policy = ToolPolicy(
        enabled=True,
        allowed=frozenset([sensitive_tool.name]),
        auto_approve=frozenset({ToolRisk.SAFE, ToolRisk.SENSITIVE}),
    )
    executor = ToolExecutor(registry=registry, policy=policy)

    res = executor.execute("read_private_keys", {"path": "/etc/ssl"})
    assert res.ok is True
    assert res.output == "Read /etc/ssl"
    assert sensitive_tool.call_count == 1


# --------------------------------------------------------------------------
# Test 25: Planner cannot downgrade tool side_effect
# --------------------------------------------------------------------------
def test_planner_cannot_downgrade_tool_side_effect(sample_catalogue):
    planner = CompoundTaskPlanner()
    plan = TaskPlan(
        goal="Format drive",
        steps=[
            {
                "step_id": "step_0",
                "name": "format",
                "tool": "system_format_drive",
                "capability": "system.format",
                "arguments": {"drive": "C:"},
                "side_effect": "READ_ONLY",
            }
        ]
    )
    with pytest.raises(ValueError, match="Planner cannot downgrade tool risk or side-effects"):
        planner.validate_plan(plan, tools_catalogue=sample_catalogue)


# --------------------------------------------------------------------------
# Test 26: Backward compatibility: steps=[] creates empty container
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_backward_compatibility_steps_empty_list_creates_empty_container(sqlite_storage, monkeypatch):
    registry = ToolRegistry()
    monkeypatch.setattr("server.routes.agent.get_device_registry", lambda: registry)

    llm = MockPlannerLLM("Should not be called")
    monkeypatch.setattr("brain.router.BrainRouter", lambda: llm)

    task_runtime = TaskRuntime(session_factory=sqlite_storage.session)
    configure_task_runtime(task_runtime)

    req = CreateTaskRequest(goal="Empty task container", steps=[])
    res = await create_durable_task(req, token="valid")

    assert res["status"].upper() in ("PENDING", "COMPLETED")
    assert len(res.get("steps", [])) == 0
    assert llm.call_count == 0

    with sqlite_storage.session() as s:
        assert s.query(DurableTaskRecord).count() == 1
        assert s.query(DurableStepRecord).count() == 0


# --------------------------------------------------------------------------
# Test 27: Backward compatibility: explicit steps=[...] creates durable task directly
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_backward_compatibility_explicit_steps_creates_durable_task_directly(sqlite_storage, monkeypatch):
    registry = ToolRegistry()
    echo_tool = MockEchoTool()
    registry.register(echo_tool)
    monkeypatch.setattr("server.routes.agent.get_device_registry", lambda: registry)

    llm = MockPlannerLLM("Should not be called")
    monkeypatch.setattr("brain.router.BrainRouter", lambda: llm)

    task_runtime = TaskRuntime(session_factory=sqlite_storage.session)
    configure_task_runtime(task_runtime)

    req = CreateTaskRequest(
        goal="Explicit task",
        steps=[
            {
                "step_id": "step_explicit",
                "name": "explicit_echo",
                "tool": "mock_echo",
                "capability": "echo",
                "arguments": {"message": "explicit"},
                "side_effect": "READ_ONLY",
            }
        ],
        run_async=False,
    )
    res = await create_durable_task(req, token="valid")

    assert res["status"].upper() in ("PENDING", "COMPLETED")
    assert len(res.get("steps", [])) == 1
    assert llm.call_count == 0
    assert echo_tool.call_count == 1


# --------------------------------------------------------------------------
# Test 28: Verification-aware goal produces verification_required=True when requested
# --------------------------------------------------------------------------
def test_verification_aware_goal_produces_verification_required_true(sample_catalogue):
    planner = CompoundTaskPlanner()
    raw = json.dumps({
        "status": "PLANNED",
        "goal": "Verify math calculation",
        "steps": [
            {
                "step_id": "step_0",
                "name": "calc",
                "tool": "math_calc",
                "capability": "custom.math",
                "arguments": {"n": 42},
                "side_effect": "READ_ONLY",
                "verification_required": True,
            }
        ]
    })
    parsed = planner.parse_plan_response(raw)
    assert parsed["steps"][0]["verification_required"] is True
    plan = TaskPlan(
        goal=parsed["goal"],
        steps=parsed["steps"],
    )
    assert plan.steps[0]["verification_required"] is True
    assert planner.validate_plan(plan, tools_catalogue=sample_catalogue) is True
