"""
Retrieval.

Finding the handful of older lines that matter to the current turn.

What is here is lexical: tokens are compared, not meanings. That is
honest about its limits - it will find "you told me about the sqlite
migration" and it will miss "the database thing we discussed". The point
of this module is the `Retriever` seam, not the scoring inside it. An
embedding based retriever satisfies the same protocol and replaces these
classes without touching anything that calls them.

Two implementations exist:

    KeywordRetriever   the original, over conversation lines
    RankedRetriever    over episodic memories, relevance *and* recency
                       and importance

One design detail worth keeping: recent messages are skipped. They are
already in the HISTORY section of the prompt, and recalling them again
spends tokens to repeat what the model can already see.
"""

import re
from datetime import datetime
from typing import Protocol, runtime_checkable

from memory.models import EpisodicMemory, Message
from memory.sqlite import SessionLocal, db_lock, init_database


# Words too common to carry meaning. Small on purpose: an aggressive
# stop list throws away short but meaningful queries.
STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "if", "then", "than", "so",
    "is", "are", "was", "were", "be", "been", "being", "am",
    "do", "does", "did", "doing", "have", "has", "had",
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her",
    "us", "them", "my", "your", "his", "its", "our", "their",
    "this", "that", "these", "those", "what", "which", "who", "whom",
    "to", "of", "in", "on", "at", "for", "with", "about", "from", "by",
    "as", "into", "up", "down", "out", "off", "over", "under",
    "can", "could", "will", "would", "should", "may", "might", "must",
    "not", "no", "yes", "just", "very", "really", "please",
})

MIN_TOKEN = 2
MAX_LINE = 200

DEFAULT_SCOPE = 500
DEFAULT_SKIP_RECENT = 20


def tokenize(text: str) -> set[str]:
    """Meaningful lowercase words in `text`."""

    words = re.findall(r"[\w']+", str(text or "").lower())

    return {
        word
        for word in words
        if len(word) >= MIN_TOKEN and word not in STOPWORDS
    }


@runtime_checkable
class Retriever(Protocol):
    """
    Finds stored lines relevant to a query.

    Returns rendered strings, never rows, so callers never depend on the
    storage schema.
    """

    def search(self, query: str, limit: int = 3) -> list[str]:
        ...


class NullRetriever:
    """Retrieves nothing. The default when recall is switched off."""

    def search(self, query: str, limit: int = 3) -> list[str]:
        return []


class KeywordRetriever:

    def __init__(
        self,
        session=None,
        scope: int = DEFAULT_SCOPE,
        skip_recent: int = DEFAULT_SKIP_RECENT,
        min_score: int = 1,
    ):

        if session is None:
            init_database()
            session = SessionLocal()

        self.session = session
        self.scope = scope
        self.skip_recent = skip_recent
        self.min_score = min_score

    def search(self, query: str, limit: int = 3) -> list[str]:

        wanted = tokenize(query)

        if not wanted:
            return []

        candidates = self._candidates()

        scored = []

        for message in candidates:

            score = len(wanted & tokenize(message.content))

            if score >= self.min_score:
                scored.append((score, message.id, message))

        # Highest overlap first, most recent breaking ties.
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)

        return [
            self._render(message)
            for _score, _id, message in scored[:limit]
        ]

    def _candidates(self) -> list[Message]:
        """
        The window of history worth searching.

        Bounded by `scope` so recall cost does not grow with the lifetime
        of the database, and offset by `skip_recent` so it never returns
        what the prompt already contains.
        """

        with db_lock:
            return (
                self.session.query(Message)
                .order_by(Message.id.desc())
                .offset(self.skip_recent)
                .limit(self.scope)
                .all()
            )

    @staticmethod
    def _render(message: Message) -> str:

        content = " ".join(str(message.content).split())

        if len(content) > MAX_LINE:
            content = content[:MAX_LINE].rstrip() + "..."

        return f"{message.role}: {content}"


# ----------------------------------------------------------------------
# Ranked retrieval over episodic memory
# ----------------------------------------------------------------------

# What each signal is worth. Relevance dominates deliberately: a highly
# important memory about something else is still the wrong memory, and
# the failure mode of a recency-heavy ranker is that Aura answers every
# question with whatever happened most recently.
WEIGHT_RELEVANCE = 0.6
WEIGHT_RECENCY = 0.25
WEIGHT_IMPORTANCE = 0.15

# Recency is scored on a half-life rather than a cliff, so a memory does
# not fall off the end of a window between one turn and the next. Two
# weeks: a month-old memory still competes, a year-old one barely does.
RECENCY_HALF_LIFE_DAYS = 14.0

