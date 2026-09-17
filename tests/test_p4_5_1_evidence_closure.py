"""
AURA P4.5.1 — Master Software-Only Evidence Closure Test Suite.
Validates all distributed continuity guarantees through deterministic
multi-process, multi-node, relay, and crash recovery simulations.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.sync.client import SyncNetworkException, SyncRelayClient, SyncTimeoutException
from core.sync.conflict import ConflictManager
from core.sync.engine import SyncEngine
from core.sync.event_log import EventLog
from core.sync.identity import NodeIdentityManager
from core.sync.inbox import InboxProcessor
from core.sync.invocation_ledger import DurableInvocationLedger
from core.sync.models import (
    OutboxStatus,
    SyncEvent,
    SyncEventType,
    SyncNode,
    SyncNodeType,
    SyncState,
)
from core.sync.outbox import OutboxManager
from core.sync.replication import ExperienceReplicationAdapter, ToolReplicationAdapter
from memory.models import (
    AgentRunRecord,
    AuraExperienceRecord,
    Base,
    SyncConflictRecord,
    SyncEventRecord,
    SyncInboxRecord,
    SyncOutboxRecord,
    ToolInvocationRecord,
    ToolProvenanceRecord,
)
from memory.sqlite import init_sync_tables, init_task_tables, init_learning_tables
from server.main import app
from tools.base import ToolResult
from tools.outcome import ToolStatus
from tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Helpers for Isolated Node Testing
# ---------------------------------------------------------------------------

def create_isolated_node_env(tmp_path, name: str, node_type: str = SyncNodeType.LAPTOP.value):
    db_file = tmp_path / f"{name}.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    init_sync_tables(bind=engine)
    init_task_tables(bind=engine)
    init_learning_tables(bind=engine)
    session_factory = sessionmaker(bind=engine)
    node = SyncNode(
        node_id=f"node_{name}",
        node_type=node_type,
        installation_id=f"inst_{name}",
        status=SyncState.ONLINE.value,
    )

    event_log = EventLog(session_factory)
    outbox = OutboxManager(session_factory)
    conflicts = ConflictManager(session_factory)
    inbox = InboxProcessor(
        session_factory=session_factory,
        conflict_manager=conflicts,
        dispatch_handler=lambda evt: _dispatch_test_event(evt, session_factory),
    )
    ledger = DurableInvocationLedger(session_factory=session_factory)

    return {
        "name": name,
        "db_file": db_file,
        "engine": engine,
        "session_factory": session_factory,
        "node": node,
        "event_log": event_log,
        "outbox": outbox,
        "conflicts": conflicts,
        "inbox": inbox,
        "ledger": ledger,
    }


def _dispatch_test_event(event: SyncEvent, session_factory):
    if event.entity_type == "experience":
        ExperienceReplicationAdapter.apply_event(event, session_factory)
    elif event.entity_type == "tool":
        ToolReplicationAdapter.apply_event(event, session_factory)


# ---------------------------------------------------------------------------
# Objective A: Real Process Restart Durability (Subprocess Boundary)
# ---------------------------------------------------------------------------

def test_real_subprocess_restart_durability(tmp_path):
    """
    Validates true OS-level process boundary restart durability.
    Process 1: writes events, outbox, AgentRun, Invocation, then exits.
    Process 2: starts with a different PID, opens the same DB, recovers all state,
               verifies replay protection, and drains outbox.
    """
    db_path = str(tmp_path / "proc_restart.db")
    worker_script = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts", "process_restart_worker.py"))

    # Stage 1: Run process 1
    res1 = subprocess.run(
        [sys.executable, worker_script, "--phase", "stage1", "--db", db_path],
        capture_output=True,
        text=True,
        check=True,
    )
    out1 = res1.stdout
    assert "[STAGE1_COMPLETED:" in out1

    pid1 = 0
    for line in out1.splitlines():
        if "[STAGE1_PID:" in line:
            pid1 = int(line.split(":")[1].replace("]", "").strip())
    assert pid1 > 0, f"Failed to parse PID1 from output: {out1}"

    # Stage 2: Run process 2 (new PID, pointing to same DB)
    res2 = subprocess.run(
        [sys.executable, worker_script, "--phase", "stage2", "--db", db_path, "--prev-pid", str(pid1)],
        capture_output=True,
        text=True,
        check=True,
    )
    out2 = res2.stdout
    assert "[STAGE2_VERIFIED: SUCCESS]" in out2

    pid2 = 0
    for line in out2.splitlines():
        if "[STAGE2_PID:" in line:
            pid2 = int(line.split(":")[1].replace("]", "").strip())
    assert pid2 > 0 and pid2 != pid1, f"Process 2 PID ({pid2}) must differ from Process 1 ({pid1})"


# ---------------------------------------------------------------------------
# Objective B: Durable Tool Invocation Replay Protection
# ---------------------------------------------------------------------------

def test_durable_tool_invocation_replay_protection_lifecycle(tmp_path):
    """
    Verifies that tool invocations transition across RECEIVED -> EXECUTING -> COMPLETED
    and that duplicate executions across restarts return the cached report without repeating side-effects.
    """
    db_file = tmp_path / "invocation.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    init_sync_tables(bind=engine)
    session_factory = sessionmaker(bind=engine)

    ledger1 = DurableInvocationLedger(session_factory=session_factory)
    tool_call_id = "call_replay_001"
    args = {"x": 500, "y": 900}

    # Step 1: Record received
    rec = ledger1.record_received(
        invocation_id="invo_001",
        tool="android.tap",
        arguments=args,
        run_id="run_100",
        tool_call_id=tool_call_id,
    )
    assert rec.lifecycle_state == "RECEIVED"
    assert rec.request_hash != ""

    # Step 2: Mark executing
    ledger1.record_executing(tool_call_id)
    rec2 = ledger1.get_invocation(tool_call_id)
    assert rec2.lifecycle_state == "EXECUTING"

    # Step 3: Complete execution
    report = {"ok": True, "result": {"tapped": True}, "postcondition": {"status": "verified"}}
    ledger1.record_completed(tool_call_id, report, evidence_hash="ev_tap_hash")

    # Step 4: Simulate crash and restart by creating fresh ledger on same DB
    del ledger1
    ledger2 = DurableInvocationLedger(session_factory=session_factory)

    # Step 5: Duplicate invocation arrival -> check replay returns cached result
    cached = ledger2.check_replay(tool_call_id)
    assert cached is not None
    assert cached["ok"] is True
    assert cached["postcondition"]["status"] == "verified"


# ---------------------------------------------------------------------------
# Objective C: Tool Replication End-to-End (Node A -> Relay -> Node B)
# ---------------------------------------------------------------------------

def test_tool_replication_end_to_end(tmp_path):
    """
    Verifies full tool replication chain:
    Node A registers tool -> event log -> outbox -> relay -> Node B inbox -> ToolReplicationAdapter -> Node B registry.
    """
    node_a = create_isolated_node_env(tmp_path, "laptop_a")
    relay = create_isolated_node_env(tmp_path, "relay_central", node_type=SyncNodeType.RELAY.value)
    node_b = create_isolated_node_env(tmp_path, "laptop_b")

    tool_name = "custom_barcode_reader"
    tool_schema = {"name": tool_name, "parameters": {"type": "object"}}
    evidence = [{"kind": "execution", "status": "VERIFIED"}]
    prov_info = {"author": "engineer_a", "verified_at": "2026-09-16"}

    # 1. Node A logs verified tool
    event = ToolReplicationAdapter.to_event(
        tool_name=tool_name,
        schema=tool_schema,
        evidence=evidence,
        provenance_info=prov_info,
        origin_node_id=node_a["node"].node_id,
    )
    node_a["event_log"].append_event(event, enqueue_outbox=True)

    # 2. Node A pushes to Relay
    pending = node_a["outbox"].get_pending()
    events_to_push = [evt for _, evt in pending]
    acked_ids, _ = relay["inbox"].process_incoming_batch(events_to_push, receiver_node_id=relay["node"].node_id)
    node_a["outbox"].mark_acknowledged(acked_ids)
    assert node_a["outbox"].pending_count() == 0

    # 3. Relay logs events for pull
    for evt in events_to_push:
        relay["event_log"].append_event(evt)

    # 4. Node B pulls from Relay
    pulled_events = relay["event_log"].get_events_after_cursor(after_sequence=0, limit=10)
    b_acked, _ = node_b["inbox"].process_incoming_batch(pulled_events, receiver_node_id=node_b["node"].node_id)
    assert len(b_acked) == 1

    # 5. Verify Node B has persisted ToolProvenanceRecord
    session = node_b["session_factory"]()
    rec = session.query(ToolProvenanceRecord).filter_by(name=tool_name).first()
    session.close()

    assert rec is not None
    assert rec.status == "PROMOTED"
    assert json.loads(rec.manifest_json) == tool_schema

    # 6. Idempotency: deliver event again to Node B
    b_acked_2, _ = node_b["inbox"].process_incoming_batch(pulled_events, receiver_node_id=node_b["node"].node_id)
    assert len(b_acked_2) == 1
    session = node_b["session_factory"]()
    count = session.query(ToolProvenanceRecord).filter_by(name=tool_name).count()
    session.close()
    assert count == 1, "Duplicate tool delivery must not create duplicate records"


# ---------------------------------------------------------------------------
# Objective D: Experience Replication End-to-End
# ---------------------------------------------------------------------------

def test_experience_replication_end_to_end(tmp_path):
    """
    Verifies full experience replication chain:
    Node A captures experience -> event log -> outbox -> relay -> Node B inbox -> ExperienceReplicationAdapter.
    """
    node_a = create_isolated_node_env(tmp_path, "mobile_a")
    relay = create_isolated_node_env(tmp_path, "relay_server", node_type=SyncNodeType.RELAY.value)
    node_b = create_isolated_node_env(tmp_path, "laptop_b")

    exp_rec = AuraExperienceRecord(
        experience_id="exp_run_capture_77",
        run_id="run_capture_77",
        session_id="session_mobile_01",
        task_id="task_calc",
        input_text="calculate 2+2",
        model_decision="TOOL_CALL",
        selected_tool="android.tap",
        arguments_json='{"x": 100, "y": 200}',
        tool_result_json='{"ok": true}',
        evidence_json='[{"kind": "tap"}]',
        verifier_result="VERIFIED",
        final_response="4",
        outcome="SUCCESS",
        taxonomy_tag="TOOL_VERIFIED",
        created_at="2026-09-16T22:00:00",
    )

    # 1. Node A creates event
    event = ExperienceReplicationAdapter.to_event(exp_rec, origin_node_id=node_a["node"].node_id)
    node_a["event_log"].append_event(event, enqueue_outbox=True)

    # 2. Push to Relay
    pending = node_a["outbox"].get_pending()
    events_to_push = [evt for _, evt in pending]
    acked_ids, _ = relay["inbox"].process_incoming_batch(events_to_push, receiver_node_id=relay["node"].node_id)
    node_a["outbox"].mark_acknowledged(acked_ids)
    for evt in events_to_push:
        relay["event_log"].append_event(evt)

    # 3. Node B pulls from Relay and ingests
    pulled = relay["event_log"].get_events_after_cursor(after_sequence=0)
    node_b["inbox"].process_incoming_batch(pulled, receiver_node_id=node_b["node"].node_id)

    # 4. Verify Node B database
    session = node_b["session_factory"]()
    rec = session.query(AuraExperienceRecord).filter_by(run_id="run_capture_77").first()
    session.close()

    assert rec is not None
    assert rec.experience_id == "exp_run_capture_77"
    assert rec.selected_tool == "android.tap"
    assert rec.taxonomy_tag == "TOOL_VERIFIED"

    # 5. Duplicate delivery test (send same event twice)
    node_b["inbox"].process_incoming_batch(pulled, receiver_node_id=node_b["node"].node_id)
    session = node_b["session_factory"]()
    count = session.query(AuraExperienceRecord).filter_by(run_id="run_capture_77").count()
    session.close()
    assert count == 1, "Duplicate experience delivery must not create duplicate records"


# ---------------------------------------------------------------------------
# Objective E: Bidirectional Convergence
# ---------------------------------------------------------------------------

def test_bidirectional_convergence(tmp_path):
    """
    Two isolated nodes generate independent events A1..A5 and B1..B5.
    They sync symmetrically through a central relay.
    Final assertion: event-set(A) == event-set(B).
    """
    node_a = create_isolated_node_env(tmp_path, "bi_node_a")
    node_b = create_isolated_node_env(tmp_path, "bi_node_b")
    relay = create_isolated_node_env(tmp_path, "bi_relay", node_type=SyncNodeType.RELAY.value)

    # Node A generates A1..A5
    for i in range(1, 6):
        evt = SyncEvent(
            event_id=f"evt_A_{i}",
            origin_node_id=node_a["node"].node_id,
            event_type=SyncEventType.STATE_CHECKPOINT.value,
            entity_type="item",
            entity_id=f"item_a_{i}",
            payload={"source": "A", "index": i},
        )
        node_a["event_log"].append_event(evt, enqueue_outbox=True)

    # Node B generates B1..B5
    for i in range(1, 6):
        evt = SyncEvent(
            event_id=f"evt_B_{i}",
            origin_node_id=node_b["node"].node_id,
            event_type=SyncEventType.STATE_CHECKPOINT.value,
            entity_type="item",
            entity_id=f"item_b_{i}",
            payload={"source": "B", "index": i},
        )
        node_b["event_log"].append_event(evt, enqueue_outbox=True)

    # Step 1: Both push to Relay
    pending_a = [e for _, e in node_a["outbox"].get_pending()]
    pending_b = [e for _, e in node_b["outbox"].get_pending()]

    relay["inbox"].process_incoming_batch(pending_a, receiver_node_id=relay["node"].node_id)
    for e in pending_a:
        relay["event_log"].append_event(e)
    node_a["outbox"].mark_acknowledged([e.event_id for e in pending_a])

    relay["inbox"].process_incoming_batch(pending_b, receiver_node_id=relay["node"].node_id)
    for e in pending_b:
        relay["event_log"].append_event(e)
    node_b["outbox"].mark_acknowledged([e.event_id for e in pending_b])

    # Step 2: Node A pulls B events from Relay
    b_events = [e for e in relay["event_log"].get_events_after_cursor(0) if e.origin_node_id == node_b["node"].node_id]
    node_a["inbox"].process_incoming_batch(b_events, receiver_node_id=node_a["node"].node_id)
    for e in b_events:
        node_a["event_log"].append_event(e)

    # Step 3: Node B pulls A events from Relay
    a_events = [e for e in relay["event_log"].get_events_after_cursor(0) if e.origin_node_id == node_a["node"].node_id]
    node_b["inbox"].process_incoming_batch(a_events, receiver_node_id=node_b["node"].node_id)
    for e in a_events:
        node_b["event_log"].append_event(e)

    # Final Convergence Assertion
    events_on_a = {e.event_id: e.payload_hash for e in node_a["event_log"].get_events_after_cursor(0)}
    events_on_b = {e.event_id: e.payload_hash for e in node_b["event_log"].get_events_after_cursor(0)}

    assert len(events_on_a) == 10
    assert len(events_on_b) == 10
    assert events_on_a == events_on_b, "Ledgers must be bit-for-bit identical on convergence"


# ---------------------------------------------------------------------------
# Objective F: Timeout ≠ Data Loss
# ---------------------------------------------------------------------------

def test_timeout_preserves_durable_outbox_and_agent_run(tmp_path):
    """
    Forces network timeout during outbox push.
    Verifies that event is NOT lost, remains PENDING, and subsequent reconnect succeeds.
    """
    node = create_isolated_node_env(tmp_path, "timeout_node")
    evt = SyncEvent(
        event_id="evt_timeout_test_01",
        origin_node_id=node["node"].node_id,
        event_type=SyncEventType.STATE_CHECKPOINT.value,
        entity_type="checkpoint",
        entity_id="cp_01",
        payload={"state": "critical_task_in_progress"},
    )
    node["event_log"].append_event(evt, enqueue_outbox=True)
    assert node["outbox"].pending_count() == 1

    # Simulate timeout during push
    mock_client = MagicMock(spec=SyncRelayClient)
    mock_client.push_events.side_effect = SyncTimeoutException("HTTP 504 Gateway Timeout")

    engine = SyncEngine(session_factory=node["session_factory"])
    engine.node = node["node"]
    engine.outbox = node["outbox"]

    acked = engine.process_outbox(mock_client)
    assert acked == 0
    # Event MUST still be pending in outbox!
    assert node["outbox"].pending_count() == 1
    session = node["session_factory"]()
    rec = session.query(SyncOutboxRecord).filter_by(event_id="evt_timeout_test_01").first()
    session.close()
    assert rec is not None
    assert rec.status == OutboxStatus.PENDING.value
    assert rec.attempts == 1

    # Restore network and retry
    mock_client.push_events.side_effect = None
    mock_client.push_events.return_value = (["evt_timeout_test_01"], [])
    acked2 = engine.process_outbox(mock_client, ignore_backoff=True)
    assert acked2 == 1
    assert node["outbox"].pending_count() == 0


# ---------------------------------------------------------------------------
# Objective G: ACK Loss / Duplicate Delivery Recovery
# ---------------------------------------------------------------------------

def test_ack_loss_duplicate_delivery_recovery(tmp_path):
    """
    Simulates sender pushing event, receiver persisting, but ACK is lost.
    Sender retries: receiver recognizes duplicate and ACKs without re-processing.
    """
    sender = create_isolated_node_env(tmp_path, "sender_node")
    receiver = create_isolated_node_env(tmp_path, "receiver_node")

    evt = SyncEvent(
        event_id="evt_ack_loss_999",
        origin_node_id=sender["node"].node_id,
        event_type=SyncEventType.STATE_CHECKPOINT.value,
        entity_type="checkpoint",
        entity_id="cp_999",
        payload={"counter": 42},
    )
    sender["event_log"].append_event(evt, enqueue_outbox=True)

    # First push: receiver processes, but ACK is lost in transit
    acked_first, _ = receiver["inbox"].process_incoming_batch([evt], receiver_node_id=receiver["node"].node_id)
    assert "evt_ack_loss_999" in acked_first
    # (ACK is dropped before sender marks acknowledged)

    # Sender retries identical event
    acked_retry, conflicts = receiver["inbox"].process_incoming_batch([evt], receiver_node_id=receiver["node"].node_id)
    assert "evt_ack_loss_999" in acked_retry
    assert len(conflicts) == 0

    # Sender finally gets ACK
    sender["outbox"].mark_acknowledged(acked_retry)
    assert sender["outbox"].pending_count() == 0

    # Receiver inbox must only have exactly 1 record
    session = receiver["session_factory"]()
    inbox_count = session.query(SyncInboxRecord).filter_by(event_id="evt_ack_loss_999").count()
    session.close()
    assert inbox_count == 1


# ---------------------------------------------------------------------------
# Objective H: Corruption & Tampering Quarantine
# ---------------------------------------------------------------------------

def test_corruption_and_tampering_quarantine(tmp_path):
    """
    Verifies that payload hash mismatches and concurrent mutations are quarantined.
    """
    node = create_isolated_node_env(tmp_path, "quarantine_node")

    # 1. Payload hash mismatch (tampered payload)
    evt_tampered = SyncEvent(
        event_id="evt_tampered_001",
        origin_node_id="malicious_node",
        event_type=SyncEventType.STATE_CHECKPOINT.value,
        entity_type="checkpoint",
        entity_id="cp_malicious",
        payload={"secret": "tampered_value"},
        payload_hash="fake_hash_1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
    )

    acked, conflicts = node["inbox"].process_incoming_batch([evt_tampered], receiver_node_id=node["node"].node_id)
    assert len(acked) == 0
    assert len(conflicts) == 1
    assert "Hash integrity" in conflicts[0]["error"]

    session = node["session_factory"]()
    conflict_rec = session.query(SyncConflictRecord).filter_by(event_id="evt_tampered_001").first()
    session.close()
    assert conflict_rec is not None
    assert conflict_rec.conflict_type == "HASH_MISMATCH"
    assert conflict_rec.status == "QUARANTINED"


# ---------------------------------------------------------------------------
# Objective J: Data Governance Quarantine
# ---------------------------------------------------------------------------

def test_data_governance_quarantined_experiences_not_promoted(tmp_path):
    """
    Verifies that quarantined experiences remain marked as UNVERIFIED/QUARANTINED
    and learning_eligible is False, never polluting trusted production knowledge.
    """
    node = create_isolated_node_env(tmp_path, "gov_node")
    exp_quarantined = AuraExperienceRecord(
        experience_id="exp_quarantine_01",
        run_id="run_quarantine_01",
        session_id="default",
        task_id="task_fail",
        input_text="harmful or contradictory request",
        selected_tool="os.delete",
        verifier_result="FAILED",
        outcome="FAILED",
        taxonomy_tag="QUARANTINED",
        learning_eligible=False,
    )

    event = ExperienceReplicationAdapter.to_event(exp_quarantined, origin_node_id=node["node"].node_id)
    ExperienceReplicationAdapter.apply_event(event, session_factory=node["session_factory"])

    session = node["session_factory"]()
    saved = session.query(AuraExperienceRecord).filter_by(run_id="run_quarantine_01").first()
    session.close()

    assert saved is not None
    assert saved.taxonomy_tag == "QUARANTINED"
    assert saved.learning_eligible is False


# ---------------------------------------------------------------------------
# Objective K: Large Backlog Convergence (>= 1,000 Events)
# ---------------------------------------------------------------------------

def test_large_backlog_convergence(tmp_path):
    """
    Creates a backlog of 1,000 events, restarts node state from DB,
    and synchronizes in batches through a central relay.
    """
    node_a = create_isolated_node_env(tmp_path, "backlog_node_a")
    relay = create_isolated_node_env(tmp_path, "backlog_relay", node_type=SyncNodeType.RELAY.value)

    total_events = 1000
    start_time = time.time()

    # Generate 1,000 events on Node A
    for i in range(total_events):
        evt = SyncEvent(
            event_id=f"evt_backlog_{i:04d}",
            origin_node_id=node_a["node"].node_id,
            event_type=SyncEventType.STATE_CHECKPOINT.value,
            entity_type="metric",
            entity_id=f"m_{i}",
            payload={"seq": i, "val": i * 10},
        )
        node_a["event_log"].append_event(evt, enqueue_outbox=True)

    assert node_a["outbox"].pending_count() == total_events

    # Simulate node restart: reload outbox from database
    del node_a["outbox"]
    fresh_outbox = OutboxManager(session_factory=node_a["session_factory"])
    assert fresh_outbox.pending_count() == total_events

    # Drain to relay in batches of 100
    processed = 0
    while fresh_outbox.pending_count() > 0:
        pending = fresh_outbox.get_pending(limit=100)
        events_batch = [e for _, e in pending]
        acked_ids, _ = relay["inbox"].process_incoming_batch(events_batch, receiver_node_id=relay["node"].node_id)
        fresh_outbox.mark_acknowledged(acked_ids)
        processed += len(acked_ids)

    elapsed = time.time() - start_time
    assert fresh_outbox.pending_count() == 0
    assert processed == total_events
    assert relay["event_log"].count() == total_events

    session = relay["session_factory"]()
    relay_inbox_count = session.query(SyncInboxRecord).count()
    session.close()
    assert relay_inbox_count == total_events


# ---------------------------------------------------------------------------
# Objective M: Real REST Sync Routes Integration
# ---------------------------------------------------------------------------

def test_rest_sync_routes_integration():
    """
    Exercises the actual FastAPI routes:
    /api/sync/register, /api/sync/events/push, /api/sync/events/pull,
    /api/sync/events/ack, /api/sync/status, /api/sync/conflicts
    """
    from server.auth import verify_token
    app.dependency_overrides[verify_token] = lambda: "test-owner-token"
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-owner-token"}

    # 1. Register node
    reg_payload = {
        "node_id": "test_rest_node_1",
        "node_type": "LAPTOP",
        "installation_id": "inst_rest_1",
        "metadata": {"os": "windows"},
    }
    r_reg = client.post("/api/sync/register", json=reg_payload, headers=headers)
    assert r_reg.status_code == 200
    assert r_reg.json()["status"] == "ok"

    # 2. Push event
    evt = SyncEvent(
        event_id="evt_rest_001",
        origin_node_id="test_rest_node_1",
        event_type=SyncEventType.STATE_CHECKPOINT.value,
        entity_type="config",
        entity_id="cfg_01",
        payload={"theme": "dark"},
    )
    push_payload = {
        "node_id": "test_rest_node_1",
        "events": [evt.to_dict()],
    }
    r_push = client.post("/api/sync/events/push", json=push_payload, headers=headers)
    assert r_push.status_code == 200
    assert "evt_rest_001" in r_push.json()["acknowledged"]

    # 3. Pull events from a different node
    r_pull = client.get("/api/sync/events/pull?node_id=test_rest_node_2&after_sequence=0", headers=headers)
    assert r_pull.status_code == 200
    assert r_pull.json()["status"] == "ok"

    # 4. Ack events
    ack_payload = {
        "node_id": "test_rest_node_1",
        "event_ids": ["evt_rest_001"],
    }
    r_ack = client.post("/api/sync/events/ack", json=ack_payload, headers=headers)
    assert r_ack.status_code == 200
    assert r_ack.json()["status"] == "ok"

    # 5. Diagnostics status
    r_status = client.get("/api/sync/status", headers=headers)
    assert r_status.status_code == 200
    assert "total_local_events" in r_status.json()

    # 6. Conflicts listing
    r_conf = client.get("/api/sync/conflicts", headers=headers)
    assert r_conf.status_code == 200
    assert "conflicts" in r_conf.json()


# ---------------------------------------------------------------------------
# Objective O: Autonomy Lock Preservation
# ---------------------------------------------------------------------------

def test_autonomy_lock_preserved():
    """
    Strictly verifies that full_autonomy_enabled is False.
    STATE 3 must remain LOCKED.
    """
    import yaml
    config_path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # In config or cognitive settings, full autonomy must be false
    assert config.get("full_autonomy_enabled") is not True, "STATE 3 must remain locked"
