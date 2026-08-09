"""
Vision tests.

No display, no screenshot library, no camera. The window reader and the
screen capture are both injected, so the whole pipeline

    window title / frame  ->  processor  ->  VisionContext  ->  prompt

runs deterministically in memory.

The clock is injected too. Vision is throttled, and a throttle tested
against the wall clock is a flaky test waiting to happen.
"""

import pytest

from brain.message import Message
from brain.prompt_builder import PromptBuilder
from brain.prompt_sections import VISION

from events.bus import EventBus
from events.types import VisionUpdateEvent

from vision.capture import Frame, MockScreenCapture, MockWindowReader
from vision.context import VisionContext
from vision.manager import VisionManager
from vision.processor import MockVisionProcessor, WindowTitleProcessor


class FakeClock:
    """A clock the test moves by hand."""

    def __init__(self, now: float = 0.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def manager_for(title: str = "", **kwargs) -> VisionManager:
    """
    A manager that never touches the real desktop.

    `window_reader` is always injected: the default is the live user32
    reader on Windows, which would make these tests depend on whatever
    window happens to be focused.
    """

    kwargs.setdefault("enabled", True)

    return VisionManager(
        window_reader=MockWindowReader(title=title),
        **kwargs,
    )


# ----------------------------------------------------------------------
# VisionContext
# ----------------------------------------------------------------------

def test_vision_context_renders_source_and_description():
    context = VisionContext(
        source="screen",
        description="User is editing Python code",
    )

    assert context.render() == "[screen] User is editing Python code"


def test_empty_description_is_empty_context():
    assert VisionContext(source="screen", description="   ").is_empty()


# ----------------------------------------------------------------------
# WindowTitleProcessor
# ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "title, expected",
    [
        (
            "main.py - AURA - Visual Studio Code",
            "User is editing Python code in Visual Studio Code (file: main.py)",
        ),
        (
            "bus.ts - events - Visual Studio Code",
            "User is editing TypeScript code in Visual Studio Code (file: bus.ts)",
        ),
        (
            "Windows PowerShell",
            "User is working in a terminal (Windows PowerShell)",
        ),
    ],
)
def test_window_title_becomes_a_sentence(title, expected):
    assert WindowTitleProcessor().describe(None, title) == expected


def test_browser_titles_mention_browsing():
    described = WindowTitleProcessor().describe(
        None, "Python docs - Google Chrome"
    )

    assert "browsing the web" in described
    assert "Chrome" in described


def test_unknown_application_still_produces_something_usable():
    described = WindowTitleProcessor().describe(
        None, "Untitled - SomeObscureApp"
    )

    assert described.startswith("User is using SomeObscureApp")


def test_no_window_title_describes_nothing():
    assert WindowTitleProcessor().describe(None, "") == ""
    assert WindowTitleProcessor().describe(None, "   ") == ""


def test_description_is_length_capped():
    described = WindowTitleProcessor().describe(None, "x" * 500)

    assert len(described) <= 200


# ----------------------------------------------------------------------
# VisionManager: enabling
# ----------------------------------------------------------------------

def test_vision_is_off_by_default():
    """Reading someone's screen is opt in, always."""

    manager = VisionManager(window_reader=MockWindowReader(title="anything"))

    assert manager.enabled is False
    assert manager.get_context() is None


def test_the_default_processor_needs_no_optional_dependency():
    """
    Window titles are the default, so a bare install still has vision.

    The pixel processor is a composition decision made by
    launcher/services.py, not a default buried in the manager.
    """

    manager = VisionManager(window_reader=MockWindowReader(title="anything"))

    assert isinstance(manager.processor, WindowTitleProcessor)


def test_importing_vision_does_not_import_the_image_stack():
    """
    Regression: `from vision.capture import ScreenshotCapture` used to
    fail with ModuleNotFoundError: No module named 'PIL', because the
    package imported the manager, which imported the Ollama processor,
    which imported Pillow at module level.

    Capture must not depend on the encoder.
    """

    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import vision, sys;"
            "from vision.capture import ScreenshotCapture;"
            "assert 'vision.ollama_processor' not in sys.modules, "
            "'manager imported the Ollama processor eagerly';"
            "assert 'PIL' not in sys.modules, 'Pillow was imported eagerly'",
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_enabled_manager_describes_the_active_window():
    manager = manager_for("main.py - AURA - Visual Studio Code")

    context = manager.get_context()

    assert context is not None
    assert context.source == "screen"
    assert "Python" in context.description


def test_nothing_observable_yields_no_context():
    assert manager_for("").get_context() is None


def test_processing_failure_yields_no_context():
    class BrokenProcessor:
        def describe(self, frame, window_title=""):
            raise RuntimeError("OCR exploded")

    manager = manager_for("anything", processor=BrokenProcessor())

    assert manager.get_context() is None


