"""
Tests for synthesised keyboard and mouse input (phase 18.5, section 24).

Three properties carry this file.

**A refused call sends nothing.** Every validation in these tools runs
before a single event reaches the machine, and that ordering is the whole
safety story: a bad coordinate, an unknown key name, or a window that is
not in front must leave the desktop untouched rather than half-typed. The
tests assert on the mock's recorded events, so a reordering that validates
after acting fails here even though every message stays identical.

**The pointer's position is re-read, not assumed.** Measured on this
machine: `SetCursorPos(2420, 1580)` on a 1920x1080 desktop returned
**true** and left the pointer at (1919, 1079). Section 11's "must not rely
only on: the command executed without throwing" is not hypothetical here,
so the refusal and the postcondition are both tested against that number.

**A stuck modifier is caught.** It is the only durable trace a key press
leaves, and the failure it prevents is a CTRL left held down turning
everything the owner types next into a shortcut.
"""

import os
from types import SimpleNamespace

import pytest

from tools.base import ToolRisk
from tools.builtins.desktop import WindowInfo
from tools.builtins.input import (
    CHUNK,
    MAX_CHORDS,
    MAX_TEXT,
    POINTER_TOLERANCE,
    ClickMouseTool,
    Key,
    MockInputSynthesizer,
    MoveMouseTool,
    PressKeysTool,
    TypeTextTool,
    WindowsInputSynthesizer,
    default_input_synthesizer,
    parse_chords,
    split_typing,
)
from tools.executor import ToolExecutor, ToolPolicy
from tools.registry import ToolRegistry

WINDOWS_ONLY = pytest.mark.skipif(
    os.name != "nt", reason="input synthesis is Windows only"
)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

class FakeWindows:
    """
    A window source that answers from a list, or refuses to answer.

    `raises` is the case that matters most: an unreadable desktop must
    make the guard refuse rather than quietly skip itself.
    """

    def __init__(self, front="Editor", others=(), raises=False):
        self.front = front
        self.others = tuple(others)
        self.raises = raises
        self.asked = 0

    def windows(self):
        self.asked += 1
        if self.raises:
            raise OSError("the desktop cannot be read")
        found = [
            WindowInfo(handle=index + 2, title=title, pid=100 + index)
            for index, title in enumerate(self.others)
        ]
        if self.front is not None:
            found.insert(
                0, WindowInfo(handle=1, title=self.front, pid=99, foreground=True)
            )
        return found

    def focus(self, handle: int) -> bool:
        return True


def not_windows(monkeypatch) -> None:
    """
    Make the module think it is not on Windows.

    Patching the module's `os` rather than `os.name` itself, deliberately.
    `pathlib` reads `os.name` to pick a path flavour, so a global patch
    turns any *failing* assertion inside the patched window into an
    INTERNALERROR while pytest formats the report - which aborts the whole
    session and names no test. This costs nothing and cannot do that.
    """

    monkeypatch.setattr("tools.builtins.input.os", SimpleNamespace(name="posix"))


def executor_for(*instances, allowed=None, approve=True):

    registry = ToolRegistry()

    for instance in instances:
        registry.register(instance)

    names = allowed if allowed is not None else [t.name for t in instances]

    return ToolExecutor(
        registry=registry,
        policy=ToolPolicy(
            enabled=True,
            allowed=names,
            auto_approve=(
                ["safe", "sensitive", "dangerous"] if approve else ["safe"]
            ),
        ),
        confirm=(lambda tool, arguments: True) if approve else None,
    )


@pytest.fixture
def synth():
    return MockInputSynthesizer()


@pytest.fixture
def desktop():
    return FakeWindows()


# ----------------------------------------------------------------------
# Parsing, which needs no desktop
# ----------------------------------------------------------------------

class TestParsingKeyNames:

    def test_a_single_key(self):
        chords = parse_chords("enter")
        assert [[key.name for key in chord] for chord in chords] == [["enter"]]
        assert chords[0][0].code == 0x0D

    def test_a_chord_keeps_the_order_it_was_written_in(self):
        chords = parse_chords("ctrl+shift+n")
        assert [key.name for key in chords[0]] == ["ctrl", "shift", "n"]

    def test_a_sequence_of_chords(self):
        chords = parse_chords("ctrl+a delete")
        assert [[key.name for key in chord] for chord in chords] == [
            ["ctrl", "a"], ["delete"],
        ]

    def test_case_and_spacing_do_not_matter(self):
        assert parse_chords("  CTRL+S  ") == parse_chords("ctrl+s")

    @pytest.mark.parametrize(
        "spelling", ["esc", "escape", "enter", "return", "del", "delete"]
    )
    def test_the_aliases_a_model_is_likely_to_write(self, spelling):
        assert parse_chords(spelling)[0][0].code in (0x1B, 0x0D, 0x2E)

    def test_the_arrows_are_marked_extended(self):
        # Without KEYEVENTF_EXTENDEDKEY an arrow shares its virtual-key
        # code with the numeric keypad, and some applications read the
        # digit instead of the arrow.
        for name in ("up", "down", "left", "right", "home", "end", "delete"):
            assert parse_chords(name)[0][0].extended, name

    def test_a_letter_is_not_extended(self):
        assert not parse_chords("a")[0][0].extended

    def test_the_function_keys_run_to_f24(self):
        assert parse_chords("f1")[0][0].code == 0x70
        assert parse_chords("f12")[0][0].code == 0x7B
        assert parse_chords("f24")[0][0].code == 0x87

    def test_an_unknown_key_is_refused_and_not_dropped(self):
        # Dropping it would turn ctrl+s into a stray s typed into the
        # document, which the owner discovers later.
        with pytest.raises(ValueError, match="not a key I know"):
            parse_chords("ctrl+doesnotexist")

    def test_the_message_names_the_key_that_was_wrong(self):
        with pytest.raises(ValueError, match="wobble"):
            parse_chords("ctrl+wobble")

    def test_nothing_at_all_is_refused(self):
        for nothing in ("", "   ", None):
            with pytest.raises(ValueError, match="no keys"):
                parse_chords(nothing)

    def test_an_empty_half_of_a_chord_is_refused(self):
        with pytest.raises(ValueError, match="empty key"):
            parse_chords("ctrl+")

    def test_too_many_presses_in_one_call(self):
        with pytest.raises(ValueError, match=str(MAX_CHORDS)):
            parse_chords(" ".join(["a"] * (MAX_CHORDS + 1)))

    def test_exactly_the_limit_is_allowed(self):
        assert len(parse_chords(" ".join(["a"] * MAX_CHORDS))) == MAX_CHORDS

    def test_the_modifiers_know_they_are_modifiers(self):
        for name in ("ctrl", "shift", "alt", "win"):
            assert parse_chords(name)[0][0].modifier, name

    def test_a_letter_is_not_a_modifier(self):
        assert not parse_chords("q")[0][0].modifier


