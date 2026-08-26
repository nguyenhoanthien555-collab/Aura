"""
Vision processor.

Turns a raw observation into one sentence a language model can use.

The cheapest processor reads the foreground window title, because that
single string is the cheapest honest answer to "what is the user doing"
- no pixels, no OCR, no model, no privacy surprise. Pixel processors
live beside it (`OllamaVisionProcessor`, `CloudVisionProcessor`) and
implement the same `describe()` method; `ProcessorChain` puts one in
front of the other so a description degrades to the title rather than
disappearing when the image model cannot answer.
"""

import os
import re
from typing import Protocol, runtime_checkable

from core.logger import logger
from vision.capture import Frame


MAX_DESCRIPTION = 200


@runtime_checkable
class VisionProcessor(Protocol):
    """
    Describes what was observed, in plain language.

    Returns "" when there is nothing worth saying. The manager treats an
    empty description as "no vision context", so a quiet processor costs
    the prompt nothing.
    """

    def describe(self, frame: Frame | None, window_title: str = "") -> str:
        ...


class MockVisionProcessor:
    """Fixed description. Used by tests and as a safe default."""

    sends_pixels_offsite = False

    def __init__(self, description: str = "User is at the desktop"):

        self.description = description
        self.calls = 0

    def describe(self, frame: Frame | None, window_title: str = "") -> str:

        self.calls += 1

        return self.description


# ----------------------------------------------------------------------
# Window title processor
# ----------------------------------------------------------------------

EDITORS = (
    "visual studio code", "vs code", "cursor", "pycharm", "intellij",
    "webstorm", "android studio", "sublime text", "notepad++", "notepad",
    "vim", "neovim", "emacs", "atom", "visual studio", "xcode", "zed",
)

BROWSERS = (
    "chrome", "firefox", "edge", "brave", "opera", "safari", "chromium",
    "arc", "vivaldi", "tor browser",
)

TERMINALS = (
    "powershell", "command prompt", "windows terminal", "terminal",
    "cmd.exe", "wsl", "bash", "git bash", "konsole", "iterm",
)

CHAT = (
    "discord", "slack", "telegram", "messenger", "microsoft teams",
    "whatsapp", "zalo", "skype",
)

MEDIA = (
    "spotify", "youtube", "vlc", "netflix", "windows media player",
    "twitch",
)

LANGUAGES = {
    ".py": "Python", ".pyi": "Python",
    ".js": "JavaScript", ".mjs": "JavaScript", ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".java": "Java", ".kt": "Kotlin", ".swift": "Swift",
    ".c": "C", ".h": "C", ".cpp": "C++", ".cc": "C++", ".hpp": "C++",
    ".cs": "C#", ".go": "Go", ".rs": "Rust", ".rb": "Ruby",
    ".php": "PHP", ".lua": "Lua", ".r": "R", ".dart": "Dart",
    ".html": "HTML", ".css": "CSS", ".scss": "CSS", ".vue": "Vue",
    ".sql": "SQL", ".sh": "Shell", ".ps1": "PowerShell",
    ".json": "JSON", ".yaml": "YAML", ".yml": "YAML", ".toml": "TOML",
    ".md": "Markdown", ".txt": "text", ".xml": "XML",
}


FILENAME = re.compile(r"[\w\-. ]+\.[A-Za-z0-9]{1,6}")


class WindowTitleProcessor:
    """
    Describes the foreground window.

    Windows titles are conventionally "document - application", so the
    last segment names the app and the first names what is open in it.
    Everything here degrades gracefully: an unrecognised app still
    produces "User is using <app>".
    """

    # Where the pixels can end up, advertised so a caller can price the
    # act rather than guess at it. Read with getattr and a False default
    # at every use site, so this is a fact a processor may offer and not
    # a member the VisionProcessor protocol demands.
    # Titles never leave the machine; there is nothing here to send.
    sends_pixels_offsite = False

    def describe(self, frame: Frame | None, window_title: str = "") -> str:

        title = (window_title or "").strip()

        if not title:
            return ""

        subject, app = self._split(title)

        app_key = app.lower()

        description = self._describe_app(app, app_key, subject)

        return description[:MAX_DESCRIPTION]

    # ------------------------------------------------------------------

    @staticmethod
    def _split(title: str) -> tuple[str, str]:
        """
        Split "file - project - App" into (subject, app).

        Falls back to treating the whole title as both when there is no
        separator to work with.
        """

        parts = [
            part.strip()
            for part in re.split(r"\s+[-–—]\s+", title)
            if part.strip()
        ]

        if len(parts) == 1:
            return parts[0], parts[0]

        return parts[0], parts[-1]

    def _describe_app(self, app: str, app_key: str, subject: str) -> str:

        if self._matches(app_key, EDITORS):
            return self._describe_editing(app, subject)

        if self._matches(app_key, BROWSERS):
            return self._with_subject(
                f"User is browsing the web in {app}", subject, "page"
            )

        if self._matches(app_key, TERMINALS):
            return f"User is working in a terminal ({app})"

        if self._matches(app_key, CHAT):
            return f"User is chatting in {app}"

        if self._matches(app_key, MEDIA):
            return self._with_subject(
                f"User is using {app}", subject, "playing"
            )

        return self._with_subject(f"User is using {app}", subject, "on")

    @staticmethod
    def _matches(app_key: str, names) -> bool:

        return any(name in app_key for name in names)

    def _describe_editing(self, app: str, subject: str) -> str:

        filename = self._filename(subject)

        if filename:

            language = LANGUAGES.get(
                os.path.splitext(filename)[1].lower()
            )

            if language:
                return (
                    f"User is editing {language} code in {app} "
                    f"(file: {filename})"
                )

            return f"User is editing {filename} in {app}"

        return f"User is writing code in {app}"

    @staticmethod
    def _filename(subject: str) -> str:

        match = FILENAME.search(subject or "")

        if not match:
            return ""

        return match.group(0).strip()

    @staticmethod
    def _with_subject(base: str, subject: str, label: str) -> str:

        subject = (subject or "").strip()

        if not subject or subject.lower() in base.lower():
            return base

        return f"{base} ({label}: {subject})"


