"""
Screen capture.

Two independent observations are available, and either can be used
alone:

    Frame           pixels, for a future image model
    active window   the foreground window title, cheap and text only

The window title alone already answers "what is the user doing right
now" well enough to be useful in a prompt, and it needs no dependency,
no GPU and no per frame cost. Pixel capture is optional.
"""

import os
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from core.logger import logger


@dataclass(frozen=True)
class Frame:
    """
    A captured image.

    `data` is raw bytes in whatever `image_format` says. Nothing in Aura
    decodes it today - it exists so an image model can be added later
    without reshaping the pipeline.
    """

    width: int = 0
    height: int = 0
    data: bytes = b""
    image_format: str = "raw"
    source: str = "screen"

    def is_empty(self) -> bool:
        return not self.data


@runtime_checkable
class ScreenCapture(Protocol):

    def capture(self) -> Frame | None:
        ...

    def is_available(self) -> bool:
        ...


class MockScreenCapture:
    """
    Capture that invents frames.

    Used by every test and as the fallback when no capture backend is
    installed, so the vision pipeline can be exercised end to end with
    no display attached.
    """

    def __init__(
        self,
        frames: list[Frame] | None = None,
        available: bool = True,
    ):

        self.frames = list(frames or [])
        self.captures = 0
        self._available = available

    def capture(self) -> Frame | None:

        self.captures += 1

        if self.frames:
            return self.frames.pop(0)

        return Frame(width=1920, height=1080, source="mock")

    def is_available(self) -> bool:
        return self._available


# mss monitor indices. 0 is every display stitched into a single wide
# image; 1 is the primary display; 2 and up are the other displays in the
# order mss reports them.
ALL_DISPLAYS = 0
PRIMARY_DISPLAY = 1


def _as_index(value) -> int | None:
    """
    `value` as an mss monitor index, or None when it is not one.

    YAML hands back whatever was written, so `monitor: "2"` arrives as a
    string. Reading it is friendlier than grabbing nothing over a pair of
    quotes. `True` is rejected explicitly - bool is an int subclass, and
    `monitor: yes` meaning "display 1" would be an accident, not a
    choice.
    """

    if isinstance(value, bool):
        return None

    if isinstance(value, int):
        return value

    try:
        return int(str(value).strip())

    except (TypeError, ValueError):
        return None


class ScreenshotCapture:
    """
    Real screen capture through `mss`.

    Optional dependency, imported lazily. A failed grab returns None
    rather than raising - a missed frame is not worth an exception.

    `monitor` is an mss index, and picking the wrong one is the single
    most common way Aura ends up describing a screen the user is not
    looking at:

        0   every display combined into one wide image
        1   the primary display
        2+  the other displays, in mss order

    An index no display answers to is a configuration mistake rather
    than a reason to guess quietly. See `_resolve`.
    """

    def __init__(self, monitor: int = PRIMARY_DISPLAY):

        self.monitor = monitor

        # One warning per capture object, not one per frame. At the
        # default two second throttle an unconditional warning would
        # write thirty lines a minute for the whole session.
        self._warned = False

    @staticmethod
    def _mss():
        import mss                  # noqa: PLC0415  (optional dependency)

        return mss

    def is_available(self) -> bool:

        try:
            self._mss()
            return True
        except Exception:
            return False

    def _resolve(self, monitors) -> int:
        """
        The index to grab, given what mss actually reports.

        Out of range and negative both mean the configured display does
        not exist. The old behaviour silently fell back to index 0, the
        union of every display - which on a multi monitor machine hands
        the vision model a stitched image of every screen at once and
        looks, from the outside, exactly like a hallucinating model.

        The primary display is used instead, and it is logged, because a
        wrong screen described confidently is worse than a right screen
        described plainly.
        """

        index = _as_index(self.monitor)

        if index is not None and 0 <= index < len(monitors):
            return index

        fallback = (
            PRIMARY_DISPLAY
            if len(monitors) > PRIMARY_DISPLAY
            else ALL_DISPLAYS
        )

        if not self._warned:
            self._warned = True

            logger.warning(
                "Vision: monitor %r does not exist (%d reported by mss), "
                "capturing monitor %d instead",
                self.monitor,
                len(monitors),
                fallback,
            )

        return fallback

    def capture(self) -> Frame | None:

        try:
            mss = self._mss()

            with mss.mss() as screen:

                monitors = screen.monitors

                index = self._resolve(monitors)

                region = monitors[index]

                shot = screen.grab(region)

                # Which display this frame is actually of. The cheapest
                # way to tell a wrong-monitor bug from a wrong-model one,
                # and it logs geometry only - never image data.
                logger.debug(
                    "Vision: captured monitor %d, %dx%d at (%s, %s)",
                    index,
                    shot.width,
                    shot.height,
                    region.get("left", "?"),
                    region.get("top", "?"),
                )

                return Frame(
                    width=shot.width,
                    height=shot.height,
                    data=bytes(shot.rgb),
                    image_format="rgb",
                    source="screen",
                )

        except Exception as error:
            logger.debug("Screen capture failed: %s", error)
            return None


# ----------------------------------------------------------------------
# Active window
# ----------------------------------------------------------------------

@runtime_checkable
class WindowReader(Protocol):

    def active_window(self) -> str:
        ...


class MockWindowReader:

    def __init__(self, titles: list[str] | None = None, title: str = ""):

        self.titles = list(titles or [])
        self.title = title
        self.calls = 0

    def active_window(self) -> str:

        self.calls += 1

        if self.titles:
            return self.titles.pop(0)

        return self.title


class WindowsWindowReader:
    """
    Foreground window title via user32, through ctypes.

    No dependency: ctypes is standard library and user32 is part of
    Windows. On any other platform this returns an empty string, which
    the processor treats as "nothing observed".
    """

    def active_window(self) -> str:

        if os.name != "nt":
            return ""

        try:
            import ctypes           # noqa: PLC0415

            user32 = ctypes.windll.user32

            handle = user32.GetForegroundWindow()

            if not handle:
                return ""

            length = user32.GetWindowTextLengthW(handle)

            if length <= 0:
                return ""

            buffer = ctypes.create_unicode_buffer(length + 1)

            user32.GetWindowTextW(handle, buffer, length + 1)

            return buffer.value or ""

        except Exception as error:
            logger.debug("Active window lookup failed: %s", error)
            return ""


def default_window_reader() -> WindowReader:
    """The best window reader for this platform."""

    if os.name == "nt":
        return WindowsWindowReader()

    return MockWindowReader()
