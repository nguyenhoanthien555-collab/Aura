"""
Subprocess worker script for P4.5.1 Real Process Restart Durability tests.
Exercises true OS-level process termination and state recovery across PIDs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Ensure repo root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.sync.event_log import EventLog
from core.sync.invocation_ledger import DurableInvocationLedger
from core.sync.models import SyncEvent, SyncEventType
from core.sync.outbox import OutboxManager
from memory.models import AgentRunRecord, Base
from memory.sqlite import init_sync_tables


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True, choices=["stage1", "stage2"])
    parser.add_argument("--db", required=True)
    parser.add_argument("--prev-pid", type=int, default=0)
    args = parser.parse_args()

    pid = os.getpid()
    db_path = os.path.abspath(args.db)
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    session_factory = sessionmaker(bind=engine)

    if args.phase == "stage1":
        print(f"[STAGE1_PID: {pid}]")
        init_sync_tables(bind=engine)

        event_log = EventLog(session_factory=session_factory)
        outbox = OutboxManager(session_factory=session_factory)
        ledger = DurableInvocationLedger(session_factory=session_factory)

        # 1. Persist events + outbox
        e1 = SyncEvent(
            event_id="evt_proc_001",
            origin_node_id="node_stage1_worker",
            event_type=SyncEventType.STATE_CHECKPOINT.value,
            entity_type="delta",
            entity_id="res_001",
            payload={"action": "create", "value": 100},
        )
        e2 = SyncEvent(
            event_id="evt_proc_002",
            origin_node_id="node_stage1_worker",
            event_type=SyncEventType.CAPABILITY_UPDATE.value,
            entity_type="delta",
            entity_id="res_002",
            payload={"action": "update", "value": 200},
        )
        event_log.append_event(e1, enqueue_outbox=True)
        event_log.append_event(e2, enqueue_outbox=True)

        # 2. Persist AgentRun
        with engine.begin() as conn:
            run_rec = AgentRunRecord(
                run_id="run_proc_crash_99",
                task_id="task_proc_crash_99",
                session_id="session_proc_test",
                goal="Multi-process restart resilience",
                status="running",
                rounds=2,
                messages_json=json.dumps([
                    {"role": "user", "content": "open app"},
                    {"role": "assistant", "content": "tapping screen"},
                ]),
                unverified_json="[]",
                created_at=1000.0,
            )
            session = session_factory()
            session.add(run_rec)
            session.commit()
            session.close()

        # 3. Persist Invocation
        ledger.record_received(
            invocation_id="invo_subprocess_001",
            tool="android.tap",
            arguments={"x": 350, "y": 720},
            run_id="run_proc_crash_99",
            tool_call_id="call_subprocess_tap_01",
        )
        ledger.record_completed(
            tool_call_id_or_inv_id="call_subprocess_tap_01",
            result={"ok": True, "action": "tap", "postcondition": {"status": "verified"}},
            evidence_hash="ev_hash_abc123",
        )

        print("[STAGE1_COMPLETED: evt_proc_001, evt_proc_002, run_proc_crash_99, call_subprocess_tap_01]")
        sys.exit(0)

    elif args.phase == "stage2":
        print(f"[STAGE2_PID: {pid}]")
        if args.prev_pid > 0 and args.prev_pid == pid:
            print("[ERROR: PID matched previous process; not a real process boundary]")
            sys.exit(1)

        event_log = EventLog(session_factory=session_factory)
        outbox = OutboxManager(session_factory=session_factory)
        ledger = DurableInvocationLedger(session_factory=session_factory)

        # 1. Verify events survived intact
        e1 = event_log.get_event("evt_proc_001")
        e2 = event_log.get_event("evt_proc_002")
        assert e1 is not None, "evt_proc_001 missing after restart"
        assert e2 is not None, "evt_proc_002 missing after restart"
        assert e1.payload.get("value") == 100
        assert e2.payload.get("value") == 200

        # 2. Verify outbox pending work survived
        pending = outbox.get_pending()
        pending_ids = [evt.event_id for _, evt in pending]
        assert "evt_proc_001" in pending_ids, "evt_proc_001 not pending in outbox"
        assert "evt_proc_002" in pending_ids, "evt_proc_002 not pending in outbox"

        # 3. Verify AgentRun restored
        session = session_factory()
        run_rec = session.query(AgentRunRecord).filter_by(run_id="run_proc_crash_99").first()
        session.close()
        assert run_rec is not None, "AgentRunRecord missing after restart"
        assert run_rec.status == "running"
        assert run_rec.rounds == 2
        msgs = json.loads(run_rec.messages_json)
        assert len(msgs) == 2

        # 4. Verify Invocation Replay Protection survived
        replay = ledger.check_replay("call_subprocess_tap_01")
        assert replay is not None, "Replay check returned None for completed invocation"
        assert replay.get("ok") is True
        assert replay.get("postcondition", {}).get("status") == "verified"

        # 5. Drain outbox
        outbox.mark_acknowledged(["evt_proc_001", "evt_proc_002"])
        assert outbox.pending_count() == 0, "Outbox pending count should be 0 after ACK"

        print("[STAGE2_VERIFIED: SUCCESS]")
        sys.exit(0)


if __name__ == "__main__":
    main()
