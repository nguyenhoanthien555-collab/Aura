"""
A picture of the owner's screen, written where the owner allowed writing.

Section 24's "screenshots", and the shape follows from three facts that
were measured rather than assumed.

**It needs nothing installed.** `mss` is an optional dependency and is
not present on the owner's machine, so `vision.capture.ScreenshotCapture`
reported unavailable and the pixel half of vision was dead code there.
`GdiScreenCapture` was added beside it - `BitBlt` through ctypes, exactly
as `list_windows` reads user32 - and `encode_png` writes the PNG with
`zlib`, which is standard library. So the whole PC layer stays installable
by doing nothing, the way 18.1, 18.2 and 18.3 are.

**The destination is the grant the owner already gave.** A screenshot is
a file appearing on disk, so it goes through `tools.writable_paths` and
through the same containment, atomic write and root-relative reporting the
18.3 writers use - imported from `filesystem.py` rather than reimplemented,
because two containment checks can drift and the one that drifts is a
sandbox escape. No new path setting exists to disagree with that one.

**Which display is the owner's decision, not the model's.** The index is
configured, and the per-call argument builds a *fresh* capture for that
index rather than mutating a shared one, so a bad index cannot leak into
the next call's warning state.

The content is the most sensitive thing any tool in this layer produces -
a screen holds passwords in plain text, private messages, whatever
happens to be open. That is why the risk is DANGEROUS despite the tool
only creating one file, why the description says so where a confirmation
prompt will show it, and why nothing here ever puts image bytes into a
message, a log line or a return value.
"""

from pathlib import Path

from core.logger import logger
from tools.base import Parameter, Tool, ToolResult, ToolRisk, fail, ok

# One containment implementation for the whole tool layer, not two.
# These are private to `filesystem.py` and are imported anyway, which is
# the lesser of the two evils on offer: the alternative is a second
# resolve-and-compare in this file, and a second one can drift from the
# first. A drift in a message is cosmetic; a drift in `_contained` is a
# path escape. The same reasoning `run_command` uses when it imports
# `PROVIDER_KEYS` out of `brain/router.py` rather than keeping its own
# list of names to strip.
from tools.builtins.filesystem import (
    _atomic_write,
    _contained,
    _flag,
    _resolve_roots,
    _shown,
)
from vision.capture import PRIMARY_DISPLAY, encode_png


