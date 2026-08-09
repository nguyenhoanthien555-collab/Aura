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

    def save(self, role: str, content: str) -> None:
        message = Message(
            role=role,
            content=content,
        )

        # Held across add+commit, not around each one: a half-applied
        # write is exactly what another thread must never observe.
        with db_lock:
            self.session.add(message)
            self.session.commit()

    def get_recent(self, limit: int = 10) -> list[Message]:
        """
        Most recent messages, NEWEST FIRST.
        """
        with db_lock:
            return (
                self.session.query(Message)
                .order_by(Message.id.desc())
                .limit(limit)
                .all()
            )

    def clear(self) -> None:
        with db_lock:
            self.session.query(Message).delete()
            self.session.commit()
