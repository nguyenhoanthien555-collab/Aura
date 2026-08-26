"""
Task classes, and the one function that lets a model be chosen per task.

AURA answers several different kinds of question through the same
pipeline: prose to a person, a JSON action for a device agent, a single
routing word, a code fix, a long transcript to summarise. Those are not
the same job, and the owner may reasonably want a different worker on
each.

What this module deliberately does NOT do is let that choice leak
upward. `classify_task` is pure - it reads its arguments and nothing else,
no configuration, no environment, no clock - so the same turn always
lands in the same lane. And `generate_for` degrades to plain `generate`
when the object it is handed knows nothing about tasks, which is what
keeps every existing provider, mock and test fake working unedited.

The cue tuples come from `brain.persona`. They are imported rather than
restated: two copies of "what counts as a technical message" that drifted
apart is how a classifier starts disagreeing with the style engine about
the same sentence.
"""

from enum import Enum
from typing import Protocol, Sequence, runtime_checkable

from brain.agent_mode import is_agent_tick, is_intent_probe
from brain.persona import FAILURE_CUES, TECHNICAL_CUES


class TaskClass(str, Enum):
    """
    What kind of work a turn is.

    `str`-valued because lane configuration arrives as JSON from a phone;
    comparing a stored `"coding"` against `TaskClass.CODING` has to be
    true without the caller remembering to convert.
    """

    CHAT = "chat"
    REASONING = "reasoning"
    CODING = "coding"
    VISION = "vision"
    TOOL_PLANNING = "tool_planning"
    FAST_RESPONSE = "fast_response"
    LONG_CONTEXT = "long_context"
    EMBEDDING = "embedding"
    FALLBACK = "fallback"

    @classmethod
    def coerce(cls, value) -> "TaskClass":
        """
        The task a caller meant, or CHAT.

        Anything unrecognised is CHAT rather than an error: a lane name
        from a settings file that this build does not know about should
        cost the owner a default, not a crash.
        """

        if isinstance(value, cls):
            return value

        try:
            return cls(str(value).strip().lower())
        except (ValueError, AttributeError):
            return cls.CHAT


# Above this many characters of prompt material, the binding constraint on
# the turn is context length rather than subject matter - so it outranks
# the lexical classes below it. Counted over history plus the message,
# because that is what will actually be sent.
LONG_CONTEXT_CHARS = 8000


def classify_task(
    user_message: str | None,
    context: dict | None = None,
    history: Sequence[str] | None = None,
) -> TaskClass:
    """
    Which lane this turn belongs in.

    Ordered by how much the answer would be *wrong* if the class were
    ignored, not by how specific the signal is:

    1. Machine turns first, and unconditionally. An agent tick's reply is
       parsed as JSON by an Android service. Routing it to a worker chosen
       for long prose because it happened to carry a large accessibility
       tree would break the parse - the size of a machine turn says
       nothing about what kind of answer it needs.
    2. Length next. A transcript that will not fit is a fact about the
       request that no amount of subject matter changes.
    3. Then subject matter, and only from words that are actually present.
       Nothing is inferred from a cue's absence; the fallthrough is CHAT.

    Failure cues alone mean REASONING - something is wrong and needs
    thinking about. Failure cues *with* technical cues mean CODING: a
    traceback in a file full of gradle and python is a code problem, and
    the coding worker is the better one to hand it to.
    """

    if is_agent_tick(context):
        return TaskClass.TOOL_PLANNING

    if is_intent_probe(context):
        return TaskClass.FAST_RESPONSE

    text = (user_message or "").strip()
    lowered = text.lower()

    spent = len(text) + sum(len(str(item)) for item in (history or ()))
    if spent > LONG_CONTEXT_CHARS:
        return TaskClass.LONG_CONTEXT

    technical = any(cue in lowered for cue in TECHNICAL_CUES)
    failing = any(cue in lowered for cue in FAILURE_CUES)

    if technical:
        return TaskClass.CODING

    if failing:
        return TaskClass.REASONING

    return TaskClass.CHAT


@runtime_checkable
class CapabilityLLM(Protocol):
    """
    An LLM that can be asked for a particular kind of work.

    A separate protocol rather than a method on `brain.ports.LLM`, and
    that is not a style choice: `LLM` is `@runtime_checkable`, so adding a
    method to it would make `isinstance(provider, LLM)` false for every
    provider in the tree at once. `brain.streaming.StreamingLLM` exists
    for exactly the same reason, and `generate_for` below is the
    counterpart of `stream_of`.
    """

    def generate_for(self, prompt: str, task: str) -> str:
        ...


def generate_for(llm, prompt: str, task=None) -> str:
    """
    Ask `llm` for `task`, or just ask it, if that is all it can do.

    The degradation is the point. Every provider in `brain/providers/`,
    `MockProvider`, a bare `BrainRouter` and every fake in the test suite
    implement `generate` and nothing else; each of them stays a valid
    argument here, and injecting one is indistinguishable from the
    capability layer not being wired up.
    """

    if task is not None:
        lane_aware = getattr(llm, "generate_for", None)
        if callable(lane_aware):
            return lane_aware(prompt, task)

    return llm.generate(prompt)
