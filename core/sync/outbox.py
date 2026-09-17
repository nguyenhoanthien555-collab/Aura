"""
Outbox buffer and retry state machine for AURA P4.
Ensures at-least-once, durable delivery without data loss on timeout.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from typing import Any

from core.logger import logger
from core.sync.models import OutboxStatus, SyncEvent
from memory.models import SyncEventRecord, SyncOutboxRecord
from memory.sqlite import SessionLocal, db_lock


class OutboxManager:
    def __init__(self, session_factory=SessionLocal):
        self._session_factory = session_factory

    def get_pending(self, limit: int = 50, ignore_backoff: bool = False) -> list[tuple[SyncOutboxRecord, SyncEvent]]:
        """Fetch pending outbox records ready for transmission."""
        now_iso = datetime.now().isoformat(timespec="seconds")
        with db_lock:
            session = self._session_factory()
            try:
                # Include items with next_retry_at <= now or empty
                records = (
                    session.query(SyncOutboxRecord)
                    .filter(
                        SyncOutboxRecord.status.in_([OutboxStatus.PENDING.value, OutboxStatus.SENDING.value]),
                    )
                    .order_by(SyncOutboxRecord.outbox_id.asc())
                    .limit(limit)
                    .all()
                )

                results = []
                for rec in records:
                    if not ignore_backoff and rec.next_retry_at and rec.next_retry_at > now_iso:
                        continue
                    evt_rec = session.query(SyncEventRecord).filter_by(event_id=rec.event_id).first()
                    if evt_rec:
                        evt = SyncEvent(
                            event_id=evt_rec.event_id,
                            origin_node_id=evt_rec.origin_node_id,
                            event_type=evt_rec.event_type,
                            entity_type=evt_rec.entity_type,
                            entity_id=evt_rec.entity_id,
                            schema_version=evt_rec.schema_version,
                            created_at=evt_rec.created_at,
                            logical_sequence=evt_rec.logical_sequence,
                            payload=json.loads(evt_rec.payload_json or "{}"),
                            payload_hash=evt_rec.payload_hash,
                            parent_event_id=evt_rec.parent_event_id,
                            provenance=json.loads(evt_rec.provenance_json or "{}"),
                            received_at=evt_rec.received_at,
                        )
                        results.append((rec, evt))
                return results
            finally:
                session.close()

    def mark_sending(self, event_ids: list[str]):
        """Transition events to SENDING state."""
        now_iso = datetime.now().isoformat(timespec="seconds")
        with db_lock:
            session = self._session_factory()
            try:
                for eid in event_ids:
                    rec = session.query(SyncOutboxRecord).filter_by(event_id=eid).first()
                    if rec:
                        rec.status = OutboxStatus.SENDING.value
                        rec.attempts += 1
                        rec.last_attempt_at = now_iso
                session.commit()
            except Exception:
                session.rollback()
            finally:
                session.close()

    def mark_acknowledged(self, event_ids: list[str]):
        """Durably mark events as ACKNOWLEDGED."""
        now_iso = datetime.now().isoformat(timespec="seconds")
        with db_lock:
            session = self._session_factory()
            try:
                for eid in event_ids:
                    rec = session.query(SyncOutboxRecord).filter_by(event_id=eid).first()
                    if rec:
                        rec.status = OutboxStatus.ACKNOWLEDGED.value
                        rec.acknowledged_at = now_iso
                session.commit()
            except Exception:
                session.rollback()
            finally:
                session.close()

    def mark_retry(self, event_id: str, error_message: str, max_backoff: float = 120.0):
        """
        Record failure, calculate exponential backoff with jitter, revert to PENDING.
        CRITICAL: Never deletes event on timeout/error!
        """
        with db_lock:
            session = self._session_factory()
            try:
                rec = session.query(SyncOutboxRecord).filter_by(event_id=event_id).first()
                if rec:
                    rec.status = OutboxStatus.PENDING.value
                    rec.error_message = error_message
                    if max_backoff <= 0.0:
                        rec.next_retry_at = None
                    else:
                        # Exponential backoff with jitter: min(max_backoff, 2^attempts + jitter)
                        backoff_secs = min(max_backoff, (2 ** min(rec.attempts, 7)) + random.uniform(0.1, 1.0))
                        next_retry = datetime.now() + timedelta(seconds=backoff_secs)
                        rec.next_retry_at = next_retry.isoformat(timespec="seconds")
                session.commit()
            except Exception:
                session.rollback()
            finally:
                session.close()

    def mark_quarantined(self, event_id: str, reason: str):
        """Mark permanently quarantined due to integrity failure."""
        with db_lock:
            session = self._session_factory()
            try:
                rec = session.query(SyncOutboxRecord).filter_by(event_id=event_id).first()
                if rec:
                    rec.status = OutboxStatus.QUARANTINED.value
                    rec.error_message = f"QUARANTINED: {reason}"
                session.commit()
            except Exception:
                session.rollback()
            finally:
                session.close()

    def pending_count(self) -> int:
        with db_lock:
            session = self._session_factory()
            try:
                return (
                    session.query(SyncOutboxRecord)
                    .filter_by(status=OutboxStatus.PENDING.value)
                    .count()
                )
            finally:
                session.close()

    def prune_acknowledged(self, max_records_to_keep: int = 5000) -> int:
        """
        Prune older acknowledged outbox records to bound queue storage growth.
        Never removes PENDING, SENDING, or QUARANTINED records.
        """
        with db_lock:
            session = self._session_factory()
            try:
                total_ack = (
                    session.query(SyncOutboxRecord)
                    .filter_by(status=OutboxStatus.ACKNOWLEDGED.value)
                    .count()
                )
                if total_ack <= max_records_to_keep:
                    return 0

                excess = total_ack - max_records_to_keep
                oldest_ids = (
                    session.query(SyncOutboxRecord.outbox_id)
                    .filter_by(status=OutboxStatus.ACKNOWLEDGED.value)
                    .order_by(SyncOutboxRecord.outbox_id.asc())
                    .limit(excess)
                    .all()
                )
                if oldest_ids:
                    id_list = [row[0] for row in oldest_ids]
                    deleted = (
                        session.query(SyncOutboxRecord)
                        .filter(SyncOutboxRecord.outbox_id.in_(id_list))
                        .delete(synchronize_session=False)
                    )
                    session.commit()
                    logger.info("Pruned %d acknowledged outbox records", deleted)
                    return deleted
                return 0
            except Exception as e:
                session.rollback()
                logger.warning("Failed to prune acknowledged outbox records: %s", e)
                return 0
            finally:
                session.close()

