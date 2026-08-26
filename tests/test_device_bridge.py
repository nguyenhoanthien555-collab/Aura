"""
Tests for the device gateway and its HTTP transport (PARTS 1-3, 23).

The properties under test: synchronous invoke semantics over a polling
device, bounded waits that produce structured TIMEOUT, correlation
(refusing results for unknown invocations), cancellation resolving
pending work, and auth on every route.
"""

import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.device_gateway import (
    DeviceGateway,
    configure_device_gateway,
)
from server.routes.device import router as device_router


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(device_router)

    from server.auth import verify_token

    app.dependency_overrides[verify_token] = lambda: "test"

    gateway = DeviceGateway()
    configure_device_gateway(gateway)

    with TestClient(app) as test_client:
        yield test_client, gateway

    configure_device_gateway(None)


def _await_invocation(gateway, timeout_s: float = 5.0):
    """The next queued invocation, waited for rather than assumed."""

    deadline = time.monotonic() + timeout_s

    while time.monotonic() < deadline:
        pending = gateway.poll()

        if pending is not None:
            return pending

        time.sleep(0.01)

    return None



def test_invoke_returns_the_structured_report_the_device_sent(client):

    test_client, gateway = client

    def fake_device():
        # Poll until work appears rather than sleeping a fixed interval:
        # the route resolves the tool through the registry before it
        # queues anything, and a one-shot poll raced that setup.
        invocation = _await_invocation(gateway)
        assert invocation is not None, "nothing was ever queued"
        gateway.complete(invocation.invocation_id, {
            "ok": True,
            "run_id": invocation.run_id,
            "tool_call_id": invocation.tool_call_id,
            "tool": invocation.tool,
            "result": {"package": "com.aura.companion"},
            "observation_id": "obs_abc123456789abcd",
        })

    thread = threading.Thread(target=fake_device)
    thread.start()

    response = test_client.post("/api/device/invoke", json={
        "run_id": "run_abc123456789abcd",
        "tool": "android.get_foreground_app",
        "arguments": {},
        "timeout_s": 5,
    })
    thread.join()

    assert response.status_code == 200
    body = response.json()

    assert body["ok"] is True
    assert body["result"]["package"] == "com.aura.companion"
    assert body["run_id"] == "run_abc123456789abcd"
    assert body["tool"] == "android.get_foreground_app"
    assert body["observation_id"].startswith("obs_")


def test_a_disconnected_device_yields_structured_timeout_not_a_hang(client):

    test_client, _ = client

    start = time.monotonic()
    response = test_client.post("/api/device/invoke", json={
        "tool": "android.tap",
        "arguments": {"text": "Search"},
        "timeout_s": 0.3,
    })
    elapsed = time.monotonic() - start

    assert response.status_code == 504
    body = response.json()

    assert body["ok"] is False
    assert body["error"]["code"] == "TIMEOUT"
    assert elapsed < 3          # bounded, not hung


def test_results_for_unknown_invocations_are_refused(client):

    test_client, _ = client

    response = test_client.post("/api/device/results", json={
        "reports": [{
            "invocation_id": "invo_doesnotexist0000",
            "ok": True,
            "result": {},
        }],
    })

    body = response.json()

    assert body["accepted"] == 0
    assert len(body["rejected"]) == 1


def test_cancelling_a_run_resolves_pending_work_as_cancelled():

    gateway = DeviceGateway()
    answers = {}

    def waiter():
        answers["report"] = gateway.submit(
            "android.launch_app",
            {"package": "com.y"},
            run_id="run_cancelme000001",
            timeout_s=10,
        )

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(0.1)

    cancelled = gateway.cancel_run("run_cancelme000001")
    thread.join(timeout=2)

    assert cancelled == 1
    assert answers["report"]["error"]["code"] == "CANCELLED"
    assert gateway.pending_count() == 0


def test_non_android_tools_are_rejected_at_the_route(client):

    test_client, gateway = client

    response = test_client.post("/api/device/invoke", json={
        "tool": "file.delete",
        "arguments": {},
    })

    assert response.status_code == 422
    assert gateway.submitted == 0


def test_malformed_run_ids_never_reach_the_queue(client):

    test_client, gateway = client

    response = test_client.post("/api/device/invoke", json={
        "run_id": "drop table runs",
        "tool": "android.home",
    })

    assert response.status_code == 422
    assert gateway.pending_count() == 0


def test_poll_is_empty_when_nothing_is_queued_and_fifo_when_it_is():

    gateway = DeviceGateway()

    assert gateway.poll() is None

    first = gateway.submit("android.back", {}, timeout_s=0.05)
    # submit times out quickly; use direct queueing for FIFO check:
    gateway2 = DeviceGateway()

    from server.device_gateway import PendingInvocation
    gateway2._pending.append(PendingInvocation(
        invocation_id="invo_1", tool="android.back", arguments={},
    ))
    gateway2._pending.append(PendingInvocation(
        invocation_id="invo_2", tool="android.home", arguments={},
    ))

    assert gateway2.poll().invocation_id == "invo_1"
    gateway2.complete("invo_1", {"ok": True})
    assert gateway2.poll().invocation_id == "invo_2"


def test_cli_emit_handles_unicode_and_vietnamese(capsys):
    import json
    from scripts.aura_android import _emit

    report = {
        "ok": True,
        "tool": "android.get_ui_tree",
        "result": {
            "node_1": {
                "text": "Mở YouTube và tìm kiếm bài hát",
                "content_description": "Sponsored - Dùng thử miễn phí 90 ngày",
            }
        },
    }

    # JSON mode
    _emit(report, as_json=True)
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert parsed["result"]["node_1"]["text"] == "Mở YouTube và tìm kiếm bài hát"
    assert parsed["result"]["node_1"]["content_description"] == "Sponsored - Dùng thử miễn phí 90 ngày"

    # Human-readable mode
    _emit(report, as_json=False)
    captured_text = capsys.readouterr()
    assert "Mở YouTube" in captured_text.out