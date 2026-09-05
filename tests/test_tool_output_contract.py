"""
The structured tool output contract (Phase 3).

What these tests hold in place: every tool execution produces a
machine-readable result whose status, error, evidence and retry safety
can be read without parsing prose, and the model-facing rendering of
that result is deterministic.
"""

import json
import time

import pytest

from core.capabilities import registry as capability_registry
from core.capabilities.models import Capability, CapabilityState

from tools.base import (
    Parameter,
    SideEffect,
    Tool,
    ToolResult,
    ToolRisk,
    fail,
    ok,
    serialize_for_model,
)
from tools.executor import ToolExecutor, ToolPolicy
from tools.outcome import (
    CODE_CAPABILITY_UNAVAILABLE,
    CODE_OUTPUT_SCHEMA,
    Evidence,
    EvidenceKind,
    Retryability,
    ToolError,
    ToolErrorCategory,
    ToolStatus,
    retryability_of,
)
from tools.registry import ToolRegistry
from tools.schema import (
    mcp_export,
    openai_function_schema,
    output_schema,
    to_json_schema,
    tool_definition,
    validate_output,
)


# ----------------------------------------------------------------------
# Doubles
# ----------------------------------------------------------------------

class EchoTool(Tool):

    name = "echo"
    capability = "echo"
    description = "Repeat a string back"
    risk = ToolRisk.SAFE
    side_effect = SideEffect.READ_ONLY

    parameters = (
        Parameter(name="text", description="What to repeat", type="string"),
    )

    def __init__(self):
        self.calls = []

    def execute(self, text: str) -> str:
        self.calls.append(text)
        return text


class BoomTool(Tool):

    name = "boom"
    capability = "echo"
    description = "Always raises"
    risk = ToolRisk.SAFE

    def execute(self, **arguments):
        raise RuntimeError("it exploded")


class SlowTool(Tool):

    name = "slow"
    capability = "echo"
    description = "Sleeps past any deadline"
    risk = ToolRisk.SAFE
    side_effect = SideEffect.NON_IDEMPOTENT

    def execute(self, **arguments):
        import time as _time

        _time.sleep(2)
        return "eventually"


class VerifiedTool(Tool):

    name = "verifying"
    capability = "echo"
    description = "Declares its own postcondition"
    risk = ToolRisk.SAFE

    def __init__(self, verdict):
        self.verdict = verdict

    def execute(self, **arguments):
        return "done"

    def verify(self, **arguments):
        return self.verdict


class StructuredTool(Tool):

    name = "structured"
    capability = "echo"
    description = "Returns schema-checked data"
    risk = ToolRisk.SAFE

    output_schema = {
        "type": "object",
        "required": ["event_id"],
        "properties": {"event_id": {"type": "string"}},
    }

    def __init__(self, payload=None):
        self.payload = payload

    def execute(self, **arguments):
        return ToolResult(ok=True, output="made", data=self.payload)


class TouchTool(Tool):

    name = "touch"
    capability = "touch"
    description = "Changes the world, once per run"
    risk = ToolRisk.DANGEROUS
    side_effect = SideEffect.NON_IDEMPOTENT

    def __init__(self):
        self.ran = 0

    def execute(self, **arguments):
        self.ran += 1
        return "done"


def executor_for(tool: Tool, **policy) -> ToolExecutor:

    policy.setdefault("enabled", True)
    policy.setdefault("allowed", frozenset({tool.name}))
    policy.setdefault(
        "auto_approve", frozenset({ToolRisk.SAFE, ToolRisk.DANGEROUS})
    )

    return ToolExecutor(
        registry=ToolRegistry([tool]),
        policy=ToolPolicy(**policy),
    )


@pytest.fixture(autouse=True)
def _caps():
    from core.capabilities import health as health_registry

    # A failing health check registered by one test must not poison the
    # next: resolve_capability recomputes availability from this global
    # registry on every call.
    health_registry._checks.pop("echo", None)
    health_registry._checks.pop("touch", None)

    for c in ("echo", "touch"):
        # Re-registering overwrites (and logs a warning), so check by
        # lookup rather than a `has` the registry does not expose.
        if capability_registry.get(c) is None:
            capability_registry.register(
                Capability(
                    capability_id=c,
                    name=c,
                    description="phase 3 test",
                    category="test",
                )
            )


