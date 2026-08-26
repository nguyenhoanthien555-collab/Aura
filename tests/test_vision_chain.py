"""
Processor chain tests.

Phase 19.1's claim in one line: a description degrades instead of
disappearing. A pixel processor used to *replace* `WindowTitleProcessor`,
and `VisionManager.refresh` reads an empty description as "no
observation" - so an owner whose Ollama daemon was down traded a working
sentence ("User is browsing the web in Chrome") for None by switching on
the feature meant to improve it. `ProcessorChain` is the fix, and this
file is the floor under it.

Two things here are more than a five line loop, because they are the two
ways that bug comes back:

  * both decline modes advance the chain. `OllamaVisionProcessor` returns
    "" for every failure it has and never raises; `CloudVisionProcessor`
    declines by raising `ProviderUnavailableError`. A chain that handled
    only one of those would fall through for one backend and go silent
    for the other.

  * `WindowTitleProcessor` is last in what the composition root builds,
    so a machine with no reachable vision model still says which window
    is in front.

Nothing here touches a network. `urlopen` is patched wherever a real
Ollama processor is used, cloud providers are local fakes that record
what they were handed, and the tests that need a key in the environment
are given a string that is deliberately not one.
"""

import logging
import urllib.error

import pytest

from brain.providers.errors import ProviderUnavailableError
from vision.capture import Frame
from vision.cloud_processor import CloudVisionProcessor
from vision.ollama_processor import OllamaVisionProcessor
from vision.processor import (
    MockVisionProcessor,
    ProcessorChain,
    VisionProcessor,
    WindowTitleProcessor,
)


# ----------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------

class BrokenProcessor:
    """
    A processor that declines by raising, the way cloud vision does.

    The class name matters: `ProcessorChain._warn_once` logs
    `type(processor).__name__`, and the warning tests assert on it.
    """

    def __init__(self, error: Exception | None = None):

        self.error = error or ProviderUnavailableError("provider is down")
        self.calls = 0

    def describe(self, frame: Frame | None, window_title: str = "") -> str:

        self.calls += 1

        raise self.error


class Recorder:
    """A processor that keeps every (frame, window_title) it was handed."""

    def __init__(self, description: str = ""):

        self.description = description
        self.calls = []

    def describe(self, frame: Frame | None, window_title: str = "") -> str:

        self.calls.append((frame, window_title))

        return self.description


class FakeVisionProvider:
    """
    A cloud vision provider that records the prompt and sends nothing.

    Shaped like `GeminiVisionProvider` exactly where
    `CloudVisionProcessor` touches it - a name, the vision flag and
    `describe_image` - and like nothing else.
    """

    provider_name = "fake-vision"
    supports_vision = True

    def __init__(self, description: str = "A window is open."):

        self.description = description
        self.prompts = []

    def describe_image(self, prompt: str, image: bytes, mime: str) -> str:

        self.prompts.append((prompt, image, mime))

        return self.description


def rgb_frame(width: int = 4, height: int = 3, source: str = "screen") -> Frame:
    """A tiny solid frame in the format the desktop backends produce."""

    return Frame(
        width=width,
        height=height,
        data=bytes([10, 20, 30]) * (width * height),
        image_format="rgb",
        source=source,
    )


