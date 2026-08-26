"""
What is true right now.

Everything the decision engine is allowed to look at, gathered into one
frozen value. Frozen because a decision has to be reproducible: given
this exact context, the answer is always the same, which is what makes
the whole thing testable without waiting for real time to pass.

The gathering is deliberately separate from the deciding. Assembling
this object touches the clock, the database and the session state;
deciding touches nothing at all. That split is why the decision engine
has no fixtures and no mocks in its tests.
"""

from dataclasses import dataclass, field
from datetime import datetime

from core.temporal import TemporalContext


@dataclass(frozen=True)
class PendingTask:
    """
    A real piece of unfinished work.

    Only ever constructed from something the user actually said. There
    is no code path that guesses at one, because a reminder about work
    that does not exist is worse than no reminder: it is Aura
    confidently making something up about the user's life.
    """

    description: str
    since: datetime | None = None
    source: str = "memory"


@dataclass(frozen=True)
class ProactiveContext:
    """
    The inputs to one proactive decision.

    `last_user_message_at` and `last_proactive_at` are naive local
    datetimes, matching `core.temporal`, so every interval here is
    computed the same way and against the same clock.
    """

    temporal: TemporalContext

    # When the user was last actually here. None means "not in this
    # process's lifetime", which is treated as a long time ago.
    last_user_message_at: datetime | None = None

    # When Aura last spoke unprompted, and in which category.
    last_proactive_at: datetime | None = None
    last_proactive_category: str = ""

    # Real pending work. Empty is the normal case and must stay cheap.
    pending_tasks: tuple[PendingTask, ...] = ()

    # A line or two of relevant memory, if anything is relevant. Used to
    # decide whether Aura has anything worth adding, and never invented.
    relevant_memories: tuple[str, ...] = ()

    # How many proactive messages have already gone out today.
    sent_today: int = 0

    # What was said recently, for duplicate and similarity suppression.
    recent_messages: tuple[str, ...] = ()

    # Whether the user has been greeted during this part of the day
    # already. Prevents "good morning" twice in one morning.
    greeted_this_part: bool = False

    @property
    def now(self) -> datetime:
        return self.temporal.now

    @property
    def hour(self) -> int:
        return self.temporal.now.hour

    @property
    def part_of_day(self) -> str:
        return self.temporal.part_of_day

    def seconds_since_user(self) -> float:
        """
        How long since the user said anything.

        Infinity when unknown. That is the deliberate reading of a
        missing value, but "safe" is not the whole truth and the earlier
        wording here claimed it was: it said every rule uses this only to
        hold back. The active-conversation guard does - a small value
        keeps Aura quiet. But the greeting, task and appreciation rules
        fire on a *large* value, and infinity is the largest, so a
        missing presence reads to them as a long absence that justifies
        speaking.

        The greeting is the sharp edge. A brand-new user with no message
        on record has `seconds_since_user == inf`, which clears
        `GREETING_AWAY_SECONDS`, so the only thing standing between an
        empty history and an unprompted "welcome back" is
        `greeted_this_part` and the engine's presence source reading the
        durable `messages` table. Neither is on this object, which is why
        the honest version of this docstring names the firing path
        instead of denying it exists.
        """

        if self.last_user_message_at is None:
            return float("inf")

        return (self.now - self.last_user_message_at).total_seconds()

    def seconds_since_proactive(self) -> float:
        """Infinity when Aura has not spoken unprompted yet."""

        if self.last_proactive_at is None:
            return float("inf")

        return (self.now - self.last_proactive_at).total_seconds()

    def has_pending_work(self) -> bool:
        return bool(self.pending_tasks)

    def as_dict(self) -> dict:
        """Structured form, for logging why a decision went the way it did."""

        return {
            "time": self.temporal.as_dict(),
            "seconds_since_user": self.seconds_since_user(),
            "seconds_since_proactive": self.seconds_since_proactive(),
            "pending_tasks": len(self.pending_tasks),
            "relevant_memories": len(self.relevant_memories),
            "sent_today": self.sent_today,
            "greeted_this_part": self.greeted_this_part,
        }
