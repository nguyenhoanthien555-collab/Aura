"""
Edge TTS tests.

Nothing here reaches the network, writes a permanent file or makes a
sound. Two seams make that possible, and they exist for exactly this
reason:

    synthesizer=    replaces edge_tts.Communicate
    player=         replaces the speakers

What is checked is the contract between those two - that the provider
passes the right voice, that it plays what it synthesised, and that it
cleans up after itself even when synthesis fails.
"""

import os

import pytest

from events.bus import EventBus
from events.types import ResponseEvent, SpeakingEvent

from voice.factory import _as_int, _words_per_minute, create_tts_provider
from voice.tts.audio import NullAudioPlayer
from voice.tts.engine import TTSEngine
from voice.tts.provider import TTSProvider, is_provider_available
from voice.tts.providers.edge import (
    DEFAULT_PITCH,
    DEFAULT_RATE,
    DEFAULT_VOICE,
    EdgeTTSProvider,
    normalise_hertz,
    normalise_percent,
)
from voice.tts.providers.mock import MockTTSProvider
from voice.tts.values import number_in


# ----------------------------------------------------------------------
# Doubles
# ----------------------------------------------------------------------

class FakeSynthesizer:
    """
    Stands in for edge_tts. Records its arguments and writes a file, so
    the provider's cleanup can be observed.
    """

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


class BrokenSynthesizer:
    """
    Fails the way the real one does when the network is down.

    Records the path first, so the cleanup on the failure path can be
    checked - that is the case a `finally` block exists for.
    """

    def __init__(self):
        self.paths: list[str] = []

    def __call__(self, **kwargs):
        self.paths.append(kwargs["path"])
        raise RuntimeError("edge-tts could not reach the service")


def provider_for(**kwargs) -> EdgeTTSProvider:
    """An Edge provider with both ends replaced."""

    kwargs.setdefault("synthesizer", FakeSynthesizer())
    kwargs.setdefault("player", NullAudioPlayer())

    return EdgeTTSProvider(**kwargs)


# ----------------------------------------------------------------------
# Construction
# ----------------------------------------------------------------------

def test_the_default_voice_is_auras():
    assert EdgeTTSProvider().voice == "en-US-AvaMultilingualNeural"
    assert DEFAULT_VOICE == "en-US-AvaMultilingualNeural"


def test_she_speaks_slightly_faster_and_slightly_higher_by_default():
    """Warm and conversational rather than a newsreader."""

    provider = EdgeTTSProvider()

    assert provider.rate == DEFAULT_RATE == "+5%"
    assert provider.pitch == DEFAULT_PITCH == "+10Hz"


def test_a_configured_voice_wins():
    provider = provider_for(voice="en-GB-SoniaNeural", rate="+20%")

    assert provider.voice == "en-GB-SoniaNeural"
    assert provider.rate == "+20%"


def test_an_empty_voice_falls_back_to_the_default():
    """A blank in config means "whatever the provider considers its own"."""

    assert provider_for(voice="").voice == DEFAULT_VOICE
    assert provider_for(voice="   ").voice == DEFAULT_VOICE


def test_it_satisfies_the_existing_provider_protocol():
    """
    The whole point of putting it in voice/tts/providers: TTSEngine and
    the factory need no change to use it.
    """

    assert isinstance(provider_for(), TTSProvider)


def test_repr_does_not_hide_what_it_will_sound_like():
    assert "AvaMultilingual" in repr(provider_for())


# ----------------------------------------------------------------------
# Rate, pitch and volume normalisation
# ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("+5%", "+5%"),
        ("-10%", "-10%"),
        ("5", "+5%"),
        ("5%", "+5%"),
        (5, "+5%"),
        (0, "+0%"),
        (-3, "-3%"),
        (2.0, "+2%"),
    ],
)
def test_a_rate_is_understood_however_it_was_written(raw, expected):
    assert normalise_percent(raw, "+5%") == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("+10Hz", "+10Hz"),
        ("-5Hz", "-5Hz"),
        ("+10hz", "+10Hz"),
        (10, "+10Hz"),
        ("10", "+10Hz"),
        (0, "+0Hz"),
    ],
)
def test_a_pitch_is_understood_however_it_was_written(raw, expected):
    assert normalise_hertz(raw, "+10Hz") == expected


def test_nonsense_falls_back_instead_of_raising():
    """A typo in a voice setting should not cost the reply."""

    assert normalise_percent("quickly", "+5%") == "+5%"
    assert normalise_hertz("higher", "+10Hz") == "+10Hz"
    assert normalise_percent(None, "+5%") == "+5%"
    assert normalise_percent(True, "+5%") == "+5%"


def test_a_sapi_era_config_still_produces_a_voice():
    """
    `rate: 0` was written for SAPI, where 0 means normal. Edge would
    reject it outright; reading it as "+0%" is friendlier than refusing
    to speak over a units mismatch.
    """

    assert provider_for(rate=0, pitch=0).rate == "+0%"


