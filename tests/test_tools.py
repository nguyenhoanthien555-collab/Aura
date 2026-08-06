"""
Tool tests.

The brief's constraint for this subsystem was one line: "Do not allow
direct uncontrolled execution." Most of what follows tests refusal
rather than execution, because refusal is the behaviour that matters.

Five gates, each tested on its own:

    1. tools enabled at all
    2. the tool is registered
    3. the tool name is on the allow list
    4. its risk is auto approved, or a human approved this call
    5. its arguments are plain data
"""

from datetime import datetime

import pytest

from events.bus import EventBus
from events.types import ToolCompletedEvent, ToolInvokedEvent

from tools.base import Parameter, Tool, ToolResult, ToolRisk, fail, ok
from tools.builtins.clock import CurrentTimeTool
from tools.builtins.filesystem import ListDirectoryTool, ReadFileTool
from tools.executor import ToolExecutor, ToolPolicy
from tools.factory import build_registry, build_tools
from tools.registry import ToolRegistry


# ----------------------------------------------------------------------
# Test tools
# ----------------------------------------------------------------------

class EchoTool(Tool):

    name = "echo"
    description = "Repeat a string back"
    risk = ToolRisk.SAFE

    parameters = (Parameter(name="text", description="What to repeat"),)

    def __init__(self):
        self.calls = []

    def execute(self, text: str) -> str:
        self.calls.append(text)
        return text


class TouchTool(Tool):
    """Stands in for anything that changes the machine."""

    name = "touch"
    description = "Pretend to change something"
    risk = ToolRisk.DANGEROUS

    def __init__(self):
        self.ran = 0

    def execute(self, **arguments) -> str:
        self.ran += 1
        return "done"


class ReadTool(Tool):

    name = "peek"
    description = "Pretend to read user data"
    risk = ToolRisk.SENSITIVE

    def __init__(self):
        self.ran = 0

    def execute(self, **arguments) -> str:
        self.ran += 1
        return "contents"


def executor_for(tool: Tool, **policy) -> ToolExecutor:
    """An executor with one tool and an explicitly stated policy."""

    policy.setdefault("enabled", True)
    policy.setdefault("allowed", frozenset({tool.name}))

    return ToolExecutor(
        registry=ToolRegistry([tool]),
        policy=ToolPolicy(**policy),
    )


# ----------------------------------------------------------------------
# Base types
# ----------------------------------------------------------------------

def test_tool_result_is_truthy_only_when_it_succeeded():
    assert bool(ok("output")) is True
    assert bool(fail("nope")) is False


def test_tool_result_renders_errors_visibly():
    assert fail("permission denied").render() == "error: permission denied"


def test_an_unlabelled_tool_is_dangerous_by_default():
    """A tool author cannot opt out of permissions by forgetting to."""

    class Unlabelled(Tool):
        name = "unlabelled"

        def execute(self, **arguments):
            return "ran"

    assert Unlabelled.risk is ToolRisk.DANGEROUS


def test_describe_lists_parameters():
    described = EchoTool().describe()

    assert "echo" in described
    assert "text" in described


def test_required_parameters_excludes_optional_ones():
    assert CurrentTimeTool().required_parameters() == []
    assert EchoTool().required_parameters() == ["text"]


# ----------------------------------------------------------------------
# Registry
# ----------------------------------------------------------------------

def test_registry_stores_and_finds_tools():
    tool = EchoTool()
    registry = ToolRegistry([tool])

    assert registry.get("echo") is tool
    assert registry.has("echo")
    assert "echo" in registry
    assert len(registry) == 1


def test_registry_rejects_an_unnamed_tool():
    class Nameless(Tool):
        name = ""

        def execute(self, **arguments):
            return ""

    with pytest.raises(ValueError):
        ToolRegistry().register(Nameless())


def test_registry_rejects_a_duplicate_name():
    """A tool that quietly replaces another is how read_file changes meaning."""

    registry = ToolRegistry([EchoTool()])

    with pytest.raises(ValueError):
        registry.register(EchoTool())


def test_unknown_tool_lookup_returns_none():
    assert ToolRegistry().get("nope") is None


def test_registry_can_filter_by_risk():
    registry = ToolRegistry([EchoTool(), TouchTool()])

    assert [t.name for t in registry.by_risk(ToolRisk.SAFE)] == ["echo"]
    assert [t.name for t in registry.by_risk(ToolRisk.DANGEROUS)] == ["touch"]


