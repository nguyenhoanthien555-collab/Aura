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

import pytest

from events.bus import EventBus
from events.types import (
    AuraState,
    ErrorEvent,
    Event,
    ListeningEvent,
    ResponseEvent,
    SpeakingEvent,
    StateChangedEvent,
    ThinkingEvent,
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
