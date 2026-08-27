"""
Synthesised keyboard and mouse input, aimed at a window the owner named.

Section 24's last two items - "keyboard input" and "mouse interaction" -
and the whole file is shaped by one measurement. `SetCursorPos` was asked
for (2420, 1580) on a 1920x1080 desktop. It returned **true** and put the
pointer at (1919, 1079): five hundred pixels from where the caller aimed,
reported as success. Section 11 says verification "must not rely only on:
the command executed without throwing", and here is the reason in one
line - the API's own success value is a lie by omission, so every tool in
this file re-reads the machine afterwards instead of believing it.

Three facts were measured on this host before any of it was written.

**`SendInput` really does reach the input queue.** A synthesised
VK_SHIFT down made `GetAsyncKeyState(VK_SHIFT)` report held, and the
matching up cleared it. That is the difference between "the call
returned" and "the system saw a key".

**A wrong `cbSize` inserts nothing and says nothing.** `SendInput` with
`cbSize=8` returned 0 with `GetLastError()` still 0. So the accepted-event
count is the only signal that exists for a whole class of mistake, and
every send in this file compares it against what was submitted.

**A stuck modifier is a real, durable, readable postcondition.** Because
`GetAsyncKeyState` can see a held key, a chord whose release events went
missing is detectable *after the fact* - and it matters more than it
sounds. A CTRL left down turns the owner's next keystroke into a
shortcut, and every one after that, until they notice. That is the one
honest postcondition the keyboard has, and `press_keys` asserts it.

What none of this can assert is that the keystrokes *arrived where they
were aimed*. Microsoft documents that `SendInput` blocked by UIPI reports
failure through neither its return value nor `GetLastError`, so a full
accepted count does not mean an elevated window received anything. The
docstrings below say so rather than implying otherwise, and the `window`
argument exists to narrow the damage: it refuses to type at all unless
the window the caller expected is the one in front.
"""

from dataclasses import dataclass
from typing import Protocol, Sequence

from core.logger import logger
from tools.base import Parameter, Tool, ToolResult, ToolRisk, fail, ok
from tools.builtins.desktop import WindowSource

import os


# How far the pointer may sit from where it was aimed and still count as
# arrived. Measured exact on this machine - every in-bounds SetCursorPos
# landed on the requested pixel - so this is slack for hosts that scale
# or snap, not for the case it must catch: a clamp at the desktop edge is
# off by hundreds of pixels and has to fail.
POINTER_TOLERANCE = 2

# How much text one call may type, and how many chords one call may
# press. Both are lengths a *model* chose, which is the same reason
# `MAX_WRITE_BYTES` exists in filesystem.py and the same reason
# `take_screenshot` deliberately has no cap: a screenshot's size is the
# owner's display, but a paste is whatever the model felt like emitting,
# and every character here is two events queued ahead of the owner's own
# typing.
MAX_TEXT = 4096
MAX_CHORDS = 16

# One SendInput call per this many events. There is no documented ceiling
# on the array length; chunking keeps a long paste from depending on
# there not being one.
CHUNK = 512

MOUSE_BUTTONS = ("left", "right", "middle")

# Virtual-key codes, by the names a model is likely to produce. Aliases
# are deliberate - a model that writes "escape", "esc", "return" or
# "enter" means the same key each time, and refusing three of the four
# would be a puzzle rather than a safeguard.
_NAMED_KEYS = {
    "ctrl": 0x11, "control": 0x11,
    "shift": 0x10,
    "alt": 0x12, "menu": 0x12,
    "win": 0x5B, "super": 0x5B, "cmd": 0x5B, "meta": 0x5B,
    "enter": 0x0D, "return": 0x0D,
    "tab": 0x09,
    "esc": 0x1B, "escape": 0x1B,
    "backspace": 0x08, "back": 0x08,
    "space": 0x20, "spacebar": 0x20,
    "delete": 0x2E, "del": 0x2E,
    "insert": 0x2D, "ins": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21, "pgup": 0x21,
    "pagedown": 0x22, "pgdn": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "capslock": 0x14,
    "printscreen": 0x2C, "prtsc": 0x2C,
}

for _index in range(1, 25):
    _NAMED_KEYS[f"f{_index}"] = 0x6F + _index

for _code in range(ord("a"), ord("z") + 1):
    _NAMED_KEYS[chr(_code)] = _code - 32

for _digit in range(10):
    _NAMED_KEYS[str(_digit)] = 0x30 + _digit