# ----------------------------------------------------------------------
# Gate 1: tools disabled
# ----------------------------------------------------------------------

def test_nothing_runs_when_tools_are_disabled():
    tool = EchoTool()

    executor = executor_for(tool, enabled=False)

    result = executor.execute("echo", {"text": "hi"})

    assert result.ok is False
    assert "disabled" in result.error
    assert tool.calls == []


def test_disabled_tools_are_not_listed():
    assert executor_for(EchoTool(), enabled=False).available() == []


def test_a_fresh_policy_grants_nothing():
    """Closed by default: no config at all means no tool use."""

    policy = ToolPolicy()

    assert policy.enabled is False
    assert policy.allowed == frozenset()


# ----------------------------------------------------------------------
# Gate 2: unknown tool
# ----------------------------------------------------------------------

def test_unknown_tool_is_refused():
    executor = executor_for(EchoTool())

    result = executor.execute("rm_rf", {})

    assert result.ok is False
    assert "unknown tool" in result.error


# ----------------------------------------------------------------------
# Gate 3: allow list
# ----------------------------------------------------------------------

def test_a_registered_tool_that_is_not_allowed_cannot_run():
    tool = EchoTool()

    executor = executor_for(tool, allowed=frozenset())

    result = executor.execute("echo", {"text": "hi"})

    assert result.ok is False
    assert "not allowed" in result.error
    assert tool.calls == []


def test_available_lists_only_allowed_tools():
    executor = ToolExecutor(
        registry=ToolRegistry([EchoTool(), TouchTool()]),
        policy=ToolPolicy(enabled=True, allowed=frozenset({"echo"})),
    )

    assert executor.available() == ["echo"]


def test_check_explains_why_without_running_anything():
    tool = EchoTool()

    executor = executor_for(tool, allowed=frozenset())

    assert "not allowed" in executor.check("echo")
    assert tool.calls == []


def test_check_returns_empty_when_a_tool_may_run():
    assert executor_for(EchoTool()).check("echo") == ""


# ----------------------------------------------------------------------
# Gate 4: approval
# ----------------------------------------------------------------------

def test_a_safe_tool_runs_without_asking():
    tool = EchoTool()

    result = executor_for(tool).execute("echo", {"text": "hi"})

    assert result.ok is True
    assert result.output == "hi"
    assert tool.calls == ["hi"]


def test_a_dangerous_tool_cannot_run_with_no_one_to_ask():
    """
    The important one. With no confirmation handler attached, a model
    that asks Aura to change the machine gets a denial - not because the
    call failed, but because nothing exists that could say yes.
    """

    tool = TouchTool()

    result = executor_for(tool).execute("touch", {})

    assert result.ok is False
    assert "permission denied" in result.error
    assert tool.ran == 0


def test_a_sensitive_tool_also_needs_approval():
    tool = ReadTool()

    result = executor_for(tool).execute("peek", {})

    assert result.ok is False
    assert tool.ran == 0


def test_a_refused_confirmation_blocks_the_call():
    tool = TouchTool()

    executor = executor_for(tool)
    executor.confirm = lambda tool, arguments: False

    result = executor.execute("touch", {})

    assert result.ok is False
    assert tool.ran == 0


def test_an_accepted_confirmation_allows_the_call():
    tool = TouchTool()

    executor = executor_for(tool)
    executor.confirm = lambda tool, arguments: True

    assert executor.execute("touch", {}).ok is True
    assert tool.ran == 1


def test_confirmation_sees_the_tool_and_its_arguments():
    seen = {}

    def confirm(tool, arguments):
        seen["tool"] = tool.name
        seen["risk"] = tool.risk
        seen["arguments"] = dict(arguments)
        return True

    executor = executor_for(TouchTool())
    executor.confirm = confirm

    executor.execute("touch", {"target": "notes.txt"})

    assert seen["tool"] == "touch"
    assert seen["risk"] is ToolRisk.DANGEROUS
    assert seen["arguments"] == {"target": "notes.txt"}


def test_a_crashing_confirmation_handler_means_no():
    tool = TouchTool()

    executor = executor_for(tool)
    executor.confirm = lambda tool, arguments: 1 / 0

    assert executor.execute("touch", {}).ok is False
    assert tool.ran == 0


