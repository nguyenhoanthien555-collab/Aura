"""
Integration tests for AURA P4 Sync REST endpoints.
Verifies registration, push, pull, ack, diagnostics, and conflict resolution via FastAPI TestClient.
"""

import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server.auth import verify_token
from server.routes.sync import router as sync_router
from core.sync.models import SyncEvent, SyncEventType
from memory.sqlite import init_sync_tables


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


def test_sync_register_node(client):
    """POST /api/sync/register registers or updates node identity."""
    res = client.post(
        "/api/sync/register",
        json={
            "node_id": "node_test_mobile",
            "node_type": "ANDROID",
            "installation_id": "inst_01",
            "metadata": {"model": "OPPO Reno6 5G", "os": "Android 13"},
        },
        headers={"Authorization": "Bearer test_token"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["node_id"] == "node_test_mobile"
    assert "last_seen" in data


def test_sync_push_events_valid(client):
    """POST /api/sync/events/push receives, validates and acks events."""
    event = SyncEvent(
        event_id="evt_route_test_01",
        origin_node_id="node_test_mobile",
        event_type=SyncEventType.EXPERIENCE_CREATED.value,
        entity_type="experience",
        entity_id="exp_test_01",
        logical_sequence=1,
        payload={"action": "open_youtube", "query": "minecraft"},
    )

    res = client.post(
        "/api/sync/events/push",
        json={
            "node_id": "node_test_mobile",
            "events": [event.to_dict()],
        },
        headers={"Authorization": "Bearer test_token"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "evt_route_test_01" in data["acknowledged"]
    assert len(data["conflicts"]) == 0


def test_sync_push_events_corrupt_hash_quarantined(client):
    """POST /api/sync/events/push quarantines events with tampered payload hashes."""
    event = SyncEvent(
        event_id="evt_route_tampered_01",
        origin_node_id="node_test_mobile",
        event_type=SyncEventType.EXPERIENCE_CREATED.value,
        entity_type="experience",
        entity_id="exp_tampered_01",
        logical_sequence=2,
        payload={"legit": "data"},
    )
    event_dict = event.to_dict()
    event_dict["payload_hash"] = "bad_hash_tampered_38927498"

    res = client.post(
        "/api/sync/events/push",
        json={
            "node_id": "node_test_mobile",
            "events": [event_dict],
        },
        headers={"Authorization": "Bearer test_token"},
    )
    assert res.status_code == 200
    data = res.json()
    assert "evt_route_tampered_01" not in data["acknowledged"]
    assert len(data["conflicts"]) == 1
    assert data["conflicts"][0]["event_id"] == "evt_route_tampered_01"


def test_sync_pull_events(client):
    """GET /api/sync/events/pull returns events for peer node."""
    event = SyncEvent(
        event_id="evt_from_laptop_01",
        origin_node_id="node_laptop_peer",
        event_type=SyncEventType.STATE_CHECKPOINT.value,
        entity_type="state",
        entity_id="state_peer_01",
        logical_sequence=10,
        payload={"sync": "peer_update"},
    )
    client.post(
        "/api/sync/events/push",
        json={"node_id": "node_laptop_peer", "events": [event.to_dict()]},
        headers={"Authorization": "Bearer test_token"},
    )

    res = client.get(
        "/api/sync/events/pull?node_id=node_test_mobile&after_sequence=0&limit=50",
        headers={"Authorization": "Bearer test_token"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    event_ids = [e["event_id"] for e in data["events"]]
    assert "evt_from_laptop_01" in event_ids


def test_sync_ack_events(client):
    """POST /api/sync/events/ack updates outbox acknowledgment state."""
    res = client.post(
        "/api/sync/events/ack",
        json={
            "node_id": "node_test_mobile",
            "event_ids": ["evt_route_test_01"],
        },
        headers={"Authorization": "Bearer test_token"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["acknowledged_count"] == 1


def test_sync_status_diagnostics(client):
    """GET /api/sync/status returns live diagnostics."""
    res = client.get(
        "/api/sync/status",
        headers={"Authorization": "Bearer test_token"},
    )
    assert res.status_code == 200
    data = res.json()
    assert "node_id" in data
    assert "node_type" in data
    assert "total_local_events" in data
    assert "pending_outbox_events" in data
    assert "quarantined_conflicts" in data


def test_sync_conflicts_and_resolve(client):
    """GET /api/sync/conflicts lists quarantined items and POST resolves them."""
    res = client.get(
        "/api/sync/conflicts?status=QUARANTINED",
        headers={"Authorization": "Bearer test_token"},
    )
    assert res.status_code == 200
    conflicts = res.json()["conflicts"]
    assert len(conflicts) >= 1
    target_conflict = conflicts[0]
    conflict_id = target_conflict["conflict_id"]

    res_resolve = client.post(
        f"/api/sync/conflicts/{conflict_id}/resolve",
        json={"resolution": "RESOLVED_AUDIT_PASS"},
        headers={"Authorization": "Bearer test_token"},
    )
    assert res_resolve.status_code == 200
    resolve_data = res_resolve.json()
    assert resolve_data["status"] == "ok"
    assert resolve_data["conflict_id"] == conflict_id
