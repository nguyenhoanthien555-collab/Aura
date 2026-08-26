"""
The on-demand vision tool.

Phase 19.2. Aura has had ambient vision since phase 4: a line in every
prompt saying what is on screen, throttled and silent on failure, that
nobody asked for. This is the other half - the model deciding it needs to
look, saying so, and getting an answer back through the same five gates
every other tool goes through.

What this file pins, and why each one is here rather than assumed:

  * `execute` observes now. It calls `refresh()`, not `get_context()`,
    because the throttle that stops fifty turns becoming fifty
    screenshots would answer a question nobody asked - the model said
    "look", and a two second old cache is a different answer.

  * `execute` refuses when vision is off. `VisionManager.refresh()` does
    not consult `enabled`; only `get_context()` does. So the *absence* of
    a check here would be a tool that looks at a screen the owner said
    not to look at, which is section 2 with pixels attached. Pinned by
    asserting nothing was observed, not just that it raised.

  * `verify` is a real postcondition. Section 11 forbids trusting "it did
    not throw", and for this tool the lie it has to catch is specific:
    "I looked and saw X" and "I did not look and X is what I remember"
    are the same string. Freshness is the only reading that separates
    them.

  * `verify` does not look again. It reads `last_observation` and
    `seconds_since_observation` rather than `get_context()`, which
    re-observes once its throttle expires - with `min_interval: 0`, every
    single time. That would be verification paying the full price of the
    thing it verifies, and with a hosted provider in the chain the price
    is a second upload of the owner's screen.

  * The risk level is read off the processor chain per instance. Reading
    the screen is SENSITIVE. Sending a picture of it to a third party is
    not the same act, and section 30 does not let the second ride on the
    first's permission.

  * Registered is not enabled, twice over: the factory only registers the
    tool while vision is on, and the shipped `config.yaml` does not name
    it in `tools.allowed`.
"""

import inspect

import pytest
import yaml

from tools.base import ToolProtocol, ToolRisk
from tools.builtins.vision import STALE_AFTER, DescribeScreenTool, _risk_for
from tools.executor import ToolExecutor, ToolPolicy
from tools.factory import build_registry
from tools.registry import ToolRegistry

from vision.capture import MockWindowReader
from vision.cloud_processor import CloudVisionProcessor
from vision.manager import VisionManager
from vision.processor import (
    MockVisionProcessor,
    ProcessorChain,
    WindowTitleProcessor,
)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

