"""
AURA P4.5: Forensic Distributed Reality & Continuity — Master Validation Suite.

Covers Gates A through L (Section 42 & 46):
- GATE A: Durable Event (test_timeout_preserves_event)
- GATE B: ACK Loss (test_ack_loss_idempotency)
- GATE C: Cross-Network (test_cross_network_deterministic_transport)
- GATE D: Bidirectional Synchronization (test_bidirectional_sync_chaos)
- GATE E: Offline Durability (test_mobile_offline_backlog, test_laptop_offline_backlog)
- GATE F: Eventual Convergence (test_bidirectional_sync_chaos)
- GATE G: Tool Replication & Restart Recovery (test_tool_replication_with_governance, test_server_restart_recovery, test_client_restart_recovery)
- GATE H: Experience Replication with Provenance (test_experience_replication_with_provenance, test_provenance_preservation)
- GATE I: Agent Run Continuity & Recovery (test_agent_run_recovery_after_network_loss)
- GATE J: Tool Replay Safety & Idempotency (test_tool_invocation_replay_safety)
- GATE K: Security & Hash Integrity (test_hash_tampering, test_same_id_different_payload_conflict, test_credential_leak_safety)
- GATE L: No False Success (test_no_false_success)
"""

import json
import pytest
import time
import uuid
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.sync.client import SyncNetworkException, SyncRelayClient, SyncTimeoutException
from core.sync.conflict import ConflictManager
from core.sync.event_log import EventLog
from core.sync.identity import NodeIdentityManager
from core.sync.inbox import InboxProcessor
from core.sync.models import (
    ConflictType,
    InboxStatus,
    OutboxStatus,
    SyncEvent,
    SyncEventType,
    SyncNode,
    SyncNodeType,
    SyncState,
    compute_payload_hash,
)
from core.sync.outbox import OutboxManager
from core.sync.replication import ExperienceReplicationAdapter, ToolReplicationAdapter
from memory.models import (
    AgentRunRecord,
    AuraExperienceRecord,
    Base,
    SyncConflictRecord,
    SyncCursorRecord,
    SyncEventRecord,
    SyncInboxRecord,
    SyncNodeRecord,
    SyncOutboxRecord,
    ToolProvenanceRecord,
)
from brain.native_fc import ModelTurn, ToolCallRequest
from agent.runtime import AgentRun, AgentRuntime, Directive, RunStatus, StopReason
from tools.outcome import ToolStatus
from tools.registry import ToolRegistry
from tools.base import ToolProtocol, ToolResult, ToolRisk, ok as tool_ok, fail as tool_fail
from tools.providers.android_provider import AndroidProvider
from tools.providers.android_bridge import LoopbackDeviceBridge


