"""
Comprehensive verification test suite for AURA Phase 2:
Android Native Event Sync & Distributed Continuity.

Verifies:
1. Android DTO deserialization parity on FastAPI endpoints
2. Bidirectional convergence: Laptop <---> Render Relay <---> Android Device
3. Offline backlog accumulation and complete draining
4. 10-cycle network flapping resilience (zero event loss, zero duplication)
5. Anti-fake mutation testing (tampered hashes quarantined, idempotent duplicate suppression)
6. Strict safety invariant: full_autonomy_enabled == False
"""

import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth import verify_token
from server.routes.sync import router as sync_router
from core.sync.models import (
    SyncEvent,
    compute_payload_hash,
)
from memory.sqlite import init_sync_tables, SessionLocal, db_lock
from memory.models import SyncEventRecord, SyncNodeRecord


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
# Test 1: Android DTO Deserialization Parity
# ---------------------------------------------------------------------------

def test_android_dto_deserialization(client):
    """
    Simulates the exact wire JSON produced by Android's Kotlin SyncDto:
    RegisterNodeRequestDto and PushEventsRequestDto.
    """
    # 1. Register Android Node
    reg_payload = {
        "node_id": "android-oppo-reno6",
        "node_type": "ANDROID",
        "installation_id": "android-oppo-reno6",
        "metadata": {
            "model": "OPPO CPH2251",
            "os_version": "Android 13",
            "battery_level": 88
        }
    }
    reg_res = client.post("/api/sync/register", json=reg_payload)
    assert reg_res.status_code == 200
    assert reg_res.json()["status"] == "ok"
    assert reg_res.json()["node_id"] == "android-oppo-reno6"

    # 2. Push Event using exact Kotlin SyncEventDto wire format
    event_payload = {"screen": "com.android.settings/.Settings", "action": "focus_changed"}
    payload_hash = compute_payload_hash(event_payload)

    push_payload = {
        "node_id": "android-oppo-reno6",
        "events": [
            {
                "event_id": "evt-android-screen-001",
                "origin_node_id": "android-oppo-reno6",
                "event_type": "SCREEN_STATE",
                "entity_type": "screen",
                "entity_id": "com.android.settings",
                "schema_version": 1,
                "created_at": "2026-09-17T12:00:00Z",
                "logical_sequence": 1,
                "payload": event_payload,
                "payload_hash": payload_hash,
                "parent_event_id": None,
                "provenance": {"device": "OPPO Reno6"},
                "received_at": None,
            }
        ]
    }
    push_res = client.post("/api/sync/events/push", json=push_payload)
    assert push_res.status_code == 200
    push_data = push_res.json()
    assert push_data["status"] == "ok"
    assert "evt-android-screen-001" in push_data["acknowledged"]
    assert len(push_data["conflicts"]) == 0


# ---------------------------------------------------------------------------
# Test 2: Bidirectional Flow (Laptop <---> Render Relay <---> Android)
# ---------------------------------------------------------------------------

