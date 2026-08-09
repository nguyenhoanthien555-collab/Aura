"""
Event types.

Every event is a frozen dataclass. Events are facts about something that
already happened - they are never commands, and never carry behaviour.

Rule of thumb for adding an event:
    - name it in the past tense of what occurred
    - keep the payload primitive (str/float/bool/dataclass)
    - never put a live handle (socket, window, session) inside one
"""

from dataclasses import dataclass, field
from enum import Enum


class AuraState(str, Enum):
    """
    High level state of the companion.

    The avatar maps these directly onto animations, which is why the
    values are plain lowercase strings - they can be used as asset keys
    or written to a log without conversion.
    """

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


class Mood(str, Enum):
    """
    How Aura currently feels, independent of what she is doing.

    Orthogonal to AuraState on purpose: she can be THINKING and CURIOUS
    at the same time, and an avatar wants both - one picks the animation,
    the other picks the expression.

    Nothing detects mood yet. It is set explicitly or it stays NEUTRAL.
    The enum exists now so that whatever sets it later does not have to
    change the event, the avatar or the state machine to do it.
    """

    NEUTRAL = "neutral"
    HAPPY = "happy"
    CURIOUS = "curious"
    FOCUSED = "focused"
    TEASING = "teasing"
    SLEEPY = "sleepy"


class Expression(str, Enum):
    """
    What Aura's face is doing.

    A third axis, and the finest grained of the three:

        AuraState   what she is doing      (speaking)
        Mood        how she feels          (teasing)
        Expression  what her face shows    (playful)

    State and mood are both durable - they persist until something
    changes them. An expression is momentary. SURPRISED is not a mood; it
    is a face held for a second and then released back to whatever the
    mood implies. That is why expressions are derived and published by
    the avatar layer rather than tracked in the brain: nothing outside
    the face needs to know about them.

    Values are lowercase strings so they can be used directly as sprite
    keys, Live2D expression ids or VRM blendshape names without a
    conversion table.
    """

    NEUTRAL = "neutral"
    HAPPY = "happy"
    EXCITED = "excited"
    PLAYFUL = "playful"
    TEASING = "teasing"
    THINKING = "thinking"
    FOCUSED = "focused"
    SURPRISED = "surprised"
    CONFUSED = "confused"
    SLEEPY = "sleepy"
    EMBARRASSED = "embarrassed"


@dataclass(frozen=True)
class Event:
    """Base class for all events."""


# ----------------------------------------------------------------------
# Conversation
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class UserInputEvent(Event):
    """The user said or typed something."""

    text: str
    source: str = "text"          # "text" | "voice"


@dataclass(frozen=True)
class ThinkingEvent(Event):
    """A request was handed to the LLM."""


@dataclass(frozen=True)
class ResponseEvent(Event):
    """
    The LLM produced a reply.

    `streamed` marks a reply whose text already went out token by token
    as StreamChunkEvent. Anything that consumed the stream has the full
    text already; anything that did not - a logger, a transcript window -
    still gets it here. TTSEngine uses the flag to avoid speaking a reply
    twice when streaming speech is wired up.
    """

    text: str
    streamed: bool = False


@dataclass(frozen=True)
class StreamStartedEvent(Event):
    """Generation began. No text yet."""


@dataclass(frozen=True)
class StreamChunkEvent(Event):
    """
    One piece of a reply, as it arrives.

    `text` is the new fragment only, never the accumulated reply, so a
    consumer appends rather than replaces. `index` counts fragments from
    zero, which lets a late subscriber notice it missed the beginning.
    """

    text: str
    index: int = 0


@dataclass(frozen=True)
class StreamFinishedEvent(Event):
    """
    Generation ended.

    On success `text` is the finished reply, after styling - which is
    not always the concatenation of the chunks, because the style filter
    needs a whole reply before it can tell an opening filler clause from
    a real sentence. Anything that printed chunks should replace its
    buffer with this. The difference is only ever a deleted filler
    phrase; no fact, path, command or version is altered.

    `ok` is False when the provider failed part way through, in which
    case `text` holds whatever had arrived before it stopped and the
    turn was not saved.
    """

    text: str = ""
    ok: bool = True
    chunks: int = 0


@dataclass(frozen=True)
class ErrorEvent(Event):
    """Something failed. Carries a message, never an exception object."""

    message: str
    source: str = ""


# ----------------------------------------------------------------------
# Voice
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class ListeningEvent(Event):
    """Microphone capture started or stopped."""

    active: bool = True


@dataclass(frozen=True)
class TranscriptEvent(Event):
    """Speech to text produced a transcript."""

    text: str


