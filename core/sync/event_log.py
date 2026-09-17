"""
Durable, append-only local event log for AURA P4.
Backed by SQLite with transaction isolation.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from core.logger import logger
from core.sync.models import SyncEvent, compute_payload_hash
from memory.models import SyncEventRecord, SyncOutboxRecord
from memory.sqlite import SessionLocal, db_lock, init_sync_tables


class EventLog:
    def __init__(self, session_factory=SessionLocal):
        self._session_factory = session_factory
        init_sync_tables()

    def append_event(
        self,
        event: SyncEvent,
        target_node_id: str = "",
        enqueue_outbox: bool = True,
    ) -> SyncEvent:
        """
        Durably append an event to the local event log.
        Optionally queues in the sync outbox for replication.
        """
        # Ensure hash integrity
        if not event.payload_hash:
            event.payload_hash = compute_payload_hash(event.payload)
        elif not event.verify_hash():
            raise ValueError(f"Payload hash verification failed for event {event.event_id}")

        with db_lock:
            session = self._session_factory()
            try:
                # Check for existing event
                existing = session.query(SyncEventRecord).filter_by(event_id=event.event_id).first()
                if existing:
                    if existing.payload_hash != event.payload_hash:
                        raise ValueError(
                            f"Integrity conflict: Event {event.event_id} already exists with different hash!"
                        )
                    # Idempotent - return existing
                    return SyncEvent(
                        event_id=existing.event_id,
                        origin_node_id=existing.origin_node_id,
                        event_type=existing.event_type,
                        entity_type=existing.entity_type,
                        entity_id=existing.entity_id,
                        schema_version=existing.schema_version,
                        created_at=existing.created_at,
                        logical_sequence=existing.logical_sequence,
                        payload=json.loads(existing.payload_json or "{}"),
                        payload_hash=existing.payload_hash,
                        parent_event_id=existing.parent_event_id,
                        provenance=json.loads(existing.provenance_json or "{}"),
                        received_at=existing.received_at,
                    )

                # Determine sequence number if not provided
                if event.logical_sequence <= 0:
                    max_seq = (
                        session.query(SyncEventRecord.logical_sequence)
                        .filter_by(origin_node_id=event.origin_node_id)
                        .order_by(SyncEventRecord.logical_sequence.desc())
                        .first()
                    )
                    event.logical_sequence = (max_seq[0] + 1) if max_seq else 1

                rec = SyncEventRecord(
                    event_id=event.event_id,
                    origin_node_id=event.origin_node_id,
                    event_type=event.event_type,
                    entity_type=event.entity_type,
                    entity_id=event.entity_id,
                    schema_version=event.schema_version,
                    created_at=event.created_at,
                    logical_sequence=event.logical_sequence,
                    payload_json=json.dumps(event.payload, ensure_ascii=False),
                    payload_hash=event.payload_hash,
                    parent_event_id=event.parent_event_id or "",
                    provenance_json=json.dumps(event.provenance, ensure_ascii=False),
                    received_at=event.received_at or datetime.now().isoformat(timespec="seconds"),
                )
                session.add(rec)

                if enqueue_outbox:
                    outbox = SyncOutboxRecord(
                        event_id=event.event_id,
                        target_node_id=target_node_id,
                        status="PENDING",
                        attempts=0,
                        created_at=datetime.now().isoformat(timespec="seconds"),
                    )
                    session.add(outbox)

                session.commit()
                return event
            except Exception as e:
                session.rollback()
                logger.error(f"Failed to append sync event {event.event_id}: {e}")
                raise
            finally:
                session.close()

    def get_event(self, event_id: str) -> SyncEvent | None:
        with db_lock:
            session = self._session_factory()
            try:
                rec = session.query(SyncEventRecord).filter_by(event_id=event_id).first()
                if not rec:
                    return None
                return SyncEvent(
                    event_id=rec.event_id,
                    origin_node_id=rec.origin_node_id,
                    event_type=rec.event_type,
                    entity_type=rec.entity_type,
                    entity_id=rec.entity_id,
                    schema_version=rec.schema_version,
                    created_at=rec.created_at,
                    logical_sequence=rec.logical_sequence,
                    payload=json.loads(rec.payload_json or "{}"),
                    payload_hash=rec.payload_hash,
                    parent_event_id=rec.parent_event_id,
                    provenance=json.loads(rec.provenance_json or "{}"),
                    received_at=rec.received_at,
                )
            finally:
                session.close()

    def list_events(
        self,
        entity_type: str | None = None,
        origin_node_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SyncEvent]:
        with db_lock:
            session = self._session_factory()
            try:
                q = session.query(SyncEventRecord)
                if entity_type:
                    q = q.filter_by(entity_type=entity_type)
                if origin_node_id:
                    q = q.filter_by(origin_node_id=origin_node_id)
                q = q.order_by(SyncEventRecord.logical_sequence.asc()).offset(offset).limit(limit)
                records = q.all()
                return [
                    SyncEvent(
                        event_id=r.event_id,
                        origin_node_id=r.origin_node_id,
                        event_type=r.event_type,
                        entity_type=r.entity_type,
                        entity_id=r.entity_id,
                        schema_version=r.schema_version,
                        created_at=r.created_at,
                        logical_sequence=r.logical_sequence,
                        payload=json.loads(r.payload_json or "{}"),
                        payload_hash=r.payload_hash,
                        parent_event_id=r.parent_event_id,
                        provenance=json.loads(r.provenance_json or "{}"),
                        received_at=r.received_at,
                    )
                    for r in records
                ]
            finally:
                session.close()

    def get_events_after_cursor(
        self,
        origin_node_id: str | None = None,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[SyncEvent]:
        with db_lock:
            session = self._session_factory()
            try:
                q = session.query(SyncEventRecord)
                if origin_node_id:
                    q = q.filter_by(origin_node_id=origin_node_id)
                q = (
                    q.filter(SyncEventRecord.logical_sequence > after_sequence)
                    .order_by(SyncEventRecord.logical_sequence.asc())
                    .limit(limit)
                )
                records = q.all()
                return [
                    SyncEvent(
                        event_id=r.event_id,
                        origin_node_id=r.origin_node_id,
                        event_type=r.event_type,
                        entity_type=r.entity_type,
                        entity_id=r.entity_id,
                        schema_version=r.schema_version,
                        created_at=r.created_at,
                        logical_sequence=r.logical_sequence,
                        payload=json.loads(r.payload_json or "{}"),
                        payload_hash=r.payload_hash,
                        parent_event_id=r.parent_event_id,
                        provenance=json.loads(r.provenance_json or "{}"),
                        received_at=r.received_at,
                    )
                    for r in records
                ]
            finally:
                session.close()

    def count(self) -> int:
        with db_lock:
            session = self._session_factory()
            try:
                return session.query(SyncEventRecord).count()
            finally:
                session.close()
