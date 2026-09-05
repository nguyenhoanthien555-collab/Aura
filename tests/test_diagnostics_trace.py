"""
The per-request diagnostics trace.

Pins gap 2 from the architecture audit: one consolidated JSON record per
request, written to `logs/diagnostics.jsonl`, carrying identifiers,
counts, durations and the stream reconciliation - and carrying none of
the conversation content, because a diagnostics file is not a second
transcript.

Two boundary integrations are pinned end to end:

    * an agent run writes its trace when it stops, whatever the outcome;
    * the reconciliation helper is honest about drops (produced vs
      delivered) and the WebSocket complete frame carries it.

The emissions must never break the request they describe - tested
explicitly for the case the trace file cannot be created.
"""

import json

import pytest

from agent.runtime import RunStatus
from core.trace import (
    emit_trace,
    provider_label,
    stream_reconciliation,
)
from brain.providers.fallback import FallbackProvider


@pytest.fixture()
def trace_file(tmp_path, monkeypatch):
    """
    Redirect the JSONL sink into the test's tmp directory.

    The diagnostics logger is a module global built lazily, so the test
    clears any handler a previous test created and lets the next
    emission rebuild it against the redirected path.
    """

    import core.trace as trace_mod

    path = tmp_path / "diagnostics.jsonl"

    monkeypatch.setattr(trace_mod, "TRACE_FILE", path)
    trace_mod._logger.handlers.clear()

    yield path

    trace_mod._logger.handlers.clear()


def read_records(path):

    lines = path.read_text(encoding="utf-8").splitlines()

    return [json.loads(line) for line in lines if line.strip()]


# ----------------------------------------------------------------------
# The emitter
# ----------------------------------------------------------------------

def test_one_request_writes_one_json_line(trace_file):
    emit_trace(
        "agent_run",
        run_id="run_abc123456789abcd",
        session_id="session_abc1234567",
        status="completed",
        rounds=3,
    )

    records = read_records(trace_file)

    assert len(records) == 1

    record = records[0]

    assert record["kind"] == "agent_run"
    assert record["run_id"] == "run_abc123456789abcd"
    assert record["status"] == "completed"
    assert record["rounds"] == 3
    assert record["ts"]          # timestamped, machine-readable


def test_none_fields_are_omitted_rather_than_written_as_nulls(trace_file):
    emit_trace("chat_stream", message_id="m1", provider=None)

    record = read_records(trace_file)[0]

    assert "provider" not in record


def test_tracing_never_breaks_the_request_when_the_sink_is_unusable(
    tmp_path, monkeypatch
):
    import core.trace as trace_mod

    # A directory cannot be a file handler's target. Emission must log
    # and move on - a diagnostics problem must never cost a turn.
    monkeypatch.setattr(trace_mod, "TRACE_FILE", tmp_path)
    trace_mod._logger.handlers.clear()

    emit_trace("agent_run", run_id="run_abc123456789abcd")


# ----------------------------------------------------------------------
# Helpers the boundaries share
# ----------------------------------------------------------------------

def test_reconciliation_calls_a_clean_stream_complete():
    report = stream_reconciliation(
        ["Hello ", "there."], ["Hello ", "there."]
    )

    assert report == {
        "produced_fragments": 2,
        "delivered_fragments": 2,
        "produced_chars": 12,
        "delivered_chars": 12,
        "complete": True,
    }


def test_reconciliation_names_a_drop_instead_of_hiding_it():
    # Three fragments were produced, two were delivered - a disconnect
    # mid-reply, say. `complete` is the flag a dashboard can count.
    report = stream_reconciliation(
        ["a", "b", "c"], ["a", "b"]
    )

    assert report["produced_chars"] == 3
    assert report["delivered_chars"] == 2
    assert report["complete"] is False


def test_provider_label_reads_through_wrappers_and_chains():
    class Inner:

        provider_name = "gemini"

        def generate(self, prompt):
            return ""

    class Adapter:

        def __init__(self, llm):
            self.llm = llm

    assert provider_label(Inner()) == "gemini"
    assert provider_label(Adapter(Inner())) == "gemini"
    assert provider_label(None) == ""
    assert provider_label(object()) == ""


