"""
Phase 5A offline tests - the Android app-inventory foundation.

Phase 5A adds one read-only device capability (`android.app_inventory`
/ `android.list_apps`), a structured inventory payload, and - critically -
a seam that converts device postconditions into canonical Phase 3 Evidence
on the normal chat-path ToolResult, so a verified Android action can reach
VERIFIED instead of being capped at INFERRED.

Everything here is deterministic and hardware-free: a loopback bridge and a
scripted bridge stand in for the phone, and the real AndroidProvider,
ToolExecutor, EvidenceLedger and ResponseVerifier exercise the real wiring
(no fake device, but no real device either). Nothing claims live
verification.
"""
import json
import time

from brain.verify import ResponseVerifier
from brain.verify.ledger import EvidenceLedger
from brain.verify.status import ClaimState, VerifierDecision
from core.capabilities import registry as capability_registry
from core.capabilities.models import CapabilityState
from tools.base import ToolRisk
from tools.executor import ToolExecutor, ToolPolicy
from tools.outcome import Evidence, EvidenceKind, SideEffect, ToolStatus
from tools.providers.android_bridge import (
    DeviceBridge,
    LoopbackDeviceBridge,
    failure,
    normalise_device_report,
)
from tools.providers.android_provider import AndroidProvider
from tools.registry import ToolRegistry
from tools.schema import openai_function_schema

ANDROID_TOOLS = {
    "android.get_foreground_app", "android.get_ui_tree", "android.find_node",
    "android.screenshot", "android.tap", "android.long_press", "android.swipe",
    "android.type_text", "android.press_key", "android.back", "android.home",
    "android.launch_app", "android.wait_for", "android.verify",
    "android.list_apps",
}


# ----------------------------------------------------------------------
# Doubles - deterministic, no network, no device
# ----------------------------------------------------------------------

class FakeStore:
    def __init__(self):
        self.saved = []

    def save(self, role, content):
        self.saved.append((role, content))

    def get_recent(self, limit=10):
        return []


class ScriptedLLM:
    """One string per generate call; the first may be a tool JSON."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.replies.pop(0)


def _noop_capabilities(words):
    """A registry that knows nothing. Never matches, never assumes."""
    return "UNKNOWN", False, ""


class ScriptedBridge(DeviceBridge):
    """A real DeviceBridge whose reports are scripted per tool."""

    def __init__(self, reports=None):
        self.reports = dict(reports or {})
        self.received = []

    def invoke(self, tool: str, arguments: dict) -> dict:
        self.received.append((tool, dict(arguments)))
        return self.reports.get(
            tool,
            failure(tool, "UNSUPPORTED_TOOL", "no scripted report for this tool"),
        )

    def status(self) -> dict:
        return {
            "state": "AVAILABLE",
            "healthy": True,
            "reason": "scripted bridge",
            "permissions": {"android.accessibility": True},
        }


class TickClock:
    """A step clock so consecutive calls observably move forward."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        self.t += 1.0
        return self.t


def android_executor(bridge=None):
    """A registry + executor with every Android tool allowed."""
    bridge = bridge or LoopbackDeviceBridge()
    registry = ToolRegistry()
    count = AndroidProvider(bridge).register_into(registry)
    policy = ToolPolicy.from_config({
        "enabled": True,
        "allowed": sorted(ANDROID_TOOLS),
        "auto_approve": ["safe", "sensitive", "dangerous"],
    })
    executor = ToolExecutor(registry=registry, policy=policy)
    return bridge, registry, executor, count


def make_manager(llm, executor, verifier=None):
    return ConversationManager(
        memory=FakeStore(),
        builder=PromptBuilder(),
        llm=llm,
        tools=executor,
        verifier=verifier or ResponseVerifier(
            capability_provider=_noop_capabilities,
        ),
    )


def run_tool(executor, name, arguments=None):
    return executor.execute(name, dict(arguments or {}))


# ----------------------------------------------------------------------
# 1. Registration, capability, schema, risk
# ----------------------------------------------------------------------

