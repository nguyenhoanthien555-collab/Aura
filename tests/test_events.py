"""
Event bus tests.

The bus is the seam every other subsystem hangs off, so its guarantees
are tested directly rather than only through the systems that rely on
them:

    - a handler registered on a base class receives subclasses
    - one broken handler does not stop delivery to the rest
    - subscribing or publishing from inside a handler is safe
"""

import dataclasses
import logging

import pytest

from events.bus import EventBus
from events.types import (
    AuraState,
    BlinkEvent,
    ErrorEvent,
    Event,
    ListeningEvent,
    ResponseEvent,
    SpeakingEvent,
    StateChangedEvent,
    StreamChunkEvent,
    StreamFinishedEvent,
    ThinkingEvent,
    ToolInvokedEvent,
    UserInputEvent,
    VisionUpdateEvent,
)


# ----------------------------------------------------------------------
# Dispatch
# ----------------------------------------------------------------------

def test_handler_receives_its_event():
    bus = EventBus()
    seen = []

    bus.subscribe(ResponseEvent, seen.append)

    bus.publish(ResponseEvent(text="hi"))

    assert [event.text for event in seen] == ["hi"]


def test_handler_does_not_receive_other_events():
    bus = EventBus()
    seen = []

    bus.subscribe(ResponseEvent, seen.append)

    bus.publish(ThinkingEvent())
    bus.publish(UserInputEvent(text="hello"))

    assert seen == []


def test_subscribing_to_base_event_catches_every_subclass():
    """This is what the avatar state machine relies on."""

    bus = EventBus()
    seen = []

    bus.subscribe(Event, seen.append)

    bus.publish(UserInputEvent(text="hello"))
    bus.publish(ThinkingEvent())
    bus.publish(ResponseEvent(text="hi"))
    bus.publish(VisionUpdateEvent(source="screen", description="editing"))

    assert len(seen) == 4


def test_subscribe_all_receives_everything():
    bus = EventBus()
    seen = []

    bus.subscribe_all(seen.append)

    bus.publish(ThinkingEvent())
    bus.publish(ErrorEvent(message="boom"))

    assert len(seen) == 2


def test_delivery_is_in_subscription_order():
    bus = EventBus()
    order = []

    bus.subscribe(ResponseEvent, lambda _event: order.append("first"))
    bus.subscribe(ResponseEvent, lambda _event: order.append("second"))

    bus.publish(ResponseEvent(text="hi"))

    assert order == ["first", "second"]


# ----------------------------------------------------------------------
# Isolation
# ----------------------------------------------------------------------

def test_failing_handler_does_not_stop_the_others():
    """A crashing avatar must not take down the conversation."""

    bus = EventBus()
    survived = []

    def explode(_event):
        raise RuntimeError("renderer died")

    bus.subscribe(ResponseEvent, explode)
    bus.subscribe(ResponseEvent, survived.append)

    bus.publish(ResponseEvent(text="hi"))

    assert len(survived) == 1


def test_publish_never_raises():
    bus = EventBus()

    def explode(_event):
        raise RuntimeError("boom")

    bus.subscribe(Event, explode)

    bus.publish(ThinkingEvent())          # must not raise


# ----------------------------------------------------------------------
# Re-entrancy
# ----------------------------------------------------------------------

def test_subscribing_during_publish_does_not_affect_that_publish():
    bus = EventBus()
    late = []

    def add_handler(_event):
        bus.subscribe(ResponseEvent, late.append)

    bus.subscribe(ResponseEvent, add_handler)

    bus.publish(ResponseEvent(text="one"))
    assert late == []

    bus.publish(ResponseEvent(text="two"))
    assert [event.text for event in late] == ["two"]


def test_publishing_from_inside_a_handler_works():
    """
    The real path: TTS answers a ResponseEvent by publishing
    SpeakingEvent, and the avatar is listening to both.
    """

    bus = EventBus()
    seen = []

    def on_response(_event):
        bus.publish(SpeakingEvent(text="hi", active=True))

    bus.subscribe(ResponseEvent, on_response)
    bus.subscribe(Event, seen.append)

    bus.publish(ResponseEvent(text="hi"))

    kinds = [type(event) for event in seen]

    assert ResponseEvent in kinds
    assert SpeakingEvent in kinds


# ----------------------------------------------------------------------
# Unsubscribe
# ----------------------------------------------------------------------