def test_widening_auto_approve_is_an_explicit_choice():
    tool = TouchTool()

    executor = executor_for(
        tool,
        auto_approve=frozenset({ToolRisk.SAFE, ToolRisk.DANGEROUS}),
    )

    assert executor.execute("touch", {}).ok is True


# ----------------------------------------------------------------------
# Gate 5: arguments
# ----------------------------------------------------------------------

def test_a_missing_required_argument_is_refused():
    tool = EchoTool()

    result = executor_for(tool).execute("echo", {})

    assert result.ok is False
    assert "missing arguments" in result.error
    assert tool.calls == []


@pytest.mark.parametrize(
    "value",
    [
        lambda: "smuggled behaviour",
        object(),
        {1, 2, 3},
    ],
)
def test_arguments_that_are_not_plain_data_are_refused(value):
    """
    A live object in an argument would let a caller smuggle behaviour
    through what is supposed to be inert data.
    """

    tool = EchoTool()

    result = executor_for(tool).execute("echo", {"text": value})

    assert result.ok is False
    assert "not plain data" in result.error
    assert tool.calls == []


def test_nested_plain_data_is_accepted():
    class Structured(Tool):
        name = "structured"
        risk = ToolRisk.SAFE

        def execute(self, **arguments):
            return "ok"

    executor = executor_for(Structured())

    result = executor.execute(
        "structured",
        {"items": [1, 2, {"nested": True}]},
    )

    assert result.ok is True


def test_a_non_string_argument_name_is_refused():
    tool = EchoTool()

    result = executor_for(tool).execute("echo", {1: "hi"})

    assert result.ok is False
    assert tool.calls == []


# ----------------------------------------------------------------------
# Execution behaviour
# ----------------------------------------------------------------------

def test_a_raising_tool_becomes_a_failed_result_not_an_exception():
    class Exploding(Tool):
        name = "explode"
        risk = ToolRisk.SAFE

        def execute(self, **arguments):
            raise RuntimeError("kaboom")

    result = executor_for(Exploding()).execute("explode", {})

    assert result.ok is False
    assert "kaboom" in result.error


def test_a_plain_string_return_is_normalised():
    result = executor_for(EchoTool()).execute("echo", {"text": "hi"})

    assert isinstance(result, ToolResult)
    assert result.tool == "echo"


def test_long_output_is_truncated():
    class Verbose(Tool):
        name = "verbose"
        risk = ToolRisk.SAFE

        def execute(self, **arguments):
            return "x" * 10_000

    result = executor_for(Verbose()).execute("verbose", {})

    assert len(result.output) < 10_000


def test_history_records_every_attempt_including_refusals():
    executor = ToolExecutor(
        registry=ToolRegistry([EchoTool(), TouchTool()]),
        policy=ToolPolicy(enabled=True, allowed=frozenset({"echo", "touch"})),
    )

    executor.execute("echo", {"text": "hi"})
    executor.execute("touch", {})

    assert executor.history == [("echo", True), ("touch", False)]


# ----------------------------------------------------------------------
# Events
# ----------------------------------------------------------------------

def test_both_the_request_and_the_outcome_are_published():
    bus = EventBus()
    invoked, completed = [], []

    bus.subscribe(ToolInvokedEvent, invoked.append)
    bus.subscribe(ToolCompletedEvent, completed.append)

    executor = executor_for(EchoTool())
    executor.events = bus

    executor.execute("echo", {"text": "hi"})

    assert [event.name for event in invoked] == ["echo"]
    assert [(event.name, event.ok) for event in completed] == [("echo", True)]


def test_a_denial_is_as_visible_on_the_bus_as_a_success():
    bus = EventBus()
    completed = []
    bus.subscribe(ToolCompletedEvent, completed.append)

    executor = executor_for(TouchTool())
    executor.events = bus

    executor.execute("touch", {})

    assert completed[0].ok is False
    assert "permission denied" in completed[0].detail


def test_unsafe_arguments_are_not_put_inside_an_event():
    bus = EventBus()
    invoked = []
    bus.subscribe(ToolInvokedEvent, invoked.append)

    executor = executor_for(EchoTool())
    executor.events = bus

    executor.execute("echo", {"text": lambda: "live object"})

    assert invoked[0].arguments == {"text": "<omitted>"}


# ----------------------------------------------------------------------
# Filesystem containment
# ----------------------------------------------------------------------

