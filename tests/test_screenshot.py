"""
Screenshots (phase 18.4, section 24).

One DANGEROUS tool, one new capture backend, one PNG encoder, and the
tests are weighted towards the three things that can be wrong in ways no
byte count would show.

  * **The picture is a picture.** `GetDIBits` returning the right number
    of bytes is exactly the sentence section 11 forbids resting on: a
    vertically mirrored image, or one with red and blue swapped, has the
    identical length. So the pixels are compared against PIL's
    `ImageGrab`, an independent implementation of the same capture, and
    also against that reference deliberately flipped and channel-swapped,
    which must match *worse*.

  * **Nothing is captured on a path that will be refused.** The screen is
    the most sensitive thing this layer touches. `execute` proves the
    destination before it reads a single pixel, and a counting capture
    asserts the order - because a screenshot held in memory on a failure
    path is a privacy leak that leaves no trace to find later.

  * **The image only ever lands where the owner allowed writing.** Same
    containment as 18.3, imported rather than reimplemented, and tested
    the same way: traversal, absolute, and a directory junction, with the
    file outside checked byte-for-byte afterwards.

The encoder gets its own attention because it is hand-rolled. Every
structural claim about the PNG is checked by decoding it with PIL rather
than by re-reading my own bytes back with my own assumptions.
"""

import io
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from tools.base import ToolProtocol, ToolRisk
from tools.builtins.screen import PNG_MAGIC, ScreenshotTool
from tools.executor import ToolExecutor, ToolPolicy
from tools.factory import build_registry
from tools.registry import ToolRegistry
from vision.capture import (
    ALL_DISPLAYS,
    PRIMARY_DISPLAY,
    Frame,
    GdiScreenCapture,
    ScreenCapture,
    ScreenshotCapture,
    _bgrx_to_rgb,
    default_screen_capture,
    encode_png,
)


WINDOWS_ONLY = pytest.mark.skipif(
    os.name != "nt", reason="GDI capture is Windows only"
)


# ----------------------------------------------------------------------
# Fixtures and helpers
# ----------------------------------------------------------------------

def rgb_frame(width=4, height=3, colour=(10, 20, 30)) -> Frame:
    """A solid frame in the format both capture backends produce."""

    return Frame(
        width=width,
        height=height,
        data=bytes(colour) * (width * height),
        image_format="rgb",
        source="test",
    )


class CountingCapture:
    """
    A capture that records how often it was asked, and for which display.

    Used instead of a real screen so the refusal tests can assert that
    nothing was captured at all - which is the property, not an
    implementation detail.
    """

    def __init__(self, frame=None, available=True):
        self.frame = frame if frame is not None else rgb_frame()
        self.captures = 0
        self._available = available

    def capture(self):
        self.captures += 1
        return self.frame

    def is_available(self) -> bool:
        return self._available


class Factory:
    """`monitor -> capture`, recording every index it was asked for."""

    def __init__(self, capture=None):
        self.capture = capture if capture is not None else CountingCapture()
        self.asked: list[int] = []

    def __call__(self, monitor):
        self.asked.append(monitor)
        return self.capture


def not_windows(monkeypatch) -> None:
    """
    Make `vision.capture` believe it is not on Windows.

    Deliberately not `monkeypatch.setattr(os, "name", "posix")`, which is
    the obvious spelling and is a trap. `pathlib` reads `os.name` to
    decide which Path flavour to build, so an assertion that *fails*
    inside the patched window makes pytest raise "cannot instantiate
    'PosixPath' on your system" while formatting the failure - an
    INTERNALERROR that aborts the whole session, names no test, and skips
    everything that had not run yet.

    A green run never notices. The mutation battery did: two mutations
    came back caught by "<collection error>" instead of by a test.

    `vision.capture` reads `os` for `os.name` and nothing else, so a
    namespace carrying just that is enough, and anything else it reaches
    for fails loudly here rather than quietly there.
    """

    monkeypatch.setattr("vision.capture.os", SimpleNamespace(name="posix"))


@pytest.fixture
def writable(tmp_path):
    """A writable root, with the owner's own file just outside it."""

    root = tmp_path / "shots"
    root.mkdir()

    (tmp_path / "secret.png").write_bytes(b"the owner's own file")

    return root


@pytest.fixture
def counter():
    return CountingCapture()


@pytest.fixture
def tool(writable, counter):
    """The tool over one root, with a capture that touches no display."""

    return ScreenshotTool([str(writable)], capture_factory=Factory(counter))


def executor_for(*instances, allowed=None, approve=True):
    """An executor with these tools registered and DANGEROUS approved."""

    registry = ToolRegistry()

    for instance in instances:
        registry.register(instance)

    names = allowed if allowed is not None else [t.name for t in instances]

    return ToolExecutor(
        registry=registry,
        policy=ToolPolicy(
            enabled=True,
            allowed=names,
            auto_approve=(
                ["safe", "sensitive", "dangerous"] if approve else ["safe"]
            ),
        ),
        confirm=(lambda tool, arguments: True) if approve else None,
    )


# ----------------------------------------------------------------------
# The encoder: hand-rolled, so decoded by someone else
# ----------------------------------------------------------------------