# ----------------------------------------------------------------------
# Registry
# ----------------------------------------------------------------------

def test_definitions_describe_every_registered_tool():
    registry = ToolRegistry([EchoTool(), TouchTool()])

    definitions = registry.definitions()

    assert [d["name"] for d in definitions] == ["echo", "touch"]
    for definition in definitions:
        assert definition["description"]
        assert definition["input_schema"]["type"] == "object"
        assert definition["risk_level"] in ("safe", "sensitive", "dangerous")
        assert definition["side_effect"] in (
            "READ_ONLY", "IDEMPOTENT", "NON_IDEMPOTENT", "UNKNOWN",
        )
        assert definition["version"]


def test_definitions_carry_the_declared_output_schema():
    definition = tool_definition(StructuredTool())

    assert definition["output_schema"] == StructuredTool.output_schema
    assert tool_definition(EchoTool())["output_schema"] is None


def test_a_non_dict_output_schema_is_refused_at_declaration():
    class Bad(Tool):
        name = "bad"
        capability = "echo"
        output_schema = "not a schema"

        def execute(self, **arguments):
            return "x"

    with pytest.raises(ValueError):
        output_schema(Bad())


def test_mcp_export_is_standards_shaped():
    payload = mcp_export([EchoTool()])

    assert payload == [{
        "name": "echo",
        "description": "Repeat a string back",
        "inputSchema": to_json_schema(EchoTool()),
    }]
    # No Aura-specific fields smuggled into the standards shape.
    assert set(payload[0]) == {"name", "description", "inputSchema"}


def test_mcp_export_matches_the_openai_payload_names():
    tools = [EchoTool(), TouchTool()]
    mcp_names = [t["name"] for t in mcp_export(tools)]
    openai_names = [
        t["function"]["name"]
        for t in (openai_function_schema(t) for t in tools)
    ]

    assert mcp_names == openai_names


def test_a_bad_parameter_type_is_refused_not_stringified():
    class BadType(Tool):
        name = "badtype"
        capability = "echo"
        parameters = (Parameter(name="x", type="Coordinate"),)

        def execute(self, **arguments):
            return "x"

    with pytest.raises(ValueError):
        to_json_schema(BadType())


# ----------------------------------------------------------------------
# Input validation - malformed calls never reach the tool
# ----------------------------------------------------------------------

def test_valid_arguments_run():
    tool = EchoTool()

    result = executor_for(tool).execute("echo", {"text": "hi"})

    assert result.ok
    assert tool.calls == ["hi"]


def test_an_unknown_argument_is_refused_before_execution():
    tool = EchoTool()

    result = executor_for(tool).execute("echo", {"nope": "hi"})

    assert not result.ok
    assert result.status == ToolStatus.INVALID_ARGUMENTS.value
    assert result.error_code == "INVALID_ARGUMENTS"
    assert tool.calls == []


def test_a_mistyped_argument_is_refused_before_execution():
    tool = EchoTool()

    result = executor_for(tool).execute("echo", {"text": 123})

    assert result.status == ToolStatus.INVALID_ARGUMENTS.value
    assert "string" in result.error
    assert tool.calls == []


def test_a_missing_required_argument_is_invalid_not_failed():
    tool = EchoTool()

    result = executor_for(tool).execute("echo", {})

    assert result.status == ToolStatus.INVALID_ARGUMENTS.value
    assert "missing arguments" in result.error


def test_non_plain_data_is_refused():
    tool = EchoTool()

    result = executor_for(tool).execute("echo", {"text": object()})

    assert result.status == ToolStatus.INVALID_ARGUMENTS.value


# ----------------------------------------------------------------------
# Execution statuses
# ----------------------------------------------------------------------

def test_success_carries_the_full_contract():
    result = executor_for(EchoTool()).execute("echo", {"text": "hi"})

    assert result.ok
    assert result.status == ToolStatus.SUCCESS.value
    assert result.execution_id.startswith("exec_")
    assert result.started_at
    assert result.completed_at
    assert result.evidence_summary == "UNVERIFIED"
    assert result.side_effect == "READ_ONLY"
    # The structured payload a machine reads, independent of prose.
    assert result.data["status"] == "SUCCESS"
    assert result.data["retryable"] == "SAFE"
    assert result.data["execution_id"] == result.execution_id


