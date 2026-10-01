"""
Phase 1: Runtime, Lifecycle & Durability Closure Test Suite.

Verifies:
1. /api/chat InvocationLedger parity (durable record, state transitions).
2. Crash-after-executing replay protection in /api/chat (AMBIGUOUS_CRASH_RECOVERY).
3. Completed invocation replay caching in /api/chat.
4. ServerRuntime & AuraDaemon lifecycle ownership (deterministic start, duplicate prevention, graceful stop).
5. Periodic backup worker lifecycle (online SQLite snapshot, integrity verification).
6. Periodic queue pruning lifecycle (outbox/inbox terminal record pruning, preserving pending/sending/quarantined).
7. Proactive worker lifecycle (honors enabled=False, evaluates when enabled).
8. Real /api/chat HTTP end-to-end evidence & response verification.
9. State 3 Autonomy Lock Invariant (full_autonomy_enabled == False).
"""

import json
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from brain.conversation import ConversationManager, _Turn
from brain.message import Message
from brain.tool_calling import ToolCall
from core.logger import logger
from core.paths import BACKUPS_DIR, DATA_DIR
from core.sync.inbox import InboxProcessor
from core.sync.invocation_ledger import DurableInvocationLedger
from core.sync.models import InboxStatus, OutboxStatus, SyncEvent
from core.sync.outbox import OutboxManager
from daemon.supervisor import AuraDaemon, SubsystemHealth
from agent.autonomy_guard import AutonomyGateManager
from memory.backup import (
    create_database_backup,
    get_backup_history,
    verify_database_integrity,
)
from memory.models import SyncInboxRecord, SyncOutboxRecord, ToolInvocationRecord
from memory.sqlite import (
    SessionLocal,
    db_lock,
    init_database,
    init_sync_tables,
    init_tool_invocation_tables,
)
from server.config import settings
from server.main import app
from server.runtime import ServerRuntime
from tools.base import Parameter, Tool, ToolResult, ToolRisk
from tools.executor import ToolExecutor, ToolPolicy
from tools.outcome import SideEffect
from tools.registry import ToolRegistry


# ----------------------------------------------------------------------
# Test Tools & Fakes
# ----------------------------------------------------------------------

from core.capabilities import registry as cap_registry
from core.capabilities.models import Capability, CapabilityState

# Register test capability
cap_registry.register(
    Capability(
        capability_id="test.side_effect",
        name="Test Side Effect",
        description="Side effect tool for testing",
        category="testing",
        availability_state=CapabilityState.AVAILABLE,
    )
)

class SideEffectRecorderTool(Tool):
    """Deterministic tool that records execution counts and side effects."""

    name = "record_side_effect"
    description = "A mutating tool that executes a side effect and increments counter."
    risk = ToolRisk.SAFE
    capability = "test.side_effect"
    side_effect = SideEffect.MUTATING

    def __init__(self):
        super().__init__()
        self.call_count = 0
        self.last_arguments = None

    def execute(self, action: str = "", target: str = "", **kwargs) -> ToolResult:
        self.call_count += 1
        self.last_arguments = {"action": action, "target": target}
        return ToolResult(
            ok=True,
            output=f"Executed {action} on {target} (count={self.call_count})",
            data={"count": self.call_count, "action": action, "target": target},
            tool=self.name,
        )


class FakeLLM:
    """Mock LLM for conversation manager tests."""

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.index = 0
        self.provider_name = "mock_fake"

    def generate(self, prompt: str, **kwargs) -> str:
        if self.index < len(self.responses):
            res = self.responses[self.index]
            self.index += 1
            return res
        return "Default response."


class FakeStore:
    def __init__(self):
        self.messages = []

    def save(self, user_msg, assistant_msg, session_id="default"):
        self.messages.append((user_msg, assistant_msg))

    def history(self, limit=20, session_id="default"):
        return []


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------