class TestTheEncoderWritesRealPngs:

    def decoded(self, frame):
        """The frame encoded by us and decoded by PIL."""

        from PIL import Image

        return Image.open(io.BytesIO(encode_png(frame)))

    def test_the_signature_is_the_png_signature(self):
        png = encode_png(rgb_frame())

        assert png.startswith(PNG_MAGIC)
        assert png[:8] == bytes([137, 80, 78, 71, 13, 10, 26, 10])

    def test_an_independent_decoder_reads_the_exact_pixels(self):
        # Four distinct colours, so a channel swap, a row reversal and a
        # transpose all produce a different answer.
        data = bytes([
            255, 0, 0,      0, 255, 0,
            0, 0, 255,      255, 255, 255,
        ])
        frame = Frame(width=2, height=2, data=data, image_format="rgb")

        image = self.decoded(frame)

        assert image.size == (2, 2)
        assert image.mode == "RGB"
        assert list(image.getdata()) == [
            (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255),
        ]

    def test_a_non_square_frame_is_not_transposed(self):
        # Width and height are two arguments of the same type in the IHDR
        # and in every buffer calculation, so swapping them is the easiest
        # mistake here to make and the hardest to see.
        wide = self.decoded(rgb_frame(width=7, height=3))
        tall = self.decoded(rgb_frame(width=3, height=7))

        assert wide.size == (7, 3)
        assert tall.size == (3, 7)

    def test_the_rows_are_in_order(self):
        # A top row that differs from the bottom one, so a bottom-up write
        # cannot pass. This is the mirroring defect the GDI backend's
        # negative biHeight exists to avoid, checked on the encoder side.
        data = bytes([255, 0, 0] * 2 + [0, 0, 255] * 2)
        frame = Frame(width=2, height=2, data=data, image_format="rgb")

        image = self.decoded(frame)

        assert image.getpixel((0, 0)) == (255, 0, 0)
        assert image.getpixel((0, 1)) == (0, 0, 255)

    def test_the_header_says_truecolour_eight_bit(self):
        png = encode_png(rgb_frame(width=5, height=6))

        assert png[12:16] == b"IHDR"
        assert int.from_bytes(png[16:20], "big") == 5
        assert int.from_bytes(png[20:24], "big") == 6
        assert png[24] == 8       # bit depth
        assert png[25] == 2       # colour type 2 = truecolour rgb
        assert png[26] == 0       # deflate
        assert png[27] == 0       # adaptive filtering
        assert png[28] == 0       # no interlace

    def test_every_chunk_crc_is_correct(self):
        import struct
        import zlib

        png = encode_png(rgb_frame())

        offset = 8
        seen = []

        while offset < len(png):
            length = struct.unpack(">I", png[offset:offset + 4])[0]
            kind = png[offset + 4:offset + 8]
            payload = png[offset + 8:offset + 8 + length]
            stored = struct.unpack(
                ">I", png[offset + 8 + length:offset + 12 + length]
            )[0]

            assert stored == zlib.crc32(kind + payload) & 0xFFFFFFFF, (
                f"{kind!r} chunk has a wrong CRC"
            )

            seen.append(kind)
            offset += 12 + length

        assert seen == [b"IHDR", b"IDAT", b"IEND"]

    def test_every_scanline_carries_its_filter_byte(self):
        import struct
        import zlib

        width, height = 4, 3
        png = encode_png(rgb_frame(width=width, height=height))

        # Pull the IDAT payload back out and inflate it.
        offset = 8
        idat = b""

        while offset < len(png):
            length = struct.unpack(">I", png[offset:offset + 4])[0]
            if png[offset + 4:offset + 8] == b"IDAT":
                idat = png[offset + 8:offset + 8 + length]
                break
            offset += 12 + length

        raw = zlib.decompress(idat)

        # One filter byte per row, then the row. A missing filter byte
        # still decompresses and still has a plausible length, so the
        # count is the thing to assert.
        assert len(raw) == height * (1 + width * 3)
        for row in range(height):
            assert raw[row * (1 + width * 3)] == 0

    def test_the_output_is_deterministic(self):
        # Same frame, same bytes. A screenshot tool that produced a
        # different file every time from identical pixels would make any
        # byte comparison downstream impossible.
        frame = rgb_frame()

        assert encode_png(frame) == encode_png(frame)


class TestTheEncoderRefusesWhatItCannotEncode:

    def test_an_already_encoded_frame_is_refused_not_passed_through(self):
        # The dangerous alternative is silently returning the input, which
        # would write JPEG bytes into a file called `.png`.
        jpeg = Frame(
            width=2, height=2, data=b"\xff\xd8\xff\xe0nonsense",
            image_format="jpeg",
        )

        with pytest.raises(ValueError, match="raw rgb"):
            encode_png(jpeg)

    def test_the_default_raw_format_is_refused(self):
        # `Frame`'s default `image_format` is "raw", and MockScreenCapture
        # returns exactly that when it runs out of frames. Encoding it as
        # if it were rgb would produce a garbage image of the right size.
        with pytest.raises(ValueError, match="raw rgb"):
            encode_png(Frame(width=2, height=2, data=b"x" * 12))

    def test_a_frame_whose_data_is_the_wrong_length_is_refused(self):
        short = Frame(
            width=4, height=4, data=b"\x00" * 10, image_format="rgb"
        )

        with pytest.raises(ValueError, match="expected 48"):
            encode_png(short)

    def test_a_frame_with_no_area_is_refused(self):
        for width, height in ((0, 5), (5, 0), (0, 0), (-2, 3)):
            with pytest.raises(ValueError, match="no area"):
                encode_png(
                    Frame(
                        width=width, height=height, data=b"",
                        image_format="rgb",
                    )
                )


class TestChannelOrder:

    def test_bgrx_becomes_rgb(self):
        # One pixel, four distinguishable byte values, so a wrong stride
        # or a wrong offset cannot coincide with the right answer.
        assert _bgrx_to_rgb(bytes([1, 2, 3, 4])) == bytes([3, 2, 1])

    def test_the_padding_byte_is_dropped(self):
        assert len(_bgrx_to_rgb(bytes(range(16)))) == 12

    def test_pixels_stay_in_order(self):
        raw = bytes([1, 2, 3, 255, 4, 5, 6, 255])

        assert _bgrx_to_rgb(raw) == bytes([3, 2, 1, 6, 5, 4])

    def test_an_empty_buffer_gives_an_empty_result(self):
        assert _bgrx_to_rgb(b"") == b""


# ----------------------------------------------------------------------
# The GDI backend
# ----------------------------------------------------------------------

