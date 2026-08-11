"""
Text to speech engine.

This is where a Response becomes sound. The brain never calls it - the
engine subscribes to ResponseEvent instead, so the direction of the
dependency is voice -> events, never brain -> voice.

    bus.publish(ResponseEvent(text))  ->  TTSEngine  ->  provider.speak()

`attach(bus)` wires that up in one line and returns an unsubscribe
callable for shutdown.

It also follows MoodChangedEvent, so a mood is audible as well as
visible. Pacing is offered to the provider through an optional method
rather than added to the TTSProvider protocol: that protocol is
runtime_checkable, and adding a method to it would make every existing
provider - including a user's own - fail isinstance overnight. A provider
that wants mood implements `set_pacing`; one that does not is never asked.
"""

from core.logger import logger
from events.types import MoodChangedEvent, ResponseEvent, SpeakingEvent
from voice.tts.pacing import pacing_for
from voice.tts.provider import TTSProvider, is_provider_available


class TTSEngine:

    def __init__(
        self,
        provider: TTSProvider,
        events=None,
        enabled: bool = True,
        skip_streamed: bool = False,
    ):
        """
        `skip_streamed` ignores replies whose text was already spoken
        sentence by sentence as it streamed. It stays False until a
        streaming speaker is attached, because with nothing else speaking
        those replies, skipping them would simply make Aura silent.
        """

        self.provider = provider
        self.events = events
        self.enabled = enabled
        self.skip_streamed = skip_streamed

        self._releases: list = []

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

        self._emit(SpeakingEvent(text=text, active=True, voice=self._voice()))

        try:
            self.provider.speak(text)
            return True

        except Exception as error:
            logger.warning("Speech failed: %s", error)
            return False

        finally:
            self._emit(SpeakingEvent(text="", active=False))

    def stop(self) -> bool:
        """
        Cut the current utterance short.

        Returns True when a provider was actually able to act on it.
        Optional by absence, exactly like `set_pacing` below: a provider
        that implements `cancel` is asked to, one that does not finishes
        its sentence, and the TTSProvider protocol stays a single method
        so no existing provider fails isinstance.

        No SpeakingEvent is published here. The `speak` call being
        cancelled emits `active=False` from its own `finally`, so the
        avatar leaves its speaking state exactly once - emitting again
        from this side would either duplicate that or, when nothing is
        speaking, announce the end of speech that never started.
        """

        canceller = getattr(self.provider, "cancel", None)

        if canceller is None:
            return False

        try:
            canceller()
            return True

        except Exception as error:
            logger.warning("Provider refused cancel: %s", error)
            return False

    def _voice(self) -> str:
        """
        The provider's voice name, for anything downstream that cares.

        An avatar picking a face to match the voice needs this, and
        asking the provider here is cheaper than making every provider
        publish its own events. Optional: a provider with no `voice`
        attribute simply reports nothing.
        """

        return str(getattr(self.provider, "voice", "") or "")

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
        Speak every reply that appears on the bus, and follow her mood.

        Returns a callable that undoes both subscriptions, so a launcher
        can detach the voice without tearing down the bus.
        """

        self.events = self.events or bus

        self._releases = [
            bus.subscribe(ResponseEvent, self._on_response),
            bus.subscribe(MoodChangedEvent, self._on_mood),
        ]

        def detach() -> None:
            self.detach()

        return detach

    def detach(self) -> None:

        for release in self._releases:
            try:
                release()
            except Exception:
                pass

        self._releases.clear()

    def _on_response(self, event: ResponseEvent) -> None:

        if self.skip_streamed and getattr(event, "streamed", False):
            return

        self.speak(event.text)

    def _on_mood(self, event: MoodChangedEvent) -> None:
        """
        Offer the provider a new pacing.

        Optional by absence, exactly like `voice` above: `set_pacing` is
        looked up rather than required, so no existing provider changes
        and no protocol widens.
        """

        setter = getattr(self.provider, "set_pacing", None)

        if setter is None:
            return

        try:
            setter(pacing_for(getattr(event, "mood", None)))
        except Exception as error:
            # Pacing is a nicety. Losing it must not cost the voice.
            logger.debug("Provider refused pacing: %s", error)

    def _emit(self, event) -> None:

        if self.events is None:
            return

        try:
            self.events.publish(event)
        except Exception as error:
            # Aura still speaks. Only the notification that she is
            # speaking was lost, which a lip-sync or avatar subscriber
            # would experience as silence it cannot explain.
            logger.debug(
                "Speech event publish failed (%s): %s",
                type(event).__name__,
                error,
            )
