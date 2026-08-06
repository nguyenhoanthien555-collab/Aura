"""
Mock speech to text.

Not a stub: this is the provider the test suite runs against, and the
one Aura falls back to when no real engine is installed. It returns
scripted transcripts, so a whole voice pipeline can be exercised with
no microphone, no model download and no network.
"""

from voice.stt.provider import AudioChunk


class MockSTTProvider:

    def __init__(
        self,
        transcripts: list[str] | None = None,
        default: str = "",
    ):
        """
        `transcripts` is consumed one call at a time. Once exhausted,
        every further call returns `default` - empty by default, which
        the engine treats as "nothing was said".
        """

        self.transcripts = list(transcripts or [])
        self.default = default
        self.received: list[AudioChunk] = []

    def transcribe(self, audio: AudioChunk) -> str:

        self.received.append(audio)

        if self.transcripts:
            return self.transcripts.pop(0)

        return self.default

    def is_available(self) -> bool:
        return True
