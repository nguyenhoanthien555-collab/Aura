"""
Voice tests.

Nothing here touches a microphone, a speaker, a model or a network.
MockMicrophone plays a scripted list of audio chunks, MockSTTProvider
returns scripted transcripts, and MockTTSProvider records what it was
asked to say.

The point of the mocks is not convenience - it is that the engines own
no audio code at all. If these tests can drive the whole voice path with
two in-memory doubles, then the real providers really are replaceable.
"""

import pytest

from events.bus import EventBus
from events.types import ListeningEvent, ResponseEvent, SpeakingEvent, TranscriptEvent

from voice.stt.engine import KeywordWakeWord, SpeechToTextEngine
from voice.stt.microphone import MockMicrophone
from voice.stt.provider import AudioChunk
from voice.stt.providers.mock import MockSTTProvider

from voice.tts.engine import TTSEngine
from voice.tts.provider import is_provider_available
from voice.tts.providers.mock import MockTTSProvider

from voice.factory import create_microphone, create_stt_provider, create_tts_provider


@pytest.fixture
def bus():
    return EventBus()


def spoken_audio(marker: bytes = b"\x01\x02") -> AudioChunk:
    """A chunk that is distinguishable from silence."""

    return AudioChunk(data=marker * 100)


# ----------------------------------------------------------------------
# AudioChunk
# ----------------------------------------------------------------------

def test_audio_chunk_duration():
    # 16000 frames of 16 bit mono at 16 kHz is exactly one second.
    chunk = AudioChunk(data=b"\x00\x00" * 16000)

    assert chunk.duration == pytest.approx(1.0)


def test_silence_is_empty_of_sound_but_not_of_data():
    chunk = AudioChunk.silence(0.5)

    assert chunk.duration == pytest.approx(0.5)
    assert not chunk.is_empty()


def test_empty_chunk_reports_empty():
    assert AudioChunk(data=b"").is_empty()


# ----------------------------------------------------------------------
# Microphone
# ----------------------------------------------------------------------

def test_mock_microphone_plays_its_script_then_returns_silence():
    first = spoken_audio()
    microphone = MockMicrophone(chunks=[first])

    assert microphone.record(1.0) is first
    assert microphone.record(1.0).is_empty() is False      # silence, not None
    assert microphone.calls == [1.0, 1.0]


def test_mock_microphone_can_report_itself_unavailable():
    assert MockMicrophone(available=False).is_available() is False


# ----------------------------------------------------------------------
# Speech to text engine
# ----------------------------------------------------------------------

def test_listen_once_returns_the_transcript(bus):
    engine = SpeechToTextEngine(
        provider=MockSTTProvider(transcripts=["what time is it"]),
        microphone=MockMicrophone(chunks=[spoken_audio()]),
        events=bus,
    )

    assert engine.listen_once() == "what time is it"


def test_listen_once_passes_recorded_audio_to_the_provider():
    audio = spoken_audio()
    provider = MockSTTProvider(transcripts=["hello"])

    engine = SpeechToTextEngine(
        provider=provider,
        microphone=MockMicrophone(chunks=[audio]),
    )

    engine.listen_once()

    assert provider.received == [audio]


def test_listen_once_publishes_listening_start_and_stop(bus):
    seen = []
    bus.subscribe(ListeningEvent, seen.append)

    engine = SpeechToTextEngine(
        provider=MockSTTProvider(transcripts=["hi"]),
        microphone=MockMicrophone(chunks=[spoken_audio()]),
        events=bus,
    )

    engine.listen_once()

    assert [event.active for event in seen] == [True, False]


def test_listen_once_publishes_the_transcript(bus):
    seen = []
    bus.subscribe(TranscriptEvent, seen.append)

    engine = SpeechToTextEngine(
        provider=MockSTTProvider(transcripts=["hello there"]),
        microphone=MockMicrophone(chunks=[spoken_audio()]),
        events=bus,
    )

    engine.listen_once()

    assert [event.text for event in seen] == ["hello there"]


def test_silence_produces_no_transcript_event(bus):
    seen = []
    bus.subscribe(TranscriptEvent, seen.append)

    engine = SpeechToTextEngine(
        provider=MockSTTProvider(),           # default "" for everything
        microphone=MockMicrophone(),
        events=bus,
    )

    assert engine.listen_once() == ""
    assert seen == []


