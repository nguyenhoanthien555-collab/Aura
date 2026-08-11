"""
The decision engine.

One function, `should_proactively_message(context)`, returning a decision
that always carries a reason. Pure: no clock, no database, no I/O. Given
the same context it returns the same answer, which is what makes the
behaviour of this system something that can be asserted rather than
observed.

The order of the checks is the design. Reasons to stay silent are
evaluated first and cheaply, because silence is the answer the
overwhelming majority of the time and a system that computes a message
before deciding whether to send it will eventually send one by accident.

What this module does NOT do: it does not check cooldowns, quiet hours or
the daily cap. That is `ProactivePolicy`, and the separation is
deliberate - this answers "is there a reason to speak", the policy
answers "is it acceptable to speak right now". Both must say yes. Keeping
them apart is what stops a good reason from overriding the anti-spam
rules, which is precisely how a proactive assistant turns into a
nuisance.
"""

import math
from dataclasses import dataclass
from enum import Enum

# Reused rather than redefined: the companion engine already has a
# priority scale and a second one would drift from it.
from companion.decision import Priority
from proactive.context import ProactiveContext


class Category(str, Enum):
    """
    The kinds of unprompted message Aura may send.

    A closed set on purpose. Each one has its own cooldown and its own
    justification rule, and a category that is not listed here cannot be
    sent at all.
    """

    GREETING = "greeting"
    APPRECIATION = "appreciation"
    WELLBEING = "wellbeing"
    TASK = "task"


# How long the user must have been away before a greeting is a greeting
# rather than an interruption. Four hours: long enough that "hey, you're
# back" is true.
GREETING_AWAY_SECONDS = 4 * 3600

# Wellbeing check-ins require a long, unbroken working stretch. Three
# hours of continuous presence, not three hours of silence.
WELLBEING_SESSION_SECONDS = 3 * 3600

# A task reminder waits until the user has been away from it a while, so
# it does not fire while they are actively working on that very thing.
TASK_QUIET_SECONDS = 2 * 3600

# The window in which the user counts as "here right now". Speaking
# unprompted inside it interrupts an active conversation.
ACTIVE_CONVERSATION_SECONDS = 120


@dataclass(frozen=True)
class ProactiveDecision:
    """
    Whether to speak, and why - or why not.

    `reason` is populated on both answers. A silent decision that does
    not say why it was silent is untunable, and this system is mostly
    silent.
    """

    send: bool
    reason: str
    category: str = ""
    priority: Priority = Priority.LOW
    detail: str = ""

    @classmethod
    def silent(cls, reason: str) -> "ProactiveDecision":
        return cls(send=False, reason=reason)

    def as_dict(self) -> dict:
        return {
            "send": self.send,
            "reason": self.reason,
            "category": self.category,
            "priority": self.priority.value,
            "detail": self.detail,
        }


def _away(seconds: float) -> str:
    """
    How long the user has been gone, in words.

    Infinity means there is no record of them at all - the process has
    restarted and nobody has spoken since. The honest phrasing says that,
    rather than printing a number that was never measured. It also keeps
    `int()` away from a NaN, which is what `inf // 3600` produces.
    """

    if math.isinf(seconds):
        return "away, no record of a previous message"

    return f"away {int(seconds // 3600)}h"


def should_proactively_message(context: ProactiveContext) -> ProactiveDecision:
    """
    Is there a reason for Aura to speak first?

    Returns the single best reason, or a silent decision explaining what
    stopped it.
    """

    since_user = context.seconds_since_user()

    # --- reasons to stay quiet, cheapest first ------------------------

    if since_user < ACTIVE_CONVERSATION_SECONDS:
        return ProactiveDecision.silent(
            "recent interaction - the user is here right now"
        )

    # --- reasons to speak, most justified first -----------------------

    # A pending task the user has stepped away from. Highest value:
    # it is specific, it is real, and it is the only category that can
    # tell the user something they did not already know.
    if context.has_pending_work() and since_user >= TASK_QUIET_SECONDS:

        task = context.pending_tasks[0]

        return ProactiveDecision(
            send=True,
            reason=(
                f"unfinished task + {_away(since_user)} "
                f"+ {context.part_of_day} window"
            ),
            category=Category.TASK.value,
            priority=Priority.NORMAL,
            detail=task.description,
        )

    # Welcome back. Requires a real absence and a part of the day that
    # has not already been greeted.
    if since_user >= GREETING_AWAY_SECONDS and not context.greeted_this_part:
        return ProactiveDecision(
            send=True,
            reason=(
                f"user {_away(since_user)} "
                f"+ no {context.part_of_day} greeting yet"
            ),
            category=Category.GREETING.value,
            priority=Priority.LOW,
        )

    # Appreciation, and only with something to appreciate. The guard is
    # the point: "thanks for everything" with no referent is the most
    # hollow thing an assistant can say.
    if context.relevant_memories and since_user >= GREETING_AWAY_SECONDS:
        return ProactiveDecision(
            send=True,
            reason="shared context worth acknowledging + cooldown satisfied",
            category=Category.APPRECIATION.value,
            priority=Priority.LOW,
            detail=context.relevant_memories[0],
        )

    # Wellbeing, last and most throttled. Fires on a long stretch of
    # continuous presence, which is why it reads `last_user_message_at`
    # as a session start rather than an absence.
    if (
        context.last_user_message_at is not None
        and ACTIVE_CONVERSATION_SECONDS <= since_user < GREETING_AWAY_SECONDS
        and context.temporal.part_of_day == "night"
    ):
        return ProactiveDecision(
            send=True,
            reason="late night + active session + wellbeing cooldown satisfied",
            category=Category.WELLBEING.value,
            priority=Priority.LOW,
        )

    return ProactiveDecision.silent(
        "nothing worth interrupting for - no pending work, "
        "no absence, nothing to add"
    )