def unreachable_daemon(monkeypatch) -> None:
    """Every Ollama request refused, without a socket being opened."""

    def refuse(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(
        "vision.ollama_processor.urllib.request.urlopen",
        refuse,
    )


# ----------------------------------------------------------------------
# The first real description wins
# ----------------------------------------------------------------------

def test_the_first_real_description_wins():
    """
    A fallback, not a fan-out. A processor behind one that answered is
    not consulted at all - asking the expensive processor first only pays
    off if the cheap one costs nothing when it does not run.
    """

    first = MockVisionProcessor("A browser window is open on a news site")
    second = MockVisionProcessor("User is browsing the web in Chrome")

    chain = ProcessorChain([first, second])

    assert chain.describe(rgb_frame(), "News - Chrome") == (
        "A browser window is open on a news site"
    )

    assert first.calls == 1
    assert second.calls == 0


# ----------------------------------------------------------------------
# Both ways of declining advance the chain
# ----------------------------------------------------------------------

def test_an_empty_return_advances_the_chain():
    """An empty string is the protocol's "nothing to say", not an answer."""

    chain = ProcessorChain([
        MockVisionProcessor(""),
        MockVisionProcessor("User is browsing the web in Chrome"),
    ])

    assert chain.describe(rgb_frame(), "News - Chrome") == (
        "User is browsing the web in Chrome"
    )


def test_a_whitespace_only_return_advances_the_chain():
    """
    A model that replies with a newline has said nothing, and the manager
    would render it as "[screen]   ". The chain strips before it decides,
    so blank is blank however it is spelled.
    """

    chain = ProcessorChain([
        MockVisionProcessor("   "),
        MockVisionProcessor("User is using Spotify"),
    ])

    assert chain.describe(None, "Spotify") == "User is using Spotify"


def test_the_way_ollama_declines_advances_the_chain(monkeypatch):
    """
    Decline mode one, from the real class. `OllamaVisionProcessor`
    returns "" for every failure it has - dead daemon, HTTP error, model
    not pulled, unencodable frame - and never raises. A chain that
    advanced only on an exception would go silent for this backend.
    """

    pytest.importorskip("PIL", reason="Pillow is an optional vision extra")

    unreachable_daemon(monkeypatch)

    pixels = OllamaVisionProcessor(host="http://127.0.0.1:11434")

    # The decline mode itself, so what follows pins a real behaviour
    # rather than this file's belief about one.
    assert pixels.describe(rgb_frame()) == ""

    chain = ProcessorChain([pixels, WindowTitleProcessor()])

    assert chain.describe(
        rgb_frame(), "main.py - AURA - Visual Studio Code"
    ) == "User is editing Python code in Visual Studio Code (file: main.py)"


def test_the_way_cloud_vision_declines_advances_the_chain():
    """
    Decline mode two, from the real class. `CloudVisionProcessor` raises
    `ProviderUnavailableError` once it has no provider left, so a chain
    that only checked for "" would let that reach
    `VisionManager.refresh` instead of falling through - the test above's
    bug, one backend over.
    """

    pytest.importorskip("PIL", reason="Pillow is an optional vision extra")

    cloud = CloudVisionProcessor([])

    with pytest.raises(ProviderUnavailableError):
        cloud.describe(rgb_frame())

    chain = ProcessorChain([cloud, WindowTitleProcessor()])

    assert chain.describe(rgb_frame(), "Discord") == "User is chatting in Discord"


def test_any_raise_advances_the_chain():
    """
    Not only the provider errors. The chain is the floor under the
    prompt, so a TypeError from a misbehaving processor must cost the
    description, not the turn.
    """

    broken = BrokenProcessor(TypeError("describe() got an unexpected keyword"))

    chain = ProcessorChain([
        broken,
        MockVisionProcessor("User is at the desktop"),
    ])

    assert chain.describe(None, "") == "User is at the desktop"
    assert broken.calls == 1


# ----------------------------------------------------------------------
# An exhausted chain is quiet, never loud
# ----------------------------------------------------------------------

def test_an_exhausted_chain_describes_nothing():
    """
    Nobody had anything, so "" is the whole answer - and it is one the
    manager already knows how to read.
    """

    chain = ProcessorChain([
        MockVisionProcessor(""),
        MockVisionProcessor("   "),
    ])

    assert chain.describe(rgb_frame(), "") == ""


def test_a_chain_where_every_processor_raised_describes_nothing():
    """
    Still "" and still not an exception. `VisionManager` treats an empty
    description as "no vision context" and carries on; a raise would
    reach the turn, so the chain's own failure mode has to be the
    protocol's.
    """

    chain = ProcessorChain([
        BrokenProcessor(),
        BrokenProcessor(RuntimeError("boom")),
    ])

    assert chain.describe(rgb_frame(), "Discord") == ""


# ----------------------------------------------------------------------
# None entries and the empty chain
# ----------------------------------------------------------------------

def test_none_entries_are_dropped_at_construction():
    """
    So a caller can write `ProcessorChain([pixels_or_none, titles])`
    without first deciding whether the optional half exists - which is
    how the composition root assembles it.
    """

    titles = WindowTitleProcessor()

    chain = ProcessorChain([None, titles, None])

    assert chain.processors == [titles]
    assert chain.describe(None, "Discord") == "User is chatting in Discord"


def test_a_chain_with_nothing_in_it_describes_nothing():
    """An all-None list is an empty chain, and an empty chain is quiet."""

    assert ProcessorChain([]).processors == []
    assert ProcessorChain([]).describe(rgb_frame(), "Discord") == ""

    assert ProcessorChain([None, None]).processors == []
    assert ProcessorChain([None, None]).describe(rgb_frame(), "Discord") == ""


# ----------------------------------------------------------------------
# What a consulted processor is handed
# ----------------------------------------------------------------------

def test_the_frame_and_title_reach_every_processor_consulted():
    """
    Passed through, not rebuilt. The cheap processor reads the title and
    the pixel ones read the frame, so a chain that dropped either
    argument would silence half of itself.
    """

    frame = rgb_frame()

    declines = Recorder("")
    answers = Recorder("User is chatting in Discord")

    chain = ProcessorChain([declines, answers])

    assert chain.describe(frame, "Discord") == "User is chatting in Discord"

    assert declines.calls == [(frame, "Discord")]
    assert answers.calls == [(frame, "Discord")]

    # The same object, not an equal one: nothing in between re-encodes.
    assert declines.calls[0][0] is frame
    assert answers.calls[0][0] is frame


# ----------------------------------------------------------------------
# Warned once, then debug
# ----------------------------------------------------------------------

def test_a_repeatedly_failing_processor_is_warned_about_once(caplog):
    """
    The manager re-observes every `min_interval` seconds - 2 by default -
    so a backend that is down stays down. A warning per attempt would
    bury the log under one repeated line, and a warning that is always
    there is one nobody reads; the repeats go to debug instead.
    """

    caplog.set_level(logging.DEBUG, logger="Aura")

    chain = ProcessorChain([BrokenProcessor(), WindowTitleProcessor()])

    for _ in range(3):
        assert chain.describe(None, "Discord") == "User is chatting in Discord"

    warnings = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING
    ]

    assert len(warnings) == 1
    assert "BrokenProcessor" in warnings[0].getMessage()

    repeats = [
        record
        for record in caplog.records
        if record.levelno == logging.DEBUG
        and "failed again" in record.getMessage()
    ]

    assert len(repeats) == 2


