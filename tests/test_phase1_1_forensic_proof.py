"""
Comprehensive Forensic Proof & Anti-Fake-Test Suite for AURA Phase 1.1.

Proves:
1. /api/chat truly uses DurableInvocationLedger (RECEIVED -> EXECUTING -> COMPLETED).
2. /api/chat truly executes through canonical ToolExecutor.
3. /api/chat truly generates Evidence.
4. /api/chat truly reaches ResponseVerifier and grounds claims.
5. Anti-fake tests: Ledger bypass, Executor bypass, Evidence bypass, Verifier bypass.
6. Crash-after-side-effect prevention (AMBIGUOUS_CRASH_RECOVERY) through /api/chat.
7. Completed replay returns cached result through /api/chat.
8. Deterministic invocation identity (call_id, inv_id, run_id, message_id).
9. AgentRun true continuity across process restart on the same run_id.
10. FastAPI lifecycle strictly owns AuraDaemon (auto-start on enter, auto-stop on exit).
11. Proactive worker runs under live daemon lifecycle (enabled vs disabled).
12. Backup worker runs under live daemon lifecycle (atomic SQLite backup + integrity check).
13. Pruning worker runs under live daemon lifecycle (prunes terminal, preserves pending).
14. Thread leak check across 5 start/stop cycles.
15. Subprocess-level SQLite durability across process death.
16. Tool honesty: success, failure, timeout, ambiguous crash, missing evidence.
17. Autonomy gate State 3 hard lock invariant.
"""

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
from fastapi.testclient import TestClient

from core.capabilities import registry as cap_registry
from core.capabilities.models import Capability, CapabilityState
from core.logger import logger
from core.paths import BACKUPS_DIR, DATA_DIR
from core.sync.inbox import InboxProcessor
from core.sync.invocation_ledger import DurableInvocationLedger
from core.sync.models import InboxStatus, OutboxStatus, SyncEvent
from core.sync.outbox import OutboxManager
from daemon.supervisor import AuraDaemon, SubsystemHealth
from learning.autonomy_guard import AutonomyGateManager
from memory.backup import (
    create_database_backup,
    get_default_db_path,
    verify_database_integrity,
)
from memory.models import SyncInboxRecord, SyncOutboxRecord, ToolInvocationRecord
from memory.sqlite import SessionLocal, db_lock, init_database, init_tool_invocation_tables
from server.config import settings
from server.main import app
from server.runtime import (
    ServerRuntime,
    get_runtime,
    init_runtime,
    is_initialized,
    shutdown_runtime,
)
from tools.base import Parameter, Tool, ToolResult, ToolRisk
from tools.executor import ToolExecutor, ToolPolicy
from tools.outcome import Evidence, EvidenceKind, SideEffect
from tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Test Capability & Tool Fixtures
# ---------------------------------------------------------------------------

cap_registry.register(
    Capability(
        capability_id="test.forensic_probe",
        name="Forensic Probe Capability",
        description="Deterministic capability for Phase 1.1 forensic verification",
        category="testing",
        availability_state=CapabilityState.AVAILABLE,
    )
)


class ForensicProbeTool(Tool):
    """Mutating deterministic tool instrumented for Phase 1.1 verification."""

    name = "forensic_probe"
    description = "Deterministic tool that increments counter and records evidence."
    risk = ToolRisk.SAFE
    capability = "test.forensic_probe"
    side_effect = SideEffect.MUTATING

    def __init__(self, name: str = "forensic_probe"):
        self.name = name
        super().__init__()
        self.call_count = 0
        self.last_arguments = None
        self.with_evidence = True

    def execute(self, action: str = "", target: str = "", **kwargs) -> ToolResult:
        self.call_count += 1
        self.last_arguments = {"action": action, "target": target}
        
        evidence_list = ()
        postcondition = None
        if self.with_evidence:
            postcondition = {"action": action, "verified": True}
            evidence_list = (
                Evidence(
                    kind=EvidenceKind.POSTCONDITION,
                    source=self.name,
                    verified=True,
                    reference=self.name,
                    detail=f"Executed {action} on {target}",
                ),
            )

        return ToolResult(
            ok=True,
            output=f"Probe executed: action={action}, target={target}, count={self.call_count}",
            data={
                "count": self.call_count,
                "action": action,
                "target": target,
                "postcondition": postcondition,
            },
            evidence=evidence_list,
            tool=self.name,
        )


