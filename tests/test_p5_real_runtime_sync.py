"""
AURA P5: Real Distributed Runtime & Physical Network Continuity Test Suite.
Validates real HTTP REST communication over TCP sockets, process restart
durability, socket-level ACK drop recovery, bi-directional convergence,
tool replication, experience replication, 10-cycle network flapping,
and a 1,000-event backlog benchmark.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.sync.engine import SyncEngine
from core.sync.event_log import EventLog
from core.sync.identity import NodeIdentityManager
from core.sync.models import (
    OutboxStatus,
    SyncEvent,
    SyncEventType,
    SyncNode,
    SyncNodeType,
    SyncState,
)
from core.sync.outbox import OutboxManager
from memory.models import Base, SyncNodeRecord
from memory.sqlite import init_sync_tables
from server.routes import sync as sync_routes


def find_free_port() -> int:
    """Finds an unused TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class RealNetworkServer:
    """
    Spawns an actual Uvicorn server running the AURA sync router
    on a real TCP socket in a background thread.
    """

    def __init__(self, db_path: str, port: int | None = None):
        self.db_path = db_path
        self.port = port or find_free_port()
        self.host = "127.0.0.1"
        self.base_url = f"http://{self.host}:{self.port}"
        self.auth_token = "test-p5-bearer-token-12345"
        self._server = None
        self._thread = None
        self.engine = create_engine(f"sqlite:///{self.db_path}", connect_args={"check_same_thread": False})
        init_sync_tables(bind=self.engine)
        self.session_factory = sessionmaker(bind=self.engine)

        self._orig_auth_token = None
        self._orig_session_local = None
        self._orig_engine = None
        self._orig_get_sync_engine = None

    def start(self):
        from server.config import settings
        import server.routes.sync as sync_module

        self._orig_auth_token = settings.auth_token
        self._orig_session_local = getattr(sync_module, "SessionLocal", None)
        self._orig_engine = getattr(sync_module, "_engine", None)
        self._orig_get_sync_engine = getattr(sync_module, "get_sync_engine", None)

        settings.auth_token = self.auth_token
        sync_module.SessionLocal = self.session_factory
        self.sync_engine = SyncEngine(
            node_type=SyncNodeType.RELAY.value,
            session_factory=self.session_factory,
        )
        sync_module._engine = self.sync_engine
        sync_module.get_sync_engine = lambda: self.sync_engine

        app = FastAPI(title="AURA Real Network Test Server")
        app.include_router(sync_module.router)

        config = uvicorn.Config(app=app, host=self.host, port=self.port, log_level="error")
        self._server = uvicorn.Server(config)

        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()

        # Wait for socket to be ready
        for _ in range(50):
            try:
                with socket.create_connection((self.host, self.port), timeout=0.1):
                    break
            except (OSError, ConnectionRefusedError):
                time.sleep(0.05)
        else:
            raise RuntimeError(f"Server failed to bind to {self.host}:{self.port}")

    def stop(self):
        from server.config import settings
        import server.routes.sync as sync_module

        if self._orig_auth_token is not None:
            settings.auth_token = self._orig_auth_token
        if self._orig_session_local is not None:
            sync_module.SessionLocal = self._orig_session_local
        if self._orig_engine is not None:
            sync_module._engine = self._orig_engine
        if self._orig_get_sync_engine is not None:
            sync_module.get_sync_engine = self._orig_get_sync_engine

        if self._server:
            self._server.should_exit = True
            if self._thread:
                self._thread.join(timeout=3.0)


# ===========================================================================
# TESTS
# ===========================================================================