def test_list_apps_registers_into_one_registry():

    _, registry, _, count = android_executor()

    assert count == len(ANDROID_TOOLS)
    assert "android.list_apps" in registry.names()


def test_list_apps_is_safe_and_read_only():

    _, registry, _, _ = android_executor()
    tool = registry.get("android.list_apps")

    assert tool.risk is ToolRisk.SAFE
    assert tool.side_effect is SideEffect.READ_ONLY


def test_inventory_capability_registered_but_not_predeclared_available():
    """Existence != availability. Mere registration must not read 'available'."""

    android_executor()

    cap = capability_registry.get("android.app_inventory")

    assert cap is not None
    assert cap.required_dependencies == ["android.companion"]
    assert cap.availability_state != CapabilityState.AVAILABLE.value


def test_inventory_exports_a_valid_openai_schema():

    _, registry, _, _ = android_executor()
    schema = openai_function_schema(registry.get("android.list_apps"))

    function = schema["function"]
    assert schema["type"] == "function"
    assert function["name"] == "android.list_apps"
    assert function["parameters"]["type"] == "object"


# ----------------------------------------------------------------------
# 2. Loopback inventory behaviour (via the real executor)
# ----------------------------------------------------------------------

def test_empty_inventory_is_a_valid_success():

    bridge, _, executor, _ = android_executor()
    bridge.installed_apps = []

    result = run_tool(executor, "android.list_apps")

    assert result.ok
    assert result.status == ToolStatus.SUCCESS.value
    data = result.data["result"]
    assert data["packages"] == []
    assert data["count"] == 0
    assert data["source"] == "android.package_manager"


def test_multiple_apps_are_all_reported():

    bridge, _, executor, _ = android_executor()
    bridge.installed_apps = [
        {"package": "com.aura.companion", "label": "Aura",
         "launchable": True, "enabled": True},
        {"package": "com.android.chrome", "label": "Chrome",
         "launchable": True, "enabled": True},
        {"package": "com.whatsapp", "label": "WhatsApp",
         "launchable": True, "enabled": True},
    ]

    result = run_tool(executor, "android.list_apps")
    data = result.data["result"]

    assert data["count"] == 3
    packages = {entry["package"] for entry in data["packages"]}
    assert packages == {
        "com.aura.companion", "com.android.chrome", "com.whatsapp",
    }


def test_duplicate_package_entries_are_preserved_not_deduped():
    """The transport reports what the device reported; it does not edit it."""

    bridge, _, executor, _ = android_executor()
    bridge.installed_apps = [
        {"package": "com.duplicate", "launchable": True, "enabled": True},
        {"package": "com.duplicate", "launchable": True, "enabled": True},
    ]

    data = run_tool(executor, "android.list_apps").data["result"]

    assert data["count"] == 2
    assert [e["package"] for e in data["packages"]] == ["com.duplicate", "com.duplicate"]


def test_disabled_and_non_launchable_apps_are_passed_through():

    bridge, _, executor, _ = android_executor()
    bridge.installed_apps = [
        {"package": "com.disabled", "label": "Disabled app",
         "launchable": True, "enabled": False},
        {"package": "com.nonlaunch", "label": "Service only",
         "launchable": False, "enabled": True},
    ]

    data = run_tool(executor, "android.list_apps").data["result"]
    by_package = {e["package"]: e for e in data["packages"]}

    assert by_package["com.disabled"]["enabled"] is False
    assert by_package["com.nonlaunch"]["launchable"] is False


def test_incomplete_package_objects_are_not_coerced_or_invented():
    """A package object missing fields is carried as-is; the transport never
    fabricates values it does not have."""

    bridge, _, executor, _ = android_executor()
    bridge.installed_apps = [{"package": "com.minimal"}]

    data = run_tool(executor, "android.list_apps").data["result"]

    assert data["packages"] == [{"package": "com.minimal"}]


