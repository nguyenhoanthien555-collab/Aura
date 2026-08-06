"""
Microphone capture.

Recording is separated from transcription so that either half can be
mocked alone. Tests use MockMicrophone and never touch hardware.
"""

from typing import Protocol, runtime_checkable

from core.logger import logger
from voice.stt.provider import AudioChunk


DEFAULT_SAMPLE_RATE = 16000
DEFAULT_RECORD_SECONDS = 5.0


@runtime_checkable
class Microphone(Protocol):
    """
    A source of recorded audio.

    `is_available()` must be answerable without recording anything, so a
    launcher can decide whether to offer voice input at all.
    """

    def record(self, seconds: float) -> AudioChunk:
        ...

    def is_available(self) -> bool:
        ...


class MockMicrophone:
    """
    A microphone that exists only in memory.

    Give it a script of chunks and it plays them back in order; once the
    script runs out it returns silence forever. This is what every test
    uses, and it is also the fallback when no audio device is present.
    """

    def __init__(
        self,
        chunks: list[AudioChunk] | None = None,
        available: bool = True,
    ):

        self.chunks = list(chunks or [])
        self.calls: list[float] = []
        self._available = available

    def record(self, seconds: float = DEFAULT_RECORD_SECONDS) -> AudioChunk:

        self.calls.append(seconds)

        if self.chunks:
            return self.chunks.pop(0)

        return AudioChunk.silence(seconds)

    def is_available(self) -> bool:
        return self._available


class SystemMicrophone:
    """
    Real capture through `sounddevice`.

    The dependency is optional and imported lazily, so importing this
    module on a machine without PortAudio installed is harmless. If the
    import or the device query fails, `is_available()` returns False and
    the launcher falls back to MockMicrophone.
    """

    def __init__(
        self,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = 1,
        device: int | str | None = None,
    ):

        self.sample_rate = sample_rate
        self.channels = channels
        self.device = device

    @staticmethod
    def _sounddevice():
        import sounddevice          # noqa: PLC0415  (optional dependency)

        return sounddevice

    def is_available(self) -> bool:

        try:
            sd = self._sounddevice()
        except Exception:
            return False

        try:
            devices = sd.query_devices()
        except Exception as error:
            logger.debug("Microphone query failed: %s", error)
            return False

        for device in devices:
            if device.get("max_input_channels", 0) > 0:
                return True

        return False

    def record(self, seconds: float = DEFAULT_RECORD_SECONDS) -> AudioChunk:
        """
        Block for `seconds` and return what was heard.

        Any capture failure degrades to silence rather than raising, so a
        yanked USB microphone cannot crash a running companion.
        """

        try:
            sd = self._sounddevice()

            frames = int(seconds * self.sample_rate)

            recording = sd.rec(
                frames,
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="int16",
                device=self.device,
            )

            sd.wait()

            return AudioChunk(
                data=bytes(recording),
                sample_rate=self.sample_rate,
                channels=self.channels,
            )

        except Exception as error:
            logger.warning("Microphone capture failed: %s", error)
            return AudioChunk.silence(seconds, self.sample_rate)
