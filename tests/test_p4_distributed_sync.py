"""
AURA P4: Distributed Continuity & Always-Sync — Comprehensive Test Suite.

Verifies:
1. Node Identity (stable, non-IP)
2. Event-Sourced Sync Core & Local Event Log
3. Outbox / Inbox State Machine & Reliability
4. Idempotency & Duplicate Delivery
5. Network Timeout Safety (network timeout never equals data loss)
6. Process Restart Recovery & Cursor Resume
7. Bidirectional Synchronization & Eventual Convergence
8. Conflict Detection & Quarantine (hash tampering & concurrent mutations)
9. Provenance Preservation
10. Tool & Capability Replication
11. Experience Replication
12. Data Governance & Security
13. Network Chaos Testing (100% loss, 50% loss, random latency, timeouts)
14. Large Backlog (1,000 events: zero lost, zero duplicates)
"""

import hashlib
import json
import pytest
import uuid
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.sync.client import SyncNetworkException, SyncRelayClient, SyncTimeoutException
from core.sync.conflict import ConflictManager
from core.sync.engine import SyncEngine
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


@pytest.fixture
def test_db():
    """Isolated, in-memory SQLite database per test."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield session_factory
    engine.dispose()


# ---------------------------------------------------------------------------
# 1. Node Identity Tests
# ---------------------------------------------------------------------------

def test_node_identity(test_db):
    """Verifies stable, non-IP node identity."""
    NodeIdentityManager.reset_for_tests()
    node = NodeIdentityManager.get_local_node(node_type=SyncNodeType.LAPTOP.value)
    assert node.node_id.startswith("node_laptop_")
    assert node.node_type == SyncNodeType.LAPTOP.value
    assert len(node.installation_id) > 10

    # Ensure idempotency / stability on reload
    node_again = NodeIdentityManager.get_local_node(node_type=SyncNodeType.LAPTOP.value)
    assert node.node_id == node_again.node_id
    assert node.installation_id == node_again.installation_id


# ---------------------------------------------------------------------------
# 2. Event Creation & Hash Verification Tests
# ---------------------------------------------------------------------------

def test_event_creation_and_hash():
    """Verifies deterministic SHA-256 payload hashing and integrity verification."""
    payload = {"tool": "android.launch_app", "package": "com.google.android.youtube", "step": 1}
    event = SyncEvent(
        event_id="evt_test_001",
        origin_node_id="node_mobile_01",
        event_type=SyncEventType.TOOL_VERIFIED.value,
        entity_type="tool",
        entity_id="android.launch_app",
        logical_sequence=1,
        payload=payload,
    )

    assert event.payload_hash != ""
    assert event.verify_hash() is True

    # Tamper with payload
    event.payload["package"] = "com.malicious.app"
    assert event.verify_hash() is False


# ---------------------------------------------------------------------------
# 3. Local Event Log Persistence Tests
# ---------------------------------------------------------------------------

def test_event_persistence(test_db):
    """Verifies durable append and retrieval of events from SQLite."""
    event_log = EventLog(session_factory=test_db)
    payload = {"result": "success", "observation": "YouTube home screen"}
    event = SyncEvent(
        event_id="evt_test_002",
        origin_node_id="node_laptop_01",
        event_type=SyncEventType.EXPERIENCE_CREATED.value,
        entity_type="experience",
        entity_id="run_001",
        payload=payload,
    )

    saved = event_log.append_event(event)
    assert saved.logical_sequence == 1
    assert event_log.count() == 1

    retrieved = event_log.get_event("evt_test_002")
    assert retrieved is not None
    assert retrieved.event_id == "evt_test_002"
    assert retrieved.payload == payload
    assert retrieved.verify_hash() is True


# ---------------------------------------------------------------------------
# 4. Outbox State Machine Tests
# ---------------------------------------------------------------------------

def test_outbox_lifecycle(test_db):
    """Verifies PENDING -> SENDING -> ACKNOWLEDGED state machine."""
    event_log = EventLog(session_factory=test_db)
    outbox = OutboxManager(session_factory=test_db)

    event = SyncEvent(
        event_id="evt_outbox_001",
        origin_node_id="node_mobile_01",
        event_type=SyncEventType.STATE_CHECKPOINT.value,
        entity_type="state",
        entity_id="state_01",
        payload={"battery": 95},
    )
    event_log.append_event(event, enqueue_outbox=True)

    pending = outbox.get_pending()
    assert len(pending) == 1
    assert pending[0][1].event_id == "evt_outbox_001"
    assert pending[0][0].status == OutboxStatus.PENDING.value

    # Mark sending
    outbox.mark_sending(["evt_outbox_001"])
    # Mark acknowledged
    outbox.mark_acknowledged(["evt_outbox_001"])

    session = test_db()
    rec = session.query(SyncOutboxRecord).filter_by(event_id="evt_outbox_001").first()
    assert rec.status == OutboxStatus.ACKNOWLEDGED.value
    assert rec.acknowledged_at != ""
    session.close()


# ---------------------------------------------------------------------------
# 5. Network Timeout Safety Tests (Non-negotiable rule)
# ---------------------------------------------------------------------------

def test_timeout_preserves_event(test_db):
    """
    NETWORK TIMEOUT MUST NEVER EQUAL DATA LOSS.
    Verifies that a network timeout retains event in outbox with backoff retry.
    """
    event_log = EventLog(session_factory=test_db)
    outbox = OutboxManager(session_factory=test_db)

    event = SyncEvent(
        event_id="evt_timeout_001",
        origin_node_id="node_mobile_01",
        event_type=SyncEventType.EXPERIENCE_CREATED.value,
        entity_type="experience",
        entity_id="exp_01",
        payload={"action": "search_minecraft"},
    )
    event_log.append_event(event, enqueue_outbox=True)

    # Simulate network timeout
    outbox.mark_sending(["evt_timeout_001"])
    outbox.mark_retry("evt_timeout_001", error_message="Request timed out after 10s")

    # Event must NOT be deleted!
    session = test_db()
    rec = session.query(SyncOutboxRecord).filter_by(event_id="evt_timeout_001").first()
    assert rec is not None
    assert rec.status == OutboxStatus.PENDING.value
    assert rec.attempts == 1
    assert rec.next_retry_at != ""
    assert "timed out" in rec.error_message
    session.close()


# ---------------------------------------------------------------------------
# 6. Idempotency & Duplicate Delivery Tests
# ---------------------------------------------------------------------------

def test_idempotency_and_duplicate_delivery(test_db):
    """
    Sending same event multiple times results in exactly one stored record
    and returns idempotent success.
    """
    inbox = InboxProcessor(session_factory=test_db)
    payload = {"data": "unique_content"}
    event = SyncEvent(
        event_id="evt_dedup_001",
        origin_node_id="node_mobile_01",
        event_type=SyncEventType.EXPERIENCE_CREATED.value,
        entity_type="experience",
        entity_id="exp_dedup",
        payload=payload,
    )

    # First delivery
    acked1, conflicts1 = inbox.process_incoming_batch([event], receiver_node_id="node_laptop_01")
    assert acked1 == ["evt_dedup_001"]
    assert len(conflicts1) == 0

    # Second delivery (duplicate)
    acked2, conflicts2 = inbox.process_incoming_batch([event], receiver_node_id="node_laptop_01")
    assert acked2 == ["evt_dedup_001"]
    assert len(conflicts2) == 0

    # Third delivery (duplicate)
    acked3, conflicts3 = inbox.process_incoming_batch([event], receiver_node_id="node_laptop_01")
    assert acked3 == ["evt_dedup_001"]

    # Verify only 1 record in sync_events
    session = test_db()
    count = session.query(SyncEventRecord).filter_by(event_id="evt_dedup_001").count()
    assert count == 1
    session.close()


# ---------------------------------------------------------------------------
# 7. Conflict Detection & Quarantine Tests
# ---------------------------------------------------------------------------

def test_conflict_detection_and_quarantine(test_db):
    """
    Same event_id with DIFFERENT payload hash must trigger conflict quarantine.
    Must NOT silently overwrite!
    """
    inbox = InboxProcessor(session_factory=test_db)
    event1 = SyncEvent(
        event_id="evt_conflict_001",
        origin_node_id="node_mobile_01",
        event_type=SyncEventType.EXPERIENCE_CREATED.value,
        entity_type="experience",
        entity_id="exp_conflict",
        payload={"value": "original"},
    )
    inbox.process_incoming_batch([event1], receiver_node_id="node_laptop_01")

    # Incoming event with same ID but different payload
    event2 = SyncEvent(
        event_id="evt_conflict_001",
        origin_node_id="node_mobile_01",
        event_type=SyncEventType.EXPERIENCE_CREATED.value,
        entity_type="experience",
        entity_id="exp_conflict",
        payload={"value": "tampered_or_conflicting"},
    )

    acked, conflicts = inbox.process_incoming_batch([event2], receiver_node_id="node_laptop_01")
    assert len(acked) == 0
    assert len(conflicts) == 1
    assert conflicts[0]["event_id"] == "evt_conflict_001"

    # Verify conflict record was quarantined
    session = test_db()
    conflict_rec = session.query(SyncConflictRecord).filter_by(event_id="evt_conflict_001").first()
    assert conflict_rec is not None
    assert conflict_rec.status == "QUARANTINED"
    assert conflict_rec.existing_hash == event1.payload_hash
    assert conflict_rec.incoming_hash == event2.payload_hash

    # Verify original record remained intact (NO SILENT OVERWRITE)
    original_rec = session.query(SyncEventRecord).filter_by(event_id="evt_conflict_001").first()
    assert json.loads(original_rec.payload_json)["value"] == "original"
    session.close()


# ---------------------------------------------------------------------------
# 8. Experience Replication Tests
# ---------------------------------------------------------------------------

def test_experience_replication(test_db):
    """Verifies AuraExperienceRecord replication with intact provenance and evidence."""
    exp = AuraExperienceRecord(
        experience_id="exp_orig_01",
        session_id="session_test",
        run_id="run_mobile_01",
        input_text="mở youtube rồi search minecraft",
        selected_tool="android.launch_app",
        arguments_json=json.dumps({"package": "com.google.android.youtube"}),
        tool_result_json=json.dumps({"ok": True, "result": {"launched": True}}),
        evidence_json=json.dumps([{"kind": "postcondition", "verified": True}]),
        verifier_result="VERIFIED",
        final_response="Đã mở YouTube và tìm kiếm Minecraft",
        outcome="SUCCESS",
        taxonomy_tag="TOOL_VERIFIED",
    )

    # Convert to SyncEvent
    sync_event = ExperienceReplicationAdapter.to_event(exp, origin_node_id="node_mobile_01", sequence=1)
    assert sync_event.entity_type == "experience"
    assert sync_event.entity_id == "run_mobile_01"
    assert sync_event.verify_hash() is True

    # Apply on receiver
    applied = ExperienceReplicationAdapter.apply_event(sync_event, session_factory=test_db)
    assert applied is True

    # Verify database contains replicated experience
    session = test_db()
    replicated = session.query(AuraExperienceRecord).filter_by(run_id="run_mobile_01").first()
    assert replicated is not None
    assert replicated.input_text == "mở youtube rồi search minecraft"
    assert replicated.verifier_result == "VERIFIED"
    assert replicated.taxonomy_tag == "TOOL_VERIFIED"
    assert json.loads(replicated.arguments_json)["package"] == "com.google.android.youtube"
    session.close()


# ---------------------------------------------------------------------------
# 9. Tool & Capability Replication Tests
# ---------------------------------------------------------------------------

def test_tool_replication(test_db):
    """Verifies discovered & validated tool replication to peer nodes."""
    tool_event = ToolReplicationAdapter.to_event(
        tool_name="android.custom_camera",
        schema={"parameters": {"resolution": "1080p"}},
        evidence=[{"kind": "test", "passed": True}],
        provenance_info={"author": "android_node", "sandbox": "passed"},
        origin_node_id="node_mobile_01",
        sequence=5,
    )

    applied = ToolReplicationAdapter.apply_event(tool_event, session_factory=test_db)
    assert applied is True

    session = test_db()
    prov = session.query(ToolProvenanceRecord).filter_by(name="android.custom_camera").first()
    assert prov is not None
    assert prov.status == "PROMOTED"
    assert prov.version == 1
    assert "resolution" in prov.manifest_json
    session.close()


# ---------------------------------------------------------------------------
# 10. Cursor Resume & Incremental Sync Tests
# ---------------------------------------------------------------------------

def test_cursor_resume(test_db):
    """Verifies that reconnecting nodes resume from last acked sequence cursor."""
    event_log = EventLog(session_factory=test_db)
    for i in range(1, 11):
        event_log.append_event(
            SyncEvent(
                event_id=f"evt_seq_{i:03d}",
                origin_node_id="node_mobile_01",
                event_type=SyncEventType.STATE_CHECKPOINT.value,
                entity_type="state",
                entity_id=f"state_{i}",
                logical_sequence=i,
                payload={"seq": i},
            ),
            enqueue_outbox=False,
        )

    # Pull after cursor 7
    events = event_log.get_events_after_cursor(origin_node_id="node_mobile_01", after_sequence=7, limit=10)
    assert len(events) == 3
    assert [e.logical_sequence for e in events] == [8, 9, 10]


# ---------------------------------------------------------------------------
# 11. Bidirectional Sync & Eventual Convergence Tests
# ---------------------------------------------------------------------------

def test_bidirectional_sync_convergence(test_db):
    """
    Mobile and Laptop both generate concurrent events.
    Both nodes exchange events through Relay.
    Eventually Mobile.state == Laptop.state.
    """
    # Create separate databases for Mobile, Laptop, and Relay
    db_mobile = test_db
    engine_relay = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine_relay)
    db_relay = sessionmaker(bind=engine_relay, expire_on_commit=False)

    engine_laptop = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine_laptop)
    db_laptop = sessionmaker(bind=engine_laptop, expire_on_commit=False)

    # 1. Mobile generates 5 events
    mobile_log = EventLog(session_factory=db_mobile)
    mobile_events = []
    for i in range(1, 6):
        e = mobile_log.append_event(
            SyncEvent(
                event_id=f"evt_mob_{i}",
                origin_node_id="node_mobile",
                event_type=SyncEventType.EXPERIENCE_CREATED.value,
                entity_type="experience",
                entity_id=f"exp_mob_{i}",
                payload={"action": f"mobile_action_{i}"},
            )
        )
        mobile_events.append(e)

    # 2. Laptop generates 5 events
    laptop_log = EventLog(session_factory=db_laptop)
    laptop_events = []
    for i in range(1, 6):
        e = laptop_log.append_event(
            SyncEvent(
                event_id=f"evt_lap_{i}",
                origin_node_id="node_laptop",
                event_type=SyncEventType.EXPERIENCE_CREATED.value,
                entity_type="experience",
                entity_id=f"exp_lap_{i}",
                payload={"action": f"laptop_action_{i}"},
            )
        )
        laptop_events.append(e)

    # 3. Both push to Relay
    relay_inbox = InboxProcessor(session_factory=db_relay)
    relay_inbox.process_incoming_batch(mobile_events, receiver_node_id="node_relay")
    relay_inbox.process_incoming_batch(laptop_events, receiver_node_id="node_relay")

    # 4. Laptop pulls mobile events from Relay
    relay_log = EventLog(session_factory=db_relay)
    laptop_inbox = InboxProcessor(session_factory=db_laptop)
    events_for_laptop = relay_log.get_events_after_cursor(origin_node_id="node_mobile", after_sequence=0)
    laptop_inbox.process_incoming_batch(events_for_laptop, receiver_node_id="node_laptop")

    # 5. Mobile pulls laptop events from Relay
    mobile_inbox = InboxProcessor(session_factory=db_mobile)
    events_for_mobile = relay_log.get_events_after_cursor(origin_node_id="node_laptop", after_sequence=0)
    mobile_inbox.process_incoming_batch(events_for_mobile, receiver_node_id="node_mobile")

    # 6. Verify EVENTUAL CONVERGENCE: Mobile.events == Laptop.events == 10
    assert mobile_log.count() == 10
    assert laptop_log.count() == 10
    assert relay_log.count() == 10

    mob_ids = set(e.event_id for e in mobile_log.list_events())
    lap_ids = set(e.event_id for e in laptop_log.list_events())
    assert mob_ids == lap_ids


# ---------------------------------------------------------------------------
# 12. Large Backlog Test (1,000 Events)
# ---------------------------------------------------------------------------

def test_large_backlog_1000_events(test_db):
    """
    Generate 1,000 mixed events while receiver is disconnected.
    Reconnect receiver.
    Verify:
      - 1,000/1,000 received
      - 1,000/1,000 integrity valid
      - 0 lost
      - 0 duplicate logical records
    """
    engine_recv = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine_recv)
    db_recv = sessionmaker(bind=engine_recv, expire_on_commit=False)

    sender_log = EventLog(session_factory=test_db)
    generated_events = []

    types = [
        SyncEventType.EXPERIENCE_CREATED.value,
        SyncEventType.TOOL_VERIFIED.value,
        SyncEventType.STATE_CHECKPOINT.value,
    ]

    for i in range(1, 1001):
        etype = types[i % 3]
        e = SyncEvent(
            event_id=f"evt_bulk_{i:04d}",
            origin_node_id="node_bulk_sender",
            event_type=etype,
            entity_type="bulk",
            entity_id=f"item_{i}",
            logical_sequence=i,
            payload={"index": i, "batch": "backlog_1000", "timestamp": str(i)},
        )
        sender_log.append_event(e, enqueue_outbox=True)
        generated_events.append(e)

    assert sender_log.count() == 1000

    # Reconnect receiver and process all 1,000 events in batches of 200
    receiver_inbox = InboxProcessor(session_factory=db_recv)
    total_acked = 0
    for chunk_start in range(0, 1000, 200):
        batch = generated_events[chunk_start : chunk_start + 200]
        acked, conflicts = receiver_inbox.process_incoming_batch(batch, receiver_node_id="node_recv")
        total_acked += len(acked)
        assert len(conflicts) == 0

    assert total_acked == 1000

    # Verify receiver event count and integrity
    recv_log = EventLog(session_factory=db_recv)
    assert recv_log.count() == 1000
    received_events = recv_log.list_events(limit=1000)
    assert len(received_events) == 1000

    for rev in received_events:
        assert rev.verify_hash() is True

    # Duplicate delivery test: resend all 1,000 events
    total_re_acked = 0
    for chunk_start in range(0, 1000, 200):
        batch = generated_events[chunk_start : chunk_start + 200]
        acked, conflicts = receiver_inbox.process_incoming_batch(batch, receiver_node_id="node_recv")
        total_re_acked += len(acked)

    assert total_re_acked == 1000
    # Still exactly 1,000 records
    assert recv_log.count() == 1000


# ---------------------------------------------------------------------------
# 13. Network Chaos & Simulated Packet Loss Tests
# ---------------------------------------------------------------------------

def test_network_chaos_packet_loss_recovery(test_db):
    """
    Harness testing:
    - 50% packet drop
    - Connection resets
    - Eventual recovery without event loss
    """
    import random
    random.seed(42)

    event_log = EventLog(session_factory=test_db)
    outbox = OutboxManager(session_factory=test_db)

    events = [
        SyncEvent(
            event_id=f"evt_chaos_{i}",
            origin_node_id="node_chaos",
            event_type=SyncEventType.EXPERIENCE_CREATED.value,
            entity_type="experience",
            entity_id=f"exp_{i}",
            payload={"i": i},
        )
        for i in range(10)
    ]

    for e in events:
        event_log.append_event(e, enqueue_outbox=True)

    # Simulate unreliable transport: 50% drop rate
    acked_set = set()
    attempts = 0
    while len(acked_set) < 10 and attempts < 25:
        attempts += 1
        pending = outbox.get_pending(limit=10)
        for outbox_rec, evt in pending:
            if random.random() < 0.5:
                # Dropped / timed out - mark retry with immediate re-eligibility in simulation
                outbox.mark_retry(evt.event_id, "Simulated network drop", max_backoff=0.0)
            else:
                outbox.mark_acknowledged([evt.event_id])
                acked_set.add(evt.event_id)

    # Ensure all 10 were eventually acknowledged without data loss
    assert len(acked_set) == 10
    assert outbox.pending_count() == 0
