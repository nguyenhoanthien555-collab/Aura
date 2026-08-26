"""
Tool framework tests.

tests/test_tools.py covers the five permission gates. This file covers
the two things Section 7 added underneath them:

    ToolProtocol      what a tool is, structurally
    the timeout       what happens when a tool never returns

Nothing here touches the network, the filesystem or a subprocess. The
slowest test waits a fraction of a second on a threading.Event, which is
the only honest way to test a deadline.
"""

import threading
import time

import pytest

from events.bus import EventBus
from events.types import ToolCompletedEvent

from tools.base import (
    Parameter,
    Tool,
    ToolProtocol,
    ToolRisk,
    describe_tool,
    fail,
    ok,
)
from tools.executor import ToolExecutor, ToolPolicy
from tools.registry import ToolRegistry
from tools.timeout import (
    DEFAULT_TOOL_TIMEOUT,
    ToolTimeout,
    call_with_timeout,
    seconds_or,
)


# ----------------------------------------------------------------------
# Doubles
# ----------------------------------------------------------------------

class StructuralTool:
    """
    A tool that inherits nothing.

    The point of the Protocol: three attributes and a method, no import
    of tools.base required beyond the risk level itself.
    """

    name = "structural"
    risk = ToolRisk.SAFE

    def __init__(self):
        self.calls: list[dict] = []

    def execute(self, **arguments) -> str:
        self.calls.append(dict(arguments))
        return "ran"


class EchoingTool(Tool):
    """The ordinary path: inherits the base class, declares parameters."""

    name = "echo"
    description = "Repeat a string back"
    risk = ToolRisk.SAFE

    parameters = (Parameter(name="text", description="What to repeat"),)

    def execute(self, text: str) -> str:
        return text


class HangingTool(Tool):
    """
    Blocks until released. Stands in for a dead network share or a
    subprocess that never exits.

    The release is exposed so a test can let the worker finish rather
    than leaving it parked for the rest of the session.
    """

    name = "hang"
    description = "Never returns on its own"
    risk = ToolRisk.SAFE

    def __init__(self):
        self.release = threading.Event()
        self.started = threading.Event()

    def execute(self, **arguments) -> str:
        self.started.set()
        self.release.wait(30.0)
        return "finally"


class ThreadRecordingTool(Tool):
    """Records which thread it ran on."""

    name = "which_thread"
    description = "Reports its own thread"
    risk = ToolRisk.SAFE

    def __init__(self, timeout=None):
        self.timeout = timeout
        self.thread: threading.Thread | None = None

    def execute(self, **arguments) -> str:
        self.thread = threading.current_thread()
        return self.thread.name


def executor_for(tool, **policy) -> ToolExecutor:

    policy.setdefault("enabled", True)
    policy.setdefault("allowed", frozenset({tool.name}))

    return ToolExecutor(
        registry=ToolRegistry([tool]),
        policy=ToolPolicy(**policy),
    )


@pytest.fixture
def hanging():
    """A hanging tool that is always released, however the test ends."""

    tool = HangingTool()

    yield tool

    tool.release.set()


# ----------------------------------------------------------------------
# The Protocol
# ----------------------------------------------------------------------

def test_the_base_class_satisfies_the_protocol():
    """
    The ABC is one implementation of the interface, not the interface.
    If this ever fails, every builtin has stopped being a tool.
    """

    assert isinstance(StructuralTool(), ToolProtocol)

    class Inheriting(Tool):
        name = "inheriting"
        risk = ToolRisk.SAFE

        def execute(self, **arguments):
            return ""

    assert isinstance(Inheriting(), ToolProtocol)


def test_a_tool_needs_no_base_class_at_all():
    """
    The whole point of Section 7. A plugin can ship this object without
    importing anything from tools/ except the risk level.
    """

    tool = StructuralTool()

    result = executor_for(tool).execute("structural", {"x": 1})

    assert result.ok is True
    assert result.output == "ran"
    assert tool.calls == [{"x": 1}]


def test_something_that_is_not_a_tool_is_refused_at_registration():
    """
    The boundary check. A malformed tool is far easier to diagnose here
    than halfway through a call it cannot complete.
    """

    class NotATool:
        name = "impostor"
        risk = ToolRisk.SAFE

    with pytest.raises(ValueError, match="ToolProtocol"):
        ToolRegistry().register(NotATool())


