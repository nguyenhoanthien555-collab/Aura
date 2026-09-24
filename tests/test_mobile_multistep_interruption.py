import pytest
from brain.verify.ledger import EvidenceLedger
from brain.verify.verify import ResponseVerifier
from core.ids import new_task_id
from tools.outcome import Evidence, EvidenceKind, ToolStatus
from server.device_gateway import DeviceGateway, PendingInvocation
from agent.task_runtime import TaskRuntime, TaskStatus, StepStatus


def test_mobile_tool_anti_hallucination_verification():
    ledger = EvidenceLedger()
    verifier = ResponseVerifier()

    reply_unverified = "Tôi đã chạm vào nút gửi trên màn hình điện thoại."
    ver_res = verifier.verify(reply_unverified, ledger)
    assert any(c.state in ("UNKNOWN", "CONTRADICTED") for c in ver_res.claims)
    assert ver_res.changed is True or any(c.hallucination for c in ver_res.claims)

    ev = Evidence(kind=EvidenceKind.POSTCONDITION, verified=True, detail="Node tapped")
    ledger.add_tool(
        tool="android.tap",
        status=ToolStatus.SUCCESS.value,
        evidence=(ev,),
        outcome="Chạm vào nút gửi thành công",
        capability="android.tap",
    )

    ver_res_grounded = verifier.verify(reply_unverified, ledger)
    assert not any(c.state == "CONTRADICTED" for c in ver_res_grounded.claims)
    assert any(c.state in ("VERIFIED", "SUPPORTED") for c in ver_res_grounded.claims)


def test_device_gateway_cancel_all():
    gateway = DeviceGateway()
    inv1 = PendingInvocation(invocation_id="inv-1", run_id="run-1", tool="android.tap", arguments={"x": 100, "y": 200})
    inv2 = PendingInvocation(invocation_id="inv-2", run_id="run-2", tool="android.type_text", arguments={"text": "hello"})

    gateway._pending.extend([inv1, inv2])
    assert len(gateway._pending) == 2

    cancelled_count = gateway.cancel_all(reason="Emergency interrupt requested by Tris")
    assert cancelled_count == 2
    assert len(gateway._pending) == 0

    res1 = gateway._results.get("inv-1")
    assert res1 is not None
    assert res1["ok"] is False
    assert res1["error"]["code"] == ToolStatus.CANCELLED.value or "CANCELLED" in str(res1["error"])


def test_task_runtime_cancel_all_active_tasks():
    runtime = TaskRuntime()
    tid1 = new_task_id()
    tid2 = new_task_id()

    task1 = runtime.create_task(goal="Task 1", task_id=tid1)
    task2 = runtime.create_task(goal="Task 2", task_id=tid2)

    runtime.update_task_status(tid1, TaskStatus.RUNNING.value)
    runtime.update_task_status(tid2, TaskStatus.PENDING.value)

    active_tasks = runtime.list_active_tasks()
    active_ids = {t.task_id for t in active_tasks}
    assert tid1 in active_ids
    assert tid2 in active_ids

    cancelled_ids = runtime.cancel_all_active_tasks(reason="Emergency interrupt")
    assert tid1 in cancelled_ids
    assert tid2 in cancelled_ids

    t1_after = runtime.get_task(tid1)
    t2_after = runtime.get_task(tid2)
    assert t1_after.status == TaskStatus.CANCELLED.value
    assert t2_after.status == TaskStatus.CANCELLED.value


def test_server_runtime_interrupt_coordination():
    from server.runtime import ServerRuntime
    from events.types import AgentInterruptedEvent

    runtime = ServerRuntime(config={"auth_token": "test-token"}, memory=None)

    interrupted_events = []
    runtime.bus.subscribe(AgentInterruptedEvent, lambda evt: interrupted_events.append(evt))

    res = runtime.interrupt(reason="Barge-in interrupt")
    assert res["interrupted"] is True
    assert "Em đã dừng lại ngay lập tức theo lệnh của anh rồi!" in res["message"]
    assert len(interrupted_events) == 1
    assert interrupted_events[0].reason == "Barge-in interrupt"


def test_interrupt_endpoint_http():
    from fastapi.testclient import TestClient
    from server.main import app
    from server.config import settings

    client = TestClient(app)
    token = settings.auth_token or "dev"
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.post("/api/agent/interrupt", json={"reason": "Stop now"}, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["interrupted"] is True
    assert "Em đã dừng lại ngay lập tức theo lệnh của anh rồi!" in data["message"]

    resp2 = client.post("/api/tasks/interrupt", json={"reason": "Stop all tasks"}, headers=headers)
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["interrupted"] is True
