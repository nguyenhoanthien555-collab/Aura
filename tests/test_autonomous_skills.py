"""
Focused regression suite for autonomous skill selection and execution:

    natural-language intent -> skill discovery -> live capability
    filtering -> permission/health gates -> ToolExecutor -> ToolResult
    -> postcondition verification -> grounded answer

Pins what grounding demands: the registry is the only authority on what
may run, refusals are structured instead of fabricated success, mutating
actions need verified postconditions, and device-state questions are
answered from observations or not at all.
"""

import uuid

import pytest

from agent.runtime import AgentRuntime, RunStatus, StopReason
from brain.native_fc import ModelTurn, ToolCallRequest
from core.capabilities import health, permissions
from core.capabilities.discovery import discovery as skill_discovery
from core.capabilities.models import CapabilityState
from tools.base import ToolRisk
from tools.executor import ToolExecutor, ToolPolicy
from tools.providers.android_bridge import LoopbackDeviceBridge
from tools.providers.android_provider import AndroidProvider
from tools.registry import ToolRegistry


CANONICAL_INTENTS = {
    "Open TikTok.": "android.app_launch",
    "What app is currently open on my phone?": "android.foreground_app",
    "Go home.": "android.home",
    "Find the Settings button.": "android.ui_search",
}


class ScriptedLLM:
    """Plays back prepared turns; records every request."""

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


def tool_turn(name, **arguments):
    return ModelTurn(tool_calls=(
        ToolCallRequest(
            call_id=uuid.uuid4().hex[:8], name=name, arguments=arguments
        ),
    ))


def make_inline_runtime(turns, bridge=None):
    """An inline runtime whose skills come from the real provider chain."""

    bridge = bridge or LoopbackDeviceBridge()

    registry = ToolRegistry()
    AndroidProvider(bridge).register_into(registry)

    executor = ToolExecutor(
        registry=registry,
        policy=ToolPolicy(
            enabled=True,
            allowed=frozenset(registry.names()),
            auto_approve=frozenset({
                ToolRisk.SAFE, ToolRisk.SENSITIVE, ToolRisk.DANGEROUS,
            }),
        ),
    )

    runtime = AgentRuntime(
        llm=ScriptedLLM(turns),
        executor=executor,
        registry=registry,
        system_prompt="You are Aura's device agent.",
        max_steps=15,
    )

    return runtime, bridge


@pytest.fixture()
def grounding_state():
    """Loopback-backed Android capability checks for this test only."""

    from core.capabilities import registry as cap_registry

    saved_caps = dict(cap_registry._capabilities)
    saved_perm_checks = dict(permissions._checks)
    saved_perm_grants = dict(permissions._granted_permissions)
    saved_health_checks = dict(health._checks)

    bridge = LoopbackDeviceBridge()
    registry = ToolRegistry()
    provider = AndroidProvider(bridge)
    provider.register_into(registry)

    yield bridge

    # Restore whatever registrations other tests expect: capabilities,
    # permission resolvers and health checks are process-wide globals.
    for cap_id, cap in saved_caps.items():
        cap_registry.register(cap)
    permissions._checks.clear()
    permissions._checks.update(saved_perm_checks)
    permissions._granted_permissions.clear()
    permissions._granted_permissions.update(saved_perm_grants)
    health._checks.clear()
    health._checks.update(saved_health_checks)


@pytest.fixture()
def permission_env():
    """Snapshot/restore wrapper around the global permission resolver."""

    from core.capabilities import permissions as perms

    saved_checks = dict(perms._checks)
    saved_grants = dict(perms._granted_permissions)

    yield perms

    perms._checks.clear()
    perms._checks.update(saved_checks)
    perms._granted_permissions.clear()
    perms._granted_permissions.update(saved_grants)


@pytest.fixture()
def health_env():
    """Snapshot/restore wrapper around the global health registry."""

    from core.capabilities import health as health_registry

    saved_checks = dict(health_registry._checks)

    yield health_registry

    health_registry._checks.clear()
    health_registry._checks.update(saved_checks)


# ----------------------------------------------------------------------
# Intent -> capability discovery and ranking
# ----------------------------------------------------------------------

def test_canonical_intents_rank_the_right_executable_skill(grounding_state):
    for intent, expected_id in CANONICAL_INTENTS.items():
        item = skill_discovery.select_best_executable(intent)

        assert item is not None, f"no executable skill found for: {intent}"
        assert item["capability_id"] == expected_id
        assert item["state"] == CapabilityState.AVAILABLE.value