class TestSplittingText:

    def test_plain_text_stays_one_run(self):
        assert split_typing("hello") == ["hello"]

    def test_a_newline_becomes_a_key_press(self):
        # Injecting U+000A as a character does not start a new line in
        # most applications. VK_RETURN does.
        pieces = split_typing("a\nb")
        assert pieces[0] == "a"
        assert isinstance(pieces[1], Key) and pieces[1].code == 0x0D
        assert pieces[2] == "b"

    def test_a_windows_line_ending_is_one_press_not_two(self):
        pieces = split_typing("a\r\nb")
        assert [p if isinstance(p, str) else p.name for p in pieces] == [
            "a", "enter", "b",
        ]

    def test_a_blank_line_keeps_both_of_its_newlines(self):
        # The bug this catches: collapsing on "the last piece was an
        # enter" instead of "the previous character was a carriage
        # return" turns two paragraphs into one.
        pieces = split_typing("a\n\nb")
        assert [p if isinstance(p, str) else p.name for p in pieces] == [
            "a", "enter", "enter", "b",
        ]

    def test_a_tab_becomes_a_key_press(self):
        pieces = split_typing("a\tb")
        assert pieces[1].name == "tab"
        assert pieces[1].code == 0x09

    def test_a_lone_newline_is_just_the_key(self):
        assert [p.name for p in split_typing("\n")] == ["enter"]

    def test_text_with_nothing_special_in_it_is_not_split(self):
        assert len(split_typing("one two three")) == 1


# ----------------------------------------------------------------------
# Coordinates
# ----------------------------------------------------------------------

class TestTheCoordinatesMustBeOnTheDesktop:

    def test_a_point_on_the_desktop_is_accepted(self, synth):
        assert MoveMouseTool(synth)._target(100, 200) == (100, 200)

    def test_a_point_past_the_right_edge_is_refused(self, synth):
        # The measured case: SetCursorPos would return true and clamp.
        with pytest.raises(ValueError, match="off the desktop"):
            MoveMouseTool(synth)._target(2420, 540)

    def test_a_point_below_the_bottom_edge_is_refused(self, synth):
        with pytest.raises(ValueError, match="off the desktop"):
            MoveMouseTool(synth)._target(960, 1580)

    def test_the_refusal_says_how_big_the_desktop_is(self, synth):
        with pytest.raises(ValueError, match="1920 pixels wide"):
            MoveMouseTool(synth)._target(5000, 5)

    def test_the_last_pixel_is_on_the_desktop(self, synth):
        assert MoveMouseTool(synth)._target(1919, 1079) == (1919, 1079)

    def test_one_past_the_last_pixel_is_not(self, synth):
        with pytest.raises(ValueError):
            MoveMouseTool(synth)._target(1920, 1079)

    def test_a_monitor_left_of_the_first_one_is_reachable(self):
        # A second display to the left starts at a negative x. Refusing
        # negatives would refuse half of that owner's desktop.
        synth = MockInputSynthesizer(bounds=(-1920, 0, 3840, 1080))
        assert MoveMouseTool(synth)._target(-1000, 500) == (-1000, 500)

    def test_a_desktop_that_cannot_be_measured_does_not_block_the_move(self):
        # Unreadable bounds are not a reason to refuse: the postcondition
        # still catches a clamp afterwards.
        synth = MockInputSynthesizer(bounds=None)
        assert MoveMouseTool(synth)._target(9999, 9999) == (9999, 9999)

    def test_a_string_from_a_model_is_read_as_a_number(self, synth):
        assert MoveMouseTool(synth)._target(" 100 ", "200") == (100, 200)

    def test_a_bool_is_not_a_coordinate(self, synth):
        # True == 1 in Python, so without the guard this would silently
        # aim at the top-left corner.
        with pytest.raises(ValueError, match="not true or false"):
            MoveMouseTool(synth)._target(True, 10)

    def test_nonsense_is_refused_by_name(self, synth):
        with pytest.raises(ValueError, match="must be a number"):
            MoveMouseTool(synth)._target("over there", 10)


# ----------------------------------------------------------------------
# Moving
# ----------------------------------------------------------------------

class TestMovingThePointer:

    def test_the_pointer_is_asked_to_move_there(self, synth):
        MoveMouseTool(synth).execute(400, 300)
        assert synth.moves == [(400, 300)]

    def test_the_message_says_where(self, synth):
        assert "400, 300" in MoveMouseTool(synth).execute(400, 300)

    def test_a_move_the_machine_refuses_is_an_error(self):
        synth = MockInputSynthesizer(moves=False)
        with pytest.raises(RuntimeError, match="could not be moved"):
            MoveMouseTool(synth).execute(400, 300)

    def test_a_machine_that_cannot_be_typed_at_is_an_error(self):
        synth = MockInputSynthesizer(available=False)
        with pytest.raises(RuntimeError, match="cannot be typed at"):
            MoveMouseTool(synth).execute(400, 300)

    def test_no_synthesizer_at_all_is_an_error(self):
        tool = MoveMouseTool(synthesizer=None)
        tool.synthesizer = None
        with pytest.raises(RuntimeError, match="cannot be typed at"):
            tool.execute(400, 300)


