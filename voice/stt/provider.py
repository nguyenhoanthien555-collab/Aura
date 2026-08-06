"""
Speech to text provider interface.

A provider turns recorded audio into text. That is the whole contract -
it says nothing about where the audio came from, so a file, a socket or
a microphone are all equally valid sources.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class AudioChunk:
    """
    A block of raw PCM audio.

    Defaults describe the format every bundled provider expects:
    16 kHz, mono, signed 16 bit little endian. Providers that need
    something else are responsible for converting, not the caller.
    """

    data: bytes
    sample_rate: int = 16000
    channels: int = 1
    sample_width: int = 2          # bytes per sample

    @property
    def frame_size(self) -> int:
        return max(1, self.sample_width * self.channels)

    @property
    def duration(self) -> float:
        """Length in seconds."""

        if not self.sample_rate:
            return 0.0

        frames = len(self.data) / self.frame_size

        return frames / self.sample_rate

    def is_empty(self) -> bool:
        return not self.data

    @classmethod
    def silence(
        cls,
        seconds: float = 1.0,
        sample_rate: int = 16000,
    ) -> "AudioChunk":
        """Silent audio of a given length. Used as a safe fallback."""

        frames = int(max(0.0, seconds) * sample_rate)

        return cls(
            data=b"\x00\x00" * frames,
            sample_rate=sample_rate,
        )


@runtime_checkable
class SpeechToTextProvider(Protocol):
    """
    Turns audio into text.

    Returns an empty string when nothing intelligible was heard. It must
    not raise for silence - silence is a normal outcome, not an error.
    """

    def transcribe(self, audio: AudioChunk) -> str:
        ...