@dataclass(frozen=True)
class SpeakingEvent(Event):
    """
    Text to speech started or finished.

    Carries enough for an avatar to animate speech without asking the
    voice system anything:

        active      start or stop, so the animation has both edges
        text        what is being said, for a subtitle or a mouth shape
        voice       which voice, so a future avatar can match a face to it
        duration    seconds, when the provider knows before it starts

    `duration` is 0.0 whenever it is unknown, which is the common case -
    a provider that streams audio cannot say in advance. An avatar should
    treat 0.0 as "animate until the stop event arrives" rather than as a
    zero length utterance.

    None of this is required. Aura's own TTSEngine publishes only `text`
    and `active`; the rest exist so that adding them later is filling in
    a field rather than changing an event everyone subscribes to.
    """

    text: str = ""
    active: bool = True
    voice: str = ""
    duration: float = 0.0


# ----------------------------------------------------------------------
# Vision
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class VisionUpdateEvent(Event):
    """A new visual observation is available."""

    source: str
    description: str


# ----------------------------------------------------------------------
# Companion
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class CompanionNotificationEvent(Event):
    """
    Aura decided to say something the user did not ask for.

    Published by companion/, consumed by whatever can reach the user - a
    WebSocket, a push notification, a desktop toast. The engine knows
    nothing about any of them, which is why this carries text and numbers
    rather than a destination.

    `reason` is why she spoke. It is written for a human reading a log or
    asking "why did you say that", and is never parsed.
    """

    message: str
    reason: str = ""
    priority: str = "normal"          # "low" | "normal" | "high"
    confidence: float = 0.0
    source: str = ""                  # app or window that prompted it
    device_id: str = ""


# ----------------------------------------------------------------------
# Avatar
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class StateChangedEvent(Event):
    """The companion moved into a new state."""

    state: AuraState


@dataclass(frozen=True)
class MoodChangedEvent(Event):
    """
    Aura's mood changed.

    Nothing publishes this yet. It is here so that when something does -
    a sentiment pass, an explicit command, a time of day rule - the
    avatar and any overlay can already be listening for it.

    `reason` is free text for logs and debugging. It is never parsed.
    """

    mood: Mood = Mood.NEUTRAL
    reason: str = ""


@dataclass(frozen=True)
class ExpressionChangedEvent(Event):
    """
    Aura's face changed.

    Published by the avatar layer, consumed by renderers. The brain never
    publishes one and never subscribes to one - what her face is doing is
    presentation, and presentation is derived from conversation facts
    rather than commanded by them.

    `hold` is a hint in seconds: how long this expression is worth
    keeping before drifting back to whatever the mood implies. 0.0 means
    "until something else changes it", which is the case for expressions
    derived from a durable mood.
    """

    expression: Expression = Expression.NEUTRAL
    hold: float = 0.0
    reason: str = ""


@dataclass(frozen=True)
class BlinkEvent(Event):
    """
    Time to blink.

    An idle-liveliness cue with no meaning attached: a renderer that has
    a blink animation plays it, one that does not ignores it. Carried on
    the bus rather than run inside a renderer's own timer so that several
    surfaces - a Live2D model and a debug overlay - blink together.
    """

    double: bool = False


# Paired lifecycle events, for animation.
#
# These deliberately do NOT subclass ThinkingEvent or SpeakingEvent.
# Subscribing to a base class catches its subclasses, so a director that
# listens for SpeakingEvent and publishes a subclass of it would receive
# its own output and recurse. Sibling types under Event keep the derived
# layer strictly downstream of the domain layer.

@dataclass(frozen=True)
class ThinkingStartedEvent(Event):
    """Generation began, phrased for an animator rather than a logger."""


@dataclass(frozen=True)
class ThinkingFinishedEvent(Event):
    """Generation ended, successfully or not."""

    ok: bool = True


@dataclass(frozen=True)
class SpeechStartedEvent(Event):
    """
    Audible speech began.

    Everything a mouth needs and nothing more. `duration` is 0.0 when the
    provider cannot say in advance, which is the common case; treat it as
    "animate until the finish event" rather than a zero length utterance.
    """

    text: str = ""
    voice: str = ""
    duration: float = 0.0


@dataclass(frozen=True)
class SpeechFinishedEvent(Event):
    """Audible speech ended. `ok` is False when synthesis failed."""

    ok: bool = True


@dataclass(frozen=True)
class TypingStartedEvent(Event):
    """
    Text began appearing on screen.

    Distinct from speech: a muted Aura still types, and an avatar may
    want a different idle for "writing" than for "talking".
    """


@dataclass(frozen=True)
class TypingFinishedEvent(Event):
    """Text finished appearing."""


# ----------------------------------------------------------------------
# Tools
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class ToolInvokedEvent(Event):
    """A tool was asked to run."""

    name: str
    arguments: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ToolCompletedEvent(Event):
    """A tool finished, successfully or not."""

    name: str
    ok: bool
    detail: str = ""