def test_subscribe_returns_working_unsubscribe():
    bus = EventBus()
    seen = []

    unsubscribe = bus.subscribe(ResponseEvent, seen.append)

    bus.publish(ResponseEvent(text="one"))
    unsubscribe()
    bus.publish(ResponseEvent(text="two"))

    assert [event.text for event in seen] == ["one"]


def test_subscribe_all_returns_working_unsubscribe():
    bus = EventBus()
    seen = []

    unsubscribe = bus.subscribe_all(seen.append)

    bus.publish(ThinkingEvent())
    unsubscribe()
    bus.publish(ThinkingEvent())

    assert len(seen) == 1


def test_clear_removes_every_handler():
    bus = EventBus()

    bus.subscribe(ResponseEvent, lambda _event: None)
    bus.subscribe_all(lambda _event: None)

    bus.clear()

    assert bus.handler_count() == 0


def test_handler_count_is_per_type():
    bus = EventBus()

    bus.subscribe(ResponseEvent, lambda _event: None)
    bus.subscribe(ResponseEvent, lambda _event: None)
    bus.subscribe(ThinkingEvent, lambda _event: None)

    assert bus.handler_count(ResponseEvent) == 2
    assert bus.handler_count(ThinkingEvent) == 1
    assert bus.handler_count() == 3


# ----------------------------------------------------------------------
# Event payloads
# ----------------------------------------------------------------------

def test_events_are_frozen():
    """Events are facts. A subscriber must not be able to edit one."""

    event = ResponseEvent(text="hi")

    with pytest.raises(dataclasses.FrozenInstanceError):
        event.text = "changed"


def test_listening_and_speaking_carry_an_active_flag():
    assert ListeningEvent().active is True
    assert ListeningEvent(active=False).active is False
    assert SpeakingEvent(text="hi").active is True


def test_state_changed_event_carries_an_aura_state():
    event = StateChangedEvent(state=AuraState.THINKING)

    assert event.state is AuraState.THINKING
    assert event.state.value == "thinking"


def test_user_input_event_records_its_source():
    assert UserInputEvent(text="hi").source == "text"
    assert UserInputEvent(text="hi", source="voice").source == "voice"


# ----------------------------------------------------------------------
# The bus gets an observer (section 18)
#
# `subscribe_all` had no production caller, and two docstrings said it
# did: `events/__init__.py` claims "Avatar, voice, and logging
# subscribe", and `subscribe_all` itself says "Used by logging and by the
# avatar debug overlay". Neither existed, so nothing on the bus was ever
# observable. When a proactive notification fired and never reached the
# phone there was no way to tell whether it was published at all,
# dropped by the outbox age limit, or drained by another device.
#
# The constraint that shapes the logger is section 30: keys must never
# appear in normal logs, and by the same argument neither must the
# owner's conversation. So the rule is default-deny on strings - a field
# is redacted to its length unless its event opts it in - while numbers,
# bools and enums log as themselves, because a count is not a secret.
# ----------------------------------------------------------------------

def attached_logger(bus):
    """An EventLogger already listening to `bus`."""

    from events.log import EventLogger

    watcher = EventLogger()
    watcher.attach(bus)
    return watcher


def test_the_logger_names_every_event_it_sees(caplog):
    bus = EventBus()
    attached_logger(bus)

    with caplog.at_level(logging.DEBUG, logger="Aura"):
        bus.publish(ThinkingEvent())

    assert "ThinkingEvent" in caplog.text


def test_the_logger_sees_events_it_was_never_told_about(caplog):
    """
    The point of `subscribe_all`. A new event type must show up in the
    log without the logger being taught about it, or the logger becomes a
    second registry that drifts from `events/types.py`.
    """

    @dataclasses.dataclass(frozen=True)
    class SomethingNobodyPlanned(Event):
        pass

    bus = EventBus()
    attached_logger(bus)

    with caplog.at_level(logging.DEBUG, logger="Aura"):
        bus.publish(SomethingNobodyPlanned())

    assert "SomethingNobodyPlanned" in caplog.text


def test_what_the_owner_said_is_not_written_to_the_log(caplog):
    """
    Section 30, one step further than keys.

    `UserInputEvent.text` is the owner's own sentence. A logger that
    printed payloads would copy every conversation into a log file, and
    the moment the owner pastes a key into chat it would copy that too.
    Default-deny is the only rule that holds under both.
    """

    bus = EventBus()
    attached_logger(bus)

    with caplog.at_level(logging.DEBUG, logger="Aura"):
        bus.publish(UserInputEvent(text="my key is sk-do-not-log-this"))

    assert "UserInputEvent" in caplog.text
    assert "sk-do-not-log-this" not in caplog.text