def test_each_failing_position_is_warned_about_for_itself(caplog):
    """
    `_warned` is keyed by position, so two dead backends are two lines
    and an owner reading the log can tell which one to fix - still one
    line each, however long they stay down.
    """

    caplog.set_level(logging.DEBUG, logger="Aura")

    chain = ProcessorChain([
        BrokenProcessor(),
        BrokenProcessor(RuntimeError("also down")),
        WindowTitleProcessor(),
    ])

    chain.describe(None, "Discord")
    chain.describe(None, "Discord")

    warnings = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING
    ]

    assert len(warnings) == 2


def test_an_empty_return_is_never_warned_about(caplog):
    """
    "" is documented behaviour, and the layer that knows *why* it is
    empty has already logged it - `OllamaVisionProcessor._ask` says
    "Ollama unreachable at %s" itself. A second line here would repeat
    that and know less.
    """

    caplog.set_level(logging.DEBUG, logger="Aura")

    chain = ProcessorChain([
        MockVisionProcessor(""),
        WindowTitleProcessor(),
    ])

    assert chain.describe(None, "Discord") == "User is chatting in Discord"

    assert [
        record
        for record in caplog.records
        if record.levelno >= logging.WARNING
    ] == []


# ----------------------------------------------------------------------
# Contract with the rest of vision/
# ----------------------------------------------------------------------

def test_the_chain_is_itself_a_vision_processor():
    """
    It goes wherever a single processor went, which is what let the
    composition root start returning one without the manager changing.
    """

    assert isinstance(ProcessorChain([WindowTitleProcessor()]), VisionProcessor)


# ----------------------------------------------------------------------
# The composition root
#
# Where the order stops being a property of a class and becomes a fact
# about the running program. No network: the key below is a string that
# is not a key, and no test in this section asks a provider anything.
# ----------------------------------------------------------------------

def build_chain(**vision):
    """The chain the launcher would build for this `vision:` section."""

    pytest.importorskip("PIL", reason="Pillow is an optional vision extra")

    from launcher.services import _build_vision_processor

    settings = {
        "cloud_model": "gemini-3.6-flash",
        "ollama_model": "qwen2.5vl:7b",
        **vision,
    }

    config = {
        "vision": settings,
        "llm": {"model": "gemini-3.6-flash", "host": ""},
    }

    return _build_vision_processor(settings, config)


def test_the_built_chain_is_local_then_cloud_then_titles(monkeypatch):
    """
    Phase 19.1's order, and the last position is the guarantee: a machine
    with no reachable vision model still describes the active window
    instead of reporting nothing. Local first is a section 30 statement
    as much as a cost one - the model on this machine gets first refusal
    on the owner's screen, and pixels only leave when it had nothing to
    say.
    """

    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    chain = build_chain(send_screen_to_cloud=True)

    assert [type(p).__name__ for p in chain.processors] == [
        "OllamaVisionProcessor",
        "CloudVisionProcessor",
        "WindowTitleProcessor",
    ]