def test_screen_question_ranks_a_screen_observation_capability(grounding_state):
    item = skill_discovery.select_best_executable(
        "What is on my phone screen?"
    )

    assert item is not None
    assert item["capability_id"] in ("android.screen_capture",
                                     "android.ui_tree")


def test_question_intents_prefer_observations_over_mutations():
    explanation = skill_discovery.explain(
        "What app is currently open on my phone?"
    )

    ids = [item["capability_id"] for item in explanation["ranked"]]
    assert ids.index("android.foreground_app") < ids.index("android.app_launch")


def test_selection_carries_the_bound_tool_name(grounding_state):
    item = skill_discovery.select_best_executable(
        "What app is currently open on my phone?"
    )

    assert item["tool"] == "android.get_foreground_app"


# ----------------------------------------------------------------------
# Executable-state filtering
# ----------------------------------------------------------------------

def test_missing_capability_reports_no_match(grounding_state):
    explanation = skill_discovery.explain("recite poetry from a pinecone")

    assert explanation["selected"] is None
    assert explanation["diagnosis"]["no_capability_matched"] is True
    assert explanation["ranked"] == []


def test_permission_blocked_candidates_are_filtered_out(
    grounding_state, permission_env
):
    permission_env.register_check(
        "android.accessibility",
        lambda: (False, "user revoked accessibility"),
    )

    assert skill_discovery.select_best_executable("Go home.") is None

    diagnosis = skill_discovery.explain("Go home.")["diagnosis"]
    assert diagnosis["all_candidates_blocked"] is True
    blocked = {
        entry["capability_id"]: entry
        for entry in diagnosis["missing_permissions"]
    }
    assert blocked["android.home"]["permissions"] == ["android.accessibility"]
    missing_detail = blocked["android.home"]["missing"][0]
    assert missing_detail["permission"] == "android.accessibility"
    assert "revoked" in missing_detail["reason"]


def test_unhealthy_dependency_candidates_are_filtered_out(
    grounding_state, health_env
):
    health_env.register_check(
        "android.home",
        lambda: {"healthy": False,
                 "reason": "Android companion dependency is unhealthy"},
    )

    assert skill_discovery.select_best_executable("Go home.") is None

    diagnosis = skill_discovery.explain("Go home.")["diagnosis"]
    broken = {
        entry["capability_id"]: entry
        for entry in diagnosis["unhealthy_dependencies"]
    }
    assert broken["android.home"]["state"] == CapabilityState.UNHEALTHY.value


def test_missing_heartbeat_candidates_are_filtered_out(
    grounding_state, health_env
):
    health_env.register_check(
        "android.home",
        lambda: {"healthy": False,
                 "reason": "no Android companion poll heartbeat has been received"},
    )

    assert skill_discovery.select_best_executable("Go home.") is None

    entries = (
        skill_discovery.explain("Go home.")["diagnosis"]
        ["unhealthy_dependencies"]
    )
    assert entries[0]["state"] == CapabilityState.UNAVAILABLE.value
    assert "heartbeat" in entries[0]["reason"]


# ----------------------------------------------------------------------
# Autonomous execution through the ToolExecutor
# ----------------------------------------------------------------------

def test_observation_intent_executes_via_executor_and_grounds_the_reply():
    runtime, bridge = make_inline_runtime([
        tool_turn("android.get_foreground_app"),
        ModelTurn(text="com.aura.companion is in the foreground."),
    ])

    run = runtime.run_to_completion(
        runtime.start_run("What app is currently open on my phone?", "s1")
    )

    # Executed through the ToolExecutor against the bridge - exactly once.
    assert bridge.invocations == [("android.get_foreground_app", {})]
    assert ("android.get_foreground_app", True) in runtime.executor.history

    assert run.status is RunStatus.COMPLETED
    assert run.stop_reason is StopReason.GOAL_VERIFIED
    assert run.observed_ok == 1


def test_unknown_tool_is_refused_before_any_device_directive():
    from tools.providers.android_bridge import DeclaredOnlyBridge

    registry = ToolRegistry()
    AndroidProvider(DeclaredOnlyBridge()).register_into(registry)

    runtime = AgentRuntime(
        llm=ScriptedLLM([tool_turn("android.cheerful_nonsense"),
                         ModelTurn(text="reported the refusal.")]),
        registry=registry,
        system_prompt="device agent",
        deferred=True,
        max_steps=5,
    )

    run = runtime.start_run("open nothing in particular", "s2")
    directive = runtime.advance(run)

    assert directive.kind == "tool_calls"
    # The hallucinated name never becomes a device directive...
    assert directive.tool_calls == ()
    # ...it comes back as a structured refusal instead.
    envelope = directive.envelopes[0]
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "UNKNOWN_TOOL"