def test_a_tool_whose_risk_is_not_a_risk_is_refused():
    """
    A Protocol checks that an attribute exists, never what it is. A tool
    with `risk = "safe"` would sail past isinstance and then fail the
    approval gate open, because a plain string is in no auto_approve set
    and its `.value` does not exist.
    """

    class Sloppy:
        name = "sloppy"
        risk = "safe"

        def execute(self, **arguments):
            return ""

    with pytest.raises(ValueError, match="ToolRisk"):
        ToolRegistry().register(Sloppy())


def test_the_protocol_stayed_narrow():
    """
    The widening hazard, stated as a test.

    ToolProtocol is runtime_checkable, so every name on it is a
    requirement for every tool that will ever exist - including ones
    written outside this repository. Adding `describe` or `parameters`
    to it would break them all overnight, and this is what would notice.

    Written as the smallest object that must qualify rather than as an
    assertion about the Protocol's internals, because that is the thing
    actually being promised.
    """

    class Minimal:
        name = "minimal"
        risk = ToolRisk.SAFE

        def execute(self, **arguments):
            return ""

    assert isinstance(Minimal(), ToolProtocol)


def test_a_tool_can_be_described_without_inheriting_describe():
    """
    `describe` is not on the Protocol, so the registry cannot assume it.
    A tool that has one is asked; a tool that does not still describes.
    """

    assert describe_tool(StructuralTool()) == "structural"

    class Documented:
        name = "documented"
        description = "Does a thing"
        risk = ToolRisk.SAFE

        def execute(self, **arguments):
            return ""

    assert describe_tool(Documented()) == "documented: Does a thing"


def test_a_tool_that_describes_itself_is_asked_first():
    tool = EchoingTool()

    assert describe_tool(tool) == tool.describe()
    assert "text" in describe_tool(tool)


def test_a_tool_whose_describe_raises_still_describes():
    """One broken tool must not empty the whole TOOLS prompt section."""

    class Awkward:
        name = "awkward"
        description = "Cannot introduce itself"
        risk = ToolRisk.SAFE

        def describe(self):
            raise RuntimeError("nope")

        def execute(self, **arguments):
            return ""

    assert describe_tool(Awkward()) == "awkward: Cannot introduce itself"


def test_the_registry_describes_a_mixed_set():
    registry = ToolRegistry([StructuralTool(), EchoingTool()])

    described = registry.describe()

    assert "structural" in described
    assert "echo" in described


def test_a_structural_tool_with_no_declared_parameters_is_not_blocked():
    """
    `required_parameters` is not on the Protocol. No declaration means no
    requirement - the tool has not been checked, rather than checked and
    found to need nothing.
    """

    tool = StructuralTool()

    assert executor_for(tool).execute("structural", {}).ok is True


def test_a_tool_missing_its_own_arguments_fails_rather_than_crashing():
    """
    The other half of the same decision. Without a declaration the gate
    cannot catch this, so Python does - and the executor turns the
    TypeError into a failed result like any other.
    """

    class Strict:
        name = "strict"
        risk = ToolRisk.SAFE

        def execute(self, required):
            return required

    result = executor_for(Strict()).execute("strict", {})

    assert result.ok is False
    assert "TypeError" in result.error


def test_a_tool_whose_parameter_list_raises_does_not_break_the_call():

    class Moody(Tool):
        name = "moody"
        risk = ToolRisk.SAFE

        def required_parameters(self):
            raise RuntimeError("nope")

        def execute(self, **arguments):
            return "ran"

    assert executor_for(Moody()).execute("moody", {}).ok is True


def test_the_declared_parameters_are_still_enforced_when_present():
    """The ABC path has not changed."""

    result = executor_for(EchoingTool()).execute("echo", {})

    assert result.ok is False
    assert "missing arguments" in result.error


# ----------------------------------------------------------------------
# call_with_timeout, on its own
# ----------------------------------------------------------------------

def test_a_call_that_returns_in_time_returns_its_value():
    assert call_with_timeout(lambda: "value", timeout=5.0) == "value"


