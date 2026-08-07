"""
Speech cancellation tests.

Section 5's one genuinely missing capability: stopping an utterance that
is already in flight. Nothing here reaches the network, writes a
permanent file or makes a sound - the same two seams the rest of the Edge
tests use are enough to drive every path.

The interesting cases are the boundaries. Cancelling before synthesis,
cancelling between synthesis and playback, and cancelling when nothing is
speaking all have to behave differently, and only one of them is
obvious.
"""

import threading

from events.bus import EventBus
from events.types import (
    ResponseEvent,
    SpeakingEvent,
    StreamChunkEvent,
    StreamFinishedEvent,
    StreamStartedEvent,
)

from voice.tts.audio import NullAudioPlayer
from voice.tts.engine import TTSEngine
from voice.tts.provider import TTSProvider
from voice.tts.providers.edge import EdgeTTSProvider
from voice.tts.providers.mock import MockTTSProvider
from voice.tts.streaming import StreamingSpeaker


# ----------------------------------------------------------------------
# Doubles
# ----------------------------------------------------------------------

class FakeSynthesizer:
    """Records its arguments and writes a file, so cleanup is visible."""

    def __init__(self, write: bool = True):
        self.calls: list[dict] = []
        self.write = write

    def __call__(self, **kwargs) -> None:

        self.calls.append(kwargs)

        if self.write:
            with open(kwargs["path"], "wb") as handle:
                handle.write(b"fake mp3 bytes")

    @property
    def last(self) -> dict:
        return self.calls[-1]


class CancellingSynthesizer(FakeSynthesizer):
    """
    Cancels the provider from inside synthesis.

    Stands in for a cancel that arrives on another thread while the
    network round trip is still running - the case the checkpoint
    between synthesis and playback exists for, and the one that is
    otherwise a race to reproduce.
    """

    def __init__(self, provider_holder: list):
        super().__init__()
        self.holder = provider_holder

    def __call__(self, **kwargs) -> None:

        super().__call__(**kwargs)

        self.holder[0].cancel()


def provider_for(**kwargs) -> EdgeTTSProvider:
    """An Edge provider with both ends replaced."""

    kwargs.setdefault("synthesizer", FakeSynthesizer())
    kwargs.setdefault("player", NullAudioPlayer())

    return EdgeTTSProvider(**kwargs)


# ----------------------------------------------------------------------
# The provider
# ----------------------------------------------------------------------

def test_a_fresh_provider_is_not_cancelled():

    assert provider_for().cancelled is False


def test_cancel_sets_the_flag():

    provider = provider_for()

    provider.cancel()

    assert provider.cancelled is True


def test_cancel_before_speaking_does_not_mute_the_next_reply():
    """
    The failure mode that matters.

    A cancel that arrives while nothing is speaking must not leave the
    flag set, or Aura goes permanently silent - which is far worse than
    one extra spoken sentence.
    """

    synth = FakeSynthesizer()
    provider = provider_for(synthesizer=synth)

    provider.cancel()
    provider.speak("hey bro")

    assert len(synth.calls) == 1
    assert provider.cancelled is False


def test_cancel_during_synthesis_skips_playback():
    """
    Audio that arrived after the cancel is dropped rather than played.

    Synthesis is the long stage, so this is where a real cancel almost
    always lands.
    """

    holder: list = []
    player = NullAudioPlayer()

    provider = provider_for(
        synthesizer=CancellingSynthesizer(holder),
        player=player,
    )

    holder.append(provider)

    provider.speak("this reply is no longer wanted")

    assert player.played == []


def test_the_temporary_file_is_removed_when_cancelled():
    """Cleanup lives in a `finally`, so cancelling must not leak a file."""

    import os

    holder: list = []
    synth = CancellingSynthesizer(holder)

    provider = provider_for(synthesizer=synth)

    holder.append(provider)

    provider.speak("dropped")

    assert not os.path.exists(synth.last["path"])


def test_cancel_reaches_a_player_that_can_stop():

    player = NullAudioPlayer()
    provider = provider_for(player=player)

    provider.cancel()

    assert player.stopped == 1


def test_cancel_survives_a_player_that_cannot_stop():
    """
    A player with no `stop` is not an error.

    `stop` is optional by absence, exactly like `set_pacing`, so a user's
    own three line player keeps working.
    """

    class MinimalPlayer:
        def __init__(self):
            self.played: list[str] = []

        def play(self, path: str) -> None:
            self.played.append(path)

    provider = provider_for(player=MinimalPlayer())

    provider.cancel()

    assert provider.cancelled is True


def test_cancel_survives_a_player_whose_stop_raises():

    class AngryPlayer:
        def play(self, path: str) -> None:
            pass

        def stop(self) -> None:
            raise RuntimeError("device is gone")

    provider = provider_for(player=AngryPlayer())

    provider.cancel()

    assert provider.cancelled is True


def test_speaking_after_a_cancel_plays_again():
    """Cancelling one utterance must not disable the provider."""

    player = NullAudioPlayer()
    provider = provider_for(player=player)

    provider.speak("first")
    provider.cancel()
    provider.speak("second")

    assert len(player.played) == 2


def test_cancel_is_safe_from_another_thread():

    provider = provider_for()

    errors: list = []

    def cancel() -> None:
        try:
            provider.cancel()
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=cancel) for _ in range(4)]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join(5.0)

    assert errors == []
    assert provider.cancelled is True


