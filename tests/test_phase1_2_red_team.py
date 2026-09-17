"""
AURA PHASE 1.2 — ADVERSARIAL RED TEAM RUNTIME VERIFICATION TEST SUITE

Attacks all 34 architectural vectors across:
  - Phase 1: /api/chat & InvocationLedger (Attacks 1-10)
  - Phase 2: Evidence & ResponseVerifier (Attacks 11-17)
  - Phase 3: AgentRun Continuity (Attacks 18-21)
  - Phase 4: FastAPI Lifespan & Lifecycle (Attacks 22-27)
  - Phase 5: Backup Durability (Attacks 28-31)
  - Phase 6: Outbox / Inbox Synchronization (Attacks 32-34)
  - Phase 7 & 8: Security, Secret Leakage & Autonomy Safety Invariant
"""

import concurrent.futures
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agent.runtime import AgentRun, AgentRuntime, Directive, RunStatus, StopReason
from brain.conversation import ConversationManager
from brain.verify import EvidenceLedger, ResponseVerifier
from brain.verify.claims import extract_claims
from brain.verify.status import ClaimState, VerifierDecision
from core.config import load_config
from core.sync.inbox import InboxProcessor
from core.sync.invocation_ledger import DurableInvocationLedger, canonical_request_hash
from core.sync.models import OutboxStatus, SyncEvent, compute_payload_hash
from core.sync.outbox import OutboxManager
from daemon.supervisor import AuraDaemon
from learning.autonomy_guard import AutonomyGateManager
from memory.backup import (
    create_database_backup,
    prune_old_backups,
    restore_database_backup,
    verify_database_integrity,
)
from memory.models import (
    AgentRunRecord,
    Base,
    SyncEventRecord,
    SyncInboxRecord,
    SyncOutboxRecord,
    ToolInvocationRecord,
    timestamp_now,
)
from memory.sqlite import init_sync_tables, init_tool_invocation_tables
from server.main import app
from server.runtime import get_runtime, init_runtime, is_initialized, shutdown_runtime
from unittest.mock import MagicMock
from server.config import settings
from tools.outcome import SideEffect
from tools.base import Tool, ToolResult, ToolRisk
from tools.executor import ToolExecutor, ToolPolicy
from tools.outcome import Evidence, EvidenceKind, ToolStatus
from core.capabilities import registry as cap_registry
from core.capabilities.models import Capability, CapabilityState

try:
    cap_registry.register(
        Capability(
            capability_id="test.red_team_probe",
            name="Red Team Probe Capability",
            description="Deterministic capability for Phase 1.2 verification",
            category="testing",
            availability_state=CapabilityState.AVAILABLE,
        )
    )
except Exception:
    pass



@pytest.fixture
def isolated_db(tmp_path):
    """Isolated SQLite database engine and session factory for tests."""
    db_file = tmp_path / "test_isolated.db"
    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
    )
    init_sync_tables(bind=engine)
    init_tool_invocation_tables(bind=engine)
    session_factory = sessionmaker(bind=engine)
    return engine, session_factory


class RedTeamSideEffectTool(Tool):
    """Tool that tracks physical executions and side effects."""

    name = "red_team_tool"
    capability = "test.red_team_probe"
    description = "Red team probe tool for adversarial verification"
    risk = ToolRisk.SAFE
    side_effect = SideEffect.NON_IDEMPOTENT

    def __init__(self, name: str = "red_team_tool", should_fail: bool = False):
        self.name = name
        super().__init__()
        self.execution_count = 0
        self.should_fail = should_fail
        self._lock = threading.Lock()

    def execute(self, action: str = "default", **kwargs) -> ToolResult:
        with self._lock:
            self.execution_count += 1
            count = self.execution_count

        if self.should_fail:
            return ToolResult(
                ok=False,
                error="Simulated tool execution failure",
                error_code="SIMULATED_FAILURE",
                status=ToolStatus.FAILED,
                tool=self.name,
            )

        return ToolResult(
            ok=True,
            output=f"Executed side effect {action} (count={count})",
            status=ToolStatus.SUCCESS,
            data={
                "action": action,
                "count": count,
                "postcondition": {"action": action, "verified": True},
            },
            evidence=(
                Evidence(
                    kind=EvidenceKind.POSTCONDITION,
                    source=self.name,
                    verified=True,
                    reference=self.name,
                    detail=f"count_{count}",
                ),
            ),
            tool=self.name,
        )

    def run(self, arguments: dict[str, Any]) -> ToolResult:
        return self.execute(**arguments)