class TestTheGdiBackendIsADropInForMss:

    def test_it_satisfies_the_capture_protocol(self):
        assert isinstance(GdiScreenCapture(), ScreenCapture)

    def test_it_answers_the_same_questions_as_the_mss_backend(self):
        # Structural: both are constructed the same way and expose the
        # same two members, which is what `default_screen_capture` and
        # `VisionManager` rely on.
        for name in ("capture", "is_available"):
            assert callable(getattr(GdiScreenCapture(monitor=2), name))
            assert callable(getattr(ScreenshotCapture(monitor=2), name))

    def test_it_is_unavailable_off_windows(self, monkeypatch):
        not_windows(monkeypatch)

        assert GdiScreenCapture().is_available() is False

    def test_capture_returns_none_off_windows(self, monkeypatch):
        not_windows(monkeypatch)

        assert GdiScreenCapture().capture() is None

    def test_the_binding_is_cached(self, monkeypatch):
        capture = GdiScreenCapture()

        if not capture.is_available():
            pytest.skip("no gdi here")

        first = capture._bind()

        # If the second call rebound, it would have to consult os.name
        # again; breaking os.name proves it does not.
        not_windows(monkeypatch)

        assert capture._bind() is first


class TestTheConfiguredDisplay:
    """
    `_resolve` deliberately mirrors `ScreenshotCapture._resolve`, because
    a configured `monitor` must mean the same display whichever backend
    happens to be installed.
    """

    def three(self):
        return [{"union": True}, {"primary": True}, {"second": True}]

    def test_an_index_a_display_answers_to_is_used(self):
        for index in (0, 1, 2):
            assert GdiScreenCapture(monitor=index)._resolve(self.three()) == index

    def test_a_string_index_is_read(self):
        # YAML hands back whatever was written, so `monitor: "2"` arrives
        # as a string.
        assert GdiScreenCapture(monitor="2")._resolve(self.three()) == 2

    def test_true_is_not_read_as_an_index(self):
        # bool is an int subclass, so `monitor: yes` would otherwise mean
        # the primary display by accident rather than by choice.
        #
        # `== PRIMARY_DISPLAY` cannot say that on its own, and the reason
        # is worth writing down: PRIMARY_DISPLAY is 1 and `True == 1` is
        # true in Python, so an implementation that passed the bool
        # straight through as an index would satisfy an equality check
        # while doing the wrong thing. The type is the claim.
        resolved = GdiScreenCapture(monitor=True)._resolve(self.three())

        assert resolved == PRIMARY_DISPLAY
        assert type(resolved) is int, "the bool was used as an index"

    def test_false_is_not_display_zero(self):
        # Where the guard is load-bearing rather than merely tidy.
        # Without it `monitor: no` is `int(False)`, which is 0, which is
        # every display stitched into one wide image - a different screen
        # from the fallback, chosen silently.
        assert GdiScreenCapture(monitor=False)._resolve(self.three()) == (
            PRIMARY_DISPLAY
        )

    def test_a_bool_is_reported_as_a_configuration_mistake(self, caplog):
        # The other half of it: not only "not display 1", but the owner
        # gets told, because `monitor: yes` is a typo for something and
        # section 2 says warn rather than quietly reinterpret.
        with caplog.at_level("WARNING"):
            GdiScreenCapture(monitor=True)._resolve(self.three())

        assert "does not exist" in caplog.text

    def test_an_index_no_display_answers_to_falls_back_to_primary(self):
        # Not to 0, which is every display stitched into one wide image -
        # the failure that looks like a hallucinating model from outside.
        assert GdiScreenCapture(monitor=9)._resolve(self.three()) == PRIMARY_DISPLAY
        assert GdiScreenCapture(monitor=-1)._resolve(self.three()) == PRIMARY_DISPLAY

    def test_with_only_a_union_the_fallback_is_the_union(self):
        assert GdiScreenCapture(monitor=9)._resolve([{"union": True}]) == ALL_DISPLAYS

    def test_the_warning_fires_once_per_object(self, caplog):
        capture = GdiScreenCapture(monitor=9)

        with caplog.at_level("WARNING"):
            for _ in range(5):
                capture._resolve(self.three())

        warnings = [
            record for record in caplog.records
            if "does not exist" in record.getMessage()
        ]

        assert len(warnings) == 1, (
            "at a two second capture interval an unconditional warning "
            "writes thirty lines a minute for the whole session"
        )

    def test_the_warning_names_the_configured_value(self, caplog):
        with caplog.at_level("WARNING"):
            GdiScreenCapture(monitor=9)._resolve(self.three())

        assert "9" in caplog.text
        assert "gdi" in caplog.text.lower()


