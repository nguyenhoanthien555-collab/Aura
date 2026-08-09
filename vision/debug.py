"""
Vision debugging.

    python -m vision.debug              list monitors, save + describe monitor 1
    python -m vision.debug --monitor 2  the second display
    python -m vision.debug --all        save every monitor, describe none
    python -m vision.debug --no-model   capture only, no Ollama round trip

Answers one question: is the vision model receiving the screen the user
is actually looking at? The PNG this writes is the exact byte string that
was base64 encoded into the request, saved at the processor boundary -
not a second screenshot taken afterwards.

Writes PNGs of your screen to the current directory. Nothing here runs
as part of Aura; it exists to be run by hand.
"""

import argparse
import sys

from core.config import load_config
from vision.capture import ScreenshotCapture, default_window_reader


def list_monitors() -> list[dict]:
    """
    Every monitor mss can see, with its geometry.

    Index 0 is the union of all displays; 1 onwards are the physical
    ones. A frame that describes the wrong application is very often a
    `vision.monitor` pointing at the wrong index here.
    """

    try:
        import mss
    except ImportError:
        print("mss is not installed: pip install mss")
        return []

    with mss.mss() as screen:
        monitors = list(screen.monitors)

    for index, monitor in enumerate(monitors):
        label = "all displays combined" if index == 0 else f"display {index}"

        print(
            f"  monitor {index}: "
            f"{monitor['width']}x{monitor['height']} "
            f"at ({monitor['left']}, {monitor['top']})  {label}"
        )

    return monitors


def capture(monitor: int):
    """Grab one frame and report what came back."""

    capture_backend = ScreenshotCapture(monitor=monitor)

    if not capture_backend.is_available():
        print("Screen capture unavailable: pip install mss")
        return None

    frame = capture_backend.capture()

    if frame is None or frame.is_empty():
        print(f"monitor {monitor}: capture returned no frame")
        return None

    expected = frame.width * frame.height * 3

    print(
        f"monitor {monitor}: {frame.width}x{frame.height} "
        f"{frame.image_format}, {len(frame.data)} bytes "
        f"(expected {expected} for packed RGB)"
    )

    if len(frame.data) != expected:
        print("  WARNING: byte count does not match width*height*3")

    return frame


def describe(frame, path: str, use_model: bool) -> None:
    """
    Save the frame, then optionally ask the vision model about it.

    Both use the same processor instance, so the PNG on disk and the
    image in the request are the same bytes by construction.
    """

    settings = load_config().get("vision") or {}

    try:
        from vision.ollama_processor import (
            DEFAULT_HOST,
            DEFAULT_MODEL,
            OllamaVisionProcessor,
        )
    except ImportError as error:
        print(f"Cannot build the vision processor: {error}")
        return

    processor = OllamaVisionProcessor(
        model=settings.get("model") or DEFAULT_MODEL,
        host=settings.get("host") or DEFAULT_HOST,
        timeout=settings.get("timeout") or 120.0,
        debug_path=path,
    )

    if not use_model:
        try:
            png = processor._to_png(frame)
        except Exception as error:
            print(f"Could not encode the frame: {error}")
            return

        processor._save_debug(png)
        print(f"saved {path} ({len(png)} bytes), model not called")
        return

    title = default_window_reader().active_window()

    print(f"active window title: {title!r}")
    print(f"asking {processor.model} at {processor.host} ...")

    description = processor.describe(frame, title)

    print()
    print(f"saved {path} - open it and compare with what the model said")
    print()

    if description:
        print(f"model: {description}")
    else:
        print("model returned nothing; run with logging.level: DEBUG for why")


def main(argv=None) -> int:

    parser = argparse.ArgumentParser(
        prog="python -m vision.debug",
        description="Verify what the vision model actually receives.",
    )

    parser.add_argument(
        "--monitor",
        type=int,
        default=None,
        help="mss monitor index (default: vision.monitor from config)",
    )

    parser.add_argument(
        "--all",
        action="store_true",
        help="save every monitor as debug_monitor_N.png, describe none",
    )

    parser.add_argument(
        "--no-model",
        action="store_true",
        help="capture and save only, no Ollama request",
    )

    parser.add_argument(
        "--out",
        default="debug_screen.png",
        help="where to write the frame (default: debug_screen.png)",
    )

    arguments = parser.parse_args(argv)

    settings = load_config().get("vision") or {}

    print("monitors:")
    monitors = list_monitors()

    if not monitors:
        return 1

    print()

    if arguments.all:

        for index in range(1, len(monitors)):

            frame = capture(index)

            if frame is not None:
                describe(frame, f"debug_monitor_{index}.png", False)

        return 0

    monitor = (
        arguments.monitor
        if arguments.monitor is not None
        else settings.get("monitor", 1)
    )

    frame = capture(monitor)

    if frame is None:
        return 1

    describe(frame, arguments.out, not arguments.no_model)

    return 0


if __name__ == "__main__":
    sys.exit(main())
