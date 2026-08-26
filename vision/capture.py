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

    `data` is raw bytes in whatever `image_format` says. Two things read
    it: `encode_png` below, which requires "rgb", and the vision
    processors, which send it to a model - `CloudVisionProcessor._compact`
    handles both "rgb" (straight from a desktop backend) and an already
    encoded "png"/"jpeg" (the phone).

    `source` says where the pixels came from - "screen", "phone", "mock" -
    so a processor can describe them honestly rather than assuming.
    """

    width: int = 0
    height: int = 0
    data: bytes = b""
    image_format: str = "raw"
    source: str = "screen"

    def is_empty(self) -> bool:
        return not self.data


def encode_png(frame: Frame) -> bytes:
    """
    A raw-RGB `Frame` as PNG bytes, with nothing installed.

    `zlib` and `struct` are standard library, so this works on a stock
    machine. That matters because it is what the `take_screenshot` tool
    writes, and every other tool in the PC layer - window enumeration,
    command execution, filesystem writes - needs no dependency either. A
    screenshot that required `pillow` would be the one capability an owner
    could not use without installing something, for no reason the format
    demands: PNG is four chunks and a `zlib` stream.

    `OllamaVisionProcessor._to_png` still uses PIL, and is deliberately
    left alone. It round-trips already-encoded frames through `Image.open`
    to guarantee the model gets one known format, which is more than this
    does, and rewriting a tested path that works is churn. What it should
    eventually do is fall back to this when PIL is missing - recorded
    rather than done here.

    Raises ValueError for anything that is not raw RGB. An already
    encoded frame is not re-encoded and not passed through silently: a
    caller who hands over a JPEG and gets bytes back that are still a
    JPEG under a `.png` name has been told something untrue about their
    own file.
    """

    import struct                       # noqa: PLC0415
    import zlib                         # noqa: PLC0415

    if frame.image_format != "rgb":
        raise ValueError(
            f"can only encode raw rgb frames, not {frame.image_format!r}"
        )

    width, height = int(frame.width), int(frame.height)

    if width <= 0 or height <= 0:
        raise ValueError(f"frame has no area: {width}x{height}")

    expected = width * height * 3

    if len(frame.data) != expected:
        raise ValueError(
            f"frame data is {len(frame.data)} bytes, "
            f"expected {expected} for {width}x{height} rgb"
        )

    # PNG wants every scanline prefixed with its filter type. 0 is "no
    # filter", which costs some compression and no correctness, and keeps
    # this short enough to read in one sitting.
    stride = width * 3

    raw = bytearray()

    for row in range(height):
        start = row * stride
        raw.append(0)
        raw += frame.data[start:start + stride]

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(
        ">IIBBBBB",
        width,
        height,
        8,          # bit depth
        2,          # colour type 2 = truecolour rgb
        0,          # deflate
        0,          # adaptive filtering
        0,          # no interlace
    )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )


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


class GdiScreenCapture:
    """
    Real screen capture through GDI, with nothing installed.

    Satisfies the same `ScreenCapture` protocol as `ScreenshotCapture` and
    honours the same `monitor` indices, so it is a drop-in for it. It
    exists because `mss` is an optional dependency that is not installed
    on the owner's machine, which meant the pixel half of vision - and
    every screenshot - was dead code on the only machine Aura actually
    runs on. `BitBlt` and `GetDIBits` are in gdi32, which is always there,
    the same way `list_windows` reads user32 rather than installing a
    window library.

    Not a replacement: `mss` is preferred when present. It is faster, it
    handles some display topologies this does not, and replacing a working
    dependency with a hand-rolled one would be the speculative rewrite
    Section 41 forbids. This is the fallback that makes the feature exist
    at all without it.

    Two details are easy to get wrong and are worth naming.

    **DPI.** A process that has not declared DPI awareness is lied to by
    Windows about the size of the screen: on a display scaled to 150%,
    `GetSystemMetrics` reports 1280x720 for a 1920x1080 panel and `BitBlt`
    hands back a stretched approximation. The fix is awareness, and the
    scope of the fix matters - `SetProcessDpiAwarenessContext` mutates the
    whole process permanently, which is not a decision a single tool call
    should make on behalf of everything else in it. `SetThreadDpiAwareness\
Context` is used instead, set immediately before the grab and restored
    immediately after, so the capture sees true pixels and nothing outside
    it changes. Measured on the owner's machine: the call succeeds and
    returns a restorable previous context. Its *benefit* could not be
    measured there, because that display runs at 100% scaling where aware
    and unaware report the same numbers - so the awareness handling is
    correct by construction and unverified by observation, which is worth
    saying plainly rather than claiming a fix that was never seen to fix
    anything.

    **Row order.** A negative `biHeight` asks GDI for top-down rows. With
    a positive height the rows arrive bottom-up, which produces a
    vertically mirrored image that is otherwise perfectly valid - the kind
    of defect that survives every test that only checks byte counts.
    """

    # GDI constants. Named rather than inlined for the same reason
    # `SW_RESTORE` is named in `tools/builtins/desktop.py`: a bare
    # 0x00CC0020 in a call is unreadable.
    SRCCOPY = 0x00CC0020
    DIB_RGB_COLORS = 0
    BI_RGB = 0
    BITS_PER_PIXEL = 32

    # Per-monitor-aware v2, as a DPI_AWARENESS_CONTEXT. The API takes an
    # opaque handle whose documented values are these small negatives.
    DPI_PER_MONITOR_AWARE_V2 = -4

    MONITORINFOF_PRIMARY = 1

    def __init__(self, monitor: int = PRIMARY_DISPLAY):

        self.monitor = monitor

        # One warning per capture object, not one per frame - the same
        # reason `ScreenshotCapture` keeps this flag.
        self._warned = False

        self._bound = None

    # ------------------------------------------------------------------

    def _bind(self):
        """
        user32 and gdi32 with signatures declared, or None off Windows.

        Cached, because the argtypes assignments mutate shared function
        objects and repeating them once per frame is pointless work.
        """

        if self._bound is not None:
            return self._bound

        if os.name != "nt":
            return None

        try:
            import ctypes               # noqa: PLC0415
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32

            user32.GetSystemMetrics.argtypes = [ctypes.c_int]
            user32.GetSystemMetrics.restype = ctypes.c_int

            user32.GetDC.argtypes = [wintypes.HWND]
            user32.GetDC.restype = wintypes.HDC

            user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
            user32.ReleaseDC.restype = ctypes.c_int

            gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
            gdi32.CreateCompatibleDC.restype = wintypes.HDC

            gdi32.CreateCompatibleBitmap.argtypes = [
                wintypes.HDC, ctypes.c_int, ctypes.c_int
            ]
            gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP

            gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
            gdi32.SelectObject.restype = wintypes.HGDIOBJ

            gdi32.BitBlt.argtypes = [
                wintypes.HDC, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int,
                wintypes.HDC, ctypes.c_int, ctypes.c_int,
                wintypes.DWORD,
            ]
            gdi32.BitBlt.restype = ctypes.c_bool

            gdi32.GetDIBits.argtypes = [
                wintypes.HDC, wintypes.HBITMAP, ctypes.c_uint,
                ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
                ctypes.c_uint,
            ]
            gdi32.GetDIBits.restype = ctypes.c_int

            gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
            gdi32.DeleteObject.restype = ctypes.c_bool

            gdi32.DeleteDC.argtypes = [wintypes.HDC]
            gdi32.DeleteDC.restype = ctypes.c_bool

            self._bound = (ctypes, wintypes, user32, gdi32)

            return self._bound

        except Exception as error:
            logger.debug("gdi32/user32 unavailable: %s", error)
            return None

    def is_available(self) -> bool:

        return self._bind() is not None

    # ------------------------------------------------------------------

    def _monitors(self) -> list[dict]:
        """
        Every display, in the index order `ScreenshotCapture` uses.

        Index 0 is the union of all displays, matching what mss puts at
        `monitors[0]`, so a configured `monitor` means the same display
        whichever backend is installed. The union is computed from the
        enumerated rectangles rather than read from the virtual-screen
        metrics: both answers agreed on the owner's single-display machine,
        and the enumerated one cannot disagree with the per-display entries
        it was derived from.
        """

        bound = self._bind()

        if bound is None:
            return []

        ctypes, wintypes, user32, _gdi32 = bound

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long),
            ]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                ("rcWork", RECT), ("dwFlags", wintypes.DWORD),
            ]

        callback = ctypes.WINFUNCTYPE(
            ctypes.c_bool, wintypes.HMONITOR, wintypes.HDC,
            ctypes.POINTER(RECT), wintypes.LPARAM,
        )

        user32.EnumDisplayMonitors.argtypes = [
            wintypes.HDC, ctypes.POINTER(RECT), callback, wintypes.LPARAM
        ]
        user32.EnumDisplayMonitors.restype = ctypes.c_bool

        user32.GetMonitorInfoW.argtypes = [
            wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)
        ]
        user32.GetMonitorInfoW.restype = ctypes.c_bool

        displays: list[dict] = []

        def visit(handle, _dc, _rect, _parameter) -> bool:

            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)

            if user32.GetMonitorInfoW(handle, ctypes.byref(info)):

                area = info.rcMonitor

                displays.append({
                    "left": area.left,
                    "top": area.top,
                    "width": area.right - area.left,
                    "height": area.bottom - area.top,
                    "primary": bool(
                        info.dwFlags & self.MONITORINFOF_PRIMARY
                    ),
                })

            # Keep enumerating. Returning False here would stop at the
            # first display and quietly hide every other one.
            return True

        try:
            user32.EnumDisplayMonitors(None, None, callback(visit), 0)
        except Exception as error:
            logger.debug("EnumDisplayMonitors failed: %s", error)
            return []

        if not displays:
            return []

        # The primary display first, so index 1 is the primary exactly as
        # mss reports it. Enumeration order is not documented to put it
        # first, and on a machine where it does not, `monitor: 1` would
        # silently mean a different screen.
        displays.sort(key=lambda entry: not entry["primary"])

        union = {
            "left": min(entry["left"] for entry in displays),
            "top": min(entry["top"] for entry in displays),
            "width": (
                max(entry["left"] + entry["width"] for entry in displays)
                - min(entry["left"] for entry in displays)
            ),
            "height": (
                max(entry["top"] + entry["height"] for entry in displays)
                - min(entry["top"] for entry in displays)
            ),
            "primary": False,
        }

        return [union] + displays

    def _resolve(self, monitors) -> int:
        """
        The index to grab, given what this machine actually reports.

        Deliberately the same behaviour and the same warning as
        `ScreenshotCapture._resolve`: an index no display answers to is a
        configuration mistake, and falling back to the union of every
        display would hand a describing model a stitched image of all of
        them, which looks exactly like a hallucination from the outside.
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
                "Vision: monitor %r does not exist (%d reported by gdi), "
                "capturing monitor %d instead",
                self.monitor,
                len(monitors),
                fallback,
            )

        return fallback

    def capture(self) -> Frame | None:
        """
        One frame of the chosen display as raw RGB, or None.

        None rather than an exception for a failed grab, matching
        `ScreenshotCapture`: a missed frame is not worth unwinding a
        caller that will ask again in two seconds.
        """

        bound = self._bind()

        if bound is None:
            return None

        ctypes, wintypes, user32, gdi32 = bound

        monitors = self._monitors()

        if not monitors:
            logger.debug("Screen capture failed: no displays reported")
            return None

        region = monitors[self._resolve(monitors)]

        width, height = int(region["width"]), int(region["height"])

        if width <= 0 or height <= 0:
            logger.debug("Screen capture failed: display has no area")
            return None

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD),
                ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [
                ("bmiHeader", BITMAPINFOHEADER),
                ("bmiColors", wintypes.DWORD * 3),
            ]

        restore_dpi = self._aware(ctypes, user32)

        screen_dc = None
        memory_dc = None
        bitmap = None
        previous = None

        try:
            screen_dc = user32.GetDC(None)

            if not screen_dc:
                logger.debug("Screen capture failed: no screen DC")
                return None

            memory_dc = gdi32.CreateCompatibleDC(screen_dc)
            bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)

            if not memory_dc or not bitmap:
                logger.debug("Screen capture failed: no memory bitmap")
                return None

            previous = gdi32.SelectObject(memory_dc, bitmap)

            copied = gdi32.BitBlt(
                memory_dc, 0, 0, width, height,
                screen_dc, region["left"], region["top"],
                self.SRCCOPY,
            )

            if not copied:
                logger.debug("Screen capture failed: BitBlt refused")
                return None

            info = BITMAPINFO()
            info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            info.bmiHeader.biWidth = width
            # Negative: top-down rows. Positive would give a valid but
            # vertically mirrored image.
            info.bmiHeader.biHeight = -height
            info.bmiHeader.biPlanes = 1
            info.bmiHeader.biBitCount = self.BITS_PER_PIXEL
            info.bmiHeader.biCompression = self.BI_RGB

            buffer = ctypes.create_string_buffer(width * height * 4)

            rows = gdi32.GetDIBits(
                memory_dc, bitmap, 0, height, buffer,
                ctypes.byref(info), self.DIB_RGB_COLORS,
            )

            if rows != height:
                logger.debug(
                    "Screen capture failed: GetDIBits returned %s of %s rows",
                    rows, height,
                )
                return None

            logger.debug(
                "Vision: captured monitor %d, %dx%d at (%s, %s) via gdi",
                self._resolve(monitors), width, height,
                region["left"], region["top"],
            )

            return Frame(
                width=width,
                height=height,
                data=_bgrx_to_rgb(buffer.raw),
                image_format="rgb",
                source="screen",
            )

        except Exception as error:
            logger.debug("Screen capture failed: %s", error)
            return None

        finally:
            # Every handle, in reverse order, whatever happened above. A
            # leaked DC or bitmap is a per-capture leak in a loop that
            # runs for the life of the process.
            try:
                if memory_dc and previous:
                    gdi32.SelectObject(memory_dc, previous)
                if bitmap:
                    gdi32.DeleteObject(bitmap)
                if memory_dc:
                    gdi32.DeleteDC(memory_dc)
                if screen_dc:
                    user32.ReleaseDC(None, screen_dc)
            except Exception as error:
                logger.debug("Screen capture cleanup failed: %s", error)

            restore_dpi()

    def _aware(self, ctypes, user32):
        """
        Become per-monitor DPI aware for this thread; return the undo.

        Thread-scoped on purpose. The process-wide call is permanent and
        affects every other thing in the process, which is not a choice a
        screenshot should make for the whole application.

        Returns a callable either way, so the caller's `finally` has
        nothing to check. On a Windows old enough to lack the API, or if
        the call is refused, the undo is a no-op and the capture proceeds
        unaware - a possibly-scaled screenshot is better than none.
        """

        try:
            user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
            user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p

            previous = user32.SetThreadDpiAwarenessContext(
                ctypes.c_void_p(self.DPI_PER_MONITOR_AWARE_V2)
            )

            if not previous:
                return lambda: None

            def restore() -> None:
                try:
                    user32.SetThreadDpiAwarenessContext(
                        ctypes.c_void_p(previous)
                    )
                except Exception as error:
                    logger.debug("DPI awareness not restored: %s", error)

            return restore

        except Exception as error:
            logger.debug("Thread DPI awareness unavailable: %s", error)
            return lambda: None


