"""
Subprocess worker for P4.5.2 Critical A: Crash-After-Side-Effect test.

Stage 1 (PROCESS 1):
  record_received -> record_executing -> execute side effect -> EXIT (no record_completed)

Stage 2 (PROCESS 2):
  same DB -> check_replay() must NOT return None -> verify side_effect_count == 1
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.sync.invocation_ledger import DurableInvocationLedger
from memory.sqlite import init_sync_tables


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True, choices=["stage1", "stage2"])
    parser.add_argument("--db", required=True)
    parser.add_argument("--counter-file", required=True)
    parser.add_argument("--prev-pid", type=int, default=0)
    args = parser.parse_args()

    pid = os.getpid()
    db_path = os.path.abspath(args.db)
    counter_path = os.path.abspath(args.counter_file)
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    session_factory = sessionmaker(bind=engine)

    INVOCATION_ID = "inv_crash_window_001"
    TOOL_CALL_ID = "call_crash_tap_001"
    TOOL_NAME = "android.tap"
    TOOL_ARGS = {"x": 500, "y": 900}

    if args.phase == "stage1":
        print(f"[STAGE1_PID: {pid}]")
        init_sync_tables(bind=engine)

        ledger = DurableInvocationLedger(session_factory=session_factory)

        # 1. Record RECEIVED
        rec = ledger.record_received(
            invocation_id=INVOCATION_ID,
            tool=TOOL_NAME,
            arguments=TOOL_ARGS,
            run_id="run_crash_test_001",
            tool_call_id=TOOL_CALL_ID,
        )
        assert rec.lifecycle_state == "RECEIVED", f"Expected RECEIVED, got {rec.lifecycle_state}"

        # 2. Record EXECUTING
        ledger.record_executing(TOOL_CALL_ID)
        rec2 = ledger.get_invocation(TOOL_CALL_ID)
        assert rec2.lifecycle_state == "EXECUTING", f"Expected EXECUTING, got {rec2.lifecycle_state}"

        # 3. EXECUTE SIDE EFFECT - increment durable counter
        current = 0
        if os.path.exists(counter_path):
            with open(counter_path, "r") as f:
                current = int(f.read().strip())
        current += 1
        with open(counter_path, "w") as f:
            f.write(str(current))
        print(f"[SIDE_EFFECT_COUNT: {current}]")

        # 4. CRASH - exit WITHOUT record_completed()
        print("[CRASH_SIMULATED: exiting without record_completed]")
        sys.exit(0)

    elif args.phase == "stage2":
        print(f"[STAGE2_PID: {pid}]")
        if args.prev_pid > 0 and args.prev_pid == pid:
            print("[ERROR: PID matched previous process]")
            sys.exit(1)

        ledger = DurableInvocationLedger(session_factory=session_factory)

        # 1. Verify invocation is in EXECUTING state
        rec = ledger.get_invocation(TOOL_CALL_ID)
        assert rec is not None, "Invocation record missing after restart"
        assert rec.lifecycle_state == "EXECUTING", (
            f"Expected EXECUTING after crash, got {rec.lifecycle_state}"
        )
        print(f"[PERSISTED_STATE: {rec.lifecycle_state}]")

        # 2. check_replay() MUST NOT return None
        replay_result = ledger.check_replay(TOOL_CALL_ID)
        assert replay_result is not None, (
            "CRITICAL FAILURE: check_replay() returned None for EXECUTING state. "
            "This would cause duplicate side effect execution."
        )
        print(f"[REPLAY_RESULT: {json.dumps(replay_result)}]")

        # 3. Verify AMBIGUOUS_CRASH_RECOVERY
        assert replay_result.get("status") == "AMBIGUOUS_CRASH_RECOVERY", (
            f"Expected AMBIGUOUS_CRASH_RECOVERY, got {replay_result.get('status')}"
        )
        assert replay_result.get("ok") is False
        assert replay_result.get("recovery_state") == "EXECUTING"

        # 4. Verify side-effect counter is still 1
        with open(counter_path, "r") as f:
            final_count = int(f.read().strip())
        assert final_count == 1, (
            f"CRITICAL: Side effect count is {final_count}, expected 1."
        )
        print(f"[FINAL_SIDE_EFFECT_COUNT: {final_count}]")
        print("[DUPLICATE_PREVENTED: True]")
        print("[STAGE2_VERIFIED: CRASH_WINDOW_SAFE]")
        sys.exit(0)


if __name__ == "__main__":
    main()
