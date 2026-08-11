"""
Episodic memory.

Things that happened, each with the date it happened on. This is the
store that makes "what was I working on last week" answerable, and it is
deliberately not the conversation transcript: the transcript is every
line of dialogue in order, this is the handful of events worth keeping.

Storage only, in the same spirit as ProfileStore. Nothing here decides
what deserves a row - `memory.selection` does that, and this module will
faithfully store whatever it is handed.

Two timestamps, and the distinction matters:

    occurred_at   when the event happened
    created_at    when Aura was told about it

"I finished the migration last night", said this afternoon, has an
`occurred_at` of last night and a `created_at` of today. Only
`occurred_at` may be used to describe the event back to the user, or
Aura ends up saying "you finished it this afternoon" about something
that happened while she was not running.
"""

from datetime import datetime

from core.temporal import local_now
from memory.models import EpisodicMemory, timestamp_now
from memory.sqlite import SessionLocal, db_lock, init_database


MAX_CONTENT = 2000

# How many rows the ranker is allowed to consider. Recall must stay
# bounded no matter how large the database gets: the prompt has room for
# a handful of lines, so scanning a hundred thousand rows to pick three
# is wasted work with a latency cost on every turn.
DEFAULT_SCOPE = 400


def _clamp(value, default: float = 0.5) -> float:
    """Scores are orderings in 0..1. Anything else is a caller bug."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return default

    return max(0.0, min(1.0, number))


class EpisodicStore:

    def __init__(self, session=None):

        if session is None:
            init_database()
            session = SessionLocal()

        self.session = session

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def remember(
        self,
        content: str,
        category: str = "event",
        importance: float = 0.5,
        confidence: float = 0.5,
        occurred_at: datetime | str | None = None,
        source: str = "user",
    ) -> EpisodicMemory | None:
        """
        Store one episode. Returns None when there is nothing to store.

        An exact-duplicate episode already recorded today is folded into
        the existing row rather than added again: people repeat
        themselves, and three copies of "I finished the migration" would
        crowd two other memories out of a bounded recall.
        """

        text = str(content or "").strip()[:MAX_CONTENT]

        if not text:
            return None

        when = occurred_at or local_now()

        if isinstance(when, datetime):
            when = when.isoformat(timespec="seconds")

        with db_lock:

            existing = (
                self.session.query(EpisodicMemory)
                .filter(EpisodicMemory.content == text)
                .order_by(EpisodicMemory.id.desc())
                .first()
            )

            if existing is not None and existing.occurred_at[:10] == when[:10]:

                # Said again on the same day. Keep the stronger scores;
                # repetition is weak evidence that it mattered.
                existing.importance = max(
                    existing.importance, _clamp(importance)
                )
                existing.confidence = max(
                    existing.confidence, _clamp(confidence)
                )

                self.session.commit()

                return existing

            episode = EpisodicMemory(
                content=text,
                category=str(category or "event")[:32],
                importance=_clamp(importance),
                confidence=_clamp(confidence),
                occurred_at=when,
                created_at=timestamp_now(),
                source=str(source or "user")[:16],
            )

            self.session.add(episode)
            self.session.commit()

        return episode

    def forget(self, episode_id: int) -> bool:

        with db_lock:

            episode = self.session.get(EpisodicMemory, episode_id)

            if episode is None:
                return False

            self.session.delete(episode)
            self.session.commit()

        return True

    def clear(self) -> None:

        with db_lock:
            self.session.query(EpisodicMemory).delete()
            self.session.commit()

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def recent(self, limit: int = 20) -> list[EpisodicMemory]:
        """Most recently *occurred* first, which is not insertion order."""

        with db_lock:
            return (
                self.session.query(EpisodicMemory)
                .order_by(
                    EpisodicMemory.occurred_at.desc(),
                    EpisodicMemory.id.desc(),
                )
                .limit(max(1, int(limit)))
                .all()
            )

    def candidates(self, scope: int = DEFAULT_SCOPE) -> list[EpisodicMemory]:
        """
        The pool the ranker scores against.

        Bounded by construction. Ranking a bounded pool is the whole
        reason recall cannot degrade into dumping the database into a
        prompt, which is the failure this store is most able to cause.
        """

        return self.recent(limit=max(1, int(scope)))

    def by_category(self, category: str, limit: int = 20) -> list[EpisodicMemory]:

        with db_lock:
            return (
                self.session.query(EpisodicMemory)
                .filter(EpisodicMemory.category == category)
                .order_by(EpisodicMemory.occurred_at.desc())
                .limit(max(1, int(limit)))
                .all()
            )

    def since(self, moment: datetime | str) -> list[EpisodicMemory]:
        """Everything that happened at or after `moment`."""

        cutoff = (
            moment.isoformat(timespec="seconds")
            if isinstance(moment, datetime)
            else str(moment)
        )

        with db_lock:
            return (
                self.session.query(EpisodicMemory)
                .filter(EpisodicMemory.occurred_at >= cutoff)
                .order_by(EpisodicMemory.occurred_at.desc())
                .all()
            )

    def __len__(self) -> int:
        with db_lock:
            return self.session.query(EpisodicMemory).count()
