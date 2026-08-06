"""
Database models for Aura memory.

Two independent tables:

    Message   the conversation, append only, kept in order
    UserFact  what Aura has learned about the user, keyed and updatable

They are deliberately not related. A fact outlives the conversation that
produced it, and clearing the chat history must not erase what Aura
knows about the person it is talking to.

Note: this Message is a storage row and stays distinct from
brain.message.Message, the pipeline value. brain/adapters.py is the one
place that converts between them, and it converts in one direction only.
"""

from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Text, UniqueConstraint
from datetime import datetime


def timestamp_now() -> str:
    """Shared timestamp format for every table."""

    return datetime.now().isoformat(timespec="seconds")


class Base(DeclarativeBase):
    pass


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)

    role: Mapped[str] = mapped_column(String(20))

    content: Mapped[str] = mapped_column(Text())

    timestamp: Mapped[str] = mapped_column(default=timestamp_now)


class UserFact(Base):
    """
    One thing Aura knows about the user.

    `key` is a stable slug ("name", "job", "likes_coffee") so a fact can
    be corrected in place rather than accumulating contradictions.
    `category` groups facts for retrieval; `source` records whether the
    user stated it or Aura inferred it, which matters when deciding how
    confidently to repeat it back.
    """

    __tablename__ = "user_facts"

    __table_args__ = (
        UniqueConstraint("key", name="uq_user_facts_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    key: Mapped[str] = mapped_column(String(64), index=True)

    value: Mapped[str] = mapped_column(Text())

    category: Mapped[str] = mapped_column(String(32), default="profile")

    source: Mapped[str] = mapped_column(String(16), default="user")

    created_at: Mapped[str] = mapped_column(default=timestamp_now)

    updated_at: Mapped[str] = mapped_column(default=timestamp_now)

    def render(self) -> str:
        """The form that goes into a prompt."""

        return f"{self.key.replace('_', ' ')}: {self.value}"