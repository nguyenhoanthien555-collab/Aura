"""
Tests for the device-driven agent step endpoint (PARTS 7, 13, 31).

The endpoint is the wire contract of the migration, so the tests pin the
contract itself: ids validated at the boundary, sessions isolated,
observations recorded per run, tool reports folded before reasoning, and
the response always carrying a run snapshot.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from brain.native_fc import ModelTurn, ToolCallRequest
from core.observations import ObservationStore
from server.routes.agent import (
    configure_agent_runtime,
    router as agent_router,
)
from tools.providers.android_bridge import DeclaredOnlyBridge
from tools.providers.android_provider import AndroidProvider
from tools.registry import ToolRegistry


class StubLLM:
    """Plays back turns; records requests like ScriptedLLM."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    def generate_with_tools(self, system, messages, tools):
        self.requests.append({
            "system": system,
            "messages": [dict(m) for m in messages],
            "tools": tools,
        })
        return self.turns.pop(0)


def tool_turn(name="android.tap", **arguments):
    return ModelTurn(tool_calls=(
        ToolCallRequest(call_id="provider-1", name=name,
                        arguments=arguments),
    ))


@pytest.fixture()
def client():
    """A fresh app with a stubbed deferred runtime and open auth."""

    from agent.runtime import AgentRuntime

    registry = ToolRegistry()
    AndroidProvider(DeclaredOnlyBridge()).register_into(registry)

    runtime = AgentRuntime(
        llm=StubLLM([
            tool_turn("android.launch_app",
                      package="com.google.android.youtube"),
            ModelTurn(text="All done."),
        ]),
        registry=registry,
        observations=ObservationStore(clock=lambda: 1000.0),
        system_prompt="device agent",
        deferred=True,
        max_steps=10,
    )

    configure_agent_runtime(runtime)      # also resets any prior state

    app = FastAPI()
    app.include_router(agent_router)

    from server.auth import verify_token

    app.dependency_overrides[verify_token] = lambda: "test"

    with TestClient(app) as test_client:
        yield test_client, runtime

    configure_agent_runtime(None)


def test_first_step_starts_a_run_and_returns_tool_calls(client):

    test_client, _ = client

    response = test_client.post("/api/agent/step", json={
        "session_id": "session_test00000020",
        "goal": "Open YouTube",
        "observations": [
            {"kind": "accessibility_tree", "source": "device",
             "data": {"package": "com.android.launcher", "nodes": {}}},
        ],
    })

    assert response.status_code == 200
    body = response.json()

    assert body["run_id"].startswith("run_")
    assert body["task_id"].startswith("task_")
    assert body["status"] == "running"
    assert body["directive"]["type"] == "tool_calls"

    call = body["directive"]["tool_calls"][0]
    assert call["tool"] == "android.launch_app"
    assert call["tool_call_id"].startswith("call_")
    assert call["arguments"]["package"] == "com.google.android.youtube"


def test_a_goal_is_required_to_start_a_run(client):

    test_client, _ = client

    response = test_client.post("/api/agent/step", json={
        "session_id": "session_test00000021",
    })

    assert response.status_code == 422


def test_malformed_run_ids_are_rejected_at_the_boundary(client):

    test_client, runtime = client

    # Mint a real run so the id-format check (not 404) is what fires.
    runtime.start_run("x", "session_test00000022")

    response = test_client.post("/api/agent/step", json={
        "session_id": "session_test00000022",
        "run_id": "../../../etc/passwd",
    })

    assert response.status_code == 422


def test_a_session_cannot_drive_another_sessions_run(client):
    """
    The structural stale-state guard: replaying one task's run id from
    another session is forbidden, not merely ignored.
    """

    test_client, runtime = client

    run = runtime.start_run("task A", "session_owner")

    response = test_client.post("/api/agent/step", json={
        "session_id": "session_attacker",
        "run_id": run.run_id,
        "goal": "task B",
    })

    assert response.status_code == 403


def test_unknown_run_id_is_a_404_not_a_crash(client):

    test_client, _ = client

    response = test_client.post("/api/agent/step", json={
        "session_id": "session_test00000023",
        "run_id": "run_abcdef0123456789",
    })

    assert response.status_code == 404


def test_device_results_are_folded_before_the_next_model_round(client):
    """
    The full two-step dance: directives out, envelopes back, and only
    then does the model reason again - with the results in front of it.
    """

    test_client, runtime = client

    first = test_client.post("/api/agent/step", json={
        "session_id": "session_test00000024",
        "goal": "Open YouTube",
        "observations": [
            {"kind": "foreground_app",
             "data": {"package": "com.android.launcher"}},
        ],
    }).json()

    call = first["directive"]["tool_calls"][0]

    second = test_client.post("/api/agent/step", json={
        "session_id": "session_test00000024",
        "run_id": first["run_id"],
        "observations": [
            {"kind": "accessibility_tree",
             "data": {"package": "com.google.android.youtube"}},
        ],
        "tool_results": [
            {
                "tool_call_id": call["tool_call_id"],
                "call_id": "provider-1",
                "tool": call["tool"],
                "arguments": call["arguments"],
                "ok": True,
                "result": {"launched": "com.google.android.youtube"},
                "postcondition": {"verified": False},
            }
        ],
    }).json()

    # The launch reported verified=False, so the runtime REFUSES the
    # model's completion claim - the PART 9 gate, exercised over HTTP.
    assert second["status"] == "running"
    assert second["directive"]["type"] == "final"
    assert second["directive"]["text"] == "verification required"

    # The model's second round saw exactly one folded tool message.
    messages = runtime.llm.requests[1]["messages"]
    tool_messages = [m for m in messages if m["role"] == "tool"]

    assert len(tool_messages) == 1


def test_observations_sent_with_a_step_are_recorded_for_that_run(client):

    test_client, runtime = client

    run = runtime.start_run("observe", "session_test00000025")

    test_client.post("/api/agent/step", json={
        "session_id": "session_test00000025",
        "run_id": run.run_id,
        "observations": [
            {
                "kind": "screenshot",
                "source": "device-screen",
                "data": {"reference": "frame-42"},
            },
        ],
    })

    history = runtime.observations.history(run_id=run.run_id)

    assert len(history) == 1
    assert history[0].kind == "screenshot"
    assert history[0].observation_id.startswith("obs_")


def test_the_catalogue_offered_to_the_model_is_the_android_family(client):

    test_client, runtime = client

    test_client.post("/api/agent/step", json={
        "session_id": "session_test00000026",
        "goal": "anything",
    })

    tools = runtime.llm.requests[0]["tools"]
    names = {entry["function"]["name"] for entry in tools}

    assert "android.launch_app" in names
    assert "android.wait_for" in names
    assert all(entry["type"] == "function" for entry in tools)


def test_tool_results_without_pending_calls_are_a_conflict(client):
    """Replaying stale envelopes against a fresh run must not fold."""

    test_client, runtime = client

    runtime.start_run("fresh task", "session_test00000027")

    response = test_client.post("/api/agent/step", json={
        "session_id": "session_test00000027",
        "run_id": runtime.get_run(
            [r for r in ()] or list(runtime._runs)[0]
        ).run_id,
        "tool_results": [
            {
                "tool_call_id": "call_forged00000001",
                "tool": "android.tap",
                "arguments": {},
                "ok": True,
            }
        ],
    })

    # The run's last message is the goal, not a tool_calls request, so
    # there is nothing these results can be paired with.
    assert response.status_code == 409