def test_the_chain_reports_its_provider_and_attempts():
    from tests.test_capability_routing import (
        FailingProvider,
        WorkingProvider,
    )

    chain = FallbackProvider(
        [FailingProvider(), WorkingProvider()], "fake_dead->fake_alive"
    )

    # A chain's own name is the requested chain, not the provider that
    # answered - asked-for and active stay distinct, exactly like
    # BrainRouter.active_chain() documents.
    assert provider_label(chain) == "fake_dead->fake_alive"

    assert chain.generate("hi") == "hello"
    assert chain.active_provider_name == "fake_alive"


# ----------------------------------------------------------------------
# End to end: an agent run lands its trace
# ----------------------------------------------------------------------

def test_an_agent_run_writes_its_trace_when_it_stops(trace_file):
    import uuid

    from agent.runtime import AgentRuntime
    from brain.native_fc import ModelTurn, ToolCallRequest
    from core.observations import ObservationStore
    from tools.executor import ToolExecutor, ToolPolicy
    from tools.providers.android_bridge import LoopbackDeviceBridge
    from tools.providers.android_provider import AndroidProvider
    from tools.registry import ToolRegistry

    class ScriptedLLM:

        def __init__(self, turns):
            self.turns = list(turns)
            self.provider_name = "scripted"

        def generate_with_tools(self, system, messages, tools):
            return self.turns.pop(0)

    registry = ToolRegistry()

    # The loopback settles launches instantly, so launch_app genuinely
    # verifies under the static test clock (the same device test 7 in
    # test_agent_runtime.py uses for verified convergence).
    class InstantSettle(LoopbackDeviceBridge):
        SETTLE_S = 0.0

    AndroidProvider(InstantSettle(clock=lambda: 1000.0)).register_into(
        registry
    )

    executor = ToolExecutor(
        registry=registry,
        policy=ToolPolicy.from_config({
            "enabled": True,
            "allowed": {"android.launch_app", "android.wait_for",
                        "android.verify"},
            "auto_approve": ["safe", "sensitive", "dangerous"],
        }),
    )

    runtime = AgentRuntime(
        llm=ScriptedLLM([
            ModelTurn(tool_calls=(
                ToolCallRequest(
                    call_id=uuid.uuid4().hex[:8],
                    name="android.launch_app",
                    arguments={"package": "com.any"},
                ),
            )),
            ModelTurn(tool_calls=(
                ToolCallRequest(
                    call_id=uuid.uuid4().hex[:8],
                    name="android.wait_for",
                    arguments={"condition": "foreground=com.any"},
                ),
            )),
            ModelTurn(text="Launched."),
        ]),
        executor=executor,
        registry=registry,
        system_prompt="test",
        clock=lambda: 1000.0,
    )

    run = runtime.start_run("open any app", "session_test00000001")
    runtime.run_to_completion(run)

    assert run.status is RunStatus.COMPLETED

    records = read_records(trace_file)
    run_records = [r for r in records if r.get("kind") == "agent_run"]
    tool_records = [r for r in records if r.get("kind") == "tool_execution"]

    # One summary record per run, with one Phase 3 `tool_execution` line
    # per executed tool sitting beside it - the run count is no longer the
    # whole file, and both halves are pinned here.
    assert len(run_records) == 1
    assert run.tool_call_count == 2
    assert len(tool_records) == run.tool_call_count

    record = run_records[0]

    assert record["run_id"] == run.run_id
    assert record["task_id"] == run.task_id
    assert record["status"] == "completed"
    assert record["stop_reason"] == "goal_verified"
    assert record["provider"] == "scripted"
    assert record["rounds"] == run.rounds
    assert record["tool_calls"] == run.tool_call_count
    assert "duration_s" in record

    # The tool lines carry identity, status and verification state but
    # never the arguments or the content they named.
    by_tool = {r["tool"]: r for r in tool_records}

    assert set(by_tool) == {"android.launch_app", "android.wait_for"}
    assert all(r["status"] == "SUCCESS" for r in tool_records)
    assert all(r["execution_id"].startswith("exec_") for r in tool_records)
    assert all("retryable" in r and "evidence" in r for r in tool_records)
    assert "com.any" not in json.dumps(tool_records)