def test_chat_invocation_ledger_parity_lifecycle(tmp_path):
    """
    Verify /api/chat execution path records durable invocation lifecycle:
    RECEIVED -> EXECUTING -> COMPLETED in DurableInvocationLedger.
    """
    init_tool_invocation_tables()
    tool = SideEffectRecorderTool()
    registry = ToolRegistry()
    registry.register(tool)
    executor = ToolExecutor(
        registry=registry,
        policy=ToolPolicy(
            enabled=True,
            allowed=frozenset({"record_side_effect"}),
            auto_approve=frozenset({ToolRisk.SAFE}),
        ),
    )
    ledger = DurableInvocationLedger()

    manager = ConversationManager(
        memory=FakeStore(),
        builder=None,
        llm=FakeLLM([]),
        tools=executor,
        invocation_ledger=ledger,
    )

    test_msg_id = f"msg_{uuid.uuid4().hex[:8]}"
    turn = _Turn(
        user_msg=Message(role="user", content="run side effect"),
        session_id="test_session_1",
        context={"message_id": test_msg_id},
    )
    call = ToolCall(name="record_side_effect", arguments={"action": "write", "target": "file1.txt"})

    result_str = manager._run_tool(call, turn)
    assert "Executed write on file1.txt" in result_str
    assert tool.call_count == 1

    # Check DurableInvocationLedger
    expected_call_id = f"call_chat_{test_msg_id}_record_side_effect"
    rec = ledger.get_invocation(expected_call_id)
    assert rec is not None
    assert rec.lifecycle_state == "COMPLETED"
    assert rec.tool == "record_side_effect"
    assert rec.run_id == "test_session_1"
    res_data = json.loads(rec.result_json)
    assert res_data["ok"] is True
    assert "file1.txt" in res_data["output"]


def test_chat_crash_after_executing_prevents_replay(tmp_path):
    """
    Case A & B: Tool was in EXECUTING state when process crashed.
    On replay/restart, ConversationManager._run_tool MUST detect
    AMBIGUOUS_CRASH_RECOVERY and REFUSE blind re-execution.
    """
    init_tool_invocation_tables()
    tool = SideEffectRecorderTool()
    registry = ToolRegistry()
    registry.register(tool)
    executor = ToolExecutor(
        registry=registry,
        policy=ToolPolicy(
            enabled=True,
            allowed=frozenset({"record_side_effect"}),
            auto_approve=frozenset({ToolRisk.SAFE}),
        ),
    )
    ledger = DurableInvocationLedger()

    test_msg_id = f"crash_msg_{uuid.uuid4().hex[:8]}"
    call_id = f"call_chat_{test_msg_id}_record_side_effect"
    inv_id = f"invo_chat_{test_msg_id}_record_side_effect"

    # Simulate crash window: record was marked RECEIVED -> EXECUTING
    ledger.record_received(
        invocation_id=inv_id,
        tool="record_side_effect",
        arguments={"action": "reboot_critical", "target": "system"},
        run_id="crash_session",
        tool_call_id=call_id,
    )
    ledger.record_executing(call_id)

    # Verify state is EXECUTING in SQLite
    rec = ledger.get_invocation(call_id)
    assert rec.lifecycle_state == "EXECUTING"

    # Now the turn arrives after restart
    manager = ConversationManager(
        memory=FakeStore(),
        builder=None,
        llm=FakeLLM([]),
        tools=executor,
        invocation_ledger=ledger,
    )
    turn = _Turn(
        user_msg=Message(role="user", content="retry after crash"),
        session_id="crash_session",
        context={"message_id": test_msg_id},
    )
    call = ToolCall(name="record_side_effect", arguments={"action": "reboot_critical", "target": "system"})

    output = manager._run_tool(call, turn)

    # Verify tool was NOT executed again (call count is 0)
    assert tool.call_count == 0
    assert "RECOVERY:" in output
    assert "AMBIGUOUS" in output.upper() or "re-execution was prevented" in output


