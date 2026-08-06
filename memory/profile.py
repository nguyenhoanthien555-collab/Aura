"""
User profile memory.

Long term facts about the person Aura is talking to, stored one key at a
time so they can be corrected rather than accumulated. Saying "actually
I moved to Da Nang" should overwrite the old city, not leave Aura holding
two contradictory beliefs.

This is storage only. Nothing here decides what is worth remembering -
that judgement belongs upstream, and today it is the user's explicit
instruction.
"""

import re

from memory.models import UserFact, timestamp_now
from memory.sqlite import SessionLocal, init_database


MAX_KEY = 64
MAX_VALUE = 2000


def normalise_key(key: str) -> str:
    """
    Turn free text into a stable slug.

    "Favourite Drink" and "favourite_drink" must be the same fact, or
    the upsert silently becomes an insert and the contradiction is back.
    """

    slug = str(key or "").strip().lower()

    slug = re.sub(r"[^a-z0-9]+", "_", slug).strip("_")

    return slug[:MAX_KEY]


class ProfileStore:

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
        key: str,
        value: str,
        category: str = "profile",
        source: str = "user",
    ) -> UserFact | None:
        """
        Store or update one fact. Returns None for an unusable key.
        """

        slug = normalise_key(key)

        if not slug:
            return None

        text = str(value or "").strip()[:MAX_VALUE]

        if not text:
            return None

        fact = (
            self.session.query(UserFact)
            .filter(UserFact.key == slug)
            .one_or_none()
        )

        if fact is None:

            fact = UserFact(
                key=slug,
                value=text,
                category=category,
                source=source,
            )

            self.session.add(fact)

        else:
            fact.value = text
            fact.category = category
            fact.source = source
            fact.updated_at = timestamp_now()

        self.session.commit()

        return fact

    def forget(self, key: str) -> bool:

        slug = normalise_key(key)

        fact = (
            self.session.query(UserFact)
            .filter(UserFact.key == slug)
            .one_or_none()
        )

        if fact is None:
            return False

        self.session.delete(fact)
        self.session.commit()

        return True

    def clear(self) -> None:

        self.session.query(UserFact).delete()
        self.session.commit()

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def get(self, key: str) -> str:
        """The value, or "" when Aura does not know."""

        slug = normalise_key(key)

        fact = (
            self.session.query(UserFact)
            .filter(UserFact.key == slug)
            .one_or_none()
        )

        return fact.value if fact is not None else ""

    def all(self, limit: int | None = None) -> list[UserFact]:
        """
        Every fact, most recently updated first.

        Recency ordering matters when a limit is applied: a truncated
        profile should keep what was just learned, not the oldest thing
        Aura ever heard.
        """

        query = (
            self.session.query(UserFact)
            .order_by(UserFact.updated_at.desc(), UserFact.id.desc())
        )

        if limit is not None:
            query = query.limit(limit)

        return query.all()

    def by_category(self, category: str) -> list[UserFact]:

        return (
            self.session.query(UserFact)
            .filter(UserFact.category == category)
            .order_by(UserFact.key.asc())
            .all()
        )

    def render(self, limit: int | None = None) -> list[str]:
        """Prompt ready lines."""

        return [fact.render() for fact in self.all(limit)]

    def __len__(self) -> int:
        return self.session.query(UserFact).count()
