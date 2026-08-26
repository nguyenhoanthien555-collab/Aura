"""
Regression tests for the agent runtime (migration PARTS 7, 8, 9, 10,
13, 25, 31).

These pin the behaviours whose absence caused the old failures: multi-
round convergence without depending on the step ceiling, structured
envelopes for success and failure, verification gating on completion,
per-run isolation of transcripts and observations, unique ids end to
end, and compaction that preserves the goal.
"""

import json
import uuid

from agent.runtime import (
    AgentRuntime,
    Directive,
    RunStatus,
    StopReason,
)
from brain.native_fc import ModelTurn, ToolCallRequest
from core.observations import (
    ObservationKind,
    ObservationStore,
)
from tools.executor import ToolExecutor, ToolPolicy
from tools.providers.android_bridge import LoopbackDeviceBridge
from tools.providers.android_provider import AndroidProvider
from tools.registry import ToolRegistry


ALL_ANDROID = sorted({
    "android.get_foreground_app", "android.get_ui_tree",
    "android.find_node", "android.screenshot", "android.tap",
    "android.long_press", "android.swipe", "android.type_text",
    "android.press_key", "android.back", "android.home",
    "android.launch_app", "android.wait_for", "android.verify",
})


class ScriptedLLM:
    """
    A native-function-calling model that plays back prepared turns and
    records every request it receives - which is how the tests assert on
    the transcript the runtime actually sent.
    """

    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    def generate_with_tools(self, system, messages, tools):
        self.requests.append({
            "system": system,
            "messages": [dict(message) for message in messages],
            "tools": tools,
        })
        return self.turns.pop(0)


def tool_call(name, **arguments):
    return ModelTurn(tool_calls=(
        ToolCallRequest(
            call_id=uuid.uuid4().hex[:8], name=name, arguments=arguments
        ),
    ))


def text_turn(body):
    return ModelTurn(text=body)


def make_runtime(turns, bridge=None, **kwargs):
    """A fully wired inline runtime over the loopback device."""

    clock = kwargs.pop("clock", None) or {"now": 1000.0}
    bridge = bridge or LoopbackDeviceBridge(clock=lambda: clock["now"])

    registry = ToolRegistry()
    AndroidProvider(bridge).register_into(registry)

    executor = ToolExecutor(
        registry=registry,
        policy=ToolPolicy.from_config({
            "enabled": True,
            "allowed": ALL_ANDROID,
            "auto_approve": ["safe", "sensitive", "dangerous"],
        }),
    )

    runtime = AgentRuntime(
        llm=ScriptedLLM(turns),
        executor=executor,
        registry=registry,
        system_prompt="You are Aura's device agent.",
        max_steps=kwargs.pop("max_steps", 15),
        clock=lambda: clock["now"],
        **kwargs,
    )

    return runtime, bridge, registry


# ----------------------------------------------------------------------
# Convergence (PART 7)
# ----------------------------------------------------------------------

def test_multi_round_task_converges_verified_without_hitting_the_ceiling():
    """
    The YouTube-search shape: launch -> wait -> tap -> type -> submit ->
    verify results -> done. The ceiling is 15; the task takes well under.
    """

    turns = [
        # A bare text turn means "final answer" under native function
        # calling, so the narration rides with the first tool call.
        tool_call("android.launch_app",
                  package="com.google.android.youtube"),
        tool_call("android.wait_for",
                  condition="foreground=com.google.android.youtube"),
        tool_call("android.tap", text="Search"),
        tool_call("android.type_text", text="Minecraft"),
        tool_call("android.press_key", key="enter"),
        tool_call("android.verify", check="text_visible=result-search_btn"),
        text_turn("YouTube is showing Minecraft search results."),
    ]

    # The loopback settles launches instantly, so the wait_for round
    # genuinely verifies; the point under test is round structure.
    class InstantSettle(LoopbackDeviceBridge):
        SETTLE_S = 0.0

    runtime, bridge, _ = make_runtime(
        turns, bridge=InstantSettle(clock=lambda: 1000.0)
    )

    run = runtime.start_run(
        goal="Open YouTube and search Minecraft",
        session_id="session_test00000001",
    )
    runtime.run_to_completion(run)

    assert run.status is RunStatus.COMPLETED
    assert run.stop_reason is StopReason.GOAL_VERIFIED
    assert run.rounds == 7          # every round accounted for...
    assert run.rounds < runtime.max_steps   # ...and none wasted on hope


