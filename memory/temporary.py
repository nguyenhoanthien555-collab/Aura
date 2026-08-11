"""
Temporary context.

The third kind of memory, and the one defined by what it must never
become. "I'm at a cafe right now", "just woke up", "about to head out" -
true for an hour, actively misleading a week later, and worthless as a
permanent fact about a person.

The design is one decision: **this never touches the database.** Not a
table with a TTL column, not a row with `valid_until` and a sweeper.
Process memory that expires on read. A cleanup job that fails leaves
stale facts behind; a store that cannot persist cannot leak. If Aura
restarts, the cafe is forgotten, which is exactly right.

Promotion to permanent memory is possible and is never automatic. It
takes an explicit `promote()` call by something that has a reason, and
the reason it exists at all is the case where a passing remark turns out
to matter: "I'm at the hospital right now" may deserve to be kept, and
only a caller with more context than this module can say so.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Lock

from core.temporal import local_now


# How long a passing remark stays true. Three hours covers "this
# afternoon" without surviving into tomorrow morning.
DEFAULT_TTL_SECONDS = 3 * 3600

# A hard ceiling on how much can be held at once, so a chatty session
# cannot grow this without bound between restarts.
DEFAULT_MAX_ENTRIES = 12


@dataclass(frozen=True)
class TemporaryNote:
    """One passing thing, and when it stops being true."""

    text: str
    created_at: datetime
    expires_at: datetime
    category: str = "context"

    def expired(self, now: datetime) -> bool:
        return now >= self.expires_at

    def render(self) -> str:
        return self.text


class TemporaryContext:
    """
    Short-lived context, held in process and never written down.

    Thread-safe: notes arrive on request threads and are read by the
    prompt builder on the same ones.

    `clock` is injected so expiry can be tested without sleeping.
    """

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        clock=local_now,
    ):
        self.ttl = float(ttl_seconds)
        self.max_entries = max(1, int(max_entries))
        self.clock = clock

        self._lock = Lock()
        self._notes: list[TemporaryNote] = []

    # ------------------------------------------------------------------

    def note(
        self,
        text: str,
        category: str = "context",
        ttl_seconds: float | None = None,
    ) -> TemporaryNote | None:
        """Record something that is true for now. Returns None if empty."""

        content = " ".join(str(text or "").split())

        if not content:
            return None

        now = self.clock()
        ttl = self.ttl if ttl_seconds is None else float(ttl_seconds)

        entry = TemporaryNote(
            text=content,
            created_at=now,
            expires_at=now + timedelta(seconds=max(0.0, ttl)),
            category=str(category or "context"),
        )

        with self._lock:

            # Restating the same thing refreshes it rather than stacking.
            self._notes = [
                note for note in self._notes if note.text != content
            ]

            self._notes.append(entry)

            if len(self._notes) > self.max_entries:
                self._notes = self._notes[-self.max_entries:]

        return entry

    def active(self) -> list[TemporaryNote]:
        """
        What is still true, oldest first.

        Expiry happens here rather than on a timer: nothing has to be
        running for a stale note to disappear, it simply stops being
        returned.
        """

        now = self.clock()

        with self._lock:
            self._notes = [note for note in self._notes if not note.expired(now)]

            return list(self._notes)

    def render(self, limit: int = 3) -> list[str]:
        """
        Prompt lines, newest first and hard-bounded.

        Newest first because if only one line fits, the most recent
        passing remark is the one still likely to be true.
        """

        notes = self.active()

        if not notes:
            return []

        return [note.render() for note in reversed(notes)][: max(0, int(limit))]

    def promote(self, text: str) -> str:
        """
        Hand a note to a caller that intends to store it permanently,
        and drop it from here.

        This module does not do the storing. It cannot - it has no
        database and no opinion about what deserves one. Returns the
        exact text so the caller can persist it, or "" if it had already
        expired, which is itself an answer: it did not matter enough.
        """

        content = " ".join(str(text or "").split())

        now = self.clock()

        with self._lock:

            for note in self._notes:

                if note.text == content and not note.expired(now):
                    self._notes.remove(note)
                    return note.text

        return ""

    def clear(self) -> None:
        with self._lock:
            self._notes.clear()

    def __len__(self) -> int:
        return len(self.active())