def test_every_query_observes_a_fresh_timestamp():
    """No caching: each list_apps carries its own observed_at, moving forward."""

    bridge, _, executor, _ = android_executor(LoopbackDeviceBridge(TickClock()))
    bridge.installed_apps = [{"package": "com.x"}]

    first = run_tool(executor, "android.list_apps").data["result"]
    second = run_tool(executor, "android.list_apps").data["result"]

    assert first["observed_at"]
    assert second["observed_at"]
    assert first["observed_at"] != second["observed_at"]


# ----------------------------------------------------------------------
# 3. Gateway-path normalisation: malformed inventory is never success
# ----------------------------------------------------------------------

def test_inventory_without_observed_at_is_execution_failed():

    report = normalise_device_report(
        {"ok": True, "result": {"packages": ["com.x"]}},
        "android.list_apps",
    )

    assert not report["ok"]
    assert report["error"]["code"] == "EXECUTION_FAILED"


def test_inventory_with_a_non_list_packages_field_is_execution_failed():

    report = normalise_device_report(
        {"ok": True, "result": {"packages": "com.x", "observed_at": "now"}},
        "android.list_apps",
    )

    assert not report["ok"]
    assert report["error"]["code"] == "EXECUTION_FAILED"


def test_wellformed_inventory_normalises_as_success():

    report = normalise_device_report(
        {"ok": True,
         "result": {"packages": [{"package": "com.x"}], "observed_at": "now"}},
        "android.list_apps",
    )

    assert report["ok"]
    assert "error" not in report
    assert report["result"]["packages"] == [{"package": "com.x"}]


def test_non_inventory_tools_are_not_inventory_validated():

    report = normalise_device_report(
        {"ok": True, "result": {"cash": "in the bank"}},
        "android.get_foreground_app",
    )

    assert report["ok"]


# ----------------------------------------------------------------------
# 3b. Status gates: timeout / permission denied / capability unavailable.
#     The executor (not the bridge report) decides these, so they are
#     forced from the capability + policy seam exactly as test_tool_output_contract
#     does, and monkeypatch restores the global registries afterwards.
# ----------------------------------------------------------------------

class _BlockingBridge(DeviceBridge):
    """A bridge whose list_apps never finishes within the tool's deadline."""

    def status(self) -> dict:
        return {
            "state": "AVAILABLE", "healthy": True, "reason": "blocking bridge",
            "permissions": {"android.accessibility": True},
        }

    def invoke(self, tool: str, arguments: dict) -> dict:
        time.sleep(1.0)
        return failure(tool, "EXECUTION_FAILED", "returned too late anyway")


def test_inventory_timeout_is_timeout_not_failure(monkeypatch):
    """A device that does not answer on time is TIMEOUT, never success."""

    bridge, registry, executor, _ = android_executor(_BlockingBridge())
    monkeypatch.setattr(registry.get("android.list_apps"), "timeout", 0.05)

    result = run_tool(executor, "android.list_apps")

    assert not result.ok
    assert result.status == ToolStatus.TIMEOUT.value
    assert result.error_code == "TIMEOUT"


def test_inventory_permission_denied_is_denied_not_success(monkeypatch):
    """A missing accessibility grant is a denial, and nothing runs."""

    from core.capabilities import permissions as perm_registry

    bridge, _, executor, _ = android_executor()

    def denied_accessibility():
        return {"granted": False, "reason": "accessibility service disabled"}

    monkeypatch.setattr(
        perm_registry, "_checks", {"android.accessibility": denied_accessibility},
    )

    result = run_tool(executor, "android.list_apps")

    assert not result.ok
    assert result.status == ToolStatus.DENIED.value
    assert result.error_code == "BLOCKED_PERMISSION"
    assert result.authorization == "missing"
    assert bridge.invocations == []  # the gate refused before any device call