@WINDOWS_ONLY
class TestTheGdiBackendAgainstTheRealScreen:

    def test_it_is_available_here(self):
        assert GdiScreenCapture().is_available() is True

    def test_the_union_is_first_and_the_primary_is_second(self):
        monitors = GdiScreenCapture()._monitors()

        assert len(monitors) >= 2, "a union entry plus at least one display"
        assert monitors[PRIMARY_DISPLAY]["primary"] is True

    def test_the_union_covers_every_display(self):
        monitors = GdiScreenCapture()._monitors()

        union, displays = monitors[0], monitors[1:]

        for display in displays:
            assert display["left"] >= union["left"]
            assert display["top"] >= union["top"]
            assert (
                display["left"] + display["width"]
                <= union["left"] + union["width"]
            )
            assert (
                display["top"] + display["height"]
                <= union["top"] + union["height"]
            )

    def test_the_frame_is_the_size_of_the_display(self):
        capture = GdiScreenCapture()

        monitors = capture._monitors()
        expected = monitors[PRIMARY_DISPLAY]

        frame = capture.capture()

        assert frame is not None
        assert (frame.width, frame.height) == (
            expected["width"], expected["height"]
        )

    def test_the_frame_is_raw_rgb_of_exactly_the_right_length(self):
        frame = GdiScreenCapture().capture()

        assert frame.image_format == "rgb"
        assert frame.source == "screen"
        assert len(frame.data) == frame.width * frame.height * 3
        assert not frame.is_empty()

    def test_the_frame_is_not_a_blank_image(self):
        # An all-zero buffer is what a failed BitBlt that still reports
        # success leaves behind, and it has the correct length.
        frame = GdiScreenCapture().capture()

        assert frame.data != bytes(len(frame.data))

    def test_the_picture_matches_an_independent_capture(self):
        # The claim is "this is a picture of the screen", and the only
        # honest way to check it is to compare against someone else's
        # implementation of the same thing.
        pil = pytest.importorskip("PIL.ImageGrab", reason="pillow absent")

        from PIL import Image

        mine = Image.open(io.BytesIO(encode_png(GdiScreenCapture().capture())))
        theirs = pil.grab().convert("RGB")

        if mine.size != theirs.size:
            pytest.skip(f"grabs disagree on size: {mine.size} {theirs.size}")

        a, b = mine.load(), theirs.load()
        width, height = mine.size
        step_x, step_y = max(1, width // 40), max(1, height // 30)

        def closeness(reference) -> float:
            near = total = 0
            for y in range(0, height, step_y):
                for x in range(0, width, step_x):
                    total += 1
                    p, q = a[x, y], reference[x, y]
                    if max(abs(p[i] - q[i]) for i in range(3)) <= 12:
                        near += 1
                    del p, q
            return near / total

        upright = closeness(b)

        # The two grabs are of different instants, so exact equality is
        # not the bar. What is asserted is that the upright, correctly
        # ordered comparison beats both of the specific defects that a
        # byte count cannot see.
        flipped = closeness(
            theirs.transpose(Image.FLIP_TOP_BOTTOM).load()
        )

        red, green, blue = theirs.split()
        swapped = closeness(Image.merge("RGB", (blue, green, red)).load())

        assert upright > 0.9, f"only {upright:.0%} of sampled pixels matched"
        assert upright > flipped, "the image looks vertically mirrored"
        assert upright > swapped, "red and blue look swapped"

    def test_repeated_captures_do_not_leak_gdi_handles(self):
        # The `finally` block releases a DC, a memory DC and a bitmap. A
        # leak there is invisible per call and fatal over a session, and
        # Windows will tell us the count if we ask.
        import ctypes

        user32 = ctypes.windll.user32
        user32.GetGuiResources.argtypes = [
            ctypes.c_void_p, ctypes.c_uint
        ]
        user32.GetGuiResources.restype = ctypes.c_uint

        process = ctypes.windll.kernel32.GetCurrentProcess()
        GR_GDIOBJECTS = 0

        capture = GdiScreenCapture()

        capture.capture()               # bind and warm up
        before = user32.GetGuiResources(process, GR_GDIOBJECTS)

        for _ in range(8):
            assert capture.capture() is not None

        after = user32.GetGuiResources(process, GR_GDIOBJECTS)

        assert after - before <= 2, (
            f"GDI objects went from {before} to {after} over 8 captures"
        )

    def test_the_capture_undoes_its_dpi_awareness_change(self, monkeypatch):
        # Thread scoped, and set back afterwards, so a screenshot does not
        # silently change how the rest of the process sees the screen.
        #
        # Asserted by handing `capture` a recording undo rather than by
        # reading the thread context around the call, because the readable
        # version cannot fail: the first capture of the session leaves the
        # thread aware, so a later measurement sees the already-changed
        # value both before and after and agrees with itself.
        undone = []

        monkeypatch.setattr(
            GdiScreenCapture,
            "_aware",
            lambda self, ctypes, user32: lambda: undone.append(1),
        )

        assert GdiScreenCapture().capture() is not None
        assert undone == [1]

    def test_the_undo_runs_even_when_the_grab_fails(self, monkeypatch):
        # The `finally` placement, not just the happy path. A failed
        # capture that left the thread per-monitor aware would change how
        # every later `GetSystemMetrics` in the process reads, and nothing
        # would point at the screenshot that did it.
        import ctypes
        from ctypes import wintypes

        undone = []

        real = GdiScreenCapture()._bind()

        if real is None:
            pytest.skip("no gdi here")

        class NoScreenDc:
            def GetDC(self, window):
                return 0

        square = {
            "left": 0, "top": 0, "width": 8, "height": 8, "primary": True,
        }

        monkeypatch.setattr(
            GdiScreenCapture,
            "_aware",
            lambda self, ctypes, user32: lambda: undone.append(1),
        )
        monkeypatch.setattr(
            GdiScreenCapture,
            "_bind",
            lambda self: (ctypes, wintypes, NoScreenDc(), real[3]),
        )
        monkeypatch.setattr(
            GdiScreenCapture, "_monitors", lambda self: [square, square]
        )

        assert GdiScreenCapture().capture() is None
        assert undone == [1]

    def test_the_undo_really_restores_the_previous_context(self):
        # And the undo is a real restore rather than a lambda that returns
        # None - which is what the two tests above accept by construction.
        import ctypes

        user32 = ctypes.windll.user32

        try:
            user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
            before = user32.GetThreadDpiAwarenessContext()
        except Exception:
            pytest.skip("thread DPI awareness unavailable here")

        capture = GdiScreenCapture()

        undo = capture._aware(ctypes, user32)

        try:
            during = user32.GetThreadDpiAwarenessContext()
        finally:
            undo()

        assert user32.GetThreadDpiAwarenessContext() == before

        if during == before:
            pytest.skip("this thread is already per-monitor aware")


class TestChoosingABackend:

    def test_mss_is_preferred_when_it_is_installed(self, monkeypatch):
        monkeypatch.setattr(
            ScreenshotCapture, "is_available", lambda self: True
        )

        assert isinstance(default_screen_capture(), ScreenshotCapture)

    def test_gdi_is_the_fallback(self, monkeypatch):
        monkeypatch.setattr(
            ScreenshotCapture, "is_available", lambda self: False
        )
        monkeypatch.setattr(
            GdiScreenCapture, "is_available", lambda self: True
        )

        assert isinstance(default_screen_capture(), GdiScreenCapture)

    def test_nothing_rather_than_a_mock_when_neither_works(self, monkeypatch):
        # A MockScreenCapture wired in by default would let the tool report
        # success having written 1920x1080 of nothing.
        monkeypatch.setattr(
            ScreenshotCapture, "is_available", lambda self: False
        )
        monkeypatch.setattr(
            GdiScreenCapture, "is_available", lambda self: False
        )

        assert default_screen_capture() is None

    def test_the_monitor_is_passed_through(self, monkeypatch):
        monkeypatch.setattr(
            ScreenshotCapture, "is_available", lambda self: True
        )

        assert default_screen_capture(monitor=3).monitor == 3

    def test_the_monitor_reaches_the_gdi_branch_too(self, monkeypatch):
        # Two branches, two constructor calls, and only the first was
        # covered - so a dropped `monitor=` on the fallback path would
        # have captured the wrong screen on exactly the machines that need
        # the fallback.
        monkeypatch.setattr(
            ScreenshotCapture, "is_available", lambda self: False
        )
        monkeypatch.setattr(
            GdiScreenCapture, "is_available", lambda self: True
        )

        chosen = default_screen_capture(monitor=3)

        assert isinstance(chosen, GdiScreenCapture)
        assert chosen.monitor == 3


# ----------------------------------------------------------------------
# Containment: the image lands where the owner allowed writing
# ----------------------------------------------------------------------

class TestNothingLandsOutsideAWritableRoot:

    def test_a_traversal_is_refused_and_the_file_outside_is_untouched(
        self, tool, writable, counter
    ):
        outside = writable.parent / "secret.png"
        before = outside.read_bytes()

        with pytest.raises(PermissionError):
            tool.execute(str(writable / ".." / "secret.png"))

        assert outside.read_bytes() == before

    def test_an_absolute_path_outside_is_refused(self, tool, writable):
        outside = writable.parent / "secret.png"

        with pytest.raises(PermissionError):
            tool.execute(str(outside), overwrite=True)

        assert outside.read_bytes() == b"the owner's own file"

    def test_with_no_roots_nothing_is_writable(self, tmp_path):
        tool = ScreenshotTool([], capture_factory=Factory())

        with pytest.raises(PermissionError, match="no directories"):
            tool.execute(str(tmp_path / "shot.png"))

    def test_a_relative_path_gets_its_own_message(self, tool):
        # "outside the allowed directories" is true but useless for a bare
        # filename, and a caller told only that tries another bare
        # filename.
        with pytest.raises(PermissionError, match="relative path"):
            tool.execute("shot.png")

    def test_a_junction_pointing_out_is_refused(self, tool, writable):
        # Symlinks need a privilege the test runner does not hold, so the
        # same escape is exercised with a directory junction, which needs
        # none and which `resolve()` sees through identically.
        private = writable.parent / "private"
        private.mkdir()
        (private / "shot.png").write_bytes(b"the owner's own file")

        link = writable / "shortcut"

        made = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(private)],
            capture_output=True, text=True,
        )

        if made.returncode != 0:
            pytest.skip(f"junctions unavailable here: {made.stderr.strip()}")

        try:
            with pytest.raises(PermissionError):
                tool.execute(str(link / "shot.png"), overwrite=True)

            assert (private / "shot.png").read_bytes() == b"the owner's own file"
        finally:
            # `shutil.rmtree` follows a junction into its target, and
            # pytest reaps old tmp_path directories with rmtree.
            os.rmdir(link)

    def test_the_containment_check_is_the_one_from_filesystem(self):
        # Not a style point. Two resolve-and-compare implementations can
        # drift, and the one that drifts is a path escape.
        source = io.open(
            "tools/builtins/screen.py", encoding="utf-8"
        ).read()

        assert "from tools.builtins.filesystem import" in source
        assert "_contained," in source
        assert "def _contained" not in source, (
            "screen.py has its own containment check"
        )


