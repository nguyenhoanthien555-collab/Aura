"""
What to actually say.

Templates, not a language model call. Three reasons, and the third is
the one that decided it:

  * a proactive message fires when the user is *not* there, so a slow
    provider call has nobody waiting on it and a failed one has nobody
    to apologise to
  * these messages are short and their shape is fixed; an LLM adds
    latency, cost and non-determinism for no gain
  * a template cannot hallucinate. A generated reminder can invent a
    task, and inventing work the user never mentioned is the single
    worst thing this system could do

The composer never writes a fact. Everything specific in an outgoing
message - a task description, a recalled memory - is text that came from
the user, passed through unchanged. Aura supplies the wrapper around it
and nothing else.

Variety is by rotation, not randomness: the index comes from the caller,
so the same context always produces the same message and a test can
assert on it. Rotation exists so a greeting does not read like a
recording; it is not an attempt to sound spontaneous.
"""

from core.temporal import AFTERNOON, EVENING, MORNING, NIGHT
from proactive.decision import Category, ProactiveDecision
from proactive.context import ProactiveContext


GREETINGS = {
    MORNING: (
        "Morning. Anything you want to get moving today?",
        "Hey, morning. What's the plan?",
    ),
    AFTERNOON: (
        "Afternoon. How's it going?",
        "Hey. Back at it?",
    ),
    EVENING: (
        "Evening. How did today end up?",
        "Hey. Winding down, or still going?",
    ),
    NIGHT: (
        "Still up? Don't let me keep you.",
        "Late one. Everything alright?",
    ),
}

WELLBEING = (
    "You've been at this a while. Worth a break at some point.",
    "Long stretch. Water, and maybe look at something further than the screen.",
)

APPRECIATION = (
    "Been thinking about {subject}. Good thing to be working on.",
    "That thing about {subject} stuck with me.",
)

TASK = (
    "You left {task} unfinished. Still on the list?",
    "{task} - want to pick that back up?",
)


def _pick(options: tuple, index: int) -> str:
    """Rotation, deterministic. `index` comes from the caller."""

    return options[index % len(options)]


class MessageComposer:
    """
    Turns a decision into the words that go out.

    Returns "" when it has nothing honest to say, and the engine treats
    that as a refusal. A composer that always produces something forces
    the system to send something.
    """

    def compose(
        self,
        decision: ProactiveDecision,
        context: ProactiveContext,
        rotation: int = 0,
    ) -> str:

        if not decision.send:
            return ""

        category = decision.category

        if category == Category.GREETING.value:
            return _pick(
                GREETINGS.get(context.part_of_day, GREETINGS[AFTERNOON]),
                rotation,
            )

        if category == Category.WELLBEING.value:
            return _pick(WELLBEING, rotation)

        if category == Category.APPRECIATION.value:

            subject = decision.detail.strip()

            # No referent, no message. "Thanks for everything" with
            # nothing behind it is the emptiest thing an assistant can
            # say, and this is the guard that makes it impossible.
            if not subject:
                return ""

            return _pick(APPRECIATION, rotation).format(
                subject=_shorten(subject)
            )

        if category == Category.TASK.value:

            task = decision.detail.strip()

            # No known task, no reminder. Nothing here may guess at what
            # the user might have been doing.
            if not task:
                return ""

            return _pick(TASK, rotation).format(task=_shorten(task))

        return ""


def _shorten(text: str, limit: int = 90) -> str:
    """A notification is one line. Keep the user's words, just fewer."""

    clean = " ".join(str(text).split())

    # Strip a leading "I " so the sentence reads in the second person -
    # "you left I finished the migration" is not a sentence.
    for prefix in ("i've ", "i have ", "i'm ", "i am ", "i "):
        if clean.lower().startswith(prefix):
            clean = clean[len(prefix):]
            break

    if len(clean) <= limit:
        return clean

    return clean[:limit].rstrip() + "..."