class FakeClock:
    """A clock the test moves by hand."""

    def __init__(self, now: float = 0.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class SteppingClock:
    """
    A clock that moves on its own, every time it is read.

    The only way to get time to pass *inside* one `ToolExecutor.execute`
    call: the executor runs `execute` and then `verify` with nothing
    between them that a test can reach.
    """

    def __init__(self, step: float):
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        value = self.now
        self.now += self.step
        return value


class DroppedObservation:
    """
    A manager whose observation vanished between execute and verify.

    Fresh by the clock and holding nothing - the one shape a real
    `VisionManager` cannot be talked into from outside, since `clear()`
    resets both halves at once, and the shape `verify`'s second check
    exists for.
    """

    def __init__(self, age: float = 0.1):
        self.seconds_since_observation = age
        self.last_observation = None
        self.processor = WindowTitleProcessor()

    def is_available(self) -> bool:
        return True

    def refresh(self):
        from vision.context import VisionContext

        return VisionContext(source="screen", description="User is using X")


def manager_for(titles=None, processor=None, clock=None, **kwargs):
    """
    A manager that never touches the real desktop.

    `window_reader` is always injected: the default is the live user32
    reader on Windows, which would make every one of these tests depend
    on whatever window the owner happens to have focused.
    """

    kwargs.setdefault("enabled", True)

    return VisionManager(
        window_reader=MockWindowReader(titles=list(titles or ["Calculator"])),
        processor=processor,
        clock=clock or FakeClock(),
        **kwargs,
    )


def executor_for(*instances, allowed=None, approve=True):
    """An executor with these tools registered and DANGEROUS approved."""

    registry = ToolRegistry()

    for instance in instances:
        registry.register(instance)

    names = allowed if allowed is not None else [t.name for t in instances]

    return ToolExecutor(
        registry=registry,
        policy=ToolPolicy(
            enabled=True,
            allowed=names,
            auto_approve=(
                ["safe", "sensitive", "dangerous"] if approve else ["safe"]
            ),
        ),
        confirm=(lambda tool, arguments: True) if approve else None,
    )


# ----------------------------------------------------------------------
# The shape of the tool
# ----------------------------------------------------------------------

class TestTheShape:

    def test_verify_accepts_exactly_what_execute_accepts(self):
        """
        The executor calls `verify(**arguments)` with the same dict it
        passed to `execute`. A signature that has drifted apart turns
        every successful call into "ran but could not be verified".
        """

        tool = DescribeScreenTool(manager_for())

        assert (
            inspect.signature(tool.execute).parameters.keys()
            == inspect.signature(tool.verify).parameters.keys()
        )

    def test_it_satisfies_the_tool_protocol(self):
        """`ToolRegistry.register` refuses anything that does not."""

        assert isinstance(DescribeScreenTool(manager_for()), ToolProtocol)

    def test_it_takes_no_arguments(self):
        """
        Nothing to get wrong, and nothing to lie about. The manager is
        bound to one capture at build time, so a `monitor` parameter
        could only name a display it did not change.
        """

        tool = DescribeScreenTool(manager_for())

        assert tool.parameters == ()
        assert tool.required_parameters() == []

        # Gate five therefore never has anything to reject.
        assert not inspect.signature(tool.execute).parameters


# ----------------------------------------------------------------------
# Execute
# ----------------------------------------------------------------------

class TestExecute:

    def test_it_describes_the_active_window(self):

        tool = DescribeScreenTool(manager_for(titles=["Calculator"]))

        assert tool.execute() == "[screen] User is using Calculator"

    def test_it_observes_now_rather_than_reusing_the_throttled_cache(self):
        """
        The one behaviour that separates this tool from the ambient line
        already in the prompt. The model asked to look; handing it a
        cached observation answers a different question.
        """

        clock = FakeClock()

        manager = manager_for(
            titles=["Calculator", "Notepad"],
            clock=clock,
            min_interval=100.0,
        )

        first = manager.get_context()

        assert first.description == "User is using Calculator"

        # Well inside the throttle, so `get_context()` would reuse.
        clock.advance(1.0)

        assert manager.get_context().description == "User is using Calculator"

        assert DescribeScreenTool(manager).execute() == (
            "[screen] User is writing code in Notepad"
        )

    def test_vision_switched_off_refuses_and_does_not_look(self):
        """
        Section 2. `refresh()` does not consult `enabled`, so a tool
        without this check would look at a screen the owner had said not
        to look at - and would do it silently, since nothing downstream
        knows the difference.
        """

        processor = MockVisionProcessor("User is at the desktop")

        manager = manager_for(processor=processor, enabled=False)

        tool = DescribeScreenTool(manager)

        with pytest.raises(RuntimeError) as error:
            tool.execute()

        assert "vision.enabled" in str(error.value)

        # The half that matters: it refused *before* observing.
        assert processor.calls == 0
        assert manager.seconds_since_observation is None
        assert manager.last_observation is None

    def test_nothing_observable_raises_rather_than_answering_emptily(self):
        """
        An empty description is not an answer. Returning "" would put a
        successful tool result carrying nothing into the transcript, and
        the model would have no way to tell that from a blank screen.
        """

        manager = manager_for(titles=[""], processor=MockVisionProcessor(""))

        with pytest.raises(RuntimeError) as error:
            DescribeScreenTool(manager).execute()

        assert "could not be described" in str(error.value)


# ----------------------------------------------------------------------
# Verify - section 11
# ----------------------------------------------------------------------

class TestVerify:

    def test_a_real_observation_verifies(self):

        tool = DescribeScreenTool(manager_for())

        tool.execute()

        verdict = tool.verify()

        assert verdict.ok
        assert "still held" in verdict.output

    def test_nothing_observed_fails_verification(self):
        """
        Reached by calling verify without execute, which is also what a
        manager looks like when `execute` reported a description that
        came from somewhere other than looking.
        """

        verdict = DescribeScreenTool(manager_for()).verify()

        assert not verdict.ok
        assert "nothing was observed" in verdict.error

    def test_an_old_observation_fails_verification(self):
        """
        The check that catches the tool's one plausible lie: a
        description served out of a cache by something that never
        looked. `STALE_AFTER` is imported rather than written down here,
        so the test moves with the bound instead of pinning a number the
        code no longer uses.
        """

        clock = FakeClock()

        tool = DescribeScreenTool(manager_for(clock=clock))

        tool.execute()

        clock.advance(STALE_AFTER + 1.0)

        verdict = tool.verify()

        assert not verdict.ok
        assert "old rather than from looking now" in verdict.error

    def test_an_observation_just_inside_the_bound_still_verifies(self):
        """
        The bound is not a latency policy. `refresh` stamps its clock
        before asking the processor, so a vision model that takes half a
        minute to answer has done nothing wrong.
        """

        clock = FakeClock()

        tool = DescribeScreenTool(manager_for(clock=clock))

        tool.execute()

        clock.advance(STALE_AFTER - 1.0)

        assert tool.verify().ok

    def test_a_dropped_observation_fails_verification(self):

        verdict = DescribeScreenTool(DroppedObservation()).verify()

        assert not verdict.ok
        assert "not retained" in verdict.error

    def test_clearing_the_manager_fails_verification(self):
        """
        `clear()` resets the timestamp as well as the context, so this
        lands on the first branch rather than the second. Pinned as it
        actually behaves - both readings are gone, and either one being
        gone is enough to refuse.
        """

        tool = DescribeScreenTool(manager_for())

        tool.execute()

        tool.vision.clear()

        assert not tool.verify().ok

    def test_verification_does_not_observe_a_second_time(self):
        """
        Why `last_observation` exists. `min_interval: 0` makes
        `get_context()` re-observe on every single call, so a verify
        written against it would capture the screen twice per tool call -
        and with a hosted provider in the chain, upload it twice.
        """

        processor = MockVisionProcessor("User is at the desktop")

        manager = manager_for(processor=processor, min_interval=0.0)

        tool = DescribeScreenTool(manager)

        tool.execute()

        assert processor.calls == 1

        assert tool.verify().ok

        assert processor.calls == 1


# ----------------------------------------------------------------------
# Risk - section 30
# ----------------------------------------------------------------------

class TestRisk:

    def test_titles_only_is_sensitive(self):
        """Nothing leaves the machine, so this is reading, not sending."""

        tool = DescribeScreenTool(
            manager_for(processor=WindowTitleProcessor())
        )

        assert tool.risk is ToolRisk.SENSITIVE

    def test_a_local_chain_is_sensitive(self):

        chain = ProcessorChain(
            [MockVisionProcessor(), WindowTitleProcessor()]
        )

        assert DescribeScreenTool(
            manager_for(processor=chain)
        ).risk is ToolRisk.SENSITIVE

    def test_a_cloud_processor_makes_it_dangerous(self):
        """
        Constructed with no providers, so nothing can reach a network.
        The flag is a statement about what the class can do, not about
        whether it is currently configured to do it - over-reporting
        costs one confirmation prompt and under-reporting costs an
        upload nobody approved.
        """

        cloud = CloudVisionProcessor([])

        assert DescribeScreenTool(
            manager_for(processor=cloud)
        ).risk is ToolRisk.DANGEROUS

    def test_a_chain_containing_a_cloud_processor_is_dangerous(self):
        """
        A chain is as leaky as its leakiest member, and the cloud link
        sits in the middle of the shipped order rather than at either
        end - so asking the first processor would get this wrong.
        """

        chain = ProcessorChain(
            [
                MockVisionProcessor(),
                CloudVisionProcessor([]),
                WindowTitleProcessor(),
            ]
        )

        assert DescribeScreenTool(
            manager_for(processor=chain)
        ).risk is ToolRisk.DANGEROUS

    def test_a_processor_that_says_nothing_is_taken_at_its_word(self):
        """
        `sends_pixels_offsite` is a fact a processor may advertise, not a
        member the VisionProcessor protocol demands, so one written
        before the flag existed still works.
        """

        class Quiet:
            def describe(self, frame, window_title=""):
                return "something"

        assert _risk_for(Quiet()) is ToolRisk.SENSITIVE

    def test_a_missing_processor_is_sensitive_rather_than_a_crash(self):

        assert _risk_for(None) is ToolRisk.SENSITIVE

    def test_the_registry_reads_the_risk_off_the_instance(self):
        """
        `ToolRegistry.register` validates `tool.risk`, and the approval
        gate reads it again per call. A per-instance value that the
        registry flattened back to the class default would make the
        cloud upgrade decorative.
        """

        registry = ToolRegistry()

        registry.register(
            DescribeScreenTool(manager_for(processor=CloudVisionProcessor([])))
        )

        assert registry.get("describe_screen").risk is ToolRisk.DANGEROUS


# ----------------------------------------------------------------------
# Registration - the dependency gate
# ----------------------------------------------------------------------

class TestRegistration:

    def test_no_vision_manager_means_no_tool(self):

        assert "describe_screen" not in build_registry({})._tools

    def test_vision_switched_off_means_no_tool(self, caplog):
        """
        The gate is `is_available()`, not "a manager exists". A manager
        is always built; `refresh()` works whether vision is on or off,
        so registering around a disabled one would be a way to look at a
        screen the owner closed.
        """

        # `logger="Aura"`, not a bare at_level: under pytest the root
        # logger already has a handler when core.logger is imported, so
        # `setup_logger` returns early and never sets Aura's own level.
        # It is therefore NOTSET here but INFO after any earlier test has
        # run `apply_config_level` - and at INFO a DEBUG record is
        # dropped at the logger, before caplog's root handler can see it.
        # A bare at_level only raises root's level, so this assertion
        # passed alone and failed in the full suite.
        with caplog.at_level("DEBUG", logger="Aura"):
            registry = build_registry(
                {}, None, manager_for(enabled=False)
            )

        assert "describe_screen" not in registry._tools
        assert "describe_screen not registered" in caplog.text

    def test_vision_switched_on_registers_it(self):

        registry = build_registry({}, None, manager_for())

        assert "describe_screen" in registry._tools

        # And the rest of the factory still ran, so a mistake in the
        # block cannot pass by registering nothing at all.
        assert "current_time" in registry._tools

    def test_the_shipped_config_does_not_allow_it(self):
        """
        Read off disk rather than through `load_config()`, so a
        defaulting bug in the loader cannot hide a silent enable.
        """

        with open("config.yaml", encoding="utf-8") as handle:
            config = yaml.safe_load(handle)

        assert "describe_screen" not in (config["tools"]["allowed"] or [])


# ----------------------------------------------------------------------
# Through the executor
# ----------------------------------------------------------------------

class TestThroughTheExecutor:

    def test_an_approved_call_returns_the_description(self):

        tool = DescribeScreenTool(manager_for(titles=["Calculator"]))

        result = executor_for(tool).execute("describe_screen")

        assert result.ok
        assert result.output == "[screen] User is using Calculator"

    def test_registered_but_not_allowed_never_looks(self):
        """
        Gate three. The shipped config is exactly this case, so this is
        what `describe_screen` does on a machine where the owner has not
        named it: nothing, including no capture.
        """

        processor = MockVisionProcessor("User is at the desktop")

        tool = DescribeScreenTool(manager_for(processor=processor))

        result = executor_for(tool, allowed=[]).execute("describe_screen")

        assert not result.ok
        assert result.error == "tool not allowed by policy: describe_screen"
        assert processor.calls == 0

    def test_allowed_but_unapproved_never_looks(self):
        """
        Gate four, and the one that matters on a server: `build_tools`
        attaches no confirmation handler there, because there is nobody
        at the other end of an HTTP request to ask. SENSITIVE is outside
        the shipped `auto_approve`, so the call is refused rather than
        approved by default.
        """

        processor = MockVisionProcessor("User is at the desktop")

        tool = DescribeScreenTool(manager_for(processor=processor))

        result = executor_for(tool, approve=False).execute("describe_screen")

        assert not result.ok
        assert result.error == "permission denied for describe_screen"
        assert processor.calls == 0

    def test_a_failed_verification_downgrades_the_success(self):
        """
        End to end proof that verify is load bearing rather than
        advisory: execute succeeds, the observation ages past the bound
        before verify reads it, and the executor turns the success into
        a failure.
        """

        clock = SteppingClock(step=STALE_AFTER + 1.0)

        tool = DescribeScreenTool(manager_for(clock=clock))

        result = executor_for(tool).execute("describe_screen")

        assert not result.ok
        assert "old rather than from looking now" in result.error
