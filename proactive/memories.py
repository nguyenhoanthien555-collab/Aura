"""
Where the things worth acknowledging come from.

This module exists because of a defect, and the defect is worth stating
before the fix: `Category.APPRECIATION` could not fire in production.

The decision engine has a branch for it, the policy has a 24-hour
cooldown for it, the composer has two templates for it and the suite has
three tests for it. All of that was reachable only when
`ProactiveContext.relevant_memories` was non-empty, and the only thing
that fills it is the engine's `memories` source - which
`launcher/services.py` never passed. So `engine.memories` was `None` in
every real process, `_gather_memories()` returned `()`, and the branch
guarded by `if context.relevant_memories` was dead code that every test
covered.

Wired, green, and dead: the same shape as the phase 11 `memory.recall`
defect and the phase 13 `last_said_at` default. The tests all passed
because they built a `ProactiveContext` by hand with the field already
populated, which is the one thing production could not do.

**What counts as something worth acknowledging.** Episodic memory's
`project` category, and nothing else. The selector assigns it to
statements about what the user is working on, which is exactly what the
composer's wording assumes ("Been thinking about {subject}. Good thing
to be working on."). Deliberately not the `plan` category: that is what
`EpisodicTaskSource` reads for reminders, and a system that mines one
category for two different kinds of unprompted message says the same
thing twice in two voices.

**Nothing here writes a fact.** The text handed out is the user's own
sentence, unchanged, exactly as `proactive/tasks.py` promises for
reminders. An appreciation about a project the user never mentioned
would be worse than silence - it is Aura inventing a life for somebody
and then admiring it.
"""

from datetime import timedelta

from core.temporal import local_now, parse_timestamp


# The category in `memory/selection.py`'s vocabulary that means "the user
# said something about what they are working on".
PROJECT = "project"

# Old enough to have stayed with her, recent enough to still be true. A
# fortnight matches `EpisodicTaskSource.DEFAULT_MAX_AGE_DAYS`, and for
# the same reason: past that, mentioning it is not warmth, it is Aura
# bringing up something the user has moved on from.
DEFAULT_MAX_AGE_DAYS = 14

# And long enough ago that acknowledging it is not just repeating what
# the user said an hour ago back at them. Longer than the task source's
# six hours, because a reminder is useful immediately and an
# appreciation is not.
DEFAULT_MIN_AGE_HOURS = 24

# Below this, a stored line is a fragment rather than a subject. The
# composer interpolates it into a sentence, and "Been thinking about ok."
# is worse than saying nothing.
MIN_SUBJECT_LENGTH = 12


class EpisodicMemorySource:
    """
    Things the user said they were working on, most recent first.

    Callable, so it can be handed straight to `ProactiveEngine` as its
    `memories` source, exactly as `EpisodicTaskSource` is handed in as
    `pending_tasks`.

    Empty is the normal answer and must stay cheap. A user who has never
    said what they are working on gets no appreciation messages, which is
    correct rather than a gap.
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

    def __call__(self) -> list[str]:
        return self.worth_acknowledging()

    def worth_acknowledging(self) -> list[str]:
        """
        The user's own words about their own work. Empty is normal.

        A store that raises is not caught here: the engine's
        `_gather_memories` already treats a failing source as a source
        with nothing to say, and duplicating that would give this module
        a second, quieter opinion about what a broken database means.
        """

        now = self.clock()

        oldest = now - timedelta(days=self.max_age_days)
        newest = now - timedelta(hours=self.min_age_hours)

        found = []

        for episode in self.store.by_category(PROJECT, limit=50):

            when = parse_timestamp(episode.occurred_at)

            if when is None:
                continue

            if not (oldest <= when <= newest):
                continue

            subject = str(episode.content or "").strip()

            if len(subject) < MIN_SUBJECT_LENGTH:
                continue

            found.append((when, subject))

        found.sort(key=lambda pair: pair[0], reverse=True)

        return [subject for _when, subject in found[: self.limit]]
