"""
Cross platform text to speech through pyttsx3.

Optional. The package is imported lazily and the engine is built on
first use, so a missing install is a fallback, not a crash.

pyttsx3 engines are not reliably reusable across `runAndWait()` calls on
every backend, so a fresh engine is created per utterance. Slower, but it
avoids the class of bug where the second sentence never plays.
"""

from core.logger import logger


class Pyttsx3TTSProvider:

    def __init__(
        self,
        rate: int | None = None,
        volume: float | None = None,
        voice: str = "",
    ):

        self.rate = rate
        self.volume = volume
        self.voice = voice

    def is_available(self) -> bool:

        try:
            import pyttsx3          # noqa: F401, PLC0415
            return True
        except Exception:
            return False

    def _engine(self):

        import pyttsx3              # noqa: PLC0415

        engine = pyttsx3.init()

        if self.rate is not None:
            engine.setProperty("rate", self.rate)

        if self.volume is not None:
            engine.setProperty("volume", max(0.0, min(1.0, self.volume)))

        if self.voice:
            self._select_voice(engine)

        return engine

    def _select_voice(self, engine) -> None:
        """Match a voice by id or by name, case insensitively."""

        wanted = self.voice.lower()

        try:
            for voice in engine.getProperty("voices"):

                name = (getattr(voice, "name", "") or "").lower()
                identifier = (getattr(voice, "id", "") or "").lower()

                if wanted in name or wanted in identifier:
                    engine.setProperty("voice", voice.id)
                    return

            logger.debug("Voice not found: %s", self.voice)

        except Exception as error:
            logger.debug("Voice selection failed: %s", error)

    def speak(self, text: str) -> None:

        if not text:
            return

        engine = self._engine()

        engine.say(text)
        engine.runAndWait()

        try:
            engine.stop()
        except Exception:
            pass