class TestThePointerPostcondition:

    def test_the_position_is_read_back(self, synth):
        tool = MoveMouseTool(synth)
        tool.execute(400, 300)
        assert tool.verify(400, 300).ok

    def test_a_clamped_move_fails_even_though_the_call_succeeded(self):
        # This is the measured failure, mocked: the machine reports
        # success and puts the pointer somewhere else entirely.
        synth = MockInputSynthesizer(cursor=(1919, 1079))
        synth.move = lambda x, y: True          # says yes, does nothing

        verdict = MoveMouseTool(synth).verify(2420, 1580)

        assert not verdict.ok
        assert "1919, 1079" in verdict.error
        assert "2420, 1580" in verdict.error

    def test_a_pointer_that_cannot_be_read_asserts_nothing(self):
        # None is "no postcondition offered", which is honest: nothing
        # was learned. It is not the same as the move having failed.
        synth = MockInputSynthesizer(cursor=None)
        assert MoveMouseTool(synth).verify(400, 300) is None

    def test_a_pixel_of_slack_is_allowed(self):
        synth = MockInputSynthesizer(cursor=(400 + POINTER_TOLERANCE, 300))
        assert MoveMouseTool(synth).verify(400, 300).ok

    def test_one_pixel_more_than_the_slack_is_not(self):
        synth = MockInputSynthesizer(cursor=(400 + POINTER_TOLERANCE + 1, 300))
        assert not MoveMouseTool(synth).verify(400, 300).ok

    def test_the_slack_applies_to_both_axes(self):
        synth = MockInputSynthesizer(cursor=(400, 300 + POINTER_TOLERANCE + 1))
        assert not MoveMouseTool(synth).verify(400, 300).ok

    def test_the_verify_takes_the_same_arguments_as_execute(self, synth):
        # The executor calls verify(**arguments) with execute's arguments.
        # A signature that disagrees fails closed on every call.
        tool = MoveMouseTool(synth)
        arguments = {"x": 400, "y": 300}
        tool.execute(**arguments)
        assert tool.verify(**arguments).ok


# ----------------------------------------------------------------------
# Clicking
# ----------------------------------------------------------------------

class TestClicking:

    def test_the_pointer_goes_there_first(self, synth, desktop):
        ClickMouseTool(synth, desktop).execute(400, 300)
        assert synth.moves == [(400, 300)]

    def test_the_left_button_is_the_default(self, synth, desktop):
        ClickMouseTool(synth, desktop).execute(400, 300)
        assert synth.clicks == [("left", False)]

    @pytest.mark.parametrize("button", ["left", "right", "middle"])
    def test_each_button(self, synth, desktop, button):
        ClickMouseTool(synth, desktop).execute(400, 300, button=button)
        assert synth.clicks == [(button, False)]

    def test_the_button_name_is_read_loosely(self, synth, desktop):
        ClickMouseTool(synth, desktop).execute(400, 300, button=" RIGHT ")
        assert synth.clicks == [("right", False)]

    def test_a_button_that_does_not_exist_is_refused(self, synth, desktop):
        with pytest.raises(ValueError, match="not a mouse button"):
            ClickMouseTool(synth, desktop).execute(400, 300, button="scroll")

    def test_a_double_click(self, synth, desktop):
        ClickMouseTool(synth, desktop).execute(400, 300, double=True)
        assert synth.clicks == [("left", True)]

    def test_double_as_a_string_from_a_model(self, synth, desktop):
        ClickMouseTool(synth, desktop).execute(400, 300, double="true")
        assert synth.clicks == [("left", True)]

    def test_nonsense_for_double_is_refused(self, synth, desktop):
        with pytest.raises(ValueError, match="must be true or false"):
            ClickMouseTool(synth, desktop).execute(400, 300, double="maybe")

    def test_the_message_names_what_was_under_the_pointer(self, desktop):
        synth = MockInputSynthesizer()
        synth.window_at = lambda x, y: "Inbox - Mail"
        message = ClickMouseTool(synth, desktop).execute(400, 300)
        assert "Inbox - Mail" in message

    def test_a_synthesizer_with_no_window_reader_still_clicks(self, desktop):
        # `window_at` is not in the Protocol - it is an extra the Windows
        # backend happens to have. A mock without it must not break the
        # click, only the wording.
        synth = MockInputSynthesizer()
        assert not hasattr(synth, "window_at")
        assert "clicked" in ClickMouseTool(synth, desktop).execute(400, 300)

    def test_blocked_input_is_reported_rather_than_believed(self, desktop):
        # Measured: SendInput with a wrong structure size inserted nothing,
        # returned 0, and left GetLastError at zero. The accepted count is
        # the only signal that exists.
        synth = MockInputSynthesizer(accepts=False)
        with pytest.raises(RuntimeError, match="accepted"):
            ClickMouseTool(synth, desktop).execute(400, 300)

    def test_the_click_postcondition_is_the_pointer(self, synth, desktop):
        tool = ClickMouseTool(synth, desktop)
        tool.execute(400, 300)
        assert tool.verify(400, 300).ok

    def test_the_click_postcondition_catches_a_clamp(self, desktop):
        synth = MockInputSynthesizer(cursor=(1919, 1079))
        synth.move = lambda x, y: True
        verdict = ClickMouseTool(synth, desktop).verify(2420, 1580)
        assert not verdict.ok

    def test_a_pointer_that_would_not_move_clicks_nothing(self, desktop):
        synth = MockInputSynthesizer(moves=False)
        with pytest.raises(RuntimeError, match="nothing was clicked"):
            ClickMouseTool(synth, desktop).execute(400, 300)
        assert synth.clicks == []

    def test_the_coordinates_are_required(self):
        # Not a convenience: a click at "wherever the pointer is" would be
        # approved without the owner seeing where it lands, because an
        # earlier call decided that.
        names = [p.name for p in ClickMouseTool.parameters]
        required = [p.name for p in ClickMouseTool.parameters if p.required]
        assert names[:2] == ["x", "y"]
        assert required == ["x", "y"]