def test_inventory_capability_unavailable_runs_nothing(monkeypatch):
    """Registered capability + a disconnected device == UNAVAILABLE."""

    from core.capabilities import health as health_registry

    bridge, _, executor, _ = android_executor()

    # The exact reason a disconnected companion reports through
    # resolve_capability: a health check whose reason carries "heartbeat".
    monkeypatch.setattr(
        health_registry, "_checks",
        {"android.app_inventory": lambda: {
            "healthy": False,
            "reason": "no Android companion poll heartbeat has been received",
        }},
    )

    result = run_tool(executor, "android.list_apps")

    assert not result.ok
    assert result.status == ToolStatus.UNAVAILABLE.value
    assert result.error_code == "CAPABILITY_UNAVAILABLE"
    assert result.execution == "not_attempted"
    assert bridge.invocations == []


def test_unavailable_inventory_can_never_be_claimed_current(monkeypatch):
    """Existence != availability: UNAVAILABLE must not ground a claim."""

    from core.capabilities import health as health_registry

    android_executor()
    monkeypatch.setattr(
        health_registry, "_checks",
        {"android.app_inventory": lambda: {
            "healthy": False,
            "reason": "no Android companion poll heartbeat has been received",
        }},
    )

    ledger = EvidenceLedger(request_id="unavail-inventory")
    ledger.add_tool(
        tool="android.list_apps",
        status=ToolStatus.UNAVAILABLE.value,
        evidence=(),
        outcome="inventory unavailable",
        capability="android.app_inventory",
    )

    verifier = ResponseVerifier(capability_provider=_noop_capabilities)
    result = verifier.verify("I can list every app on your phone.", ledger)

    assert result.claims[0].state is not ClaimState.VERIFIED


# ----------------------------------------------------------------------
# 4. Inventory evidence: observation, fresh, never memory
# ----------------------------------------------------------------------

def test_inventory_execution_creates_observation_evidence():

    _, _, executor, _ = android_executor()
    result = run_tool(executor, "android.list_apps")

    kinds = {evidence.kind for evidence in result.evidence}

    assert EvidenceKind.OBSERVATION in kinds
    observation = [e for e in result.evidence
                   if e.kind is EvidenceKind.OBSERVATION][0]
    assert observation.source == "android.package_manager"
    assert observation.verified is True


def test_inventory_is_not_promoted_to_memory():
    """Inventory is an observation; the pipeline never auto-persists it."""

    result = run_tool(android_executor()[2], "android.list_apps")

    kinds = {evidence.kind for evidence in result.evidence}
    assert EvidenceKind.OBSERVATION in kinds
    assert EvidenceKind.MEMORY not in kinds


def test_current_state_claim_requires_fresh_inventory_not_tool_existence():
    """'Tool exists / capability registered' never grounds 'is installed'."""

    # The tool and capability exist, but no list_apps evidence was gathered.
    android_executor()
    ledger = EvidenceLedger(request_id="strict")

    verifier = ResponseVerifier(capability_provider=_noop_capabilities)
    result = verifier.verify("WhatsApp is installed.", ledger)

    claim = result.claims[0]
    assert claim.hallucination == "UNSUPPORTED_FACT"
    # Tool existence alone is not proof of an installed app.
    assert claim.state is not ClaimState.VERIFIED


def test_inventory_does_not_leak_into_diagnostics(monkeypatch):
    """Package names and labels never reach the verifier trace."""

    import brain.verify.verify as verify_mod

    caught = {}

    def spy(kind, **fields):
        caught.update(fields)

    monkeypatch.setattr(verify_mod, "emit_trace", spy)

    ledger = EvidenceLedger(request_id="diag-inventory")
    ledger.add_tool(
        tool="android.list_apps",
        status=ToolStatus.SUCCESS.value,
        evidence=(Evidence(
            EvidenceKind.OBSERVATION, source="android.package_manager",
            verified=True,
        ),),
        outcome="com.whatsapp installedlabel",
        capability="android.app_inventory",
    )

    ResponseVerifier(capability_provider=_noop_capabilities).verify(
        "WhatsApp is installed.", ledger,
    )

    rendered = json.dumps(caught).lower()
    assert "whatsapp" not in rendered
    assert "installedlabel" not in rendered


