"""
The windows on the owner's desktop - reading them, and bringing one forward.

Section 24's "window management", in the two halves that can be done
honestly with nothing installed. `list_windows` says what is open;
`focus_window` brings a named window to the front and then proves it
arrived.

`focus_window` is where Section 11 stops being abstract. `SetForegroundWindow`
returns zero and changes nothing whenever Windows' foreground lock applies -
a process that does not own the current foreground window, or is not
responding to input, is refused, and the refusal looks from the calling
side exactly like success. So "the call did not throw" is precisely the
sentence Section 11 forbids resting on, and the postcondition is asked
separately: `execute` performs the action, `verify` reads the foreground
window back and fails the call when the window that was asked for is not
the one in front.

That split is deliberate rather than tidy. `open_application` has no
`verify()` because its evidence is gone by the time anyone could look - a
process that started and exited leaves nothing to re-ask. A focused
window is still in front, so the condition remains askable, and asking it
where the framework can act on the answer is better than asking it inside
`execute` where a wrong answer would have to be turned into a failure by
hand.

The enumeration is behind a source object with a mock beside it, exactly
as `vision/capture.py` does for screen capture, so the tests assert
against windows they declared rather than against whatever happened to be
open on the machine running them.
"""

import os
import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from core.logger import logger
from tools.base import Parameter, Tool, ToolResult, ToolRisk, fail, ok


# How many windows one listing may name. A desktop with eleven visible
# titled windows was typical in testing; thirty is generous without
# letting a pathological session flood a prompt.
MAX_WINDOWS = 30

# How long to wait for a foreground change to take effect, and how often
# to look. A foreground switch is not synchronous - the window manager
# gets there when it gets there - so the postcondition is polled rather
# than read once, in the same bounded-poll shape the Android executor
# uses for its own verification.
FOCUS_TIMEOUT = 1.5
FOCUS_POLL = 0.1

# ShowWindow's "restore it to where it was", which is what a minimised
# window needs before it can be brought forward at all.
SW_RESTORE = 9


@dataclass(frozen=True)
class WindowInfo:
    """
    One top-level window.

    `handle` is opaque and is never shown to a model: it is a number that
    means nothing to a reader and goes stale the moment the window
    closes, so a caller names a window by its title and the handle stays
    an implementation detail on this side of the boundary.
    """

    handle: int = 0
    title: str = ""
    pid: int = 0
    minimised: bool = False
    foreground: bool = False

    def render(self) -> str:

        marks = []

        if self.foreground:
            marks.append("in front")

        if self.minimised:
            marks.append("minimised")

        suffix = f" [{', '.join(marks)}]" if marks else ""

        return f"{self.title} (pid {self.pid}){suffix}"


@runtime_checkable
class WindowSource(Protocol):

    def windows(self) -> list[WindowInfo]:
        ...

    def focus(self, handle: int) -> bool:
        ...


class MockWindowSource:
    """
    Windows a test declared, and a focus that only rearranges them.

    `focus` moves the foreground flag rather than pretending to call the
    OS, which is what lets the postcondition tests exercise both outcomes:
    a source constructed with `honour_focus=False` accepts the call and
    changes nothing, which is exactly what the real foreground lock does.
    """

    def __init__(
        self,
        windows: list[WindowInfo] | None = None,
        honour_focus: bool = True,
        accept: bool = True,
    ):

        self.items = list(windows or [])
        self.honour_focus = honour_focus
        self.accept = accept
        self.focused: list[int] = []
        self.reads = 0

    def windows(self) -> list[WindowInfo]:

        self.reads += 1

        return list(self.items)

    def focus(self, handle: int) -> bool:

        self.focused.append(handle)

        if self.honour_focus:

            self.items = [
                WindowInfo(
                    handle=window.handle,
                    title=window.title,
                    pid=window.pid,
                    minimised=False if window.handle == handle
                    else window.minimised,
                    foreground=window.handle == handle,
                )
                for window in self.items
            ]

        return self.accept


