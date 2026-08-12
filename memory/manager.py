"""
Memory manager.

Storage only. This module knows nothing about the AI pipeline —
it deals in (role, content) pairs and ORM rows. Converting rows into
pipeline messages is done by brain/adapters.py.
"""

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

    def clear(self, session_id: str | None = None) -> None:
        with db_lock:
            query = self.session.query(Message)
            if session_id is not None:
                query = query.filter(Message.session_id == session_id)
            query.delete(synchronize_session=False)
            self.session.commit()

