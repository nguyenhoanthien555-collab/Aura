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
    MockScreenCapture,
    MockWindowReader,
    ScreenCapture,
    ScreenshotCapture,
    WindowReader,
    default_window_reader,
)
from vision.processor import (
    MockVisionProcessor,
    VisionProcessor,
    WindowTitleProcessor,
)
from vision.manager import VisionManager

__all__ = [
    "VisionContext",
    "Frame",
    "ScreenCapture",
    "ScreenshotCapture",
    "MockScreenCapture",
    "WindowReader",
    "MockWindowReader",
    "default_window_reader",
    "VisionProcessor",
    "WindowTitleProcessor",
    "MockVisionProcessor",
    "VisionManager",
]
