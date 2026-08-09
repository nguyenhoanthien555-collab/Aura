"""
Tests for AuraCLI voice interaction flow.
"""

from unittest.mock import MagicMock
import pytest

from events.bus import EventBus
from events.types import ResponseEvent
from launcher.cli import AuraCLI
from voice.stt.microphone import MockMicrophone
from voice.stt.provider import AudioChunk
from voice.stt.providers.mock import MockSTTProvider
from voice.stt.engine import SpeechToTextEngine
from voice.tts.engine import TTSEngine
from voice.tts.providers.mock import MockTTSProvider


class FakeRuntime:
    """Mock runtime for testing CLI voice flow."""

    def __init__(self, stt=None, tts=None, bus=None, reply="Hello from LLM"):
        self.bus = bus or EventBus()
        self.config = {"app": {"name": "Aura"}}
        
        mock_engine = MagicMock()
        mock_response = MagicMock()
        mock_response.text = reply
        mock_engine.chat.side_effect = self._on_chat

        self.services = MagicMock()
        self.services.stt = stt
        self.services.tts = tts
        self.services.engine = mock_engine
        self.services.avatar = None
        self.services.tools = None

        self.reply = reply
        self.chat_calls = []

    def set_tool_confirmation(self, confirm):
        pass

    def listen(self) -> str:
        if self.services.stt is None:
            return ""
        return self.services.stt.listen_once()

    def chat(self, text: str, source: str = "text"):
        self.chat_calls.append((text, source))
        self.bus.publish(ResponseEvent(text=self.reply))
        res = MagicMock()
        res.text = self.reply
        return res

    def _on_chat(self, text: str, source: str = "text"):
        return self.chat(text, source=source)


def test_cli_voice_command_success():
    """Test that /voice records once, transcribes, chats, and triggers TTS."""
    bus = EventBus()
    tts_provider = MockTTSProvider()
    tts_engine = TTSEngine(provider=tts_provider, events=bus)
    tts_engine.attach(bus)

    stt_provider = MockSTTProvider(transcripts=["hello Aura"])
    mic = MockMicrophone(chunks=[AudioChunk(data=b"\x01\x02" * 50)])
    stt_engine = SpeechToTextEngine(provider=stt_provider, microphone=mic, events=bus)

    runtime = FakeRuntime(stt=stt_engine, tts=tts_engine, bus=bus, reply="Hi there!")
    cli = AuraCLI(runtime)

    # Invoke /voice command
    cli._command("/voice")

    # Assert runtime.chat was called with "hello Aura" and source="voice"
    assert runtime.chat_calls == [("hello Aura", "voice")]
    # Assert TTS spoke the LLM reply
    assert tts_provider.spoken == ["Hi there!"]
    # Assert microphone recorded only once
    assert mic.calls == [5.0]


def test_cli_voice_command_nothing_heard():
    """Test /voice when microphone returns silence/empty transcript."""
    bus = EventBus()
    stt_provider = MockSTTProvider(default="")
    mic = MockMicrophone()
    stt_engine = SpeechToTextEngine(provider=stt_provider, microphone=mic, events=bus)

    runtime = FakeRuntime(stt=stt_engine, bus=bus)
    cli = AuraCLI(runtime)

    cli._command("/voice")

    # Chat should not be called when nothing is heard
    assert runtime.chat_calls == []


def test_cli_voice_command_disabled():
    """Test /voice when STT service is disabled (None)."""
    runtime = FakeRuntime(stt=None)
    cli = AuraCLI(runtime)

    cli._command("/voice")

    assert runtime.chat_calls == []


def test_cli_voice_command_unavailable():
    """Test /voice when STT service is marked unavailable."""
    bus = EventBus()
    stt_provider = MockSTTProvider()
    mic = MockMicrophone(available=False)
    stt_engine = SpeechToTextEngine(provider=stt_provider, microphone=mic, events=bus)

    runtime = FakeRuntime(stt=stt_engine, bus=bus)
    cli = AuraCLI(runtime)

    cli._command("/voice")

    assert runtime.chat_calls == []