def test_a_dead_local_model_still_describes_the_window(monkeypatch):
    """
    The regression end to end, through the launcher's own chain rather
    than a hand-built one: `capture_screen: true` with nothing listening
    on 11434 used to report None where titles-only reported a sentence.
    """

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    chain = build_chain()

    unreachable_daemon(monkeypatch)

    assert chain.describe(
        rgb_frame(), "bus.ts - events - Visual Studio Code"
    ) == "User is editing TypeScript code in Visual Studio Code (file: bus.ts)"


# ----------------------------------------------------------------------
# The cloud switch is off until the owner says otherwise
# ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "vision",
    [
        {},
        {"send_screen_to_cloud": False},
    ],
)
def test_a_provider_key_is_not_permission_to_send_a_screenshot(
    monkeypatch, vision
):
    """
    Section 30, and the reason `_build_cloud_vision` has two gates.
    `build_cloud_vision_processor` builds a provider from any key in the
    environment - and a real key sits in this repository's own `.env` -
    so a key configured for *text* generation would otherwise start
    uploading pictures of the owner's desktop. A key is permission to
    talk to a provider. It is not permission to send them a picture of
    this screen.
    """

    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")

    chain = build_chain(**vision)

    names = [type(p).__name__ for p in chain.processors]

    assert "CloudVisionProcessor" not in names
    assert names == ["OllamaVisionProcessor", "WindowTitleProcessor"]


def test_config_yaml_ships_the_cloud_switch_off():
    """
    The committed file, checked as a file. A default that is right and a
    config.yaml that overrode it would pass every test above and still
    ship the screenshots.
    """

    from pathlib import Path

    import yaml

    config = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))

    vision = config.get("vision") or {}

    assert vision.get("send_screen_to_cloud") is False


# ----------------------------------------------------------------------
# CloudVisionProcessor._compact
# ----------------------------------------------------------------------

def test_compact_encodes_a_raw_rgb_frame_as_jpeg():
    """
    Phase 19's other half. A device uploads an encoded image and
    `Image.open` finds its header; the desktop backends produce raw RGB,
    which has no header, so every desktop frame came back as
    ProviderUnavailableError("Screenshot could not be decoded") and the
    cloud link was unreachable from this machine.
    """

    pytest.importorskip("PIL", reason="Pillow is an optional vision extra")

    frame = Frame(
        width=2,
        height=2,
        data=bytes(2 * 2 * 3),
        image_format="rgb",
        source="screen",
    )

    image, mime = CloudVisionProcessor([])._compact(frame)

    assert mime == "image/jpeg"

    # The markers rather than a length: a provider is handed one known
    # format, and "some bytes came back" would pass for raw RGB too.
    assert image.startswith(b"\xff\xd8")
    assert image.endswith(b"\xff\xd9")


def test_compact_refuses_a_byte_count_the_geometry_disagrees_with():
    """
    `Image.frombytes` is the raw branch's validation - there is no
    separate length check - and a frame short of width*height*3 has to
    raise rather than reach a provider as garbage pixels.
    """

    pytest.importorskip("PIL", reason="Pillow is an optional vision extra")

    frame = Frame(
        width=64,
        height=64,
        data=bytes(300),
        image_format="rgb",
        source="screen",
    )

    with pytest.raises(ProviderUnavailableError):
        CloudVisionProcessor([])._compact(frame)


# ----------------------------------------------------------------------
# The prompt names the screen it was handed
# ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "source, subject",
    [
        ("phone", "Android phone screen"),
        ("screen", "computer screen"),
        ("mock", "screen"),
    ],
)
def test_the_prompt_names_the_right_screen(source, subject):
    """
    The prompt ends with "Do not invent text", so the prompt itself must
    not contain an invention. It said "Android screen" for every frame
    once - true of the only caller at the time, and a lie the moment
    phase 19 let a desktop frame reach here. Telling a vision model it is
    looking at a phone is an invitation to describe one.
    """

    pytest.importorskip("PIL", reason="Pillow is an optional vision extra")

    provider = FakeVisionProvider()

    described = CloudVisionProcessor([provider]).describe(
        rgb_frame(source=source)
    )

    assert described == "A window is open."

    prompt, image, mime = provider.prompts[0]

    assert f"Describe this {subject} accurately" in prompt
    assert prompt.endswith("Do not invent text.")
    assert mime == "image/jpeg"
