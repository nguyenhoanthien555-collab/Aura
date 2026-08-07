"""
Voice factory.

One place that maps a config name onto a concrete provider. Every import
is lazy and every failure falls back to a mock, which is what makes this
true:

    Aura starts on a machine with no microphone, no speakers, no models
    and no network. It simply cannot hear or talk.

That is a degraded companion, not a crashed one.
"""

from core.logger import logger
from voice.stt.microphone import Microphone, MockMicrophone, SystemMicrophone
from voice.stt.provider import SpeechToTextProvider
from voice.tts.provider import TTSProvider, is_provider_available
from voice.tts.values import number_in


# ----------------------------------------------------------------------
# Text to speech
# ----------------------------------------------------------------------

def create_tts_provider(
    name: str = "auto",
    options: dict | None = None,
) -> TTSProvider:
    """
    Build a speech provider by name.

    "auto" walks the bundled providers best first and takes the first
    one that reports itself available.
    """

    options = options or {}
    name = (name or "auto").strip().lower()

    if name == "auto":
        return _auto_tts(options)

    builder = _TTS_BUILDERS.get(name)

    if builder is None:
        logger.warning("Unknown TTS provider '%s', using mock", name)
        return _mock_tts()

    try:
        provider = builder(options)

    except Exception as error:
        logger.warning(
            "TTS provider '%s' could not be created (%s), using mock",
            name,
            error,
        )
        return _mock_tts()

    if not is_provider_available(provider):
        logger.warning(
            "TTS provider '%s' is not available here, using mock", name
        )
        return _mock_tts()

    return provider


def _mock_tts():

    from voice.tts.providers.mock import MockTTSProvider

    return MockTTSProvider()


def _build_sapi(options: dict):

    from voice.tts.providers.sapi import SapiTTSProvider

    return SapiTTSProvider(
        rate=_as_int(options.get("rate"), 0),
        volume=_as_int(options.get("volume"), 100),
        voice=options.get("voice", ""),
    )


def _as_int(value, fallback: int) -> int:
    """
    Read a number out of a setting that may have been written for Edge.

    `voice.tts.rate` is one key shared by every provider, so it can hold
    "+5%" when the user has been running Edge and then switches back to
    SAPI. Reading the number out of it beats refusing to start, and "+5%"
    genuinely does mean "a little faster" to both.

    The parsing itself is `voice.tts.values.number_in`, which the Edge
    provider uses to shift its own settings. Two forgiving number readers
    in one package would eventually disagree about "+5%".
    """

    return number_in(value, fallback)


def _as_float(value, fallback: float) -> float:
    """
    A seconds value out of config, falling back rather than raising.

    Used for timeouts, where a nonsense value should cost the setting and
    a negative one should not mean "give up instantly".
    """

    if isinstance(value, bool) or value is None:
        return fallback

    try:
        seconds = float(value)
    except (TypeError, ValueError):
        logger.warning("Unusable timeout %r, using %s", value, fallback)
        return fallback

    return seconds if seconds > 0 else fallback


def _build_pyttsx(options: dict):
    """
    pyttsx3, with the shared rate setting translated into its units.

    `voice.tts.rate` is written in SAPI's scale (-10..10, 0 = normal).
    pyttsx3 wants words per minute, where 0 is not "normal" but silence,
    so the value is mapped rather than passed through.
    """

    from voice.tts.providers.pyttsx import Pyttsx3TTSProvider

    volume = _as_int(options.get("volume"), 100)

    return Pyttsx3TTSProvider(
        rate=_words_per_minute(options.get("rate")),
        volume=max(0.0, min(1.0, volume / 100.0)),
        voice=options.get("voice", ""),
    )


# pyttsx3's own default, and roughly conversational speech.
BASE_WORDS_PER_MINUTE = 200
WORDS_PER_MINUTE_PER_STEP = 20


def _words_per_minute(value) -> int | None:
    """
    Turn a shared rate setting into words per minute.

    A value already in that range is taken literally; a small one is read
    as a SAPI-style offset. Anything unusable returns None, which leaves
    pyttsx3 on its own default.
    """

    rate = _as_int(value, 0)

    if rate >= 50:
        return rate

    if rate == 0:
        return None

    return max(
        50,
        BASE_WORDS_PER_MINUTE + rate * WORDS_PER_MINUTE_PER_STEP,
    )


