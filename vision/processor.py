"""
Vision processor.

Turns a raw observation into one sentence a language model can use.

The bundled processor reads the foreground window title, because that
single string is the cheapest honest answer to "what is the user doing"
- no pixels, no OCR, no model, no privacy surprise. Pixel data is
accepted and currently unused; an image model can be dropped in later by
implementing the same `describe()` method.
"""

import os
import re
from typing import Protocol, runtime_checkable

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
