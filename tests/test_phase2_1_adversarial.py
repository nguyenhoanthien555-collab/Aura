"""
AURA Phase 2.1 — Adversarial Forensic Test Suite.
Attacks Render sync routes, ACK loss, 20-cycle flapping, experience injection,
and strict autonomy preservation.
"""

import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth import verify_token
from server.routes.sync import router as sync_router, get_sync_engine
from core.sync.models import (
    SyncEvent,
    compute_payload_hash,
)
from memory.sqlite import init_sync_tables, SessionLocal, db_lock
from memory.models import SyncEventRecord, SyncNodeRecord
from agent.autonomy_guard import AutonomyGateManager


@pytest.fixture(scope="module", autouse=True)
def setup_sync_db():
    init_sync_tables()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(sync_router)
    app.dependency_overrides[verify_token] = lambda: "test_token"
    with TestClient(app) as tc:
        yield tc


# ---------------------------------------------------------------------------
# Section 5 Attack: ACK Loss & Process Restart
# ---------------------------------------------------------------------------

def test_ack_loss_with_client_restart(client):
    """
    Simulates:
    1. Android sends event.
    2. Render stores event in DB and acknowledges.
    3. Network drops response before Android receives ACK.
    4. Android process dies and restarts.
    5. Android retries pushing the unacknowledged event from outbox.
    6. Render deduplicates idempotently: returns ACK, 0 duplicate DB records.
    """
    node_id = "android-ack-loss-node"
    client.post("/api/sync/register", json={"node_id": node_id, "node_type": "ANDROID"})

    evt_id = "evt-ack-loss-001"
    payload = {"tool": "tap", "x": 500, "y": 1000}
    event = {
        "event_id": evt_id,
        "origin_node_id": node_id,
        "event_type": "DEVICE_ACTION",
        "entity_type": "action",
        "entity_id": "act-01",
        "schema_version": 1,
        "created_at": "2026-09-17T13:00:00Z",
        "logical_sequence": 1,
        "payload": payload,
        "payload_hash": compute_payload_hash(payload),
    }

    # Step 1: Initial Push (Server stores, but ACK drops in transit)
    res1 = client.post("/api/sync/events/push", json={"node_id": node_id, "events": [event]})
    assert res1.status_code == 200
    assert evt_id in res1.json()["acknowledged"]

    # Verify stored in DB exactly once
    with db_lock:
        session = SessionLocal()
        try:
            count = session.query(SyncEventRecord).filter_by(event_id=evt_id).count()
            assert count == 1
        finally:
            session.close()

    # Step 2: Client restarted, outbox still contains event because ACK was lost.
    # Client retries push with exact same event.
    res2 = client.post("/api/sync/events/push", json={"node_id": node_id, "events": [event]})
    assert res2.status_code == 200
    # Server recognizes duplicate, confirms ACK
    assert evt_id in res2.json()["acknowledged"]
    assert len(res2.json()["conflicts"]) == 0

    # Step 3: Verify STILL exactly 1 record in SQLite (no duplicate row created!)
    with db_lock:
        session = SessionLocal()
        try:
            count = session.query(SyncEventRecord).filter_by(event_id=evt_id).count()
            assert count == 1
        finally:
            session.close()


# ---------------------------------------------------------------------------
# Section 12 Attack: 20-Cycle Aggressive Network Flapping
# ---------------------------------------------------------------------------

def test_20_cycle_network_flapping(client):
    """
    Simulates 20 consecutive connect / disconnect / reconnect cycles with:
    - In-flight event creation
    - Intermittent drops
    - Interleaved pull & push
    - Verification of zero event loss, zero duplication, and clean convergence.
    """
    node_id = "android-flapping-20-node"
    client.post("/api/sync/register", json={"node_id": node_id, "node_type": "ANDROID"})

    all_event_ids = []
    for cycle in range(1, 21):
        payload = {"cycle": cycle, "timestamp": f"2026-09-17T13:10:{cycle:02d}Z"}
        evt_id = f"evt-flap20-cycle-{cycle:02d}"
        event = {
            "event_id": evt_id,
            "origin_node_id": node_id,
            "event_type": "STATE_CHECKPOINT",
            "entity_type": "checkpoint",
            "entity_id": f"chk-{cycle}",
            "schema_version": 1,
            "created_at": f"2026-09-17T13:10:{cycle:02d}Z",
            "logical_sequence": cycle,
            "payload": payload,
            "payload_hash": compute_payload_hash(payload),
        }
        all_event_ids.append(evt_id)

        # Cycle: Push
        res = client.post("/api/sync/events/push", json={"node_id": node_id, "events": [event]})
        assert res.status_code == 200
        assert evt_id in res.json()["acknowledged"]

        # Cycle: Flap (Simulate disconnect & immediate re-push retry)
        retry_res = client.post("/api/sync/events/push", json={"node_id": node_id, "events": [event]})
        assert retry_res.status_code == 200
        assert evt_id in retry_res.json()["acknowledged"]

        # Cycle: Pull status
        pull_res = client.get(f"/api/sync/events/pull?node_id={node_id}&after_sequence=0")
        assert pull_res.status_code == 200

    # Invariant: Exactly 20 distinct records in DB (0 loss, 0 duplicate)
    with db_lock:
        session = SessionLocal()
        try:
            records = session.query(SyncEventRecord).filter(SyncEventRecord.event_id.in_(all_event_ids)).all()
            assert len(records) == 20
        finally:
            session.close()