def _build_edge(options: dict):
    """
    Edge's neural voices.

    Every value comes from config; the provider supplies the defaults so
    that a half filled `voice.tts` section still produces Aura's voice
    rather than a neutral one. `rate` and `pitch` are normalised inside
    the provider, so a SAPI-era `rate: 0` is understood rather than
    rejected.

    Both timeouts are injected rather than left at their defaults. They
    are the two ways this provider can hang - a network round trip that
    never returns, and a player process that never exits - and a value
    that cannot be reached from config is a value nobody can fix without
    editing the source.
    """

    from voice.tts.audio import (
        MAX_PLAYBACK_SECONDS,
        SystemAudioPlayer,
        create_audio_player,
    )
    from voice.tts.providers.edge import (
        DEFAULT_VOICE,
        SYNTHESIS_TIMEOUT,
        EdgeTTSProvider,
    )

    playback_timeout = _as_float(
        options.get("playback_timeout"), MAX_PLAYBACK_SECONDS
    )

    player = create_audio_player(enabled=bool(options.get("playback", True)))

    # Only the real player has a timeout to set. A NullAudioPlayer is
    # already instant.
    if isinstance(player, SystemAudioPlayer):
        player.timeout = playback_timeout

    return EdgeTTSProvider(
        voice=(options.get("voice") or "").strip() or DEFAULT_VOICE,
        rate=options.get("rate", ""),
        pitch=options.get("pitch", ""),
        volume=options.get("volume", ""),
        player=player,
        timeout=_as_float(options.get("timeout"), SYNTHESIS_TIMEOUT),
    )


_TTS_BUILDERS = {
    "mock": lambda options: _mock_tts(),
    "sapi": _build_sapi,
    "windows": _build_sapi,
    "pyttsx": _build_pyttsx,
    "pyttsx3": _build_pyttsx,
    "edge": _build_edge,
    "edge-tts": _build_edge,
    "edge_tts": _build_edge,
}


# Order matters, and Edge is deliberately not in it. It is the best
# sounding voice Aura has, but it needs a network round trip per reply,
# and "auto" has to keep working on a machine that is offline. Ask for
# it by name: `provider: edge`.
_TTS_AUTO_ORDER = ["sapi", "pyttsx"]


def _auto_tts(options: dict) -> TTSProvider:

    for name in _TTS_AUTO_ORDER:

        try:
            provider = _TTS_BUILDERS[name](options)
        except Exception:
            continue

        if is_provider_available(provider):
            logger.info("TTS provider: %s", name)
            return provider

    logger.info("TTS provider: mock (no system voice available)")

    return _mock_tts()


# ----------------------------------------------------------------------
# Speech to text
# ----------------------------------------------------------------------

def create_stt_provider(
    name: str = "auto",
    options: dict | None = None,
) -> SpeechToTextProvider:
    """
    Build a transcription provider by name.

    Unlike TTS, "auto" does not eagerly try Whisper: loading a model can
    download weights and take a long time. Auto means mock unless the
    user asked for something heavier by name.
    """

    options = options or {}
    name = (name or "auto").strip().lower()

    if name in ("auto", "mock", ""):
        return _mock_stt(options)

    if name in ("whisper", "faster-whisper", "faster_whisper"):
        return _build_whisper(options)

    logger.warning("Unknown STT provider '%s', using mock", name)

    return _mock_stt(options)


def _mock_stt(options: dict):

    from voice.stt.providers.mock import MockSTTProvider

    return MockSTTProvider(
        transcripts=list(options.get("transcripts") or []),
        default=options.get("default", ""),
    )


def _build_whisper(options: dict):

    try:
        from voice.stt.providers.whisper import WhisperProvider

        provider = WhisperProvider(
            model_size=options.get("model", "base"),
            device=options.get("device", "cpu"),
            compute_type=options.get("compute_type", "int8"),
            language=options.get("language") or None,
        )

        if provider.is_available():
            return provider

        logger.warning(
            "faster-whisper is not installed, using mock transcription"
        )

    except Exception as error:
        logger.warning("Whisper unavailable (%s), using mock", error)

    return _mock_stt(options)


# ----------------------------------------------------------------------
# Microphone
# ----------------------------------------------------------------------

def create_microphone(options: dict | None = None) -> Microphone:
    """
    Build a microphone, falling back to a silent one.

    A MockMicrophone that reports itself unavailable is returned when no
    input device exists, so `SpeechToTextEngine.is_available()` correctly
    says no rather than quietly transcribing silence forever.
    """

    options = options or {}

    if options.get("mock"):
        return MockMicrophone()

    microphone = SystemMicrophone(
        sample_rate=options.get("sample_rate", 16000),
        channels=options.get("channels", 1),
        device=options.get("device"),
    )

    if microphone.is_available():
        return microphone

    logger.info("No input device found, microphone disabled")

    return MockMicrophone(available=False)