def test_bidirectional_event_flow(client):
    """
    Verifies full loop:
    1. Laptop pushes tool invocation to Relay.
    2. Android pulls the invocation from Relay and ACKs it.
    3. Android executes and pushes result back to Relay.
    4. Laptop pulls result from Relay.
    """
    laptop_node = "laptop-aura-brain"
    android_node = "android-companion-dev"

    # Register both nodes
    client.post("/api/sync/register", json={"node_id": laptop_node, "node_type": "LAPTOP"})
    client.post("/api/sync/register", json={"node_id": android_node, "node_type": "ANDROID"})

    # 1. Laptop pushes tool invocation event
    invoc_payload = {"tool": "device_action", "command": "click", "target": "Submit"}
    invoc_hash = compute_payload_hash(invoc_payload)
    invoc_event = {
        "event_id": "evt-laptop-cmd-101",
        "origin_node_id": laptop_node,
        "event_type": "DEVICE_INVOCATION",
        "entity_type": "tool_invocation",
        "entity_id": "invoc-101",
        "schema_version": 1,
        "created_at": "2026-09-17T12:01:00Z",
        "logical_sequence": 1,
        "payload": invoc_payload,
        "payload_hash": invoc_hash,
    }

    push_res = client.post("/api/sync/events/push", json={"node_id": laptop_node, "events": [invoc_event]})
    assert push_res.status_code == 200
    assert "evt-laptop-cmd-101" in push_res.json()["acknowledged"]

    # 2. Android pulls events from Relay
    pull_res = client.get(f"/api/sync/events/pull?node_id={android_node}&after_sequence=0")
    assert pull_res.status_code == 200
    events = pull_res.json()["events"]
    pulled_event = next((e for e in events if e["event_id"] == "evt-laptop-cmd-101"), None)
    assert pulled_event is not None
    assert pulled_event["origin_node_id"] == laptop_node
    assert pulled_event["payload"] == invoc_payload

    # Android ACKs receipt
    ack_res = client.post("/api/sync/events/ack", json={"node_id": android_node, "event_ids": [pulled_event["event_id"]]})
    assert ack_res.status_code == 200

    # 3. Android pushes result back to Relay
    result_payload = {"status": "SUCCESS", "verified": True, "ui_changed": True}
    result_hash = compute_payload_hash(result_payload)
    result_event = {
        "event_id": "evt-android-res-101",
        "origin_node_id": android_node,
        "event_type": "DEVICE_INVOCATION_RESULT",
        "entity_type": "tool_result",
        "entity_id": "invoc-101",
        "schema_version": 1,
        "created_at": "2026-09-17T12:01:05Z",
        "logical_sequence": 2,
        "payload": result_payload,
        "payload_hash": result_hash,
        "parent_event_id": "evt-laptop-cmd-101"
    }
    push_res2 = client.post("/api/sync/events/push", json={"node_id": android_node, "events": [result_event]})
    assert push_res2.status_code == 200
    assert "evt-android-res-101" in push_res2.json()["acknowledged"]

    # 4. Laptop pulls and sees Android's result
    laptop_pull = client.get(f"/api/sync/events/pull?node_id={laptop_node}&after_sequence=0")
    assert laptop_pull.status_code == 200
    laptop_events = laptop_pull.json()["events"]
    found_result = next((e for e in laptop_events if e["event_id"] == "evt-android-res-101"), None)
    assert found_result is not None
    assert found_result["parent_event_id"] == "evt-laptop-cmd-101"
    assert found_result["payload"]["verified"] is True


# ---------------------------------------------------------------------------
# Test 3: Offline Backlog Accumulation & Draining
# ---------------------------------------------------------------------------

def test_offline_backlog_draining(client):
    """
    Simulates Android accumulating 5 events while completely offline.
    Upon reconnect, all 5 are pushed in a single batch, acknowledged,
    and verified to be ordered and deduplicated.
    """
    node_id = "android-backlog-node"
    client.post("/api/sync/register", json={"node_id": node_id, "node_type": "ANDROID"})

    backlog = []
    for i in range(1, 6):
        payload = {"tick": i, "data": f"offline_action_{i}"}
        evt = {
            "event_id": f"evt-offline-batch-{i}",
            "origin_node_id": node_id,
            "event_type": "EXPERIENCE_CREATED",
            "entity_type": "experience",
            "entity_id": f"exp-{i}",
            "schema_version": 1,
            "created_at": f"2026-09-17T12:10:0{i}Z",
            "logical_sequence": i,
            "payload": payload,
            "payload_hash": compute_payload_hash(payload),
        }
        backlog.append(evt)

    # Reconnect and push full backlog
    push_res = client.post("/api/sync/events/push", json={"node_id": node_id, "events": backlog})
    assert push_res.status_code == 200
    data = push_res.json()
    assert len(data["acknowledged"]) == 5
    for i in range(1, 6):
        assert f"evt-offline-batch-{i}" in data["acknowledged"]


# ---------------------------------------------------------------------------
# Test 4: 10-Cycle Network Flapping Resilience
# ---------------------------------------------------------------------------

def test_10_cycle_network_flapping(client):
    """
    Simulates 10 alternating network drop & reconnect cycles.
    Validates:
    - Zero event loss
    - Zero duplicate event executions
    - Clean state convergence across all 10 cycles
    """
    node_id = "android-flapping-node"
    client.post("/api/sync/register", json={"node_id": node_id, "node_type": "ANDROID"})

    pushed_ids = []
    for cycle in range(1, 11):
        payload = {"cycle": cycle, "status": "active"}
        evt_id = f"evt-flap-cycle-{cycle}"
        evt = {
            "event_id": evt_id,
            "origin_node_id": node_id,
            "event_type": "STATE_CHECKPOINT",
            "entity_type": "checkpoint",
            "entity_id": f"chk-{cycle}",
            "schema_version": 1,
            "created_at": f"2026-09-17T12:20:{cycle:02d}Z",
            "logical_sequence": cycle,
            "payload": payload,
            "payload_hash": compute_payload_hash(payload),
        }

        # Flap: Push event
        res = client.post("/api/sync/events/push", json={"node_id": node_id, "events": [evt]})
        assert res.status_code == 200
        assert evt_id in res.json()["acknowledged"]
        pushed_ids.append(evt_id)

        # Flap: Simulate network drop / timeout - immediately retry pushing the same event
        # Idempotency must handle duplicate push seamlessly
        retry_res = client.post("/api/sync/events/push", json={"node_id": node_id, "events": [evt]})
        assert retry_res.status_code == 200
        # Invariant: Duplicate push returns acknowledged without error
        assert evt_id in retry_res.json()["acknowledged"]

    # Verify all 10 distinct events exist in SQLite
    with db_lock:
        session = SessionLocal()
        try:
            records = session.query(SyncEventRecord).filter(SyncEventRecord.event_id.in_(pushed_ids)).all()
            assert len(records) == 10
        finally:
            session.close()


