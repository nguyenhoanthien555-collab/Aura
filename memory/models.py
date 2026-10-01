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
from sqlalchemy import Float, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint
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


class CompanionMemoryRecord(Base):
    """
    Durable backing for the companion stores (facts, preferences, goals,
    projects, coding style, highlights) that used to live only in process
    memory and vanished on restart.

    One generic table rather than six: `kind` names which store owns the
    row, `key` is that store's dedup key (topic / title / name / language,
    or a unique id for append-only highlights), and `payload` is the
    JSON-serialised dataclass. Newest write to a (kind, key) wins, matching
    the in-memory "newest wins" upsert. A future per-kind schema, if one is
    ever justified, can migrate out of here without the readers changing -
    they only ever see the rendered dataclass.
    """

    __tablename__ = "companion_memory"

    __table_args__ = (
        UniqueConstraint("kind", "key", name="uq_companion_memory_kind_key"),
        Index("ix_companion_memory_kind", "kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    key: Mapped[str] = mapped_column(String(256))
    payload: Mapped[str] = mapped_column(Text(), default="{}")
    updated_at: Mapped[str] = mapped_column(default=timestamp_now)


class DurableTaskRecord(Base):
    """
    One user task spanning multiple steps and runs.
    Survives reconnect, timeout, and process restart.
    """

    __tablename__ = "durable_tasks"

    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(128), default="default", index=True)
    goal: Mapped[str] = mapped_column(Text())
    status: Mapped[str] = mapped_column(String(32), default="CREATED", index=True)
    plan_json: Mapped[str] = mapped_column(Text(), default="{}")
    current_step_id: Mapped[str] = mapped_column(String(64), default="")
    attempt: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str] = mapped_column(Text(), default="")
    recovery_state: Mapped[str] = mapped_column(Text(), default="")
    metadata_json: Mapped[str] = mapped_column(Text(), default="{}")
    created_at: Mapped[str] = mapped_column(default=timestamp_now)
    updated_at: Mapped[str] = mapped_column(default=timestamp_now)


class DurableStepRecord(Base):
    """
    One step / checkpoint of a durable task.
    """

    __tablename__ = "durable_task_steps"

    step_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), index=True)
    step_index: Mapped[int] = mapped_column(default=0)
    name: Mapped[str] = mapped_column(String(128), default="")
    tool: Mapped[str] = mapped_column(String(128), default="")
    arguments_json: Mapped[str] = mapped_column(Text(), default="{}")
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    side_effect: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    attempt: Mapped[int] = mapped_column(default=0)
    result_json: Mapped[str] = mapped_column(Text(), default="{}")
    evidence_json: Mapped[str] = mapped_column(Text(), default="[]")
    created_at: Mapped[str] = mapped_column(default=timestamp_now)
    updated_at: Mapped[str] = mapped_column(default=timestamp_now)


class ToolProvenanceRecord(Base):
    """
    Audit and provenance record for dynamically created or extended tools.
    """

    __tablename__ = "tool_provenance"

    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_tool_provenance_name_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[int] = mapped_column(default=1)
    gap_id: Mapped[str] = mapped_column(String(64), default="")
    manifest_json: Mapped[str] = mapped_column(Text(), default="{}")
    source_code: Mapped[str] = mapped_column(Text(), default="")
    source_digest: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", index=True)
    validation_json: Mapped[str] = mapped_column(Text(), default="{}")
    created_at: Mapped[str] = mapped_column(default=timestamp_now)
    updated_at: Mapped[str] = mapped_column(default=timestamp_now)


