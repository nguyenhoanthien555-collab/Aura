"""
Tests for Phase 5 Android Personal Task Tools:
SMS, Calendar, and Contacts (interfaces, schemas, policy gates, mocks, evidence).
"""

import pytest

from core.capabilities import registry as cap_registry
from core.capabilities.factory import register_core_capabilities
from brain.verify import ResponseVerifier
from brain.verify.claims import extract_claims, ClaimType
from brain.verify.ledger import EvidenceLedger
from brain.verify.status import ClaimState, VerifierDecision
from tools.base import ToolRisk
from tools.executor import ToolExecutor, ToolPolicy
from tools.outcome import EvidenceKind, SideEffect, ToolStatus
from tools.providers.android_bridge import LoopbackDeviceBridge
from tools.providers.android_task_provider import (
    AndroidTaskProvider,
    SendSMS,
    ReadSMS,
    CreateCalendarEvent,
    ListCalendarEvents,
    SearchContacts,
)
from tools.registry import ToolRegistry
from tools.schema import openai_function_schema


EXPECTED_TASK_TOOLS = {
    "android.send_sms",
    "android.read_sms",
    "android.create_calendar_event",
    "android.list_calendar_events",
    "android.search_contacts",
    "android.set_alarm",
    "android.list_alarms",
    "android.cancel_alarm",
    "android.set_clipboard",
    "android.get_clipboard",
    "android.toggle_flashlight",
    "android.get_device_health",
}



def make_task_executor(bridge=None, allowed=None, auto_approve=None):
    bridge = bridge or LoopbackDeviceBridge()
    provider = AndroidTaskProvider(bridge)
    registry = ToolRegistry()
    count = provider.register_into(registry)

    policy = ToolPolicy.from_config({
        "enabled": True,
        "allowed": sorted(allowed or EXPECTED_TASK_TOOLS),
        "auto_approve": auto_approve if auto_approve is not None else ["safe", "sensitive", "dangerous"],
    })

    executor = ToolExecutor(registry=registry, policy=policy)
    return bridge, registry, executor, count


# ---------------------------------------------------------------------------
# Registration and Schema Tests
# ---------------------------------------------------------------------------

def test_task_provider_registers_expected_tools():
    _, registry, _, count = make_task_executor()

    assert count == len(EXPECTED_TASK_TOOLS)
    assert set(registry.names()) == EXPECTED_TASK_TOOLS


def test_task_provider_without_bridge_registers_nothing():
    registry = ToolRegistry()
    provider = AndroidTaskProvider(None)
    assert not provider.available()
    assert provider.register_into(registry) == 0
    assert len(registry.names()) == 0


def test_task_tools_openai_function_schemas():
    _, registry, _, _ = make_task_executor()

    for name in EXPECTED_TASK_TOOLS:
        tool = registry.get(name)
        schema = openai_function_schema(tool)

        assert schema["type"] == "function"
        fn = schema["function"]
        assert fn["name"] == name
        assert isinstance(fn["description"], str) and fn["description"]
        assert fn["parameters"]["type"] == "object"


def test_task_tools_risk_and_side_effects():
    _, registry, _, _ = make_task_executor()

    send_sms = registry.get("android.send_sms")
    assert send_sms.risk == ToolRisk.DANGEROUS
    assert send_sms.side_effect == SideEffect.NON_IDEMPOTENT

    read_sms = registry.get("android.read_sms")
    assert read_sms.risk == ToolRisk.SAFE
    assert read_sms.side_effect == SideEffect.READ_ONLY

    create_cal = registry.get("android.create_calendar_event")
    assert create_cal.risk == ToolRisk.DANGEROUS
    assert create_cal.side_effect == SideEffect.NON_IDEMPOTENT

    list_cal = registry.get("android.list_calendar_events")
    assert list_cal.risk == ToolRisk.SAFE
    assert list_cal.side_effect == SideEffect.READ_ONLY

    search_contacts = registry.get("android.search_contacts")
    assert search_contacts.risk == ToolRisk.SAFE
    assert search_contacts.side_effect == SideEffect.READ_ONLY


# ---------------------------------------------------------------------------
# Execution and Evidence Tests
# ---------------------------------------------------------------------------

def test_send_sms_execution_and_postcondition_evidence():
    bridge, _, executor, _ = make_task_executor()

    res = executor.execute("android.send_sms", {"recipient": "+15551234567", "message": "Hello from AURA test"})

    assert res.ok is True
    assert res.status == ToolStatus.SUCCESS.value
    assert res.data["result"]["status"] == "sent"
    assert res.data["result"]["recipient"] == "+15551234567"

    # Verify postcondition evidence
    postconditions = [e for e in res.evidence if e.kind == EvidenceKind.POSTCONDITION]
    assert len(postconditions) == 1
    assert postconditions[0].verified is True
    assert postconditions[0].source == "android.postcondition"


def test_send_sms_empty_arguments_fails():
    _, _, executor, _ = make_task_executor()

    res = executor.execute("android.send_sms", {"recipient": "", "message": ""})
    assert res.ok is False
    assert res.error_code == "INVALID_ARGUMENTS"