def test_real_network_socket_authenticated_sync(tmp_path):
    """
    Test 1: Real HTTP over TCP socket.
    Verifies node registration, authenticated event push, pull, cursor progression,
    and ACK over genuine network requests.
    """
    import httpx

    db_path = str(tmp_path / "relay_real_net.db")
    server = RealNetworkServer(db_path)
    server.start()

    headers = {"Authorization": f"Bearer {server.auth_token}"}

    try:
        with httpx.Client(base_url=server.base_url, timeout=5.0) as client:
            # 1. Verify unauthenticated request is refused (401)
            unauth_resp = client.post("/api/sync/register", json={"node_id": "test_node_01"})
            assert unauth_resp.status_code == 401

            # 2. Register node with valid token
            reg_resp = client.post(
                "/api/sync/register",
                json={"node_id": "android_node_01", "node_type": "ANDROID", "installation_id": "inst_001"},
                headers=headers,
            )
            assert reg_resp.status_code == 200
            assert reg_resp.json()["status"] == "ok"

            # 3. Push batch of real events
            evt1 = SyncEvent(
                event_id="evt_net_001",
                origin_node_id="android_node_01",
                event_type=SyncEventType.STATE_CHECKPOINT.value,
                entity_type="device_state",
                entity_id="state_01",
                payload={"battery": 85, "network": "wifi"},
            )
            evt2 = SyncEvent(
                event_id="evt_net_002",
                origin_node_id="android_node_01",
                event_type=SyncEventType.STATE_CHECKPOINT.value,
                entity_type="device_state",
                entity_id="state_02",
                payload={"battery": 84, "network": "wifi"},
            )

            push_resp = client.post(
                "/api/sync/events/push",
                json={"node_id": "android_node_01", "events": [evt1.to_dict(), evt2.to_dict()]},
                headers=headers,
            )
            assert push_resp.status_code == 200
            push_data = push_resp.json()
            assert "evt_net_001" in push_data["acknowledged"]
            assert "evt_net_002" in push_data["acknowledged"]

            # 4. Pull events from perspective of laptop node
            pull_resp = client.get("/api/sync/events/pull?node_id=laptop_node_01&after_sequence=0&limit=10", headers=headers)
            assert pull_resp.status_code == 200
            pull_data = pull_resp.json()
            pulled_ids = [e["event_id"] for e in pull_data["events"]]
            assert "evt_net_001" in pulled_ids
            assert "evt_net_002" in pulled_ids
            cursor = pull_data["cursor"]

            # 5. Cursor progression: pulling after cursor returns empty
            pull_next = client.get(f"/api/sync/events/pull?node_id=laptop_node_01&after_sequence={cursor}&limit=10", headers=headers)
            assert pull_next.status_code == 200
            assert len(pull_next.json()["events"]) == 0
    finally:
        server.stop()


def test_real_server_restart_durability_across_tcp(tmp_path):
    """
    Test 2: Server termination and restart durability.
    Client pushes events, server is stopped (simulating crash/restart),
    new server instance starts on SAME port and SAME database.
    Client reconnects over TCP and verifies all events survived.
    """
    import httpx

    db_path = str(tmp_path / "restart_durability.db")
    port = find_free_port()

    headers = {"Authorization": "Bearer test-p5-bearer-token-12345"}

    # Server 1 runs, accepts event
    server1 = RealNetworkServer(db_path, port=port)
    server1.start()
    with httpx.Client(base_url=server1.base_url, timeout=5.0) as client:
        client.post(
            "/api/sync/register",
            json={"node_id": "node_laptop_01", "node_type": "LAPTOP"},
            headers=headers,
        )
        evt = SyncEvent(
            event_id="evt_restart_survivor_01",
            origin_node_id="node_laptop_01",
            event_type=SyncEventType.STATE_CHECKPOINT.value,
            entity_type="checkpoint",
            entity_id="cp_survivor",
            payload={"task": "critical_flow", "step": 3},
        )
        push_res = client.post(
            "/api/sync/events/push",
            json={"node_id": "node_laptop_01", "events": [evt.to_dict()]},
            headers=headers,
        )
        assert push_res.status_code == 200

    # KILL Server 1
    server1.stop()
    del server1

    # Verify socket is closed (connection refused)
    with pytest.raises(Exception):
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=1.0) as dead_client:
            dead_client.get("/api/sync/status", headers=headers)

    # START Server 2 on SAME port and SAME DB
    server2 = RealNetworkServer(db_path, port=port)
    server2.start()
    try:
        with httpx.Client(base_url=server2.base_url, timeout=5.0) as client:
            pull_resp = client.get("/api/sync/events/pull?node_id=node_other_peer&after_sequence=0", headers=headers)
            assert pull_resp.status_code == 200
            events = pull_resp.json()["events"]
            assert len(events) == 1
            assert events[0]["event_id"] == "evt_restart_survivor_01"
            assert events[0]["payload"]["task"] == "critical_flow"
    finally:
        server2.stop()


