"""
Where pending work comes from.

The rule this module exists to satisfy: a task reminder may only ever
mention work the user actually told Aura about. Nothing here guesses,
infers or synthesises a task. If the user has never mentioned unfinished
work, `EpisodicTaskSource` returns nothing and no task reminder is
possible - which is the correct behaviour, not a gap.

The source is episodic memory, filtered to the `plan` category. A plan
is something the user said they were going to do; that is as close to
"pending work" as anything Aura honestly knows. Two things disqualify a
plan:

    completed   the user later said they finished it
    stale       old enough that reminding is nagging, not helping

Completion is matched lexically against later episodes, which is a
deliberately conservative test: it can miss a completion that was worded
very differently, and the cost of missing one is a reminder about
something already done. That is annoying. The opposite bias - assuming
things are done and staying silent - is not detectable by the user at
all, so the noisy failure is the right one to prefer.
"""

from datetime import timedelta

from core.temporal import local_now, parse_timestamp
from memory.retrieval import tokenize
from proactive.context import PendingTask


# How long a plan stays worth mentioning. Beyond this, silence: a
# reminder about something from six weeks ago is not a reminder, it is
# an accusation.
DEFAULT_MAX_AGE_DAYS = 14

# And how recently it must have been said before a reminder is
# premature. Something mentioned an hour ago does not need chasing.
DEFAULT_MIN_AGE_HOURS = 6

# Overlap above which a later episode is treated as having completed an
# earlier plan.
COMPLETION_OVERLAP = 0.6

DONE_WORDS = frozenset({
    "finished", "done", "shipped", "completed", "solved", "fixed",
    "launched", "released", "merged", "closed", "sorted",
})

# Plans that never became real work. "I'm going to sleep" is not a task.
IGNORED = frozenset({"sleep", "eat", "nap", "shower", "rest"})

# The scaffolding of a stated intention. These words say that something
# is a plan; they say nothing about *what* the plan is, so comparing a
# plan to its completion has to ignore them. Leaving them in dilutes the
# overlap - "I'm going to rewrite the retriever" against "I finished the
# retriever rewrite" scores 0.5 on the raw tokens and would miss.
INTENT_WORDS = frozenset({
    "i'm", "im", "going", "gonna", "plan", "planning", "want", "wanna",
    "need", "should", "must", "start", "starting", "begin", "todo",
    "still", "next", "later", "soon", "today", "tomorrow", "tonight",
})


class EpisodicTaskSource:
    """
    Pending work, read out of episodic memory.

    Callable, so it can be handed straight to `ProactiveEngine` as its
    `pending_tasks` source.
    """

    def __init__(
        self,
        store,
        clock=local_now,
        max_age_days: int = DEFAULT_MAX_AGE_DAYS,
        min_age_hours: int = DEFAULT_MIN_AGE_HOURS,
        limit: int = 3,
    ):
        self.store = store
        self.clock = clock
        self.max_age_days = int(max_age_days)
        self.min_age_hours = int(min_age_hours)
        self.limit = int(limit)

    def __call__(self) -> list[PendingTask]:
        return self.pending()

    def pending(self) -> list[PendingTask]:
        """Unfinished plans, most recent first. Empty is normal."""

        now = self.clock()

        oldest = now - timedelta(days=self.max_age_days)
        newest = now - timedelta(hours=self.min_age_hours)

        episodes = self.store.candidates(scope=200)

        plans = []
        completions = []

        for episode in episodes:

            when = parse_timestamp(episode.occurred_at)

            if when is None:
                continue

            if episode.category == "plan":
                plans.append((when, episode))
            elif self._reads_as_completion(episode.content):
                completions.append((when, episode))

        pending = []

        for when, episode in plans:

            if not (oldest <= when <= newest):
                continue

            if self._ignored(episode.content):
                continue

            if self._completed_later(episode.content, when, completions):
                continue

            pending.append(
                PendingTask(
                    description=episode.content,
                    since=when,
                    source="episodic",
                )
            )

        pending.sort(key=lambda task: task.since, reverse=True)

        return pending[: self.limit]

    # ------------------------------------------------------------------

    @staticmethod
    def _reads_as_completion(text: str) -> bool:
        return bool(tokenize(text) & DONE_WORDS)

    @staticmethod
    def _ignored(text: str) -> bool:
        return bool(tokenize(text) & IGNORED)

    @staticmethod
    def _completed_later(plan: str, planned_at, completions) -> bool:
        """
        Did the user later say they had done this?

        Only episodes *after* the plan count. Saying "I finished the
        migration" on Monday does not complete a plan to work on the
        migration made on Tuesday.

        Both sides are stripped of intent and completion vocabulary, so
        what is compared is the subject of the work and nothing else.
        """

        noise = DONE_WORDS | INTENT_WORDS

        plan_tokens = tokenize(plan) - noise

        if not plan_tokens:
            return False

        for when, episode in completions:

            if when < planned_at:
                continue

            done_tokens = tokenize(episode.content) - noise

            if not done_tokens:
                continue

            overlap = len(plan_tokens & done_tokens) / len(plan_tokens)

            if overlap >= COMPLETION_OVERLAP:
                return True

        return False