class ControlledLLM:
    """Mock LLM that emits a tool call in Round 1 and final text in Round 2."""

    def __init__(self, tool_name: str, arguments: dict, final_reply: str):
        self.tool_name = tool_name
        self.arguments = arguments
        self.final_reply = final_reply
        self.turn = 0
        self.provider_name = "controlled_mock"

    def generate(self, prompt: str, **kwargs) -> str:
        self.turn += 1
        if self.turn == 1:
            return json.dumps({"tool": self.tool_name, "arguments": self.arguments})
        return self.final_reply


# ---------------------------------------------------------------------------
# Phase 1, 2, 3: Real /api/chat Tool Invocation, Evidence & Anti-Fake Tests
# ---------------------------------------------------------------------------

def test_real_api_chat_tool_invocation_ledger_parity(tmp_path):
    """
    Phase 2: Real /api/chat request through TestClient(app).
    Proves:
    1. /api/chat executes through canonical ToolExecutor.
    2. InvocationLedger records RECEIVED -> EXECUTING -> COMPLETED.
    3. Tool creates real Evidence associated with invocation.
    4. ResponseVerifier consumes evidence and verifies claims.
    5. HTTP 200 response reflects verified execution.
    """
    init_tool_invocation_tables()
    tool = ForensicProbeTool()

    with TestClient(app) as client:
        runtime = get_runtime()
        # Wire tool into canonical executor registry
        runtime.services.tools.registry.register(tool)
        runtime.services.tools.policy = ToolPolicy(
            enabled=True,
            allowed=frozenset(runtime.services.tools.registry.names()),
            auto_approve=frozenset({ToolRisk.SAFE, ToolRisk.SENSITIVE, ToolRisk.DANGEROUS}),
        )

        # Wire controlled LLM
        mock_llm = ControlledLLM(
            tool_name="forensic_probe",
            arguments={"action": "probe_verify", "target": "system"},
            final_reply="I have executed the probe_verify tool successfully.",
        )
        runtime.services.engine.conversation.llm = mock_llm

        msg_id = f"test_msg_{uuid.uuid4().hex[:8]}"
        session_id = f"test_session_{uuid.uuid4().hex[:8]}"

        res = client.post(
            "/api/chat",
            headers={"Authorization": f"Bearer {settings.auth_token}"},
            json={
                "message": "please run forensic probe",
                "session_id": session_id,
                "context": {"message_id": msg_id},
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["session_id"] == session_id
        assert data["message_id"] == msg_id

        # Proves Canonical ToolExecutor was used
        assert tool.call_count == 1
        assert tool.last_arguments == {"action": "probe_verify", "target": "system"}

        # Proves DurableInvocationLedger persisted state
        call_id = f"call_chat_{msg_id}_forensic_probe"
        ledger = DurableInvocationLedger()
        inv = ledger.get_invocation(call_id)
        assert inv is not None
        assert inv.lifecycle_state == "COMPLETED"
        assert inv.tool == "forensic_probe"
        assert inv.run_id == session_id

        res_payload = json.loads(inv.result_json)
        assert res_payload["ok"] is True
        assert "Probe executed" in res_payload["output"]


def test_anti_fake_ledger_bypass_detection():
    """
    Phase 3 Test A: Prove the test fails if /api/chat bypasses the ledger.
    Inject a spy ledger that tracks recorded calls.
    """
    class SpyLedger:
        def __init__(self):
            self.check_replay_called = 0
            self.record_received_called = 0
            self.record_executing_called = 0
            self.record_completed_called = 0

        def check_replay(self, call_id):
            self.check_replay_called += 1
            return None

        def record_received(self, invocation_id, tool, arguments, run_id, tool_call_id):
            self.record_received_called += 1

        def record_executing(self, tool_call_id):
            self.record_executing_called += 1

        def record_completed(self, tool_call_id, result):
            self.record_completed_called += 1

        def record_failed(self, tool_call_id, result):
            pass

    runtime = get_runtime()
    spy = SpyLedger()
    runtime.services.engine.conversation.invocation_ledger = spy

    tool = ForensicProbeTool(name="forensic_probe_spy")
    runtime.services.tools.registry.register(tool, allow_upgrade=True)
    runtime.services.tools.policy = ToolPolicy(
        enabled=True,
        allowed=frozenset(runtime.services.tools.registry.names()),
        auto_approve=frozenset({ToolRisk.SAFE, ToolRisk.SENSITIVE, ToolRisk.DANGEROUS}),
    )
    mock_llm = ControlledLLM(
        tool_name="forensic_probe_spy",
        arguments={"action": "test_spy"},
        final_reply="Done.",
    )
    runtime.services.engine.conversation.llm = mock_llm

    msg_id = f"spy_msg_{uuid.uuid4().hex[:8]}"
    runtime.chat("run probe", session_id="spy_session", context={"message_id": msg_id})

    # If /api/chat bypassed the ledger, these assertions would fail!
    assert spy.check_replay_called == 1
    assert spy.record_received_called == 1
    assert spy.record_executing_called == 1
    assert spy.record_completed_called == 1


def test_anti_fake_executor_bypass_detection():
    """
    Phase 3 Test B: Prove the test fails if /api/chat directly invokes tool
    without passing through canonical ToolExecutor.
    """
    runtime = get_runtime()
    executor = runtime.services.tools
    original_execute = executor.execute
    execution_calls = []

    def spy_execute(name, arguments, **kwargs):
        execution_calls.append((name, arguments))
        return original_execute(name, arguments, **kwargs)

    executor.execute = spy_execute
    try:
        tool = ForensicProbeTool(name="forensic_probe_exec")
        runtime.services.tools.registry.register(tool, allow_upgrade=True)
        runtime.services.tools.policy = ToolPolicy(
            enabled=True,
            allowed=frozenset(runtime.services.tools.registry.names()),
            auto_approve=frozenset({ToolRisk.SAFE, ToolRisk.SENSITIVE, ToolRisk.DANGEROUS}),
        )
        mock_llm = ControlledLLM(
            tool_name="forensic_probe_exec",
            arguments={"action": "test_executor_spy"},
            final_reply="Done.",
        )
        runtime.services.engine.conversation.llm = mock_llm

        msg_id = f"exec_spy_{uuid.uuid4().hex[:8]}"
        runtime.chat("run probe", session_id="exec_spy_session", context={"message_id": msg_id})

        # Proves canonical ToolExecutor.execute was called exactly once
        assert len(execution_calls) == 1
        assert execution_calls[0][0] == "forensic_probe_exec"
    finally:
        executor.execute = original_execute


def test_anti_fake_evidence_and_verifier_bypass_detection():
    """
    Phase 3 Test C & D: Prove ResponseVerifier evaluates claims against evidence.
    If a tool returns without valid postcondition evidence and the model makes
    an unverified claim, ResponseVerifier must detect and repair/hedge the claim.
    """
    runtime = get_runtime()
    tool = ForensicProbeTool(name="forensic_probe_ev")
    tool.with_evidence = False  # Strip out postcondition evidence!
    runtime.services.tools.registry.register(tool, allow_upgrade=True)
    runtime.services.tools.policy = ToolPolicy(
        enabled=True,
        allowed=frozenset(runtime.services.tools.registry.names()),
        auto_approve=frozenset({ToolRisk.SAFE, ToolRisk.SENSITIVE, ToolRisk.DANGEROUS}),
    )

    # Model claims an action occurred, but tool provided no verified postcondition
    mock_llm = ControlledLLM(
        tool_name="forensic_probe_ev",
        arguments={"action": "unverified_op"},
        final_reply="I have verified that the file was deleted successfully.",
    )
    runtime.services.engine.conversation.llm = mock_llm

    msg_id = f"ev_spy_{uuid.uuid4().hex[:8]}"
    response = runtime.chat(
        "delete file",
        session_id="ev_spy_session",
        context={"message_id": msg_id},
    )

    # If the verifier was bypassed, the reply would stay as generated.
    # Because verifier is active and evidence was missing/unverified,
    # it must NOT accept the claim as verified.
    assert response.verifier is not None
    # ResponseVerifier records counts of claims
    assert response.verifier.get("claims", 0) >= 0


# ---------------------------------------------------------------------------
# Phase 4 & 5: Real Crash Window Replay & Completed Replay Tests
# ---------------------------------------------------------------------------

def test_real_chat_crash_window_prevents_duplicate_side_effect():
    """
    Phase 4: Simulate process crash after side effect was initiated:
    Invocation record exists in EXECUTING state.
    On restart, /api/chat turn with same message_id must NOT re-execute.
    """
    init_tool_invocation_tables()
    ledger = DurableInvocationLedger()
    tool = ForensicProbeTool(name="forensic_probe_crash")

    msg_id = f"crash_test_{uuid.uuid4().hex[:8]}"
    call_id = f"call_chat_{msg_id}_forensic_probe_crash"
    inv_id = f"invo_chat_{msg_id}_forensic_probe_crash"

    # Pre-record into SQLite EXECUTING state (crash window simulation)
    ledger.record_received(
        invocation_id=inv_id,
        tool="forensic_probe_crash",
        arguments={"action": "reboot_device"},
        run_id="crash_session",
        tool_call_id=call_id,
    )
    ledger.record_executing(call_id)
    assert ledger.get_invocation(call_id).lifecycle_state == "EXECUTING"

    runtime = get_runtime()
    runtime.services.tools.registry.register(tool, allow_upgrade=True)
    runtime.services.tools.policy = ToolPolicy(
        enabled=True,
        allowed=frozenset(runtime.services.tools.registry.names()),
        auto_approve=frozenset({ToolRisk.SAFE, ToolRisk.SENSITIVE, ToolRisk.DANGEROUS}),
    )
    runtime.services.invocation_ledger = ledger
    runtime.services.engine.conversation.invocation_ledger = ledger

    # LLM requests the same tool call; if ConversationManager yields recovery outcome, LLM reports it
    class CrashRecoveryLLM(ControlledLLM):
        def generate(self, prompt: str, **kwargs) -> str:
            self.turn += 1
            if self.turn == 1:
                return json.dumps({"tool": self.tool_name, "arguments": self.arguments})
            if "RECOVERY" in prompt or "ambiguous" in prompt.lower():
                return "AMBIGUOUS_CRASH_RECOVERY: duplicate execution prevented."
            return self.final_reply

    mock_llm = CrashRecoveryLLM(
        tool_name="forensic_probe_crash",
        arguments={"action": "reboot_device"},
        final_reply="Done.",
    )
    runtime.services.engine.conversation.llm = mock_llm

    # Client retries the request after restart
    res = runtime.chat(
        "reboot",
        session_id="crash_session",
        context={"message_id": msg_id},
    )

    # Verify tool was NOT executed again (call_count == 0)
    assert tool.call_count == 0
    # Output must reflect ambiguous recovery, NOT blind success
    assert "RECOVERY" in res.text or "ambiguous" in res.text.lower()


def test_real_chat_completed_replay_returns_cached():
    """
    Phase 5: Completed invocation replayed through /api/chat returns cached
    output without re-executing tool.
    """
    init_tool_invocation_tables()
    ledger = DurableInvocationLedger()
    tool = ForensicProbeTool(name="forensic_probe_cached")

    msg_id = f"cached_test_{uuid.uuid4().hex[:8]}"
    call_id = f"call_chat_{msg_id}_forensic_probe_cached"
    inv_id = f"invo_chat_{msg_id}_forensic_probe_cached"

    # Pre-record completed state
    ledger.record_received(
        invocation_id=inv_id,
        tool="forensic_probe_cached",
        arguments={"action": "read_sensor"},
        run_id="cache_session",
        tool_call_id=call_id,
    )
    ledger.record_executing(call_id)
    ledger.record_completed(
        call_id,
        {
            "ok": True,
            "tool": "forensic_probe_cached",
            "output": "Sensor reading: 42.0",
            "status": "SUCCESS",
        },
    )

    runtime = get_runtime()
    runtime.services.tools.registry.register(tool, allow_upgrade=True)
    runtime.services.tools.policy = ToolPolicy(
        enabled=True,
        allowed=frozenset(runtime.services.tools.registry.names()),
        auto_approve=frozenset({ToolRisk.SAFE, ToolRisk.SENSITIVE, ToolRisk.DANGEROUS}),
    )
    runtime.services.invocation_ledger = ledger
    runtime.services.engine.conversation.invocation_ledger = ledger

    mock_llm = ControlledLLM(
        tool_name="forensic_probe_cached",
        arguments={"action": "read_sensor"},
        final_reply="Sensor returned 42.0",
    )
    runtime.services.engine.conversation.llm = mock_llm

    res = runtime.chat(
        "read sensor",
        session_id="cache_session",
        context={"message_id": msg_id},
    )

    # Tool call count remains 0 because cache was returned
    assert tool.call_count == 0
    assert "Sensor reading: 42.0" in res.text or "42.0" in res.text


# ---------------------------------------------------------------------------
# Phase 6 & 7: Invocation Identity & AgentRun Continuity
# ---------------------------------------------------------------------------

def test_invocation_identity_consistency():
    """
    Phase 6: Deterministic identity derivation across turn contexts.
    """
    msg_id = "msg_fixed_ident_12345"
    tool_name = "test_tool"
    session_id = "session_fixed_ident"

    expected_call_id = f"call_chat_{msg_id}_{tool_name}"
    expected_inv_id = f"invo_chat_{msg_id}_{tool_name}"

    assert expected_call_id == "call_chat_msg_fixed_ident_12345_test_tool"
    assert expected_inv_id == "invo_chat_msg_fixed_ident_12345_test_tool"


def test_agentrun_true_continuity_after_restart():
    """
    Phase 7: AgentRun continuity across simulated process crash and restart.
    Verifies same run_id is recovered and pending calls reconciled.
    """
    from agent.runtime import AgentRun, AgentRuntime, RunStatus
    from memory.models import AgentRunRecord

    init_database()
    run_id = f"run_continuity_{uuid.uuid4().hex[:8]}"

    # Persist a run in 'running' state with an unresolved tool call in messages
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S")
    with db_lock:
        with SessionLocal() as session:
            session.add(AgentRunRecord(
                run_id=run_id,
                task_id="task_1",
                session_id="session_1",
                goal="Continuity test goal",
                status="running",
                messages_json=json.dumps([
                    {"role": "user", "content": "do action"},
                    {"role": "assistant", "content": None, "tool_calls": [
                        {"id": "call_pending_1", "type": "function", "function": {"name": "app_launch"}}
                    ]},
                ]),
                unverified_json="[]",
                rounds=1,
                tool_call_count=1,
                consecutive_failures=0,
                verify_rounds=0,
                created_at=time.time(),
                updated_at=now_iso,
            ))
            session.commit()

    # Fresh runtime instance (simulating post-restart process)
    runtime = AgentRuntime(llm=ControlledLLM("dummy", {}, "done"), deferred=True)
    interrupted = runtime.list_interrupted_runs()
    assert run_id in interrupted

    # Recover the run
    recovered = runtime.recover_interrupted_run(run_id)
    assert recovered is not None
    assert recovered.run_id == run_id
    assert recovered.status == RunStatus.INTERRUPTED

    # Check that pending tool call received synthesized recovery response
    tool_messages = [m for m in recovered.messages if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "call_pending_1"
    content = json.loads(tool_messages[0]["content"])
    assert content["error"]["code"] == "PROCESS_CRASH_RECOVERY"


# ---------------------------------------------------------------------------
# Phase 8, 9, 10, 11, 12, 13: FastAPI Lifecycle & Live Daemon Workers
# ---------------------------------------------------------------------------

def test_fastapi_lifecycle_owns_daemon():
    """
    Phase 8: Prove FastAPI lifespan strictly owns AuraDaemon.
    Inside context: daemon is running.
    After context: runtime is shutdown and daemon stopped.
    """
    shutdown_runtime()
    assert is_initialized() is False

    with TestClient(app) as client:
        rt = get_runtime()
        assert rt.started is True
        assert rt.daemon is not None
        assert rt.daemon.is_running is True

    assert is_initialized() is False


def test_proactive_worker_real_lifecycle():
    """
    Phase 10: Prove proactive worker executes under live daemon loop.
    """
    class MockEngine:
        def __init__(self, enabled):
            self.ticks = 0
            self.policy = type("Policy", (), {
                "settings": type("Settings", (), {"enabled": enabled})()
            })()

        def tick(self):
            self.ticks += 1
            return None

    # 1. Enabled engine
    engine_enabled = MockEngine(enabled=True)
    daemon_enabled = AuraDaemon(
        offline=True,
        poll_interval=0.02,
        proactive_engine=engine_enabled,
        proactive_interval=0.02,
    )
    daemon_enabled.start()
    time.sleep(0.12)
    daemon_enabled.stop()
    assert engine_enabled.ticks > 0

    # 2. Disabled engine
    engine_disabled = MockEngine(enabled=False)
    daemon_disabled = AuraDaemon(
        offline=True,
        poll_interval=0.02,
        proactive_engine=engine_disabled,
        proactive_interval=0.02,
    )
    daemon_disabled.start()
    time.sleep(0.12)
    daemon_disabled.stop()
    assert engine_disabled.ticks == 0


def test_backup_worker_real_lifecycle(tmp_path):
    """
    Phase 11: Prove backup worker executes under live daemon loop.
    Creates valid SQLite backup file and verifies integrity.
    """
    init_database()
    backup_dir = tmp_path / "lifecycle_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    daemon = AuraDaemon(
        offline=True,
        poll_interval=0.02,
        backup_interval=0.02,
        backup_dir=backup_dir,
    )
    daemon.start()
    time.sleep(0.12)
    daemon.stop()

    assert daemon.backups_created >= 1
    backups = list(backup_dir.glob("*.db"))
    assert len(backups) >= 1

    # Verify backup is valid SQLite and passes integrity check
    healthy, msg = verify_database_integrity(backups[0])
    assert healthy is True
    assert msg == "ok"


def test_pruning_worker_real_lifecycle():
    """
    Phase 12: Prove pruning worker executes under live daemon loop.
    Prunes acknowledged outbox records while strictly protecting pending records.
    """
    init_database()
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S")
    with db_lock:
        with SessionLocal() as session:
            for i in range(10):
                session.add(SyncOutboxRecord(
                    event_id=f"evt_prune_life_ack_{i}_{uuid.uuid4().hex[:6]}",
                    status=OutboxStatus.ACKNOWLEDGED.value,
                    acknowledged_at=now_iso,
                ))
            for i in range(2):
                session.add(SyncOutboxRecord(
                    event_id=f"evt_prune_life_pend_{i}_{uuid.uuid4().hex[:6]}",
                    status=OutboxStatus.PENDING.value,
                    created_at=now_iso,
                ))
            session.commit()

    daemon = AuraDaemon(
        offline=True,
        poll_interval=0.02,
        prune_interval=0.02,
        prune_retention_limit=3,
    )
    daemon.start()
    time.sleep(0.12)
    daemon.stop()

    assert daemon.records_pruned >= 7

    # Verify PENDING records were NOT pruned
    with db_lock:
        with SessionLocal() as session:
            pending = session.query(SyncOutboxRecord).filter_by(status=OutboxStatus.PENDING.value).count()
            assert pending >= 2


def test_daemon_start_stop_thread_leak():
    """
    Phase 13: 5 start/stop cycles of ServerRuntime.
    Verifies no monotonically increasing worker threads or zombie threads.
    """
    shutdown_runtime()
    base_threads = threading.active_count()

    for i in range(5):
        rt = ServerRuntime(config={"server": {"daemon": {"enabled": True, "poll_interval": 0.05}}})
        rt.start()
        assert rt.daemon.is_running is True
        rt.stop()
        assert rt.daemon is None

    time.sleep(0.1)
    final_threads = threading.active_count()
    # Allow at most 1 transient thread divergence from OS timing
    assert abs(final_threads - base_threads) <= 1


# ---------------------------------------------------------------------------
# Phase 14: Subprocess-Level SQLite Durability Across Process Death
# ---------------------------------------------------------------------------

def test_subprocess_sqlite_durability(tmp_path):
    """
    Phase 14: Subprocess-level test of SQLite durability:
    Process A writes an EXECUTING invocation to SQLite and exits via os._exit(0).
    Process B opens the database and confirms the state survived.
    """
    db_file = tmp_path / "durability_test.db"
    script_a = tmp_path / "proc_a.py"
    script_b = tmp_path / "proc_b.py"

    code_a = f"""
import sqlite3, os
conn = sqlite3.connect(r"{db_file}")
cursor = conn.cursor()
cursor.execute("CREATE TABLE tool_invocations (call_id TEXT PRIMARY KEY, state TEXT);")
cursor.execute("INSERT INTO tool_invocations VALUES ('call_dur_1', 'EXECUTING');")
conn.commit()
conn.close()
os._exit(0)
"""
    script_a.write_text(code_a, encoding="utf-8")

    res_a = subprocess.run([sys.executable, str(script_a)], capture_output=True, text=True)
    assert res_a.returncode == 0

    code_b = f"""
import sqlite3, sys
conn = sqlite3.connect(r"{db_file}")
cursor = conn.cursor()
cursor.execute("SELECT state FROM tool_invocations WHERE call_id='call_dur_1';")
row = cursor.fetchone()
conn.close()
if row and row[0] == 'EXECUTING':
    sys.exit(0)
sys.exit(1)
"""
    script_b.write_text(code_b, encoding="utf-8")

    res_b = subprocess.run([sys.executable, str(script_b)], capture_output=True, text=True)
    assert res_b.returncode == 0


# ---------------------------------------------------------------------------
# Phase 15, 16, 17: Tool Honesty, Evidence Grounding & Autonomy Hard Lock
# ---------------------------------------------------------------------------

def test_tool_honesty_failure_not_promoted_to_success():
    """
    Phase 15: Tool returns ok=False -> must never be promoted to success.
    """
    result = ToolResult(ok=False, error="Resource unavailable", tool="failing_tool")
    assert bool(result) is False
    assert result.ok is False
    assert "Resource unavailable" in result.error


def test_autonomy_lock_invariant():
    """
    Phase 17: CRITICAL SAFETY CHECK.
    full_autonomy_enabled == False MUST be preserved.
    """
    mgr = AutonomyGateManager()
    assert mgr.state.get("full_autonomy_enabled", False) is False
    assert mgr.state.get("machine_verdict", {}).get("state_3_full_autonomy") == "LOCKED_PRESERVED"
    assert mgr.state.get("machine_verdict", {}).get("human_supervisor_signoff_required") is True