def test_an_exception_is_failed_with_an_execution_error():
    result = executor_for(BoomTool()).execute("boom")

    assert not result.ok
    assert result.status == ToolStatus.FAILED.value
    assert result.error_code == "TOOL_ERROR"
    assert result.error_category is ToolErrorCategory.EXECUTION
    assert result.evidence_summary == "NONE"


def test_a_timeout_is_timeout_not_failure():
    executor = executor_for(SlowTool(), timeout=0.05)

    result = executor.execute("slow")

    assert not result.ok
    assert result.status == ToolStatus.TIMEOUT.value
    assert result.error_code == "TIMEOUT"
    assert result.error_category is ToolErrorCategory.TIMEOUT


def _mark_unavailable(capability_id: str):
    """
    Simulate the device being gone: a health check whose reason carries
    the heartbeat word, which is exactly how a disconnected companion
    reports UNAVAILABLE through resolve_capability.
    """
    from core.capabilities import health as health_registry

    health_registry.register_check(
        capability_id,
        lambda: {"healthy": False, "reason": "no Android companion poll heartbeat has been received"},
    )


def test_an_unavailable_capability_is_unavailable_and_retryable():
    tool = EchoTool()
    executor = executor_for(tool)

    _mark_unavailable("echo")
    result = executor.execute("echo", {"text": "hi"})

    assert not result.ok
    assert result.status == ToolStatus.UNAVAILABLE.value
    assert result.error_code == CODE_CAPABILITY_UNAVAILABLE
    assert result.error_category is ToolErrorCategory.CAPABILITY
    assert result.retryability is Retryability.SAFE
    assert result.execution == "not_attempted"
    assert tool.calls == []


def test_a_policy_refusal_is_denied():
    executor = ToolExecutor(
        registry=ToolRegistry([EchoTool()]),
        policy=ToolPolicy(enabled=True, allowed=frozenset()),
    )

    result = executor.execute("echo", {"text": "hi"})

    assert result.status == ToolStatus.DENIED.value
    assert result.error_code == "NOT_ALLOWED"


def test_a_missing_confirmation_is_denied_not_failed():
    executor = ToolExecutor(
        registry=ToolRegistry([TouchTool()]),
        policy=ToolPolicy(
            enabled=True,
            allowed=frozenset({"touch"}),
            auto_approve=frozenset({ToolRisk.SAFE}),
        ),
    )

    result = executor.execute("touch")

    assert result.status == ToolStatus.DENIED.value
    assert result.error_code == "CONFIRMATION_REQUIRED"
    assert result.retryability is Retryability.UNSAFE


def test_an_unknown_tool_request_is_invalid_arguments():
    result = executor_for(EchoTool()).execute("no_such_tool")

    assert result.status == ToolStatus.INVALID_ARGUMENTS.value
    assert result.error_code == "TOOL_NOT_FOUND"


# ----------------------------------------------------------------------
# Output validation
# ----------------------------------------------------------------------

def test_output_matching_the_schema_stays_success():
    result = executor_for(
        StructuredTool(payload={"event_id": "abc_123"})
    ).execute("structured")

    assert result.ok
    assert result.status == ToolStatus.SUCCESS.value


def test_malformed_output_is_unknown_not_success():
    result = executor_for(
        StructuredTool(payload={"event_id": 42})
    ).execute("structured")

    assert not result.ok
    assert result.status == ToolStatus.UNKNOWN.value
    assert result.error_code == CODE_OUTPUT_SCHEMA
    # The call ran - execution says so - but the result is untrusted.
    assert result.retryability is Retryability.UNSAFE


def test_output_missing_a_required_key_is_unknown():
    result = executor_for(StructuredTool(payload={})).execute("structured")

    assert result.status == ToolStatus.UNKNOWN.value
    assert "event_id" in result.error


def test_validate_output_holds_the_shape_rules():
    schema = {
        "type": "object",
        "required": ["id"],
        "properties": {"id": {"type": "string"}},
    }

    class Schemed(Tool):
        name = "schemed"
        capability = "echo"
        output_schema = schema

        def execute(self, **arguments):
            return ""

    assert validate_output(Schemed(), {"id": "x"}) == ""
    assert validate_output(Schemed(), {"id": 1})
    assert validate_output(Schemed(), None)
    assert validate_output(EchoTool(), {"anything": "goes"}) == ""