# ----------------------------------------------------------------------
# Processor chain
#
# Phase 19 opens on an inversion rather than a missing feature: turning
# `vision.capture_screen` on made vision report *less*. The pixel
# processor replaced WindowTitleProcessor instead of layering over it,
# and `VisionManager.refresh` reads an empty description as "no
# observation" and drops the context to None - so an owner whose Ollama
# daemon is not running traded a working sentence ("User is browsing the
# web in Chrome") for nothing at all, by switching on the feature that
# was meant to improve it. Measured on this machine, both halves:
#
#     capture_screen=False -> [screen] User is browsing the web in ...
#     capture_screen=True  -> None
#
# This is the layering. Ask the expensive processor first, fall through
# to the cheap one, and a description degrades instead of vanishing.
# ----------------------------------------------------------------------

class ProcessorChain:
    """
    Ask each processor in turn; the first real description wins.

    Two ways a processor can decline, and both advance the chain,
    because the bundled implementations genuinely use both.
    `OllamaVisionProcessor` returns "" for every failure it has - dead
    daemon, HTTP error, model not pulled, unencodable frame - while
    `CloudVisionProcessor` raises `ProviderUnavailableError`. A chain
    that caught only one of those would fall through for one backend and
    go silent for the other, which is this phase's bug reintroduced one
    layer up.

    Deliberately not a `FallbackProvider`. That class is the same shape
    for text providers, and reusing it would mean `vision/` importing
    `brain/providers/` - the one dependency edge this package's own
    docstring says does not exist - to gain a five line loop that also
    advances on the wrong condition.

    Deliberately not a concatenation either. Both descriptions in the
    prompt would cost tokens to say the same thing twice: the pixel
    model can already read the window title out of the pixels, and
    "User is browsing the web in Chrome. A browser window is open on a
    news site." is two sentences where the second contains the first.
    First non-empty wins, and the cheap processor is the floor rather
    than a suffix.
    """

    def __init__(self, processors):
        """
        `processors` is tried in order. None entries are dropped, so a
        caller can write the chain as
        `ProcessorChain([pixels_or_none, WindowTitleProcessor()])`
        without deciding whether the optional half exists - the same
        courtesy `build_registry` extends to a tool whose dependency is
        missing.
        """

        self.processors = [p for p in processors if p is not None]

        # Say a raised failure once, not on every refresh. The manager
        # re-observes every `min_interval` seconds - 2 by default - so a
        # backend that is down stays down, and a warning per attempt
        # would bury the log under one repeated line. The `_warned` flag
        # idiom `GdiScreenCapture` already uses, for the same reason.
        self._warned: set[int] = set()

    @property
    def sends_pixels_offsite(self) -> bool:
        """
        True when any link in the chain can hand the frame to a third
        party.

        A property rather than a stored flag because the chain does not
        own the answer - its links do, and one of them may be replaced.
        `any`, not the first link's answer: a chain is as leaky as its
        leakiest member, and the cloud processor sits in the middle of
        the shipped order rather than at either end.
        """

        return any(
            getattr(processor, "sends_pixels_offsite", False)
            for processor in self.processors
        )

    def describe(self, frame: Frame | None, window_title: str = "") -> str:

        for index, processor in enumerate(self.processors):

            try:
                description = processor.describe(frame, window_title)

            except Exception as error:
                # A processor that raises is misbehaving by the
                # protocol's own terms - "" is how it says it has
                # nothing - so this one is worth a line even though the
                # chain recovers.
                self._warn_once(
                    index, processor, f"{type(error).__name__}: {error}"
                )
                continue

            if description and description.strip():
                return description

            # Not warned, at any level above debug. An empty return is
            # the protocol's documented "nothing worth saying", and the
            # layer that knows *why* it is empty has already logged it:
            # `OllamaVisionProcessor._ask` says "Ollama unreachable at
            # %s" at warning level itself. A second line here would
            # repeat that and know less.
            logger.debug(
                "Vision: %s had nothing to say, trying the next processor",
                type(processor).__name__,
            )

        return ""

    def _warn_once(self, index: int, processor, reason: str) -> None:

        name = type(processor).__name__

        if index in self._warned:
            logger.debug("Vision: %s failed again (%s)", name, reason)
            return

        self._warned.add(index)

        remaining = len(self.processors) - index - 1

        logger.warning(
            "Vision: %s failed (%s); %s",
            name,
            reason,
            (
                f"falling back to {remaining} more processor(s)"
                if remaining
                else "no processor left to fall back to"
            ),
        )