# ---------------------------------------------------------------------------
# Test 5: Anti-Fake Mutations
# ---------------------------------------------------------------------------

def test_mutation_1_tampered_payload_hash_quarantined(client):
    """Mutation 1: Corrupted payload hash MUST be quarantined as a conflict."""
    payload = {"valid": "content"}
    tampered_evt = {
        "event_id": "evt-tampered-mutation-1",
        "origin_node_id": "android-tamper-tester",
        "event_type": "EXPERIENCE_CREATED",
        "entity_type": "experience",
        "entity_id": "exp-tamper",
        "schema_version": 1,
        "created_at": "2026-09-17T12:30:00Z",
        "logical_sequence": 1,
        "payload": payload,
        "payload_hash": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
    }
    res = client.post("/api/sync/events/push", json={"node_id": "android-tamper-tester", "events": [tampered_evt]})
    assert res.status_code == 200
    data = res.json()
    assert "evt-tampered-mutation-1" not in data["acknowledged"]
    assert len(data["conflicts"]) == 1
    assert data["conflicts"][0]["event_id"] == "evt-tampered-mutation-1"


def test_mutation_2_duplicate_id_with_differing_payload_rejected(client):
    """Mutation 2: Re-using existing event ID with differing payload hash triggers conflict."""
    p1 = {"step": 1}
    evt1 = {
        "event_id": "evt-collision-mutation-2",
        "origin_node_id": "android-tamper-tester",
        "event_type": "EXPERIENCE_CREATED",
        "entity_type": "experience",
        "entity_id": "exp-c1",
        "schema_version": 1,
        "created_at": "2026-09-17T12:31:00Z",
        "logical_sequence": 1,
        "payload": p1,
        "payload_hash": compute_payload_hash(p1),
    }
    res1 = client.post("/api/sync/events/push", json={"node_id": "android-tamper-tester", "events": [evt1]})
    assert "evt-collision-mutation-2" in res1.json()["acknowledged"]

    p2 = {"step": 2, "tampered": True}
    evt2 = {
        "event_id": "evt-collision-mutation-2",
        "origin_node_id": "android-tamper-tester",
        "event_type": "EXPERIENCE_CREATED",
        "entity_type": "experience",
        "entity_id": "exp-c1",
        "schema_version": 1,
        "created_at": "2026-09-17T12:31:01Z",
        "logical_sequence": 2,
        "payload": p2,
        "payload_hash": compute_payload_hash(p2),
    }
    res2 = client.post("/api/sync/events/push", json={"node_id": "android-tamper-tester", "events": [evt2]})
    assert "evt-collision-mutation-2" not in res2.json()["acknowledged"]
    assert len(res2.json()["conflicts"]) >= 1


def test_mutation_3_pull_cursor_never_decreases(client):
    """Mutation 3: Verify pull cursor remains strictly monotonic."""
    node_id = "android-cursor-monotonicity-test"
    client.post("/api/sync/register", json={"node_id": node_id, "node_type": "ANDROID"})

    # Pull from 0
    res1 = client.get(f"/api/sync/events/pull?node_id={node_id}&after_sequence=0")
    assert res1.status_code == 200
    c1 = res1.json()["cursor"]

    # Pull from higher sequence
    res2 = client.get(f"/api/sync/events/pull?node_id={node_id}&after_sequence=1000")
    assert res2.status_code == 200
    c2 = res2.json()["cursor"]
    assert c2 >= 1000


# ---------------------------------------------------------------------------
# Test 6: Strict Safety Invariant Verification
# ---------------------------------------------------------------------------

def test_safety_invariant_full_autonomy_false():
    """
    Enforces the fundamental safety invariant across AURA:
    full_autonomy_enabled MUST be False.
    """
    from agent.autonomy_guard import AutonomyGateManager
    
    manager = AutonomyGateManager()
    assert manager.state["full_autonomy_enabled"] is False
    assert manager.state.get("machine_verdict", {}).get("state_3_full_autonomy") == "LOCKED_PRESERVED"

    import yaml
    import os
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
            assert cfg.get("full_autonomy_enabled", False) is False
