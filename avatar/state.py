"""
Avatar state machine.

Single owner of "what state is Aura in". Everything else - the brain,
the voice engines - publishes facts about what happened; this is the one
place that decides what those facts mean for the face on screen.

    ListeningEvent(active=True)   ->  LISTENING
    ThinkingEvent                 ->  THINKING
    SpeakingEvent(active=True)    ->  SPEAKING
    ResponseEvent / ErrorEvent    ->  IDLE

Mood is tracked alongside state but never derived from these events.
Nothing infers it, here or anywhere else - the machine only follows
MoodChangedEvent if something publishes one.

The machine holds no window and draws nothing, so it is testable on its
own with no display attached.
"""

from typing import Callable

from events.types import (
    AuraState,
    ErrorEvent,
    Event,
    ListeningEvent,
    Mood,
    MoodChangedEvent,
    ResponseEvent,
    SpeakingEvent,
    StateChangedEvent,
    ThinkingEvent,
)


Listener = Callable[[AuraState], None]
MoodListener = Callable[[Mood], None]


class AvatarStateMachine:

    def __init__(self, events=None, publish: bool = True):
        """
        `events` is used both to subscribe and to announce. Set
        `publish` to False to derive state without putting
        StateChangedEvent back on the bus.
        """

        self.events = events
        self.publish = publish

        self.state = AuraState.IDLE

        # Mood is tracked, never derived. Nothing publishes
        # MoodChangedEvent yet; when something does, this already
        # follows it. See brain/mood.py.
        self.mood = Mood.NEUTRAL

        self._listeners: list[Listener] = []
        self._mood_listeners: list[MoodListener] = []

    # ------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------

    def attach(self, bus) -> Callable[[], None]:
        """
        Derive state from everything on the bus.

        Returns an unsubscribe callable. Subscribing to Event catches
        every subclass, so one subscription covers all of them and the
        machine cannot miss a newly added event type.
        """

        self.events = self.events or bus

        return bus.subscribe(Event, self.handle)

    def on_change(self, listener: Listener) -> Callable[[], None]:
        """
        Call `listener` whenever the state changes.

        This is how a renderer follows the machine without the machine
        knowing what a renderer is.
        """

        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def on_mood_change(self, listener: MoodListener) -> Callable[[], None]:
        """
        Call `listener` whenever the mood changes.

        Separate from `on_change` because the two are independent: mood
        does not move when state does, and a renderer that only animates
        states can ignore this entirely.
        """

        self._mood_listeners.append(listener)

        def remove() -> None:
            if listener in self._mood_listeners:
                self._mood_listeners.remove(listener)

        return remove

    # ------------------------------------------------------------------
    # Transition
    # ------------------------------------------------------------------

    def handle(self, event: Event) -> AuraState:
        """Apply one event and return the resulting state."""

        if isinstance(event, MoodChangedEvent):
            self.set_mood(event.mood)
            return self.state

        next_state = self._derive(event)

        if next_state is not None:
            self.set_state(next_state)

        return self.state

    def set_mood(self, mood: Mood) -> None:
        """
        Record a mood, ignoring no-op changes.

        Publishes nothing. MoodChangedEvent is already on the bus - the
        machine is a subscriber here, not the owner, which is the one
        difference from how it treats state.
        """

        if not isinstance(mood, Mood) or mood == self.mood:
            return

        self.mood = mood

        for listener in list(self._mood_listeners):
            try:
                listener(mood)
            except Exception:
                pass

    def set_state(self, state: AuraState) -> None:
        """
        Move to a state, ignoring no-op transitions.

        Suppressing repeats matters: without it a talkative bus would
        restart the idle animation several times per turn.
        """

        if state == self.state:
            return

        self.state = state

        for listener in list(self._listeners):
            try:
                listener(state)
            except Exception:
                # A broken renderer must not break state tracking.
                pass

        if self.publish and self.events is not None:
            try:
                self.events.publish(StateChangedEvent(state=state))
            except Exception:
                pass

    @staticmethod
    def _derive(event: Event) -> AuraState | None:
        """
        Map an event onto a state, or None to leave the state alone.

        ResponseEvent returns to IDLE because the turn is over. When TTS
        is attached it immediately publishes SpeakingEvent(active=True)
        and the avatar moves to SPEAKING; the intermediate IDLE is not
        rendered because both events are delivered synchronously within
        one publish cycle.
        """

        if isinstance(event, ListeningEvent):
            return AuraState.LISTENING if event.active else AuraState.IDLE

        if isinstance(event, ThinkingEvent):
            return AuraState.THINKING

        if isinstance(event, SpeakingEvent):
            return AuraState.SPEAKING if event.active else AuraState.IDLE

        if isinstance(event, (ResponseEvent, ErrorEvent)):
            return AuraState.IDLE

        # Everything else - transcripts, vision updates, mood - leaves
        # the state where it is. Mood is handled in `handle`, above.
        return None