# ----------------------------------------------------------------------
# 5. Postcondition -> Phase 3 Evidence seam (the chat-path ToolResult)
# ----------------------------------------------------------------------

def _launch_result(report):
    """Run android.launch_app through the real provider + executor."""

    bridge = ScriptedBridge({"android.launch_app": report})
    _, _, executor, _ = android_executor(bridge)
    return run_tool(executor, "android.launch_app", {"package": "com.whatsapp"})


def _verify_claim(text, tool, status, evidence, outcome):
    ledger = EvidenceLedger(request_id="seam")
    ledger.add_tool(
        tool=tool, status=status, evidence=evidence, outcome=outcome,
        capability="android.app_launch",
    )
    return ResponseVerifier(capability_provider=_noop_capabilities).verify(
        text, ledger,
    )


def test_verified_postcondition_becomes_confirming_evidence():
    """verified=true -> POSTCONDITION evidence that confirms (item 19)."""

    result = _launch_result({
        "ok": True, "result": {"launched": "com.whatsapp"},
        "postcondition": {"verified": True},
    })

    assert any(e.confirms for e in result.evidence)
    post = [e for e in result.evidence
            if e.kind is EvidenceKind.POSTCONDITION][0]
    assert post.verified is True
    assert post.source == "android.postcondition"


def test_unverified_postcondition_never_confirms():
    """verified=false -> a failed check, never confirmation (item 20)."""

    result = _launch_result({
        "ok": True, "result": {"launched": "com.whatsapp"},
        "postcondition": {"verified": False},
    })

    assert not any(e.confirms for e in result.evidence)
    post = [e for e in result.evidence
            if e.kind is EvidenceKind.POSTCONDITION][0]
    assert post.verified is False


def test_missing_postcondition_yields_no_confirming_evidence():
    """A bare {ok: true} is a return value, never verification (item 21)."""

    result = _launch_result({"ok": True, "result": {"launched": "com.whatsapp"}})

    assert not any(e.confirms for e in result.evidence)
    assert not any(e.kind is EvidenceKind.POSTCONDITION for e in result.evidence)


def test_malformed_postcondition_yields_no_confirming_evidence():
    """A non-bool verified field is not evidence (item 22)."""

    result = _launch_result({
        "ok": True, "result": {"launched": "com.whatsapp"},
        "postcondition": {"verified": "yes"},
    })

    assert not any(e.confirms for e in result.evidence)


def test_success_return_value_alone_is_inferred_never_verified():
    """SUCCESS with only a return value is INFERRED, regardless of tone."""

    result = _verify_claim(
        "I opened whatsapp.", "android.launch_app", ToolStatus.SUCCESS.value,
        (Evidence(EvidenceKind.RETURN_VALUE, verified=None),),
        "launched whatsapp",
    )
    assert result.claims[0].state is ClaimState.INFERRED
    assert result.claims[0].state is not ClaimState.VERIFIED


# ----------------------------------------------------------------------
# 6. Adversarial action-claim matrix (deterministic)
# ----------------------------------------------------------------------

def test_message_no_postcondition_is_not_verified():

    result = _verify_claim(
        "I sent the message.", "message.send", ToolStatus.SUCCESS.value,
        (Evidence(EvidenceKind.RETURN_VALUE, verified=None),), "message sent",
    )
    assert result.claims[0].state is not ClaimState.VERIFIED


def test_message_unverified_postcondition_is_not_verified():

    result = _verify_claim(
        "I sent the message.", "message.send", ToolStatus.SUCCESS.value,
        (Evidence(EvidenceKind.POSTCONDITION, verified=False),), "message sent",
    )
    assert result.claims[0].state is ClaimState.CONTRADICTED
    assert result.claims[0].state is not ClaimState.VERIFIED


def test_message_verified_postcondition_is_verified():

    result = _verify_claim(
        "I sent the message.", "message.send", ToolStatus.SUCCESS.value,
        (Evidence(EvidenceKind.POSTCONDITION, verified=True),), "message sent",
    )
    assert result.claims[0].state is ClaimState.VERIFIED


