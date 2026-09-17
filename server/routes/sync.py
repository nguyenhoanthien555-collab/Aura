"""
Durable Synchronization Relay Endpoints for AURA P4.
Provides authenticated, idempotent HTTP endpoints for cross-network continuity.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from core.logger import logger
from core.sync.conflict import ConflictManager
from core.sync.engine import SyncEngine
from core.sync.event_log import EventLog
from core.sync.identity import NodeIdentityManager
from core.sync.inbox import InboxProcessor
from core.sync.models import SyncEvent, SyncNode, SyncNodeType, SyncState
from memory.models import SyncCursorRecord, SyncNodeRecord
from memory.sqlite import SessionLocal, db_lock, init_sync_tables
from server.auth import verify_token

router = APIRouter(prefix="/api/sync", tags=["sync"])

_engine: SyncEngine | None = None


def get_sync_engine() -> SyncEngine:
    global _engine
    if _engine is None:
        init_sync_tables()
        _engine = SyncEngine(node_type=SyncNodeType.RELAY.value)
    return _engine


# ---------------------------------------------------------------------------
# Request / Response Schemas
# ---------------------------------------------------------------------------

class RegisterNodeRequest(BaseModel):
    node_id: str
    node_type: str = "ANDROID"
    installation_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class PushEventsRequest(BaseModel):
    node_id: str
    events: list[dict[str, Any]]


class AckEventsRequest(BaseModel):
    node_id: str
    event_ids: list[str]


class ResolveConflictRequest(BaseModel):
    resolution: str = "MERGED_MANUAL"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/register")
def register_node(req: RegisterNodeRequest, token: str = Depends(verify_token)):
    """Register or update an authenticated node identity."""
    init_sync_tables()
    now_iso = datetime.now().isoformat(timespec="seconds")
    with db_lock:
        session = SessionLocal()
        try:
            rec = session.query(SyncNodeRecord).filter_by(node_id=req.node_id).first()
            if not rec:
                rec = SyncNodeRecord(
                    node_id=req.node_id,
                    node_type=req.node_type,
                    installation_id=req.installation_id,
                    status=SyncState.ONLINE.value,
                    schema_version=1,
                    created_at=now_iso,
                    last_seen=now_iso,
                    metadata_json=json.dumps(req.metadata),
                )
                session.add(rec)
            else:
                rec.last_seen = now_iso
                rec.status = SyncState.ONLINE.value
                rec.metadata_json = json.dumps(req.metadata)
            session.commit()
            return {"status": "ok", "node_id": req.node_id, "last_seen": now_iso}
        except Exception as e:
            session.rollback()
            raise HTTPException(status_code=500, detail=f"Registration failed: {e}")
        finally:
            session.close()


@router.post("/events/push")
def push_events(req: PushEventsRequest, token: str = Depends(verify_token)):
    """
    Receive a batch of events from a remote node or peer.
    Validates, verifies hashes, checks idempotency, and persists.
    """
    engine = get_sync_engine()
    events = []
    for d in req.events:
        try:
            events.append(SyncEvent.from_dict(d))
        except Exception as e:
            logger.warning(f"Malformed event in push: {e}")

    acked_ids, conflicts = engine.inbox.process_incoming_batch(
        events=events,
        receiver_node_id=engine.node.node_id,
    )

    return {
        "status": "ok",
        "acknowledged": acked_ids,
        "conflicts": conflicts,
        "received_count": len(events),
    }


@router.get("/events/pull")
def pull_events(
    node_id: str = Query(..., description="Target node requesting events"),
    after_sequence: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    token: str = Depends(verify_token),
):
    """
    Pull events from the durable relay that originated from other nodes.
    """
    engine = get_sync_engine()
    events_after = engine.event_log.get_events_after_cursor(after_sequence=after_sequence, limit=limit * 2)

    # Filter out events originated by the requester itself
    filtered = [
        e.to_dict()
        for e in events_after
        if e.origin_node_id != node_id
    ][:limit]

    latest_seq = max((e.get("logical_sequence", 0) for e in filtered), default=after_sequence)
    has_more = len(filtered) >= limit

    return {
        "status": "ok",
        "events": filtered,
        "cursor": latest_seq,
        "has_more": has_more,
    }


@router.post("/events/ack")
def ack_events(req: AckEventsRequest, token: str = Depends(verify_token)):
    """Client confirms durable receipt and persistence of events."""
    engine = get_sync_engine()
    engine.outbox.mark_acknowledged(req.event_ids)
    return {"status": "ok", "acknowledged_count": len(req.event_ids)}


@router.get("/status")
def sync_status(token: str = Depends(verify_token)):
    """Diagnostic status for observability."""
    engine = get_sync_engine()
    return engine.get_diagnostics()


@router.get("/conflicts")
def list_conflicts(
    status: str = Query("QUARANTINED"),
    token: str = Depends(verify_token),
):
    """List quarantined sync conflicts."""
    engine = get_sync_engine()
    return {"status": "ok", "conflicts": engine.conflicts.list_conflicts(status=status)}


@router.post("/conflicts/{conflict_id}/resolve")
def resolve_conflict(
    conflict_id: str,
    req: ResolveConflictRequest,
    token: str = Depends(verify_token),
):
    """Deterministically resolve a quarantined conflict."""
    engine = get_sync_engine()
    success = engine.conflicts.resolve_conflict(conflict_id, req.resolution)
    if not success:
        raise HTTPException(status_code=404, detail="Conflict not found")
    return {"status": "ok", "conflict_id": conflict_id, "resolution": req.resolution}