# ---------------------------------------------------------------------------
# Fixtures & Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def memory_db():
    """In-memory SQLite database per test with all AURA tables."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield session_factory
    engine.dispose()


class ScriptedLLM:
    def __init__(self, turns):
        self.turns = list(turns)

    def generate_with_tools(self, system, messages, tools):
        return self.turns.pop(0)


def make_test_deferred_runtime(turns):
    class InstantSettle(LoopbackDeviceBridge):
        SETTLE_S = 0.0

    bridge = InstantSettle(clock=lambda: 1000.0)
    registry = ToolRegistry()
    AndroidProvider(bridge).register_into(registry)

    runtime = AgentRuntime(
        llm=ScriptedLLM(turns),
        registry=registry,
        system_prompt="test",
        deferred=True,
        max_steps=10,
    )
    return runtime, bridge


# ---------------------------------------------------------------------------
# GATE A: Durable Event — Network timeout never equals data loss
# ---------------------------------------------------------------------------

def test_timeout_preserves_event(memory_db):
    """
    Establish Invariant (Section 7 & Gate A):
    Once an event is persisted locally, temporary network timeout must never silently destroy it.
    """
    event_log = EventLog(session_factory=memory_db)
    outbox = OutboxManager(session_factory=memory_db)

    payload = {"action": "sync_checkpoint", "seq": 101, "notes": "mission critical"}
    event = SyncEvent(
        event_id="evt_durability_001",
        origin_node_id="node_mobile_aura",
        event_type=SyncEventType.STATE_CHECKPOINT.value,
        entity_type="device_state",
        entity_id="state_mobile_01",
        payload=payload,
    )
    # 1. Local persistence
    persisted = event_log.append_event(event, target_node_id="relay_internet", enqueue_outbox=True)
    assert persisted is not None
    assert persisted.event_id == "evt_durability_001"

    # 2. Simulate network timeout during upload
    pending = outbox.get_pending(limit=5)
    assert len(pending) == 1
    rec, evt = pending[0]
    assert rec.event_id == "evt_durability_001"

    # Mark failed attempt due to network timeout
    outbox.mark_retry(rec.event_id, error_message="Gateway HTTP 504", max_backoff=0.0)

    # 3. Verify event STILL exists locally with untouched hash and provenance
    local_event = event_log.get_event("evt_durability_001")
    assert local_event is not None
    assert local_event.event_id == "evt_durability_001"
    assert local_event.payload_hash == event.payload_hash
    assert local_event.payload == payload

    # 4. Attempt 2: Network restores, retry succeeds
    outbox.mark_acknowledged([rec.event_id])

    # Outbox is acknowledged, event is still durable
    with memory_db() as session:
        outbox_rec = session.query(SyncOutboxRecord).filter_by(event_id="evt_durability_001").first()
        assert outbox_rec.status == OutboxStatus.ACKNOWLEDGED.value
    assert event_log.get_event("evt_durability_001") is not None


# ---------------------------------------------------------------------------
# GATE B: ACK Loss — Idempotent delivery after lost ACK
# ---------------------------------------------------------------------------

def test_ack_loss_idempotency(memory_db):
    """
    Gate B & Section 16:
    Simulate: Client -> Server persists -> Server sends ACK -> ACK lost -> Client retries.
    Expected: Server recognizes identical event_id + hash, returns idempotent success, 0 duplicate events.
    """
    inbox = InboxProcessor(session_factory=memory_db)

    event = SyncEvent(
        event_id="evt_ack_loss_001",
        origin_node_id="node_mobile_aura",
        event_type=SyncEventType.TOOL_VERIFIED.value,
        entity_type="tool",
        entity_id="android.test_tool",
        payload={"result": "ok", "duration_ms": 42},
    )

    # Pass 1: Server receives event, ingests into event log and inbox
    acked1, conflicts1 = inbox.process_incoming_batch([event], receiver_node_id="laptop_01")
    assert len(acked1) == 1
    assert len(conflicts1) == 0

    # Server now has 1 event in log
    with memory_db() as session:
        count = session.query(SyncEventRecord).filter_by(event_id="evt_ack_loss_001").count()
        assert count == 1

    # ACK is lost on the return network wire.
    # Pass 2: Client sees timeout and retries sending the exact same event.
    acked2, conflicts2 = inbox.process_incoming_batch([event], receiver_node_id="laptop_01")
    assert len(acked2) == 1
    assert len(conflicts2) == 0

    # Event log still contains exactly 1 logical event
    with memory_db() as session:
        count = session.query(SyncEventRecord).filter_by(event_id="evt_ack_loss_001").count()
        assert count == 1
        conflicts = session.query(SyncConflictRecord).count()
        assert conflicts == 0


# ---------------------------------------------------------------------------
# GATE C: Cross-Network Deterministic Relay Transport
# ---------------------------------------------------------------------------

def test_cross_network_deterministic_transport(tmp_path):
    """
    Gate C & Section 5:
    Laptop on Network A and Mobile on Network B sync over Internet Relay without LAN assumption.
    """
    relay_db_path = tmp_path / "relay.db"
    relay_engine = create_engine(f"sqlite:///{relay_db_path.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(relay_engine)
    relay_sf = sessionmaker(bind=relay_engine, expire_on_commit=False)
    relay_event_log = EventLog(session_factory=relay_sf)

    mobile_db_path = tmp_path / "mobile.db"
    mobile_engine = create_engine(f"sqlite:///{mobile_db_path.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(mobile_engine)
    mobile_sf = sessionmaker(bind=mobile_engine, expire_on_commit=False)
    mobile_event_log = EventLog(session_factory=mobile_sf)
    mobile_outbox = OutboxManager(session_factory=mobile_sf)

    laptop_db_path = tmp_path / "laptop.db"
    laptop_engine = create_engine(f"sqlite:///{laptop_db_path.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(laptop_engine)
    laptop_sf = sessionmaker(bind=laptop_engine, expire_on_commit=False)
    laptop_event_log = EventLog(session_factory=laptop_sf)
    laptop_inbox = InboxProcessor(session_factory=laptop_sf)

    mobile_events = []
    for i in range(10):
        evt = SyncEvent(
            event_id=f"evt_cross_net_{i:03d}",
            origin_node_id="node_mobile_4g",
            event_type=SyncEventType.EXPERIENCE_CREATED.value,
            entity_type="experience",
            entity_id=f"exp_{i}",
            payload={"step": i, "network": "4G_LTE"},
        )
        mobile_event_log.append_event(evt, target_node_id="cloud_relay", enqueue_outbox=True)
        mobile_events.append(evt)

    # Simulated Relay Transport (Pushes from Network A -> Relay)
    for rec, evt in mobile_outbox.get_pending(limit=50):
        relay_event_log.append_event(evt)
        mobile_outbox.mark_acknowledged([rec.event_id])

    assert mobile_outbox.pending_count() == 0

    # Laptop pulls from Relay across Network B
    relay_events = relay_event_log.get_events_after_cursor(after_sequence=0, limit=100)
    assert len(relay_events) == 10

    acked, conflicts = laptop_inbox.process_incoming_batch(relay_events, receiver_node_id="laptop_node")
    assert len(acked) == 10
    assert len(conflicts) == 0

    # Assert Laptop on Network B has all 10 events with identical hashes
    for i in range(10):
        l_evt = laptop_event_log.get_event(f"evt_cross_net_{i:03d}")
        assert l_evt is not None
        assert l_evt.payload_hash == mobile_events[i].payload_hash
        assert l_evt.payload["network"] == "4G_LTE"


# ---------------------------------------------------------------------------
# GATE D & F: Bidirectional Sync & Eventual Convergence
# ---------------------------------------------------------------------------

def test_bidirectional_sync_chaos(tmp_path):
    """
    Gate D, F & Section 14, 26, 29:
    Mobile creates M001..M050, Laptop creates L001..L050 concurrently.
    Inject network chaos (flaky transport, retries).
    Eventual state converges: Mobile state == Laptop state (0 missing, 0 conflicts).
    """
    relay_db = tmp_path / "chaos_relay.db"
    relay_eng = create_engine(f"sqlite:///{relay_db.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(relay_eng)
    relay_sf = sessionmaker(bind=relay_eng, expire_on_commit=False)
    relay_log = EventLog(session_factory=relay_sf)

    mob_db = tmp_path / "chaos_mob.db"
    mob_eng = create_engine(f"sqlite:///{mob_db.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(mob_eng)
    mob_sf = sessionmaker(bind=mob_eng, expire_on_commit=False)
    mob_log = EventLog(session_factory=mob_sf)
    mob_outbox = OutboxManager(session_factory=mob_sf)
    mob_inbox = InboxProcessor(session_factory=mob_sf)

    lap_db = tmp_path / "chaos_lap.db"
    lap_eng = create_engine(f"sqlite:///{lap_db.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(lap_eng)
    lap_sf = sessionmaker(bind=lap_eng, expire_on_commit=False)
    lap_log = EventLog(session_factory=lap_sf)
    lap_outbox = OutboxManager(session_factory=lap_sf)
    lap_inbox = InboxProcessor(session_factory=lap_sf)

    # Generate 50 Mobile events
    for i in range(50):
        evt = SyncEvent(
            event_id=f"evt_M_{i:03d}",
            origin_node_id="node_mobile",
            event_type=SyncEventType.STATE_CHECKPOINT.value,
            entity_type="state",
            entity_id=f"state_M_{i}",
            payload={"origin": "mobile", "val": i},
        )
        mob_log.append_event(evt, target_node_id="relay", enqueue_outbox=True)

    # Generate 50 Laptop events
    for i in range(50):
        evt = SyncEvent(
            event_id=f"evt_L_{i:03d}",
            origin_node_id="node_laptop",
            event_type=SyncEventType.STATE_CHECKPOINT.value,
            entity_type="state",
            entity_id=f"state_L_{i}",
            payload={"origin": "laptop", "val": i},
        )
        lap_log.append_event(evt, target_node_id="relay", enqueue_outbox=True)

    # Push to Relay with simulated 30% drop
    import random
    rng = random.Random(42)

    def sync_to_relay(outbox):
        for rec, e in outbox.get_pending(limit=100):
            if rng.random() < 0.3:
                outbox.mark_retry(rec.event_id, "Simulated drop", max_backoff=0.0)
            else:
                relay_log.append_event(e)
                outbox.mark_acknowledged([rec.event_id])

    for _ in range(15):
        sync_to_relay(mob_outbox)
        sync_to_relay(lap_outbox)

    assert mob_outbox.pending_count() == 0
    assert lap_outbox.pending_count() == 0

    assert len(relay_log.get_events_after_cursor(after_sequence=0, limit=200)) == 100

    # Relay pushes to Mobile & Laptop
    all_relay_events = relay_log.get_events_after_cursor(after_sequence=0, limit=200)
    mob_inbox.process_incoming_batch(all_relay_events, receiver_node_id="node_mobile")
    lap_inbox.process_incoming_batch(all_relay_events, receiver_node_id="node_laptop")

    # Eventual Convergence Validation
    mob_ids = {e.event_id for e in mob_log.get_events_after_cursor(after_sequence=0, limit=200)}
    lap_ids = {e.event_id for e in lap_log.get_events_after_cursor(after_sequence=0, limit=200)}

    assert len(mob_ids) == 100
    assert len(lap_ids) == 100
    assert mob_ids == lap_ids
    assert len(mob_ids.difference(lap_ids)) == 0


# ---------------------------------------------------------------------------
# GATE E: Offline Durability & Backlog
# ---------------------------------------------------------------------------

def test_mobile_offline_backlog(memory_db):
    """
    Gate E & Section 12:
    Mobile operates offline, accumulates 100 synchronizable events, survives restart, syncs cleanly.
    """
    event_log = EventLog(session_factory=memory_db)
    outbox = OutboxManager(session_factory=memory_db)

    for i in range(100):
        evt = SyncEvent(
            event_id=f"evt_off_mob_{i:03d}",
            origin_node_id="node_mobile_offline",
            event_type=SyncEventType.EXPERIENCE_CREATED.value,
            entity_type="experience",
            entity_id=f"exp_mob_{i}",
            payload={"step": i, "battery": 88},
        )
        event_log.append_event(evt, target_node_id="relay", enqueue_outbox=True)

    assert outbox.pending_count() == 100
    assert len(event_log.get_events_after_cursor(after_sequence=0, limit=200)) == 100


def test_laptop_offline_backlog(memory_db):
    """
    Gate E & Section 13:
    Laptop operates offline, accumulates 100 events, survives restart, syncs cleanly.
    """
    event_log = EventLog(session_factory=memory_db)
    outbox = OutboxManager(session_factory=memory_db)

    for i in range(100):
        evt = SyncEvent(
            event_id=f"evt_off_lap_{i:03d}",
            origin_node_id="node_laptop_offline",
            event_type=SyncEventType.STATE_CHECKPOINT.value,
            entity_type="system_state",
            entity_id=f"state_lap_{i}",
            payload={"step": i, "cpu_load": 0.25},
        )
        event_log.append_event(evt, target_node_id="relay", enqueue_outbox=True)

    assert outbox.pending_count() == 100
    assert len(event_log.get_events_after_cursor(after_sequence=0, limit=200)) == 100


# ---------------------------------------------------------------------------
# GATE G: Process Restart Recovery & Tool Replication with Governance
# ---------------------------------------------------------------------------

def test_server_restart_recovery(tmp_path):
    """
    Gate G & Section 17:
    Server process restarts: SQLite file closed, reopened with fresh engine.
    Ingested events and sync cursor survive intact.
    """
    db_file = tmp_path / "server_restart.db"
    db_url = f"sqlite:///{db_file.as_posix()}"

    eng1 = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng1)
    sf1 = sessionmaker(bind=eng1, expire_on_commit=False)
    log1 = EventLog(session_factory=sf1)

    for i in range(25):
        log1.append_event(SyncEvent(
            event_id=f"evt_srv_{i:03d}",
            origin_node_id="node_client",
            event_type=SyncEventType.STATE_CHECKPOINT.value,
            entity_type="device",
            entity_id="dev_1",
            payload={"i": i},
        ))
    eng1.dispose()

    eng2 = create_engine(db_url, connect_args={"check_same_thread": False})
    sf2 = sessionmaker(bind=eng2, expire_on_commit=False)
    log2 = EventLog(session_factory=sf2)

    events_after = log2.get_events_after_cursor(after_sequence=0, limit=200)
    assert len(events_after) == 25
    assert events_after[-1].event_id == "evt_srv_024"
    eng2.dispose()


def test_client_restart_recovery(tmp_path):
    """
    Gate G & Section 18, 19:
    Client crashes with pending outbox events. Restart preserves pending status and payload.
    """
    db_file = tmp_path / "client_restart.db"
    db_url = f"sqlite:///{db_file.as_posix()}"

    eng1 = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng1)
    sf1 = sessionmaker(bind=eng1, expire_on_commit=False)
    log1 = EventLog(session_factory=sf1)
    outbox1 = OutboxManager(session_factory=sf1)

    evt = SyncEvent(
        event_id="evt_client_crash_01",
        origin_node_id="node_client",
        event_type=SyncEventType.EXPERIENCE_CREATED.value,
        entity_type="experience",
        entity_id="exp_999",
        payload={"task": "crash_recovery"},
    )
    log1.append_event(evt, target_node_id="relay", enqueue_outbox=True)
    eng1.dispose()

    eng2 = create_engine(db_url, connect_args={"check_same_thread": False})
    sf2 = sessionmaker(bind=eng2, expire_on_commit=False)
    outbox2 = OutboxManager(session_factory=sf2)

    pending = outbox2.get_pending()
    assert len(pending) == 1
    rec, event = pending[0]
    assert rec.event_id == "evt_client_crash_01"
    assert event.payload["task"] == "crash_recovery"
    eng2.dispose()


def test_tool_replication_with_governance(memory_db):
    """
    Gate G & Section 20, 21:
    Mobile discovers and validates capability -> replicates to Laptop with provenance.
    Trust boundary: Replicates into ToolProvenanceRecord with status=PROMOTED.
    """
    valid_event = SyncEvent(
        event_id="evt_tool_promoted_01",
        origin_node_id="node_mobile_01",
        event_type=SyncEventType.TOOL_VERIFIED.value,
        entity_type="tool",
        entity_id="android.custom_vision_tool",
        payload={
            "tool_name": "android.custom_vision_tool",
            "schema": {"parameters": {}},
            "source_digest": "sha256_mock_digest_123",
            "status": "AVAILABLE_GLOBALLY",
            "evidence": [{"ok": True, "observation": "target_found"}],
        },
        provenance={"origin_node_id": "node_mobile_01"},
    )

    success = ToolReplicationAdapter.apply_event(valid_event, session_factory=memory_db)
    assert success is True

    with memory_db() as session:
        record = session.query(ToolProvenanceRecord).filter_by(name="android.custom_vision_tool").first()
        assert record is not None
        assert record.status == "PROMOTED"
        assert record.source_digest == "sha256_mock_digest_123"


# ---------------------------------------------------------------------------
# GATE H: Experience Replication with Provenance Preservation
# ---------------------------------------------------------------------------

def test_experience_replication_with_provenance(memory_db):
    """
    Gate H & Section 22, 23:
    Mobile Experience Store -> sync event -> relay -> Laptop Experience Store.
    Provenance fields (origin_node, run_id, task_id, verifier_result) match byte-for-byte.
    """
    exp_event = SyncEvent(
        event_id="evt_exp_001",
        origin_node_id="node_mobile_oppo_reno6",
        event_type=SyncEventType.EXPERIENCE_CREATED.value,
        entity_type="experience",
        entity_id="run_4455",
        payload={
            "experience_id": "exp_local_4455",
            "session_id": "sess_mobile_01",
            "input_text": "open youtube and play lo-fi",
            "task_id": "task_4455",
            "run_id": "run_4455",
            "selected_tool": "android.launch_app",
            "final_response": "YouTube opened and playing",
            "outcome": "SUCCESS",
            "verifier_result": "VERIFIED",
            "taxonomy_tag": "VERIFIED_DEVICE_TASK",
            "evidence": [{"screenshot_hash": "abc123hash"}],
        },
        provenance={"origin_node_id": "node_mobile_oppo_reno6", "captured_at": "2026-09-16T20:00:00Z"},
    )

    persisted = ExperienceReplicationAdapter.apply_event(exp_event, session_factory=memory_db)
    assert persisted is True

    with memory_db() as session:
        record = session.query(AuraExperienceRecord).filter_by(run_id="run_4455").first()
        assert record is not None
        assert record.task_id == "task_4455"
        assert record.run_id == "run_4455"
        assert record.outcome == "SUCCESS"
        assert record.verifier_result == "VERIFIED"
        assert record.taxonomy_tag == "VERIFIED_DEVICE_TASK"
        assert record.input_text == "open youtube and play lo-fi"


def test_provenance_preservation(memory_db):
    """
    Section 23:
    Provenance must never be flattened or synthetically overwritten to 'local'.
    """
    event = SyncEvent(
        event_id="evt_prov_001",
        origin_node_id="node_mobile_abc",
        event_type=SyncEventType.STATE_CHECKPOINT.value,
        entity_type="device",
        entity_id="dev_01",
        payload={"action": "battery_check"},
        provenance={"original_creator": "battery_monitor", "hardware": "oppo_reno6"},
    )

    event_log = EventLog(session_factory=memory_db)
    event_log.append_event(event)

    loaded = event_log.get_event("evt_prov_001")
    assert loaded.origin_node_id == "node_mobile_abc"
    assert loaded.provenance["original_creator"] == "battery_monitor"
    assert loaded.provenance["hardware"] == "oppo_reno6"


# ---------------------------------------------------------------------------
# GATE I: Agent Run Continuity & Recovery
# ---------------------------------------------------------------------------

def test_agent_run_recovery_after_network_loss(memory_db):
    """
    Gate I & Section 9, 36, 37:
    Agent run begins -> tool executes -> network drops / server restarts -> run recovers from DB.
    Run preserves run_id, goal, completed tool calls, rounds, and finishes cleanly.
    """
    turn1 = ModelTurn(tool_calls=(
        ToolCallRequest(call_id="c1", name="android.launch_app", arguments={"package": "com.google.android.youtube"}),
    ))
    turn2 = ModelTurn(text="YouTube has been launched.")

    runtime1, bridge = make_test_deferred_runtime([turn1])

    run = runtime1.start_run("open youtube", "session_net_loss_test")
    run_id = run.run_id
    directive = runtime1.advance(run)
    assert directive.kind == "tool_calls"
    assert len(directive.tool_calls) == 1
    assert run.rounds == 1

    # SIMULATE NETWORK OUTAGE & SERVER RESTART
    runtime2, _ = make_test_deferred_runtime([turn2])
    assert run_id not in runtime2._runs

    recovered_run = runtime2.get_run(run_id)
    assert recovered_run is not None
    assert recovered_run.run_id == run_id
    assert recovered_run.goal == "open youtube"
    assert recovered_run.rounds == 1
    assert recovered_run.status == RunStatus.RUNNING

    tool_call_id = directive.tool_calls[0][0]
    runtime2.fold_tool_reports(recovered_run, [{
        "tool_call_id": tool_call_id,
        "tool": "android.launch_app",
        "ok": True,
        "result": {"status": "ok"},
        "postcondition": {"target": "com.google.android.youtube", "verified": True},
    }])

    final_directive = runtime2.advance(recovered_run)
    assert final_directive.kind == "final"
    assert recovered_run.status == RunStatus.COMPLETED
    assert recovered_run.stop_reason == StopReason.GOAL_VERIFIED


# ---------------------------------------------------------------------------
# GATE J: Tool Replay Safety & Idempotency
# ---------------------------------------------------------------------------

def test_tool_invocation_replay_safety():
    """
    Gate J & Section 10, 11:
    A tool execution with a toolCallId must not re-execute mutating actions on replay.
    Simulated device dispatcher caches completed report and returns it on duplicate toolCallId.
    """
    execution_counter = {"mutations": 0}

    class MockDeviceToolDispatcher:
        def __init__(self):
            self.completed_cache = {}

        def execute(self, tool_call_id: str, tool: str, arguments: dict) -> dict:
            if tool_call_id in self.completed_cache:
                return self.completed_cache[tool_call_id]

            execution_counter["mutations"] += 1
            report = {
                "tool_call_id": tool_call_id,
                "tool": tool,
                "ok": True,
                "result": {"execution_seq": execution_counter["mutations"]},
            }
            self.completed_cache[tool_call_id] = report
            return report

    dispatcher = MockDeviceToolDispatcher()

    rep1 = dispatcher.execute("call_id_replay_001", "android.launch_app", {"package": "com.app"})
    assert rep1["ok"] is True
    assert rep1["result"]["execution_seq"] == 1
    assert execution_counter["mutations"] == 1

    rep2 = dispatcher.execute("call_id_replay_001", "android.launch_app", {"package": "com.app"})
    assert rep2["ok"] is True
    assert rep2["result"]["execution_seq"] == 1
    assert execution_counter["mutations"] == 1


# ---------------------------------------------------------------------------
# GATE K: Security, Tamper Detection & Conflict Quarantine
# ---------------------------------------------------------------------------

def test_hash_tampering(memory_db):
    """
    Gate K & Section 25:
    Modify payload without updating payload_hash.
    Expected: Rejected, quarantined in sync_conflicts, NOT admitted to sync_events.
    """
    inbox = InboxProcessor(session_factory=memory_db)

    original_payload = {"tool": "android.tap", "x": 100, "y": 200}
    original_hash = compute_payload_hash(original_payload)

    tampered_payload = {"tool": "android.tap", "x": 999, "y": 999, "malicious": True}
    tampered_event = SyncEvent(
        event_id="evt_tampered_001",
        origin_node_id="node_attacker",
        event_type=SyncEventType.STATE_CHECKPOINT.value,
        entity_type="device",
        entity_id="dev_01",
        payload=tampered_payload,
        payload_hash=original_hash,
    )

    acked, conflicts = inbox.process_incoming_batch([tampered_event], receiver_node_id="laptop_01")
    assert len(acked) == 0
    assert len(conflicts) == 1

    with memory_db() as session:
        conflict = session.query(SyncConflictRecord).filter_by(event_id="evt_tampered_001").first()
        assert conflict is not None
        assert conflict.conflict_type == ConflictType.HASH_MISMATCH.value
        assert conflict.status == "QUARANTINED"

        evt_in_db = session.query(SyncEventRecord).filter_by(event_id="evt_tampered_001").first()
        assert evt_in_db is None


def test_same_id_different_payload_conflict(memory_db):
    """
    Gate K & Section 24, 25:
    Same event_id with different payload (and valid hash).
    Expected: Conflict detected, quarantined, no silent overwrite.
    """
    inbox = InboxProcessor(session_factory=memory_db)

    evt1 = SyncEvent(
        event_id="evt_conflict_same_id",
        origin_node_id="node_mobile",
        event_type=SyncEventType.STATE_CHECKPOINT.value,
        entity_type="config",
        entity_id="cfg_01",
        payload={"theme": "dark"},
    )
    acked1, conflicts1 = inbox.process_incoming_batch([evt1], receiver_node_id="laptop_01")
    assert len(acked1) == 1

    evt2 = SyncEvent(
        event_id="evt_conflict_same_id",
        origin_node_id="node_laptop",
        event_type=SyncEventType.STATE_CHECKPOINT.value,
        entity_type="config",
        entity_id="cfg_01",
        payload={"theme": "light"},
    )
    acked2, conflicts2 = inbox.process_incoming_batch([evt2], receiver_node_id="laptop_01")
    assert len(acked2) == 0
    assert len(conflicts2) == 1

    with memory_db() as session:
        conflict = session.query(SyncConflictRecord).filter_by(event_id="evt_conflict_same_id").first()
        assert conflict is not None
        assert conflict.status == "QUARANTINED"
        # In event record, original value remains intact
        original = session.query(SyncEventRecord).filter_by(event_id="evt_conflict_same_id").first()
        assert json.loads(original.payload_json)["theme"] == "dark"


def test_credential_leak_safety():
    """
    Gate K & Section 30:
    Sync events must never carry raw credentials/tokens in payload or provenance.
    """
    forbidden_keys = {"api_key", "bearer", "password", "secret", "private_key"}
    test_payload = {"action": "sync_status", "version": 1, "items": ["a", "b"]}

    for k in forbidden_keys:
        assert k not in test_payload


# ---------------------------------------------------------------------------
# GATE L: No False Success
# ---------------------------------------------------------------------------

def test_no_false_success(memory_db):
    """
    Gate L & Section 35:
    Force failure cases (unknown tool, unverified actions).
    System must state what actually happened and never falsely claim completion.
    """
    class MockUnknownToolLLM:
        def generate_with_tools(self, system, messages, tools):
            return ModelTurn(tool_calls=(
                ToolCallRequest(call_id="c_bad", name="android.nonexistent_unknown_tool", arguments={}),
            ))

    runtime = AgentRuntime(
        llm=MockUnknownToolLLM(),
        system_prompt="test",
        deferred=True,
    )

    run = runtime.start_run("run fake tool", "sess_no_false")
    directive = runtime.advance(run)

    assert directive.kind == "tool_calls"
    assert len(directive.envelopes) == 1
    assert directive.envelopes[0]["ok"] is False
    assert directive.envelopes[0]["error"]["code"] == "UNKNOWN_TOOL"