# Nothing with less lexical overlap than this is worth a prompt line,
# whatever its importance. Without a floor, an empty-ish query returns
# the most important memories regardless of subject.
MIN_RELEVANCE = 0.05


class RankedRetriever:
    """
    Episodic recall, ranked and bounded.

    The pipeline the phase called for, in one place and in order:

        query -> candidates -> relevance -> recency -> importance
              -> temporal validity -> top results

    Every stage is a pure function of the row and the query except the
    candidate fetch, which is bounded by `EpisodicStore.candidates`. The
    whole database is never a candidate set and the result is always
    `limit` lines or fewer, so no growth in stored memory can turn into
    growth in prompt size.

    `clock` is injected: ranking depends on the current time, and a test
    that cannot pin "now" cannot assert anything about recency.
    """

    def __init__(
        self,
        store,
        clock=None,
        scope: int = 400,
        min_relevance: float = MIN_RELEVANCE,
    ):
        self.store = store
        self.scope = int(scope)
        self.min_relevance = float(min_relevance)

        if clock is None:
            from core.temporal import local_now

            clock = local_now

        self.clock = clock

    # ------------------------------------------------------------------

    def search(self, query: str, limit: int = 3) -> list[str]:
        """Rendered lines, best first. Satisfies the Retriever protocol."""

        return [
            self._render(episode, now)
            for episode, _score, now in self.rank(query, limit)
        ]

    def rank(self, query: str, limit: int = 3) -> list[tuple]:
        """
        The scored form, for tests and for anything that needs the row.

        Returns (episode, score, now) so a caller can explain a ranking
        rather than only observe it.
        """

        wanted = tokenize(query)

        if not wanted:
            return []

        now = self.clock()

        scored = []

        for episode in self.store.candidates(scope=self.scope):

            relevance = self._relevance(wanted, episode.content)

            if relevance < self.min_relevance:
                continue

            if not self._valid_now(episode, now):
                continue

            score = (
                WEIGHT_RELEVANCE * relevance
                + WEIGHT_RECENCY * self._recency(episode, now)
                + WEIGHT_IMPORTANCE * float(episode.importance or 0.0)
            )

            scored.append((score, episode.id, episode))

        # Best score first, most recent row breaking ties.
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)

        return [
            (episode, round(score, 4), now)
            for score, _id, episode in scored[: max(0, int(limit))]
        ]

    # ------------------------------------------------------------------
    # The stages
    # ------------------------------------------------------------------

    @staticmethod
    def _relevance(wanted: set[str], content: str) -> float:
        """
        Overlap as a fraction of the query, not of the memory.

        Dividing by the query length means a long memory that happens to
        contain the query words is not penalised for being long, which
        is the right bias: the question is "does this answer the query",
        not "is this memory mostly about the query".
        """

        tokens = tokenize(content)

        if not tokens or not wanted:
            return 0.0

        return len(wanted & tokens) / len(wanted)

    def _recency(self, episode, now: datetime) -> float:
        """
        1.0 for something that just happened, halving every half-life.

        Never reaches zero, so an old memory with overwhelming relevance
        can still win - which is the point of ranking rather than
        filtering by date.
        """

        from core.temporal import parse_timestamp

        occurred = parse_timestamp(episode.occurred_at)

        if occurred is None:
            return 0.0

        days = (now - occurred).total_seconds() / 86400.0

        if days <= 0:
            # Today, or dated in the future. Both are maximally current.
            return 1.0

        return 0.5 ** (days / RECENCY_HALF_LIFE_DAYS)

    @staticmethod
    def _valid_now(episode, now: datetime) -> bool:
        """
        Temporal validity: a memory of a future event is not recalled as
        though it had already happened.

        Everything episodic is valid once it has occurred; the check
        exists because `occurred_at` may legitimately be in the future
        when the user describes a plan, and reporting a plan as a past
        event is exactly the kind of confident wrongness this phase is
        supposed to prevent.
        """

        from core.temporal import parse_timestamp

        occurred = parse_timestamp(episode.occurred_at)

        if occurred is None:
            return True

        if occurred <= now:
            return True

        # A future-dated memory is only recalled if it is labelled as a
        # plan, where "you're planning to X" is a true sentence.
        return episode.category == "plan"

    @staticmethod
    def _render(episode: EpisodicMemory, now: datetime) -> str:
        """
        One prompt line, dated the way a person would say it.

        The relative phrasing is the entire reason temporal context and
        episodic memory were built in the same phase: a memory the model
        cannot place in time gets recalled and then misdescribed.
        """

        from core.temporal import describe_when

        content = " ".join(str(episode.content).split())

        if len(content) > MAX_LINE:
            content = content[:MAX_LINE].rstrip() + "..."

        when = describe_when(episode.occurred_at, now)

        return f"{when} - {content}" if when else content