def test_the_loop_uses_tools_rather_than_prose_actions():

    turns = [
        tool_call("android.launch_app", package="com.any"),
        text_turn("Launched."),
    ]

    runtime, bridge, _ = make_runtime(turns)

    run = runtime.start_run("open any app", "session_test00000002")
    runtime.run_to_completion(run)

    executed = [name for name, _ in bridge.invocations]

    assert executed == ["android.launch_app"]
    # The catalogue must be offered natively, not described in a prompt
    # for the model to imitate.
    assert runtime.llm.requests[0]["tools"]


# ----------------------------------------------------------------------
# Structured results (PART 8)
# ----------------------------------------------------------------------

def test_successful_calls_produce_part8_envelopes():

    turns = [
        tool_call("android.launch_app", package="com.google.android.youtube"),
        text_turn("requested"),
    ]

    runtime, _, _ = make_runtime(turns)
    run = runtime.start_run("launch youtube", "session_test00000003")

    directive = runtime.advance(run)

    assert directive.kind == "tool_calls"

    envelope = directive.envelopes[0]

    assert envelope["ok"] is True
    assert envelope["tool"] == "android.launch_app"
    assert envelope["result"]["launched"] == "com.google.android.youtube"
    assert envelope["postcondition"]["verified"] is False   # still settling
    assert envelope["observation_id"].startswith("obs_")
    assert envelope["tool_call_id"].startswith("call_")


def test_failed_action_returns_structured_failure_not_prose():

    turns = [
        tool_call("android.tap", text="NoSuchButton"),
        text_turn("recovering"),
    ]

    runtime, _, _ = make_runtime(turns)
    run = runtime.start_run("tap something missing", "session_test00000004")

    directive = runtime.advance(run)
    envelope = directive.envelopes[0]

    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "NODE_NOT_FOUND"
    assert envelope["error"]["message"]


def test_unknown_tool_yields_a_structured_denial_the_model_can_read():

    turns = [
        tool_call("android.teleport", destination="moon"),
        text_turn("understood"),
    ]

    runtime, _, _ = make_runtime(turns)
    run = runtime.start_run("do magic", "session_test00000005")

    directive = runtime.advance(run)
    envelope = directive.envelopes[0]

    assert envelope["ok"] is False
    assert envelope["tool"] == "android.teleport"


# ----------------------------------------------------------------------
# Postcondition verification gating (PART 9)
# ----------------------------------------------------------------------

def test_a_completion_claim_over_an_unverified_launch_is_sent_back():
    """
    The model may not declare success while a mutation lacks evidence.
    The runtime demands a verification round instead of accepting
    'complete' on faith.
    """

    turns = [
        tool_call("android.launch_app", package="com.google.android.youtube"),
        text_turn("Done! YouTube is open."),          # premature claim
        tool_call("android.wait_for",
                  condition="foreground=com.google.android.youtube"),
        text_turn("Verified - YouTube is foreground."),
    ]

    # The clock never advances past the settle, so wait_for would fail -
    # use an explicit verify that reads true regardless of settle.
    clock = {"now": 1000.0}
    bridge = LoopbackDeviceBridge(clock=lambda: clock["now"])

    turns[2] = tool_call("android.verify",
                         check="package_is=com.google.android.youtube")

    # Force the foreground directly: simulates the app having appeared
    # without the loopback's timed settle having been observed.
    runtime, bridge, _ = make_runtime(turns, clock=clock)

    directive = runtime.advance(runtime.start_run(
        "launch youtube", "session_test00000006"
    ))
    assert directive.kind == "tool_calls"

    run = runtime.get_run(list(runtime._runs)[0])

    # Premature completion -> the runtime refuses it.
    directive = runtime.advance(run)
    assert directive.text == "verification required"
    assert run.verify_rounds == 1

    # The correction names what was unverified.
    last_user = [m for m in run.messages if m["role"] == "user"][-1]
    assert "android.launch_app" in last_user["content"]