def test_listening_stops_even_when_recording_fails(bus):
    """A yanked USB microphone must not leave the avatar listening."""

    class BrokenMicrophone:
        def record(self, seconds):
            raise OSError("device disappeared")

    seen = []
    bus.subscribe(ListeningEvent, seen.append)

    engine = SpeechToTextEngine(
        provider=MockSTTProvider(),
        microphone=BrokenMicrophone(),
        events=bus,
    )

    assert engine.listen_once() == ""
    assert [event.active for event in seen] == [True, False]


def test_provider_failure_yields_empty_transcript_not_an_exception():
    class BrokenProvider:
        def transcribe(self, audio):
            raise RuntimeError("model exploded")

    engine = SpeechToTextEngine(
        provider=BrokenProvider(),
        microphone=MockMicrophone(chunks=[spoken_audio()]),
    )

    assert engine.listen_once() == ""


def test_disabled_engine_does_not_record():
    microphone = MockMicrophone(chunks=[spoken_audio()])

    engine = SpeechToTextEngine(
        provider=MockSTTProvider(transcripts=["hi"]),
        microphone=microphone,
        enabled=False,
    )

    assert engine.listen_once() == ""
    assert microphone.calls == []


def test_engine_is_unavailable_when_the_microphone_is():
    engine = SpeechToTextEngine(
        provider=MockSTTProvider(),
        microphone=MockMicrophone(available=False),
    )

    assert engine.is_available() is False


def test_engine_is_available_with_a_working_pair():
    engine = SpeechToTextEngine(
        provider=MockSTTProvider(),
        microphone=MockMicrophone(),
    )

    assert engine.is_available() is True


# ----------------------------------------------------------------------
# Continuous listening
# ----------------------------------------------------------------------

def test_listen_continuous_stops_after_max_turns():
    """max_turns is what keeps this loop finite in a test."""

    heard = []

    engine = SpeechToTextEngine(
        provider=MockSTTProvider(transcripts=["one", "two", "three"]),
        microphone=MockMicrophone(),
    )

    forwarded = engine.listen_continuous(heard.append, max_turns=3)

    assert heard == ["one", "two", "three"]
    assert forwarded == 3


def test_listen_continuous_skips_silent_turns():
    heard = []

    engine = SpeechToTextEngine(
        provider=MockSTTProvider(transcripts=["one", "", "two"]),
        microphone=MockMicrophone(),
    )

    forwarded = engine.listen_continuous(heard.append, max_turns=3)

    assert heard == ["one", "two"]
    assert forwarded == 2


def test_listen_continuous_honours_should_continue():
    heard = []

    engine = SpeechToTextEngine(
        provider=MockSTTProvider(transcripts=["one", "two"]),
        microphone=MockMicrophone(),
    )

    engine.listen_continuous(
        heard.append,
        should_continue=lambda: len(heard) < 1,
        max_turns=10,
    )

    assert heard == ["one"]


def test_stop_ends_the_loop_from_inside_a_handler():
    heard = []

    engine = SpeechToTextEngine(
        provider=MockSTTProvider(transcripts=["one", "two", "three"]),
        microphone=MockMicrophone(),
    )

    def handle(text):
        heard.append(text)
        engine.stop()

    engine.listen_continuous(handle, max_turns=10)

    assert heard == ["one"]


def test_a_failing_handler_does_not_kill_the_microphone_loop():
    heard = []

    def handle(text):
        heard.append(text)
        raise RuntimeError("downstream blew up")

    engine = SpeechToTextEngine(
        provider=MockSTTProvider(transcripts=["one", "two"]),
        microphone=MockMicrophone(),
    )

    engine.listen_continuous(handle, max_turns=2)

    assert heard == ["one", "two"]


# ----------------------------------------------------------------------
# Wake word
# ----------------------------------------------------------------------

def test_wake_word_matches_case_insensitively():
    wake = KeywordWakeWord(["aura"])

    assert wake.matches("Aura, what time is it") is True
    assert wake.matches("what time is it") is False


def test_wake_word_strips_the_phrase():
    wake = KeywordWakeWord(["aura"])

    assert wake.strip("Aura, what time is it") == "what time is it"


