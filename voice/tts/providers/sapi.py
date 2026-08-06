"""
Windows text to speech through System.Speech (SAPI 5).

Chosen as the default real voice on Windows because it needs no pip
install, no model download and no API key - it is part of the OS.

Security note: the text spoken here comes from a language model, so it
must never be pasted into a PowerShell command string. It is passed
through an environment variable and read back inside the script as
`$env:AURA_TTS_TEXT`, which means no amount of quoting, backticks or
semicolons in the reply can escape into the shell.
"""

import os
import shutil
import subprocess

from core.logger import logger


# Speech is roughly 3 words/second at rate 0; the ceiling is a guard
# against a hung process, not an expected duration.
BASE_TIMEOUT = 15.0
SECONDS_PER_CHARACTER = 0.12
MAX_TIMEOUT = 300.0


SCRIPT = (
    "Add-Type -AssemblyName System.Speech; "
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
    "$s.Rate = {rate}; "
    "$s.Volume = {volume}; "
    "if ($env:AURA_TTS_VOICE) {{ "
    "try {{ $s.SelectVoice($env:AURA_TTS_VOICE) }} catch {{ }} "
    "}}; "
    "$s.Speak($env:AURA_TTS_TEXT)"
)


class SapiTTSProvider:

    def __init__(
        self,
        rate: int = 0,
        volume: int = 100,
        voice: str = "",
    ):

        # SAPI accepts -10..10 for rate and 0..100 for volume. Clamping
        # here keeps out-of-range config values out of the script.
        self.rate = max(-10, min(10, int(rate)))
        self.volume = max(0, min(100, int(volume)))
        self.voice = voice

    def is_available(self) -> bool:

        if os.name != "nt":
            return False

        return self._powershell() is not None

    @staticmethod
    def _powershell() -> str | None:

        return (
            shutil.which("powershell")
            or shutil.which("pwsh")
        )

    def _timeout(self, text: str) -> float:

        estimate = BASE_TIMEOUT + len(text) * SECONDS_PER_CHARACTER

        return min(MAX_TIMEOUT, estimate)

    def speak(self, text: str) -> None:

        if not text:
            return

        shell = self._powershell()

        if shell is None:
            raise RuntimeError("PowerShell not found")

        environment = os.environ.copy()
        environment["AURA_TTS_TEXT"] = text
        environment["AURA_TTS_VOICE"] = self.voice

        script = SCRIPT.format(
            rate=self.rate,
            volume=self.volume,
        )

        try:
            subprocess.run(
                [
                    shell,
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    script,
                ],
                env=environment,
                check=True,
                timeout=self._timeout(text),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )

        except subprocess.TimeoutExpired:
            logger.warning("SAPI speech timed out")

        except subprocess.CalledProcessError as error:
            detail = (error.stderr or b"").decode(errors="replace").strip()
            raise RuntimeError(f"SAPI speech failed: {detail}") from error
