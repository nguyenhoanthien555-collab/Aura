"""
Database models for Aura memory.

Three independent tables, one per kind of knowing:

    Message       the conversation, append only, kept in order
    UserFact      what Aura has learned about the user, keyed and updatable
    EpisodicMemory  things that happened, with a real timestamp

They are deliberately not related. A fact outlives the conversation that
produced it, and clearing the chat history must not erase what Aura
knows about the person it is talking to. An episode outlives both: it is
a dated event, not a line of dialogue and not a standing truth.

There is deliberately no table for temporary context. "I'm at a cafe
right now" must expire on its own rather than needing a cleanup job to
notice it, so it lives in `memory.temporary` in process memory and is
never written here. The whole point is that it cannot silently become
permanent.

Note: this Message is a storage row and stays distinct from
brain.message.Message, the pipeline value. brain/adapters.py is the one
place that converts between them, and it converts in one direction only.
"""

from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import Float, Index, LargeBinary, String, Text, UniqueConstraint
from datetime import datetime


def timestamp_now() -> str:
    """Shared timestamp format for every table."""

    return datetime.now().isoformat(timespec="seconds")


class Base(DeclarativeBase):
    pass


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)

    session_id: Mapped[str] = mapped_column(String(128), default="default", index=True)

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


class EpisodicMemory(Base):
    """
    Something that happened, and when.

    Distinct from a UserFact, which is a standing truth with no date
    ("prefers tea"), and from a Message, which is a line of dialogue.
    An episode is an event worth remembering: "finished the sqlite
    migration", "started learning Japanese". It is what makes "what did
    I do last week" answerable.

    `occurred_at` is when the event happened and `created_at` is when
    Aura learned about it. They are usually the same and occasionally
    are not - "I finished it last night" is learned today about
    yesterday - and only the first one may be used to describe the
    event to the user.

    `importance` (0..1) ranks recall against relevance and recency;
    `confidence` (0..1) is how sure Aura is that it understood. Neither
    is a probability, both are orderings.
    """

    __tablename__ = "episodic_memories"

    __table_args__ = (
        Index("ix_episodic_occurred_at", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    content: Mapped[str] = mapped_column(Text())

    category: Mapped[str] = mapped_column(String(32), default="event")

    # Where this came from. "user" means the user said it; nothing else
    # may claim to be the user.
    source: Mapped[str] = mapped_column(String(16), default="user")

    importance: Mapped[float] = mapped_column(Float(), default=0.5)

    confidence: Mapped[float] = mapped_column(Float(), default=0.5)

    occurred_at: Mapped[str] = mapped_column(default=timestamp_now)

    created_at: Mapped[str] = mapped_column(default=timestamp_now)


class UserModelEntry(Base):
    """
    One attribute of the long-term user model.

    Separate from UserFact on purpose. A UserFact is something the user
    told Aura, stored flat. A model entry carries the machinery the
    profile needs and a flat fact does not: whether it is *confirmed* or
    merely *inferred*, how confident Aura is, when it was last
    corroborated, and the window over which it is even valid.

    `status` is the field that keeps Aura honest. An inference may be
    repeated as an inference and never as a fact, and nothing in the
    system may promote one to the other on its own - only the user can.

    `valid_from` / `valid_until` carry time-sensitivity. A stable trait
    has neither. "Currently working on Phase 8" has an end, and a model
    entry that has passed its `valid_until` stops being authoritative
    without having to be deleted.
    """

    __tablename__ = "user_model"

    __table_args__ = (
        UniqueConstraint("key", name="uq_user_model_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Namespaced: "identity.primary_language", "personality.curiosity".
    key: Mapped[str] = mapped_column(String(96), index=True)

    value: Mapped[str] = mapped_column(Text())

    category: Mapped[str] = mapped_column(String(32), default="identity")

    # "confirmed" | "inferred". Absence of a row is "unknown"; see
    # memory.user_model.Status.
    status: Mapped[str] = mapped_column(String(16), default="inferred")

    confidence: Mapped[float] = mapped_column(Float(), default=0.5)

    source: Mapped[str] = mapped_column(String(24), default="user")

    created_at: Mapped[str] = mapped_column(default=timestamp_now)

    updated_at: Mapped[str] = mapped_column(default=timestamp_now)

    # When the user last said this was still true. Null for an inference
    # the user has never corroborated.
    last_confirmed_at: Mapped[str | None] = mapped_column(
        String(32), default=None, nullable=True
    )

    valid_from: Mapped[str | None] = mapped_column(
        String(32), default=None, nullable=True
    )

    valid_until: Mapped[str | None] = mapped_column(
        String(32), default=None, nullable=True
    )


class SemanticVector(Base):
    """
    One embedding of one episodic memory, beside the memory itself.

    Deliberately the simplest thing that satisfies the contract: a
    table in the SAME SQLite database, not a vector database. Reasons
    recorded in .Codex/decisions.md: the candidate pool is already
    bounded (`retrieval_scope`), so cosine over a few hundred vectors
    in process is fast; one database keeps backup, deletion and
    portability honest; and no new dependency enters the tree.

    `provider`, `model`, `dimensions` and `version` travel WITH the
    vector. Two vectors are comparable only when all four match, so a
    model change makes the old rows stale (ignored, reported) rather
    than silently mixed into a search across incompatible spaces.

    `vector` is a float32 blob (stdlib `array`), normalized at write
    time by the providers that normalize, so cosine is a dot product.

    This row references the episodic memory by id but is NOT a foreign
    key with a cascade - `EpisodicStore.forget` deletes the memory and
    leaves this row behind, and the retriever's inner join makes an
    orphan structurally unreturnable. A deleted memory cannot be
    resurrected by stale vectors; that invariant is enforced by the
    query shape, not by hoping a cleanup ran.
    """

    __tablename__ = "semantic_vectors"

    __table_args__ = (
        UniqueConstraint(
            "memory_id", "provider", "model", "version",
            name="uq_semantic_vector_memory_space",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    memory_id: Mapped[int] = mapped_column(index=True)

    provider: Mapped[str] = mapped_column(String(32))

    model: Mapped[str] = mapped_column(String(96))

    dimensions: Mapped[int] = mapped_column()

    version: Mapped[str] = mapped_column(String(16), default="1")

    vector: Mapped[bytes] = mapped_column(LargeBinary())

    created_at: Mapped[str] = mapped_column(default=timestamp_now)
