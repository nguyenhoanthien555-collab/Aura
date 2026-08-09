"""
Mood.

Aura's mood is how she feels; AuraState is what she is doing. They are
separate on purpose - she can be thinking and curious at the same time,
and an avatar wants both: one chooses the animation, the other chooses
the expression.

Nothing here infers anything. There is no sentiment analysis, no
classifier, no heuristic reading of the user's tone. Mood is set
explicitly or it stays NEUTRAL. That is the whole implementation, and it
is the point: when something later does decide mood - an explicit
command, a model call, a time of day rule - it sets it through this and
every subscriber already works.

    tracker.set(Mood.CURIOUS, reason="user asked about shaders")
        -> MoodChangedEvent -> avatar, overlay, logs

The tracker knows nothing about faces, sprites or voices.

Mood reaches four places, and each one reads it for itself:

    reply style       brain/mood.py       this file, MOOD_HINTS
    speech pacing     voice/tts/pacing    rate and pitch offsets
    expression        avatar/expression   which face
    idle animation    avatar/animation    the renderer's own business

There is deliberately no shared MoodProfile object carrying all four.
That object would have to know about prompts, audio and sprites at once,
which is exactly the coupling the architecture forbids - the brain would
import a field meant for a renderer. A mood is a single enum value on the
bus, and each layer translates it into its own vocabulary. Adding a fifth
consumer means adding a table in that consumer, and touching nothing
here.
"""

from brain.ports import EventPublisher
from events.types import Mood, MoodChangedEvent


class MoodTracker:
    """
    Holds the current mood and announces changes.

    A no-op transition publishes nothing, for the same reason
    AvatarStateMachine suppresses one: a subscriber should be able to
    treat every event it receives as a real change.

    It can also follow a bus, so that a mood set anywhere - a command, a
    plugin, a future rule - is reflected here without that code holding a
    reference to this object.
    """

    def __init__(
        self,
        mood: Mood = Mood.NEUTRAL,
        events: EventPublisher | None = None,
    ):

        self._mood = mood
        self.events = events

        self._listeners: list = []
        self._release = None

    @property
    def mood(self) -> Mood:
        return self._mood

    @property
    def hint(self) -> str:
        """
        How this mood should colour the next reply.

        Read by the prompt builder. Empty for NEUTRAL, which is the
        default and needs no instruction.
        """

        return MOOD_HINTS.get(self._mood, "")

    def set(self, mood: Mood, reason: str = "") -> bool:
        """
        Move to a new mood. Returns True when it actually changed.

        An unknown value is refused rather than stored, so a subscriber
        can rely on `event.mood` always being a Mood.
        """

        if not isinstance(mood, Mood):
            return False

        if mood is self._mood:
            return False

        self._mood = mood

        self._notify(mood)

        self._emit(MoodChangedEvent(mood=mood, reason=reason))

        return True

    def reset(self, reason: str = "") -> bool:
        """Back to neutral."""

        return self.set(Mood.NEUTRAL, reason=reason)

    # ------------------------------------------------------------------
    # Following the bus
    # ------------------------------------------------------------------

    def attach(self, bus) -> "MoodTracker":
        """
        Adopt the mood from MoodChangedEvent published by anything else.

        `set` is not called here - that would re-publish the event it just
        received and, on a synchronous bus, do so from inside its own
        dispatch. The value is stored directly and listeners are told.
        """

        self.events = self.events or bus

        self._release = bus.subscribe(MoodChangedEvent, self._adopt)

        return self

    def detach(self) -> None:

        if self._release is None:
            return

        try:
            self._release()
        except Exception:
            pass

        self._release = None

    def on_change(self, listener):
        """Call `listener(mood)` on every real change. Returns a remover."""

        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def _adopt(self, event: MoodChangedEvent) -> None:

        mood = getattr(event, "mood", None)

        if not isinstance(mood, Mood) or mood is self._mood:
            return

        self._mood = mood

        self._notify(mood)

    def _notify(self, mood: Mood) -> None:

        for listener in list(self._listeners):
            try:
                listener(mood)
            except Exception:
                # A broken subscriber must not cost the mood change.
                pass

    def _emit(self, event) -> None:

        if self.events is None:
            return

        try:
            self.events.publish(event)
        except Exception:
            pass

    def __repr__(self) -> str:
        return f"MoodTracker({self._mood.value})"


