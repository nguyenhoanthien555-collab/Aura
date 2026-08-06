"""
Avatar tests.

No window is ever opened. NullRenderer records the states it was told to
draw, which is exactly what is needed to assert the required flow:

    event  ->  AvatarStateMachine  ->  renderer

The avatar contains no AI logic, so there is nothing here about
conversation content - only about states. That absence is the point.
"""

import pytest

from avatar.controller import AvatarController, create_renderer
from avatar.renderer import NullRenderer
from avatar.state import AvatarStateMachine

from events.bus import EventBus
from events.types import (
    AuraState,
    ErrorEvent,
    ListeningEvent,
    ResponseEvent,
    SpeakingEvent,
    StateChangedEvent,
    ThinkingEvent,
    UserInputEvent,
    VisionUpdateEvent,
)


@pytest.fixture
def bus():
    return EventBus()


@pytest.fixture
def machine(bus):
    """A machine attached to a bus, not publishing derived state back."""

    machine = AvatarStateMachine(publish=False)
    machine.attach(bus)
    return machine


# ----------------------------------------------------------------------
# Derivation
# ----------------------------------------------------------------------

def test_starts_idle():
    assert AvatarStateMachine().state is AuraState.IDLE


@pytest.mark.parametrize(
    "event, expected",
    [
        (ListeningEvent(active=True), AuraState.LISTENING),
        (ThinkingEvent(), AuraState.THINKING),
        (SpeakingEvent(text="hi", active=True), AuraState.SPEAKING),
        (ResponseEvent(text="hi"), AuraState.IDLE),
        (ErrorEvent(message="boom"), AuraState.IDLE),
    ],
)
def test_events_map_onto_states(bus, machine, event, expected):
    # Move off IDLE first so the IDLE cases are real transitions.
    bus.publish(ThinkingEvent())

    bus.publish(event)

    assert machine.state is expected


def test_listening_stop_returns_to_idle(bus, machine):
    bus.publish(ListeningEvent(active=True))
    bus.publish(ListeningEvent(active=False))

    assert machine.state is AuraState.IDLE


def test_speaking_stop_returns_to_idle(bus, machine):
    bus.publish(SpeakingEvent(text="hi", active=True))
    bus.publish(SpeakingEvent(active=False))

    assert machine.state is AuraState.IDLE


def test_unrelated_events_leave_the_state_alone(bus, machine):
    bus.publish(ThinkingEvent())

    bus.publish(UserInputEvent(text="hello"))
    bus.publish(VisionUpdateEvent(source="screen", description="editing"))

    assert machine.state is AuraState.THINKING


def test_a_full_turn_ends_idle(bus, machine):
    bus.publish(UserInputEvent(text="hello"))
    bus.publish(ThinkingEvent())
    bus.publish(ResponseEvent(text="hi bro"))

    assert machine.state is AuraState.IDLE


# ----------------------------------------------------------------------
# Notification
# ----------------------------------------------------------------------

def test_listeners_are_told_about_changes(bus, machine):
    seen = []
    machine.on_change(seen.append)

    bus.publish(ThinkingEvent())
    bus.publish(ResponseEvent(text="hi"))

    assert seen == [AuraState.THINKING, AuraState.IDLE]


def test_repeat_states_are_suppressed(bus, machine):
    """Otherwise a talkative bus restarts the idle animation all turn."""

    seen = []
    machine.on_change(seen.append)

    bus.publish(ThinkingEvent())
    bus.publish(ThinkingEvent())
    bus.publish(ThinkingEvent())

    assert seen == [AuraState.THINKING]


def test_a_broken_listener_does_not_break_state_tracking(bus, machine):
    def explode(_state):
        raise RuntimeError("renderer died")

    machine.on_change(explode)

    bus.publish(ThinkingEvent())

    assert machine.state is AuraState.THINKING


def test_on_change_returns_a_working_remove(bus, machine):
    seen = []
    remove = machine.on_change(seen.append)

    bus.publish(ThinkingEvent())
    remove()
    bus.publish(ResponseEvent(text="hi"))

    assert seen == [AuraState.THINKING]


