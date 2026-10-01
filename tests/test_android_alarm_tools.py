"""
Dedicated tests for Android Cyber Alarm Tools:
android.set_alarm, android.list_alarms, and android.cancel_alarm.
"""

import pytest

from brain.verify import ResponseVerifier
from brain.verify.claims import extract_claims, ClaimType
from brain.verify.ledger import EvidenceLedger
from brain.verify.status import ClaimState, VerifierDecision
from core.capabilities import registry as cap_registry
from core.capabilities.factory import register_core_capabilities
from tools.base import ToolRisk
from tools.executor import ToolExecutor, ToolPolicy
from tools.outcome import EvidenceKind, SideEffect, ToolStatus
from tools.providers.android_bridge import LoopbackDeviceBridge
from tools.providers.android_provider import tool_result_from_report
from tools.providers.android_task_provider import (
    AndroidTaskProvider,
    SetAlarm,
    ListAlarms,
    CancelAlarm,
)
from tools.registry import ToolRegistry
from tools.schema import openai_function_schema


ALARM_TOOLS = {
    "android.set_alarm",
    "android.list_alarms",
    "android.cancel_alarm",
}


def make_alarm_executor(bridge=None):
    bridge = bridge or LoopbackDeviceBridge()
    provider = AndroidTaskProvider(bridge)
    registry = ToolRegistry()
    provider.register_into(registry)

    policy = ToolPolicy.from_config({
        "enabled": True,
        "allowed": list(ALARM_TOOLS),
        "auto_approve": ["safe"],
    })

    executor = ToolExecutor(registry=registry, policy=policy)
    return bridge, registry, executor


def test_alarm_tool_schemas_and_properties():
    bridge, registry, _ = make_alarm_executor()

    set_alarm = registry.get("android.set_alarm")
    assert set_alarm.risk == ToolRisk.SAFE
    assert set_alarm.side_effect == SideEffect.NON_IDEMPOTENT
    schema = openai_function_schema(set_alarm)
    params = schema["function"]["parameters"]["properties"]
    assert "hour" in params
    assert "minute" in params
    assert "label" in params
    assert "repeat_days" in params

    list_alarms = registry.get("android.list_alarms")
    assert list_alarms.risk == ToolRisk.SAFE
    assert list_alarms.side_effect == SideEffect.READ_ONLY

    cancel_alarm = registry.get("android.cancel_alarm")
    assert cancel_alarm.risk == ToolRisk.SAFE
    assert cancel_alarm.side_effect == SideEffect.NON_IDEMPOTENT


def test_set_alarm_execution():
    bridge, registry, executor = make_alarm_executor()

    result = executor.execute(
        "android.set_alarm",
        {"hour": 7, "minute": 30, "label": "Thức dậy đi làm", "repeat_days": [1, 2, 3, 4, 5]},
    )

    assert result.status == ToolStatus.SUCCESS.value
    assert result.ok is True
    data = result.data["result"]
    assert data["hour"] == 7
    assert data["minute"] == 30
    assert data["label"] == "Thức dậy đi làm"
    assert data["repeat_days"] == [1, 2, 3, 4, 5]

    postconditions = [e for e in result.evidence if e.kind == EvidenceKind.POSTCONDITION]
    assert len(postconditions) == 1
    assert postconditions[0].verified is True


def test_set_alarm_invalid_arguments():
    bridge, registry, executor = make_alarm_executor()

    result = executor.execute("android.set_alarm", {"hour": 25, "minute": 0})
    assert result.ok is False
    assert result.error_code == "INVALID_ARGUMENTS"

    result2 = executor.execute("android.set_alarm", {"hour": 7, "minute": 60})
    assert result2.ok is False
    assert result2.error_code == "INVALID_ARGUMENTS"


def test_list_alarms_execution():
    bridge, registry, executor = make_alarm_executor()

    # Pre-populate one extra alarm
    bridge.invoke("android.set_alarm", {"hour": 8, "minute": 0, "label": "Họp sáng"})

    result = executor.execute("android.list_alarms", {})
    assert result.status == ToolStatus.SUCCESS.value
    assert result.ok is True
    assert "alarms" in result.data["result"]
    assert result.data["result"]["count"] >= 2


def test_cancel_alarm_execution():
    bridge, registry, executor = make_alarm_executor()

    # Set alarm first
    report = bridge.invoke("android.set_alarm", {"hour": 9, "minute": 0, "label": "Tập gym"})
    alarm_id = report["result"]["id"]

    # Cancel it
    result = executor.execute("android.cancel_alarm", {"alarm_id": alarm_id})
    assert result.status == ToolStatus.SUCCESS.value
    assert result.ok is True
    assert result.data["result"]["cancelled"] is True

    postconditions = [e for e in result.evidence if e.kind == EvidenceKind.POSTCONDITION]
    assert len(postconditions) == 1
    assert postconditions[0].verified is True

    # Cancel again (already cancelled)
    result_second = executor.execute("android.cancel_alarm", {"alarm_id": alarm_id})
    assert result_second.status == ToolStatus.SUCCESS.value
    assert result_second.data["result"]["cancelled"] is False
    postconditions_second = [e for e in result_second.evidence if e.kind == EvidenceKind.POSTCONDITION]
    assert len(postconditions_second) == 1
    assert postconditions_second[0].verified is False


def test_cancel_alarm_missing_id():
    bridge, registry, executor = make_alarm_executor()
    result = executor.execute("android.cancel_alarm", {"alarm_id": ""})
    assert result.ok is False
    assert result.error_code == "INVALID_ARGUMENTS"


def test_alarm_evidence_ledger_reaches_verified():
    bridge, registry, executor = make_alarm_executor()
    result = executor.execute("android.set_alarm", {"hour": 6, "minute": 0, "label": "Chạy bộ"})
    assert result.ok is True

    ledger = EvidenceLedger(request_id="req_test_alarm_1")
    ledger.add_tool(
        tool="android.set_alarm",
        status=result.status,
        evidence=result.evidence,
        outcome=str(result.data.get("result", "")),
        capability="android.alarm",
    )

    verifier = ResponseVerifier()
    res = verifier.verify("I have set an alarm for 6:00 AM on your Android phone.", ledger)
    assert res.decision == VerifierDecision.PASS
    assert len(res.claims) >= 1
    assert res.claims[0].state == ClaimState.VERIFIED


def test_alarm_capability_registered_in_factory():
    register_core_capabilities()
    alarm_cap = cap_registry.get("android.alarm")
    assert alarm_cap is not None
    assert alarm_cap.category == "android"
    assert "android.companion" in alarm_cap.required_dependencies