# ---------------------------------------------------------------------------
# Section 20 Attack: Experience / Learning Boundary Protection
# ---------------------------------------------------------------------------

def test_experience_boundary_adversarial_injection(client):
    """
    Verifies that adversarial attempts to inject corrupted or unverified
    observations into the experience pipeline are quarantined and cannot
    bypass learning safety gates.
    """
    node_id = "android-adversary-node"
    client.post("/api/sync/register", json={"node_id": node_id, "node_type": "ANDROID"})

    # Attempt 1: Tampered payload hash
    tampered_evt = {
        "event_id": "evt-adv-tampered-exp-01",
        "origin_node_id": node_id,
        "event_type": "EXPERIENCE_CREATED",
        "entity_type": "experience",
        "entity_id": "exp-bad",
        "schema_version": 1,
        "created_at": "2026-09-17T13:20:00Z",
        "logical_sequence": 1,
        "payload": {"malicious": "injected_reward", "score": 9999},
        "payload_hash": "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
    }
    res = client.post("/api/sync/events/push", json={"node_id": node_id, "events": [tampered_evt]})
    assert res.status_code == 200
    data = res.json()
    assert "evt-adv-tampered-exp-01" not in data["acknowledged"]
    assert len(data["conflicts"]) == 1

    # Verify it is quarantined and NOT in active events table as processed
    with db_lock:
        session = SessionLocal()
        try:
            rec = session.query(SyncEventRecord).filter_by(event_id="evt-adv-tampered-exp-01").first()
            # If stored, its status must NOT be PROCESSED
            if rec:
                assert rec.status != "PROCESSED"
        finally:
            session.close()


# ---------------------------------------------------------------------------
# Section 21 Attack: Autonomy Invariant Under Sync Attack
# ---------------------------------------------------------------------------

def test_autonomy_invariant_under_sync_attack(client):
    """
    Attempts to inject events with payloads designed to alter or elevate autonomy:
    {"full_autonomy_enabled": True, "state": "STATE_3_UNLOCKED"}
    Verifies that no sync event can modify the AutonomyGateManager or unlock State 3.
    """
    manager = AutonomyGateManager()
    assert manager.state["full_autonomy_enabled"] is False

    node_id = "android-autonomy-attacker"
    client.post("/api/sync/register", json={"node_id": node_id, "node_type": "ANDROID"})

    malicious_payload = {
        "full_autonomy_enabled": True,
        "action": "FORCE_AUTONOMY_UNLOCK",
        "gate_override": "ALL_PASSED"
    }
    malicious_hash = compute_payload_hash(malicious_payload)
    event = {
        "event_id": "evt-autonomy-attack-01",
        "origin_node_id": node_id,
        "event_type": "CAPABILITY_UPDATE",
        "entity_type": "autonomy_override",
        "entity_id": "gate-01",
        "schema_version": 1,
        "created_at": "2026-09-17T13:30:00Z",
        "logical_sequence": 1,
        "payload": malicious_payload,
        "payload_hash": malicious_hash,
    }

    res = client.post("/api/sync/events/push", json={"node_id": node_id, "events": [event]})
    assert res.status_code == 200

    # Invariant: Autonomy gate state MUST REMAIN STRICTLY LOCKED
    manager_check = AutonomyGateManager()
    assert manager_check.state["full_autonomy_enabled"] is False
    assert manager_check.state.get("machine_verdict", {}).get("state_3_full_autonomy") == "LOCKED_PRESERVED"
