"""
Text to speech engine.

This is where a Response becomes sound. The brain never calls it - the
engine subscribes to ResponseEvent instead, so the direction of the
dependency is voice -> events, never brain -> voice.

    bus.publish(ResponseEvent(text))  ->  TTSEngine  ->  provider.speak()

`attach(bus)` wires that up in one line and returns an unsubscribe
callable for shutdown.
"""

from core.logger import logger
from events.types import ResponseEvent, SpeakingEvent
from voice.tts.provider import TTSProvider, is_provider_available


class TTSEngine:

    def __init__(
        self,
        provider: TTSProvider,
        events=None,
        enabled: bool = True,
    ):

        self.provider = provider
        self.events = events
        self.enabled = enabled

    # ------------------------------------------------------------------
    # Speaking
    # ------------------------------------------------------------------

    def is_available(self) -> bool:

        if not self.enabled:
            return False

        return is_provider_available(self.provider)

    def speak(self, text: str) -> bool:
        """
        Say something out loud.

        Returns True when the provider accepted the text. Failures are
        logged and reported as False - a dead speaker is not a reason to
        lose the reply, which the user can still read.

        SpeakingEvent(active=False) is published even on failure, so the
        avatar never gets stuck in its speaking animation.
        """

        if not self.enabled:
            return False

        text = (text or "").strip()

        if not text:
            return False

        self._emit(SpeakingEvent(text=text, active=True))

        try:
            self.provider.speak(text)
            return True

        except Exception as error:
            logger.warning("Speech failed: %s", error)
            return False

        finally:
            self._emit(SpeakingEvent(text="", active=False))

    def speak_response(self, response) -> bool:
        """
        Speak anything that carries `.text`.

        Takes the duck type rather than brain.response.Response so that
        voice/ does not import brain/.
        """

        return self.speak(getattr(response, "text", "") or "")

    # ------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------

    def attach(self, bus):
        """
        Speak every reply that appears on the bus.

        Returns the unsubscribe callable so a launcher can detach the
        voice without tearing down the bus.
        """

        return bus.subscribe(ResponseEvent, self._on_response)

    def _on_response(self, event: ResponseEvent) -> None:
        self.speak(event.text)

    def _emit(self, event) -> None:

        if self.events is None:
            return

        try:
            self.events.publish(event)
        except Exception:
            pass
