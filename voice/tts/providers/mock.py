"""
Mock text to speech.

Silent, instant, and inspectable. Tests assert against `spoken`, and
Aura falls back to this whenever no real voice is available - which is
what lets the companion run on a machine with no audio output at all.
"""

from core.logger import logger


class MockTTSProvider:

    def __init__(self, log: bool = False):

        self.spoken: list[str] = []
        self.log = log

    def speak(self, text: str) -> None:

        self.spoken.append(text)

        if self.log:
            logger.info("[tts:mock] %s", text)

    def is_available(self) -> bool:
        return True

    @property
    def last(self) -> str:
        return self.spoken[-1] if self.spoken else ""