# ----------------------------------------------------------------------
# Typing
# ----------------------------------------------------------------------

class TestTypingText:

    def test_the_text_is_typed(self, synth, desktop):
        TypeTextTool(synth, desktop).execute("hello")
        assert synth.typed == ["hello"]

    def test_the_message_counts_characters(self, synth, desktop):
        assert "5 characters" in TypeTextTool(synth, desktop).execute("hello")

    def test_the_message_never_contains_the_text(self, synth, desktop):
        # Section 30. This is the tool most likely to be handed a
        # password, and its own result is the easiest place for one to
        # end up back in a prompt.
        secret = "hunter2-not-a-real-password"
        assert secret not in TypeTextTool(synth, desktop).execute(secret)

    def test_the_log_never_contains_the_text(self, synth, desktop, caplog):
        secret = "hunter2-not-a-real-password"
        with caplog.at_level("DEBUG"):
            TypeTextTool(synth, desktop).execute(secret)
        assert secret not in caplog.text
        assert "27 characters" in caplog.text

    def test_a_newline_is_pressed_rather_than_typed(self, synth, desktop):
        TypeTextTool(synth, desktop).execute("a\nb")
        assert synth.typed == ["a", "b"]
        assert synth.chords == [("enter",)]

    def test_nothing_to_type_is_refused(self, synth, desktop):
        for nothing in ("", None):
            with pytest.raises(ValueError, match="no text"):
                TypeTextTool(synth, desktop).execute(nothing)

    def test_too_much_text_is_refused(self, synth, desktop):
        with pytest.raises(ValueError, match=str(MAX_TEXT)):
            TypeTextTool(synth, desktop).execute("x" * (MAX_TEXT + 1))

    def test_exactly_the_limit_is_allowed(self, synth, desktop):
        TypeTextTool(synth, desktop).execute("x" * MAX_TEXT)
        assert synth.typed == ["x" * MAX_TEXT]

    def test_blocked_input_is_reported(self, desktop):
        synth = MockInputSynthesizer(accepts=False)
        with pytest.raises(RuntimeError, match="accepted"):
            TypeTextTool(synth, desktop).execute("hello")

    def test_blocked_input_on_a_pressed_key_is_reported(self, desktop):
        synth = MockInputSynthesizer(accepts=False)
        with pytest.raises(RuntimeError, match="accepted"):
            TypeTextTool(synth, desktop).execute("\n")


class TestTheTypingPostcondition:

    def test_without_a_named_window_it_asserts_nothing(self, synth, desktop):
        # There is nothing to re-ask. Whether the characters arrived is
        # not knowable from here, and a verify claiming otherwise would
        # be inventing the one fact section 11 is about.
        assert TypeTextTool(synth, desktop).verify("hello") is None

    def test_with_a_named_window_it_re_asks_the_guard(self, synth):
        desktop = FakeWindows(front="Editor - notes.txt")
        verdict = TypeTextTool(synth, desktop).verify("hello", window="Editor")
        assert verdict.ok

    def test_a_window_that_moved_away_is_a_failure(self, synth):
        desktop = FakeWindows(front="Bank - transfer")
        verdict = TypeTextTool(synth, desktop).verify("hello", window="Editor")
        assert not verdict.ok
        assert "Bank - transfer" in verdict.error

    def test_an_unreadable_desktop_asserts_nothing(self, synth):
        desktop = FakeWindows(raises=True)
        assert TypeTextTool(synth, desktop).verify("hi", window="Editor") is None

    def test_it_never_claims_the_text_arrived(self, synth):
        desktop = FakeWindows(front="Editor")
        verdict = TypeTextTool(synth, desktop).verify("hello", window="Editor")
        assert "hello" not in verdict.output
        assert "in front" in verdict.output


# ----------------------------------------------------------------------
# Pressing
# ----------------------------------------------------------------------

class TestPressingKeys:

    def test_a_chord_is_sent(self, synth, desktop):
        PressKeysTool(synth, desktop).execute("ctrl+s")
        assert synth.chords == [("ctrl", "s")]

    def test_a_sequence_is_sent_in_order(self, synth, desktop):
        PressKeysTool(synth, desktop).execute("ctrl+a delete")
        assert synth.chords == [("ctrl", "a"), ("delete",)]

    def test_the_message_spells_the_keys_back(self, synth, desktop):
        assert "ctrl+s" in PressKeysTool(synth, desktop).execute("ctrl+s")

    def test_an_unknown_key_presses_nothing_at_all(self, synth, desktop):
        with pytest.raises(ValueError):
            PressKeysTool(synth, desktop).execute("ctrl+nope")
        assert synth.chords == []

    def test_blocked_input_is_reported(self, desktop):
        synth = MockInputSynthesizer(accepts=False)
        with pytest.raises(RuntimeError, match="accepted"):
            PressKeysTool(synth, desktop).execute("ctrl+s")