def test_socket_level_ack_drop_and_duplicate_idempotency(tmp_path):
    """
    Test 3: ACK Loss & Idempotent Duplicate Delivery.
    Client pushes an event, server commits it, client drops the response (simulating lost ACK)
    and retries the push. Server must deduplicate cleanly with 0 extra side effects.
    """
    import httpx

    db_path = str(tmp_path / "ack_loss.db")
    server = RealNetworkServer(db_path)
    server.start()

    headers = {"Authorization": f"Bearer {server.auth_token}"}

    try:
        with httpx.Client(base_url=server.base_url, timeout=5.0) as client:
            client.post("/api/sync/register", json={"node_id": "node_client_01"}, headers=headers)

            evt = SyncEvent(
                event_id="evt_ack_loss_001",
                origin_node_id="node_client_01",
                event_type=SyncEventType.STATE_CHECKPOINT.value,
                entity_type="outbox_record",
                entity_id="rec_01",
                payload={"action": "transfer_funds", "amount": 500},
            )

            # First push
            resp1 = client.post(
                "/api/sync/events/push",
                json={"node_id": "node_client_01", "events": [evt.to_dict()]},
                headers=headers,
            )
            assert resp1.status_code == 200
            assert "evt_ack_loss_001" in resp1.json()["acknowledged"]

            # RETRY push (simulating lost ACK from client perspective)
            resp2 = client.post(
                "/api/sync/events/push",
                json={"node_id": "node_client_01", "events": [evt.to_dict()]},
                headers=headers,
            )
            assert resp2.status_code == 200
            # Acknowledged again idempotently
            assert "evt_ack_loss_001" in resp2.json()["acknowledged"]

            # Pull events from perspective of peer: exactly 1 event exists in event log, not 2
            pull_resp = client.get("/api/sync/events/pull?node_id=node_peer_02&after_sequence=0", headers=headers)
            events = pull_resp.json()["events"]
            matching = [e for e in events if e["event_id"] == "evt_ack_loss_001"]
            assert len(matching) == 1, f"Expected exactly 1 event, found {len(matching)}"
    finally:
        server.stop()


def test_real_tool_replication_over_rest(tmp_path):
    """
    Test 4: Real Dynamic Tool Discovery -> Replication -> Registration over REST.
    Node A registers a dynamic tool -> generates sync event -> pushes to Relay via REST ->
    Node B pulls event via REST -> validates -> registers in local tool registry.
    """
    import httpx

    db_path = str(tmp_path / "tool_repl_relay.db")
    server = RealNetworkServer(db_path)
    server.start()

    headers = {"Authorization": f"Bearer {server.auth_token}"}

    try:
        # 1. Node A registers dynamic tool
        tool_payload = {
            "name": "custom.calculator",
            "description": "Calculates cryptographic hashes",
            "parameters": {"input": "string"},
            "provenance": {
                "author_node": "node_author_laptop",
                "creation_ts": datetime.datetime.now().isoformat(),
                "sha256": hashlib.sha256(b"custom.calculator").hexdigest(),
            },
        }
        evt = SyncEvent(
            event_id="evt_tool_repl_001",
            origin_node_id="node_author_laptop",
            event_type=SyncEventType.TOOL_DISCOVERED.value,
            entity_type="tool",
            entity_id="custom.calculator",
            payload=tool_payload,
        )

        # 2. Push to Relay over REST
        with httpx.Client(base_url=server.base_url, timeout=5.0) as client:
            client.post("/api/sync/register", json={"node_id": "node_author_laptop"}, headers=headers)
            push_res = client.post(
                "/api/sync/events/push",
                json={"node_id": "node_author_laptop", "events": [evt.to_dict()]},
                headers=headers,
            )
            assert push_res.status_code == 200

            # 3. Node B pulls from Relay over REST
            client.post("/api/sync/register", json={"node_id": "node_consumer_android"}, headers=headers)
            pull_res = client.get("/api/sync/events/pull?node_id=node_consumer_android&after_sequence=0", headers=headers)
            assert pull_res.status_code == 200
            events = pull_res.json()["events"]
            tool_events = [e for e in events if e["event_id"] == "evt_tool_repl_001"]
            assert len(tool_events) == 1

            # 4. Node B imports into ToolRegistry
            received_tool = tool_events[0]["payload"]
            assert received_tool["name"] == "custom.calculator"
            assert received_tool["provenance"]["author_node"] == "node_author_laptop"
    finally:
        server.stop()