def test_a_call_that_raises_raises_through_the_thread():
    """
    The failure has to cross the thread boundary intact, or the executor
    would report "returned None" for something that actually exploded.
    """

    def boom():
        raise ValueError("kaboom")

    with pytest.raises(ValueError, match="kaboom"):
        call_with_timeout(boom, timeout=5.0)


def test_an_unbounded_call_runs_on_the_calling_thread():
    """
    Zero is not a loophole but an escape hatch. A tool that opts out
    keeps whatever thread affinity it had, which is the reason to offer
    the option at all.
    """

    here = threading.current_thread()

    assert call_with_timeout(threading.current_thread, timeout=0) is here


def test_a_bounded_call_runs_somewhere_else():
    here = threading.current_thread()

    assert call_with_timeout(threading.current_thread, timeout=5.0) is not here


def test_a_call_that_overruns_raises_tool_timeout():
    released = threading.Event()

    try:
        with pytest.raises(ToolTimeout, match="timed out"):
            call_with_timeout(
                lambda: released.wait(30.0),
                timeout=0.05,
                name="slow",
            )
    finally:
        released.set()


def test_the_timeout_message_names_the_tool_and_the_limit():
    released = threading.Event()

    try:
        with pytest.raises(ToolTimeout) as raised:
            call_with_timeout(
                lambda: released.wait(30.0),
                timeout=0.05,
                name="read_file",
            )
    finally:
        released.set()

    assert "read_file" in str(raised.value)
    assert "0.05" in str(raised.value)


# ----------------------------------------------------------------------
# seconds_or
# ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw",
    [None, "", "nope", True, False, -1, -0.5, object()],
)
def test_an_unusable_timeout_falls_back(raw):
    """
    A negative or unreadable limit must never quietly become "give up
    instantly", which is what float() would make of some of these.
    """

    assert seconds_or(raw, 30.0) == 30.0


@pytest.mark.parametrize(
    "raw, expected",
    [(5, 5.0), (2.5, 2.5), ("10", 10.0), (0, 0.0), ("0", 0.0)],
)
def test_a_usable_timeout_is_taken_literally(raw, expected):
    """Zero included: it is the documented way to say "no bound"."""

    assert seconds_or(raw, 30.0) == expected


# ----------------------------------------------------------------------
# The timeout, through the executor
# ----------------------------------------------------------------------

def test_a_tool_that_never_returns_becomes_a_failed_result(hanging):
    """
    The reason any of this exists. Without a bound, one tool call stops
    the conversation instead of failing it.
    """

    result = executor_for(hanging, timeout=0.05).execute("hang", {})

    assert result.ok is False
    assert "timed out" in result.error


def test_a_timeout_is_not_reported_as_a_crash(hanging):
    """
    "timed out" tells a user to raise the limit. "RuntimeError" sends
    them looking for a bug in a tool that may be working perfectly.
    """

    result = executor_for(hanging, timeout=0.05).execute("hang", {})

    assert "Error" not in result.error
    assert "hang" in result.error


def test_a_timeout_reaches_the_bus_like_any_other_failure(hanging):
    bus = EventBus()
    completed: list = []
    bus.subscribe(ToolCompletedEvent, completed.append)

    executor = executor_for(hanging, timeout=0.05)
    executor.events = bus

    executor.execute("hang", {})

    assert completed[0].ok is False
    assert "timed out" in completed[0].detail


def test_a_timeout_is_recorded_in_history(hanging):
    executor = executor_for(hanging, timeout=0.05)

    executor.execute("hang", {})

    assert executor.history == [("hang", False)]


def test_giving_up_does_not_wait_for_the_tool(hanging):
    """
    The wait is bounded, not the tool. `execute` has to return on time
    even though the call behind it is still running.
    """

    executor = executor_for(hanging, timeout=0.05)

    started = time.monotonic()
    executor.execute("hang", {})
    elapsed = time.monotonic() - started

    assert elapsed < 5.0
    assert hanging.started.is_set()


def test_a_tool_that_answers_is_not_delayed_by_its_deadline():
    tool = EchoingTool()

    started = time.monotonic()
    result = executor_for(tool, timeout=30.0).execute("echo", {"text": "hi"})
    elapsed = time.monotonic() - started

    assert result.output == "hi"
    assert elapsed < 5.0