class TestNothingIsCapturedOnAPathThatWillBeRefused:
    """
    The screen is the most sensitive thing this layer reads. A refusal
    that happens *after* the capture means the image existed in memory for
    no reason, and nothing downstream would ever know.
    """

    def test_a_traversal_captures_nothing(self, tool, writable, counter):
        with pytest.raises(PermissionError):
            tool.execute(str(writable / ".." / "secret.png"))

        assert counter.captures == 0

    def test_a_wrong_suffix_captures_nothing(self, tool, writable, counter):
        with pytest.raises(ValueError):
            tool.execute(str(writable / "shot.jpg"))

        assert counter.captures == 0

    def test_an_existing_file_captures_nothing(self, tool, writable, counter):
        (writable / "shot.png").write_bytes(b"an earlier screenshot")

        with pytest.raises(ValueError):
            tool.execute(str(writable / "shot.png"))

        assert counter.captures == 0

    def test_a_missing_directory_captures_nothing(
        self, tool, writable, counter
    ):
        with pytest.raises(FileNotFoundError):
            tool.execute(str(writable / "nope" / "shot.png"))

        assert counter.captures == 0

    def test_a_directory_as_the_target_captures_nothing(
        self, tool, writable, counter
    ):
        (writable / "folder.png").mkdir()

        with pytest.raises(ValueError, match="directory"):
            tool.execute(str(writable / "folder.png"))

        assert counter.captures == 0

    def test_a_bad_monitor_captures_nothing(self, tool, writable, counter):
        with pytest.raises(ValueError, match="monitor"):
            tool.execute(str(writable / "shot.png"), monitor="left one")

        assert counter.captures == 0

    def test_a_successful_call_captures_exactly_once(
        self, tool, writable, counter
    ):
        tool.execute(str(writable / "shot.png"))

        assert counter.captures == 1