def test_publishing_machine_announces_state_changes(bus):
    machine = AvatarStateMachine(publish=True)
    machine.attach(bus)

    seen = []
    bus.subscribe(StateChangedEvent, seen.append)

    bus.publish(ThinkingEvent())

    assert [event.state for event in seen] == [AuraState.THINKING]


def test_state_changed_events_do_not_feed_back(bus):
    """A machine that reacted to its own announcement would not settle."""

    machine = AvatarStateMachine(publish=True)
    machine.attach(bus)

    bus.publish(ThinkingEvent())

    assert machine.state is AuraState.THINKING


# ----------------------------------------------------------------------
# Renderer
# ----------------------------------------------------------------------

def test_null_renderer_records_what_it_was_told_to_draw():
    renderer = NullRenderer()

    renderer.set_state(AuraState.THINKING)
    renderer.set_state(AuraState.IDLE)

    assert renderer.states == [AuraState.THINKING, AuraState.IDLE]
    assert renderer.state is AuraState.IDLE


def test_null_renderer_tracks_visibility():
    renderer = NullRenderer()

    renderer.show()
    assert renderer.visible is True

    renderer.hide()
    assert renderer.visible is False

    renderer.close()
    assert renderer.closed is True


def test_disabled_avatar_renders_to_nothing():
    """No display, no Tk import, no crash."""

    assert isinstance(create_renderer(enabled=False), NullRenderer)


# ----------------------------------------------------------------------
# Controller: the required event -> avatar state flow
# ----------------------------------------------------------------------

def test_event_changes_the_avatar_state(bus):
    renderer = NullRenderer()

    controller = AvatarController(renderer=renderer)
    controller.attach(bus)

    bus.publish(ThinkingEvent())

    assert controller.state is AuraState.THINKING
    assert renderer.states == [AuraState.THINKING]


def test_a_whole_turn_drives_the_renderer(bus):
    renderer = NullRenderer()

    controller = AvatarController(renderer=renderer)
    controller.attach(bus)

    bus.publish(ListeningEvent(active=True))
    bus.publish(ListeningEvent(active=False))
    bus.publish(ThinkingEvent())
    bus.publish(SpeakingEvent(text="hi bro", active=True))
    bus.publish(SpeakingEvent(active=False))

    assert renderer.states == [
        AuraState.LISTENING,
        AuraState.IDLE,
        AuraState.THINKING,
        AuraState.SPEAKING,
        AuraState.IDLE,
    ]


def test_detach_stops_the_avatar_following_the_bus(bus):
    renderer = NullRenderer()

    controller = AvatarController(renderer=renderer)
    controller.attach(bus)

    bus.publish(ThinkingEvent())
    controller.detach()
    bus.publish(SpeakingEvent(text="hi", active=True))

    assert renderer.states == [AuraState.THINKING]


def test_start_shows_the_renderer_in_its_current_state(bus):
    renderer = NullRenderer()

    controller = AvatarController(renderer=renderer)
    controller.attach(bus)
    controller.start()

    assert renderer.visible is True
    assert renderer.state is AuraState.IDLE


def test_stop_closes_and_detaches(bus):
    renderer = NullRenderer()

    controller = AvatarController(renderer=renderer)
    controller.attach(bus)
    controller.stop()

    bus.publish(ThinkingEvent())

    assert renderer.closed is True
    assert renderer.states == []


def test_a_broken_renderer_cannot_break_the_bus(bus):
    class BrokenRenderer:
        def set_state(self, state):
            raise RuntimeError("window is gone")

        def show(self):
            raise RuntimeError("window is gone")

        def hide(self):
            pass

        def close(self):
            raise RuntimeError("window is gone")

    controller = AvatarController(renderer=BrokenRenderer())
    controller.attach(bus)

    bus.publish(ThinkingEvent())          # must not raise
    controller.start()                    # must not raise
    controller.stop()                     # must not raise

    assert controller.state is AuraState.THINKING


def test_controller_run_returns_immediately_without_a_gui():
    """A headless run needs no special case."""

    AvatarController(renderer=NullRenderer()).run()