# ----------------------------------------------------------------------
# Retry semantics - derived, never asserted
# ----------------------------------------------------------------------

def test_retry_rules_across_status_and_side_effect():
    cases = [
        (ToolStatus.UNAVAILABLE, SideEffect.NON_IDEMPOTENT, Retryability.SAFE),
        (ToolStatus.DENIED, SideEffect.READ_ONLY, Retryability.UNSAFE),
        (ToolStatus.INVALID_ARGUMENTS, SideEffect.READ_ONLY, Retryability.UNSAFE),
        (ToolStatus.TIMEOUT, SideEffect.NON_IDEMPOTENT, Retryability.UNSAFE),
        (ToolStatus.UNKNOWN, SideEffect.NON_IDEMPOTENT, Retryability.UNSAFE),
        (ToolStatus.SUCCESS, SideEffect.NON_IDEMPOTENT, Retryability.UNSAFE),
        (ToolStatus.FAILED, SideEffect.NON_IDEMPOTENT, Retryability.UNKNOWN),
        (ToolStatus.FAILED, SideEffect.READ_ONLY, Retryability.SAFE),
        (ToolStatus.FAILED, SideEffect.UNKNOWN, Retryability.UNKNOWN),
    ]

    for status, effect, expected in cases:
        assert retryability_of(status, effect) is expected, (status, effect)


def test_a_non_idempotent_success_is_never_retry_safe():
    result = executor_for(TouchTool()).execute("touch")

    assert result.ok
    assert result.side_effect == "NON_IDEMPOTENT"
    assert result.retryability is Retryability.UNSAFE


def test_only_an_affirmative_safe_authorises_a_retry():
    assert Retryability.UNKNOWN.may_retry is False
    assert Retryability.UNSAFE.may_retry is False
    assert Retryability.SAFE.may_retry is True


# ----------------------------------------------------------------------
# Evidence
# ----------------------------------------------------------------------

def test_a_verified_postcondition_reads_verified():
    result = executor_for(
        VerifiedTool(ok("confirmed", tool="verifying"))
    ).execute("verifying")

    assert result.ok
    assert result.evidence_summary == "VERIFIED"
    assert result.evidence[0].kind is EvidenceKind.POSTCONDITION


def test_a_contradicted_postcondition_downgrades_to_failed():
    result = executor_for(
        VerifiedTool(fail("the window never opened", tool="verifying"))
    ).execute("verifying")

    assert not result.ok
    assert result.evidence_summary == "CONTRADICTED"
    assert result.evidence[0].verified is False


def test_a_bare_success_is_unverified_return_value():
    result = executor_for(EchoTool()).execute("echo", {"text": "hi"})

    assert result.ok
    assert result.evidence_summary == "UNVERIFIED"
    assert result.evidence[0].kind is EvidenceKind.RETURN_VALUE
    assert result.evidence[0].verified is None


def test_a_failure_has_no_evidence():
    result = executor_for(BoomTool()).execute("boom")

    assert result.evidence_summary == "NONE"


def test_strongest_evidence_prefers_a_direct_check():
    from tools.outcome import strongest

    receipt = Evidence(EvidenceKind.RECEIPT, source="device", verified=True)
    postcondition = Evidence(
        EvidenceKind.POSTCONDITION, source="verify", verified=True
    )

    assert strongest((receipt, postcondition)) is postcondition
    assert strongest((Evidence(EvidenceKind.RETURN_VALUE),)) is None


# ----------------------------------------------------------------------
# The serializer (ToolResult -> model)
# ----------------------------------------------------------------------

def test_the_serializer_is_deterministic():
    first = serialize_for_model(ok("12:00", tool="current_time"))
    second = serialize_for_model(ok("12:00", tool="current_time"))

    assert first == second
    assert first.splitlines()[0] == "STATUS: SUCCESS"


def test_the_serializer_names_status_tool_evidence_and_retry():
    rendered = serialize_for_model(
        executor_for(EchoTool()).execute("echo", {"text": "hi"})
    )

    lines = {line.split(":", 1)[0]: line for line in rendered.splitlines()}
    assert lines["STATUS"] == "STATUS: SUCCESS"
    assert lines["TOOL"] == "TOOL: echo"
    assert lines["EVIDENCE"] == "EVIDENCE: UNVERIFIED"
    assert lines["RETRY"] == "RETRY: SAFE"
    assert "ran successfully" in lines["OUTCOME"]


