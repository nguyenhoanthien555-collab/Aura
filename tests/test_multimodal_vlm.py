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


def test_conversation_vision_context_from_user_attachment():
    """Test that ConversationManager recognizes user attached image as user_attachment vision source."""
    from unittest.mock import MagicMock
    from brain.conversation import ConversationManager

    cm = ConversationManager(llm=MagicMock(), memory=MagicMock(), builder=MagicMock())
    vis_ctx = cm._vision_context(context={"image": "fake_base64_data", "image_mime": "image/png"})

    assert vis_ctx is not None
    assert vis_ctx.source == "user_attachment"
    assert "User attached a camera photo or image" in vis_ctx.description


def test_gemini_provider_extracts_image_part(monkeypatch):
    """Test that GeminiProvider correctly parses base64 and builds image Part."""
    from brain.providers.gemini import GeminiProvider

    monkeypatch.setenv("GEMINI_API_KEY", "mock_key")
    provider = GeminiProvider(model="gemini-2.5-flash")
    raw_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    b64_str = base64.b64encode(raw_bytes).decode("ascii")

    part = provider._extract_image_part({"image": b64_str, "image_mime": "image/png"})
    assert part is not None
    assert part.inline_data.data == raw_bytes
    assert part.inline_data.mime_type == "image/png"