class WindowsWindowSource:
    """
    Top-level windows through user32, with ctypes and nothing installed.

    Every entry point is given explicit `argtypes` and `restype`, and the
    reason is weaker than it first looks - which is worth writing down,
    because the first version of this docstring claimed something false.

    It claimed an undeclared `GetForegroundWindow` returns a truncated
    handle, since ctypes defaults `restype` to `c_int` and an HWND is
    pointer-sized. Measured on this machine, it does not: 203 windows
    enumerated, largest handle 0x1202A0, none anywhere near 2**31, and the
    undeclared call returned the identical value. Windows keeps USER
    handles inside 32 bits deliberately, so the truncation being guarded
    against does not currently happen.

    The declarations stay, for two honest reasons rather than one invented
    one. They document what the API actually takes and returns, at the only
    place in this file where that is visible. And the same shortcut *is*
    a live bug one module over: `_uptime_hours` in `system.py` calls
    `GetTickCount64`, whose result genuinely exceeds `c_int` - undeclared,
    it reports negative uptime after 596.5 hours, and this machine was
    measured at 300.7. So the habit is worth keeping even where this
    particular function does not need it.

    Only visible windows with a title are reported. An invisible window,
    or one with an empty title, is a tool window or a message-only window
    - real, and not something the owner can be asked about by name.
    """

    def __init__(self):

        self._user32 = None

    # ------------------------------------------------------------------

    def _bind(self):
        """
        user32 with the signatures declared, or None off Windows.

        Bound once and cached: `windll` itself caches the library, but the
        argtypes assignments are per-function-object mutations and doing
        them on every enumeration is pointless work inside a loop that
        runs once per prompt.
        """

        if self._user32 is not None:
            return self._user32

        if os.name != "nt":
            return None

        try:
            import ctypes           # noqa: PLC0415
            from ctypes import wintypes

            user32 = ctypes.windll.user32

            user32.EnumWindows.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            user32.EnumWindows.restype = ctypes.c_bool

            user32.IsWindowVisible.argtypes = [wintypes.HWND]
            user32.IsWindowVisible.restype = ctypes.c_bool

            user32.IsIconic.argtypes = [wintypes.HWND]
            user32.IsIconic.restype = ctypes.c_bool

            user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
            user32.GetWindowTextLengthW.restype = ctypes.c_int

            user32.GetWindowTextW.argtypes = [
                wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int
            ]
            user32.GetWindowTextW.restype = ctypes.c_int

            user32.GetWindowThreadProcessId.argtypes = [
                wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
            ]
            user32.GetWindowThreadProcessId.restype = wintypes.DWORD

            user32.GetForegroundWindow.argtypes = []
            user32.GetForegroundWindow.restype = wintypes.HWND

            user32.SetForegroundWindow.argtypes = [wintypes.HWND]
            user32.SetForegroundWindow.restype = ctypes.c_bool

            user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.ShowWindow.restype = ctypes.c_bool

            self._user32 = user32

            return user32

        except Exception as error:
            logger.debug("user32 unavailable: %s", error)
            return None

    def is_available(self) -> bool:

        return self._bind() is not None

    # ------------------------------------------------------------------

    def windows(self) -> list[WindowInfo]:

        user32 = self._bind()

        if user32 is None:
            return []

        try:
            import ctypes           # noqa: PLC0415
            from ctypes import wintypes

        except Exception:
            return []

        front = user32.GetForegroundWindow()

        found: list[WindowInfo] = []

        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
        )

        def visit(handle, _unused) -> bool:

            try:
                if not user32.IsWindowVisible(handle):
                    return True

                length = user32.GetWindowTextLengthW(handle)

                if length <= 0:
                    return True

                buffer = ctypes.create_unicode_buffer(length + 1)

                user32.GetWindowTextW(handle, buffer, length + 1)

                title = buffer.value or ""

                if not title.strip():
                    return True

                pid = wintypes.DWORD()

                user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))

                found.append(
                    WindowInfo(
                        handle=int(handle or 0),
                        title=title,
                        pid=int(pid.value),
                        minimised=bool(user32.IsIconic(handle)),
                        foreground=bool(front) and int(handle or 0) == int(front),
                    )
                )

            except Exception as error:
                # One unreadable window must not abandon the enumeration:
                # a window can close between being handed to the callback
                # and being asked its title, and that is an ordinary race
                # rather than a failure of the listing.
                logger.debug("Skipped a window: %s", error)

            return True

        try:
            user32.EnumWindows(callback_type(visit), 0)

        except Exception as error:
            logger.debug("EnumWindows failed: %s", error)

        return found

    def focus(self, handle: int) -> bool:
        """
        Restore the window if it is minimised, then ask for the foreground.

        The return value is what user32 said, and it is deliberately not
        treated as proof: `SetForegroundWindow` returning true means the
        request was accepted, not that the switch happened. The tool's
        `verify` is what decides.
        """

        user32 = self._bind()

        if user32 is None:
            return False

        try:
            if user32.IsIconic(handle):
                user32.ShowWindow(handle, SW_RESTORE)

            return bool(user32.SetForegroundWindow(handle))

        except Exception as error:
            logger.debug("Focus request failed: %s", error)
            return False