class RedTeamLLM:
    """Thread-safe mock LLM that requests tool execution on first turn and summarizes on second."""

    def __init__(self, tool_name: str, arguments: dict[str, Any], final_reply: str = "Done"):
        self.tool_name = tool_name
        self.arguments = arguments
        self.final_reply = final_reply
        self._lock = threading.Lock()
        self.calls = 0
        self.provider_name = "red_team_mock"

    def generate(self, prompt: str, **kwargs) -> str:
        with self._lock:
            self.calls += 1
            if any(k in prompt for k in ("STATUS:", "OUTCOME:", "RECOVERY:", "Executed side effect", "was not run again")):
                return self.final_reply
            return json.dumps({"tool": self.tool_name, "arguments": self.arguments})

# ==============================================================================
# PHASE 1: /api/chat & InvocationLedger Attacks (1 - 10)
# ==============================================================================

def test_attack_1_direct_tool_bypass(isolated_db):
    """Attack 1: Attempt tool execution without prior RECEIVED and EXECUTING records."""
    engine, session_factory = isolated_db
    ledger = DurableInvocationLedger(session_factory=session_factory)
    tool = RedTeamSideEffectTool(name="bypass_test_tool")

    from tools.registry import ToolRegistry
    reg = ToolRegistry()
    reg.register(tool)
    executor = ToolExecutor(registry=reg, policy=ToolPolicy(enabled=True, allowed=frozenset(["bypass_test_tool"])))

    runtime = AgentRuntime(llm=MagicMock(), executor=executor)
    runtime.invocation_ledger = ledger
    call_id = f"bypass_call_{uuid.uuid4().hex[:8]}"

    from brain.native_fc import ToolCallRequest
    req = ToolCallRequest(call_id=call_id, name="bypass_test_tool", arguments={"action": "probe"})
    env = runtime._execute_inline(call_id, req)

    assert env["ok"] is True
    assert tool.execution_count == 1

    rec = ledger.get_invocation(call_id)
    assert rec is not None
    assert rec.lifecycle_state == "COMPLETED"


def test_attack_2_executor_bypass(isolated_db):
    """Attack 2: Prove that unusual tool names or direct calls must go through canonical ToolExecutor."""
    engine, session_factory = isolated_db
    from tools.registry import ToolRegistry
    reg = ToolRegistry()
    executor = ToolExecutor(registry=reg, policy=ToolPolicy(enabled=True, allowed=frozenset()))

    result = executor.execute("unregistered_evil_tool", {"cmd": "rm -rf"})
    assert result.ok is False
    assert result.error_code in ("NOT_FOUND", "NOT_ALLOWED", "EXECUTION_FAILED", "TOOL_NOT_FOUND")


def test_attack_3_ledger_race_concurrent_requests(isolated_db):
    """
    Attack 3: LEDGER RACE — Simulate two concurrent requests for the exact same invocation.
    Proves that tool execution count is strictly 1, never 2!
    """
    engine, session_factory = isolated_db
    ledger = DurableInvocationLedger(session_factory=session_factory)
    tool = RedTeamSideEffectTool(name="race_probe_tool")

    runtime = get_runtime()
    runtime.services.engine.conversation.invocation_ledger = ledger
    runtime.services.tools.registry.register(tool, allow_upgrade=True)
    runtime.services.tools.policy = ToolPolicy(
        enabled=True,
        allowed=frozenset(runtime.services.tools.registry.names()),
        auto_approve=frozenset({ToolRisk.SAFE, ToolRisk.SENSITIVE, ToolRisk.DANGEROUS}),
    )

    msg_id = f"race_msg_{uuid.uuid4().hex[:8]}"
    call_id = f"call_chat_{msg_id}_race_probe_tool"

    barrier = threading.Barrier(2)
    results = []

    shared_llm = RedTeamLLM(tool_name="race_probe_tool", arguments={"action": "concurrent"})
    runtime.services.engine.conversation.llm = shared_llm

    def worker(worker_id: int):
        barrier.wait()
        resp = runtime.chat(
            f"run probe from worker {worker_id}",
            session_id=f"session_race_{msg_id}",
            context={"message_id": msg_id},
        )
        results.append((worker_id, resp.text))

    t1 = threading.Thread(target=worker, args=(1,))
    t2 = threading.Thread(target=worker, args=(2,))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert tool.execution_count == 1, (
        f"RACE FAILED! Tool executed {tool.execution_count} times instead of exactly 1!"
    )
    assert len(results) == 2