def test_verified_evidence_clears_the_pending_mutation_and_allows_success():

    turns = [
        tool_call("android.launch_app", package="com.google.android.youtube"),
        text_turn("Done."),
        tool_call("android.wait_for",
                  condition="foreground=com.google.android.youtube"),
        text_turn("Confirmed foreground."),
    ]

    clock = {"now": 1000.0}

    def slow_clock():
        return clock["now"]

    # A settle long enough that launch stays unverified at first, plus
    # time moving forward before the wait_for executes.
    class SlowBridge(LoopbackDeviceBridge):
        SETTLE_S = 5.0

    bridge = SlowBridge(clock=slow_clock)

    runtime, _, _ = make_runtime(turns, bridge=bridge, clock=clock)

    run = runtime.start_run("launch youtube", "session_test00000007")

    runtime.advance(run)                       # launch: verified False
    assert run.unverified, "launch should await evidence"

    clock["now"] += 10.0                       # settle elapses
    runtime.advance(run)                       # premature completion
    runtime.advance(run)                       # wait_for proves it

    assert not run.unverified
    runtime.advance(run)                       # final answer accepted
    assert run.stop_reason is StopReason.GOAL_VERIFIED


def test_persistent_unverified_mutations_end_completed_but_labelled():
    """Honesty over blocking: bounded nudging, then a labelled outcome."""

    turns = [
        tool_call("android.launch_app", package="com.never.settles"),
        text_turn("done 1"),
        text_turn("done 2"),                     # ignores both nudges
        text_turn("done 3"),
    ]

    class NeverSettle(LoopbackDeviceBridge):
        SETTLE_S = 99999.0

    clock = {"now": 0.0}
    runtime, _, _ = make_runtime(
        turns, bridge=NeverSettle(clock=lambda: clock["now"]), clock=clock
    )

    run = runtime.start_run("launch", "session_test00000008")
    runtime.run_to_completion(run)

    assert run.status is RunStatus.COMPLETED
    assert run.stop_reason is StopReason.COMPLETED_UNVERIFIED


def test_consecutive_failures_stop_under_retry_exhausted_not_the_ceiling():

    turns = [
        tool_call("android.tap", text=f"ghost{i}")
        for i in range(6)
    ] + [text_turn("unused")]

    runtime, _, _ = make_runtime(turns)

    run = runtime.start_run("tap ghosts", "session_test00000009")
    runtime.run_to_completion(run)

    assert run.status is RunStatus.FAILED
    assert run.stop_reason is StopReason.RETRY_EXHAUSTED


# ----------------------------------------------------------------------
# Identity and isolation (PART 13)
# ----------------------------------------------------------------------

def test_tool_call_and_observation_ids_are_unique_across_a_run():

    class InstantSettle(LoopbackDeviceBridge):
        SETTLE_S = 0.0

    clock = {"now": 1000.0}
    bridge = InstantSettle(clock=lambda: clock["now"])
    bridge.install_screen("com.android.launcher", {
        "search_btn": {"text": "Search", "clickable": True},
    })

    turns = [
        tool_call("android.launch_app", package="com.a"),
        tool_call("android.wait_for", condition="foreground=com.a"),
        tool_call("android.tap", text="Search"),
        tool_call("android.type_text", text="x"),
        text_turn("done"),
    ]

    runtime, _, _ = make_runtime(turns, bridge=bridge, clock=clock)
    run = runtime.start_run("multi", "session_test00000010")

    seen_calls = []
    seen_obs = []

    while run.status is RunStatus.RUNNING and run.rounds < 6:
        directive = runtime.advance(run)

        for envelope in directive.envelopes:
            seen_calls.append(envelope["tool_call_id"])
            seen_obs.append(envelope["observation_id"])

    assert len(seen_calls) == len(set(seen_calls))
    assert len(seen_obs) == len(set(seen_obs))
    assert all(call_id.startswith("call_") for call_id in seen_calls)
    assert all(obs_id.startswith("obs_") for obs_id in seen_obs)