# ----------------------------------------------------------------------
# Speaking
# ----------------------------------------------------------------------

def test_speaking_synthesises_then_plays():
    synth = FakeSynthesizer()
    player = NullAudioPlayer()

    provider_for(synthesizer=synth, player=player).speak("hey bro")

    assert len(synth.calls) == 1
    assert len(player.played) == 1
    assert synth.last["path"] == player.last


def test_the_configured_voice_reaches_the_synthesiser():
    synth = FakeSynthesizer()

    provider_for(
        synthesizer=synth,
        voice="en-US-AvaMultilingualNeural",
        rate="+5%",
        pitch="+10Hz",
    ).speak("hey bro")

    assert synth.last["voice"] == "en-US-AvaMultilingualNeural"
    assert synth.last["rate"] == "+5%"
    assert synth.last["pitch"] == "+10Hz"
    assert synth.last["text"] == "hey bro"


def test_empty_text_is_not_synthesised():
    """Silence costs a network round trip otherwise."""

    synth = FakeSynthesizer()

    provider = provider_for(synthesizer=synth)

    provider.speak("")
    provider.speak("   ")

    assert synth.calls == []


def test_the_temporary_file_is_removed_afterwards():
    synth = FakeSynthesizer()

    provider_for(synthesizer=synth).speak("hey bro")

    assert not os.path.exists(synth.last["path"])


def test_a_failed_synthesis_leaves_nothing_behind():
    """The cleanup has to survive the failure path too."""

    synth = BrokenSynthesizer()

    provider = provider_for(synthesizer=synth)

    with pytest.raises(RuntimeError):
        provider.speak("hey bro")

    assert not os.path.exists(synth.paths[-1])


def test_a_failed_synthesis_never_reaches_the_player():
    player = NullAudioPlayer()

    provider = provider_for(synthesizer=BrokenSynthesizer(), player=player)

    with pytest.raises(RuntimeError):
        provider.speak("hey bro")

    assert player.played == []


def test_synthesize_can_be_used_without_playing(tmp_path):
    """For a future avatar with lip sync, or a save-to-file command."""

    synth = FakeSynthesizer()
    player = NullAudioPlayer()

    target = str(tmp_path / "aura.mp3")

    provider = provider_for(synthesizer=synth, player=player)

    assert provider.synthesize("hey bro", target) == target
    assert os.path.exists(target)

    # The file survives, unlike the temporary one speak() uses, and
    # nothing was played.
    assert player.played == []


# ----------------------------------------------------------------------
# Availability
# ----------------------------------------------------------------------

def test_an_injected_synthesiser_makes_it_available_without_edge_tts():
    """Availability must not require the package when it is not used."""

    assert provider_for().is_available() is True


def test_a_dead_player_makes_it_unavailable():
    class DeadPlayer:
        def play(self, path):
            raise RuntimeError("no audio device")

        def is_available(self):
            return False

    provider = provider_for(player=DeadPlayer())

    assert provider.is_available() is False


def test_availability_is_not_a_network_check():
    """
    Reachability is not knowable cheaply at startup, so a provider that
    can synthesise and play reports available. A failed request is
    handled where it happens.
    """

    assert provider_for().is_available() is True


def test_is_provider_available_accepts_it():
    assert is_provider_available(provider_for()) is True


# ----------------------------------------------------------------------
# The factory
# ----------------------------------------------------------------------

def test_edge_can_be_requested_by_name():
    """
    Falls back to mock when edge-tts is not installed, which is the
    correct behaviour and also what CI sees.
    """

    provider = create_tts_provider("edge", {"voice": DEFAULT_VOICE})

    assert isinstance(provider, (EdgeTTSProvider, MockTTSProvider))


@pytest.mark.parametrize("name", ["edge", "edge-tts", "edge_tts"])
def test_the_spellings_someone_would_actually_write_all_work(name):
    assert create_tts_provider(name) is not None


def test_auto_never_picks_edge():
    """
    Auto has to keep working offline. Edge needs a network round trip
    per reply, so it is opt in by name.
    """

    assert not isinstance(create_tts_provider("auto"), EdgeTTSProvider)


@pytest.mark.parametrize(
    "raw, expected",
    [("+5%", 5), ("-10%", -10), (7, 7), ("+10Hz", 10), (None, 0), ("x", 0)],
)
def test_the_shared_rate_setting_reads_as_a_number_for_other_providers(
    raw, expected
):
    """
    One `voice.tts.rate` key is read by three providers. Switching from
    Edge back to SAPI must not require rewriting it.
    """

    assert _as_int(raw, 0) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [("+5%", 5), ("-10%", -10), (7, 7), ("+10Hz", 10), (None, 0), ("x", 0)],
)
def test_the_factory_and_the_provider_read_a_setting_the_same_way(
    raw, expected
):
    """
    `_as_int` is `number_in`. Two forgiving number readers in one package
    would eventually disagree about "+5%", and the user would see it as
    the same config meaning two different speeds.
    """

    assert _as_int(raw, 0) == number_in(raw, 0) == expected


