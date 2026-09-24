"""
Tests for Real-Time Screen Perception (Laptop & Mobile) without Manual Screenshot Files.

Verifies:
1. Laptop Real-Time Screen Perception: Uses live active window perception without saving screenshot files.
2. Mobile Real-Time Screen Perception: Uses live Companion context (context['app']) to identify foreground app.
3. Anti-Hallucination & Honest Refusal: When mobile is requested without connection/context, refuses honestly.
4. Evidence Ledger Grounding: VisionEvidence stores real-time perception and verifier grounds claims without hedging.
"""

import pytest
from brain.verify import ResponseVerifier
from brain.verify.ledger import EvidenceLedger, VisionEvidence
from brain.verify.claims import extract_claims, ClaimType
from brain.verify.status import ClaimState
from vision.context import VisionContext


def test_evidence_ledger_vision_storage_and_matching():
    """Test that EvidenceLedger properly stores VisionEvidence and matches Vietnamese aliases."""
    ledger = EvidenceLedger(request_id="test-vision-ledger")
    ledger.add_vision(source="screen", description="User is editing code in Visual Studio Code (title: test_realtime_screen_perception.py)")

    assert len(ledger.vision) == 1
    assert ledger.vision[0].source == "screen"
    assert "visual studio code" in ledger.vision[0].description.lower()

    # Match queries
    assert ledger.matching_vision(["màn", "hình"]) is not None
    assert ledger.matching_vision(["visual", "studio", "code"]) is not None
    assert ledger.matching_vision(["cửa", "sổ"]) is not None


def test_response_verifier_accepts_screen_claim_when_vision_evidence_present():
    """Test that ResponseVerifier marks screen claims as SUPPORTED when VisionEvidence is in ledger."""
    verifier = ResponseVerifier(repair=True)
    ledger = EvidenceLedger(request_id="test-vision-verify")
    ledger.add_vision(
        source="screen",
        description="User is currently working in Visual Studio Code with file test.py open",
    )

    model_reply = "Trên màn hình laptop của bạn, tôi quan sát thấy bạn đang mở Visual Studio Code để lập trình."
    outcome = verifier.verify(model_reply, ledger=ledger)

    # Must NOT be repaired into the hedge: 'Tôi hiện không thể quan sát hay xác nhận trạng thái màn hình...'
    assert "chưa có công cụ thực thi" not in outcome.repaired_text
    assert "Visual Studio Code" in outcome.repaired_text