# Keys that need KEYEVENTF_EXTENDEDKEY. The navigation cluster and the
# arrows share virtual-key codes with the numeric keypad, and without the
# flag some applications read an arrow press as a keypad digit.
_EXTENDED = {0x2E, 0x2D, 0x24, 0x23, 0x21, 0x22, 0x26, 0x28, 0x25, 0x27, 0x2C}

# The modifiers, for the stuck-key postcondition. Only these can strand
# the owner's keyboard: a letter left down repeats and stops, a held CTRL
# silently reinterprets everything typed afterwards.
_MODIFIERS = (0x11, 0x10, 0x12, 0x5B)

# Characters that are a key, not a character. Injecting U+000A as text
# does not start a new line in most applications - VK_RETURN does - so
# the text is split into runs and these are pressed instead.
_AS_KEYSTROKE = {"\n": 0x0D, "\r": 0x0D, "\t": 0x09}


@dataclass(frozen=True)
class Key:
    """
    One key, named by the caller and resolved to what Windows wants.

    `name` is kept for the messages: a failure that says "ctrl is still
    held down" is actionable, and one that says "0x11 is still held down"
    is a puzzle the owner has to solve with a search engine.
    """

    name: str
    code: int
    extended: bool = False

    @property
    def modifier(self) -> bool:
        return self.code in _MODIFIERS


class InputSynthesizer(Protocol):
    """
    What the tools need from a machine that can be typed at.

    Narrow on purpose. `bounds` and `cursor` are reads, the rest are
    writes, and every write returns the number of events the system
    accepted so the caller can compare it against what was submitted.
    """

    def is_available(self) -> bool:
        ...

    def bounds(self) -> tuple[int, int, int, int] | None:
        """`(left, top, width, height)` of the whole desktop."""

    def cursor(self) -> tuple[int, int] | None:
        ...

    def move(self, x: int, y: int) -> bool:
        ...

    def click(self, button: str, double: bool) -> tuple[int, int]:
        """`(accepted, submitted)`."""

    def write(self, text: str) -> tuple[int, int]:
        """`(accepted, submitted)`."""

    def press(self, chord: Sequence[Key]) -> tuple[int, int]:
        """`(accepted, submitted)`."""

    def held(self, key: Key) -> bool:
        ...