def test_a_failure_render_names_the_code_and_category():
    rendered = serialize_for_model(
        executor_for(BoomTool()).execute("boom")
    )

    assert "STATUS: FAILED" in rendered
    assert "TOOL_ERROR/EXECUTION" in rendered
    assert "This did not happen." in rendered


def test_unknown_never_renders_as_success():
    result = ToolResult(
        ok=False,
        error="unreadable",
        tool="send_sms",
        status=ToolStatus.UNKNOWN.value,
        side_effect="NON_IDEMPOTENT",
    )

    rendered = serialize_for_model(result)

    assert rendered.splitlines()[0] == "STATUS: UNKNOWN"
    assert bool(result) is False
    assert "cannot establish whether" in rendered
    assert "ran successfully" not in rendered


def test_the_serializer_does_not_leak_internal_fields():
    result = executor_for(EchoTool()).execute("echo", {"text": "hi"})

    rendered = serialize_for_model(result)

    # Execution identity belongs to diagnostics, not to the prompt.
    assert result.execution_id not in rendered
    assert result.started_at not in rendered


# ----------------------------------------------------------------------
# Status taxonomy invariants
# ----------------------------------------------------------------------

def test_unknown_is_not_ok_by_construction():
    for status in ToolStatus:
        result = ToolResult(ok=True, status=status.value)

        assert result.ok is status.ok

        if status is ToolStatus.UNKNOWN:
            assert result.ok is False


def test_partial_is_not_a_success():
    result = ToolResult(ok=False, status=ToolStatus.PARTIAL.value)

    assert result.ok is False
    assert result.status_enum.attempted
    assert result.status_enum.established


def test_gate_statuses_never_report_attempted():
    for status in (
        ToolStatus.DENIED,
        ToolStatus.UNAVAILABLE,
        ToolStatus.INVALID_ARGUMENTS,
    ):
        assert not status.attempted
        assert status.established


def test_toolerror_derives_its_category_from_its_code():
    error = ToolError(code=CODE_CAPABILITY_UNAVAILABLE, message="no heartbeat")

    assert error.category is ToolErrorCategory.CAPABILITY
    as_dict = error.as_dict()
    assert as_dict["code"] == "CAPABILITY_UNAVAILABLE"
    assert as_dict["category"] == "CAPABILITY"


# ----------------------------------------------------------------------
# Integration: model -> tool call -> execution -> structured result -> model
# ----------------------------------------------------------------------

def test_a_model_request_becomes_a_structured_model_facing_result():
    from brain.tool_calling import ToolCall, read_tool_call

    request = read_tool_call('{"tool": "echo", "arguments": {"text": "hi"}}')
    assert isinstance(request, ToolCall)

    result = executor_for(EchoTool()).execute(request.name, request.arguments)
    rendered = serialize_for_model(result)

    assert rendered.splitlines()[0] == "STATUS: SUCCESS"
    assert "It returned: hi" in rendered


def test_a_capability_failure_becomes_a_structured_model_facing_result():
    tool = EchoTool()
    executor = executor_for(tool)

    _mark_unavailable("echo")
    result = executor.execute("echo", {"text": "hi"})

    rendered = serialize_for_model(result)

    assert "STATUS: UNAVAILABLE" in rendered
    assert "CAPABILITY_UNAVAILABLE" in rendered
    # Retry is safe, and the model is told why nothing ran.
    assert "RETRY: SAFE" in rendered
    assert "This did not happen." in rendered


# ----------------------------------------------------------------------
# Regression: the original FC/capability failure class
# ----------------------------------------------------------------------