def test_capture_failure_does_not_prevent_a_description():
    """Pixels are optional; the window title alone is enough."""

    class BrokenCapture:
        def capture(self):
            raise RuntimeError("no display")

        def is_available(self):
            return False

    manager = manager_for(
        "main.py - AURA - Visual Studio Code",
        capture=BrokenCapture(),
    )

    assert manager.get_context() is not None


def test_frames_are_passed_to_the_processor():
    frame = Frame(width=800, height=600, data=b"pixels", source="mock")
    capture = MockScreenCapture(frames=[frame])

    seen = {}

    class RecordingProcessor:
        def describe(self, frame, window_title=""):
            seen["frame"] = frame
            seen["title"] = window_title
            return "described"

    manager = manager_for(
        "Some Window",
        capture=capture,
        processor=RecordingProcessor(),
    )

    manager.refresh()

    assert seen["frame"] is frame
    assert seen["title"] == "Some Window"
    assert capture.captures == 1


# ----------------------------------------------------------------------
# VisionManager: throttling
# ----------------------------------------------------------------------

def test_repeated_turns_reuse_a_fresh_observation():
    clock = FakeClock()
    processor = MockVisionProcessor("User is at the desktop")

    manager = manager_for(
        "Desktop",
        processor=processor,
        clock=clock,
        min_interval=2.0,
    )

    manager.get_context()
    manager.get_context()
    manager.get_context()

    assert processor.calls == 1


def test_observation_is_retaken_once_it_goes_stale():
    clock = FakeClock()
    processor = MockVisionProcessor("User is at the desktop")

    manager = manager_for(
        "Desktop",
        processor=processor,
        clock=clock,
        min_interval=2.0,
    )

    manager.get_context()
    clock.advance(5.0)
    manager.get_context()

    assert processor.calls == 2


def test_refresh_ignores_the_throttle():
    clock = FakeClock()
    processor = MockVisionProcessor("User is at the desktop")

    manager = manager_for("Desktop", processor=processor, clock=clock)

    manager.refresh()
    manager.refresh()

    assert processor.calls == 2


# ----------------------------------------------------------------------
# VisionManager: events
# ----------------------------------------------------------------------

def test_first_observation_is_announced():
    bus = EventBus()
    seen = []
    bus.subscribe(VisionUpdateEvent, seen.append)

    manager_for("main.py - AURA - Visual Studio Code", events=bus).refresh()

    assert len(seen) == 1
    assert "Python" in seen[0].description


def test_an_unchanged_screen_is_not_announced_again():
    """A static screen must not spam the bus once per turn."""

    bus = EventBus()
    seen = []
    bus.subscribe(VisionUpdateEvent, seen.append)

    manager = manager_for("Desktop", events=bus)

    manager.refresh()
    manager.refresh()
    manager.refresh()

    assert len(seen) == 1


def test_a_changed_screen_is_announced():
    bus = EventBus()
    seen = []
    bus.subscribe(VisionUpdateEvent, seen.append)

    reader = MockWindowReader(
        titles=[
            "main.py - AURA - Visual Studio Code",
            "Python docs - Google Chrome",
        ]
    )

    manager = VisionManager(window_reader=reader, events=bus, enabled=True)

    manager.refresh()
    manager.refresh()

    assert len(seen) == 2
    assert "editing" in seen[0].description
    assert "browsing" in seen[1].description


# ----------------------------------------------------------------------
# VisionContext reaching the prompt
# ----------------------------------------------------------------------

def test_prompt_builder_renders_a_vision_section():
    prompt = PromptBuilder().build(
        history=[],
        user_message=Message(role="user", content="what am I doing"),
        vision=VisionContext(
            source="screen",
            description="User is editing Python code",
        ),
    )

    assert VISION in prompt
    assert "[screen] User is editing Python code" in prompt


def test_prompt_omits_the_vision_section_when_there_is_nothing_to_see():
    prompt = PromptBuilder().build(
        history=[],
        user_message=Message(role="user", content="hi"),
        vision=None,
    )

    assert VISION not in prompt


def test_prompt_omits_the_vision_section_for_an_empty_description():
    prompt = PromptBuilder().build(
        history=[],
        user_message=Message(role="user", content="hi"),
        vision=VisionContext(source="screen", description=""),
    )

    assert VISION not in prompt


def test_prompt_builder_accepts_any_object_with_the_right_shape():
    """
    brain/ reads .source and .description structurally, which is what
    keeps it from importing vision/.
    """

    class NotAVisionContext:
        source = "camera"
        description = "User is holding a mug"

    prompt = PromptBuilder().build(
        history=[],
        user_message=Message(role="user", content="hi"),
        vision=NotAVisionContext(),
    )

    assert "[camera] User is holding a mug" in prompt