# The PNG signature, for the postcondition. Eight bytes chosen by the
# format's authors precisely so a truncated or text-translated file stops
# looking like a PNG.
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class ScreenshotTool(Tool):
    """
    Capture the screen to a PNG file.

    No size limit on the image, deliberately, and it is worth saying why
    when every other writer in this layer has one. `MAX_WRITE_BYTES`
    bounds text a *model* supplied, where the model chooses the length. A
    screenshot's size is chosen by the owner's display: 1920x1080 came out
    at 151 KB here, and the only way to make it large is to own a lot of
    monitors. There is nothing for a cap to protect against that the
    approval gate does not already cover, and a cap that refused the
    owner's own screen would be the arbitrary restriction section 2
    forbids.
    """

    name = "take_screenshot"

    description = (
        "Save a picture of the screen as a PNG file. The image will "
        "contain whatever is currently visible, including any private "
        "information on screen"
    )

    risk = ToolRisk.DANGEROUS

    parameters = (
        Parameter(
            name="path",
            description="Where to save the .png file",
        ),
        Parameter(
            name="monitor",
            description=(
                "Which display: 1 is the main screen, 0 is all of them "
                "joined together, 2 and up are the others"
            ),
            required=False,
        ),
        Parameter(
            name="overwrite",
            description="true to replace a file that already exists",
            required=False,
        ),
    )

    def __init__(
        self,
        roots: list[str] | None = None,
        monitor: int = PRIMARY_DISPLAY,
        capture_factory=None,
    ):
        """
        `capture_factory` is `monitor -> ScreenCapture | None`.

        A factory rather than a capture object, because `monitor` is a
        per-call argument and the alternative is mutating a shared
        backend's `monitor` attribute between calls. That backend also
        carries a `_warned` flag whose whole purpose is to fire once, so
        reusing one object across displays would suppress the warning for
        the second bad index - a wrong screen captured silently, which is
        the exact failure `_resolve` exists to prevent.

        None means the real one. Tests pass their own and never touch a
        display.
        """

        self.roots = _resolve_roots(roots)
        self.monitor = monitor

        if capture_factory is None:
            from vision.capture import default_screen_capture

            capture_factory = default_screen_capture

        self._capture_factory = capture_factory

    # ------------------------------------------------------------------

    def _target(self, path: str, overwrite) -> Path:
        """
        The file to write, proven to be inside a writable root.

        Everything here can refuse before a single pixel is read, which is
        the order that matters: capturing the screen and then discovering
        the path was outside the allowed directories would mean the image
        existed in memory for no reason, and on a failure path there is
        nothing to gain by having taken it.
        """

        target = _contained(path, self.roots)

        if target.is_dir():
            raise ValueError(f"{target.name} is a directory, not a file")

        # PNG bytes under a `.jpg` name is a file that lies about itself,
        # and the owner finds out when something refuses to open it. The
        # suffix is not corrected silently for the same reason
        # `write_file` does not create missing parents: the caller says
        # what they meant, and the message names the fix.
        if target.suffix.lower() != ".png":
            raise ValueError(
                f"{target.name} does not end in .png, and the file will be "
                f"a PNG - ask for a .png name instead"
            )

        if target.exists() and not _flag(overwrite, "overwrite"):
            raise ValueError(
                f"{target.name} already exists. Pass overwrite=true to "
                f"replace it, or save to a different name."
            )

        if not target.parent.is_dir():
            raise FileNotFoundError(
                f"the directory {target.parent.name} does not exist - "
                f"create it with create_directory first"
            )

        return target

    def _display(self, monitor) -> int:
        """
        The display index for this call.

        Absent means the configured one. A model that does not mention a
        display gets the screen the owner said they cared about, rather
        than index 0 - which is every monitor stitched into one wide image
        and is nobody's idea of "take a screenshot".
        """

        if monitor is None or (
            isinstance(monitor, str) and not monitor.strip()
        ):
            return self.monitor

        if isinstance(monitor, bool):
            raise ValueError("monitor must be a number, not true or false")

        try:
            return int(str(monitor).strip())
        except (TypeError, ValueError):
            raise ValueError(
                f"monitor must be a number, got {monitor!r}"
            ) from None

    capability = 'vision.capture'

    def execute(self, path: str, monitor=None, overwrite=False) -> str:

        target = self._target(path, overwrite)

        display = self._display(monitor)

        capture = self._capture_factory(display)

        if capture is None:
            raise RuntimeError(
                "screen capture is not available on this machine"
            )

        frame = capture.capture()

        if frame is None or frame.is_empty():
            raise RuntimeError("the screen could not be captured")

        data = encode_png(frame)

        _atomic_write(target, data)

        # Geometry and size only. The image itself never appears in a
        # message that will be put into a prompt, which is the same rule
        # `system_information` follows when it reports the disk and not
        # the user.
        logger.info(
            "Screenshot: monitor %s, %dx%d, %d bytes",
            display, frame.width, frame.height, len(data),
        )

        return (
            f"saved {_shown(target, self.roots)} "
            f"({frame.width}x{frame.height}, {len(data)} bytes)"
        )

    def verify(self, path: str, monitor=None, overwrite=False) -> ToolResult:
        """
        The postcondition: a decodable PNG of non-zero size is now there.

        What this can honestly check, and what it deliberately does not.

        It reads the file back and parses the header, because "the write
        did not throw" is the sentence section 11 forbids: a full disk, a
        quota, or an antivirus rewriting the file as it lands all leave a
        path that exists and a file that is not an image. The signature
        plus a parsed IHDR with a non-zero area is exactly the claim
        `execute` made - a PNG arrived - re-asked from the disk.

        It does **not** claim the picture shows the screen. Nothing can
        re-ask that: the screen has already changed by the time anyone
        looks, and a second capture to compare against would be a
        different image of a different moment. That the pixels are a
        faithful picture was established once, against an independent
        implementation, and is recorded in the state files rather than
        pretended at here every call.
        """

        target = _contained(path, self.roots)

        if not target.is_file():
            return fail(f"{target.name} is not there after saving it")

        # 8 signature + 4 length + 4 "IHDR" + 13 header + 4 crc. Reading
        # the head rather than the file: proving the image is well formed
        # does not require pulling a megabyte back off disk.
        head = target.read_bytes()[:33]

        if not head.startswith(PNG_MAGIC):
            return fail(f"{target.name} is not a PNG file")

        if head[12:16] != b"IHDR":
            return fail(f"{target.name} has no PNG header chunk")

        width = int.from_bytes(head[16:20], "big")
        height = int.from_bytes(head[20:24], "big")

        if width <= 0 or height <= 0:
            return fail(
                f"{target.name} is a PNG of nothing ({width}x{height})"
            )

        return ok(f"{target.name} reads back as a {width}x{height} PNG")