# ----------------------------------------------------------------------
# What the tool refuses
# ----------------------------------------------------------------------

class TestRefusals:

    def test_a_non_png_name_is_refused_and_the_message_names_png(
        self, tool, writable
    ):
        with pytest.raises(ValueError, match=r"\.png"):
            tool.execute(str(writable / "shot.jpg"))

        assert not (writable / "shot.jpg").exists()

    def test_a_suffixless_name_is_refused(self, tool, writable):
        with pytest.raises(ValueError, match=r"\.png"):
            tool.execute(str(writable / "shot"))

    def test_the_suffix_check_is_case_insensitive(self, tool, writable):
        # `.PNG` is the same file type, and refusing it would be a
        # restriction with nothing behind it.
        tool.execute(str(writable / "SHOT.PNG"))

        assert (writable / "SHOT.PNG").read_bytes().startswith(PNG_MAGIC)

    def test_an_existing_file_needs_overwrite_stated(self, tool, writable):
        earlier = writable / "shot.png"
        earlier.write_bytes(b"an earlier screenshot")

        with pytest.raises(ValueError) as raised:
            tool.execute(str(earlier))

        assert earlier.read_bytes() == b"an earlier screenshot"

        # Both ways forward, or the model retries the identical call.
        message = str(raised.value)
        assert "overwrite=true" in message
        assert "different name" in message

    def test_overwrite_replaces_it(self, tool, writable):
        earlier = writable / "shot.png"
        earlier.write_bytes(b"an earlier screenshot")

        tool.execute(str(earlier), overwrite=True)

        assert earlier.read_bytes().startswith(PNG_MAGIC)

    def test_a_missing_parent_names_create_directory(self, tool, writable):
        with pytest.raises(FileNotFoundError, match="create_directory"):
            tool.execute(str(writable / "shots" / "today.png"))

    def test_an_unavailable_backend_is_a_clear_failure(self, writable):
        tool = ScreenshotTool(
            [str(writable)], capture_factory=lambda monitor: None
        )

        with pytest.raises(RuntimeError, match="not available"):
            tool.execute(str(writable / "shot.png"))

        assert not (writable / "shot.png").exists()

    def test_a_failed_grab_writes_nothing(self, writable):
        class Failing:
            def capture(self):
                return None

            def is_available(self):
                return True

        tool = ScreenshotTool(
            [str(writable)], capture_factory=lambda monitor: Failing()
        )

        with pytest.raises(RuntimeError, match="could not be captured"):
            tool.execute(str(writable / "shot.png"))

        assert not (writable / "shot.png").exists()

    def test_an_empty_frame_writes_nothing(self, writable):
        empty = Frame(width=1920, height=1080, data=b"", image_format="rgb")

        tool = ScreenshotTool(
            [str(writable)],
            capture_factory=lambda monitor: CountingCapture(frame=empty),
        )

        with pytest.raises(RuntimeError, match="could not be captured"):
            tool.execute(str(writable / "shot.png"))

        assert not (writable / "shot.png").exists()

    def test_a_frame_the_encoder_refuses_writes_nothing(self, writable):
        # MockScreenCapture's fallback frame is `image_format="raw"`. If
        # one ever reached the tool, writing its bytes under a `.png` name
        # would produce a file that is not an image.
        raw = Frame(width=2, height=2, data=b"x" * 12)

        tool = ScreenshotTool(
            [str(writable)],
            capture_factory=lambda monitor: CountingCapture(frame=raw),
        )

        with pytest.raises(ValueError, match="raw rgb"):
            tool.execute(str(writable / "shot.png"))

        assert not (writable / "shot.png").exists()

    def test_a_nonsense_overwrite_value_is_refused(self, tool, writable):
        (writable / "shot.png").write_bytes(b"earlier")

        with pytest.raises(ValueError):
            tool.execute(
                str(writable / "shot.png"), overwrite="probably"
            )

        assert (writable / "shot.png").read_bytes() == b"earlier"


class TestWhichDisplay:

    def test_no_monitor_argument_uses_the_configured_one(self, writable):
        factory = Factory()
        tool = ScreenshotTool(
            [str(writable)], monitor=2, capture_factory=factory
        )

        tool.execute(str(writable / "shot.png"))

        assert factory.asked == [2]

    def test_the_default_configured_display_is_the_primary(self, writable):
        factory = Factory()
        tool = ScreenshotTool([str(writable)], capture_factory=factory)

        tool.execute(str(writable / "shot.png"))

        assert factory.asked == [PRIMARY_DISPLAY]

    def test_an_explicit_monitor_wins(self, writable):
        factory = Factory()
        tool = ScreenshotTool(
            [str(writable)], monitor=1, capture_factory=factory
        )

        tool.execute(str(writable / "shot.png"), monitor=0)

        assert factory.asked == [0]

    def test_a_string_monitor_is_read(self, writable):
        factory = Factory()
        tool = ScreenshotTool([str(writable)], capture_factory=factory)

        tool.execute(str(writable / "shot.png"), monitor="2")

        assert factory.asked == [2]

    def test_a_blank_monitor_means_the_configured_one(self, writable):
        factory = Factory()
        tool = ScreenshotTool(
            [str(writable)], monitor=1, capture_factory=factory
        )

        tool.execute(str(writable / "shot.png"), monitor="   ")

        assert factory.asked == [1]

    def test_true_is_not_a_display(self, tool, writable):
        with pytest.raises(ValueError, match="not true or false"):
            tool.execute(str(writable / "shot.png"), monitor=True)

    def test_a_fresh_capture_is_built_per_call(self, writable):
        # Not one shared backend mutated between calls: it carries a
        # warn-once flag whose whole purpose is to fire, and reusing it
        # across displays would suppress the warning for the second bad
        # index.
        factory = Factory()
        tool = ScreenshotTool([str(writable)], capture_factory=factory)

        tool.execute(str(writable / "one.png"), monitor=0)
        tool.execute(str(writable / "two.png"), monitor=2)

        assert factory.asked == [0, 2]