class TestTheStuckModifierPostcondition:

    def test_a_clean_press_passes(self, synth, desktop):
        assert PressKeysTool(synth, desktop).verify("ctrl+s").ok

    def test_a_modifier_left_held_down_is_a_failure(self, desktop):
        # The nasty one: no message anywhere says why the owner's next
        # keystroke became a shortcut.
        synth = MockInputSynthesizer(stuck=("ctrl",))
        verdict = PressKeysTool(synth, desktop).verify("ctrl+s")
        assert not verdict.ok
        assert "ctrl" in verdict.error

    def test_the_failure_says_what_it_would_do(self, desktop):
        synth = MockInputSynthesizer(stuck=("alt",))
        verdict = PressKeysTool(synth, desktop).verify("alt+f4")
        assert "every key typed next" in verdict.error

    def test_a_letter_left_held_is_not_reported(self, desktop):
        # Only modifiers can strand the keyboard. A letter repeats and
        # stops; reporting it would fail good calls on a host whose key
        # state lags.
        synth = MockInputSynthesizer(stuck=("s",))
        assert PressKeysTool(synth, desktop).verify("ctrl+s").ok

    def test_only_the_keys_this_call_pressed_are_checked(self, desktop):
        # The owner physically holding shift is not this call's fault.
        synth = MockInputSynthesizer(stuck=("shift",))
        assert PressKeysTool(synth, desktop).verify("ctrl+s").ok

    def test_unparseable_keys_assert_nothing(self, synth, desktop):
        # They never reached the machine; execute already refused.
        assert PressKeysTool(synth, desktop).verify("ctrl+nope") is None

    def test_the_guard_is_re_asked_too(self, synth):
        desktop = FakeWindows(front="Bank")
        verdict = PressKeysTool(synth, desktop).verify("ctrl+s", window="Editor")
        assert not verdict.ok

    def test_a_stuck_key_outranks_the_guard(self):
        # Both are wrong; the stuck modifier is the one that keeps
        # hurting after the call, so it is the one reported.
        desktop = FakeWindows(front="Bank")
        synth = MockInputSynthesizer(stuck=("ctrl",))
        verdict = PressKeysTool(synth, desktop).verify("ctrl+s", window="Editor")
        assert "held down" in verdict.error

    def test_it_never_claims_the_shortcut_worked(self, synth, desktop):
        verdict = PressKeysTool(synth, desktop).verify("ctrl+s")
        assert "saved" not in verdict.output
        assert "held down" in verdict.output


# ----------------------------------------------------------------------
# The guard
# ----------------------------------------------------------------------

class TestTheWindowGuard:

    def test_the_matching_window_is_allowed(self, synth):
        desktop = FakeWindows(front="Editor - notes.txt")
        TypeTextTool(synth, desktop).execute("hi", window="Editor")
        assert synth.typed == ["hi"]

    def test_the_match_is_a_case_insensitive_substring(self, synth):
        desktop = FakeWindows(front="Editor - notes.txt")
        TypeTextTool(synth, desktop).execute("hi", window="NOTES.TXT")
        assert synth.typed == ["hi"]

    def test_the_wrong_window_refuses(self, synth):
        # The case this exists for: focus the editor, then type. Between
        # the two the owner alt-tabs to their bank.
        desktop = FakeWindows(front="Bank - transfer")
        with pytest.raises(RuntimeError, match="not the window in front"):
            TypeTextTool(synth, desktop).execute("hi", window="Editor")

    def test_the_refusal_names_what_is_in_front_instead(self, synth):
        desktop = FakeWindows(front="Bank - transfer")
        with pytest.raises(RuntimeError, match="Bank - transfer"):
            TypeTextTool(synth, desktop).execute("hi", window="Editor")

    def test_the_refusal_says_how_to_fix_it(self, synth):
        desktop = FakeWindows(front="Bank")
        with pytest.raises(RuntimeError, match="focus_window"):
            TypeTextTool(synth, desktop).execute("hi", window="Editor")

    def test_an_unreadable_desktop_refuses_rather_than_skipping(self, synth):
        # A guard that cannot be checked must not be ignored. Giving the
        # caller the words without the protection is worse than no.
        desktop = FakeWindows(raises=True)
        with pytest.raises(RuntimeError, match="could not be read"):
            TypeTextTool(synth, desktop).execute("hi", window="Editor")

    def test_no_window_source_at_all_refuses(self, synth):
        with pytest.raises(RuntimeError, match="will not type into it blind"):
            TypeTextTool(synth, None).execute("hi", window="Editor")

    def test_no_window_source_is_fine_when_no_guard_was_asked_for(self, synth):
        TypeTextTool(synth, None).execute("hi")
        assert synth.typed == ["hi"]

    def test_a_desktop_with_nothing_in_front_refuses_a_guard(self, synth):
        desktop = FakeWindows(front=None)
        with pytest.raises(RuntimeError, match="nothing reports being active"):
            TypeTextTool(synth, desktop).execute("hi", window="Editor")

    def test_a_blank_window_argument_is_not_a_guard(self, synth):
        desktop = FakeWindows(front="Bank")
        TypeTextTool(synth, desktop).execute("hi", window="   ")
        assert synth.typed == ["hi"]

    def test_the_guard_works_on_clicks_too(self, synth):
        desktop = FakeWindows(front="Bank")
        with pytest.raises(RuntimeError, match="not the window in front"):
            ClickMouseTool(synth, desktop).execute(400, 300, window="Editor")

    def test_the_guard_works_on_key_presses_too(self, synth):
        desktop = FakeWindows(front="Bank")
        with pytest.raises(RuntimeError, match="not the window in front"):
            PressKeysTool(synth, desktop).execute("ctrl+s", window="Editor")


# ----------------------------------------------------------------------
# The ordering property: a refused call touches nothing
# ----------------------------------------------------------------------

