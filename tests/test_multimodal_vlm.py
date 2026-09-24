"""
Tests for Local Multimodal VLM Processor, Zero-File In-RAM Vision, and Verifier Grounding.
"""

import base64
import pytest

from vision.capture import Frame, GdiScreenCapture
from brain.verify import ResponseVerifier
from brain.verify.ledger import EvidenceLedger


def test_in_memory_screen_capture_creates_zero_files(tmp_path):
    """Test that GdiScreenCapture operates directly in RAM without touching the filesystem."""
    cap = GdiScreenCapture()
    if cap.is_available():
        frame = cap.capture()
        assert frame is not None
        assert frame.width > 0
        assert frame.height > 0
        assert len(frame.data) > 0
        assert frame.image_format == "rgb"

        # Verify no files were created in temporary directory
        assert list(tmp_path.iterdir()) == []


def test_response_verifier_accepts_vlm_evidence():
    """Test that ResponseVerifier grounds visual claims backed by screen_vlm evidence."""
    verifier = ResponseVerifier(repair=True)
    ledger = EvidenceLedger(request_id="test-vlm-verify")
    ledger.add_vision(
        source="screen_vlm",
        description="On screen: Visual Studio Code with active file test_multimodal_vlm.py and green test passing banner",
    )

    model_reply = "Trên màn hình laptop, tôi thấy giao diện Visual Studio Code với kết quả kiểm thử test_multimodal_vlm.py thành công."
    outcome = verifier.verify(model_reply, ledger=ledger)

    assert "chưa có công cụ thực thi" not in outcome.repaired_text
    assert "Visual Studio Code" in outcome.repaired_text
