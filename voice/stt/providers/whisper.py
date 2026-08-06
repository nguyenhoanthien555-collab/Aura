"""
Local speech to text through Whisper.

Optional. `faster-whisper` is imported lazily and the model is loaded on
first use, so importing this module costs nothing and starting Aura
without the package installed is not an error - the factory falls back
to the mock provider.

Audio is handed over as a temporary WAV file written with the standard
library `wave` module, which keeps numpy out of the required set.
"""

import os
import tempfile
import wave

from core.logger import logger
from voice.stt.provider import AudioChunk


DEFAULT_MODEL = "base"


class WhisperProvider:

    def __init__(
        self,
        model_size: str = DEFAULT_MODEL,
        device: str = "cpu",
        compute_type: str = "int8",
        language: str | None = None,
    ):

        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language

        self._model = None

    # ------------------------------------------------------------------
    # Model
    # ------------------------------------------------------------------

    def is_available(self) -> bool:

        try:
            import faster_whisper   # noqa: F401, PLC0415
            return True
        except Exception:
            return False

    @property
    def model(self):
        """
        Load on first use.

        This can take tens of seconds and may download weights, which is
        exactly why it does not happen at construction time.
        """

        if self._model is None:

            from faster_whisper import WhisperModel   # noqa: PLC0415

            logger.info("Loading Whisper model: %s", self.model_size)

            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
            )

        return self._model

    # ------------------------------------------------------------------
    # Transcription
    # ------------------------------------------------------------------

    def transcribe(self, audio: AudioChunk) -> str:

        if audio.is_empty():
            return ""

        path = self._write_wav(audio)

        try:
            segments, _info = self.model.transcribe(
                path,
                language=self.language,
            )

            text = " ".join(
                segment.text.strip() for segment in segments
            )

            return text.strip()

        except Exception as error:
            logger.warning("Whisper transcription failed: %s", error)
            return ""

        finally:
            self._remove(path)

    @staticmethod
    def _write_wav(audio: AudioChunk) -> str:

        handle, path = tempfile.mkstemp(suffix=".wav", prefix="aura_stt_")

        os.close(handle)

        with wave.open(path, "wb") as wav:
            wav.setnchannels(audio.channels)
            wav.setsampwidth(audio.sample_width)
            wav.setframerate(audio.sample_rate)
            wav.writeframes(audio.data)

        return path

    @staticmethod
    def _remove(path: str) -> None:

        try:
            os.unlink(path)
        except OSError:
            pass
