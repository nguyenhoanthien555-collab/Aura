"""
Audio playback.

Split out from the providers on purpose. `edge-tts` synthesises bytes; it
does not play them. Rather than let one provider own both jobs, the
player is a separate collaborator that gets injected:

    EdgeTTSProvider(player=SystemAudioPlayer())   # real speakers
    EdgeTTSProvider(player=NullAudioPlayer())     # tests

That seam is what lets the voice tests run with no audio device, and it
means a future provider that also returns a file (ElevenLabs, Kokoro)
reuses this instead of writing its own playback.

Every backend is imported or located lazily, and the order is cheapest
first: an installed Python package, then something already part of
Windows, then a command line player. None of them is a hard dependency.
"""

import os
import shutil
import subprocess
import threading
from typing import Protocol, runtime_checkable

from core.logger import logger


# A guard against a hung player, not an expected duration. Speech longer
# than this is almost certainly a stuck process.
MAX_PLAYBACK_SECONDS = 300.0

# How long a terminated player gets to exit before it is killed. Long
# enough for a process to close an audio device, short enough that a
# cancelled reply feels immediate.
STOP_GRACE_SECONDS = 2.0


@runtime_checkable
class AudioPlayer(Protocol):
    """Plays an audio file and returns when it has finished."""

    def play(self, path: str) -> None:
        ...


class NullAudioPlayer:
    """
    Records what it was asked to play and makes no sound.

    Used by tests, and as the fallback when no backend exists, so a
    machine with no audio output degrades to silence rather than an
    exception.
    """

    def __init__(self, log: bool = False):
        self.played: list[str] = []
        self.stopped = 0
        self.log = log

    def play(self, path: str) -> None:

        self.played.append(path)

        if self.log:
            logger.info("[audio:null] %s", path)

    def stop(self) -> None:
        """Nothing to interrupt. Counted so a test can assert it was asked."""

        self.stopped += 1

    def is_available(self) -> bool:
        return True

    @property
    def last(self) -> str:
        return self.played[-1] if self.played else ""


# ----------------------------------------------------------------------
# Real playback
# ----------------------------------------------------------------------

# PowerShell, via WPF's MediaPlayer, which handles mp3 and ships with
# Windows. The file path travels in an environment variable rather than
# inside the script text, for the same reason the SAPI provider does it:
# no quoting in a filename can then escape into the shell.
WINDOWS_SCRIPT = (
    "Add-Type -AssemblyName presentationCore; "
    "$player = New-Object System.Windows.Media.MediaPlayer; "
    "$player.Open([uri]$env:AURA_AUDIO_FILE); "
    "$waited = 0; "
    "while (-not $player.NaturalDuration.HasTimeSpan -and $waited -lt 50) "
    "{ Start-Sleep -Milliseconds 100; $waited++ }; "
    "$player.Play(); "
    "if ($player.NaturalDuration.HasTimeSpan) "
    "{ Start-Sleep -Milliseconds "
    "([int]$player.NaturalDuration.TimeSpan.TotalMilliseconds + 300) }; "
    "$player.Close()"
)