def _bgrx_to_rgb(raw: bytes) -> bytes:
    """
    32-bit BGRX rows as packed 24-bit RGB.

    GDI hands back blue, green, red, unused. `Frame(image_format="rgb")`
    means red, green, blue with no padding, which is what
    `OllamaVisionProcessor._to_png` already assumes and what `encode_png`
    writes. Converting here rather than inventing a fourth image format
    keeps one meaning for "rgb" across both capture backends.

    A slice-assigned bytearray rather than a comprehension: this runs over
    two million pixels per frame, and the slice form does the striding in
    C instead of in a Python loop.
    """

    pixels = bytearray(raw)

    out = bytearray(len(raw) // 4 * 3)

    out[0::3] = pixels[2::4]        # red   <- third byte
    out[1::3] = pixels[1::4]        # green <- second byte
    out[2::3] = pixels[0::4]        # blue  <- first byte

    return bytes(out)


def default_screen_capture(monitor: int = PRIMARY_DISPLAY):
    """
    The best pixel capture available here, or None.

    Preference order, and each step is a deliberate choice:

        mss     when installed - faster, better tested, someone else's
                problem to maintain
        gdi     on Windows with nothing installed - the reason screen
                capture works on the owner's machine at all
        None    everywhere else, so callers fall back to window titles

    None rather than `MockScreenCapture`, following the factory rule the
    tool layer already uses: a capability whose backing is absent should be
    missing rather than present and inventing 1920x1080 frames of nothing.
    A mock capture wired in by default would let a screenshot tool report
    success having written a blank image.
    """

    candidate = ScreenshotCapture(monitor=monitor)

    if candidate.is_available():
        return candidate

    gdi = GdiScreenCapture(monitor=monitor)

    if gdi.is_available():
        return gdi

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