def test_a_bounded_tool_runs_on_a_worker_thread():
    tool = ThreadRecordingTool()

    executor_for(tool, timeout=5.0).execute("which_thread", {})

    assert tool.thread is not threading.current_thread()


def test_a_tool_can_opt_out_of_the_bound_entirely():
    """
    `timeout = 0` on the tool. Documented, declared on the base class,
    and the only way to keep the calling thread.
    """

    tool = ThreadRecordingTool(timeout=0)

    executor_for(tool, timeout=5.0).execute("which_thread", {})

    assert tool.thread is threading.current_thread()


def test_a_tool_can_raise_its_own_limit_above_the_policy():
    """
    Only the tool knows it shells out to something slow. The policy sets
    the default; the tool overrides it.
    """

    class Deliberate(Tool):
        name = "deliberate"
        risk = ToolRisk.SAFE
        timeout = 10.0

        def execute(self, **arguments):
            time.sleep(0.15)
            return "worth the wait"

    result = executor_for(Deliberate(), timeout=0.01).execute(
        "deliberate", {}
    )

    assert result.ok is True
    assert result.output == "worth the wait"


def test_a_tool_with_a_nonsense_timeout_falls_back_to_the_policy(hanging):
    """A typo in one tool must not disable the bound for it."""

    hanging.timeout = "soon"

    result = executor_for(hanging, timeout=0.05).execute("hang", {})

    assert result.ok is False
    assert "timed out" in result.error


def test_a_tool_declaring_no_timeout_uses_the_policy():
    assert EchoingTool().timeout is None

    executor = executor_for(EchoingTool(), timeout=7.0)

    assert executor._timeout_for(EchoingTool()) == 7.0


def test_an_exception_still_becomes_a_failed_result_across_the_thread():

    class Exploding(Tool):
        name = "explode"
        risk = ToolRisk.SAFE

        def execute(self, **arguments):
            raise RuntimeError("kaboom")

    result = executor_for(Exploding(), timeout=5.0).execute("explode", {})

    assert result.ok is False
    assert "kaboom" in result.error


def test_a_tool_result_survives_the_thread_unchanged():
    """The structured result is not flattened by being carried back."""

    class Structured(Tool):
        name = "structured"
        risk = ToolRisk.SAFE

        def execute(self, **arguments):
            return ok("payload", tool="structured")

    result = executor_for(Structured(), timeout=5.0).execute("structured", {})

    assert result.ok is True
    assert result.output == "payload"


# ----------------------------------------------------------------------
# Policy and config
# ----------------------------------------------------------------------

def test_a_fresh_policy_has_a_bound():
    """
    On by default. A timeout nobody opted into is the only kind that
    helps the user who did not know to ask for one.
    """

    assert ToolPolicy().timeout == DEFAULT_TOOL_TIMEOUT
    assert DEFAULT_TOOL_TIMEOUT > 0


def test_the_timeout_comes_from_config():
    policy = ToolPolicy.from_config({"timeout": 5})

    assert policy.timeout == 5.0


def test_a_config_without_a_timeout_still_has_one():
    assert ToolPolicy.from_config({}).timeout == DEFAULT_TOOL_TIMEOUT


def test_config_can_switch_the_bound_off():
    assert ToolPolicy.from_config({"timeout": 0}).timeout == 0.0


def test_the_default_config_ships_a_tool_timeout():
    from core.config import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["tools"]["timeout"] > 0




# ----------------------------------------------------------------------
# verify() - Section 11 at the tool layer
#
# tests/test_tools.py proves RememberTool's own postcondition. These
# prove the *mechanism*: the executor consults a tool's verify() after a
# successful execute() and downgrades a success the postcondition does
# not support, because "the call returned without throwing" is exactly
# what Section 11 forbids trusting on its own.
# ----------------------------------------------------------------------

class VerifyingTool(Tool):
    """
    execute() always claims success; verify() returns whatever it was
    handed. The general shape Section 11 is about - a call that returned
    without raising, and a postcondition that may or may not hold.
    """

    name = "verifying"
    description = "Claims success; its postcondition is configurable"
    risk = ToolRisk.SAFE

    def __init__(self, verdict):
        self._verdict = verdict
        self.verify_calls: list[dict] = []

    def execute(self, **arguments):
        return ok("execute said ok", tool=self.name)

    def verify(self, **arguments):
        self.verify_calls.append(dict(arguments))
        return self._verdict