class SystemAudioPlayer:
    """
    Plays a file with whatever this machine already has.

    `play` blocks until playback finishes, because the caller is a
    blocking `speak()` and the avatar's SPEAKING state lasts exactly as
    long as that call. `stop` is how another thread ends it early: the
    subprocess backends run through Popen rather than subprocess.run
    precisely so there is a handle to terminate.

    One backend cannot be interrupted. `playsound` is a blocking call
    into a library, with no process and no handle, so a cancel there
    takes effect after the current clip. Sentence-at-a-time streaming
    keeps that bounded to one sentence, and the subprocess backends -
    which is what Windows actually uses - stop immediately.
    """

    def __init__(self, timeout: float = MAX_PLAYBACK_SECONDS):
        self.timeout = timeout

        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._stopped = threading.Event()

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """
        End playback now. Safe from any thread, and safe when idle.

        Not part of the AudioPlayer protocol. That protocol is
        runtime_checkable, so adding a method would make every existing
        player - including one written by a user - fail isinstance.
        Callers look this up with getattr instead.
        """

        self._stopped.set()

        with self._lock:
            process = self._process

        if process is None:
            return

        try:
            process.terminate()
        except Exception as error:
            logger.debug("Could not terminate audio player: %s", error)

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        return self._backend() is not None

    def _backend(self) -> str | None:

        if _playsound() is not None:
            return "playsound"

        if os.name == "nt" and _powershell() is not None:
            return "windows"

        if _command_player() is not None:
            return "command"

        return None

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    def play(self, path: str) -> None:

        backend = self._backend()

        if backend is None:
            raise RuntimeError("No audio playback backend available")

        # A new clip clears the last cancel, exactly as the Edge provider
        # does: a stop that arrived while nothing was playing must not
        # silence the next reply.
        self._stopped.clear()

        if backend == "playsound":
            # No process, no handle. Uninterruptible by nature.
            _playsound()(path)
            return

        if backend == "windows":
            self._play_windows(path)
            return

        self._play_command(path)

    def _play_windows(self, path: str) -> None:

        shell = _powershell()

        environment = os.environ.copy()
        environment["AURA_AUDIO_FILE"] = os.path.abspath(path)

        self._run([shell, "-NoProfile", "-NonInteractive",
                   "-Command", WINDOWS_SCRIPT], environment)

    def _play_command(self, path: str) -> None:

        player = _command_player()

        self._run(player + [path], os.environ.copy())

    def _run(self, command: list[str], environment: dict) -> None:
        """
        Run a player to completion, keeping the handle so `stop` works.

        Popen rather than subprocess.run for exactly that reason: run()
        owns the process internally and gives no way to reach it, which
        makes cancellation impossible rather than merely awkward.
        """

        try:
            process = subprocess.Popen(
                command,
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )

        except OSError as error:
            raise RuntimeError(f"Audio playback failed: {error}") from error

        with self._lock:
            self._process = process

        try:
            # A stop that arrived between clearing the flag and starting
            # the process would otherwise be lost.
            if self._stopped.is_set():
                process.terminate()

            _, error_output = process.communicate(timeout=self.timeout)

        except subprocess.TimeoutExpired:
            logger.warning("Audio playback timed out")
            _end(process)
            return

        finally:
            with self._lock:
                self._process = None

        if self._stopped.is_set():
            # Terminated on purpose. A non-zero exit code here is the
            # cancel working, not playback failing.
            return

        if process.returncode:
            detail = (error_output or b"").decode(errors="replace").strip()
            raise RuntimeError(f"Audio playback failed: {detail}")


# ----------------------------------------------------------------------
# Backend discovery
# ----------------------------------------------------------------------

def _end(process) -> None:
    """
    Make sure a player process is gone, politely then not.

    A timed out or cancelled player that is never reaped holds the audio
    device, and the next reply is then silent for a reason that looks
    nothing like its cause.
    """

    try:
        process.terminate()
        process.communicate(timeout=STOP_GRACE_SECONDS)

    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.communicate(timeout=STOP_GRACE_SECONDS)
        except Exception as error:
            logger.debug("Could not kill audio player: %s", error)

    except Exception as error:
        logger.debug("Could not end audio player: %s", error)


def _playsound():
    """The `playsound` function, or None when the package is missing."""

    try:
        from playsound import playsound
    except Exception:
        return None

    return playsound


def _powershell() -> str | None:
    return shutil.which("powershell") or shutil.which("pwsh")


def _command_player() -> list[str] | None:
    """A command line player, in order of how quietly it exits."""

    if shutil.which("ffplay"):
        return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]

    if shutil.which("mpv"):
        return ["mpv", "--no-video", "--really-quiet"]

    if shutil.which("afplay"):
        return ["afplay"]

    return None


def create_audio_player(enabled: bool = True) -> AudioPlayer:
    """
    The player Aura should use, or a silent one.

    Never raises. An unplayable machine is a quiet companion, not a
    broken one.
    """

    if not enabled:
        return NullAudioPlayer()

    player = SystemAudioPlayer()

    if player.is_available():
        return player

    logger.info("No audio playback backend found, speech will be silent")

    return NullAudioPlayer()
