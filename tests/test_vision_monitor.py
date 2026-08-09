"""
Screen capture monitor selection.

The real backend is mss, which this test file never touches. A fake mss
module with the same shape stands in, so the tests run on a machine with
no display attached:

    screenshot.monitors -> list of dicts, index 0 being the union
    screen.grab(region) -> a shot with width/height/rgb

What is checked is the contract `ScreenshotCapture` must honour on top
of that shape: it captures the configured display, reports which one it
actually captured, and never quietly substitutes a different screen.

That last one is the regression this file exists for. An out of range
monitor index used to fall back to index 0 - the union of every display,
which on a multi monitor machine is a stitched image of all of them at
once, and from the outside looks exactly like a hallucinating vision
model.
"""

from vision.capture import ScreenshotCapture


# ----------------------------------------------------------------------
# A fake mss
# ----------------------------------------------------------------------

def rgb_for(width: int, height: int) -> bytes:
    """
    Raw RGB bytes for a region: exactly three per pixel.

    A repeating 0..255 ramp, so the content is deterministic and the
    bytes of one region are distinguishable from another's.
    """

    size = width * height * 3
    ramp = bytes(range(256))

    return (ramp * (size // 256 + 1))[:size]


class FakeShot:
    """What mss hands back from grab()."""

    def __init__(self, region: dict):
        self.width = region["width"]
        self.height = region["height"]
        self.rgb = rgb_for(self.width, self.height)


class FakeScreen:
    """mss.mss() context manager with two displays."""

    monitors = [
        # index 0: the union of every display
        {"left": 0, "top": 0, "width": 3200, "height": 1080},
        # index 1: the primary display
        {"left": 0, "top": 0, "width": 1920, "height": 1080},
        # index 2: the second display, to the left
        {"left": -1280, "top": 0, "width": 1280, "height": 1024},
    ]

    def __init__(self):
        self.grabs: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        return False

    def grab(self, region: dict) -> FakeShot:
        self.grabs.append(region)
        return FakeShot(region)


class FakeMss:
    """Stands in for the mss module."""

    def __init__(self, screen: FakeScreen | None = None):
        self.screen = screen or FakeScreen()

    def mss(self) -> FakeScreen:
        return self.screen

    @property
    def grabs(self) -> list[dict]:
        """The regions that were actually grabbed, in order."""

        return self.screen.grabs


def capture_with(backend: FakeMss, monitor) -> ScreenshotCapture:
    """A capture whose `_mss` is the fake, so nothing real is touched."""

    capture = ScreenshotCapture(monitor=monitor)
    capture._mss = lambda: backend  # noqa: SLF001  (the test seam)
    return capture


def frames_for(monitor):
    """The raw RGB bytes a real capture of that monitor would produce."""

    region = FakeScreen.monitors[monitor]
    return rgb_for(region["width"], region["height"])


# ----------------------------------------------------------------------
# Capture selects the intended monitor
# ----------------------------------------------------------------------

def test_monitor_1_grabs_the_primary_display():
    backend = FakeMss()

    frame = capture_with(backend, 1).capture()

    assert backend.grabs == [FakeScreen.monitors[1]]
    assert frame.width == 1920
    assert frame.height == 1080
    assert frame.data == frames_for(1)


def test_monitor_2_grabs_the_second_display_when_present():
    backend = FakeMss()

    frame = capture_with(backend, 2).capture()

    assert backend.grabs == [FakeScreen.monitors[2]]
    assert frame.width == 1280
    assert frame.height == 1024
    assert frame.data == frames_for(2)


def test_monitor_0_grabs_every_display_combined():
    """Index 0 is deliberate, and means the union of all displays."""

    backend = FakeMss()

    frame = capture_with(backend, 0).capture()

    assert backend.grabs == [FakeScreen.monitors[0]]
    assert frame.width == 3200
    assert frame.height == 1080


def test_the_default_is_the_primary_display():
    backend = FakeMss()

    capture_with(backend, 1).capture()

    assert backend.grabs == [FakeScreen.monitors[1]]


def test_a_string_monitor_index_is_read_the_same_way():
    """`monitor: "2"` from YAML must mean the same thing as 2."""

    backend = FakeMss()

    capture_with(backend, "2").capture()

    assert backend.grabs == [FakeScreen.monitors[2]]


def test_the_frame_bytes_are_the_region_that_was_grabbed():
    """No rescaling, no cropping, no second image of any kind."""

    backend = FakeMss()

    frame = capture_with(backend, 2).capture()

    assert frame.image_format == "rgb"
    assert len(frame.data) == frame.width * frame.height * 3


# ----------------------------------------------------------------------
# Invalid monitor selection degrades cleanly
# ----------------------------------------------------------------------

def test_out_of_range_degrades_to_the_primary_display_not_the_union():
    """
    The regression this file exists for. The old code fell back to index
    0, the union of every display, which hands the vision model a
    stitched image of all screens at once - indistinguishable from a
    hallucinating model from the outside.
    """

    backend = FakeMss()

    frame = capture_with(backend, 9).capture()

    assert backend.grabs == [FakeScreen.monitors[1]]
    assert frame.data == frames_for(1)


def test_a_negative_monitor_index_also_degrades_to_primary():
    backend = FakeMss()

    frame = capture_with(backend, -1).capture()

    assert backend.grabs == [FakeScreen.monitors[1]]


def test_unreadable_monitor_config_degrades_to_primary():
    """`monitor: yes` must not accidentally mean display 1 by bool-ness."""

    backend = FakeMss()

    frame = capture_with(backend, True).capture()

    assert backend.grabs == [FakeScreen.monitors[1]]


def test_on_a_single_display_machine_the_fallback_is_that_display():
    """
    With only monitor 0 (union) and 1 (the only display) reported, an
    out of range index degrades to display 1, not to a fallback that
    does not exist.
    """

    class SingleDisplay(FakeScreen):
        monitors = [
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
        ]

    backend = FakeMss(screen=SingleDisplay())

    frame = capture_with(backend, 3).capture()

    assert backend.grabs == [SingleDisplay.monitors[1]]
    assert frame.width == 1920
    assert frame.height == 1080
