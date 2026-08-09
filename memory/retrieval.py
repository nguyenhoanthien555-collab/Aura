"""
Retrieval.

Finding the handful of older messages that matter to the current turn.

What is here is lexical: tokens are compared, not meanings. That is
honest about its limits - it will find "you told me about the sqlite
migration" and it will miss "the database thing we discussed". The point
of this module is the `Retriever` seam, not the scoring inside it. An
embedding based retriever satisfies the same protocol and replaces this
class without touching anything that calls it.

One design detail worth keeping: recent messages are skipped. They are
already in the HISTORY section of the prompt, and recalling them again
spends tokens to repeat what the model can already see.
"""

import re
from typing import Protocol, runtime_checkable

from memory.models import Message
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
