"""
Tests for server voice TTS route (server/routes/voice.py).
"""

from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from server.config import settings
from server.main import app


@pytest.fixture
def auth_client():
    settings.auth_token = "test-voice-token"
    client = TestClient(app)
    yield client, {"Authorization": "Bearer test-voice-token"}


def test_voice_tts_empty_text_returns_400(auth_client):
    client, headers = auth_client
    response = client.get("/api/voice/tts?text=", headers=headers)
    assert response.status_code == 400
    assert "cannot be empty" in response.json()["detail"].lower()


def test_voice_tts_too_long_returns_400(auth_client):
    client, headers = auth_client
    long_text = "a" * 4001
    response = client.get(f"/api/voice/tts?text={long_text}", headers=headers)
    assert response.status_code == 400
    assert "too long" in response.json()["detail"].lower()


def test_voice_tts_synthesizes_mp3_success(auth_client):
    client, headers = auth_client

    async def mock_stream(self):
        yield {"type": "audio", "data": b"\xff\xfb\x90\x64fake_mp3_header_and_data"}

    with patch("edge_tts.Communicate.stream", new=mock_stream):
        response = client.get(
            "/api/voice/tts?text=Xin+chao+Aura&voice=zh-CN-XiaoxiaoNeural",
            headers=headers,
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/mpeg"
        assert b"fake_mp3" in response.content


def test_voice_tts_handles_edge_tts_failure(auth_client):
    client, headers = auth_client

    async def mock_failing_stream(self):
        raise RuntimeError("Service unavailable")
        yield {}

    with patch("edge_tts.Communicate.stream", new=mock_failing_stream):
        response = client.get("/api/voice/tts?text=Xin+chao+Aura", headers=headers)
        assert response.status_code == 500
        assert "tts synthesis error" in response.json()["detail"].lower()