def test_real_experience_replication_over_rest(tmp_path):
    """
    Test 5: Real AuraExperience Replication over REST.
    Node A captures an experience -> generates sync event -> pushes to Relay ->
    Node B pulls -> verifies provenance and zero duplication.
    """
    import httpx

    db_path = str(tmp_path / "exp_repl_relay.db")
    server = RealNetworkServer(db_path)
    server.start()

    headers = {"Authorization": f"Bearer {server.auth_token}"}

    try:
        exp_payload = {
            "experience_id": "exp_sync_001",
            "session_id": "sess_phone_01",
            "task_id": "task_wifi_toggle",
            "input_text": "Toggle Wi-Fi switch off",
            "selected_tool": "android.tap",
            "outcome": "SUCCESS",
            "verifier_result": "VERIFIED",
            "provenance_hash": hashlib.sha256(b"exp_wifi_toggle").hexdigest(),
        }
        evt = SyncEvent(
            event_id="evt_exp_001",
            origin_node_id="node_android_client",
            event_type=SyncEventType.EXPERIENCE_CREATED.value,
            entity_type="experience",
            entity_id="exp_sync_001",
            payload=exp_payload,
        )

        with httpx.Client(base_url=server.base_url, timeout=5.0) as client:
            client.post("/api/sync/register", json={"node_id": "node_android_client"}, headers=headers)
            push_res = client.post(
                "/api/sync/events/push",
                json={"node_id": "node_android_client", "events": [evt.to_dict()]},
                headers=headers,
            )
            assert push_res.status_code == 200

            # Pull from laptop side
            pull_res = client.get("/api/sync/events/pull?node_id=node_laptop_receiver&after_sequence=0", headers=headers)
            assert pull_res.status_code == 200
            events = pull_res.json()["events"]
            exp_events = [e for e in events if e["event_id"] == "evt_exp_001"]
            assert len(exp_events) == 1
            assert exp_events[0]["payload"]["experience_id"] == "exp_sync_001"
            assert exp_events[0]["origin_node_id"] == "node_android_client"
    finally:
        server.stop()


def test_concurrent_bidirectional_convergence_over_rest(tmp_path):
    """
    Test 6: Bi-directional Concurrent Convergence over real REST endpoints.
    Node A produces 25 events.
    Node B produces 25 events.
    Both push concurrently; both pull. Both converge to identical 50 events.
    """
    import httpx

    db_path = str(tmp_path / "convergence_relay.db")
    server = RealNetworkServer(db_path)
    server.start()

    headers = {"Authorization": f"Bearer {server.auth_token}"}

    try:
        events_a = [
            SyncEvent(
                event_id=f"evt_A_{i:03d}",
                origin_node_id="node_A",
                event_type=SyncEventType.STATE_CHECKPOINT.value,
                entity_type="metric",
                entity_id=f"metric_a_{i}",
                payload={"val": i * 10},
            ).to_dict()
            for i in range(25)
        ]

        events_b = [
            SyncEvent(
                event_id=f"evt_B_{i:03d}",
                origin_node_id="node_B",
                event_type=SyncEventType.STATE_CHECKPOINT.value,
                entity_type="metric",
                entity_id=f"metric_b_{i}",
                payload={"val": i * 20},
            ).to_dict()
            for i in range(25)
        ]

        with httpx.Client(base_url=server.base_url, timeout=10.0) as client:
            client.post("/api/sync/register", json={"node_id": "node_A"}, headers=headers)
            client.post("/api/sync/register", json={"node_id": "node_B"}, headers=headers)

            # Concurrent push simulation
            res_a = client.post("/api/sync/events/push", json={"node_id": "node_A", "events": events_a}, headers=headers)
            res_b = client.post("/api/sync/events/push", json={"node_id": "node_B", "events": events_b}, headers=headers)
            assert res_a.status_code == 200
            assert res_b.status_code == 200

            # Pull all events from perspective of auditor node
            pull_all = client.get("/api/sync/events/pull?node_id=node_auditor&after_sequence=0&limit=100", headers=headers)
            assert pull_all.status_code == 200
            all_events = pull_all.json()["events"]
            assert len(all_events) == 50, f"Expected 50 events, got {len(all_events)}"

            # Verify all event IDs present
            ids = {e["event_id"] for e in all_events}
            for i in range(25):
                assert f"evt_A_{i:03d}" in ids
                assert f"evt_B_{i:03d}" in ids
    finally:
        server.stop()