class FailingThenVerifyingTool(Tool):
    """execute() fails; verify() would pass if it were ever asked."""

    name = "fails_first"
    description = "Fails in execute; records whether verify was consulted"
    risk = ToolRisk.SAFE

    def __init__(self):
        self.verify_called = False

    def execute(self, **arguments):
        return fail("execute failed", tool=self.name)

    def verify(self, **arguments):
        self.verify_called = True
        return ok(tool=self.name)


class RaisingVerifyTool(Tool):
    """execute() succeeds; verify() cannot complete."""

    name = "verify_raises"
    description = "Succeeds, then its verify blows up"
    risk = ToolRisk.SAFE

    def execute(self, **arguments):
        return ok("done", tool=self.name)

    def verify(self, **arguments):
        raise RuntimeError("the database is gone")


def test_a_passing_verify_keeps_the_execute_result():
    """
    The postcondition holds, so nothing is downgraded - and the richer
    line execute() wrote is what survives, not the verify's, because the
    caller wants to hear what happened, not that a check passed.
    """

    tool = VerifyingTool(ok("verify said ok", tool="verifying"))
    result = executor_for(tool).execute("verifying")

    assert result.ok
    assert result.output == "execute said ok"


def test_a_failing_verify_downgrades_a_claimed_success():
    """
    The whole point. execute() returned ok; the postcondition does not
    hold; the caller is told it failed, in the verify's words.
    """

    tool = VerifyingTool(fail("the window never opened", tool="verifying"))
    result = executor_for(tool).execute("verifying")

    assert not result.ok
    assert "the window never opened" in result.error


def test_verify_is_handed_the_arguments():
    """
    verify() re-asks the postcondition from the same arguments execute()
    got, exactly as the device re-checks expected-package against the
    foreground. It cannot do that without them.
    """

    tool = VerifyingTool(ok(tool="verifying"))

    executor_for(tool).execute("verifying", {"target": "chrome"})

    assert tool.verify_calls == [{"target": "chrome"}]


def test_a_tool_without_verify_is_unaffected():
    """
    Optional by absence. A tool that never heard of verify() is verified
    by its own execute() and nothing more - the Protocol stays three
    members wide.
    """

    tool = StructuralTool()
    result = executor_for(tool).execute("structural")

    assert result.ok
    assert result.output == "ran"


def test_verify_is_not_consulted_after_a_failure():
    """
    There is no success to second-guess on a call that already failed,
    and a verify() run against a failed side effect would be checking a
    postcondition that was never meant to hold. It must not even be
    called.
    """

    tool = FailingThenVerifyingTool()
    result = executor_for(tool).execute("fails_first")

    assert not result.ok
    assert tool.verify_called is False


def test_a_verify_that_raises_fails_closed():
    """
    Section 11 again, one level up: a verify() that raised gives exactly
    "it ran without throwing" - for the verify. That is not confirmation,
    so the result is reported unverified rather than trusted.
    """

    result = executor_for(RaisingVerifyTool()).execute("verify_raises")

    assert not result.ok
    assert "verify" in result.error.lower()


def test_a_verify_returning_none_asserts_no_postcondition():
    """
    None is "I have nothing to check here", not "it failed". The result
    execute() produced stands.
    """

    tool = VerifyingTool(None)
    result = executor_for(tool).execute("verifying")

    assert result.ok
    assert result.output == "execute said ok"


def test_a_failing_verify_reaches_the_bus_as_a_failure():
    """
    A downgrade is a real failure, so it is as visible on the bus as any
    other - a watcher must not see ToolCompletedEvent(ok=True) for a call
    the postcondition rejected.
    """

    bus = EventBus()
    seen: list[ToolCompletedEvent] = []
    bus.subscribe(ToolCompletedEvent, seen.append)

    tool = VerifyingTool(fail("postcondition not met", tool="verifying"))
    ToolExecutor(
        registry=ToolRegistry([tool]),
        policy=ToolPolicy(enabled=True, allowed=frozenset({"verifying"})),
        events=bus,
    ).execute("verifying")

    assert len(seen) == 1
    assert seen[0].ok is False