def test_a_redacted_string_still_reports_its_length(caplog):
    """
    Redacted is not invisible. "a reply arrived, 21 characters" is what
    makes an empty reply distinguishable from no reply at all, and a
    length leaks nothing.
    """

    bus = EventBus()
    attached_logger(bus)

    with caplog.at_level(logging.DEBUG, logger="Aura"):
        bus.publish(ResponseEvent(text="0123456789"))

    assert "10" in caplog.text


def test_counts_and_flags_are_logged_as_themselves(caplog):
    """A number is not a secret, so it is not redacted."""

    bus = EventBus()
    attached_logger(bus)

    with caplog.at_level(logging.DEBUG, logger="Aura"):
        bus.publish(StreamFinishedEvent(text="hi", ok=False, chunks=7))

    assert "ok=False" in caplog.text
    assert "chunks=7" in caplog.text


def test_an_enum_is_logged_even_though_it_is_a_string(caplog):
    """
    The trap in the redaction rule.

    `AuraState` is declared `class AuraState(str, Enum)`, so an
    `isinstance(value, str)` test catches it and would redact
    `state=thinking` - the single most useful thing on the bus - down to
    a character count. Enums are a fixed vocabulary written in the
    source, so they are checked before strings.
    """

    bus = EventBus()
    attached_logger(bus)

    with caplog.at_level(logging.DEBUG, logger="Aura"):
        bus.publish(StateChangedEvent(state=AuraState.THINKING))

    assert "thinking" in caplog.text


def test_an_event_can_opt_a_field_in(caplog):
    """
    Not every string is private. A tool name is written in Aura's own
    source, and a log that cannot say *which* tool ran is not worth
    reading. `log_fields` is the opt-in, and absence means redacted.
    """

    bus = EventBus()
    attached_logger(bus)

    with caplog.at_level(logging.DEBUG, logger="Aura"):
        bus.publish(ToolInvokedEvent(name="current_time"))

    assert "current_time" in caplog.text


def test_tool_arguments_are_not_opted_in(caplog):
    """
    The other half of the same event. A tool's *name* is Aura's; its
    *arguments* were chosen by a model out of the owner's conversation,
    so they stay out.
    """

    bus = EventBus()
    attached_logger(bus)

    with caplog.at_level(logging.DEBUG, logger="Aura"):
        bus.publish(
            ToolInvokedEvent(
                name="write_file",
                arguments={"path": "C:/secret/place.txt"},
            )
        )

    assert "write_file" in caplog.text
    assert "secret" not in caplog.text


def test_a_failure_is_louder_than_a_step(caplog):
    """
    One level for everything would make the log either a flood at INFO
    or silent about errors at DEBUG. An ErrorEvent is the one thing worth
    seeing without turning debug on.
    """

    bus = EventBus()
    attached_logger(bus)

    with caplog.at_level(logging.WARNING, logger="Aura"):
        bus.publish(ThinkingEvent())
        bus.publish(ErrorEvent(message="provider refused", source="llm"))

    assert "ErrorEvent" in caplog.text
    assert "ThinkingEvent" not in caplog.text


def test_an_error_message_is_opted_in(caplog):
    """
    An error nobody can read is not worth raising. Provider error text is
    Aura's own diagnostic, and section 29 already requires the taxonomy
    to carry a reason without leaking secrets - the redaction happens
    where the message is built, not here.
    """

    bus = EventBus()
    attached_logger(bus)

    with caplog.at_level(logging.WARNING, logger="Aura"):
        bus.publish(ErrorEvent(message="provider refused", source="llm"))

    assert "provider refused" in caplog.text


def test_the_logger_can_be_detached(caplog):
    """
    Attaching returns a release, like every other subscriber here
    (`AvatarStateMachine.attach`, `MoodTracker`, `TTSEngine`). A test or
    a plugin that attaches must be able to stop.
    """

    bus = EventBus()
    watcher = attached_logger(bus)

    watcher.detach()

    with caplog.at_level(logging.DEBUG, logger="Aura"):
        bus.publish(ThinkingEvent())

    assert "ThinkingEvent" not in caplog.text


