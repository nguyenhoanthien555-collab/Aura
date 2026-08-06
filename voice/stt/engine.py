"""
Speech to text engine.

Coordinates a microphone and a provider, and announces what it heard on
the event bus. It owns no audio code of its own and no model - both are
injected, so the engine itself is fully testable with a mock pair.

Three listening modes, all built on the same primitive:

    listen_once()        one utterance, then return   (push to talk)
    listen_continuous()  utterance after utterance     (always on)
    wake word            continuous, but only forwards after the phrase
"""

from typing import Callable, Protocol, runtime_checkable

from core.logger import logger
from events.types import ListeningEvent, TranscriptEvent
from voice.stt.microphone import (
    DEFAULT_RECORD_SECONDS,
    Microphone,
    MockMicrophone,
)
from voice.stt.provider import AudioChunk, SpeechToTextProvider


@runtime_checkable
class WakeWord(Protocol):
    """Decides whether a transcript was addressed to Aura."""

    def matches(self, text: str) -> bool:
        ...

    def strip(self, text: str) -> str:
        ...


class KeywordWakeWord:
    """
    Plain substring wake word.

    Deliberately not an audio model: it runs on the transcript, after
    transcription, so it needs no extra dependency and no training. A
    real acoustic detector can replace it later by satisfying WakeWord.
    """

    def __init__(self, phrases: list[str] | None = None):

        self.phrases = [
            phrase.lower().strip()
            for phrase in (phrases or ["aura"])
            if phrase.strip()
        ]

    def matches(self, text: str) -> bool:

        lowered = text.lower()

        return any(phrase in lowered for phrase in self.phrases)

    def strip(self, text: str) -> str:
        """Remove the wake phrase so the brain sees only the request."""

        result = text

        for phrase in self.phrases:

            lowered = result.lower()
            index = lowered.find(phrase)

            if index != -1:
                result = result[:index] + result[index + len(phrase):]

        return result.strip(" ,.:;!?-").strip()


class SpeechToTextEngine:

    def __init__(
        self,
        provider: SpeechToTextProvider,
        microphone: Microphone | None = None,
        events=None,
        wake_word: WakeWord | None = None,
        record_seconds: float = DEFAULT_RECORD_SECONDS,
        enabled: bool = True,
    ):

        self.provider = provider
        self.microphone = microphone or MockMicrophone()
        self.events = events
        self.wake_word = wake_word
        self.record_seconds = record_seconds
        self.enabled = enabled

        self._stop = False

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """
        True when this engine can actually produce a transcript.

        Providers and microphones are not required to implement
        `is_available`; anything that omits it is assumed to work.
        """

        if not self.enabled:
            return False

        return (
            self._probe(self.microphone)
            and self._probe(self.provider)
        )

    @staticmethod
    def _probe(component) -> bool:

        check = getattr(component, "is_available", None)

        if check is None:
            return True

        try:
            return bool(check())
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Listening
    # ------------------------------------------------------------------

    def transcribe(self, audio: AudioChunk) -> str:
        """
        Transcribe audio that was captured elsewhere.

        A provider failure yields an empty transcript rather than an
        exception - a misheard turn should not end the session.
        """

        try:
            return (self.provider.transcribe(audio) or "").strip()

        except Exception as error:
            logger.warning("Transcription failed: %s", error)
            return ""

    def listen_once(self, seconds: float | None = None) -> str:
        """
        Record one utterance and return its transcript.

        This is the push to talk primitive: call it while a key is held,
        or from a button handler. Returns "" when nothing was heard.
        """

        if not self.enabled:
            return ""

        if seconds is None:
            seconds = self.record_seconds

        self._emit(ListeningEvent(active=True))

        try:
            audio = self.microphone.record(seconds)

        except Exception as error:
            logger.warning("Recording failed: %s", error)
            audio = AudioChunk(data=b"")

        finally:
            self._emit(ListeningEvent(active=False))

        text = self.transcribe(audio)

        if text:
            self._emit(TranscriptEvent(text=text))

        return text

    def listen_continuous(
        self,
        on_transcript: Callable[[str], None],
        should_continue: Callable[[], bool] | None = None,
        max_turns: int | None = None,
    ) -> int:
        """
        Listen in a loop, handing each transcript to `on_transcript`.

        Blocking by design - the caller decides whether to run it on a
        background thread. `should_continue` and `max_turns` both bound
        the loop, and `max_turns` is what keeps tests finite.

        Returns the number of transcripts forwarded.
        """

        self._stop = False
        forwarded = 0
        turns = 0

        while not self._stop:

            if max_turns is not None and turns >= max_turns:
                break

            if should_continue is not None and not should_continue():
                break

            turns += 1

            text = self.listen_once()

            if not text:
                continue

            text = self._apply_wake_word(text)

            if not text:
                continue

            try:
                on_transcript(text)
                forwarded += 1

            except Exception as error:
                # A failure downstream must not kill the microphone loop.
                logger.exception("Transcript handler failed: %s", error)

        return forwarded

    def stop(self) -> None:
        """Ask a running listen_continuous loop to finish its turn."""

        self._stop = True

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _apply_wake_word(self, text: str) -> str:
        """
        Gate a transcript on the wake phrase.

        With no wake word configured everything passes through. With one,
        only utterances containing the phrase pass, and the phrase itself
        is removed so the brain never sees "aura, " prefixes.
        """

        if self.wake_word is None:
            return text

        if not self.wake_word.matches(text):
            return ""

        return self.wake_word.strip(text)

    def _emit(self, event) -> None:

        if self.events is None:
            return

        try:
            self.events.publish(event)
        except Exception:
            pass
