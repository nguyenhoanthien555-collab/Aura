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
    capability = "echo"
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
    capability = "touch"
    description = "Pretend to change something"
    risk = ToolRisk.DANGEROUS

    def __init__(self):
        self.ran = 0

    def execute(self, **arguments) -> str:
        self.ran += 1
        return "done"


class ReadTool(Tool):

    name = "peek"
    capability = "peek"
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
    assert "permission denied" in fail("permission denied").render()


def test_an_unlabelled_tool_is_dangerous_by_default():
    """A tool author cannot opt out of permissions by forgetting to."""

    class Unlabelled(Tool):
        name = "unlabelled"
        capability = "unlabelled"

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
        capability = "nameless"

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
        capability = "structured"
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
        capability = "explode"
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
        capability = "verbose"
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


# ======================================================================
# remember - the semantic tier's missing caller (section 17)
#
# `MemoryPipeline.remember_user_stated` and `remember_user_correction`
# were written, tested and then called by nothing outside the test
# suite, so Aura could not learn a durable keyed fact from a
# conversation no matter what the user told her. The tier existed; the
# door into it did not.
#
# A tool rather than an extraction pass over every message, for two
# reasons. The keys are namespaced (`identity.name`, not `name`), and
# nothing short of a model can turn "I'm Thien btw" into that key, so
# regex extraction would either invent keys or miss most facts. And
# `memory/user_model.py` is explicit that CONFIRMED means the user
# actually said it - a tool call the model makes deliberately, with the
# key it chose, is that; a background scraper guessing at intent is not.
# ======================================================================

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.temporal import TemporalClock
from memory.models import Base
from memory.pipeline import MemoryPipeline
from memory.user_model import CATEGORIES, IDENTITY, PROJECT, Status
from tools.builtins.memory import RememberTool


@pytest.fixture
def pipeline_for_tools():
    """
    A real pipeline on an isolated in-memory database.

    Real rather than a mock: the thing under test is whether a fact
    survives being written, and a mock that records the call would pass
    with the write silently dropped.
    """

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    yield MemoryPipeline(
        session=session,
        clock=TemporalClock(now=lambda: datetime(2026, 8, 24, 10, 30)),
    )

    session.close()


@pytest.fixture
def remember(pipeline_for_tools):
    return RememberTool(pipeline_for_tools)


def test_remembering_stores_a_confirmed_fact(remember, pipeline_for_tools):
    """
    The point of the whole tier: it is still there afterwards.

    CONFIRMED rather than INFERRED because the user said it. That
    distinction is the user model's central rule and the tool does not
    get to blur it - `infer()` is the door for things Aura worked out.
    """

    remember.execute(key="identity.name", value="Thien")

    model = pipeline_for_tools.user_model

    assert model.value_of("identity.name") == "Thien"
    assert model.status_of("identity.name") is Status.CONFIRMED


def test_remembering_says_what_it_stored(remember):
    """
    The result goes back to the model as TOOL RESULTS and then, usually,
    into a reply to the user. "ok" would leave both guessing about which
    fact landed, and a wrong key stored under a confident "ok" is worse
    than a visible one.
    """

    result = remember.execute(key="project.current", value="Aura")

    assert "project.current" in str(result)
    assert "Aura" in str(result)


def test_a_category_can_be_chosen(remember, pipeline_for_tools):

    remember.execute(key="project.current", value="Aura", category=PROJECT)

    assert pipeline_for_tools.user_model.get("project.current").category == PROJECT


def test_the_default_category_is_identity(remember, pipeline_for_tools):

    remember.execute(key="identity.name", value="Thien")

    assert pipeline_for_tools.user_model.get("identity.name").category == IDENTITY


def test_an_invented_category_is_refused(remember, pipeline_for_tools):
    """
    The constants in `memory/user_model.py` carry a comment promising
    that a typo is an ImportError rather than a silently unqueryable
    row. That promise holds for every caller that imports them, and this
    is the first caller whose category arrives as free text from a
    language model - which imports nothing and cannot get an ImportError.

    So the guarantee has to be re-established here, at the boundary
    where untrusted text becomes a database row, or `category="notes"`
    writes a row that `all(category=...)` can never return.
    """

    result = remember.execute(
        key="identity.name", value="Thien", category="notes"
    )

    assert not result.ok
    assert "notes" in result.error
    assert len(pipeline_for_tools.user_model) == 0


def test_the_refusal_names_the_categories_that_would_work(remember):
    """
    A model that guessed wrong gets to retry from the error text, so the
    error carries the vocabulary rather than only rejecting.
    """

    result = remember.execute(
        key="identity.name", value="Thien", category="notes"
    )

    assert IDENTITY in result.error


def test_an_empty_key_is_refused(remember, pipeline_for_tools):

    result = remember.execute(key="", value="Thien")

    assert not result.ok
    assert len(pipeline_for_tools.user_model) == 0


