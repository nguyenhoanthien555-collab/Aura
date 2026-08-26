"""
Vision.

    screen / window  ->  Frame + title  ->  processor  ->  VisionContext

VisionManager is the only export the rest of Aura needs; it satisfies
brain.ports.VisionProvider structurally, so brain/ never imports this
package.
"""

from vision.context import VisionContext
from vision.capture import (
    Frame,
    GdiScreenCapture,
    MockScreenCapture,
    MockWindowReader,
    ScreenCapture,
    ScreenshotCapture,
    WindowReader,
    default_screen_capture,
    default_window_reader,
    encode_png,
)
from vision.processor import (
    MockVisionProcessor,
    ProcessorChain,
    VisionProcessor,
    WindowTitleProcessor,
)
from vision.manager import VisionManager

__all__ = [
    "VisionContext",
    "Frame",
    "ScreenCapture",
    "ScreenshotCapture",
    "GdiScreenCapture",
    "MockScreenCapture",
    "WindowReader",
    "MockWindowReader",
    "default_screen_capture",
    "default_window_reader",
    "encode_png",
    "VisionProcessor",
    "WindowTitleProcessor",
    "MockVisionProcessor",
    "ProcessorChain",
    "VisionManager",
]