def test_two_tasks_in_one_runtime_cannot_read_each_others_state():
    """The structural answer to 'a response reused by another task'."""

    turns_a = [
        tool_call("android.launch_app", package="com.game.one"),
        tool_call("android.wait_for", condition="foreground=com.game.one"),
        text_turn("A finished."),
    ]
    turns_b = [
        tool_call("android.launch_app", package="com.game.two"),
        tool_call("android.wait_for", condition="foreground=com.game.two"),
        text_turn("B finished."),
    ]

    class InstantSettle(LoopbackDeviceBridge):
        SETTLE_S = 0.0

    runtime, _, _ = make_runtime(
        turns_a + turns_b, bridge=InstantSettle(clock=lambda: 1000.0)
    )

    run_a = runtime.start_run("open game one", "session_test00000011")
    run_b = runtime.start_run("open game two", "session_test00000012")

    runtime.run_to_completion(run_a)
    runtime.run_to_completion(run_b)

    # Separate ids throughout.
    assert run_a.run_id != run_b.run_id
    assert run_a.task_id != run_b.task_id

    # Transcripts never crossed: each holds only its own goal.
    joined_a = json.dumps(run_a.messages)
    joined_b = json.dumps(run_b.messages)

    assert "game two" not in joined_a
    assert "game one" not in joined_b

    # Observations are scoped per run.
    obs_a = runtime.observations.history(run_id=run_a.run_id)
    obs_b = runtime.observations.history(run_id=run_b.run_id)

    assert obs_a and obs_b
    assert all(o.run_id == run_a.run_id for o in obs_a)
    assert all(o.run_id == run_b.run_id for o in obs_b)


def test_cancelled_request_stops_under_cancelled_not_completed():

    turns = [tool_call("android.tap", text=f"n{i}") for i in range(3)]

    runtime, _, _ = make_runtime(
        turns + [text_turn("never reached")]
    )

    run = runtime.start_run("long task", "session_test00000013")

    runtime.advance(run)                      # first tap executes
    assert runtime.cancel(run.run_id)

    final = runtime.run_to_completion(run)

    assert final.status is RunStatus.CANCELLED
    assert final.stop_reason is StopReason.CANCELLED


def test_step_ceiling_is_a_diagnosis_not_an_outcome():

    turns = [
        tool_call("android.get_foreground_app")   # harmless, endless
        for _ in range(10)
    ]

    runtime, _, _ = make_runtime(turns, max_steps=4)

    run = runtime.start_run("wander", "session_test00000014")
    runtime.run_to_completion(run)

    assert run.status is RunStatus.FAILED
    assert run.stop_reason is StopReason.STEP_CEILING
    assert "ceiling" in run.stop_detail


def test_silence_is_not_accepted_as_completion():

    from brain.native_fc import ModelTurn

    runtime, _, _ = make_runtime([
        ModelTurn(),      # empty round one: warned
        ModelTurn(),      # empty round two: failed
    ])

    run = runtime.start_run("do something", "session_test00000015")
    runtime.run_to_completion(run)

    assert run.status is RunStatus.FAILED


# ----------------------------------------------------------------------
# Context compaction (PART 25)
# ----------------------------------------------------------------------

