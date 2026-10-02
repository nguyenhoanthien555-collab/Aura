"""
Tests for FallbackProvider.stream and neural voice endpoint.
"""

from typing import Iterator
import pytest
from brain.providers.base import BaseProvider
from brain.providers.errors import ProviderUnavailableError
from brain.providers.fallback import FallbackProvider
from brain.router import BrainRouter


class MockStreamingProvider(BaseProvider):
    provider_name = "mock_streaming"

    def __init__(self, pieces: list[str], should_fail: bool = False):
        self.pieces = pieces
        self.should_fail = should_fail

    def generate(self, prompt: str) -> str:
        if self.should_fail:
            raise ProviderUnavailableError("Generate failed")
        return "".join(self.pieces)

    def stream(self, prompt: str) -> Iterator[str]:
        if self.should_fail:
            raise ProviderUnavailableError("Stream failed to connect")
        for p in self.pieces:
            yield p


class MockGeneratingProvider(BaseProvider):
    provider_name = "mock_generating"

    def __init__(self, reply: str):
        self.reply = reply

    def generate(self, prompt: str) -> str:
        return self.reply


def test_fallback_provider_stream_success():
    p1 = MockStreamingProvider(["hello", " ", "world"])
    fb = FallbackProvider([p1], "mock_streaming")

    chunks = list(fb.stream("test"))
    assert "".join(chunks) == "hello world"
    assert fb.active_provider_name == "mock_streaming"
    assert fb.attempts == [("mock_streaming", "ok", "")]


def test_fallback_provider_stream_failover():
    p1 = MockStreamingProvider([], should_fail=True)
    p2 = MockStreamingProvider(["fallback", " ", "success"])
    fb = FallbackProvider([p1, p2], "mock_stream_1->mock_stream_2")

    chunks = list(fb.stream("test"))
    assert "".join(chunks) == "fallback success"
    assert fb.active_provider_name == "mock_streaming"
    assert len(fb.attempts) == 2
    assert fb.attempts[0][1] == "transient/unavailable"
    assert fb.attempts[1][1] == "ok"


def test_fallback_provider_stream_with_generate_only():
    p1 = MockStreamingProvider([], should_fail=True)
    p2 = MockGeneratingProvider("complete reply")
    fb = FallbackProvider([p1, p2], "mock_stream->mock_gen")

    chunks = list(fb.stream("test"))
    assert chunks == ["complete reply"]
    assert fb.active_provider_name == "mock_generating"


def test_brain_router_stream_fallback():
    router = BrainRouter(provider="custom")
    router._provider = MockGeneratingProvider("generated text")
    chunks = list(router.stream("hi"))
    assert chunks == ["generated text"]


def test_voice_tts_endpoint():
    from fastapi.testclient import TestClient
    from server.config import settings
    from server.main import app

    settings.auth_token = "test-token"
    client = TestClient(app)

    # 1. Reject without auth
    res = client.get("/api/voice/tts?text=test")
    assert res.status_code in (401, 403)

    # 2. Reject empty text
    headers = {"Authorization": "Bearer test-token"}
    res = client.get("/api/voice/tts?text=", headers=headers)
    assert res.status_code == 400


