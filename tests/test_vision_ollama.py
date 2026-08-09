"""
Ollama vision processor tests.

No Ollama daemon, no screen, no GPU. `urllib.request.urlopen` is patched,
so the request that would have gone to the daemon is inspected instead of
sent, and the frame is built in memory.

Pillow is genuinely required to encode a frame, so the encoding tests
skip when it is absent rather than failing - which is the same contract
the runtime honours.
"""

import base64
import io
import json
import urllib.error

import pytest

from vision.capture import Frame, MockScreenCapture, MockWindowReader
from vision.manager import VisionManager
from vision.ollama_processor import OllamaVisionProcessor
from vision.processor import VisionProcessor


PIL = pytest.importorskip("PIL", reason="Pillow is an optional vision extra")


# ----------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------

class FakeResponse:
    """The context manager urlopen returns."""

    def __init__(self, body: str):
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        return False


class Daemon:
    """
    Stands in for Ollama.

    Records the request it was handed so a test can assert on the exact
    payload, and replies with whatever it was constructed with.
    """

    def __init__(self, body: str | None = None, error: Exception | None = None):

        self.body = (
            body
            if body is not None
            else json.dumps({"message": {"content": "A window is open."}})
        )

        self.error = error
        self.requests = []

    def __call__(self, request, timeout=None):

        self.requests.append(request)
        self.timeout = timeout

        if self.error is not None:
            raise self.error

        return FakeResponse(self.body)

    # Convenience accessors for the single recorded request ------------

    @property
    def payload(self) -> dict:
        return json.loads(self.requests[-1].data.decode("utf-8"))

    @property
    def url(self) -> str:
        return self.requests[-1].full_url

    @property
    def message(self) -> dict:
        return self.payload["messages"][0]


def rgb_frame(width: int = 4, height: int = 3) -> Frame:
    """A tiny solid frame in the format ScreenshotCapture produces."""

    return Frame(
        width=width,
        height=height,
        data=bytes([10, 20, 30]) * (width * height),
        image_format="rgb",
        source="screen",
    )


@pytest.fixture
def daemon(monkeypatch):

    fake = Daemon()

    monkeypatch.setattr(
        "vision.ollama_processor.urllib.request.urlopen",
        fake,
    )

    return fake


def with_daemon(monkeypatch, **kwargs) -> Daemon:

    fake = Daemon(**kwargs)

    monkeypatch.setattr(
        "vision.ollama_processor.urllib.request.urlopen",
        fake,
    )

    return fake


# ----------------------------------------------------------------------
# Empty frames never reach the daemon
# ----------------------------------------------------------------------

def test_no_frame_describes_nothing(daemon):
    assert OllamaVisionProcessor().describe(None) == ""
    assert daemon.requests == []


def test_empty_frame_describes_nothing(daemon):
    """A capture failure must not cost a model round trip."""

    assert OllamaVisionProcessor().describe(Frame()) == ""
    assert daemon.requests == []


# ----------------------------------------------------------------------
# The request
# ----------------------------------------------------------------------

def test_request_goes_to_the_chat_endpoint(daemon):

    OllamaVisionProcessor(host="http://127.0.0.1:11434").describe(rgb_frame())

    assert daemon.url == "http://127.0.0.1:11434/api/chat"


def test_a_trailing_slash_on_the_host_does_not_double_up(daemon):

    OllamaVisionProcessor(host="http://127.0.0.1:11434/").describe(rgb_frame())

    assert daemon.url == "http://127.0.0.1:11434/api/chat"


def test_request_names_the_model(daemon):

    OllamaVisionProcessor(model="qwen2.5vl:7b").describe(rgb_frame())

    assert daemon.payload["model"] == "qwen2.5vl:7b"
    assert daemon.payload["stream"] is False


def test_request_carries_the_frame_as_a_base64_png(daemon):

    frame = rgb_frame(4, 3)

    OllamaVisionProcessor().describe(frame)

    images = daemon.message["images"]

    assert len(images) == 1

    png = base64.b64decode(images[0])

    assert png.startswith(b"\x89PNG\r\n\x1a\n")

    # The decoded image is the frame, at the frame's size.
    from PIL import Image

    decoded = Image.open(io.BytesIO(png))

    assert decoded.size == (4, 3)
    assert decoded.convert("RGB").getpixel((0, 0)) == (10, 20, 30)


def test_prompt_forbids_inventing_what_is_not_visible(daemon):
    """The grounding instruction is the fix for hallucinated screens."""

    OllamaVisionProcessor().describe(rgb_frame())

    content = daemon.message["content"].lower()

    assert "only what is visibly present" in content
    assert "do not invent" in content


def test_window_title_is_not_sent_to_the_model(daemon):
    """
    The description must come from pixels.

    Handing the model the window title invites it to describe the title
    instead of the screen, which is the failure this processor exists to
    avoid.
    """

    OllamaVisionProcessor().describe(rgb_frame(), "CurseForge")

    assert "CurseForge" not in json.dumps(daemon.payload)