class MockInputSynthesizer:
    """
    A synthesizer that records instead of touching the machine.

    Tests drive this. Nothing in production builds one, for the reason
    `default_window_source` gives: a mock that cheerfully reports typing
    into a desktop it cannot see is worse than a missing tool.
    """

    def __init__(
        self,
        bounds: tuple[int, int, int, int] | None = (0, 0, 1920, 1080),
        cursor: tuple[int, int] | None = (10, 10),
        available: bool = True,
        accepts: bool = True,
        moves: bool = True,
        stuck: Sequence[str] = (),
    ):
        self._bounds = bounds
        self._cursor = cursor
        self._available = available
        self._accepts = accepts
        self._moves = moves
        self._stuck = tuple(stuck)

        self.typed: list[str] = []
        self.chords: list[tuple[str, ...]] = []
        self.clicks: list[tuple[str, bool]] = []
        self.moves: list[tuple[int, int]] = []

    def is_available(self) -> bool:
        return self._available

    def bounds(self):
        return self._bounds

    def cursor(self):
        return self._cursor

    def move(self, x: int, y: int) -> bool:
        self.moves.append((x, y))
        if self._moves:
            self._cursor = (x, y)
        return self._moves

    def _count(self, submitted: int) -> tuple[int, int]:
        return (submitted if self._accepts else max(0, submitted - 1), submitted)

    def click(self, button: str, double: bool):
        self.clicks.append((button, double))
        return self._count(4 if double else 2)

    def write(self, text: str):
        self.typed.append(text)
        # Two events per UTF-16 code unit, down and up. Parenthesised
        # because `2 * n // 2` is `n`, which would have made every
        # accepted-count test agree with itself for the wrong reason.
        return self._count(2 * (len(text.encode("utf-16-le")) // 2))

    def press(self, chord: Sequence[Key]):
        self.chords.append(tuple(key.name for key in chord))
        return self._count(2 * len(chord))

    def held(self, key: Key) -> bool:
        return key.name in self._stuck


class WindowsInputSynthesizer:
    """
    `SendInput` and `SetCursorPos` through ctypes, with nothing installed.

    The same shape as `WindowsWindowSource` next door: signatures declared
    once and cached, every call wrapped, and `is_available` answering
    whether the binding worked rather than whether the import did.

    `INPUT` is declared with an anonymous union, and `dwExtraInfo` is
    `wintypes.WPARAM` because that is the pointer-sized integer ctypes
    ships - `ULONG_PTR` has no `wintypes` name. Measured here:
    `sizeof(INPUT)` is 40 on this 64-bit host, and the size is passed to
    every call because getting it wrong inserts nothing while reporting
    no error at all.

    Buttons are sent at the pointer's current position rather than with
    `MOUSEEVENTF_ABSOLUTE`, and the pointer is placed by `SetCursorPos`.
    That is a deliberate avoidance: absolute mouse coordinates in
    `SendInput` are normalised to 0..65535 across the primary display,
    not pixels, and a tool that quietly halves the owner's coordinates on
    a multi-monitor desktop is worse than one that does not exist.
    `SetCursorPos` takes pixels.
    """

    def __init__(self):
        self._api = None

    # ------------------------------------------------------------------

    def _bind(self):

        if self._api is not None:
            return self._api

        if os.name != "nt":
            return None

        try:
            import ctypes           # noqa: PLC0415
            from ctypes import wintypes

            pointer_sized = wintypes.WPARAM

            class MOUSEINPUT(ctypes.Structure):
                _fields_ = [
                    ("dx", wintypes.LONG),
                    ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", pointer_sized),
                ]

            class KEYBDINPUT(ctypes.Structure):
                _fields_ = [
                    ("wVk", wintypes.WORD),
                    ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", pointer_sized),
                ]

            class HARDWAREINPUT(ctypes.Structure):
                _fields_ = [
                    ("uMsg", wintypes.DWORD),
                    ("wParamL", wintypes.WORD),
                    ("wParamH", wintypes.WORD),
                ]

            class _EITHER(ctypes.Union):
                _fields_ = [
                    ("mi", MOUSEINPUT),
                    ("ki", KEYBDINPUT),
                    ("hi", HARDWAREINPUT),
                ]

            class INPUT(ctypes.Structure):
                _anonymous_ = ("payload",)
                _fields_ = [("type", wintypes.DWORD), ("payload", _EITHER)]

            user32 = ctypes.windll.user32

            user32.SendInput.argtypes = [
                wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int
            ]
            user32.SendInput.restype = wintypes.UINT

            user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
            user32.SetCursorPos.restype = ctypes.c_bool

            user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
            user32.GetCursorPos.restype = ctypes.c_bool

            # Signed, and it matters: the high bit is the "held" flag, and
            # an unsigned read of 0x8000 is positive where a c_short is
            # negative. Declaring it keeps the bit test honest.
            user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
            user32.GetAsyncKeyState.restype = ctypes.c_short

            user32.GetSystemMetrics.argtypes = [ctypes.c_int]
            user32.GetSystemMetrics.restype = ctypes.c_int

            user32.WindowFromPoint.argtypes = [wintypes.POINT]
            user32.WindowFromPoint.restype = wintypes.HWND

            user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
            user32.GetAncestor.restype = wintypes.HWND

            user32.GetWindowTextW.argtypes = [
                wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int
            ]
            user32.GetWindowTextW.restype = ctypes.c_int

            self._api = (ctypes, wintypes, user32, INPUT, KEYBDINPUT, MOUSEINPUT)

        except Exception as error:
            logger.debug("Input synthesis unavailable: %s", error)
            return None

        return self._api

    def is_available(self) -> bool:
        return self._bind() is not None

    # ------------------------------------------------------------------

    def bounds(self):

        api = self._bind()

        if api is None:
            return None

        _, _, user32, *_ = api

        try:
            # SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, then the extents. The
            # virtual desktop, not the primary display: a second monitor
            # to the left of the first starts at a negative x, and
            # refusing those coordinates would refuse half the desktop.
            return (
                user32.GetSystemMetrics(76),
                user32.GetSystemMetrics(77),
                user32.GetSystemMetrics(78),
                user32.GetSystemMetrics(79),
            )

        except Exception as error:
            logger.debug("Could not read the desktop bounds: %s", error)
            return None

    def cursor(self):

        api = self._bind()

        if api is None:
            return None

        ctypes, wintypes, user32, *_ = api

        try:
            point = wintypes.POINT()

            if not user32.GetCursorPos(ctypes.byref(point)):
                return None

            return (int(point.x), int(point.y))

        except Exception as error:
            logger.debug("Could not read the pointer: %s", error)
            return None

    def window_at(self, x: int, y: int) -> str:
        """
        The title of the top-level window under a point, or "".

        `WindowFromPoint` answers with the deepest child - a button, a
        text area - whose title is usually empty, so `GetAncestor` walks
        up to the window the owner would recognise by name.
        """

        api = self._bind()

        if api is None:
            return ""

        ctypes, wintypes, user32, *_ = api

        try:
            deepest = user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))

            if not deepest:
                return ""

            root = user32.GetAncestor(deepest, 2) or deepest

            buffer = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(root, buffer, 512)

            return buffer.value or ""

        except Exception as error:
            logger.debug("Could not read the window under the pointer: %s", error)
            return ""

    def move(self, x: int, y: int) -> bool:

        api = self._bind()

        if api is None:
            return False

        _, _, user32, *_ = api

        try:
            return bool(user32.SetCursorPos(int(x), int(y)))

        except Exception as error:
            logger.debug("Could not move the pointer: %s", error)
            return False

    # ------------------------------------------------------------------

    def _send(self, events) -> tuple[int, int]:
        """
        Submit events in chunks and total what the system accepted.

        A short total is the only evidence available for a whole family of
        failures, so it is returned rather than logged and dropped.
        """

        api = self._bind()

        if api is None:
            return (0, len(events))

        ctypes, _, user32, INPUT, *_ = api

        accepted = 0

        for start in range(0, len(events), CHUNK):

            batch = events[start:start + CHUNK]
            array = (INPUT * len(batch))(*batch)

            try:
                accepted += int(
                    user32.SendInput(len(batch), array, ctypes.sizeof(INPUT))
                )

            except Exception as error:
                logger.debug("SendInput failed: %s", error)
                break

        return (accepted, len(events))

    def _key_event(self, code: int, scan: int, flags: int):

        _, _, _, INPUT, KEYBDINPUT, _ = self._bind()

        return INPUT(
            type=1,
            ki=KEYBDINPUT(
                wVk=code, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0
            ),
        )

    def _mouse_event(self, flags: int):

        _, _, _, INPUT, _, MOUSEINPUT = self._bind()

        return INPUT(
            type=0,
            mi=MOUSEINPUT(
                dx=0, dy=0, mouseData=0, dwFlags=flags, time=0, dwExtraInfo=0
            ),
        )

    def click(self, button: str, double: bool) -> tuple[int, int]:

        if self._bind() is None:
            return (0, 0)

        down, up = {
            "left": (0x0002, 0x0004),
            "right": (0x0008, 0x0010),
            "middle": (0x0020, 0x0040),
        }[button]

        pair = [self._mouse_event(down), self._mouse_event(up)]

        return self._send(pair * 2 if double else pair)

    def write(self, text: str) -> tuple[int, int]:
        """
        Type literal text, one UTF-16 code unit at a time.

        Code units rather than characters, because `KEYEVENTF_UNICODE`
        carries a 16-bit `wScan`: an emoji is a surrogate pair, and the
        two halves have to arrive as two adjacent events for Windows to
        recombine them.
        """

        if self._bind() is None:
            return (0, 0)

        KEYEVENTF_UNICODE = 0x0004
        KEYEVENTF_KEYUP = 0x0002

        events = []

        for pair in range(0, len(text.encode("utf-16-le")) // 2):

            unit = int.from_bytes(
                text.encode("utf-16-le")[pair * 2:pair * 2 + 2], "little"
            )

            events.append(self._key_event(0, unit, KEYEVENTF_UNICODE))
            events.append(
                self._key_event(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)
            )

        return self._send(events)

    def press(self, chord: Sequence[Key]) -> tuple[int, int]:
        """
        Hold every key in order, then release in reverse.

        Reverse on the way up because that is what a chord is: CTRL goes
        down first and comes up last, and releasing it before the letter
        turns `ctrl+s` into a stray `s` typed into the document.
        """

        if self._bind() is None:
            return (0, 0)

        KEYEVENTF_EXTENDEDKEY = 0x0001
        KEYEVENTF_KEYUP = 0x0002

        events = []

        for key in chord:
            flags = KEYEVENTF_EXTENDEDKEY if key.extended else 0
            events.append(self._key_event(key.code, 0, flags))

        for key in reversed(list(chord)):
            flags = KEYEVENTF_KEYUP
            if key.extended:
                flags |= KEYEVENTF_EXTENDEDKEY
            events.append(self._key_event(key.code, 0, flags))

        return self._send(events)

    def held(self, key: Key) -> bool:

        api = self._bind()

        if api is None:
            return False

        _, _, user32, *_ = api

        try:
            return bool(user32.GetAsyncKeyState(key.code) & 0x8000)

        except Exception as error:
            logger.debug("Could not read the key state: %s", error)
            return False


def default_input_synthesizer() -> InputSynthesizer | None:
    """
    A real synthesizer for this platform, or None when there is none.

    None rather than a mock, the same rule the rest of the tool layer
    follows: a tool whose dependency is absent is not registered, so it
    is missing rather than present and lying about having typed.
    """

    synthesizer = WindowsInputSynthesizer()

    if synthesizer.is_available():
        return synthesizer

    return None


# ----------------------------------------------------------------------
# Parsing, which is pure and therefore testable without a desktop
# ----------------------------------------------------------------------

def parse_chords(keys: str) -> list[tuple[Key, ...]]:
    """
    `"ctrl+a delete"` into two chords, or a ValueError naming the problem.

    Space separates chords, `+` separates the keys held together in one.
    An unknown name is refused rather than dropped: a chord silently
    missing its modifier is `s` typed into a document instead of a save,
    and the owner finds out later.
    """

    text = str(keys or "").strip()

    if not text:
        raise ValueError("no keys were given")

    chords: list[tuple[Key, ...]] = []

    for word in text.split():

        chord: list[Key] = []

        for name in word.split("+"):

            wanted = name.strip().lower()

            if not wanted:
                raise ValueError(
                    f"'{word}' has an empty key in it - write it as "
                    f"ctrl+s, with one key on each side of the plus"
                )

            if wanted not in _NAMED_KEYS:
                raise ValueError(
                    f"'{name}' is not a key I know. Use a letter, a digit, "
                    f"f1-f24, or a name like enter, tab, esc, delete, up, "
                    f"ctrl, shift, alt, win"
                )

            code = _NAMED_KEYS[wanted]

            chord.append(Key(wanted, code, code in _EXTENDED))

        chords.append(tuple(chord))

    if len(chords) > MAX_CHORDS:
        raise ValueError(
            f"{len(chords)} key presses in one call is more than the "
            f"{MAX_CHORDS} allowed - send them in smaller steps"
        )

    return chords


def split_typing(text: str) -> list[str | Key]:
    """
    Text into runs of literal characters and the keys that are not text.

    A newline injected as the character U+000A does not start a new line
    in most applications; VK_RETURN does. Same for a tab. So the text is
    cut at those points and they are pressed as keys, which is the
    difference between typing a two-line note and typing one line with an
    invisible character in the middle of it.
    """

    pieces: list[str | Key] = []

    run = ""

    previous = ""

    for character in text:

        if character in _AS_KEYSTROKE:

            if run:
                pieces.append(run)
                run = ""

            # A CRLF is one line break, not two. Only the newline that
            # directly follows a carriage return is dropped - looking at
            # the last piece instead would swallow the second newline of
            # a blank line, turning two paragraphs into one.
            if character == "\n" and previous == "\r":
                previous = character
                continue

            if character == "\t":
                pieces.append(Key("tab", _AS_KEYSTROKE[character]))
            else:
                pieces.append(Key("enter", 0x0D))

            previous = character

            continue

        run += character

        previous = character

    if run:
        pieces.append(run)

    return pieces


def _coordinate(value, axis: str) -> int:

    if isinstance(value, bool):
        raise ValueError(f"{axis} must be a number, not true or false")

    try:
        return int(str(value).strip())

    except (TypeError, ValueError):
        raise ValueError(f"{axis} must be a number, got {value!r}") from None


# ----------------------------------------------------------------------
# Tools
# ----------------------------------------------------------------------

class _InputTool(Tool):
    """
    What the four of them share: a synthesizer, and an optional guard.

    Every one is DANGEROUS, and that is the top of the existing ladder
    rather than a new rung. A fourth risk level would have to be learned
    by the settings contract, the Android DTO, config.yaml and the Hub
    before it protected anything, and it would protect nothing the owner
    cannot already get by leaving `dangerous` out of `tools.auto_approve`.
    The `window` guard below is the part the ladder genuinely cannot
    express, so that is where the effort went.
    """

    # Mouse and keyboard synthesis share one explicitly registered
    # capability. This prevents direct tool construction from falling back
    # to a tool-name capability and skipping capability resolution.
    capability = "desktop.input"
    risk = ToolRisk.DANGEROUS

    def __init__(
        self,
        synthesizer: InputSynthesizer | None = None,
        windows: WindowSource | None = None,
    ):
        if synthesizer is None:
            synthesizer = default_input_synthesizer()

        self.synthesizer = synthesizer
        self.windows = windows

    # ------------------------------------------------------------------

    def _synthesizer(self) -> InputSynthesizer:

        if self.synthesizer is None or not self.synthesizer.is_available():
            raise RuntimeError(
                "this computer cannot be typed at from here"
            )

        return self.synthesizer

    def _foreground(self) -> str | None:
        """
        The title of the window in front, or None if it cannot be read.
        """

        if self.windows is None:
            return None

        try:
            front = [
                window for window in self.windows.windows() if window.foreground
            ]

        except Exception as error:
            logger.debug("Could not read the foreground window: %s", error)
            return None

        return front[0].title if front else ""

    def _guard(self, window) -> str:
        """
        Refuse unless the window the caller expected is the one in front.

        The case this exists for is a plan two steps long: focus the
        editor, then type. Between the two steps the owner can alt-tab to
        their bank, and the second step will type into whatever is there
        now - the tool has no idea it is no longer talking to the editor.
        Naming the window turns that from a silent misdelivery into a
        refusal.

        A guard that cannot be checked is refused rather than skipped.
        Ignoring a safeguard because the desktop is unreadable would give
        the caller the words without the protection, which is worse than
        saying no.
        """

        wanted = str(window or "").strip()

        if not wanted:
            return ""

        if self.windows is None:
            raise RuntimeError(
                "I cannot check which window is in front on this computer, "
                "so I will not type into it blind"
            )

        front = self._foreground()

        if front is None:
            raise RuntimeError(
                "the desktop could not be read, so I cannot confirm "
                f"'{wanted}' is the window in front"
            )

        if wanted.lower() not in front.lower():
            raise RuntimeError(
                f"'{wanted}' is not the window in front - "
                + (f"'{front}' is" if front else "nothing reports being active")
                + ". Bring it forward with focus_window first."
            )

        return front

    def _sent(self, accepted: int, submitted: int, what: str) -> None:
        """
        A short accepted count is a failure, and the only sign of one.

        Measured: `SendInput` with the wrong structure size inserted
        nothing, returned 0, and left `GetLastError` at zero. Comparing
        the count is the whole detection.
        """

        if submitted and accepted < submitted:
            raise RuntimeError(
                f"the system accepted {accepted} of {submitted} {what} - "
                f"something is blocking synthesised input (a lock screen, "
                f"or a window running as administrator)"
            )

    def _unstuck(self, chord: Sequence[Key]) -> ToolResult | None:
        """
        No modifier this call pressed is still held.

        The real postcondition, and the only durable one the keyboard
        offers. A CTRL left down does not announce itself - it turns
        every later keystroke into a shortcut until the owner works out
        what happened - and `GetAsyncKeyState` can see it.
        """

        stuck = [
            key.name for key in chord
            if key.modifier and self._synthesizer().held(key)
        ]

        if stuck:
            return fail(
                f"{', '.join(sorted(set(stuck)))} is still held down after "
                f"the key press, which would change every key typed next",
                tool=self.name,
            )

        return None


class MoveMouseTool(_InputTool):
    """
    Put the pointer somewhere. The one tool here with a clean answer.
    """

    name = "move_mouse"

    description = "Move the mouse pointer to a position on the screen"

    parameters = (
        Parameter(name="x", description="Pixels from the left of the screen"),
        Parameter(name="y", description="Pixels from the top of the screen"),
    )

    def _target(self, x, y) -> tuple[int, int]:
        """
        A point on the desktop, refused before the pointer moves if not.

        Refused rather than clamped, because clamping is exactly what
        Windows does and exactly what makes it dangerous: asked for
        (2420, 1580) on this 1920x1080 desktop, `SetCursorPos` returned
        true and left the pointer at (1919, 1079). A click aimed off the
        screen would land on whatever sits in the bottom-right corner,
        and the caller would be told it succeeded.
        """

        wanted = (_coordinate(x, "x"), _coordinate(y, "y"))

        bounds = self._synthesizer().bounds()

        if bounds is None:
            return wanted

        left, top, width, height = bounds

        if not (left <= wanted[0] < left + width):
            raise ValueError(
                f"x={wanted[0]} is off the desktop, which is {width} pixels "
                f"wide starting at x={left}"
            )

        if not (top <= wanted[1] < top + height):
            raise ValueError(
                f"y={wanted[1]} is off the desktop, which is {height} pixels "
                f"tall starting at y={top}"
            )

        return wanted

    def execute(self, x, y) -> str:

        target = self._target(x, y)

        if not self._synthesizer().move(*target):
            raise RuntimeError(
                f"the pointer could not be moved to {target[0]}, {target[1]}"
            )

        return f"moved the pointer to {target[0]}, {target[1]}"

    def verify(self, x, y) -> ToolResult | None:
        """
        Is the pointer actually there?

        The postcondition section 11 asks for, and it is not decoration:
        `SetCursorPos` reports success for a point it silently clamped, so
        "it returned true" is precisely the sentence that must not be
        trusted. Reading the position back is a different question asked
        of a different function.

        Milliseconds after the move - the executor calls this straight
        after `execute` - so the owner's own hand is a narrow race rather
        than a likely one, and `POINTER_TOLERANCE` absorbs a host that
        snaps to something coarser than a pixel.
        """

        target = (_coordinate(x, "x"), _coordinate(y, "y"))

        where = self._synthesizer().cursor()

        if where is None:
            # Unreadable is not wrong. The executor treats None as no
            # postcondition offered, which is honest: nothing was learned.
            return None

        drift = (abs(where[0] - target[0]), abs(where[1] - target[1]))

        if max(drift) > POINTER_TOLERANCE:
            return fail(
                f"the pointer is at {where[0]}, {where[1]} and not at "
                f"{target[0]}, {target[1]} where it was aimed",
                tool=self.name,
            )

        return ok(f"the pointer is at {where[0]}, {where[1]}", tool=self.name)


class ClickMouseTool(MoveMouseTool):
    """
    Click at a point on the screen.

    The coordinates are required, and that is a safety property rather
    than an inconvenience. A click at "wherever the pointer happens to
    be" would put the target in state the owner never saw: they would
    approve `click_mouse(left)` while the interesting question - what is
    under the pointer - was decided by an earlier call. Requiring the
    point means the confirmation prompt names it.
    """

    name = "click_mouse"

    description = (
        "Click the mouse at a position on the screen. This presses a real "
        "mouse button on whatever is at that point"
    )

    parameters = (
        Parameter(name="x", description="Pixels from the left of the screen"),
        Parameter(name="y", description="Pixels from the top of the screen"),
        Parameter(
            name="button",
            description="left, right or middle. Left if not given",
            required=False,
        ),
        Parameter(
            name="double",
            description="true to double-click",
            required=False,
        ),
        Parameter(
            name="window",
            description=(
                "Only click if this window is in front, as a safety check"
            ),
            required=False,
        ),
    )

    def _button(self, button) -> str:

        wanted = str(button or "left").strip().lower()

        if wanted not in MOUSE_BUTTONS:
            raise ValueError(
                f"'{button}' is not a mouse button - use "
                f"{', '.join(MOUSE_BUTTONS)}"
            )

        return wanted

    def execute(self, x, y, button=None, double=False, window="") -> str:

        target = self._target(x, y)

        which = self._button(button)

        twice = _truth(double, "double")

        self._guard(window)

        synthesizer = self._synthesizer()

        if not synthesizer.move(*target):
            raise RuntimeError(
                f"the pointer could not be moved to {target[0]}, {target[1]}, "
                f"so nothing was clicked"
            )

        # Read what is there *before* clicking. A click can open a menu
        # over the thing that was clicked, so asking afterwards would
        # name the menu and not the target.
        under = ""

        reader = getattr(synthesizer, "window_at", None)

        if callable(reader):
            under = reader(*target) or ""

        accepted, submitted = synthesizer.click(which, twice)

        self._sent(accepted, submitted, "mouse events")

        return (
            f"{'double-' if twice else ''}clicked the {which} button at "
            f"{target[0]}, {target[1]}"
            + (f", on '{under}'" if under else "")
        )

    def verify(self, x, y, button=None, double=False, window="") -> ToolResult | None:
        """
        The pointer reached the point the click was aimed at.

        That is all this can honestly claim, and it is worth being exact
        about what is missing. Whether the click *did* anything is not
        knowable from here: only the application that received it knows
        whether the button under the pointer was enabled, whether the
        window had grabbed the input, or whether anything happened at
        all. Asserting the pointer's position catches the failure that
        does silently happen - a coordinate clamped to the desktop edge -
        and claims nothing beyond it.
        """

        return MoveMouseTool.verify(self, x, y)


class TypeTextTool(_InputTool):
    """
    Type text into whatever window is in front.
    """

    name = "type_text"

    description = (
        "Type text on the keyboard, into whichever window is currently in "
        "front. The text goes wherever the cursor is, exactly as if typed"
    )

    parameters = (
        Parameter(name="text", description="The text to type"),
        Parameter(
            name="window",
            description=(
                "Only type if this window is in front, as a safety check"
            ),
            required=False,
        ),
    )

    def execute(self, text: str, window="") -> str:

        typing = str(text if text is not None else "")

        if not typing:
            raise ValueError("there is no text to type")

        if len(typing) > MAX_TEXT:
            raise ValueError(
                f"{len(typing)} characters is more than the {MAX_TEXT} this "
                f"can type in one go - send it in smaller pieces"
            )

        front = self._guard(window)

        synthesizer = self._synthesizer()

        for piece in split_typing(typing):

            if isinstance(piece, Key):
                accepted, submitted = synthesizer.press((piece,))
                self._sent(accepted, submitted, "key events")
                continue

            accepted, submitted = synthesizer.write(piece)
            self._sent(accepted, submitted, "key events")

        # The length, never the text. Section 30 keeps credentials out of
        # normal logs, and a tool that types is the most likely one in
        # this layer to be handed a password: `take_screenshot` logs its
        # geometry and not its pixels for the same reason.
        logger.info(
            "Typed %d characters into %s",
            len(typing), f"'{front}'" if front else "the active window",
        )

        return (
            f"typed {len(typing)} characters"
            + (f" into '{front}'" if front else "")
        )

    def verify(self, text: str, window="") -> ToolResult | None:
        """
        Whether the window aimed at is still the window in front.

        Deliberately not "the text arrived". Nothing here can ask that:
        the characters went into the system's input queue and the
        receiving application decides what to do with them, and
        Microsoft documents that input blocked by UIPI is reported
        through neither `SendInput`'s return value nor `GetLastError`. A
        `verify` claiming the text landed would be inventing the one
        thing that is genuinely unknowable, which is what section 11 is
        about.

        So it re-asks the question it can: the guard held. With no window
        named there is nothing to re-ask, and None is returned rather
        than a check dressed up as one.
        """

        wanted = str(window or "").strip()

        if not wanted:
            return None

        front = self._foreground()

        if front is None:
            return None

        if wanted.lower() not in front.lower():
            return fail(
                f"'{wanted}' is no longer the window in front - "
                + (f"'{front}' is" if front else "nothing reports being active")
                + ", so some of the typing may have gone there",
                tool=self.name,
            )

        return ok(f"'{front}' is still in front", tool=self.name)


class PressKeysTool(_InputTool):
    """
    Press a key or a combination, like ctrl+s or alt+f4.
    """

    name = "press_keys"

    description = (
        "Press keys or a keyboard shortcut, like enter, ctrl+s or alt+f4, "
        "into whichever window is currently in front. Separate keys held "
        "together with + and separate presses with a space"
    )

    parameters = (
        Parameter(
            name="keys",
            description="The keys, for example 'ctrl+s' or 'ctrl+a delete'",
        ),
        Parameter(
            name="window",
            description=(
                "Only press if this window is in front, as a safety check"
            ),
            required=False,
        ),
    )

    def execute(self, keys: str, window="") -> str:

        chords = parse_chords(keys)

        front = self._guard(window)

        synthesizer = self._synthesizer()

        for chord in chords:
            accepted, submitted = synthesizer.press(chord)
            self._sent(accepted, submitted, "key events")

        spelled = " ".join(
            "+".join(key.name for key in chord) for chord in chords
        )

        return (
            f"pressed {spelled}"
            + (f" in '{front}'" if front else "")
        )

    def verify(self, keys: str, window="") -> ToolResult | None:
        """
        Nothing is left held down, and the guard still holds.

        The stuck modifier is the real one. It is the only durable trace a
        key press leaves, it is readable through `GetAsyncKeyState`, and
        the failure it catches is genuinely nasty: a CTRL that never came
        back up turns everything the owner types next into shortcuts,
        with no message anywhere saying why.

        What it does not claim is that the shortcut did what it is for.
        `ctrl+s` reaching the application is not the application having
        saved, and only the application knows the difference.
        """

        try:
            chords = parse_chords(keys)

        except ValueError:
            # Unparseable keys never reached the machine, so there is no
            # postcondition to check. `execute` already refused.
            return None

        pressed = [key for chord in chords for key in chord]

        stuck = self._unstuck(pressed)

        if stuck is not None:
            return stuck

        wanted = str(window or "").strip()

        if not wanted:
            return ok("nothing is left held down", tool=self.name)

        front = self._foreground()

        if front is None:
            return ok("nothing is left held down", tool=self.name)

        if wanted.lower() not in front.lower():
            return fail(
                f"'{wanted}' is no longer the window in front - "
                + (f"'{front}' is" if front else "nothing reports being active"),
                tool=self.name,
            )

        return ok(
            f"nothing is left held down, and '{front}' is still in front",
            tool=self.name,
        )


def _truth(value, name: str) -> bool:
    """
    A flag from a model, which may arrive as a string.

    The same shape as `filesystem._flag`, and not imported from it
    because that one raises with a filesystem-shaped message. A model
    that writes `double="true"` means true.
    """

    if value is None or value is False:
        return False

    if value is True:
        return True

    spelled = str(value).strip().lower()

    if spelled in ("true", "yes", "1", "on"):
        return True

    if spelled in ("", "false", "no", "0", "off"):
        return False

    raise ValueError(f"{name} must be true or false, got {value!r}")