def test_blocked_capability_fails_structured_without_touching_the_device(
    permission_env,
):
    runtime, bridge = make_inline_runtime([
        tool_turn("android.get_ui_tree"),
        ModelTurn(text="the capability is unavailable right now."),
    ])

    permission_env.register_check(
        "android.accessibility",
        lambda: (False, "accessibility service disabled by user"),
    )

    run = runtime.start_run("inspect the current UI tree", "s3")
    directive = runtime.advance(run)

    envelope = directive.envelopes[0]
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "BLOCKED_PERMISSION"
    assert "accessibility" in envelope["error"]["message"]
    assert bridge.invocations == []


def test_missing_node_returns_real_failure_not_an_invented_element():
    bridge = LoopbackDeviceBridge()
    bridge.install_screen("com.android.launcher", {})  # no Settings button

    runtime, _ = make_inline_runtime([
        tool_turn("android.find_node", text="Settings"),
        ModelTurn(text="NODE_NOT_FOUND: there is no Settings button."),
    ], bridge=bridge)

    run = runtime.start_run("Find the Settings button.", "s4")

    directive = runtime.advance(run)
    envelope = directive.envelopes[0]

    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "NODE_NOT_FOUND"

    finish = runtime.advance(run)
    assert finish.kind == "final"


def _always_settled_clock():
    """A loopback clock whose every read skips past any settle delay."""

    state = {"t": 0.0}

    def now() -> float:
        state["t"] += 10.0
        return state["t"]

    return now


def test_mutation_needs_verified_postcondition_before_completed_verdict():
    bridge = LoopbackDeviceBridge(clock=_always_settled_clock())

    runtime, _ = make_inline_runtime([
        tool_turn("android.launch_app", package="com.example.launchme"),
        tool_turn("android.wait_for",
                  condition="foreground=com.example.launchme"),
        ModelTurn(text="launched and verified."),
    ], bridge=bridge)

    run = runtime.start_run("Open ExampleApp.", "s5")
    runtime.advance(run)  # launch executes; settle pending -> unverified

    assert any(entry[0] == "android.launch_app" for entry in run.unverified)

    # Completion cannot be accepted while unverified verification rounds
    # remain - the second scripted turn performs wait_for and retires it.
    runtime.advance(run)

    final = runtime.advance(run)
    assert final.kind == "final"
    assert run.status is RunStatus.COMPLETED
    assert run.stop_reason is StopReason.GOAL_VERIFIED
    assert run.unverified == []


def test_unobserved_question_cannot_be_answered_as_verified_fact():
    runtime, bridge = make_inline_runtime([
        ModelTurn(text="You are definitely inside TikTok."),
        ModelTurn(text="TikTok is open on your phone right now."),
    ])

    run = runtime.run_to_completion(
        runtime.start_run("What app is currently open on my phone?", "s6")
    )

    # No observation ever ran; an answer may exist but must NOT be
    # classified as grounded fact.
    assert bridge.invocations == []
    assert run.observed_ok == 0
    assert run.unverified  # sentinel: question answered without evidence
    assert run.stop_reason is StopReason.COMPLETED_UNVERIFIED


def test_correction_loop_tells_the_model_to_observe_first():
    runtime, _ = make_inline_runtime([
        ModelTurn(text="TikTok."),
        tool_turn("android.get_foreground_app"),
        ModelTurn(text="The foreground app is com.aura.companion."),
    ])

    run = runtime.start_run("What app is open on my phone?", "s7")

    first = runtime.advance(run)
    assert first.kind == "final" and first.text == "observation required"

    user_correction = [
        message for message in run.messages
        if message.get("role") == "user"
    ][-1]
    assert "no successful observation yet" in user_correction["content"]

    runtime.advance(run)
    finish = runtime.advance(run)

    assert finish.kind == "final"
    assert run.observed_ok == 1
    assert run.stop_reason is StopReason.GOAL_VERIFIED


def test_self_knowledge_context_includes_live_inventory_not_hardcoding():
    runtime, _ = make_inline_runtime([
        ModelTurn(text="answer inventory-based"),
    ])

    run = runtime.start_run("What can you do on my Android phone?", "s8")

    runtime.advance(run)

    system_text = runtime.llm.requests[0]["system"]
    assert "FULL LIVE CAPABILITY INVENTORY" in system_text
    assert "AVAILABLE NOW (ACTUALLY EXECUTABLE)" in system_text
    assert "LIVE CAPABILITY EVIDENCE" in system_text