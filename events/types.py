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
    """The LLM produced a reply."""

    text: str


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
    """Text to speech started or finished."""

    text: str = ""
    active: bool = True


# ----------------------------------------------------------------------
# Vision
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class VisionUpdateEvent(Event):
    """A new visual observation is available."""

    source: str
    description: str


# ----------------------------------------------------------------------
# Avatar
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class StateChangedEvent(Event):
    """The companion moved into a new state."""

    state: AuraState


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
