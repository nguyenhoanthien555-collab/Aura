"""
Conflict detection, deterministic resolution, and quarantine for AURA P4.
Prevents silent data overwrites and preserves dual histories.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from core.logger import logger
from core.sync.models import ConflictType
from memory.models import SyncConflictRecord
from memory.sqlite import SessionLocal, db_lock, init_sync_tables


class ConflictManager:
    def __init__(self, session_factory=SessionLocal):
        self._session_factory = session_factory
        init_sync_tables()

    def record_conflict(
        self,
        event_id: str,
        origin_node_id: str,
        conflict_type: str,
        existing_hash: str,
        incoming_hash: str,
        entity_type: str = "",
        entity_id: str = "",
        reason: str = "",
    ) -> SyncConflictRecord:
        """Record an integrity or concurrent update conflict for quarantine."""
        conflict_id = f"conf_{uuid.uuid4().hex[:12]}"
        now_iso = datetime.now().isoformat(timespec="seconds")

        with db_lock:
            session = self._session_factory()
            try:
                rec = SyncConflictRecord(
                    conflict_id=conflict_id,
                    event_id=event_id,
                    origin_node_id=origin_node_id,
                    conflict_type=conflict_type,
                    existing_hash=existing_hash,
                    incoming_hash=incoming_hash,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    reason=reason,
                    status="QUARANTINED",
                    created_at=now_iso,
                )
                session.add(rec)
                session.commit()
                logger.warning(
                    f"Sync conflict recorded: id={conflict_id}, event={event_id}, type={conflict_type}, reason={reason}"
                )
                return rec
            except Exception as e:
                session.rollback()
                logger.error(f"Failed to record sync conflict: {e}")
                raise
            finally:
                session.close()

    def list_conflicts(self, status: str = "QUARANTINED") -> list[dict]:
        with db_lock:
            session = self._session_factory()
            try:
                records = session.query(SyncConflictRecord).filter_by(status=status).all()
                return [
                    {
                        "conflict_id": r.conflict_id,
                        "event_id": r.event_id,
                        "origin_node_id": r.origin_node_id,
                        "conflict_type": r.conflict_type,
                        "existing_hash": r.existing_hash,
                        "incoming_hash": r.incoming_hash,
                        "entity_type": r.entity_type,
                        "entity_id": r.entity_id,
                        "reason": r.reason,
                        "status": r.status,
                        "created_at": r.created_at,
                        "resolved_at": r.resolved_at,
                    }
                    for r in records
                ]
            finally:
                session.close()

    def resolve_conflict(self, conflict_id: str, resolution: str) -> bool:
        with db_lock:
            session = self._session_factory()
            try:
                rec = session.query(SyncConflictRecord).filter_by(conflict_id=conflict_id).first()
                if rec:
                    rec.status = "RESOLVED"
                    rec.resolved_at = datetime.now().isoformat(timespec="seconds")
                    rec.reason = f"{rec.reason} | RESOLUTION: {resolution}"
                    session.commit()
                    return True
                return False
            except Exception:
                session.rollback()
                return False
            finally:
                session.close()
