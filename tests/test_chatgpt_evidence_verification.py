"""
Integration tests for ChatGPT Claim -> Evidence verification and truthfulness.

Verifies:
1. Verified postconditions keep the positive claim (VERIFIED).
2. Failed/unverified postconditions are repaired or hedged (INFERRED / CONTRADICTED).
3. Android app inventory observation grounds app existence claims.
4. Unverified claims change the text to an honest qualification.
"""

from brain.verify import ResponseVerifier
from brain.verify.ledger import EvidenceLedger
from brain.verify.status import ClaimState, VerifierDecision
from tools.outcome import Evidence, EvidenceKind, ToolStatus


def _noop_capabilities(words):
    return "UNKNOWN", False, ""


def test_chatgpt_claim_verified_by_android_postcondition():
    """When Android tool returns verified: true, the claim passes as VERIFIED."""
    verifier = ResponseVerifier(capability_provider=_noop_capabilities, repair=True)
    ledger = EvidenceLedger(request_id="req_1")

    # Simulate android.launch_app with verified postcondition
    ledger.add_tool(
        tool="android.launch_app",
        status=ToolStatus.SUCCESS.value,
        evidence=(
            Evidence(
                kind=EvidenceKind.POSTCONDITION,
                source="android.postcondition",
                verified=True,
                reference="android.launch_app",
                detail="device postcondition",
            ),
        ),
        outcome="com.google.android.youtube launched",
        capability="android.app_launch",
    )

    reply = "I launched the app."
    vresult = verifier.verify(reply, ledger)

    assert vresult.claims[0].state is ClaimState.VERIFIED
    assert vresult.decision is VerifierDecision.PASS
    assert not vresult.changed


def test_chatgpt_claim_hedged_when_postcondition_unverified():
    """When Android tool has no postcondition confirmation, claim is INFERRED and hedged."""
    verifier = ResponseVerifier(capability_provider=_noop_capabilities, repair=True)
    ledger = EvidenceLedger(request_id="req_2")

    # Tool returned ok, but without verified postcondition check
    ledger.add_tool(
        tool="android.launch_app",
        status=ToolStatus.SUCCESS.value,
        evidence=(),
        outcome="launch intent dispatched",
        capability="android.app_launch",
    )

    reply = "I launched the app."
    vresult = verifier.verify(reply, ledger)

    # Claim must be INFERRED and text must be qualified/hedged
    assert vresult.claims[0].state is ClaimState.INFERRED
    assert vresult.claims[0].state is not ClaimState.VERIFIED
    assert vresult.changed


def test_chatgpt_claim_repaired_when_tool_fails():
    """When tool execution failed (e.g. phone offline / UNAVAILABLE), claim is CONTRADICTED."""
    verifier = ResponseVerifier(capability_provider=_noop_capabilities, repair=True)
    ledger = EvidenceLedger(request_id="req_3")

    ledger.add_tool(
        tool="android.launch_app",
        status=ToolStatus.UNAVAILABLE.value,
        evidence=(),
        outcome="DEVICE_NOT_CONNECTED",
        capability="android.app_launch",
    )

    reply = "I launched the app."
    vresult = verifier.verify(reply, ledger)

    assert vresult.claims[0].state is ClaimState.CONTRADICTED
    assert vresult.decision is VerifierDecision.REPAIR
    assert vresult.changed


def test_chatgpt_app_inventory_evidence():
    """Test observation evidence from android.list_apps grounds device state."""
    ledger = EvidenceLedger(request_id="req_4")

    ledger.add_tool(
        tool="android.list_apps",
        status=ToolStatus.SUCCESS.value,
        evidence=(
            Evidence(
                kind=EvidenceKind.OBSERVATION,
                source="android.package_manager",
                verified=True,
                reference="android.list_apps",
                detail="app inventory observed on device",
            ),
        ),
        outcome='[{"package": "com.spotify.music", "label": "Spotify"}]',
        capability="android.app_inventory",
    )

    # A verified observation evidence is recorded in ledger
    ev_types = [e.kind for e in ledger.tools[0].evidence]
    assert EvidenceKind.OBSERVATION in ev_types
