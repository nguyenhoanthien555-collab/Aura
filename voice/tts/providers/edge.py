"""
Microsoft Edge text to speech.

The nicest voice Aura can have without an API key: `edge-tts` reaches the
same neural voices Edge's Read Aloud uses, streams back an mp3, and costs
nothing. It does need a network connection, which is why it is not the
"auto" default - a local companion should not go mute when the wifi does.

Two collaborators, both injected:

    synthesis   edge_tts.Communicate, imported lazily
    playback    voice.tts.audio.AudioPlayer

Splitting them is what makes this testable. A test passes a fake
synthesiser and a NullAudioPlayer and asserts on the arguments; no
network, no speaker, no temporary file left behind.

Voice defaults live in core.config, not here. The constructor's defaults
exist so the class is usable on its own, and are the same values the
config ships with.

Value coercion lives in voice.tts.values and is re-exported at the bottom
of this module, because tests and older code import it from here.
"""

import os
import asyncio
import tempfile
import threading

from core.logger import logger
from voice.tts.audio import AudioPlayer, NullAudioPlayer, create_audio_player
from voice.tts.values import (
    HERTZ,
    PERCENT,
    normalise_hertz,
    normalise_percent,
    number_in,
    shift_hertz,
    shift_percent,
)


# en-US-AvaMultilingualNeural: warm, expressive, conversational rather
# than newsreader. Multilingual, so a Vietnamese sentence in the middle
# of an English reply is pronounced rather than spelled out.
DEFAULT_VOICE = "en-US-AvaMultilingualNeural"

# Slightly quicker and slightly brighter than neutral. Enough to read as
# a person talking to a friend, small enough not to sound sped up.
DEFAULT_RATE = "+5%"
DEFAULT_PITCH = "+10Hz"
DEFAULT_VOLUME = "+0%"

# Synthesis is a network round trip. Past this something is wrong, and
# waiting longer only delays the fallback to a silent reply.
SYNTHESIS_TIMEOUT = 60.0


class EdgeTTSProvider:
    """
    Speaks through Edge's neural voices.

    `speak` blocks until playback finishes, which is what the TTSEngine
    and the avatar's SPEAKING state both assume. `cancel` is the way out
    of that block from another thread - see the Cancellation section.
    """

    def __init__(
        self,
        voice: str = DEFAULT_VOICE,
        rate: str | int | float = DEFAULT_RATE,
        pitch: str | int | float = DEFAULT_PITCH,
        volume: str | int | float = DEFAULT_VOLUME,
        player: AudioPlayer | None = None,
        synthesizer=None,
        timeout: float = SYNTHESIS_TIMEOUT,
    ):

        # Stripped before the fallback, not after: "   " is truthy, so
        # falling back first would leave a blank voice that edge-tts
        # cannot resolve.
        self.voice = (voice or "").strip() or DEFAULT_VOICE

        # Normalised rather than validated. A config carried over from
        # the SAPI provider says `rate: 0`, which edge-tts would reject
        # outright; turning it into "+0%" is friendlier than refusing to
        # speak over a units mismatch.
        self.rate = normalise_percent(rate, DEFAULT_RATE)
        self.pitch = normalise_hertz(pitch, DEFAULT_PITCH)
        self.volume = normalise_percent(volume, DEFAULT_VOLUME)

        # The settings as configured, kept so a mood can shift them
        # without consuming them. `rate` above is the effective value and
        # is what synthesis reads; these two are what it returns to.
        self._base_rate = self.rate
        self._base_pitch = self.pitch

        self.player = player if player is not None else create_audio_player()
        self.synthesizer = synthesizer
        self.timeout = timeout

        self._cancelled = threading.Event()

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    def cancel(self) -> None:
        """
        Stop the utterance in flight.

        Graceful rather than immediate, and the distinction is
        deliberate: nothing is killed mid-write. `speak` checks between
        its two stages, so a cancel during synthesis discards the audio
        instead of playing it, and a cancel during playback goes to the
        player, which stops the process it started. The temporary file is
        removed either way, because that cleanup lives in a `finally`.

        The window is therefore bounded by whichever stage is running,
        not by the length of the sentence. Safe to call from any thread,
        and safe to call when nothing is speaking.

        Found by TTSEngine through getattr rather than declared on the
        TTSProvider protocol, for the same reason `set_pacing` is: that
        protocol is runtime_checkable, and widening it would break
        isinstance for every provider that does not implement this.
        """

        self._cancelled.set()

        stop = getattr(self.player, "stop", None)

        if stop is None:
            # A player with no stop finishes the clip. Cancelling still
            # took effect for anything queued behind it.
            return

        try:
            stop()
        except Exception as error:
            logger.debug("Player refused stop: %s", error)

    @property
    def cancelled(self) -> bool:
        """True when a cancel is pending for the utterance in flight."""

        return self._cancelled.is_set()

    # ------------------------------------------------------------------
    # Pacing
    # ------------------------------------------------------------------

    def set_pacing(self, pacing) -> None:
        """
        Shift rate and pitch by a mood's offsets.

        Found by TTSEngine through getattr rather than declared on the
        TTSProvider protocol - that protocol is runtime_checkable, and
        widening it would break isinstance for every provider that does
        not implement this one.

        Offsets apply to the configured values, never to the current
        ones, so moods do not compound: ten teasing replies in a row
        leave her speaking at teasing pace, not at ten times it.
        """

        self.rate = shift_percent(self._base_rate, getattr(pacing, "rate", 0))
        self.pitch = shift_hertz(self._base_pitch, getattr(pacing, "pitch", 0))

    def clear_pacing(self) -> None:
        """Back to the configured voice."""

        self.rate = self._base_rate
        self.pitch = self._base_pitch

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """
        True when speech could actually be heard.

        Deliberately not a network check. Reachability is not knowable
        cheaply or reliably at startup, so a failed synthesis is handled
        where it happens - as a warning and a silent turn - rather than
        gating the provider here.
        """

        if self.synthesizer is None and _load_edge_tts() is None:
            return False

        return _player_available(self.player)

    # ------------------------------------------------------------------
    # Speaking
    # ------------------------------------------------------------------

    def speak(self, text: str) -> None:
        """
        Synthesise `text` and play it, blocking until it is done.

        Two cancellation checkpoints, one before each expensive stage.
        A new utterance clears any stale cancel first: a `cancel()` that
        arrived while nothing was speaking must not mute the next reply,
        because the failure mode of a permanently silent companion is far
        worse than the failure mode of one extra spoken sentence.
        """

        text = (text or "").strip()

        if not text:
            return

        self._cancelled.clear()

        path = self._temporary_file()

        try:
            if self._cancelled.is_set():
                return

            self.synthesize(text, path)

            # Synthesis is the long stage. A cancel that landed during it
            # means the audio is already unwanted, so it is dropped
            # rather than played to an empty room.
            if self._cancelled.is_set():
                logger.debug("Speech cancelled before playback")
                return

            self.player.play(path)

        finally:
            _remove(path)

    def synthesize(self, text: str, path: str) -> str:
        """
        Write spoken audio for `text` to `path`.

        Separate from `speak` so a caller that wants the file - a future
        avatar with lip sync, a save-to-mp3 command - does not have to
        play it.
        """

        synthesize = self.synthesizer

        if synthesize is None:
            synthesize = _edge_synthesize

        synthesize(
            text=text,
            path=path,
            voice=self.voice,
            rate=self.rate,
            pitch=self.pitch,
            volume=self.volume,
            timeout=self.timeout,
        )

        return path

    @staticmethod
    def _temporary_file() -> str:

        handle, path = tempfile.mkstemp(prefix="aura_tts_", suffix=".mp3")
        os.close(handle)

        return path

    def __repr__(self) -> str:
        return (
            f"EdgeTTSProvider(voice={self.voice!r}, "
            f"rate={self.rate!r}, pitch={self.pitch!r})"
        )