class DurableConfirmationRecord(Base):
    """
    Durable human-in-the-loop confirmation request for a dangerous/sensitive step.
    Binds exact task, step, tool, and argument payload to prevent spoofing or replay.
    """

    __tablename__ = "durable_confirmations"

    confirmation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), index=True)
    step_id: Mapped[str] = mapped_column(String(64), index=True)
    tool: Mapped[str] = mapped_column(String(128), default="")
    risk: Mapped[str] = mapped_column(String(32), default="dangerous")
    side_effect: Mapped[str] = mapped_column(String(32), default="mutating")
    description: Mapped[str] = mapped_column(Text(), default="")
    arguments_json: Mapped[str] = mapped_column(Text(), default="{}")
    redacted_arguments_json: Mapped[str] = mapped_column(Text(), default="{}")
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    decision: Mapped[str] = mapped_column(String(32), default="")
    decision_by: Mapped[str] = mapped_column(String(128), default="")
    decided_at: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[str] = mapped_column(default=timestamp_now)
    expires_at: Mapped[str] = mapped_column(String(32), default="")
    fingerprint: Mapped[str] = mapped_column(String(64), default="")


class DurableClarificationRecord(Base):
    """
    Durable human-in-the-loop clarification request for an ambiguous goal.
    Preserves original goal and structured questions without discarding the task.
    """

    __tablename__ = "durable_clarifications"

    clarification_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), index=True)
    goal: Mapped[str] = mapped_column(Text(), default="")
    questions_json: Mapped[str] = mapped_column(Text(), default="[]")
    answers_json: Mapped[str] = mapped_column(Text(), default="{}")
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    created_at: Mapped[str] = mapped_column(default=timestamp_now)
    answered_at: Mapped[str] = mapped_column(String(32), default="")


class AuraExperienceRecord(Base):
    """
    Persisted experience for AURA self-learning and continual improvement.
    Captures input, decision, tool calls, outcomes, evidence, verification, and privacy classification.
    """

    __tablename__ = "aura_experiences"

    experience_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(128), default="default", index=True)
    task_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    run_id: Mapped[str] = mapped_column(String(64), default="")
    input_text: Mapped[str] = mapped_column(Text(), default="")
    model_decision: Mapped[str] = mapped_column(String(64), default="ANSWER")
    selected_tool: Mapped[str] = mapped_column(String(128), default="")
    arguments_json: Mapped[str] = mapped_column(Text(), default="{}")
    tool_result_json: Mapped[str] = mapped_column(Text(), default="{}")
    evidence_json: Mapped[str] = mapped_column(Text(), default="[]")
    verifier_result: Mapped[str] = mapped_column(String(32), default="UNVERIFIED")
    final_response: Mapped[str] = mapped_column(Text(), default="")
    outcome: Mapped[str] = mapped_column(String(32), default="SUCCESS")
    user_feedback: Mapped[str] = mapped_column(String(64), default="")
    privacy_class: Mapped[str] = mapped_column(String(32), default="INTERNAL")
    learning_eligible: Mapped[bool] = mapped_column(default=True)
    quality_score: Mapped[float] = mapped_column(Float(), default=0.5)
    category: Mapped[str] = mapped_column(String(64), default="general")
    taxonomy_tag: Mapped[str] = mapped_column(String(32), default="UNVERIFIED")
    created_at: Mapped[str] = mapped_column(default=timestamp_now)


class BrainVersionRecord(Base):
    """
    Durable record of versioned Brain packages, lifecycle status, and evaluation results.
    """

    __tablename__ = "brain_versions"

    brain_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    status: Mapped[str] = mapped_column(String(32), default="CANDIDATE", index=True)
    manifest_json: Mapped[str] = mapped_column(Text(), default="{}")
    evaluation_json: Mapped[str] = mapped_column(Text(), default="{}")
    created_at: Mapped[str] = mapped_column(default=timestamp_now)
    updated_at: Mapped[str] = mapped_column(default=timestamp_now)