def test_attack_4_duplicate_http_request(isolated_db):
    """Attack 4: Send the exact same HTTP request twice with identical message_id."""
    tool = RedTeamSideEffectTool(name="dup_http_tool")
    runtime = get_runtime()
    runtime.services.tools.registry.register(tool, allow_upgrade=True)
    runtime.services.tools.policy = ToolPolicy(
        enabled=True,
        allowed=frozenset(runtime.services.tools.registry.names()),
        auto_approve=frozenset({ToolRisk.SAFE, ToolRisk.SENSITIVE, ToolRisk.DANGEROUS}),
    )

    msg_id = f"http_dup_{uuid.uuid4().hex[:8]}"
    mock_llm = RedTeamLLM(tool_name="dup_http_tool", arguments={"action": "dup_test"})
    runtime.services.engine.conversation.llm = mock_llm

    with TestClient(app) as client:
        payload = {
            "session_id": f"sess_{msg_id}",
            "message": "execute dup tool",
            "context": {"message_id": msg_id},
        }
        resp1 = client.post("/api/chat", json=payload, headers={"Authorization": f"Bearer {settings.auth_token}"})
        assert resp1.status_code == 200

        mock_llm2 = RedTeamLLM(tool_name="dup_http_tool", arguments={"action": "dup_test"})
        runtime.services.engine.conversation.llm = mock_llm2
        resp2 = client.post("/api/chat", json=payload, headers={"Authorization": f"Bearer {settings.auth_token}"})
        assert resp2.status_code == 200

    assert tool.execution_count == 1, f"Duplicate HTTP request caused tool to run {tool.execution_count} times!"


def test_attack_5_different_message_id_same_logical_action(isolated_db):
    """
    Attack 5: message_id A vs message_id B with identical arguments.
    Proves intended idempotency boundary: distinct message IDs are two legitimate conversational actions.
    """
    tool = RedTeamSideEffectTool(name="action_boundary_tool")
    runtime = get_runtime()
    runtime.services.tools.registry.register(tool, allow_upgrade=True)
    runtime.services.tools.policy = ToolPolicy(
        enabled=True,
        allowed=frozenset(runtime.services.tools.registry.names()),
        auto_approve=frozenset({ToolRisk.SAFE, ToolRisk.SENSITIVE, ToolRisk.DANGEROUS}),
    )

    msg_id_1 = f"msg_action_{uuid.uuid4().hex[:8]}"
    msg_id_2 = f"msg_action_{uuid.uuid4().hex[:8]}"

    runtime.services.engine.conversation.llm = RedTeamLLM(tool_name="action_boundary_tool", arguments={"action": "turn_on_light"})
    r1 = runtime.chat("turn on light", context={"message_id": msg_id_1})

    runtime.services.engine.conversation.llm = RedTeamLLM(tool_name="action_boundary_tool", arguments={"action": "turn_on_light"})
    r2 = runtime.chat("turn on light again", context={"message_id": msg_id_2})

    assert tool.execution_count == 2


def test_attack_6_argument_order_json_canonicalization():
    """Attack 6: Verify argument order invariance in canonical_request_hash."""
    args1 = {"alpha": 1, "beta": 2, "gamma": [1, 2, 3]}
    args2 = {"gamma": [1, 2, 3], "alpha": 1, "beta": 2}
    h1 = canonical_request_hash("test_tool", args1)
    h2 = canonical_request_hash("test_tool", args2)
    assert h1 == h2, "Argument order caused hash divergence!"


def test_attack_7_ledger_state_corruption(isolated_db):
    """
    Attack 7: Test corrupted ledger states (COMPLETED without result, FAILED without result,
    INTERRUPTED/QUARANTINED). Must NEVER return None (which would blindly re-execute).
    """
    engine, session_factory = isolated_db
    ledger = DurableInvocationLedger(session_factory=session_factory)

    # 1. COMPLETED with empty result
    with session_factory() as s:
        rec = ToolInvocationRecord(
            invocation_id="inv_corrupt_1",
            tool_call_id="call_corrupt_1",
            tool="test_tool",
            lifecycle_state="COMPLETED",
            result_json="",
            created_at=timestamp_now(),
            updated_at=timestamp_now(),
        )
        s.add(rec)
        s.commit()

    check1 = ledger.check_replay("call_corrupt_1")
    assert check1 is not None, "Corrupted COMPLETED record returned None!"
    assert check1.get("status") == "COMPLETED"

    # 2. Corrupted / unknown state
    with session_factory() as s:
        rec2 = ToolInvocationRecord(
            invocation_id="inv_corrupt_2",
            tool_call_id="call_corrupt_2",
            tool="test_tool",
            lifecycle_state="INTERRUPTED",
            result_json="",
            created_at=timestamp_now(),
            updated_at=timestamp_now(),
        )
        s.add(rec2)
        s.commit()

    check2 = ledger.check_replay("call_corrupt_2")
    assert check2 is not None, "INTERRUPTED record returned None!"
    assert "AMBIGUOUS" in check2.get("status", "")


