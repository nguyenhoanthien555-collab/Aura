"""
Memory manager.

Storage only. This module knows nothing about the AI pipeline —
it deals in (role, content) pairs and ORM rows. Converting rows into
pipeline messages is done by brain/adapters.py.
"""

from memory.sqlite import SessionLocal, init_database
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

        self.session.add(message)
        self.session.commit()

    def get_recent(self, limit: int = 10) -> list[Message]:
        """
        Most recent messages, NEWEST FIRST.
        """
        return (
            self.session.query(Message)
            .order_by(Message.id.desc())
            .limit(limit)
            .all()
        )

    def clear(self) -> None:
        self.session.query(Message).delete()
        self.session.commit()