class TrainingJobRecord(Base):
    """
    Durable record of self-learning training jobs executed in process-isolated workers.
    """

    __tablename__ = "training_jobs"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    base_brain_id: Mapped[str] = mapped_column(String(64), default="")
    output_brain_id: Mapped[str] = mapped_column(String(64), default="")
    dataset_path: Mapped[str] = mapped_column(Text(), default="")
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    metrics_json: Mapped[str] = mapped_column(Text(), default="{}")
    logs: Mapped[str] = mapped_column(Text(), default="")
    started_at: Mapped[str] = mapped_column(default=timestamp_now)
    completed_at: Mapped[str] = mapped_column(String(32), default="")


# ---------------------------------------------------------------------------
# P4 — Distributed Continuity & Always-Sync models
# ---------------------------------------------------------------------------

class SyncNodeRecord(Base):
    """
    Physical or logical node participating in distributed AURA synchronization.
    """
    __tablename__ = "sync_nodes"

    node_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    node_type: Mapped[str] = mapped_column(String(32), default="LAPTOP")
    installation_id: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="ONLINE")
    schema_version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[str] = mapped_column(default=timestamp_now)
    last_seen: Mapped[str] = mapped_column(default=timestamp_now)
    metadata_json: Mapped[str] = mapped_column(Text(), default="{}")


class SyncEventRecord(Base):
    """
    Immutable, event-sourced record for distributed synchronization.
    Cryptographically authenticated via payload_hash.
    """
    __tablename__ = "sync_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    origin_node_id: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str] = mapped_column(String(64), index=True)
    entity_id: Mapped[str] = mapped_column(String(128), index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[str] = mapped_column(default=timestamp_now)
    logical_sequence: Mapped[int] = mapped_column(default=0, index=True)
    payload_json: Mapped[str] = mapped_column(Text(), default="{}")
    payload_hash: Mapped[str] = mapped_column(String(64), index=True)
    parent_event_id: Mapped[str] = mapped_column(String(64), default="", nullable=True)
    provenance_json: Mapped[str] = mapped_column(Text(), default="{}")
    received_at: Mapped[str] = mapped_column(default=timestamp_now)


class SyncOutboxRecord(Base):
    """
    Outbox buffer ensuring reliable, at-least-once delivery to peers / relay.
    Events remain PENDING across restarts until durably ACKed.
    """
    __tablename__ = "sync_outbox"

    outbox_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    target_node_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    last_attempt_at: Mapped[str] = mapped_column(String(32), default="")
    next_retry_at: Mapped[str] = mapped_column(String(32), default="")
    error_message: Mapped[str] = mapped_column(Text(), default="")
    created_at: Mapped[str] = mapped_column(default=timestamp_now)
    acknowledged_at: Mapped[str] = mapped_column(String(32), default="")


class SyncInboxRecord(Base):
    """
    Inbox buffer tracking incoming replication events and idempotency.
    """
    __tablename__ = "sync_inbox"

    inbox_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    origin_node_id: Mapped[str] = mapped_column(String(64), index=True)
    payload_hash: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    processed_at: Mapped[str] = mapped_column(String(32), default="")
    error_message: Mapped[str] = mapped_column(Text(), default="")
    created_at: Mapped[str] = mapped_column(default=timestamp_now)


class SyncCursorRecord(Base):
    """
    Tracks the high-watermark cursor for peer-to-peer or relay sync.
    """
    __tablename__ = "sync_cursors"

    __table_args__ = (
        UniqueConstraint("node_id", "peer_node_id", name="uq_sync_cursors_peer"),
    )

    cursor_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(String(64), index=True)
    peer_node_id: Mapped[str] = mapped_column(String(64), index=True)
    last_sequence: Mapped[int] = mapped_column(default=0)
    last_event_id: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[str] = mapped_column(default=timestamp_now)


class SyncConflictRecord(Base):
    """
    Quarantine record for hash tampering or concurrent mutation conflicts.
    """
    __tablename__ = "sync_conflicts"

    conflict_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), index=True)
    origin_node_id: Mapped[str] = mapped_column(String(64), default="")
    conflict_type: Mapped[str] = mapped_column(String(32), default="HASH_MISMATCH")
    existing_hash: Mapped[str] = mapped_column(String(64), default="")
    incoming_hash: Mapped[str] = mapped_column(String(64), default="")
    entity_type: Mapped[str] = mapped_column(String(64), default="")
    entity_id: Mapped[str] = mapped_column(String(128), default="")
    reason: Mapped[str] = mapped_column(Text(), default="")
    status: Mapped[str] = mapped_column(String(32), default="QUARANTINED")
    created_at: Mapped[str] = mapped_column(default=timestamp_now)
    resolved_at: Mapped[str] = mapped_column(String(32), default="")