class TestNothingReachesTheMachineOnARefusedCall:
    """
    Every one of these would still pass if validation ran *after* acting,
    were it not for the assertion on the recorded events. Half-typed text
    and a click already delivered cannot be taken back.
    """

    def test_a_coordinate_off_the_desktop_moves_nothing(self, synth, desktop):
        with pytest.raises(ValueError):
            ClickMouseTool(synth, desktop).execute(2420, 1580)
        assert synth.moves == []
        assert synth.clicks == []

    def test_a_bad_button_moves_nothing(self, synth, desktop):
        with pytest.raises(ValueError):
            ClickMouseTool(synth, desktop).execute(400, 300, button="scroll")
        assert synth.moves == []
        assert synth.clicks == []

    def test_a_bad_double_flag_moves_nothing(self, synth, desktop):
        with pytest.raises(ValueError):
            ClickMouseTool(synth, desktop).execute(400, 300, double="maybe")
        assert synth.moves == []
        assert synth.clicks == []

    def test_a_failed_guard_clicks_nothing(self, synth):
        desktop = FakeWindows(front="Bank")
        with pytest.raises(RuntimeError):
            ClickMouseTool(synth, desktop).execute(400, 300, window="Editor")
        assert synth.moves == []
        assert synth.clicks == []

    def test_a_failed_guard_types_nothing(self, synth):
        desktop = FakeWindows(front="Bank")
        with pytest.raises(RuntimeError):
            TypeTextTool(synth, desktop).execute("secret", window="Editor")
        assert synth.typed == []
        assert synth.chords == []

    def test_a_failed_guard_presses_nothing(self, synth):
        desktop = FakeWindows(front="Bank")
        with pytest.raises(RuntimeError):
            PressKeysTool(synth, desktop).execute("ctrl+s", window="Editor")
        assert synth.chords == []

    def test_text_over_the_limit_types_nothing(self, synth, desktop):
        with pytest.raises(ValueError):
            TypeTextTool(synth, desktop).execute("x" * (MAX_TEXT + 1))
        assert synth.typed == []

    def test_empty_text_types_nothing(self, synth, desktop):
        with pytest.raises(ValueError):
            TypeTextTool(synth, desktop).execute("")
        assert synth.typed == []

    def test_an_unknown_key_presses_nothing(self, synth, desktop):
        with pytest.raises(ValueError):
            PressKeysTool(synth, desktop).execute("ctrl+nope")
        assert synth.chords == []

    def test_too_many_chords_press_nothing(self, synth, desktop):
        with pytest.raises(ValueError):
            PressKeysTool(synth, desktop).execute(" ".join(["a"] * 99))
        assert synth.chords == []


# ----------------------------------------------------------------------
# Risk and registration
# ----------------------------------------------------------------------

class TestTheRiskLevel:

    @pytest.mark.parametrize(
        "cls", [MoveMouseTool, ClickMouseTool, TypeTextTool, PressKeysTool]
    )
    def test_every_one_of_them_is_dangerous(self, cls):
        assert cls.risk is ToolRisk.DANGEROUS

    @pytest.mark.parametrize(
        "cls", [ClickMouseTool, TypeTextTool, PressKeysTool]
    )
    def test_the_description_says_it_is_real_input(self, cls):
        # The description is what a confirmation prompt shows, so it has
        # to say what the owner is agreeing to.
        assert any(
            word in cls.description.lower()
            for word in ("real", "as if typed", "in front")
        )

    def test_the_names_are_the_four_expected(self):
        assert [
            cls.name for cls in
            (MoveMouseTool, ClickMouseTool, TypeTextTool, PressKeysTool)
        ] == ["move_mouse", "click_mouse", "type_text", "press_keys"]


class TestChoosingABackend:

    def test_off_windows_there_is_no_synthesizer(self, monkeypatch):
        not_windows(monkeypatch)
        assert default_input_synthesizer() is None

    def test_off_windows_the_backend_reports_unavailable(self, monkeypatch):
        not_windows(monkeypatch)
        assert not WindowsInputSynthesizer().is_available()

    def test_it_is_never_a_mock(self, monkeypatch):
        # A mock reporting that it typed into a desktop it cannot see is
        # worse than a missing tool.
        not_windows(monkeypatch)
        assert not isinstance(default_input_synthesizer(), MockInputSynthesizer)

    @WINDOWS_ONLY
    def test_on_windows_there_is_one(self):
        assert default_input_synthesizer() is not None


class TestTheFactory:

    def test_the_four_are_registered_when_input_is_available(self, monkeypatch):
        import tools.factory as factory

        monkeypatch.setattr(
            "tools.builtins.input.default_input_synthesizer",
            lambda: MockInputSynthesizer(),
        )

        names = {tool.name for tool in factory._builtin_tools({})}

        assert {"move_mouse", "click_mouse", "type_text", "press_keys"} <= names

    def test_none_are_registered_when_input_is_not(self, monkeypatch):
        # The factory's rule: a tool whose dependency is absent is
        # missing rather than present and broken.
        import tools.factory as factory

        monkeypatch.setattr(
            "tools.builtins.input.default_input_synthesizer", lambda: None
        )

        names = {tool.name for tool in factory._builtin_tools({})}

        assert not names & {
            "move_mouse", "click_mouse", "type_text", "press_keys"
        }

    def test_they_share_the_window_source_with_the_desktop_tools(
        self, monkeypatch
    ):
        """
        One enumeration of the desktop, not several.

        The `window` argument is a safety guard - only type if this window
        is in front - and a guard reading its own separately-timed
        enumeration would be answering about a desktop nobody else saw.

        The stub hands out a *different* source on every call so a second
        call is detectable. Returning one shared object here would make
        this test pass whether the factory called the function once or
        five times, which is how it passed a mutation that did.
        """

        import tools.factory as factory

        handed = []

        def fresh():
            handed.append(FakeWindows())
            return handed[-1]

        monkeypatch.setattr(
            "tools.builtins.input.default_input_synthesizer",
            lambda: MockInputSynthesizer(),
        )
        monkeypatch.setattr(
            "tools.builtins.desktop.default_window_source", fresh
        )

        built = {tool.name: tool for tool in factory._builtin_tools({})}

        assert len(handed) == 1, f"the desktop was enumerated {len(handed)} times"

        # `list_windows` calls it `source`, these call it `windows`. The
        # object is what has to match, not the attribute name.
        shared = built["list_windows"].source

        assert shared is handed[0]

        for name in ("move_mouse", "click_mouse", "type_text", "press_keys"):
            assert built[name].windows is shared, name


