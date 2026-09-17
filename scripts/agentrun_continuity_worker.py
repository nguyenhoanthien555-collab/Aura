"""
Subprocess worker for P4.5.2 Critical B: AgentRun Same-Run Continuity test.

Stage 1 (PROCESS 1):
  Create run with tool_calls in transcript (simulating mid-execution crash),
  persist to SQLite, exit.

Stage 2 (PROCESS 2):
  Same DB, recover_interrupted_run(), verify same run_id, transcript reconciled,
  tool history preserved, pending work identified.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from memory.models import AgentRunRecord, Base
from memory.sqlite import init_sync_tables


RUN_ID = "run_continuity_p452"
TOOL_CALL_ID_1 = "call_cont_tap_01"
TOOL_CALL_ID_2 = "call_cont_swipe_02"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True, choices=["stage1", "stage2"])
    parser.add_argument("--db", required=True)
    parser.add_argument("--prev-pid", type=int, default=0)
    args = parser.parse_args()

    pid = os.getpid()
    db_path = os.path.abspath(args.db)
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    session_factory = sessionmaker(bind=engine)

    if args.phase == "stage1":
        print(f"[STAGE1_PID: {pid}]")
        init_sync_tables(bind=engine)

        # Build a realistic message transcript that ends mid-tool-execution
        messages = [
            {"role": "user", "content": "Goal: Open Settings and toggle WiFi"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": TOOL_CALL_ID_1,
                        "type": "function",
                        "function": {"name": "android.tap", "arguments": '{"x": 540, "y": 1200}'},
                    },
                    {
                        "id": TOOL_CALL_ID_2,
                        "type": "function",
                        "function": {"name": "android.swipe", "arguments": '{"x1": 0, "y1": 500, "x2": 0, "y2": 100}'},
                    },
                ],
            },
            # NOTE: No tool response messages — simulating crash during tool wait
        ]

        session = session_factory()
        rec = AgentRunRecord(
            run_id=RUN_ID,
            task_id="task_continuity_001",
            session_id="session_continuity_test",
            goal="Open Settings and toggle WiFi",
            status="running",
            rounds=3,
            tool_call_count=5,
            consecutive_failures=0,
            verify_rounds=0,
            requires_observation=True,
            observed_ok=1,
            unobserved_rounds=0,
            messages_json=json.dumps(messages),
            unverified_json=json.dumps([
                ["android.tap", TOOL_CALL_ID_1, "mutating", "Settings opened"],
            ]),
            created_at=1000.0,
        )
        session.add(rec)
        session.commit()
        session.close()

        print(f"[RUN_ID: {RUN_ID}]")
        print(f"[STATUS: running]")
        print(f"[PENDING_TOOL_CALLS: {TOOL_CALL_ID_1}, {TOOL_CALL_ID_2}]")
        print("[CRASH_SIMULATED: exiting with pending tool calls in transcript]")
        sys.exit(0)

    elif args.phase == "stage2":
        print(f"[STAGE2_PID: {pid}]")
        if args.prev_pid > 0 and args.prev_pid == pid:
            print("[ERROR: PID matched previous process]")
            sys.exit(1)

        # Import runtime and recover the run
        from agent.runtime import AgentRuntime, RunStatus

        runtime = AgentRuntime.__new__(AgentRuntime)
        runtime._runs = {}
        runtime._lock = __import__("threading").Lock()

        # 1. Verify the run exists and is still "running" in the DB
        session = session_factory()
        raw_rec = session.query(AgentRunRecord).filter_by(run_id=RUN_ID).first()
        session.close()
        assert raw_rec is not None, "AgentRunRecord missing after restart"
        assert raw_rec.status == "running"
        print(f"[DB_STATUS_BEFORE_RECOVERY: {raw_rec.status}]")

        # 2. Discover interrupted runs
        interrupted = runtime.list_interrupted_runs(session_factory=session_factory)
        assert RUN_ID in interrupted, f"Expected {RUN_ID} in interrupted runs, got {interrupted}"
        print(f"[INTERRUPTED_RUNS_DISCOVERED: {interrupted}]")

        # 3. Recover the interrupted run
        run = runtime.recover_interrupted_run(RUN_ID, session_factory=session_factory)
        assert run is not None, "recover_interrupted_run returned None"

        # 4. Verify SAME run ID
        assert run.run_id == RUN_ID, f"Run ID changed: expected {RUN_ID}, got {run.run_id}"
        print(f"[RECOVERED_RUN_ID: {run.run_id}]")
        print(f"[SAME_RUN_ID: True]")

        # 5. Verify status is INTERRUPTED (because pending tool calls were found)
        assert run.status == RunStatus.INTERRUPTED, (
            f"Expected INTERRUPTED status, got {run.status}"
        )
        print(f"[RECOVERED_STATUS: {run.status.value}]")

        # 6. Verify tool call history preserved
        assert run.rounds == 3, f"Rounds not preserved: {run.rounds}"
        assert run.tool_call_count == 5, f"Tool call count not preserved: {run.tool_call_count}"
        print(f"[TOOL_HISTORY_PRESERVED: rounds={run.rounds}, tool_calls={run.tool_call_count}]")

        # 7. Verify pending work identified - synthesized tool responses exist
        tool_responses = [m for m in run.messages if m.get("role") == "tool"]
        assert len(tool_responses) == 2, f"Expected 2 synthesized tool responses, got {len(tool_responses)}"

        for resp in tool_responses:
            content = json.loads(resp["content"])
            assert content.get("status") == "TIMEOUT_RECOVERY"
            assert content.get("ok") is False
            assert "PROCESS_CRASH_RECOVERY" in content.get("error", {}).get("code", "")
        print("[PENDING_WORK_RECONCILED: 2 timeout tool responses synthesized]")

        # 8. Verify unverified list preserved
        assert len(run.unverified) == 1
        print(f"[UNVERIFIED_PRESERVED: {len(run.unverified)} items]")

        # 9. Verify the transcript is now valid (no unresolved tool_calls)
        from agent.runtime import AgentRuntime as RT
        pending = RT._find_pending_tool_calls(run.messages)
        assert len(pending) == 0, f"Still have pending tool calls after recovery: {pending}"
        print("[TRANSCRIPT_VALID_FOR_CONTINUATION: True]")

        # 10. Verify provenance
        assert run.goal == "Open Settings and toggle WiFi"
        assert run.session_id == "session_continuity_test"
        print(f"[PROVENANCE_PRESERVED: goal='{run.goal}', session='{run.session_id}']")

        print("[STAGE2_VERIFIED: SAME_RUN_CONTINUITY_PROVEN]")
        sys.exit(0)


if __name__ == "__main__":
    main()