def test_capability_unavailable_is_not_a_generic_provider_failure():
    """
    The original defect: "No cloud provider is configured or supports
    function calling" collapsed into a generic task-aborted failure and
    invited failover retries against providers that could never serve
    the request. Phase 1 made the error type distinct; Phase 3 pins the
    distinction in the outcome vocabulary.
    """

    from brain.providers.errors import (
        CapabilityUnavailableError,
        ProviderUnavailableError,
    )

    error = CapabilityUnavailableError(
        "No configured provider supports function calling"
    )

    # Not a transient failure: failover must not treat it as one.
    assert not isinstance(error, ProviderUnavailableError)

    # In the tool vocabulary it is a CAPABILITY error, not PROVIDER.
    mapped = ToolError(code=CODE_CAPABILITY_UNAVAILABLE, message=str(error))

    assert mapped.category is ToolErrorCategory.CAPABILITY
    assert mapped.category is not ToolErrorCategory.PROVIDER

    payload = mapped.as_dict()

    # Machine-readable end to end: the runtime branches on `category`,
    # never on the sentence.
    assert payload["category"] == "CAPABILITY"
    assert payload["message"] == str(error)


def test_capability_unavailable_does_not_mark_side_effects_retryable():
    """
    The reasoning layer gets enough to recover with: the status says
    UNAVAILABLE (nothing ran, so a retry cannot duplicate a side
    effect), the category says CAPABILITY (retrying the same provider
    changes nothing until a capable one is configured) - the two facts
    together, not a generic "task aborted".
    """

    result = ToolResult(
        ok=False,
        error="No cloud provider is configured or supports function calling",
        tool="send_sms",
        status=ToolStatus.UNAVAILABLE.value,
        error_code=CODE_CAPABILITY_UNAVAILABLE,
        side_effect="NON_IDEMPOTENT",
    )

    assert result.status_enum.attempted is False
    assert result.retryability is Retryability.SAFE
    assert result.error_category is ToolErrorCategory.CAPABILITY

    rendered = serialize_for_model(result)

    assert "STATUS: UNAVAILABLE" in rendered
    assert "CAPABILITY_UNAVAILABLE/CAPABILITY" in rendered


# ----------------------------------------------------------------------
# Diagnostics integration
# ----------------------------------------------------------------------

def test_every_execution_writes_a_tool_trace_line(tmp_path, monkeypatch):
    """
    The Phase 1 diagnostics trace gains one line per tool execution,
    carrying identity, status, duration and verification state - and
    never the arguments or the content they named.
    """

    import logging

    from core import trace

    trace_file = tmp_path / "diagnostics.jsonl"
    recorder = logging.getLogger("aura.phase3.trace-test")
    recorder.setLevel(logging.INFO)
    recorder.propagate = False
    handler = logging.FileHandler(trace_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    recorder.addHandler(handler)

    monkeypatch.setattr(trace, "_diagnostics_logger", lambda: recorder)

    executor_for(EchoTool()).execute("echo", {"text": "hi"})
    executor_for(BoomTool()).execute("boom")

    lines = [
        json.loads(line)
        for line in trace_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    tool_lines = [l for l in lines if l.get("kind") == "tool_execution"]

    assert len(tool_lines) == 2

    success = tool_lines[0]
    assert success["tool"] == "echo"
    assert success["status"] == "SUCCESS"
    assert success["execution_id"].startswith("exec_")
    assert "retryable" in success
    assert "evidence" in success

    failure = tool_lines[1]
    assert failure["status"] == "FAILED"
    assert failure["error_code"] == "TOOL_ERROR"

    # Arguments and content never enter the trace.
    assert "hi" not in json.dumps(tool_lines)


def test_trace_failure_does_not_break_execution(tmp_path, monkeypatch):
    from core import trace

    def broken_emit(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(trace, "emit_trace", broken_emit)

    result = executor_for(EchoTool()).execute("echo", {"text": "hi"})

    assert result.ok


# ----------------------------------------------------------------------
# Performance - the contract must be cheap
# ----------------------------------------------------------------------

def test_the_contract_overhead_is_microseconds():
    """
    Measured, not asserted to a tight bound: 200 full executor rounds
    including stamping, validation and serialization must stay well
    under a millisecond each. The printout is the evidence.
    """

    tool = EchoTool()
    executor = executor_for(tool)

    rounds = 200
    start = time.perf_counter()
    for _ in range(rounds):
        result = executor.execute("echo", {"text": "hi"})
        serialize_for_model(result)
    elapsed = time.perf_counter() - start

    per_call_ms = elapsed / rounds * 1000

    print(f"\nper-execution contract overhead: {per_call_ms:.3f} ms")

    # A generous ceiling: schema validation plus result stamping plus
    # serialization is microsecond work; this fails only if it is not.
    assert per_call_ms < 5
    assert tool.calls[-1] == "hi"