class TestTheShippedConfigGrantsNothing:

    def test_none_of_them_are_allowed_out_of_the_box(self):
        import yaml

        with open("config.yaml", encoding="utf-8") as handle:
            shipped = yaml.safe_load(handle)

        allowed = shipped.get("tools", {}).get("allowed") or []

        assert not set(allowed) & {
            "move_mouse", "click_mouse", "type_text", "press_keys"
        }


# ----------------------------------------------------------------------
# Through the executor
# ----------------------------------------------------------------------

class TestThroughTheExecutor:

    def test_an_approved_call_runs(self, synth, desktop):
        executor = executor_for(TypeTextTool(synth, desktop))
        assert executor.execute("type_text", {"text": "hello"}).ok
        assert synth.typed == ["hello"]

    def test_without_approval_nothing_is_typed(self, synth, desktop):
        executor = executor_for(TypeTextTool(synth, desktop), approve=False)
        assert not executor.execute("type_text", {"text": "hello"}).ok
        assert synth.typed == []

    def test_an_unlisted_tool_types_nothing(self, synth, desktop):
        executor = executor_for(TypeTextTool(synth, desktop), allowed=[])
        assert not executor.execute("type_text", {"text": "hello"}).ok
        assert synth.typed == []

    def test_a_failed_postcondition_downgrades_the_call(self, desktop):
        # The click happened; the pointer is not where it was aimed. The
        # executor reports the failure rather than the tool's own message.
        synth = MockInputSynthesizer(cursor=(1919, 1079))
        synth.move = lambda x, y: True

        executor = executor_for(ClickMouseTool(synth, desktop))

        result = executor.execute("click_mouse", {"x": 400, "y": 300})

        assert not result.ok
        assert "1919, 1079" in result.error

    def test_a_stuck_modifier_downgrades_the_call(self, desktop):
        synth = MockInputSynthesizer(stuck=("ctrl",))
        executor = executor_for(PressKeysTool(synth, desktop))

        result = executor.execute("press_keys", {"keys": "ctrl+s"})

        assert not result.ok
        assert "held down" in result.error

    def test_a_good_call_keeps_its_own_message(self, synth, desktop):
        executor = executor_for(MoveMouseTool(synth))
        assert "400, 300" in executor.execute(
            "move_mouse", {"x": 400, "y": 300}
        ).output

    def test_the_guard_failure_reaches_the_caller(self, synth):
        desktop = FakeWindows(front="Bank - transfer")
        executor = executor_for(TypeTextTool(synth, desktop))

        result = executor.execute(
            "type_text", {"text": "hello", "window": "Editor"}
        )

        assert not result.ok
        assert "Bank - transfer" in result.error
        assert synth.typed == []


# ----------------------------------------------------------------------
# The real machine
# ----------------------------------------------------------------------

@WINDOWS_ONLY
class TestTheWindowsBackend:
    """
    Reads only, with one exception that puts itself back.

    Nothing here presses a key or a mouse button. The suite runs while the
    owner may be using the machine, and a synthesised click during a test
    run would land on whatever they happen to have open.
    """

    def test_the_input_structure_is_the_size_windows_expects(self):
        import ctypes

        synthesizer = WindowsInputSynthesizer()
        bound = synthesizer._bind()

        assert bound is not None

        _, _, _, INPUT, KEYBDINPUT, MOUSEINPUT = bound

        # Measured on this host: 40, with a pointer-sized dwExtraInfo. A
        # wrong size is the failure that inserts nothing and reports no
        # error at all.
        assert ctypes.sizeof(INPUT) == 40
        assert ctypes.sizeof(KEYBDINPUT) == 24
        assert ctypes.sizeof(MOUSEINPUT) == 32

    def test_the_binding_is_cached(self):
        synthesizer = WindowsInputSynthesizer()
        assert synthesizer._bind() is synthesizer._bind()

    def test_the_desktop_has_a_size(self):
        bounds = WindowsInputSynthesizer().bounds()
        assert bounds is not None
        assert bounds[2] > 0 and bounds[3] > 0

    def test_the_pointer_can_be_read(self):
        where = WindowsInputSynthesizer().cursor()
        assert where is not None
        assert isinstance(where[0], int) and isinstance(where[1], int)

    def test_the_pointer_lands_exactly_where_it_is_sent(self):
        # The measurement the whole file rests on, and its own undo.
        synthesizer = WindowsInputSynthesizer()

        start = synthesizer.cursor()
        assert start is not None

        try:
            assert synthesizer.move(120, 140)
            assert synthesizer.cursor() == (120, 140)
        finally:
            synthesizer.move(*start)

    def test_an_off_desktop_move_is_clamped_and_still_reports_success(self):
        # Section 11 in one assertion: the API says yes and does something
        # else. This is why `_target` refuses instead of trusting it.
        synthesizer = WindowsInputSynthesizer()

        left, top, width, height = synthesizer.bounds()
        start = synthesizer.cursor()

        try:
            assert synthesizer.move(left + width + 500, top + height + 500)
            landed = synthesizer.cursor()
            assert landed != (left + width + 500, top + height + 500)
            assert landed == (left + width - 1, top + height - 1)
        finally:
            synthesizer.move(*start)

    def test_the_window_under_a_point_can_be_named(self):
        synthesizer = WindowsInputSynthesizer()
        where = synthesizer.cursor()
        # A string either way - "" when there is nothing there is a real
        # answer, not a failure.
        assert isinstance(synthesizer.window_at(*where), str)

    def test_no_modifier_is_reported_held_when_none_is(self):
        synthesizer = WindowsInputSynthesizer()
        # If this fails the owner is holding a key, which the suite
        # cannot control - but it is worth knowing, because every
        # stuck-modifier verify would then be reporting the truth.
        assert not synthesizer.held(Key("ctrl", 0x11))

    def test_chunking_is_not_accidentally_disabled(self):
        assert CHUNK > 0