def test_an_empty_value_is_refused(remember, pipeline_for_tools):
    """
    An empty value is the failure the user model warns about in its own
    docstring: a stored blank reads like a known fact whose answer is
    "nothing", and `value_of` cannot distinguish it from a real one.
    """

    result = remember.execute(key="identity.name", value="   ")

    assert not result.ok
    assert len(pipeline_for_tools.user_model) == 0


def test_remembering_again_updates_rather_than_duplicates(
    remember, pipeline_for_tools
):
    """
    The user changing their mind is ordinary, and two rows for one key
    would put both in the prompt and let the model pick.
    """

    remember.execute(key="identity.name", value="Thien")
    remember.execute(key="identity.name", value="Ember")

    assert pipeline_for_tools.user_model.value_of("identity.name") == "Ember"
    assert len(pipeline_for_tools.user_model) == 1


def test_remembering_is_safe_and_therefore_needs_no_approval(remember):
    """
    SAFE, and the reasoning is worth stating because it is arguable.

    The taxonomy in `tools/base.py` is about damage outside Aura: SAFE
    reads a clock, SENSITIVE moves the user's data somewhere it was not,
    DANGEROUS changes the machine. Remembering sends nothing outward,
    touches nothing on disk but Aura's own database, and files something
    the user just said in the same process that already had it.

    The counter-argument is real: it writes, and it persists. But the
    cost of calling it SENSITIVE is not caution, it is silence -
    `auto_approve: [safe]` plus no human to ask in server mode means
    every call is refused, and the tier goes back to being unreachable
    with a permission error standing in for a design decision.
    """

    executor = executor_for(remember)

    result = executor.execute(
        "remember", {"key": "identity.name", "value": "Thien"}
    )

    assert result.ok is True


def test_remembering_runs_on_the_calling_thread(remember, pipeline_for_tools):
    """
    Not a performance choice - the tool does not work otherwise.

    The executor bounds a tool by running it on a daemon thread, and a
    SQLAlchemy SQLite session belongs to the thread that opened it, so a
    threaded `remember` raises ProgrammingError on every call. It did,
    the first time this suite ran it through an executor rather than
    calling `execute` directly, and it would have done the same in
    production.

    `timeout = 0` is the documented escape hatch for exactly this, and
    this test exists so that deleting it fails here with a reason
    attached rather than somewhere downstream with a thread id.
    """

    assert remember.timeout == 0

    result = executor_for(remember).execute(
        "remember", {"key": "identity.name", "value": "Thien"}
    )

    assert result.ok is True
    assert pipeline_for_tools.user_model.value_of("identity.name") == "Thien"


# ----------------------------------------------------------------------
# remember and Section 11
#
# `remember_user_stated` returns the Belief it wrote, or None when it
# wrote nothing - and `execute` discarded that return value, so the tool
# reported "remembered X = Y" for writes that never happened. Two live
# paths reach it, both with a key chosen by a language model:
#
#   key="???"   -> the slug is punctuation only and strips to empty
#   key="名前"   -> normalise_key keeps [a-z0-9] and the slug is empty
#
# The second is not hypothetical on this owner's machine. Either way the
# user was told a fact about them was saved and nothing was stored.
# ----------------------------------------------------------------------

@pytest.mark.parametrize("key", ["???", "---", "名前", "!!!"])
def test_a_key_that_stores_nothing_is_not_reported_as_remembered(
    remember, pipeline_for_tools, key
):
    """
    Section 11: not "the call returned without throwing" but "the fact is
    there". A model told "remembered" writes it into the reply, and the
    user is then told something false about their own profile.
    """

    result = remember.execute(key=key, value="Thien")

    assert not result.ok
    assert len(pipeline_for_tools.user_model) == 0


def test_the_refusal_names_the_key_it_could_not_keep(remember):
    """
    The reason goes back to the model as TOOL RESULTS, which is its only
    chance to choose a key that survives. A bare "failed" would have it
    retry the same unusable key.
    """

    result = remember.execute(key="名前", value="Thien")

    assert "名前" in result.error


def test_a_fact_that_lands_is_still_reported_as_remembered(remember):
    """The ordinary path is untouched by the guard above."""

    result = remember.execute(key="identity.name", value="Thien")

    assert result.ok
    assert "Thien" in result.output


def test_verify_confirms_a_fact_that_reads_back(remember):
    """
    The postcondition of remembering is that a later recall can find it,
    so the postcondition is a read, through the same door the prompt
    uses.
    """

    remember.execute(key="identity.name", value="Thien")

    assert remember.verify(key="identity.name", value="Thien").ok


def test_verify_rejects_a_fact_that_is_not_readable_back(remember):
    """
    Nothing was ever stored under this key. verify() must say so rather
    than pass by default - a postcondition that cannot fail is not one.
    """

    verdict = remember.verify(key="identity.name", value="Thien")

    assert not verdict.ok
    assert "identity.name" in verdict.error