def test_adding_cancel_did_not_widen_the_protocol():
    """
    The constraint the whole design turns on.

    TTSProvider is runtime_checkable. If `cancel` had been added to it,
    every provider without one - including a user's own - would fail
    isinstance overnight.
    """

    assert isinstance(MockTTSProvider(), TTSProvider)
    assert isinstance(provider_for(), TTSProvider)

    assert not hasattr(TTSProvider, "cancel")


# ----------------------------------------------------------------------
# The engine
# ----------------------------------------------------------------------

def test_the_engine_forwards_stop_to_the_provider():

    provider = provider_for()
    engine = TTSEngine(provider=provider)

    assert engine.stop() is True
    assert provider.cancelled is True


def test_the_engine_reports_a_provider_that_cannot_stop():
    """
    MockTTSProvider has no `cancel`. Saying so beats pretending.
    """

    engine = TTSEngine(provider=MockTTSProvider())

    assert engine.stop() is False


def test_the_engine_absorbs_a_provider_that_raises_on_cancel():

    class AngryProvider:
        def speak(self, text: str) -> None:
            pass

        def cancel(self) -> None:
            raise RuntimeError("nope")

    engine = TTSEngine(provider=AngryProvider())

    assert engine.stop() is False


def test_stopping_publishes_no_speaking_event():
    """
    The `speak` call being cancelled already emits active=False from its
    own `finally`. Emitting again here would either duplicate that or,
    when nothing is speaking, announce the end of speech that never
    started - and the avatar would leave its speaking state twice.
    """

    bus = EventBus()
    seen: list = []
    bus.subscribe(SpeakingEvent, seen.append)

    engine = TTSEngine(provider=provider_for(), events=bus)

    engine.stop()

    assert seen == []


def test_a_cancelled_reply_still_ends_the_speaking_state():
    """The avatar must never be stranded mid-animation."""

    bus = EventBus()
    seen: list = []
    bus.subscribe(SpeakingEvent, seen.append)

    holder: list = []

    provider = provider_for(synthesizer=CancellingSynthesizer(holder))
    holder.append(provider)

    TTSEngine(provider=provider, events=bus).attach(bus)

    bus.publish(ResponseEvent(text="a reply that gets cancelled"))

    assert seen[0].active is True
    assert seen[-1].active is False


# ----------------------------------------------------------------------
# The streaming speaker
# ----------------------------------------------------------------------

class RecordingEngine:
    """Stands in for TTSEngine. Records sentences and stop calls."""

    def __init__(self):
        self.spoken: list[str] = []
        self.stops = 0

    def speak(self, text: str) -> bool:
        self.spoken.append(text)
        return True

    def stop(self) -> bool:
        self.stops += 1
        return True


def speaker_for(engine) -> StreamingSpeaker:
    """A synchronous speaker - no worker thread, no timing."""

    return StreamingSpeaker(engine=engine, start_worker=False)


def test_cancelling_a_stream_drops_what_was_queued():
    """
    The queued sentences are the rest of a reply nobody wants any more.
    Speaking them after a cancel is the opposite of cancelling.
    """

    engine = RecordingEngine()
    speaker = speaker_for(engine)

    speaker.feed("This is the first sentence of the reply. ")
    speaker.feed("This is the second sentence of the reply. ")

    speaker.cancel()

    assert speaker.drain() == 0
    assert engine.spoken == []


def test_cancelling_a_stream_stops_the_sentence_in_flight():

    engine = RecordingEngine()
    speaker = speaker_for(engine)

    speaker.cancel()

    assert engine.stops == 1


def test_cancelling_a_stream_drops_the_half_sentence():
    """
    A partial sentence left in the aggregator must not surface at the
    front of the next reply.
    """

    engine = RecordingEngine()
    speaker = speaker_for(engine)

    speaker.feed("this fragment never finished")

    speaker.cancel()

    bus = EventBus()
    speaker.attach(bus)

    bus.publish(StreamStartedEvent())
    bus.publish(StreamChunkEvent(text="A brand new reply entirely. "))
    bus.publish(StreamFinishedEvent(ok=True))

    speaker.drain()

    assert all("fragment" not in sentence for sentence in engine.spoken)


def test_a_speaker_can_be_reused_after_cancelling():
    """Cancelling one reply must not end the speaker."""

    engine = RecordingEngine()
    speaker = speaker_for(engine)

    speaker.feed("The first reply, which gets cancelled midway. ")
    speaker.cancel()

    speaker.feed("The second reply, which should be spoken fully. ")
    speaker.drain()

    assert len(engine.spoken) == 1
    assert "second reply" in engine.spoken[0]


def test_cancelling_survives_an_engine_that_cannot_stop():
    """
    `stop` is looked up on the engine, not required of it.
    """

    class MinimalEngine:
        def __init__(self):
            self.spoken: list[str] = []

        def speak(self, text: str) -> bool:
            self.spoken.append(text)
            return True

    speaker = speaker_for(MinimalEngine())

    speaker.feed("Something long enough to become a sentence. ")

    speaker.cancel()

    assert speaker.drain() == 0


def test_cancelling_survives_an_engine_whose_stop_raises():

    class AngryEngine:
        def speak(self, text: str) -> bool:
            return True

        def stop(self) -> bool:
            raise RuntimeError("nope")

    speaker = speaker_for(AngryEngine())

    speaker.cancel()

    assert speaker.drain() == 0