def test_a_timeout_that_makes_no_sense_falls_back():
    """
    A negative or unreadable timeout must not mean "give up instantly".
    """

    from voice.factory import _as_float

    assert _as_float(None, 60.0) == 60.0
    assert _as_float("", 60.0) == 60.0
    assert _as_float("nope", 60.0) == 60.0
    assert _as_float(True, 60.0) == 60.0
    assert _as_float(-5, 60.0) == 60.0
    assert _as_float(0, 60.0) == 60.0
    assert _as_float(12.5, 60.0) == 12.5
    assert _as_float("30", 60.0) == 30.0


def test_edge_takes_both_timeouts_from_config():
    """
    The two ways this provider can hang are a network round trip that
    never returns and a player that never exits. Both have to be
    reachable from config, or nobody can fix them without editing source.
    """

    from voice.factory import _build_edge

    provider = _build_edge({"timeout": 12.0, "playback": False})

    assert provider.timeout == 12.0


def test_edge_can_be_built_without_playback():
    """
    Synthesis without a speaker. The reason the player is injected rather
    than constructed inside the provider.
    """

    from voice.factory import _build_edge

    provider = _build_edge({"playback": False})

    assert isinstance(provider.player, NullAudioPlayer)


def test_pyttsx_gets_words_per_minute_not_a_sapi_offset():
    """
    pyttsx3 reads rate as words per minute, where 0 is silence rather
    than "normal". The SAPI scale is mapped rather than passed through.
    """

    assert _words_per_minute(0) is None          # leave pyttsx3's default
    assert _words_per_minute("+5%") == 300
    assert _words_per_minute(-5) == 100
    assert _words_per_minute(180) == 180         # already wpm


# ----------------------------------------------------------------------
# Through the engine, on the bus
# ----------------------------------------------------------------------

def test_a_reply_on_the_bus_is_spoken_by_edge():
    """
    The flow the brief cares about, with Edge in place of the mock. The
    brain still knows nothing about any of it.
    """

    bus = EventBus()
    synth = FakeSynthesizer()

    provider = provider_for(synthesizer=synth)

    TTSEngine(provider=provider, events=bus).attach(bus)

    bus.publish(ResponseEvent(text="Bro that idea is actually pretty sick."))

    assert synth.last["text"] == "Bro that idea is actually pretty sick."


def test_the_speaking_event_carries_the_voice():
    """An avatar picking a face to match the voice needs this."""

    bus = EventBus()
    seen = []
    bus.subscribe(SpeakingEvent, seen.append)

    provider = provider_for(voice="en-US-AvaMultilingualNeural")

    TTSEngine(provider=provider, events=bus).attach(bus)

    bus.publish(ResponseEvent(text="hey bro"))

    assert seen[0].active is True
    assert seen[0].voice == "en-US-AvaMultilingualNeural"
    assert seen[-1].active is False


def test_a_provider_with_no_voice_attribute_still_works():
    """`voice` is optional - MockTTSProvider does not have one."""

    bus = EventBus()
    seen = []
    bus.subscribe(SpeakingEvent, seen.append)

    TTSEngine(provider=MockTTSProvider(), events=bus).attach(bus)

    bus.publish(ResponseEvent(text="hey bro"))

    assert seen[0].voice == ""


def test_a_network_failure_costs_the_speech_not_the_reply():
    """
    Edge is the one provider that can fail for reasons outside this
    machine. The engine has to absorb that.
    """

    bus = EventBus()

    provider = provider_for(synthesizer=BrokenSynthesizer())
    engine = TTSEngine(provider=provider, events=bus)
    engine.attach(bus)

    assert engine.speak("hey bro") is False


def test_the_avatar_is_not_left_speaking_after_a_failure():
    bus = EventBus()
    seen = []
    bus.subscribe(SpeakingEvent, seen.append)

    provider = provider_for(synthesizer=BrokenSynthesizer())

    TTSEngine(provider=provider, events=bus).attach(bus)

    bus.publish(ResponseEvent(text="hey bro"))

    assert seen[-1].active is False


# ----------------------------------------------------------------------
# Audio player
# ----------------------------------------------------------------------

def test_the_null_player_records_and_stays_silent():
    player = NullAudioPlayer()

    player.play("a.mp3")
    player.play("b.mp3")

    assert player.played == ["a.mp3", "b.mp3"]
    assert player.last == "b.mp3"


def test_a_disabled_player_is_the_null_one():
    from voice.tts.audio import create_audio_player

    assert isinstance(create_audio_player(enabled=False), NullAudioPlayer)


def test_creating_a_player_never_raises():
    """A machine with no audio output is a quiet companion, not a crash."""

    from voice.tts.audio import create_audio_player

    assert create_audio_player() is not None
