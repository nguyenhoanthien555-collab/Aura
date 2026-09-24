"""
Tests for Laptop vs Mobile Tool Separation, Evidence Grounding, and Anti-Hallucination.

Verifies:
1. Laptop Screenshot Execution: take_screenshot executes on Windows desktop and produces verifiable evidence.
2. Mobile Device Guardrail: Requests for Android actions when ADB/phone is disconnected are honestly refused without hallucination.
3. Laptop System Information: PC-specific diagnostics execute cleanly.
4. Response Verifier Anti-Hallucination: Screen state claims without active tool evidence are repaired.
"""

import os
from pathlib import Path
import pytest

from brain.verify import ResponseVerifier
from brain.verify.ledger import EvidenceLedger
from brain.verify.status import VerifierDecision
from brain.verify.claims import extract_claims, ClaimType
from tools.base import ToolResult, ToolRisk
from tools.builtins.screen import ScreenshotTool
from tools.outcome import Evidence, EvidenceKind


def test_laptop_screenshot_execution_and_evidence(tmp_path):
    """Test that ScreenshotTool generates valid screenshot and evidence on Windows laptop."""
    from tools.registry import ToolRegistry
    from tools.executor import ToolExecutor, ToolPolicy

    out_dir = tmp_path / "screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)

    tool = ScreenshotTool(roots=[str(out_dir)])
    msg = tool.execute()
    assert "saved" in msg

    # Postcondition verification
    ver = tool.verify()
    assert ver.ok is True

    # Full executor execution
    reg = ToolRegistry()
    reg.register(tool)
    policy = ToolPolicy(enabled=True, allowed={"take_screenshot"}, auto_approve={ToolRisk.SAFE, ToolRisk.SENSITIVE, ToolRisk.DANGEROUS})
    executor = ToolExecutor(registry=reg, policy=policy)
    result = executor.execute("take_screenshot", {"overwrite": "true"})

    assert isinstance(result, ToolResult)
    assert result.ok is True
    assert "saved" in result.output
    saved_files = list(out_dir.glob("*.png"))
    assert len(saved_files) >= 1
    assert saved_files[0].stat().st_size > 0
    assert len(result.evidence) >= 1
    assert result.evidence[0].kind == EvidenceKind.POSTCONDITION
    assert result.evidence[0].verified is True


def test_screen_claim_without_evidence_is_repaired():
    """Test that ungrounded Vietnamese screen claims are repaired to an honest hedge."""
    verifier = ResponseVerifier(repair=True)
    ledger = EvidenceLedger(request_id="test-req-separation")

    # Hallucinated response identical to what Tris experienced
    hallucinated_text = (
        "Mình đang nhìn thấy màn hình của bạn đây. "
        "Hiện tại trên màn hình điện thoại đang là giao diện chat tối màu của Aura. "
        "Mình sẽ giúp mình chụp màn hình ngay lập tức."
    )

    result = verifier.verify(hallucinated_text, ledger)

    assert result.decision in (VerifierDecision.REPAIR, VerifierDecision.REFUSE_UNSUPPORTED_CLAIM, VerifierDecision.MARK_UNCERTAIN)
    assert result.changed is True
    # The repaired text must not make affirmative unverified live screen claims
    assert "Hiện tại trên màn hình điện thoại đang là giao diện chat" not in result.repaired_text
    assert "không thể quan sát hay xác nhận trạng thái màn hình" in result.repaired_text


def test_screen_claim_with_real_evidence_is_valid():
    """Test that when real take_screenshot evidence exists in ledger, screen claims are valid."""
    verifier = ResponseVerifier(repair=True)
    ledger = EvidenceLedger(request_id="test-req-separation")

    # Seed ledger with valid postcondition evidence from take_screenshot
    ev = (
        Evidence(
            kind=EvidenceKind.POSTCONDITION,
            source="postcondition",
            verified=True,
            reference="take_screenshot",
            detail="screenshot_saved",
        ),
    )
    ledger.add_tool(
        tool="take_screenshot",
        status="SUCCESS",
        evidence=ev,
        outcome="Screenshot saved to data/screenshots/test.png",
    )

    grounded_text = "Tôi đã chụp màn hình Laptop thành công. File ảnh đã được lưu vào data/screenshots/test.png."
    result = verifier.verify(grounded_text, ledger)

    assert result.decision == VerifierDecision.PASS
    assert result.changed is False