# ----------------------------------------------------------------------
# The Windows event stream, without a Windows desktop
# ----------------------------------------------------------------------

class TestTheEventsSentToWindows:
    """
    The order and flags of the events `press` and `write` build.

    This is the one part of the backend the mock cannot speak for, and it
    holds the failure that is hardest to see afterwards: releasing CTRL
    before the letter turns `ctrl+s` into a stray `s` typed into the
    owner's document. The e2e probe caught it on real hardware by reading
    a `shift+q` back as `Q`; this catches it without touching a keyboard.

    `_bind` is stubbed truthy and `_key_event` is replaced with a recorder,
    so no INPUT structure is built and `SendInput` is never reached.
    """

    KEYUP = 0x0002
    UNICODE = 0x0004
    EXTENDED = 0x0001

    @staticmethod
    def recorder(monkeypatch):

        synthesizer = WindowsInputSynthesizer()
        events = []

        monkeypatch.setattr(
            synthesizer, "_bind", lambda: (None,) * 6, raising=False
        )
        monkeypatch.setattr(
            synthesizer,
            "_key_event",
            lambda code, scan, flags: events.append((code, scan, flags)),
            raising=False,
        )
        monkeypatch.setattr(
            synthesizer,
            "_send",
            lambda built: (len(built), len(built)),
            raising=False,
        )

        return synthesizer, events

    # ------------------------------------------------------------------

    def test_a_chord_goes_down_in_order(self, monkeypatch):
        synthesizer, events = self.recorder(monkeypatch)

        synthesizer.press(parse_chords("ctrl+shift+n")[0])

        down = [code for code, _, flags in events if not flags & self.KEYUP]
        assert down == [0x11, 0x10, 0x4E]

    def test_a_chord_comes_up_in_reverse(self, monkeypatch):
        # The whole point. CTRL goes down first and comes up last, so the
        # letter is still inside the modifier when the application sees it.
        synthesizer, events = self.recorder(monkeypatch)

        synthesizer.press(parse_chords("ctrl+s")[0])

        assert [(code, flags) for code, _, flags in events] == [
            (0x11, 0), (0x53, 0),
            (0x53, self.KEYUP), (0x11, self.KEYUP),
        ]

    def test_every_key_is_released(self, monkeypatch):
        # A missing key-up is the stuck modifier the postcondition catches
        # after the fact; not building it is how it gets stuck.
        synthesizer, events = self.recorder(monkeypatch)

        synthesizer.press(parse_chords("ctrl+alt+delete")[0])

        assert len(events) == 6
        assert sum(1 for _, _, flags in events if flags & self.KEYUP) == 3

    def test_the_extended_flag_is_on_both_halves(self, monkeypatch):
        # Windows matches the up event to the down event by flags as well
        # as code. An extended down with a plain up leaves the key down.
        synthesizer, events = self.recorder(monkeypatch)

        synthesizer.press(parse_chords("up")[0])

        assert all(flags & self.EXTENDED for _, _, flags in events)

    def test_a_plain_letter_carries_no_extended_flag(self, monkeypatch):
        synthesizer, events = self.recorder(monkeypatch)

        synthesizer.press(parse_chords("a")[0])

        assert not any(flags & self.EXTENDED for _, _, flags in events)

    def test_text_is_sent_as_unicode_scan_codes(self, monkeypatch):
        # wVk stays 0: the character is carried in wScan with
        # KEYEVENTF_UNICODE, which is what makes layout irrelevant.
        synthesizer, events = self.recorder(monkeypatch)

        synthesizer.write("hi")

        assert [(code, scan) for code, scan, _ in events] == [
            (0, ord("h")), (0, ord("h")), (0, ord("i")), (0, ord("i")),
        ]
        assert all(flags & self.UNICODE for _, _, flags in events)

    def test_each_character_is_pressed_and_released(self, monkeypatch):
        synthesizer, events = self.recorder(monkeypatch)

        synthesizer.write("hi")

        assert [bool(flags & self.KEYUP) for _, _, flags in events] == [
            False, True, False, True,
        ]

    def test_an_emoji_is_sent_as_two_adjacent_halves(self, monkeypatch):
        # KEYEVENTF_UNICODE carries 16 bits, so a character outside the
        # BMP is a surrogate pair and the two halves have to arrive next
        # to each other for Windows to recombine them.
        synthesizer, events = self.recorder(monkeypatch)

        synthesizer.write("\U0001F600")

        scans = [scan for _, scan, _ in events]

        assert len(events) == 4
        assert scans == [0xD83D, 0xD83D, 0xDE00, 0xDE00]

    def test_an_accented_character_survives(self, monkeypatch):
        synthesizer, events = self.recorder(monkeypatch)

        synthesizer.write("é")

        assert [scan for _, scan, _ in events] == [0x00E9, 0x00E9]

    def test_nothing_is_built_when_the_binding_fails(self, monkeypatch):
        # Off Windows there is no user32 to bind, and the honest answer is
        # zero accepted out of zero submitted rather than an exception.
        synthesizer = WindowsInputSynthesizer()
        monkeypatch.setattr(synthesizer, "_bind", lambda: None, raising=False)

        assert synthesizer.press(parse_chords("ctrl+s")[0]) == (0, 0)
        assert synthesizer.write("hello") == (0, 0)