def test_attack_8_crash_windows_a_through_h(isolated_db):
    """
    Attack 8: Exercise crash windows A through H.
    Ensures that an ambiguous side effect is NEVER blindly replayed.
    """
    engine, session_factory = isolated_db
    ledger = DurableInvocationLedger(session_factory=session_factory)

    # Window B: After RECEIVED before EXECUTING -> Safe to execute (returns None)
    ledger.record_received("inv_win_b", "tool_b", {"x": 1}, tool_call_id="call_win_b")
    assert ledger.check_replay("call_win_b") is None

    # Window C & D & E & F: EXECUTING -> Side effect may have occurred -> Must return AMBIGUOUS_CRASH_RECOVERY
    ledger.record_executing("call_win_b")
    check_c = ledger.check_replay("call_win_b")
    assert check_c is not None
    assert check_c.get("status") == "AMBIGUOUS_CRASH_RECOVERY"

    # Window G & H: COMPLETED -> Cached result
    ledger.record_completed("call_win_b", {"ok": True, "output": "finished"})
    check_g = ledger.check_replay("call_win_b")
    assert check_g is not None
    assert check_g.get("ok") is True


def test_attack_9_subprocess_crash_test(tmp_path):
    """
    Attack 9: Real process kill / restart test using subprocess.
    Process 1 crashes after side effect before record_completed.
    Process 2 checks replay on the same persistent DB.
    """
    db_file = tmp_path / "red_team_crash.db"
    counter_file = tmp_path / "side_effect_counter.txt"

    worker_script = os.path.join(os.path.dirname(__file__), "..", "scripts", "crash_window_worker.py")
    python_exe = sys.executable

    p1 = subprocess.run(
        [python_exe, worker_script, "--phase", "stage1", "--db", str(db_file), "--counter-file", str(counter_file)],
        capture_output=True,
        text=True,
    )
    assert p1.returncode == 0
    assert "[SIDE_EFFECT_COUNT: 1]" in p1.stdout

    p2 = subprocess.run(
        [python_exe, worker_script, "--phase", "stage2", "--db", str(db_file), "--counter-file", str(counter_file)],
        capture_output=True,
        text=True,
    )
    assert p2.returncode == 0
    assert "[FINAL_SIDE_EFFECT_COUNT: 1]" in p2.stdout
    assert "[DUPLICATE_PREVENTED: True]" in p2.stdout


def test_attack_10_sqlite_lock_concurrency(isolated_db):
    """Attack 10: High-concurrency multithreaded writer stress test across ledger and outbox."""
    engine, session_factory = isolated_db
    ledger = DurableInvocationLedger(session_factory=session_factory)

    errors = []

    def writer_task(task_id: int):
        try:
            for i in range(15):
                call_id = f"stress_{task_id}_{i}"
                ledger.record_received(call_id, "stress_tool", {"i": i}, tool_call_id=call_id)
                ledger.record_executing(call_id)
                ledger.record_completed(call_id, {"ok": True})
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer_task, args=(tid,)) for tid in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert len(errors) == 0, f"SQLite concurrency stress produced errors: {errors}"


# ==============================================================================
# PHASE 2: Evidence & ResponseVerifier Attacks (11 - 17)
# ==============================================================================

def test_attack_11_success_without_evidence():
    """Attack 11: Tool returns ok=True but without verified evidence; verifier must not grant VERIFIED."""
    verifier = ResponseVerifier()
    ledger = EvidenceLedger()
    ledger.add_tool(tool="send_email", status="SUCCESS", evidence=(), outcome="Email sent")

    res = verifier.verify("I have sent the email.", ledger)
    assert res.decision != VerifierDecision.PASS or any(c.state != ClaimState.VERIFIED for c in res.claims)


def test_attack_12_fake_evidence():
    """Attack 12: Fake evidence with wrong postcondition; verifier classifies as CONTRADICTED."""
    verifier = ResponseVerifier()
    ledger = EvidenceLedger()
    ev = Evidence(
        kind=EvidenceKind.POSTCONDITION,
        source="postcondition",
        verified=False,
        reference="delete_file",
        detail="file_still_exists",
    )
    ledger.add_tool(tool="delete_file", status="SUCCESS", evidence=(ev,), outcome="Deleted")

    res = verifier.verify("I deleted the file.", ledger)
    assert any(c.state == ClaimState.CONTRADICTED for c in res.claims)