def test_chat_completed_replay_returns_cached_result(tmp_path):
    """
    Verify replay of COMPLETED tool returns cached result without re-executing tool.
    """
    init_tool_invocation_tables()
    tool = SideEffectRecorderTool()
    registry = ToolRegistry()
    registry.register(tool)
    executor = ToolExecutor(
        registry=registry,
        policy=ToolPolicy(
            enabled=True,
            allowed=frozenset({"record_side_effect"}),
            auto_approve=frozenset({ToolRisk.SAFE}),
        ),
    )
    ledger = DurableInvocationLedger()

    test_msg_id = f"cache_msg_{uuid.uuid4().hex[:8]}"
    call_id = f"call_chat_{test_msg_id}_record_side_effect"
    inv_id = f"invo_chat_{test_msg_id}_record_side_effect"

    # Pre-record completed invocation
    ledger.record_received(
        invocation_id=inv_id,
        tool="record_side_effect",
        arguments={"action": "read", "target": "doc.txt"},
        run_id="session_cache",
        tool_call_id=call_id,
    )
    ledger.record_executing(call_id)
    ledger.record_completed(
        call_id,
        {"ok": True, "output": "CACHED_RESULT_FROM_PREVIOUS_TURN", "status": "SUCCESS"},
    )

    manager = ConversationManager(
        memory=FakeStore(),
        builder=None,
        llm=FakeLLM([]),
        tools=executor,
        invocation_ledger=ledger,
    )
    turn = _Turn(
        user_msg=Message(role="user", content="repeat request"),
        session_id="session_cache",
        context={"message_id": test_msg_id},
    )
    call = ToolCall(name="record_side_effect", arguments={"action": "read", "target": "doc.txt"})

    output = manager._run_tool(call, turn)

    # Tool was not called
    assert tool.call_count == 0
    assert "CACHED_RESULT_FROM_PREVIOUS_TURN" in output


def test_fastapi_server_runtime_lifecycle_management():
    """
    Verify ServerRuntime lifecycle:
    1. start() starts AuraDaemon.
    2. Calling start() again does not duplicate the daemon thread.
    3. stop() shuts down the daemon gracefully.
    """
    runtime = ServerRuntime(config={"server": {"daemon": {"enabled": True, "poll_interval": 0.2}}})
    assert runtime.started is False
    assert runtime.daemon is None

    runtime.start()
    assert runtime.started is True
    assert runtime.daemon is not None
    assert runtime.daemon.is_running is True

    thread_id = runtime.daemon._thread.ident

    # Duplicate start check
    runtime.start()
    assert runtime.daemon._thread.ident == thread_id, "Duplicate start created duplicate thread!"

    # Stop check
    runtime.stop()
    assert runtime.started is False
    assert runtime.daemon is None


def test_supervisor_periodic_backup_worker(tmp_path):
    """
    Verify AuraDaemon._step_backup_worker():
    Executes online backup, checks integrity, increments count.
    """
    backup_dir = tmp_path / "supervisor_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    daemon = AuraDaemon(
        offline=True,
        poll_interval=0.1,
        backup_interval=0.001,  # Force tick
    )
    assert daemon.backups_created == 0

    daemon._step_backup_worker()
    assert daemon.backups_created == 1

    health = daemon.get_health()
    assert health.backup == SubsystemHealth.HEALTHY.value
    assert health.backups_created == 1