# ----------------------------------------------------------------------
# What the tool writes, and what it says about it
# ----------------------------------------------------------------------

class TestWhatLandsOnDisk:

    def test_the_file_is_a_png_of_the_frames_size(self, tool, writable):
        tool.execute(str(writable / "shot.png"))

        written = (writable / "shot.png").read_bytes()

        assert written.startswith(PNG_MAGIC)
        assert int.from_bytes(written[16:20], "big") == 4
        assert int.from_bytes(written[20:24], "big") == 3

    def test_the_file_decodes_as_an_image(self, tool, writable):
        from PIL import Image

        tool.execute(str(writable / "shot.png"))

        image = Image.open(writable / "shot.png")

        assert image.size == (4, 3)
        assert image.getpixel((0, 0)) == (10, 20, 30)

    def test_nothing_else_is_left_behind(self, tool, writable):
        # `_atomic_write` writes a temporary in the target's own directory
        # and renames it. A leftover would sit beside the screenshot.
        tool.execute(str(writable / "shot.png"))

        assert sorted(p.name for p in writable.iterdir()) == ["shot.png"]

    def test_a_nested_write_reports_the_whole_path(self, tool, writable):
        (writable / "august").mkdir()

        message = tool.execute(str(writable / "august" / "shot.png"))

        assert "august/shot.png" in message

    def test_the_message_names_the_size_and_the_dimensions(
        self, tool, writable
    ):
        message = tool.execute(str(writable / "shot.png"))

        written = (writable / "shot.png").read_bytes()

        assert "4x3" in message
        assert str(len(written)) in message

    def test_the_message_does_not_name_the_owners_home_directory(
        self, tool, writable
    ):
        # Every message here ends up in a prompt and so leaves the
        # machine, and an absolute path names the username.
        message = tool.execute(str(writable / "shot.png"))

        assert str(Path.home()) not in message
        assert not Path(message.split()[1]).is_absolute()

    def test_no_refusal_names_the_owners_home_directory(
        self, tool, writable
    ):
        attempts = [
            ((str(writable / "shot.jpg"),), {}),
            ((str(writable / "nope" / "shot.png"),), {}),
            ((str(writable.parent / "secret.png"),), {"overwrite": True}),
            (("shot.png",), {}),
        ]

        for arguments, keywords in attempts:
            with pytest.raises(Exception) as raised:
                tool.execute(*arguments, **keywords)

            assert str(Path.home()) not in str(raised.value), arguments

    def test_the_image_never_appears_in_the_message(self, tool, writable):
        message = tool.execute(str(writable / "shot.png"))

        # A short one-line sentence, not an image. The frame is 36 bytes
        # of pixel data; the message must not contain it.
        assert len(message) < 120
        assert "\n" not in message
        assert bytes([10, 20, 30]).decode("latin-1") not in message

    def test_the_log_line_carries_geometry_and_not_pixels(
        self, tool, writable, caplog
    ):
        with caplog.at_level("INFO"):
            tool.execute(str(writable / "shot.png"))

        logged = caplog.text

        assert "4x3" in logged
        assert bytes([10, 20, 30]).decode("latin-1") not in logged


# ----------------------------------------------------------------------
# Section 11: the postcondition
# ----------------------------------------------------------------------

class TestVerify:

    def test_verify_accepts_exactly_what_execute_accepts(self):
        # The executor calls `check(**arguments)` with the identical
        # arguments it passed to `execute`, so a signature that has
        # drifted is a TypeError at the worst possible moment.
        import inspect

        assert (
            inspect.signature(ScreenshotTool.execute).parameters.keys()
            == inspect.signature(ScreenshotTool.verify).parameters.keys()
        )

    def test_a_real_screenshot_verifies(self, tool, writable):
        target = writable / "shot.png"

        tool.execute(str(target))

        result = tool.verify(str(target))

        assert result.ok
        assert "4x3" in result.output

    def test_verify_passes_with_the_same_arguments_execute_got(
        self, tool, writable
    ):
        target = writable / "shot.png"

        tool.execute(str(target), monitor=1, overwrite=False)

        assert tool.verify(str(target), monitor=1, overwrite=False).ok

    def test_a_missing_file_fails_verification(self, tool, writable):
        result = tool.verify(str(writable / "never.png"))

        assert not result.ok
        assert "not there" in result.error

    def test_a_file_that_is_not_a_png_fails_verification(
        self, tool, writable
    ):
        # What a full disk, a quota, or an antivirus rewriting the file as
        # it lands leaves behind: a path that exists and is not an image.
        (writable / "shot.png").write_bytes(b"not an image at all")

        result = tool.verify(str(writable / "shot.png"))

        assert not result.ok
        assert "not a PNG" in result.error

    def test_a_truncated_png_fails_verification(self, tool, writable):
        target = writable / "shot.png"
        tool.execute(str(target))

        target.write_bytes(target.read_bytes()[:4])

        assert not tool.verify(str(target)).ok

    def test_a_png_with_no_header_chunk_fails_verification(
        self, tool, writable
    ):
        target = writable / "shot.png"
        target.write_bytes(PNG_MAGIC + b"\x00\x00\x00\x0dJUNK" + b"\x00" * 17)

        result = tool.verify(str(target))

        assert not result.ok
        assert "no PNG header" in result.error

    def test_a_png_of_nothing_fails_verification(self, tool, writable):
        target = writable / "shot.png"

        # A well formed signature and IHDR claiming zero width.
        target.write_bytes(
            PNG_MAGIC
            + (13).to_bytes(4, "big")
            + b"IHDR"
            + (0).to_bytes(4, "big")
            + (0).to_bytes(4, "big")
            + bytes([8, 2, 0, 0, 0])
            + b"\x00\x00\x00\x00"
        )

        result = tool.verify(str(target))

        assert not result.ok
        assert "nothing" in result.error

    def test_verify_refuses_a_path_outside_the_roots(self, tool, writable):
        with pytest.raises(PermissionError):
            tool.verify(str(writable.parent / "secret.png"))

    def test_verify_does_not_claim_the_picture_shows_the_screen(self):
        # An honest boundary, and stated in the docstring so nobody fills
        # in the perceived gap later: the screen has already changed, and a
        # second capture to compare against is a different moment.
        assert ScreenshotTool.verify.__doc__
        assert "does not" in ScreenshotTool.verify.__doc__


