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
        rate=options.get("rate", 0),
        volume=options.get("volume", 100),
        voice=options.get("voice", ""),
    )


def _build_pyttsx(options: dict):

    from voice.tts.providers.pyttsx import Pyttsx3TTSProvider

    return Pyttsx3TTSProvider(
        rate=options.get("rate"),
        volume=options.get("volume"),
        voice=options.get("voice", ""),
    )


_TTS_BUILDERS = {
    "mock": lambda options: _mock_tts(),
    "sapi": _build_sapi,
    "windows": _build_sapi,
    "pyttsx": _build_pyttsx,
    "pyttsx3": _build_pyttsx,
}


# Order matters: SAPI first because on Windows it needs no install.
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