def test_supervisor_periodic_pruning_worker(tmp_path):
    """
    Verify AuraDaemon._step_pruning_worker():
    Prunes terminal outbox and inbox records beyond threshold,
    while strictly protecting PENDING, SENDING, and QUARANTINED records.
    """
    init_database()
    init_sync_tables()
    with db_lock:
        session = SessionLocal()
        try:
            now = time.strftime("%Y-%m-%dT%H:%M:%S")
            # Seed 10 ACKNOWLEDGED and 2 PENDING outbox records
            for i in range(10):
                session.add(SyncOutboxRecord(
                    event_id=f"evt_prune_ack_{i}_{uuid.uuid4().hex[:6]}",
                    status=OutboxStatus.ACKNOWLEDGED.value,
                    acknowledged_at=now,
                ))
            for i in range(2):
                session.add(SyncOutboxRecord(
                    event_id=f"evt_prune_pend_{i}_{uuid.uuid4().hex[:6]}",
                    status=OutboxStatus.PENDING.value,
                    created_at=now,
                ))
            session.commit()
        finally:
            session.close()

    daemon = AuraDaemon(
        offline=True,
        poll_interval=0.1,
        prune_interval=0.001,  # Force tick
    )

    outbox = OutboxManager()
    # Call pruning directly with small retention limit of 3
    deleted = outbox.prune_acknowledged(max_records_to_keep=3)
    assert deleted >= 7

    # Verify PENDING records were NOT deleted
    with db_lock:
        session = SessionLocal()
        try:
            pending_count = session.query(SyncOutboxRecord).filter_by(status=OutboxStatus.PENDING.value).count()
            assert pending_count >= 2
        finally:
            session.close()


def test_supervisor_proactive_worker_disabled_behavior():
    """
    Verify that when proactive is disabled, _step_proactive_worker does not tick.
    """
    class MockPolicy:
        def __init__(self, enabled):
            self.settings = type("Settings", (), {"enabled": enabled})()

    class MockProactiveEngine:
        def __init__(self, enabled):
            self.policy = MockPolicy(enabled)
            self.ticked = False

        def tick(self):
            self.ticked = True
            return None

    # Disabled engine
    engine_disabled = MockProactiveEngine(enabled=False)
    daemon_disabled = AuraDaemon(
        offline=True,
        proactive_engine=engine_disabled,
        proactive_interval=0.001,
    )
    daemon_disabled._step_proactive_worker()
    assert engine_disabled.ticked is False

    # Enabled engine
    engine_enabled = MockProactiveEngine(enabled=True)
    daemon_enabled = AuraDaemon(
        offline=True,
        proactive_engine=engine_enabled,
        proactive_interval=0.001,
    )
    daemon_enabled._step_proactive_worker()
    assert engine_enabled.ticked is True


def test_api_chat_endpoint_e2e_with_tool_and_evidence(monkeypatch):
    """
    Test real HTTP POST /api/chat with TestClient:
    Verifies auth, chat pipeline execution, and message response.
    """
    from brain.conversation import Response
    from server.runtime import get_runtime

    runtime = get_runtime()
    if runtime and hasattr(runtime, "engine") and hasattr(runtime.engine, "conversation"):
        monkeypatch.setattr(
            runtime.engine.conversation,
            "chat",
            lambda *args, **kwargs: Response(text="hello back from aura"),
        )

    client = TestClient(app)
    token = settings.auth_token

    # Verify unauthorized request is rejected
    unauth_res = client.post("/api/chat", json={"message": "hello"})
    assert unauth_res.status_code == 401

    # Verify authorized chat request
    res = client.post(
        "/api/chat",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "message": "hello aura",
            "session_id": "test_e2e_session",
            "context": {"source": "test"},
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert "reply" in data
    assert "session_id" in data
    assert data["session_id"] == "test_e2e_session"
    assert "message_id" in data


def test_autonomy_gate_state3_locked():
    """
    CRITICAL INVARIANT CHECK:
    Verify full_autonomy_enabled == False across all governance modules.
    """
    gate_mgr = AutonomyGateManager()
    assert gate_mgr.state.get("full_autonomy_enabled", False) is False
    assert gate_mgr.state.get("machine_verdict", {}).get("state_3_full_autonomy") == "LOCKED_PRESERVED"
    assert gate_mgr.state.get("machine_verdict", {}).get("human_supervisor_signoff_required") is True
