"""
Inbox processor for receiving, validating, deduping, and persisting incoming events.
Follows the mandatory order: Authenticate -> Validate -> Hash Verify -> Idempotency -> Persist -> Dispatch -> ACK.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable

from core.logger import logger
from core.sync.conflict import ConflictManager
from core.sync.models import ConflictType, InboxStatus, SyncEvent, compute_payload_hash
from memory.models import SyncCursorRecord, SyncEventRecord, SyncInboxRecord
from memory.sqlite import SessionLocal, db_lock, init_sync_tables


class InboxProcessor:
    def __init__(
        self,
        session_factory=SessionLocal,
        conflict_manager: ConflictManager | None = None,
        dispatch_handler: Callable[[SyncEvent], None] | None = None,
    ):
        self._session_factory = session_factory
        self.conflicts = conflict_manager or ConflictManager(session_factory)
        self.dispatch_handler = dispatch_handler
        init_sync_tables()

    def process_incoming_batch(
        self,
        events: list[SyncEvent],
        receiver_node_id: str,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """
        Process a batch of incoming events.
        Returns:
            (acknowledged_event_ids, list_of_conflicts)
        """
        acknowledged = []
        conflicts = []

        for event in events:
            # 1. Validate schema
            if not event.event_id or not event.origin_node_id or not event.entity_type:
                conflicts.append({"event_id": event.event_id, "error": "Invalid event schema"})
                continue

            # 2. Verify payload hash integrity
            if not event.verify_hash():
                expected_hash = compute_payload_hash(event.payload)
                self.conflicts.record_conflict(
                    event_id=event.event_id,
                    origin_node_id=event.origin_node_id,
                    conflict_type=ConflictType.HASH_MISMATCH.value,
                    existing_hash=event.payload_hash,
                    incoming_hash=expected_hash,
                    entity_type=event.entity_type,
                    entity_id=event.entity_id,
                    reason="Tampered payload: payload_hash does not match computed SHA-256",
                )
                conflicts.append({"event_id": event.event_id, "error": "Hash integrity check failed"})
                continue

            # 3. Check idempotency and persist
            with db_lock:
                session = self._session_factory()
                try:
                    existing = session.query(SyncEventRecord).filter_by(event_id=event.event_id).first()
                    if existing:
                        # Case A: duplicate with same hash -> idempotent success
                        if existing.payload_hash == event.payload_hash:
                            acknowledged.append(event.event_id)
                            continue
                        else:
                            # Case B: duplicate with DIFFERENT hash -> Conflict quarantine!
                            self.conflicts.record_conflict(
                                event_id=event.event_id,
                                origin_node_id=event.origin_node_id,
                                conflict_type=ConflictType.HASH_MISMATCH.value,
                                existing_hash=existing.payload_hash,
                                incoming_hash=event.payload_hash,
                                entity_type=event.entity_type,
                                entity_id=event.entity_id,
                                reason="Existing event_id with differing payload hash",
                            )
                            conflicts.append({"event_id": event.event_id, "error": "Duplicate ID with conflicting hash"})
                            continue

                    # Determine sequence number if not provided
                    if event.logical_sequence <= 0:
                        max_seq = (
                            session.query(SyncEventRecord.logical_sequence)
                            .order_by(SyncEventRecord.logical_sequence.desc())
                            .first()
                        )
                        event.logical_sequence = (max_seq[0] + 1) if max_seq else 1

                    # Persist event
                    now_iso = datetime.now().isoformat(timespec="seconds")
                    event_rec = SyncEventRecord(
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
                        received_at=now_iso,
                    )
                    session.add(event_rec)

                    # Persist inbox tracking
                    inbox_rec = SyncInboxRecord(
                        event_id=event.event_id,
                        origin_node_id=event.origin_node_id,
                        payload_hash=event.payload_hash,
                        status=InboxStatus.PROCESSED.value,
                        processed_at=now_iso,
                        created_at=now_iso,
                    )
                    session.add(inbox_rec)

                    # Update cursor
                    cursor = (
                        session.query(SyncCursorRecord)
                        .filter_by(node_id=receiver_node_id, peer_node_id=event.origin_node_id)
                        .first()
                    )
                    if not cursor:
                        cursor = SyncCursorRecord(
                            node_id=receiver_node_id,
                            peer_node_id=event.origin_node_id,
                            last_sequence=event.logical_sequence,
                            last_event_id=event.event_id,
                            updated_at=now_iso,
                        )
                        session.add(cursor)
                    else:
                        if event.logical_sequence > cursor.last_sequence:
                            cursor.last_sequence = event.logical_sequence
                            cursor.last_event_id = event.event_id
                            cursor.updated_at = now_iso

                    session.commit()
                    acknowledged.append(event.event_id)

                    # 4. Dispatch to domain handler if present
                    if self.dispatch_handler:
                        try:
                            self.dispatch_handler(event)
                        except Exception as de:
                            logger.error(f"Error in sync dispatch handler for {event.event_id}: {de}")

                except Exception as e:
                    session.rollback()
                    logger.error(f"Error processing incoming event {event.event_id}: {e}")
                    conflicts.append({"event_id": event.event_id, "error": str(e)})
                finally:
                    session.close()

        return acknowledged, conflicts

    def prune_processed(self, max_records_to_keep: int = 5000) -> int:
        """Prune older processed inbox records to bound storage growth."""
        with db_lock:
            session = self._session_factory()
            try:
                total_proc = (
                    session.query(SyncInboxRecord)
                    .filter_by(status=InboxStatus.PROCESSED.value)
                    .count()
                )
                if total_proc <= max_records_to_keep:
                    return 0

                excess = total_proc - max_records_to_keep
                oldest_ids = (
                    session.query(SyncInboxRecord.inbox_id)
                    .filter_by(status=InboxStatus.PROCESSED.value)
                    .order_by(SyncInboxRecord.inbox_id.asc())
                    .limit(excess)
                    .all()
                )
                if oldest_ids:
                    id_list = [row[0] for row in oldest_ids]
                    deleted = (
                        session.query(SyncInboxRecord)
                        .filter(SyncInboxRecord.inbox_id.in_(id_list))
                        .delete(synchronize_session=False)
                    )
                    session.commit()
                    logger.info("Pruned %d processed inbox records", deleted)
                    return deleted
                return 0
            except Exception as e:
                session.rollback()
                logger.warning("Failed to prune processed inbox records: %s", e)
                return 0
            finally:
                session.close()