def default_window_source() -> WindowSource | None:
    """
    A real window source for this platform, or None when there is none.

    None rather than an empty mock, for the reason `default_process_source`
    gives: the factory's rule is that a tool whose dependency is absent is
    not registered, so it is missing rather than present and broken, and a
    mock that answers "no windows are open" on a machine full of windows is
    worse than no tool at all.
    """

    source = WindowsWindowSource()

    if source.is_available():
        return source

    return None


# ----------------------------------------------------------------------
# Tools
# ----------------------------------------------------------------------

class ListWindowsTool(Tool):

    name = "list_windows"
    description = "List the windows currently open on this computer"
    risk = ToolRisk.SENSITIVE

    parameters = (
        Parameter(
            name="title",
            description="Only windows whose title contains this",
            required=False,
        ),
    )

    def __init__(self, source: WindowSource | None = None):

        self.source = (
            source or default_window_source() or MockWindowSource()
        )

    def execute(self, title: str = "") -> ToolResult:
        """
        Name the open windows, the foreground one first.

        Foreground first because "what is the user looking at" is the
        question this answers most of the time, and burying it in
        alphabetical order makes the model hunt for it.
        """

        try:
            windows = self.source.windows()

        except Exception as error:
            return fail(
                f"could not list windows: {type(error).__name__}: {error}",
                tool=self.name,
            )

        wanted = str(title or "").strip().lower()

        if wanted:
            windows = [
                window
                for window in windows
                if wanted in window.title.lower()
            ]

        if not windows:

            if wanted:
                return ok(f"no open window matches '{title}'", tool=self.name)

            # Deliberately a success, where the same case in
            # `list_processes` is a failure. The asymmetry is not an
            # oversight: every operating system has processes, so an empty
            # process listing can only mean the reading broke, whereas a
            # session genuinely can have no visible titled window - a
            # freshly booted desktop with everything closed, or a
            # disconnected remote session - and calling that a failure
            # would report a broken tool on a working machine.
            return ok("no windows are open", tool=self.name)

        windows.sort(
            key=lambda window: (not window.foreground, window.title.lower())
        )

        shown = windows[:MAX_WINDOWS]

        lines = [window.render() for window in shown]

        if len(windows) > len(shown):
            lines.append(f"...and {len(windows) - len(shown)} more not listed")

        return ok("\n".join(lines), tool=self.name)