# ----------------------------------------------------------------------
# Synthesis
# ----------------------------------------------------------------------

def _edge_synthesize(
    text: str,
    path: str,
    voice: str,
    rate: str,
    pitch: str,
    volume: str,
    timeout: float,
) -> None:
    """
    Run edge-tts and leave an mp3 at `path`.

    edge-tts is async only, and Aura's TTSEngine is synchronous, so the
    coroutine is driven to completion here. The engine already runs off
    the conversation's critical path, so blocking is correct - the
    alternative would be a reply that is heard after the next one.
    """

    edge_tts = _load_edge_tts()

    if edge_tts is None:
        raise RuntimeError(
            "edge-tts is not installed. Run: pip install edge-tts"
        )

    async def run() -> None:

        communicate = edge_tts.Communicate(
            text,
            voice=voice,
            rate=rate,
            pitch=pitch,
            volume=volume,
        )

        await communicate.save(path)

    _run_coroutine(run(), timeout)

    if not os.path.exists(path) or os.path.getsize(path) == 0:
        raise RuntimeError("Edge TTS returned no audio")


def _run_coroutine(coroutine, timeout: float) -> None:
    """
    Drive a coroutine from synchronous code, loop or no loop.

    `asyncio.run` is the normal path. It raises if a loop is already
    running on this thread, which happens when Aura is embedded in an
    async host, so that case gets its own thread with its own loop
    rather than a "cannot be called from a running event loop" crash.
    """

    try:
        asyncio.get_running_loop()

    except RuntimeError:
        asyncio.run(asyncio.wait_for(coroutine, timeout))
        return

    error: list[BaseException] = []

    def worker() -> None:
        try:
            asyncio.run(asyncio.wait_for(coroutine, timeout))
        except BaseException as caught:      # re-raised on the caller's thread
            error.append(caught)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout + 5.0)

    if error:
        raise error[0]


def _load_edge_tts():
    """The edge_tts module, or None when it is not installed."""

    try:
        import edge_tts
    except Exception:
        return None

    return edge_tts


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _player_available(player) -> bool:

    check = getattr(player, "is_available", None)

    if check is None:
        return True

    try:
        return bool(check())
    except Exception:
        return False


def _remove(path: str) -> None:

    try:
        os.unlink(path)
    except OSError:
        pass


__all__ = [
    "EdgeTTSProvider",
    "DEFAULT_VOICE",
    "DEFAULT_RATE",
    "DEFAULT_PITCH",
    "DEFAULT_VOLUME",
    "NullAudioPlayer",
    # Re-exported from voice.tts.values for backward compatibility
    "PERCENT",
    "HERTZ",
    "normalise_percent",
    "normalise_hertz",
    "number_in",
    "shift_percent",
    "shift_hertz",
]