def parse_mood(name: str, fallback: Mood = Mood.NEUTRAL) -> Mood:
    """
    Read a mood out of a string, tolerantly.

    For config files and future commands. An unrecognised name falls back
    rather than raising - a typo in a mood is not worth a crash.
    """

    text = (name or "").strip().lower()

    for mood in Mood:
        if mood.value == text:
            return mood

    return fallback


# ----------------------------------------------------------------------
# Mood as reply style
# ----------------------------------------------------------------------

# The invariant, restated wherever a mood hint travels.
#
# The base style hint already says it, but that hint is replaceable from
# config. A mood line that reached the model without this clause attached
# would be the one place where "sound sleepy" could be read as licence to
# be vague about a version number.
ACCURACY = (
    "Mood changes tone and pacing only. Facts, code, file paths, "
    "commands and version numbers stay exactly as they would be in any "
    "other mood."
)

# What each mood does to her writing.
#
# Every line is about rhythm, length and warmth - never about content,
# certainty or willingness to answer. A mood must not be able to make her
# hedge, refuse, or skip a detail she would otherwise give.
#
# NEUTRAL is absent on purpose. It is the default voice, personality.md
# already describes it, and repeating it as an instruction would only
# dilute the lines that matter.
MOOD_HINTS: dict[Mood, str] = {
    Mood.HAPPY: (
        "She is in a good mood right now: a little brighter and quicker "
        "than usual, glad about something that worked. Still composed - "
        "warmth in the wording, not exclamation marks."
    ),
    Mood.CURIOUS: (
        "She is curious right now: interested in the thing itself, "
        "thinking out loud at an unhurried pace, and likely to end on a "
        "genuine question about the part she wants to know more about."
    ),
    Mood.FOCUSED: (
        "She is focused right now: tighter and more direct than usual, "
        "fewer asides, straight at the problem. Still warm, just not "
        "chatty."
    ),
    Mood.TEASING: (
        "She is feeling playful right now: light teasing is welcome, the "
        "kind between friends who like each other. Tease the situation "
        "or the bug, never the person, stay kind about it, and help just "
        "as much as always."
    ),
    Mood.SLEEPY: (
        "She is low energy right now: shorter sentences, softer, a "
        "slower rhythm. Still completely coherent - tired, not sloppy."
    ),
}


def mood_hint(mood: Mood) -> str:
    """
    The prompt line for a mood, with the accuracy clause attached.

    Empty for NEUTRAL and for anything unrecognised, so a caller can
    append the result unconditionally.
    """

    hint = MOOD_HINTS.get(mood, "")

    if not hint:
        return ""

    return f"{hint} {ACCURACY}"


def current_mood(source, fallback: Mood = Mood.NEUTRAL) -> Mood:
    """
    Read the mood out of whatever was injected as a mood source.

    Accepts a MoodTracker, any object with a `.mood`, a zero argument
    callable, or a bare Mood. Tolerant on purpose: the styler that calls
    this should not care which of those it was handed, and a source that
    misbehaves must cost a prompt line rather than the reply.
    """

    if source is None:
        return fallback

    if isinstance(source, Mood):
        return source

    try:
        if callable(source):
            value = source()
        else:
            value = getattr(source, "mood", None)
    except Exception:
        return fallback

    return value if isinstance(value, Mood) else fallback


__all__ = [
    "MoodTracker",
    "parse_mood",
    "mood_hint",
    "current_mood",
    "MOOD_HINTS",
    "ACCURACY",
    "Mood",
]