class FocusWindowTool(Tool):

    name = "focus_window"
    description = "Bring an open window to the front by part of its title"
    risk = ToolRisk.DANGEROUS

    parameters = (
        Parameter(
            name="title",
            description="Part of the title of the window to bring forward",
        ),
        Parameter(
            name="pid",
            description=(
                "The pid from list_windows, when two windows share a title"
            ),
            required=False,
        ),
    )

    def __init__(self, source: WindowSource | None = None):

        self.source = (
            source or default_window_source() or MockWindowSource()
        )

    # ------------------------------------------------------------------

    def _match(
        self, title: str, pid: int = 0
    ) -> tuple[WindowInfo | None, str]:
        """
        The one window these arguments name, or nothing and the reason why.

        An ambiguous title is refused rather than resolved. Picking the
        first of two matches would look like it worked and would put the
        wrong window in front roughly half the time, which is worse than
        a refusal that says how to be specific.

        Saying how is the part that needed a real desktop to get right. A
        first version answered "name one of them more precisely", which
        this machine immediately showed to be useless advice: it had two
        windows both titled exactly `Settings`, and no title is more
        precise than an exact one. The pid distinguishes them, is already
        in every line `list_windows` prints, and is therefore something a
        caller can actually supply - so the refusal names the pids and the
        tool accepts one.
        """

        wanted = str(title or "").strip().lower()

        if not wanted:
            return None, "a window title is required"

        windows = self.source.windows()

        matches = [
            window for window in windows if wanted in window.title.lower()
        ]

        chosen = _as_pid(pid)

        if chosen:

            narrowed = [
                window for window in matches if window.pid == chosen
            ]

            if not narrowed:
                return None, (
                    f"no open window matches '{title}' with pid {chosen}"
                )

            matches = narrowed

        if not matches:
            return None, f"no open window matches '{title}'"

        if len(matches) > 1:

            names = ", ".join(
                f"{window.title!r} (pid {window.pid})"
                for window in matches[:5]
            )

            return None, (
                f"'{title}' matches {len(matches)} windows: {names} - "
                f"say which by passing its pid"
            )

        return matches[0], ""

    # ------------------------------------------------------------------

    def execute(self, title: str, pid=0) -> ToolResult:
        """
        Ask for the window to come forward. Does not claim that it did.

        The wording matters: this returns "asked for" rather than
        "brought to the front", because at this point that is all that is
        known. `verify` is what turns it into a claim, and the executor
        downgrades the result if the claim does not hold.
        """

        try:
            window, reason = self._match(title, pid)

        except Exception as error:
            return fail(
                f"could not read the open windows: "
                f"{type(error).__name__}: {error}",
                tool=self.name,
            )

        if window is None:
            return fail(reason, tool=self.name)

        accepted = self.source.focus(window.handle)

        if not accepted:
            # user32 said no outright. That is a real refusal and there
            # is nothing for the postcondition to wait for.
            return fail(
                f"Windows refused to bring '{window.title}' forward. "
                f"Nothing changed.",
                tool=self.name,
            )

        return ok(
            f"asked for '{window.title}' to come to the front",
            tool=self.name,
        )

    def verify(self, title: str, pid=0) -> ToolResult | None:
        """
        Is the window actually in front now?

        Polled rather than read once, because a foreground switch is not
        synchronous and reading immediately would fail a call that was
        about to succeed. Bounded, so a switch that never happens fails
        within FOCUS_TIMEOUT rather than hanging.

        Returns None only when the desktop cannot be read at all - the
        executor treats None as "no postcondition offered", and an
        unreadable desktop genuinely offers none. It is not the same as
        the window not being in front, and reporting a failure here would
        blame the focus for a broken enumeration.

        When a pid was supplied the pid is what gets checked, not just the
        title. That is the whole reason the argument exists: the case it
        was added for is two windows with identical titles, and a title
        comparison would happily confirm that the wrong one of the two
        came forward.
        """

        deadline = time.monotonic() + FOCUS_TIMEOUT

        wanted = str(title or "").strip().lower()

        chosen = _as_pid(pid)

        asked = f"'{title}' (pid {chosen})" if chosen else f"'{title}'"

        front = ""

        while True:

            try:
                windows = self.source.windows()

            except Exception as error:
                logger.debug("Focus verification could not read windows: %s", error)
                return None

            if not windows:
                return None

            current = [window for window in windows if window.foreground]

            if current:

                window = current[0]

                front = (
                    f"'{window.title}' (pid {window.pid})" if chosen
                    else f"'{window.title}'"
                )

                matched = wanted in window.title.lower()

                if chosen:
                    matched = matched and window.pid == chosen

                if matched:
                    return ok(f"{front} is in front", tool=self.name)

            if time.monotonic() >= deadline:
                break

            time.sleep(FOCUS_POLL)

        if front:
            # The pid is in both halves or neither, because the case this
            # message exists for is two windows with the same title, and
            # "X did not come forward, X is still in front" describes
            # nothing at all.
            return fail(
                f"{asked} did not come to the front - {front} is still "
                f"the active window",
                tool=self.name,
            )

        return fail(
            f"{asked} did not come to the front, and no window reports "
            f"being active",
            tool=self.name,
        )


def _as_pid(pid) -> int:
    """
    A pid the caller asked for, or 0 meaning "they did not ask".

    Coerced rather than validated, for the same reason `_ceiling` in
    `system.py` is: the argument is optional, it arrives from a model that
    may well write it as a string, and an unreadable one is a request for
    the default rather than an error worth failing a call over. A bool is
    excluded explicitly because `int(True)` is 1, and pid 1 is a real
    process on every OS that has one.

    Using 0 for "they did not ask" needs a word, because pid 0 is also
    real: Windows reports "System Idle Process" there, which the process
    parser deliberately keeps. It is safe here and not there because pid 0
    owns no windows, so no caller can be asking for it - a title plus pid 0
    is a caller who supplied a title and no pid.
    """

    if isinstance(pid, bool) or pid is None:
        return 0

    try:
        asked = int(pid)
    except (TypeError, ValueError):
        return 0

    return asked if asked > 0 else 0


__all__ = [
    "FocusWindowTool",
    "ListWindowsTool",
    "MockWindowSource",
    "WindowInfo",
    "WindowSource",
    "WindowsWindowSource",
    "default_window_source",
]