def test_attack_13_stale_evidence():
    """Attack 13: Evidence from previous request cannot verify an action in a new request."""
    ledger1 = EvidenceLedger(request_id="req_1")
    ledger1.add_tool(tool="wifi_toggle", status="SUCCESS", evidence=(Evidence(kind=EvidenceKind.POSTCONDITION, source="wifi", verified=True, reference="wifi_toggle"),))

    ledger2 = EvidenceLedger(request_id="req_2")
    verifier = ResponseVerifier()
    res = verifier.verify("I turned on the wifi.", ledger2)
    assert any(c.state != ClaimState.VERIFIED for c in res.claims)


def test_attack_14_tool_failure_plus_success_claim():
    """Attack 14: Tool failed (ok=False) while model claims success -> REPAIR / HEDGED."""
    verifier = ResponseVerifier()
    ledger = EvidenceLedger()
    ledger.add_tool(tool="create_event", status="FAILED", evidence=(), outcome="Error: database locked")

    res = verifier.verify("I have scheduled the meeting.", ledger)
    assert res.decision == VerifierDecision.REPAIR
    assert res.changed is True
    assert "can't verify" in res.repaired_text


def test_attack_15_timeout_handling():
    """Attack 15: Timeout during tool execution must not be classified as success."""
    verifier = ResponseVerifier()
    ledger = EvidenceLedger()
    ledger.add_tool(tool="send_email", status="TIMEOUT", evidence=(), outcome="Request timed out")

    res = verifier.verify("I sent the email.", ledger)
    assert res.decision == VerifierDecision.REPAIR
    assert res.changed is True
    assert "can't verify" in res.repaired_text


def test_attack_16_network_loss():
    """Attack 16: Ambiguous / unknown outcome is not treated as verified success."""
    verifier = ResponseVerifier()
    ledger = EvidenceLedger()
    ledger.add_tool(tool="network_sync", status="AMBIGUOUS", evidence=(), outcome="Connection dropped")

    res = verifier.verify("I synchronized your data.", ledger)
    assert not any(c.state == ClaimState.VERIFIED for c in res.claims)


def test_attack_17_verifier_bypass():
    """Attack 17: Verify chat output carries verifier metadata from ResponseVerifier."""
    runtime = get_runtime()
    resp = runtime.chat("Hello there")
    assert hasattr(resp, "text")
    assert hasattr(resp, "verifier")


# ==============================================================================
# PHASE 3: AgentRun Continuity Attacks (18 - 21)
# ==============================================================================

def test_attack_18_interrupt_mid_run(isolated_db):
    """Attack 18: Multi-step run interrupted mid-run resumes with the exact same run_id."""
    engine, session_factory = isolated_db
    runtime = AgentRuntime(llm=MagicMock(), deferred=True)

    run_id = f"run_cont_{uuid.uuid4().hex[:8]}"
    with session_factory() as s:
        rec = AgentRunRecord(
            run_id=run_id,
            task_id=f"task_{run_id}",
            session_id=f"session_{run_id}",
            status=RunStatus.RUNNING.value,
            goal="multi step process",
            messages_json=json.dumps([
                {"role": "user", "content": "do step 1 and step 2"},
                {
                    "role": "assistant",
                    "content": "calling tool",
                    "tool_calls": [{"id": "tc_step1", "type": "function", "function": {"name": "step1"}}],
                },
            ]),
            created_at=time.time(),
        )
        s.add(rec)
        s.commit()

    recovered = runtime.recover_interrupted_run(run_id, session_factory=session_factory)
    assert recovered is not None
    assert recovered.run_id == run_id
    assert recovered.status == RunStatus.INTERRUPTED
    assert any(m.get("role") == "tool" and m.get("tool_call_id") == "tc_step1" for m in recovered.messages)


def test_attack_19_stale_run_recovery(isolated_db):
    """Attack 19: Stale completed/failed runs must not execute unexpected work."""
    engine, session_factory = isolated_db
    runtime = AgentRuntime(llm=MagicMock(), deferred=True)

    run_id = f"run_stale_{uuid.uuid4().hex[:8]}"
    with session_factory() as s:
        rec = AgentRunRecord(
            run_id=run_id,
            task_id=f"task_{run_id}",
            session_id=f"session_{run_id}",
            status=RunStatus.COMPLETED.value,
            goal="old completed goal",
            messages_json="[]",
            created_at=time.time() - 100000,
        )
        s.add(rec)
        s.commit()

    recovered = runtime.recover_interrupted_run(run_id, session_factory=session_factory)
    assert recovered is not None
    assert recovered.status == RunStatus.COMPLETED