@pytest.fixture
def sandbox(tmp_path):
    """A directory tree with one secret deliberately outside it."""

    root = tmp_path / "allowed"
    root.mkdir()
    (root / "notes.txt").write_text("inside the sandbox", encoding="utf-8")
    (root / "sub").mkdir()

    (tmp_path / "secret.txt").write_text("outside the sandbox", encoding="utf-8")

    return root


def test_reading_inside_the_sandbox_works(sandbox):
    tool = ReadFileTool([str(sandbox)])

    assert tool.execute(str(sandbox / "notes.txt")) == "inside the sandbox"


def test_reading_outside_the_sandbox_is_refused(sandbox):
    tool = ReadFileTool([str(sandbox)])

    with pytest.raises(PermissionError):
        tool.execute(str(sandbox.parent / "secret.txt"))


def test_dot_dot_traversal_is_refused(sandbox):
    """
    Resolve first, then check containment. Checking the string before
    resolving would let this through.
    """

    tool = ReadFileTool([str(sandbox)])

    with pytest.raises(PermissionError):
        tool.execute(str(sandbox / ".." / "secret.txt"))


def test_no_configured_roots_means_nothing_is_readable(sandbox):
    tool = ReadFileTool([])

    with pytest.raises(PermissionError):
        tool.execute(str(sandbox / "notes.txt"))


def test_listing_marks_directories(sandbox):
    listing = ListDirectoryTool([str(sandbox)]).execute(str(sandbox))

    assert "notes.txt" in listing
    assert "sub/" in listing


def test_a_refused_path_reaches_the_caller_as_a_denial_not_a_crash(sandbox):
    """The executor turns the PermissionError into a failed result."""

    tool = ReadFileTool([str(sandbox)])

    executor = ToolExecutor(
        registry=ToolRegistry([tool]),
        policy=ToolPolicy(
            enabled=True,
            allowed=frozenset({"read_file"}),
            auto_approve=frozenset({ToolRisk.SAFE, ToolRisk.SENSITIVE}),
        ),
    )

    result = executor.execute(
        "read_file",
        {"path": str(sandbox.parent / "secret.txt")},
    )

    assert result.ok is False
    assert "outside" in result.error


# ----------------------------------------------------------------------
# Clock
# ----------------------------------------------------------------------

def test_clock_tool_uses_its_injected_clock():
    fixed = datetime(2026, 1, 2, 3, 4, 5)

    tool = CurrentTimeTool(clock=lambda: fixed)

    assert tool.execute(format="%Y-%m-%d %H:%M") == "2026-01-02 03:04"


def test_clock_tool_is_safe_and_therefore_needs_no_approval():
    fixed = datetime(2026, 1, 2, 3, 4, 5)

    executor = executor_for(CurrentTimeTool(clock=lambda: fixed))

    assert executor.execute("current_time", {}).ok is True


# ----------------------------------------------------------------------
# Policy from config, and the factory
# ----------------------------------------------------------------------

def test_policy_reads_config():
    policy = ToolPolicy.from_config(
        {
            "enabled": True,
            "allowed": ["current_time", "read_file"],
            "auto_approve": ["safe", "sensitive"],
        }
    )

    assert policy.enabled is True
    assert policy.allowed == frozenset({"current_time", "read_file"})
    assert ToolRisk.SENSITIVE in policy.auto_approve


def test_policy_ignores_an_unknown_risk_level():
    policy = ToolPolicy.from_config({"auto_approve": ["safe", "nonsense"]})

    assert policy.auto_approve == frozenset({ToolRisk.SAFE})


def test_empty_config_is_a_closed_policy():
    policy = ToolPolicy.from_config({})

    assert policy.enabled is False
    assert policy.allowed == frozenset()


def test_registry_always_has_the_clock():
    assert "current_time" in build_registry({}).names()


def test_filesystem_tools_appear_only_with_allowed_paths(tmp_path):
    without = build_registry({})
    with_paths = build_registry({"allowed_paths": [str(tmp_path)]})

    assert "read_file" not in without.names()
    assert "read_file" in with_paths.names()


def test_default_built_executor_permits_nothing():
    """A fresh install grants no tool at all."""

    assert build_tools({}).available() == []


def test_a_registered_but_unpermitted_tool_is_visible_and_inert():
    """
    "You have not allowed this" is a better answer than "unknown tool" -
    the tool exists, the permission does not.
    """

    executor = build_tools({"enabled": True, "allowed": []})

    assert "current_time" in executor.registry.names()
    assert executor.available() == []
    assert "not allowed" in executor.check("current_time")