def test_old_payloads_compact_while_the_goal_and_recent_rounds_survive():

    class InstantSettle(LoopbackDeviceBridge):
        SETTLE_S = 0.0

    clock = {"now": 1000.0}
    bridge = InstantSettle(clock=lambda: clock["now"])

    # One node carrying a big payload - a tree large enough that its
    # envelope crosses the compaction length threshold.
    big_text = "x" * 800
    bridge.install_screen("com.some.app", {
        "n1": {"text": big_text, "clickable": False},
    })

    tree_rounds = 14
    turns = [
        tool_call("android.get_ui_tree") for _ in range(tree_rounds)
    ] + [text_turn("finished looking")]

    runtime, _, _ = make_runtime(
        turns, bridge=bridge, clock=clock, max_steps=20
    )

    run = runtime.start_run("inspect repeatedly", "session_test00000016")
    runtime.run_to_completion(run)

    assert run.status is RunStatus.COMPLETED

    contents = [
        message.get("content", "") for message in run.messages
    ]

    # The goal is untouched.
    assert any("inspect repeatedly" in c for c in contents)

    # Old payload messages were summarized, not deleted...
    compacted = [c for c in contents if c.startswith("[compacted]")]
    assert compacted
    assert all("android.get_ui_tree: ok" in c for c in compacted)

    # ...recent rounds stay verbatim for the current decision.
    verbatim_trees = [c for c in contents if big_text in c]
    assert verbatim_trees
    assert len(compacted) + len(verbatim_trees) >= tree_rounds


# ----------------------------------------------------------------------
# Deferred (device-driven) mode (PARTS 7, 8)
# ----------------------------------------------------------------------

def make_deferred_runtime(turns):
    """The device-polling shape: directives out, folded reports in."""

    class InstantSettle(LoopbackDeviceBridge):
        SETTLE_S = 0.0

    bridge = InstantSettle(clock=lambda: 1000.0)

    registry = ToolRegistry()
    AndroidProvider(bridge).register_into(registry)

    runtime = AgentRuntime(
        llm=ScriptedLLM(turns),
        registry=registry,
        observations=ObservationStore(clock=lambda: 1000.0),
        system_prompt="device agent",
        deferred=True,
        max_steps=10,
    )

    return runtime, bridge


def test_deferred_mode_hands_out_calls_without_executing_them():

    runtime, bridge = make_deferred_runtime([
        tool_call("android.launch_app", package="com.google.android.youtube"),
    ])

    run = runtime.start_run("launch youtube", "session_test00000017")

    directive = runtime.advance(run)

    assert directive.kind == "tool_calls"
    assert not directive.envelopes          # nothing executed here

    _, request = directive.tool_calls[0]
    assert request.name == "android.launch_app"

    # And nothing reached a device through this runtime - the phone will
    # pick the directive up instead.
    assert bridge.invocations == []

    wire = directive.to_dict()
    assert wire["type"] == "tool_calls"
    assert wire["tool_calls"][0]["tool"] == "android.launch_app"


def test_device_reports_fold_back_into_the_transcript():

    runtime, bridge = make_deferred_runtime([
        tool_call("android.launch_app", package="com.google.android.youtube"),
        text_turn("requested and reported."),
    ])

    run = runtime.start_run("launch youtube", "session_test00000018")

    directive = runtime.advance(run)
    call_id, request = directive.tool_calls[0]

    # The phone executes against its own device...
    report = bridge.invoke(request.name, request.arguments)

    # ...and sends back an envelope in the agreed shape.
    envelope = {
        "tool_call_id": call_id,
        "call_id": request.call_id,
        "tool": request.name,
        "arguments": request.arguments,
        "ok": bool(report.get("ok")),
        "result": report.get("result", {}),
        "postcondition": report.get("postcondition"),
    }

    messages_before = len(runtime.llm.requests[0]["messages"])
    runtime.fold_tool_reports(run, [envelope])

    # One more round: the model is asked again and sees a proper tool
    # result message.
    runtime.advance(run)
    messages = runtime.llm.requests[1]["messages"]
    assert len(messages) > messages_before

    tool_messages = [m for m in messages if m["role"] == "tool"]
    assert tool_messages
    folded = json.loads(tool_messages[-1]["content"])
    assert folded[0]["ok"] is True
    assert folded[0]["result"]["launched"] == "com.google.android.youtube"

    # Verification bookkeeping applies identically off-device: launch
    # reported unmet until something proves the foreground changed.
    assert run.unverified