def test_continuous_listening_forwards_only_addressed_utterances():
    heard = []

    engine = SpeechToTextEngine(
        provider=MockSTTProvider(
            transcripts=[
                "just talking to myself",
                "aura what time is it",
            ]
        ),
        microphone=MockMicrophone(),
        wake_word=KeywordWakeWord(["aura"]),
    )

    engine.listen_continuous(heard.append, max_turns=2)

    assert heard == ["what time is it"]


def test_no_wake_word_forwards_everything():
    heard = []

    engine = SpeechToTextEngine(
        provider=MockSTTProvider(transcripts=["anything at all"]),
        microphone=MockMicrophone(),
    )

    engine.listen_continuous(heard.append, max_turns=1)

    assert heard == ["anything at all"]


# ----------------------------------------------------------------------
# Text to speech engine
# ----------------------------------------------------------------------

def test_speak_reaches_the_provider():
    provider = MockTTSProvider()

    assert TTSEngine(provider=provider).speak("hello bro") is True
    assert provider.last == "hello bro"


def test_empty_text_is_not_spoken():
    provider = MockTTSProvider()

    assert TTSEngine(provider=provider).speak("   ") is False
    assert provider.spoken == []


def test_disabled_engine_stays_silent():
    provider = MockTTSProvider()

    assert TTSEngine(provider=provider, enabled=False).speak("hi") is False
    assert provider.spoken == []


def test_speak_publishes_start_and_stop(bus):
    seen = []
    bus.subscribe(SpeakingEvent, seen.append)

    TTSEngine(provider=MockTTSProvider(), events=bus).speak("hello")

    assert [event.active for event in seen] == [True, False]


def test_speaking_stops_even_when_the_provider_fails(bus):
    """Otherwise the avatar sticks in its speaking animation forever."""

    class BrokenProvider:
        def speak(self, text):
            raise RuntimeError("no audio device")

    seen = []
    bus.subscribe(SpeakingEvent, seen.append)

    engine = TTSEngine(provider=BrokenProvider(), events=bus)

    assert engine.speak("hello") is False
    assert [event.active for event in seen] == [True, False]


def test_speak_response_takes_anything_with_text():
    """voice/ must never need to import brain.response.Response."""

    class AnythingWithText:
        text = "duck typed reply"

    provider = MockTTSProvider()

    TTSEngine(provider=provider).speak_response(AnythingWithText())

    assert provider.last == "duck typed reply"


def test_attach_speaks_replies_that_appear_on_the_bus(bus):
    """
    Event inversion: the brain publishes, the voice listens. Nothing in
    brain/ ever calls speak().
    """

    provider = MockTTSProvider()

    TTSEngine(provider=provider, events=bus).attach(bus)

    bus.publish(ResponseEvent(text="here is your answer"))

    assert provider.spoken == ["here is your answer"]


def test_attach_returns_a_working_unsubscribe(bus):
    provider = MockTTSProvider()

    detach = TTSEngine(provider=provider, events=bus).attach(bus)

    bus.publish(ResponseEvent(text="one"))
    detach()
    bus.publish(ResponseEvent(text="two"))

    assert provider.spoken == ["one"]


def test_provider_without_is_available_is_assumed_available():
    class Bare:
        def speak(self, text):
            pass

    assert is_provider_available(Bare()) is True


# ----------------------------------------------------------------------
# Factory
# ----------------------------------------------------------------------

def test_stt_auto_does_not_reach_for_whisper():
    """Auto must not trigger a model download. Auto means mock."""

    from voice.stt.providers.mock import MockSTTProvider as Mock

    assert isinstance(create_stt_provider("auto"), Mock)


def test_unknown_stt_provider_falls_back_to_mock():
    from voice.stt.providers.mock import MockSTTProvider as Mock

    assert isinstance(create_stt_provider("does-not-exist"), Mock)


def test_unknown_tts_provider_falls_back_to_mock():
    assert isinstance(create_tts_provider("does-not-exist"), MockTTSProvider)


def test_tts_mock_can_be_requested_by_name():
    assert isinstance(create_tts_provider("mock"), MockTTSProvider)


def test_microphone_factory_can_be_forced_to_mock():
    assert isinstance(create_microphone({"mock": True}), MockMicrophone)


def test_microphone_factory_always_returns_something_recordable():
    """
    On a machine with no input device this returns a MockMicrophone that
    reports itself unavailable - never None, so callers need no guard.
    """

    microphone = create_microphone({})

    assert hasattr(microphone, "record")
    assert hasattr(microphone, "is_available")