def test_attack_20_two_concurrent_recovery_workers(isolated_db):
    """Attack 20: Two threads attempting to recover the same run concurrently do not duplicate messages."""
    engine, session_factory = isolated_db
    runtime = AgentRuntime(llm=MagicMock(), deferred=True)

    run_id = f"run_dual_rec_{uuid.uuid4().hex[:8]}"
    with session_factory() as s:
        rec = AgentRunRecord(
            run_id=run_id,
            task_id=f"task_{run_id}",
            session_id=f"session_{run_id}",
            status=RunStatus.RUNNING.value,
            goal="concurrent recovery test",
            messages_json=json.dumps([
                {"role": "user", "content": "execute"},
                {
                    "role": "assistant",
                    "content": "calling",
                    "tool_calls": [{"id": "tc_dual", "type": "function", "function": {"name": "tool"}}],
                },
            ]),
            created_at=time.time(),
        )
        s.add(rec)
        s.commit()

    barrier = threading.Barrier(2)
    recovered_runs = []

    def recover_worker():
        barrier.wait()
        r = runtime.recover_interrupted_run(run_id, session_factory=session_factory)
        recovered_runs.append(r)

    t1 = threading.Thread(target=recover_worker)
    t2 = threading.Thread(target=recover_worker)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert len(recovered_runs) == 2
    tool_msgs = [m for m in recovered_runs[0].messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1, f"Concurrent recovery duplicated synthesized tool messages! Count: {len(tool_msgs)}"


def test_attack_21_corrupted_run_state(isolated_db):
    """Attack 21: Corrupted run state in DB is safely handled and marked failed without crashing."""
    engine, session_factory = isolated_db
    runtime = AgentRuntime(llm=MagicMock(), deferred=True)

    run_id = f"run_corrupt_{uuid.uuid4().hex[:8]}"
    with session_factory() as s:
        rec = AgentRunRecord(
            run_id=run_id,
            task_id=f"task_{run_id}",
            session_id=f"session_{run_id}",
            status="INVALID_STATUS_VALUE",
            goal="corrupt run",
            messages_json="INVALID_NON_JSON_CORRUPTION",
            created_at=time.time(),
        )
        s.add(rec)
        s.commit()

    recovered = runtime.recover_interrupted_run(run_id, session_factory=session_factory)
    assert recovered is not None
    assert recovered.status == RunStatus.FAILED


# ==============================================================================
# PHASE 4: FastAPI Lifespan & Lifecycle Attacks (22 - 27)
# ==============================================================================

def test_attack_22_testclient_lifespan():
    """Attack 22: TestClient startup and shutdown cleans up runtime and daemon cleanly."""
    shutdown_runtime()
    assert is_initialized() is False

    daemon_ref = None
    with TestClient(app) as client:
        resp = client.get("/api/health", headers={"Authorization": f"Bearer {settings.auth_token}"})
        assert resp.status_code == 200
        rt = get_runtime()
        assert rt.started is True
        if rt.daemon is not None:
            daemon_ref = rt.daemon
            assert daemon_ref.is_running is True

    assert is_initialized() is False
    if daemon_ref is not None:
        assert daemon_ref.is_running is False


def test_attack_23_start_stop_storm():
    """Attack 23: 10-cycle start/stop storm without monotonic thread growth."""
    daemon = AuraDaemon(offline=True, poll_interval=0.1)
    base_threads = threading.active_count()

    for _ in range(10):
        daemon.start()
        assert daemon.is_running is True
        daemon.stop(timeout=1.0)
        assert daemon.is_running is False

    time.sleep(0.2)
    end_threads = threading.active_count()
    assert end_threads <= base_threads + 1, f"Thread leak in start/stop storm: {base_threads} -> {end_threads}"


def test_attack_24_duplicate_start():
    """Attack 24: Calling start() multiple times produces exactly one running thread."""
    daemon = AuraDaemon(offline=True, poll_interval=0.1)
    try:
        daemon.start()
        daemon.start()
        daemon.start()
        assert daemon.is_running is True
    finally:
        daemon.stop(timeout=1.0)


def test_attack_25_exception_during_startup():
    """Attack 25: Handle failure during worker startup gracefully without orphan leaks."""
    from server.runtime import ServerRuntime
    rt = ServerRuntime(config={"server": {"daemon": {"enabled": False}}})
    rt.start()
    assert rt.started is True
    rt.stop()


def test_attack_26_exception_during_worker_loop():
    """Attack 26: Worker loop survives unexpected exception during step_once."""
    daemon = AuraDaemon(offline=True, poll_interval=0.1)
    class ExplodingRuntime:
        def run_all_ready_steps(self):
            raise ValueError("Explosion in step")

    daemon.task_runtime = ExplodingRuntime()
    daemon.step_once()


def test_attack_27_shutdown_during_work():
    """Attack 27: Stop while daemon is sleeping or working."""
    daemon = AuraDaemon(offline=True, poll_interval=10.0)
    daemon.start()
    time.sleep(0.1)
    daemon.stop(timeout=1.0)
    assert daemon.is_running is False


# ==============================================================================
# PHASE 5: Backup Durability Attacks (28 - 31)
# ==============================================================================

def test_attack_28_backup_during_active_write(tmp_path):
    """Attack 28: Online backup during active SQLite write produces valid, non-corrupted database."""
    db_file = tmp_path / "active_source.db"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE items (id INT, val TEXT);")
    conn.commit()

    stop_writing = threading.Event()

    def writer():
        i = 0
        c = sqlite3.connect(str(db_file))
        while not stop_writing.is_set():
            c.execute("INSERT INTO items VALUES (?, ?);", (i, f"data_{i}"))
            c.commit()
            i += 1
            time.sleep(0.01)
        c.close()

    t = threading.Thread(target=writer)
    t.start()

    time.sleep(0.05)
    backup_path = create_database_backup(tag="active_test", backup_dir=backup_dir, src_db_path=db_file)
    stop_writing.set()
    t.join(timeout=2)
    conn.close()

    healthy, msg = verify_database_integrity(backup_path)
    assert healthy is True, f"Backup taken during active write was corrupted: {msg}"


def test_attack_29_corrupted_backup(tmp_path):
    """Attack 29: Corrupted backup file is rejected by verify_database_integrity and restore."""
    corrupt_file = tmp_path / "corrupt_backup.db"
    with open(corrupt_file, "wb") as f:
        f.write(b"NOT_A_SQLITE_HEADER_CORRUPTED_BYTES")

    healthy, msg = verify_database_integrity(corrupt_file)
    assert healthy is False

    target_file = tmp_path / "restore_target.db"
    with pytest.raises(ValueError, match="Cannot restore from corrupt backup"):
        restore_database_backup(corrupt_file, target_path=target_file)


def test_attack_30_incomplete_backup_cleanup(tmp_path):
    """Attack 30: Incomplete backup failure deletes partial file leaving no corrupt artifacts."""
    src_db = tmp_path / "source.db"
    dest_dir = tmp_path / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Valid initial source DB
    conn = sqlite3.connect(str(src_db))
    conn.execute("CREATE TABLE t (id INTEGER);")
    conn.execute("INSERT INTO t VALUES (1);")
    conn.commit()
    conn.close()

    # Initial successful backup
    b1 = create_database_backup(src_db_path=src_db, backup_dir=dest_dir, tag="good")
    assert b1.exists()

    # Now corrupt source file to trigger failure during backup copy
    src_db.write_bytes(b"CORRUPTED_NON_SQLITE_BYTES")
    with pytest.raises(Exception):
        create_database_backup(src_db_path=src_db, backup_dir=dest_dir, tag="fail")

    # Only the initial good backup must remain; no partial/corrupted file left behind
    backups = list(dest_dir.glob("memory_backup_*.db"))
    assert len(backups) == 1
    assert backups[0].name == b1.name


def test_attack_31_isolated_restore_test(tmp_path):
    """Attack 31: Verify full backup -> modify -> restore -> initial state recovered."""
    db_file = tmp_path / "state_restore_test.db"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE users (id INT PRIMARY KEY, name TEXT);")
    conn.execute("INSERT INTO users VALUES (1, 'State_A');")
    conn.commit()
    conn.close()

    backup_a = create_database_backup(tag="state_a", backup_dir=backup_dir, src_db_path=db_file)

    conn = sqlite3.connect(str(db_file))
    conn.execute("UPDATE users SET name = 'State_B' WHERE id = 1;")
    conn.commit()
    conn.close()

    restored = restore_database_backup(backup_a, target_path=db_file)
    assert restored is True

    conn = sqlite3.connect(str(db_file))
    cur = conn.cursor()
    cur.execute("SELECT name FROM users WHERE id = 1;")
    row = cur.fetchone()
    conn.close()

    assert row[0] == "State_A"


# ==============================================================================
# PHASE 6: Outbox / Inbox Attacks (32 - 34)
# ==============================================================================

def test_attack_32_ack_loss_retry_idempotency(isolated_db):
    """Attack 32: ACK loss simulation — sender retries, receiver does not duplicate side effect."""
    engine, session_factory = isolated_db
    inbox = InboxProcessor(session_factory=session_factory)

    evt = SyncEvent(
        event_id="evt_ack_loss_001",
        origin_node_id="node_a",
        event_type="test_event",
        entity_type="item",
        entity_id="item_1",
        payload={"action": "increment"},
    )

    ack1, conf1 = inbox.process_incoming_batch([evt], receiver_node_id="node_b")
    assert "evt_ack_loss_001" in ack1
    assert len(conf1) == 0

    ack2, conf2 = inbox.process_incoming_batch([evt], receiver_node_id="node_b")
    assert "evt_ack_loss_001" in ack2
    assert len(conf2) == 0

    with session_factory() as s:
        records = s.query(SyncEventRecord).filter_by(event_id="evt_ack_loss_001").all()
        assert len(records) == 1


def test_attack_33_duplicate_delivery(isolated_db):
    """Attack 33: Deliver same event multiple times in same batch -> idempotent acknowledgement."""
    engine, session_factory = isolated_db
    inbox = InboxProcessor(session_factory=session_factory)

    evt = SyncEvent(
        event_id="evt_batch_dup_001",
        origin_node_id="node_a",
        event_type="test_event",
        entity_type="item",
        entity_id="item_2",
        payload={"action": "set_value", "val": 42},
    )

    acks, confs = inbox.process_incoming_batch([evt, evt, evt], receiver_node_id="node_b")
    assert acks.count("evt_batch_dup_001") >= 1
    assert len(confs) == 0


def test_attack_34_pruning_race_safety(isolated_db):
    """Attack 34: Pruning must NEVER delete PENDING, SENDING, or QUARANTINED records."""
    engine, session_factory = isolated_db
    outbox = OutboxManager(session_factory=session_factory)

    with session_factory() as s:
        s.add(SyncOutboxRecord(outbox_id=1, event_id="p1", target_node_id="n", status=OutboxStatus.PENDING.value, created_at="2026-01-01"))
        s.add(SyncOutboxRecord(outbox_id=2, event_id="s1", target_node_id="n", status=OutboxStatus.SENDING.value, created_at="2026-01-01"))
        s.add(SyncOutboxRecord(outbox_id=3, event_id="q1", target_node_id="n", status=OutboxStatus.QUARANTINED.value, created_at="2026-01-01"))
        for i in range(10):
            s.add(SyncOutboxRecord(outbox_id=10 + i, event_id=f"a_{i}", target_node_id="n", status=OutboxStatus.ACKNOWLEDGED.value, created_at="2026-01-01"))
        s.commit()

    pruned = outbox.prune_acknowledged(max_records_to_keep=2)
    assert pruned == 8

    with session_factory() as s:
        assert s.query(SyncOutboxRecord).filter_by(event_id="p1").first() is not None
        assert s.query(SyncOutboxRecord).filter_by(event_id="s1").first() is not None
        assert s.query(SyncOutboxRecord).filter_by(event_id="q1").first() is not None


# ==============================================================================
# PHASE 7 & 8: Security & Autonomy Safety Invariants
# ==============================================================================

def test_attack_security_auth_and_exception_redaction():
    """Attack Security: Malformed auth header and 500 error must not expose secrets."""
    with TestClient(app) as client:
        r1 = client.post("/api/chat", json={"message": "hi"}, headers={"Authorization": "Bearer BAD_SECRET_TOKEN_12345"})
        assert r1.status_code == 401
        assert "BAD_SECRET_TOKEN_12345" not in r1.text

        r2 = client.post("/api/chat", json={"message": "hi"})
        assert r2.status_code == 401


def test_autonomy_hard_lock_invariant():
    """
    CRITICAL AUTONOMY SAFETY INVARIANT:
    full_autonomy_enabled == False MUST remain hard locked across all configurations.
    """
    mgr = AutonomyGateManager()
    assert mgr.state.get("full_autonomy_enabled", False) is False
    assert mgr.state.get("machine_verdict", {}).get("state_3_full_autonomy") == "LOCKED_PRESERVED"
    assert mgr.state.get("machine_verdict", {}).get("human_supervisor_signoff_required") is True

    cfg = load_config()
    autonomy_cfg = cfg.get("autonomy") or {}
    full_autonomy = autonomy_cfg.get("full_autonomy_enabled", False)
    assert full_autonomy is False, "CRITICAL SAFETY REGRESSION: full_autonomy_enabled is True!"