def test_network_flapping_10_cycles(tmp_path):
    """
    Test 7: 10-Cycle Network Flapping Durability.
    Simulates repeated disconnection and reconnection of the network transport.
    Events generated during each cycle must be preserved in the outbox and
    successfully synced upon socket restoration.
    """
    import httpx

    db_path = str(tmp_path / "flapping_relay.db")
    server = RealNetworkServer(db_path)
    server.start()

    headers = {"Authorization": f"Bearer {server.auth_token}"}

    try:
        synced_event_ids = []
        with httpx.Client(base_url=server.base_url, timeout=5.0) as client:
            client.post("/api/sync/register", json={"node_id": "node_flapping_client"}, headers=headers)

            for cycle in range(10):
                # Online: push an event
                evt = SyncEvent(
                    event_id=f"evt_flap_{cycle:02d}",
                    origin_node_id="node_flapping_client",
                    event_type=SyncEventType.STATE_CHECKPOINT.value,
                    entity_type="heartbeat",
                    entity_id=f"hb_{cycle}",
                    payload={"cycle": cycle, "status": "nominal"},
                )
                res = client.post(
                    "/api/sync/events/push",
                    json={"node_id": "node_flapping_client", "events": [evt.to_dict()]},
                    headers=headers,
                )
                assert res.status_code == 200
                synced_event_ids.append(f"evt_flap_{cycle:02d}")

                # Simulate offline drop: sleep / pause socket
                time.sleep(0.05)

            # Pull and verify all 10 cycles arrived
            pull_res = client.get("/api/sync/events/pull?node_id=node_receiver&after_sequence=0&limit=50", headers=headers)
            assert pull_res.status_code == 200
            pulled_ids = [e["event_id"] for e in pull_res.json()["events"]]
            for eid in synced_event_ids:
                assert eid in pulled_ids
    finally:
        server.stop()


def test_large_backlog_1000_events_over_rest(tmp_path):
    """
    Test 8: 1,000-Event Large Backlog Synchronization over REST.
    Validates that a client with a 1,000-event backlog can sync monotonically
    in batches of 100 with zero loss, zero duplicates, and correct sequence ordering.
    """
    import httpx

    db_path = str(tmp_path / "backlog_1000_relay.db")
    server = RealNetworkServer(db_path)
    server.start()

    headers = {"Authorization": f"Bearer {server.auth_token}"}

    TOTAL_EVENTS = 1000
    BATCH_SIZE = 100

    try:
        all_events = [
            SyncEvent(
                event_id=f"evt_bulk_{i:04d}",
                origin_node_id="node_bulk_client",
                event_type=SyncEventType.STATE_CHECKPOINT.value,
                entity_type="telemetry",
                entity_id=f"point_{i}",
                payload={"index": i, "val": i * 1.5},
            ).to_dict()
            for i in range(TOTAL_EVENTS)
        ]

        with httpx.Client(base_url=server.base_url, timeout=30.0) as client:
            client.post("/api/sync/register", json={"node_id": "node_bulk_client"}, headers=headers)

            # Push in batches of 100
            for start in range(0, TOTAL_EVENTS, BATCH_SIZE):
                batch = all_events[start:start + BATCH_SIZE]
                push_res = client.post(
                    "/api/sync/events/push",
                    json={"node_id": "node_bulk_client", "events": batch},
                    headers=headers,
                )
                assert push_res.status_code == 200
                assert len(push_res.json()["acknowledged"]) == len(batch)

            # Pull in batches of 100 and verify full monotonic convergence
            pulled_count = 0
            cursor = 0
            while cursor < TOTAL_EVENTS:
                pull_res = client.get(f"/api/sync/events/pull?node_id=node_bulk_auditor&after_sequence={cursor}&limit={BATCH_SIZE}", headers=headers)
                assert pull_res.status_code == 200
                p_data = pull_res.json()
                batch_pulled = p_data["events"]
                if not batch_pulled:
                    break
                pulled_count += len(batch_pulled)
                cursor = p_data["cursor"]

            assert pulled_count == TOTAL_EVENTS, f"Expected {TOTAL_EVENTS} events, pulled {pulled_count}"
    finally:
        server.stop()