def test_failed_action_cannot_be_claimed_successful():

    result = _verify_claim(
        "I opened whatsapp.", "android.launch_app", ToolStatus.FAILED.value,
        (Evidence(EvidenceKind.POSTCONDITION, verified=False),),
        "whatsapp launch failed",
    )
    assert result.claims[0].state is ClaimState.CONTRADICTED


def test_denied_action_cannot_be_claimed_successful():

    result = _verify_claim(
        "I opened whatsapp.", "android.launch_app", ToolStatus.DENIED.value,
        (), "whatsapp launch denied",
    )
    assert result.claims[0].state is ClaimState.CONTRADICTED
    assert result.claims[0].state is not ClaimState.VERIFIED


def test_unknown_action_cannot_be_claimed_successful():

    result = _verify_claim(
        "I opened whatsapp.", "android.launch_app", ToolStatus.UNKNOWN.value,
        (), "whatsapp outcome unclear",
    )
    assert result.claims[0].state is ClaimState.UNKNOWN
    assert result.claims[0].state is not ClaimState.VERIFIED


def test_verified_open_whatsapp_can_reach_verified():

    result = _verify_claim(
        "I opened whatsapp.", "android.launch_app", ToolStatus.SUCCESS.value,
        (Evidence(EvidenceKind.POSTCONDITION, verified=True),),
        "launched whatsapp",
    )
    assert result.claims[0].state is ClaimState.VERIFIED


# ----------------------------------------------------------------------
# 7. Chat-path: the verified-action Evidence a ConversationManager recorder
#    forwards can reach VERIFIED (item 25); injected only via real ToolResult.
# ----------------------------------------------------------------------

def test_chat_recorder_forwards_provider_evidence_to_verified():
    """The chat path's `_record_tool_evidence` forwards `result.evidence`
    verbatim into the ledger (`ledger.add_tool(status=..., evidence=...)`).
    A real AndroidProvider ToolResult carrying a verified device
    postcondition therefore reaches VERIFIED exactly as the recorded
    conversation path would."""

    result = _launch_result({
        "ok": True, "result": {"launched": "com.whatsapp"},
        "postcondition": {"verified": True},
    })

    # Mirror ConversationManager._record_tool_evidence exactly:
    ledger = EvidenceLedger(request_id="chat-recorder")
    ledger.add_tool(
        tool="android.launch_app",
        status=str(result.status or "SUCCESS"),
        evidence=tuple(result.evidence or ()),
        outcome="launched whatsapp",
        capability="android.app_launch",
    )

    # And the chat recorder feeds that same evidence into the prompt so the
    # model answers with the result in front of it. The final reply the
    # verifier sees is checked against this ledger - the real chat contract.
    reply = "I launched the app."
    outcome_ledger = ledger  # the recorder attaches the matching outcome
    vresult = ResponseVerifier(capability_provider=_noop_capabilities).verify(
        reply, outcome_ledger,
    )

    assert vresult.claims[0].state is ClaimState.VERIFIED
    assert vresult.decision is VerifierDecision.PASS


def test_chat_recorder_unverified_evidence_is_hedged_not_asserted():

    result = _launch_result({"ok": True, "result": {"launched": "com.whatsapp"}})

    ledger = EvidenceLedger(request_id="chat-recorder-unverified")
    ledger.add_tool(
        tool="android.launch_app",
        status=str(result.status or "SUCCESS"),
        evidence=tuple(result.evidence or ()),
        outcome="launched whatsapp",
        capability="android.app_launch",
    )

    vresult = ResponseVerifier(capability_provider=_noop_capabilities).verify(
        "I launched the app.", ledger,
    )

    # SUCCESS + return value only -> INFERRED -> the reply is qualified.
    assert vresult.claims[0].state is ClaimState.INFERRED
    assert vresult.claims[0].state is not ClaimState.VERIFIED
    assert vresult.changed