def test_a_logger_that_cannot_read_an_event_says_so_and_survives(caplog):
    """
    The bus already swallows handler errors, so a logger that raised
    would only show up as "Event handler failed" once per event - noise
    that hides whatever was being debugged. It reports the type it could
    not read and moves on.
    """

    @dataclasses.dataclass(frozen=True)
    class Hostile(Event):
        boom: str = "x"

        def __getattribute__(self, name):
            if name == "boom":
                raise RuntimeError("no")
            return object.__getattribute__(self, name)

    bus = EventBus()
    attached_logger(bus)

    with caplog.at_level(logging.DEBUG, logger="Aura"):
        bus.publish(Hostile())

    # Both halves, because the first passed on its own with the failure
    # path never entered. A plain `class Hostile(Event)` inherits an
    # *empty* `__dataclass_fields__` from its frozen base, so `describe`
    # returned "" before reading anything and the hostile attribute was
    # never touched. Asserting the reason proves the except branch ran.
    assert "Hostile" in caplog.text
    assert "unreadable" in caplog.text


def test_a_blink_does_not_fill_the_log(caplog):
    """
    The event that would make the log useless when nothing is happening.

    `BlinkEvent` is an idle-liveliness cue on a timer: it keeps firing
    while Aura sits doing nothing, so a logger that wrote every one would
    bury a real trace under blinks recorded overnight. Nothing is learned
    from the thousandth blink that was not learned from the first.
    """

    bus = EventBus()
    attached_logger(bus)

    with caplog.at_level(logging.DEBUG, logger="Aura"):
        bus.publish(BlinkEvent())

    assert "BlinkEvent" not in caplog.text


def test_a_stream_chunk_does_not_fill_the_log(caplog):
    """
    The event that would drown a single reply.

    One `StreamChunkEvent` per fragment means a 300 token answer costs
    300 lines, and the count is already reported once - and accurately -
    by `StreamFinishedEvent.chunks`. Excluding it loses nothing and keeps
    a turn readable.
    """

    bus = EventBus()
    attached_logger(bus)

    with caplog.at_level(logging.DEBUG, logger="Aura"):
        bus.publish(StreamChunkEvent(text="lo", index=3))
        bus.publish(StreamFinishedEvent(text="lofi", ok=True, chunks=2))

    assert "StreamChunkEvent" not in caplog.text
    assert "chunks=2" in caplog.text


def test_the_exclusions_are_by_type_not_by_name(caplog):
    """
    A subclass of an excluded event is excluded too.

    Matching on `type(event).__name__` would let a renamed or wrapped
    blink back in, and the reason for excluding it - it fires on a timer
    forever - is inherited along with the behaviour.
    """

    @dataclasses.dataclass(frozen=True)
    class DoubleBlink(BlinkEvent):
        pass

    bus = EventBus()
    attached_logger(bus)

    with caplog.at_level(logging.DEBUG, logger="Aura"):
        bus.publish(DoubleBlink())

    assert "DoubleBlink" not in caplog.text


# ----------------------------------------------------------------------
# The wiring, not the class (section 18)
#
# `EventLogger` being correct is worth nothing if the one bus the process
# actually runs on has nobody listening. That is exactly the mutation
# that survived the whole suite in phase 11 part 2 - a correct component
# with an unwired composition root - and the lesson was to test the root.
# ----------------------------------------------------------------------

def test_the_composition_root_attaches_an_observer():
    from launcher.services import build_services

    services = build_services(
        config={
            "llm": {"provider": "mock"},
            "memory": {"recall": False, "profile": False},
            "voice": {"enabled": False},
            "vision": {"enabled": False},
            "tools": {"enabled": False},
        }
    )

    assert services.event_log is not None
    assert services.bus.handler_count() > 0


def test_the_attached_observer_writes_what_crosses_the_real_bus(caplog):
    """
    One step past "an object exists": publish onto the bus the root built
    and check the line comes out. A logger attached to a *different* bus
    would satisfy the test above and nothing else.
    """

    from launcher.services import build_services

    services = build_services(
        config={
            "llm": {"provider": "mock"},
            "memory": {"recall": False, "profile": False},
            "voice": {"enabled": False},
            "vision": {"enabled": False},
            "tools": {"enabled": False},
        }
    )

    with caplog.at_level(logging.DEBUG, logger="Aura"):
        services.bus.publish(ErrorEvent(message="wired", source="test"))

    assert "ErrorEvent" in caplog.text
    assert "wired" in caplog.text
