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


class ScreenshotCapture:
    """
    Real screen capture through `mss`.

    Optional dependency, imported lazily. A failed grab returns None
    rather than raising - a missed frame is not worth an exception.
    """

    def __init__(self, monitor: int = 1):

        self.monitor = monitor

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

    def capture(self) -> Frame | None:

        try:
            mss = self._mss()

            with mss.mss() as screen:

                monitors = screen.monitors

                index = self.monitor

                if index >= len(monitors):
                    index = 0

                shot = screen.grab(monitors[index])

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