# ----------------------------------------------------------------------
# The five gates
# ----------------------------------------------------------------------

class TestThroughTheExecutor:

    def test_the_tool_satisfies_the_protocol(self, tool):
        assert isinstance(tool, ToolProtocol)

    def test_a_screenshot_is_dangerous(self, tool):
        assert tool.risk is ToolRisk.DANGEROUS

    def test_the_description_warns_what_is_in_the_picture(self, tool):
        # This is the text a confirmation prompt shows the owner, and a
        # screen holds passwords in plain text.
        assert "private" in tool.description.lower()

    def test_it_runs_through_the_executor(self, tool, writable):
        executor = executor_for(tool)

        result = executor.execute("take_screenshot", {
            "path": str(writable / "shot.png")
        })

        assert result.ok, result.error
        assert (writable / "shot.png").read_bytes().startswith(PNG_MAGIC)

    def test_without_approval_nothing_is_captured(
        self, tool, writable, counter
    ):
        # The shipped state: auto_approve is ['safe'] and a server has no
        # confirmation handler, so a DANGEROUS call is refused at gate 4 -
        # before the tool is reached at all.
        executor = executor_for(tool, approve=False)

        result = executor.execute("take_screenshot", {
            "path": str(writable / "shot.png")
        })

        assert not result.ok
        assert counter.captures == 0
        assert not (writable / "shot.png").exists()

    def test_a_tool_not_on_the_allowed_list_is_refused(self, tool, writable):
        executor = executor_for(tool, allowed=["current_time"])

        result = executor.execute("take_screenshot", {
            "path": str(writable / "shot.png")
        })

        assert not result.ok

    def test_a_refused_path_comes_back_as_a_failure_not_a_crash(
        self, tool, writable
    ):
        executor = executor_for(tool)

        result = executor.execute("take_screenshot", {
            "path": str(writable.parent / "secret.png"),
            "overwrite": True,
        })

        assert not result.ok
        assert (writable.parent / "secret.png").read_bytes() == (
            b"the owner's own file"
        )

    def test_the_executor_downgrades_a_success_verify_denies(
        self, tool, writable, monkeypatch
    ):
        # Section 11: a verify that says no turns a successful execute
        # into a failed call. Simulated by deleting the file between the
        # two, which is what a directory being cleaned under us looks
        # like.
        executor = executor_for(tool)

        target = writable / "shot.png"

        original = ScreenshotTool.execute

        def execute_then_vanish(self, *arguments, **keywords):
            message = original(self, *arguments, **keywords)
            target.unlink()
            return message

        monkeypatch.setattr(ScreenshotTool, "execute", execute_then_vanish)

        result = executor.execute("take_screenshot", {"path": str(target)})

        assert not result.ok, "a vanished file was reported as a success"


# ----------------------------------------------------------------------
# Section 2: registered when granted, and not before
# ----------------------------------------------------------------------

class TestTheShippedConfigGrantsNothing:

    def shipped(self) -> dict:
        """
        The `tools` section as `config.yaml` actually ships it.

        Read from disk rather than through `load_config()`, so a defaulting
        bug in the loader cannot hide a silent enable.
        """

        with io.open("config.yaml", encoding="utf-8") as handle:
            return yaml.safe_load(handle)["tools"]

    def test_no_writable_root_ships(self):
        assert self.shipped()["writable_paths"] == []

    def test_take_screenshot_is_not_allowed(self):
        assert "take_screenshot" not in (self.shipped()["allowed"] or [])

    def test_the_shipped_config_registers_no_screenshot_tool(self):
        registry = build_registry(self.shipped())

        assert "take_screenshot" not in registry._tools

    def test_a_writable_root_registers_it(self, tmp_path):
        registry = build_registry({"writable_paths": [str(tmp_path)]})

        assert "take_screenshot" in registry._tools

    def test_a_reading_grant_alone_does_not_register_it(self, tmp_path):
        registry = build_registry({"allowed_paths": [str(tmp_path)]})

        assert "take_screenshot" not in registry._tools

    def test_it_is_not_registered_without_a_capture_backend(
        self, tmp_path, monkeypatch, caplog
    ):
        # The factory's standing rule: a tool whose dependency is absent is
        # missing rather than present and failing. A headless server should
        # never be offered a screenshot it cannot take.
        import vision.capture

        monkeypatch.setattr(
            vision.capture, "default_screen_capture", lambda *a, **k: None
        )

        with caplog.at_level("INFO"):
            registry = build_registry({"writable_paths": [str(tmp_path)]})

        assert "take_screenshot" not in registry._tools
        assert "write_file" in registry._tools, (
            "the writers must still register - only the screenshot depends "
            "on a display"
        )
        assert "take_screenshot not registered" in caplog.text

    def test_the_registered_tool_writes_to_the_granted_root(self, tmp_path):
        registry = build_registry({"writable_paths": [str(tmp_path)]})

        instance = registry._tools["take_screenshot"]

        assert instance.roots == [tmp_path.resolve()]

    def test_no_new_settable_path_was_invented(self):
        # The destination is `writable_paths`, which is deliberately not
        # settable over the wire. A second setting for the same question
        # could disagree with the first.
        from core.settings_store import ALLOWED

        for path in ALLOWED:
            assert "screenshot" not in path
            assert not path.startswith("tools.writable_paths")