class AgentRunRecord(Base):
    """
    Durable storage for AgentRun executions.
    Preserves loop state across server restarts, network drops, and device reconnects.
    """
    __tablename__ = "agent_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    goal: Mapped[str] = mapped_column(Text())
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    stop_reason: Mapped[str] = mapped_column(String(64), default="", nullable=True)
    stop_detail: Mapped[str] = mapped_column(Text(), default="")
    rounds: Mapped[int] = mapped_column(default=0)
    tool_call_count: Mapped[int] = mapped_column(default=0)
    consecutive_failures: Mapped[int] = mapped_column(default=0)
    verify_rounds: Mapped[int] = mapped_column(default=0)
    requires_observation: Mapped[bool] = mapped_column(default=False)
    observed_ok: Mapped[int] = mapped_column(default=0)
    unobserved_rounds: Mapped[int] = mapped_column(default=0)
    messages_json: Mapped[str] = mapped_column(Text(), default="[]")
    unverified_json: Mapped[str] = mapped_column(Text(), default="[]")
    created_at: Mapped[float] = mapped_column(Float(), default=0.0)
    updated_at: Mapped[str] = mapped_column(default=timestamp_now)


class ToolInvocationRecord(Base):
    """
    Durable invocation ledger for tool executions.
    Guarantees replay protection and crash/restart recovery across processes.
    """
    __tablename__ = "tool_invocations"

    invocation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    tool_call_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    tool: Mapped[str] = mapped_column(String(64), index=True)
    lifecycle_state: Mapped[str] = mapped_column(String(32), default="RECEIVED", index=True)
    request_hash: Mapped[str] = mapped_column(String(64), default="")
    arguments_json: Mapped[str] = mapped_column(Text(), default="{}")
    result_json: Mapped[str] = mapped_column(Text(), default="")
    evidence_hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[str] = mapped_column(default=timestamp_now)
    updated_at: Mapped[str] = mapped_column(default=timestamp_now)


class EntityNode(Base):
    """
    An entity in Aura's personalized knowledge graph (Person, Project, Topic, Preference, Location, etc.).
    """
    __tablename__ = "entity_nodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    entity_type: Mapped[str] = mapped_column(String(32), default="CONCEPT", index=True)
    description: Mapped[str] = mapped_column(Text(), default="")
    properties_json: Mapped[str] = mapped_column(Text(), default="{}")
    created_at: Mapped[str] = mapped_column(default=timestamp_now)
    updated_at: Mapped[str] = mapped_column(default=timestamp_now)


class EntityRelation(Base):
    """
    A directed relation between two entities in Aura's knowledge graph.
    (e.g., Thien -> LIKES -> Bac Xiu, Aura -> CREATED_BY -> Thien).
    """
    __tablename__ = "entity_relations"
    __table_args__ = (
        UniqueConstraint("source_id", "relation", "target_id", name="uq_entity_relation"),
        Index("ix_relation_source", "source_id"),
        Index("ix_relation_target", "target_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("entity_nodes.id", ondelete="CASCADE"))
    relation: Mapped[str] = mapped_column(String(64), index=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("entity_nodes.id", ondelete="CASCADE"))
    confidence: Mapped[float] = mapped_column(Float(), default=1.0)
    source: Mapped[str] = mapped_column(String(32), default="user")
    created_at: Mapped[str] = mapped_column(default=timestamp_now)