def test_timeout_is_passed_to_the_request(daemon):

    OllamaVisionProcessor(timeout=45.0).describe(rgb_frame())

    assert daemon.timeout == 45.0


# ----------------------------------------------------------------------
# The response
# ----------------------------------------------------------------------

def test_description_is_taken_from_the_message_content(monkeypatch):

    with_daemon(
        monkeypatch,
        body=json.dumps(
            {"message": {"content": "  CurseForge is open on the Mods tab.  "}}
        ),
    )

    described = OllamaVisionProcessor().describe(rgb_frame())

    assert described == "CurseForge is open on the Mods tab."


def test_description_is_length_capped(monkeypatch):

    with_daemon(
        monkeypatch,
        body=json.dumps({"message": {"content": "x" * 5000}}),
    )

    assert len(OllamaVisionProcessor().describe(rgb_frame())) <= 600


@pytest.mark.parametrize(
    "body",
    [
        "not json at all",
        json.dumps({"error": "model 'qwen2.5vl:7b' not found"}),
        json.dumps({"message": {"content": ""}}),
        json.dumps({"message": {"content": "   "}}),
        json.dumps({"message": "wrong type"}),
        json.dumps({}),
        json.dumps([1, 2, 3]),
    ],
)
def test_an_unusable_response_describes_nothing(monkeypatch, body):
    """Every malformed shape degrades to no vision, never an exception."""

    with_daemon(monkeypatch, body=body)

    assert OllamaVisionProcessor().describe(rgb_frame()) == ""


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.HTTPError("u", 500, "Server Error", {}, None),
        urllib.error.HTTPError("u", 404, "Not Found", {}, None),
        urllib.error.URLError("connection refused"),
        TimeoutError("timed out"),
    ],
)
def test_a_failed_request_describes_nothing(monkeypatch, error):

    with_daemon(monkeypatch, error=error)

    assert OllamaVisionProcessor().describe(rgb_frame()) == ""


# ----------------------------------------------------------------------
# Debug frame
# ----------------------------------------------------------------------

def test_debug_frame_is_the_image_that_was_sent(daemon, tmp_path):
    """
    The saved PNG must be the exact bytes that were encoded, not a
    second screenshot taken afterwards.
    """

    target = tmp_path / "frames" / "debug_screen.png"

    OllamaVisionProcessor(debug_path=target).describe(rgb_frame())

    assert target.exists()

    sent = base64.b64decode(daemon.message["images"][0])

    assert target.read_bytes() == sent


def test_no_debug_file_is_written_by_default(daemon, tmp_path, monkeypatch):

    monkeypatch.chdir(tmp_path)

    OllamaVisionProcessor().describe(rgb_frame())

    assert list(tmp_path.iterdir()) == []


# ----------------------------------------------------------------------
# Contract with the rest of vision/
# ----------------------------------------------------------------------

def test_processor_satisfies_the_vision_processor_protocol():
    assert isinstance(OllamaVisionProcessor(), VisionProcessor)


def test_injected_into_the_manager_it_describes_a_captured_frame(daemon):

    manager = VisionManager(
        capture=MockScreenCapture(frames=[rgb_frame()]),
        processor=OllamaVisionProcessor(),
        window_reader=MockWindowReader(title="CurseForge"),
        enabled=True,
    )

    context = manager.get_context()

    assert context is not None
    assert context.description == "A window is open."
    assert len(daemon.requests) == 1


def test_a_disabled_manager_never_calls_the_model(daemon):
    """The privacy safeguard: off means no capture and no request."""

    capture = MockScreenCapture(frames=[rgb_frame()])

    manager = VisionManager(
        capture=capture,
        processor=OllamaVisionProcessor(),
        window_reader=MockWindowReader(title="CurseForge"),
        enabled=False,
    )

    assert manager.get_context() is None
    assert capture.captures == 0
    assert daemon.requests == []


def test_throttling_still_applies_to_the_model(daemon):
    """A 7B vision model is far too expensive to run once per turn."""

    clock = type("Clock", (), {"now": 0.0, "__call__": lambda self: self.now})()

    manager = VisionManager(
        capture=MockScreenCapture(frames=[rgb_frame() for _ in range(4)]),
        processor=OllamaVisionProcessor(),
        window_reader=MockWindowReader(title="CurseForge"),
        enabled=True,
        min_interval=2.0,
        clock=clock,
    )

    manager.get_context()
    manager.get_context()
    manager.get_context()

    assert len(daemon.requests) == 1

    clock.now = 5.0
    manager.get_context()

    assert len(daemon.requests) == 2


def test_refresh_always_takes_a_new_frame_and_asks_again(daemon):
    """refresh() is the escape hatch from the throttle."""

    capture = MockScreenCapture(frames=[rgb_frame(), rgb_frame()])

    manager = VisionManager(
        capture=capture,
        processor=OllamaVisionProcessor(),
        window_reader=MockWindowReader(title="CurseForge"),
        enabled=True,
    )

    manager.refresh()
    manager.refresh()

    assert capture.captures == 2
    assert len(daemon.requests) == 2