def test_read_sms_execution_and_filter():
    bridge, _, executor, _ = make_task_executor()

    # Pre-populate SMS
    bridge.sms_messages.append({"recipient": "+19998887777", "message": "Meeting at 3pm", "status": "received"})

    res = executor.execute("android.read_sms", {"query": "Meeting"})
    assert res.ok is True
    assert res.data["result"]["count"] == 1
    assert res.data["result"]["messages"][0]["recipient"] == "+19998887777"


def test_create_calendar_event_execution_and_postcondition_evidence():
    bridge, _, executor, _ = make_task_executor()

    res = executor.execute("android.create_calendar_event", {
        "title": "Design Review",
        "start_time": "2026-10-05T14:00:00",
        "end_time": "2026-10-05T15:00:00",
        "description": "AURA Phase 5 roadmap review",
    })

    assert res.ok is True
    assert res.status == ToolStatus.SUCCESS.value
    assert res.data["result"]["event"]["title"] == "Design Review"

    postconditions = [e for e in res.evidence if e.kind == EvidenceKind.POSTCONDITION]
    assert len(postconditions) == 1
    assert postconditions[0].verified is True
    assert postconditions[0].reference == "android.create_calendar_event"


def test_create_calendar_event_missing_title_fails():
    _, _, executor, _ = make_task_executor()

    res = executor.execute("android.create_calendar_event", {"title": "", "start_time": "2026-10-05"})
    assert res.ok is False
    assert res.error_code == "INVALID_ARGUMENTS"


def test_list_calendar_events_filter():
    bridge, _, executor, _ = make_task_executor()

    res = executor.execute("android.list_calendar_events", {"start_date": "2026-10-01"})
    assert res.ok is True
    assert res.data["result"]["count"] >= 1
    assert res.data["result"]["events"][0]["title"] == "Team Standup"


def test_search_contacts_by_name_and_phone():
    bridge, _, executor, _ = make_task_executor()

    res1 = executor.execute("android.search_contacts", {"query": "Alice"})
    assert res1.ok is True
    assert res1.data["result"]["count"] == 1
    assert res1.data["result"]["contacts"][0]["name"] == "Alice Smith"

    res2 = executor.execute("android.search_contacts", {"query": "0987654321"})
    assert res2.ok is True
    assert res2.data["result"]["count"] == 1
    assert res2.data["result"]["contacts"][0]["name"] == "Bob Jones"


# ---------------------------------------------------------------------------
# Verifier Ledger Integration Tests
# ---------------------------------------------------------------------------

def test_task_tool_postcondition_binds_and_verifies_claim():
    bridge, _, executor, _ = make_task_executor()

    result = executor.execute("android.send_sms", {"recipient": "+15551234567", "message": "Arrived safely."})
    assert result.ok is True

    ledger = EvidenceLedger(request_id="req_test_1")
    ledger.add_tool(
        tool="android.send_sms",
        status=result.status,
        evidence=result.evidence,
        outcome=str(result.data.get("result", "")),
        capability="android.sms",
    )

    verifier = ResponseVerifier()
    res = verifier.verify("I sent the SMS message to +15551234567.", ledger)
    assert res.decision == VerifierDecision.PASS
    assert len(res.claims) >= 1
    assert res.claims[0].state == ClaimState.VERIFIED


def test_task_capabilities_registered_in_factory():
    register_core_capabilities()

    sms_cap = cap_registry.get("android.sms")
    assert sms_cap is not None
    assert "android.permission.SEND_SMS" in sms_cap.required_permissions

    cal_cap = cap_registry.get("android.calendar")
    assert cal_cap is not None
    assert "android.permission.READ_CALENDAR" in cal_cap.required_permissions

    contacts_cap = cap_registry.get("android.contacts")
    assert contacts_cap is not None
    assert "android.permission.READ_CONTACTS" in contacts_cap.required_permissions

    flashlight_cap = cap_registry.get("android.flashlight")
    assert flashlight_cap is not None

    health_cap = cap_registry.get("android.device_health")
    assert health_cap is not None


def test_toggle_flashlight_execution():
    bridge, _, executor, _ = make_task_executor()

    result = executor.execute("android.toggle_flashlight", {"enabled": True})
    assert result.ok is True
    assert result.status == ToolStatus.SUCCESS
    assert result.side_effect == str(SideEffect.IDEMPOTENT)
    assert result.data["result"]["enabled"] is True
    assert result.data["result"]["status"] == "torch_on"
    assert result.evidence[0].verified is True
    assert bridge.flashlight_enabled is True

    result_off = executor.execute("android.toggle_flashlight", {"enabled": False})
    assert result_off.ok is True
    assert result_off.data["result"]["enabled"] is False
    assert result_off.data["result"]["status"] == "torch_off"
    assert bridge.flashlight_enabled is False


def test_get_device_health_execution():
    _, _, executor, _ = make_task_executor()

    result = executor.execute("android.get_device_health", {})
    assert result.ok is True
    assert result.status == ToolStatus.SUCCESS
    assert result.side_effect == str(SideEffect.READ_ONLY)
    data = result.data["result"]
    assert "battery_level" in data
    assert "is_charging" in data
    assert "temperature_c" in data
    assert "memory_available_mb" in data
    assert "storage_free_gb" in data
    assert result.evidence[0].kind == EvidenceKind.RETURN_VALUE
