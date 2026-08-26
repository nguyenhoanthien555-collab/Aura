"""
Memory manager.

Storage only. This module knows nothing about the AI pipeline —
it deals in (role, content) pairs and ORM rows. Converting rows into
pipeline messages is done by brain/adapters.py.
"""

from datetime import datetime

from core.temporal import parse_timestamp
from memory.sqlite import SessionLocal, db_lock, init_database
from memory.models import Message


class MemoryManager:

    def __init__(self, session=None):
        """
        A session may be injected (tests, alternate databases).
        """
        if session is None:
            init_database()
            session = SessionLocal()

        self.session = session

    def save(self, role: str, content: str, session_id: str = "default") -> None:
        message = Message(
            role=role,
            content=content,
            session_id=session_id,
        )

        # Held across add+commit, not around each one: a half-applied
        # write is exactly what another thread must never observe.
        with db_lock:
            self.session.add(message)
            self.session.commit()

    def get_recent(self, limit: int = 10, session_id: str = "default") -> list[Message]:
        """
        Most recent messages for a session, NEWEST FIRST.
        """
        with db_lock:
            return (
                self.session.query(Message)
                .filter(Message.session_id == session_id)
                .order_by(Message.id.desc())
                .limit(limit)
                .all()
            )

    def last_said_at(
        self, role: str = "user", session_id: str | None = None
    ) -> datetime | None:
        """
        When this role last said something here, or None.

        Added for the proactive engine, which needs to know whether the
        owner is actually absent before greeting them as though they
        were. It kept that in a field set by `note_chat()`, which meant
        the answer was lost on every restart and a restart read as "away
        forever" - so Aura would welcome back somebody who had been
        talking to her a minute earlier (sections 8, 19 and 21).

        The answer belongs here rather than there. This class owns the
        `messages` table, every real chat turn already writes a row to
        it, and the proactive package has no business reaching into
        another module's storage.

        Filtered rather than sliced: `get_recent` would answer this for
        an ordinary conversation and then quietly stop the first time a
        run of replies grew longer than its limit.

        `session_id` defaults to every session rather than to "default",
        unlike the methods above it, and the difference matters: an
        Android install supplies its own id (`server/session.py`), so
        "default" would have excluded every message the owner ever sent
        from their phone. That is also the right question rather than a
        patch around a wrong one - `server/runtime.py` states the
        deployment as one person and one Aura, with the auth token as the
        only identity boundary, and the caller that needs this has one
        instance per process rather than one per session. Naming a session
        still narrows it, because the table really is stored that way.
        """

        with db_lock:
            query = self.session.query(Message).filter(Message.role == role)

            if session_id is not None:
                query = query.filter(Message.session_id == session_id)

            row = query.order_by(Message.id.desc()).first()

        if row is None:
            return None

        # None on an unparseable timestamp, which is the same answer as
        # "nobody has spoken". A row that cannot be read is not evidence
        # of presence, and guessing one would be worse than not knowing.
        return parse_timestamp(row.timestamp)

    def clear(self, session_id: str | None = None) -> None:
        with db_lock:
            query = self.session.query(Message)
            if session_id is not None:
                query = query.filter(Message.session_id == session_id)
            query.delete(synchronize_session=False)
            self.session.commit()

