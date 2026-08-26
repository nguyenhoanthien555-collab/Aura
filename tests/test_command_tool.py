"""
Tests for the declared-command tool (Section 24, phase 18.2).

Section 24's line is *"Do not give arbitrary LLM text direct unrestricted
shell execution without a controlled tool boundary"*, so the tests that
matter most here are the ones that try to get arbitrary text through the
boundary and fail. Three of them run real programs and real payloads,
because the interesting failures are not the ones a mock can stage:

  * a value containing `&&` reaching a program as one argument rather than
    as two commands,
  * a batch file being refused before cmd.exe can re-parse a quote,
  * a command that overruns being stopped along with its children instead
    of holding the reply open.

The remainder is the owner's contract (Section 2): what gets refused, what
only gets warned about, and what the shipped config does and does not
enable.
"""

import io
import json
import os
import subprocess
import sys
import time

import pytest
import yaml

from tools.base import ToolProtocol, ToolRisk
from tools.builtins.commands import (
    DEFAULT_COMMAND_TIMEOUT,
    MAX_OUTPUT,
    Command,
    RunCommandTool,
    _child_environment,
    _credential_names,
    _normalise,
)
from tools.executor import ToolExecutor, ToolPolicy
from tools.factory import build_registry
from tools.registry import ToolRegistry


WINDOWS_ONLY = pytest.mark.skipif(
    os.name != "nt", reason="batch files are a Windows problem"
)


# A tiny program that reports its own argv, so a test can prove that what
# the model supplied arrived as exactly one argument. Using the running
# interpreter keeps it portable and needs nothing installed.
ECHO_ARGV = "import sys, json; print(json.dumps(sys.argv[1:]))"

ENV_REPORT = (
    "import os;"
    "found = sorted(k for k in os.environ"
    " if any(w in k.upper() for w in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD')));"
    "print('CREDENTIALS:', len(found), '|', ','.join(found));"
    "print('LEAK:', any('sk-test' in v for v in os.environ.values()));"
    "print('PATH:', bool(os.environ.get('PATH')));"
    "print('ORDINARY:', os.environ.get('SOMETHING_ORDINARY', 'missing'))"
)


def argv_tool(*extra: str, **entry) -> RunCommandTool:
    """A command that prints its arguments as JSON."""

    declared = {
        "argv": [sys.executable, "-c", ECHO_ARGV, *extra],
        **entry,
    }

    return RunCommandTool({"probe": declared})


def reported_argv(result) -> list[str]:
    """The argv a successful `probe` run printed."""

    assert result.ok, result.error

    body = result.output.split("\n", 1)[1]

    return json.loads(body)


# ----------------------------------------------------------------------
# The property the whole phase exists for
# ----------------------------------------------------------------------


class TestNothingBecomesASecondCommand:
    """
    The model's value is data, all the way to the program.

    Every case here is a string that would mean something to a shell. None
    of them ever meets one, so each has to come back as a single argument
    with the punctuation intact.
    """

    @pytest.mark.parametrize(
        "payload",
        [
            "plain",
            "two words",
            "a && b",
            "a & b",
            "a | b",
            "a; b",
            "a > out.txt",
            "$(echo hi)",
            "`echo hi`",
            "%PATH%",
            'quote " inside',
            "back\\slash",
            "new\nline",
            "semi;colon && pipe | redirect >",
            "--looks-like-a-flag",
        ],
    )
    def test_a_value_arrives_as_exactly_one_argument(self, payload):

        tool = argv_tool("{value}")

        got = reported_argv(tool.execute("probe", {"value": payload}))

        assert got == [payload]

    def test_a_value_cannot_add_an_argument(self):
        """
        The count is fixed by the owner's argv, not by what is put in it.

        This is the one that would break if anything here ever joined the
        argv into a string and re-split it.
        """

        tool = argv_tool("--flag", "{value}", "--after")

        got = reported_argv(
            tool.execute("probe", {"value": "x --injected y"})
        )

        assert got == ["--flag", "x --injected y", "--after"]

    def test_a_slot_embedded_in_an_element_stays_one_element(self):

        tool = argv_tool("--pattern={value}")

        got = reported_argv(tool.execute("probe", {"value": "a && b"}))

        assert got == ["--pattern=a && b"]

    def test_the_same_slot_twice_fills_both(self):

        tool = argv_tool("{value}", "{value}")

        got = reported_argv(tool.execute("probe", {"value": "x"}))

        assert got == ["x", "x"]

    def test_a_number_is_rendered_without_the_caller_stringifying_it(self):

        tool = argv_tool("{count}")

        assert reported_argv(tool.execute("probe", {"count": 12})) == ["12"]


# ----------------------------------------------------------------------
# Batch files: the measured hole
# ----------------------------------------------------------------------


@WINDOWS_ONLY
class TestBatchFilesAreRefused:
    """
    A `.bat` hands its arguments back to cmd.exe, and a quote escapes.

    The payload here is the one that actually created a file during the
    probe that motivated this refusal, so these tests fail loudly if the
    refusal is ever removed - including on a future interpreter where the
    underlying behaviour may have changed again.
    """

    PAYLOAD = 'x" & echo INJECTED > {canary} & "'

    @pytest.fixture
    def batch(self, tmp_path):

        script = tmp_path / "victim.bat"
        script.write_text("@echo off\r\necho GOT[%1]\r\n", encoding="utf-8")

        return script

    def test_a_batch_file_with_a_slot_is_refused_and_nothing_runs(
        self, batch, tmp_path
    ):

        canary = tmp_path / "canary.txt"

        tool = RunCommandTool(
            {"risky": {"argv": [str(batch), "{value}"]}}
        )

        result = tool.execute(
            "risky", {"value": self.PAYLOAD.format(canary=canary)}
        )

        assert not result.ok
        assert "batch file" in result.error
        assert "Nothing was run" in result.error

        # The refusal is the point, not the message: if the command had run,
        # this file would exist.
        assert not canary.exists()

    def test_the_refusal_says_what_to_do_instead(self, batch):

        tool = RunCommandTool(
            {"risky": {"argv": [str(batch), "{value}"]}}
        )

        error = tool.execute("risky", {"value": "x"}).error

        assert "victim.bat" in error
        assert "Declare the underlying program directly" in error

    def test_a_batch_file_with_no_slot_still_runs(self, batch):
        """
        Section 2: the owner declared the whole thing, arguments included.

        There is no model-supplied text in it, so there is nothing for
        cmd.exe to re-parse that the owner did not write themselves.
        """

        tool = RunCommandTool(
            {"safe": {"argv": [str(batch), "literal & text"]}}
        )

        result = tool.execute("safe")

        assert result.ok
        assert "GOT[" in result.output

    def test_a_cmd_file_is_refused_the_same_way(self, tmp_path):

        script = tmp_path / "victim.cmd"
        script.write_text("@echo off\r\necho hi\r\n", encoding="utf-8")

        tool = RunCommandTool({"risky": {"argv": [str(script), "{v}"]}})

        assert "batch file" in tool.execute("risky", {"v": "x"}).error

    def test_the_suffix_check_is_case_insensitive(self, tmp_path):

        script = tmp_path / "victim.BAT"
        script.write_text("@echo off\r\necho hi\r\n", encoding="utf-8")

        tool = RunCommandTool({"risky": {"argv": [str(script), "{v}"]}})

        assert "batch file" in tool.execute("risky", {"v": "x"}).error


# ----------------------------------------------------------------------
# Overrunning, and the process tree
# ----------------------------------------------------------------------


class TestAnOverrunIsBoundedAndSaysSo:

    # Long enough that the child is definitely still running when the
    # deadline passes, short enough not to slow the suite down.
    LIMIT = 0.6

    def sleeper(self, seconds: float = 30.0) -> list[str]:

        return [
            sys.executable,
            "-c",
            f"import sys, time; print('STARTED', flush=True); "
            f"time.sleep({seconds})",
        ]

    def test_a_command_that_overruns_is_stopped_and_reported_as_failed(self):

        tool = RunCommandTool(
            {"slow": {"argv": self.sleeper(), "timeout": self.LIMIT}}
        )

        started = time.monotonic()
        result = tool.execute("slow")
        elapsed = time.monotonic() - started

        assert not result.ok
        assert "did not finish" in result.error
        assert "was stopped" in result.error

        # Bounded, not merely eventually returned. The generous ceiling is
        # the kill path, which is allowed to take a moment.
        assert elapsed < self.LIMIT + 8.0

    def test_what_it_printed_before_the_kill_is_still_reported(self):
        """
        The most useful thing about a killed command is what it got to say.

        Discarding it would make a timeout indistinguishable from a command
        that produced nothing, which are very different problems.
        """

        tool = RunCommandTool(
            {"slow": {"argv": self.sleeper(), "timeout": self.LIMIT}}
        )

        assert "STARTED" in tool.execute("slow").error

    def test_a_command_that_finishes_in_time_is_not_reported_as_stopped(self):

        tool = RunCommandTool(
            {"quick": {"argv": self.sleeper(0.01), "timeout": 20.0}}
        )

        result = tool.execute("quick")

        assert result.ok
        assert "STARTED" in result.output

    def test_repeated_overruns_do_not_accumulate(self):
        """
        Three in a row, each still bounded.

        A kill path that leaves the process behind would not fail the
        single-shot test above - the call still returns - but the leftovers
        pile up. This is the portable half of the evidence; the Windows
        test below reads the process table directly for the real answer.
        """

        tool = RunCommandTool(
            {"slow": {"argv": self.sleeper(), "timeout": self.LIMIT}}
        )

        for attempt in range(3):

            started = time.monotonic()
            result = tool.execute("slow")
            elapsed = time.monotonic() - started

            assert not result.ok
            assert elapsed < self.LIMIT + 8.0, f"run {attempt}: {elapsed:.2f}s"


@WINDOWS_ONLY
def test_a_grandchild_does_not_survive_the_kill():
    """
    The case that made a one second timeout take 29.25 seconds.

    A batch file that leaves a background process running is the exact
    shape that held an inherited pipe open. It is a Windows test because
    `start /b` is, and because `taskkill /T` is the thing being checked.
    """

    import tempfile
    from pathlib import Path

    directory = Path(tempfile.mkdtemp())

    script = directory / "spawner.bat"
    script.write_text(
        "@echo off\r\necho STARTING\r\n"
        "start /b ping -n 40 127.0.0.1\r\n"
        "ping -n 40 127.0.0.1\r\n",
        encoding="utf-8",
    )

    tool = RunCommandTool({"slow": {"argv": [str(script)], "timeout": 1.0}})

    started = time.monotonic()
    result = tool.execute("slow")
    elapsed = time.monotonic() - started

    assert not result.ok
    # The measured pipe version of this took 29.25s. Anything near that
    # means the tree kill stopped working.
    assert elapsed < 10.0, f"took {elapsed:.2f}s"

    # And the grandchild specifically.
    time.sleep(0.5)

    listing = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq PING.EXE"],
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout

    assert "PING.EXE" not in listing.upper()


# ----------------------------------------------------------------------
# Exit status, which is what Section 11 rests on here
# ----------------------------------------------------------------------


class TestTheProgramsOwnVerdictIsUsed:

    def test_a_non_zero_exit_is_a_failure_not_a_success_with_sad_text(self):

        tool = RunCommandTool(
            {
                "bad": [
                    sys.executable,
                    "-c",
                    "import sys; sys.stderr.write('it went wrong'); "
                    "sys.exit(3)",
                ]
            }
        )

        result = tool.execute("bad")

        assert not result.ok
        assert "status 3" in result.error
        assert "it went wrong" in result.error

    def test_a_program_that_fails_silently_still_reports_the_status(self):

        tool = RunCommandTool(
            {"bad": [sys.executable, "-c", "import sys; sys.exit(7)"]}
        )

        result = tool.execute("bad")

        assert not result.ok
        assert "status 7" in result.error
        assert "said nothing" in result.error

    def test_a_success_with_no_output_says_so_rather_than_returning_nothing(
        self,
    ):
        """
        An empty string reads to a model like a tool that broke.

        `touch`-shaped commands legitimately print nothing, and the
        difference between "did it and had nothing to say" and "produced no
        answer" is the difference between a model reporting success and a
        model retrying.
        """

        tool = RunCommandTool({"quiet": [sys.executable, "-c", "pass"]})

        result = tool.execute("quiet")

        assert result.ok
        assert "produced no output" in result.output

    def test_stderr_is_reported_when_a_successful_command_used_it(self):

        tool = RunCommandTool(
            {
                "noisy": [
                    sys.executable,
                    "-c",
                    "import sys; sys.stderr.write('warning: heads up')",
                ]
            }
        )

        result = tool.execute("noisy")

        assert result.ok
        assert "heads up" in result.output

    def test_line_structure_survives(self):
        """
        Deliberately not `apps.py::_tail`, which collapses onto one line.

        A model reading a file listing needs the lines; collapsing them
        makes two filenames look like one.
        """

        tool = RunCommandTool(
            {
                "lines": [
                    sys.executable,
                    "-c",
                    "print('one'); print('two'); print('three')",
                ]
            }
        )

        output = tool.execute("lines").output

        assert "one\ntwo\nthree" in output

    def test_a_stray_carriage_return_does_not_reach_the_prompt(self):

        tool = RunCommandTool(
            {"crlf": [sys.executable, "-c", r"print('a\r'); print('b\r')"]}
        )

        assert "\r" not in tool.execute("crlf").output

    def test_long_output_is_truncated_and_the_truncation_is_announced(self):

        tool = RunCommandTool(
            {
                "loud": [
                    sys.executable,
                    "-c",
                    f"print('x' * {MAX_OUTPUT * 3})",
                ]
            }
        )

        result = tool.execute("loud")

        assert result.ok
        assert f"first {MAX_OUTPUT} characters" in result.output
        # Bounded, with room for the framing sentence and the note.
        assert len(result.output) < MAX_OUTPUT + 300


# ----------------------------------------------------------------------
# Refusals a caller can act on
# ----------------------------------------------------------------------


class TestBadCallsAreRefusedClearly:

    def test_an_undeclared_name_raises_rather_than_failing(self):
        """
        Matching `open_application`: this is not a command that failed, it
        is a request for something the owner never declared.
        """

        tool = RunCommandTool({"known": ["git", "--version"]})

        with pytest.raises(PermissionError) as raised:
            tool.execute("something_else")

        assert "something_else" in str(raised.value)

    def test_a_missing_value_is_named(self):

        tool = argv_tool("{value}")

        error = tool.execute("probe").error

        assert "no value for value" in error
        assert "Nothing was run" in error

    def test_a_misspelled_value_names_both_halves(self):
        """
        The phase 18.1 lesson, applied again.

        Reporting only the missing name sends the caller back to add
        `value` while still sending `valeu`, and it fails a second time for
        a reason it was never told.
        """

        tool = argv_tool("{value}")

        error = tool.execute("probe", {"valeu": "x"}).error

        assert "value" in error
        assert "valeu" in error
        assert "misspelled" in error

    def test_an_unexpected_value_on_a_command_with_no_slots_is_refused(self):

        tool = RunCommandTool({"plain": ["git", "--version"]})

        error = tool.execute("plain", {"extra": "x"}).error

        assert "does not take extra" in error
        assert "no values at all" in error

    @pytest.mark.parametrize(
        "value", [["a", "b"], {"a": 1}, True, False, None]
    )
    def test_a_value_that_is_not_text_or_a_number_is_refused(self, value):
        """
        A list has no single obvious spelling as one argv element, and a
        bool's spelling is almost never the flag the program wants.
        """

        tool = argv_tool("{value}")

        result = tool.execute("probe", {"value": value})

        assert not result.ok
        assert "must be text or a number" in result.error

    def test_a_null_character_is_refused_before_the_os_sees_it(self):

        tool = argv_tool("{value}")

        result = tool.execute("probe", {"value": "a\x00b"})

        assert not result.ok
        assert "null character" in result.error

    def test_values_must_be_a_mapping(self):

        tool = argv_tool("{value}")

        result = tool.execute("probe", ["value", "x"])

        assert not result.ok
        assert "must be a mapping" in result.error

    def test_no_values_at_all_is_fine_for_a_command_with_no_slots(self):

        tool = RunCommandTool({"plain": [sys.executable, "-c", "print(1)"]})

        assert tool.execute("plain").ok
        assert tool.execute("plain", None).ok
        assert tool.execute("plain", {}).ok

    def test_a_missing_program_is_refused_before_anything_is_spawned(self):

        tool = RunCommandTool({"ghost": ["definitely-not-a-program-xyz"]})

        result = tool.execute("ghost")

        assert not result.ok
        assert "was not found on this machine" in result.error
        assert "Nothing was run" in result.error

    def test_a_declared_directory_that_is_gone_is_a_refusal(self):
        """
        Not a fallback to Aura's own directory. `git status` run in the
        wrong repository answers confidently about the wrong thing, which
        is worse than not answering.
        """

        tool = RunCommandTool(
            {
                "elsewhere": {
                    "argv": [sys.executable, "-c", "print(1)"],
                    "cwd": "/definitely/not/here/xyz",
                }
            }
        )

        result = tool.execute("elsewhere")

        assert not result.ok
        assert "does not exist" in result.error
        assert "Nothing was run" in result.error

    def test_a_declared_directory_that_exists_is_used(self, tmp_path):

        tool = RunCommandTool(
            {
                "here": {
                    "argv": [sys.executable, "-c", "import os; print(os.getcwd())"],
                    "cwd": str(tmp_path),
                }
            }
        )

        result = tool.execute("here")

        assert result.ok
        assert os.path.realpath(str(tmp_path)) in os.path.realpath(
            result.output.split("\n", 1)[1].strip()
        )

    @pytest.mark.parametrize(
        "literal",
        [
            "{}",              # find . -exec rm {} \;
            "a{2,3}",          # grep -E
            "{name: .n}",      # jq
            "{k: v for k in x}",
            "--format={\"c\":1}",
        ],
    )
    def test_a_brace_the_owner_typed_reaches_the_program_unchanged(
        self, literal
    ):
        """
        These are ordinary program syntax, not misspelled slots.

        An earlier version refused any argv with a brace left in it after
        substitution, which broke every one of these. The braces are the
        owner's own text - the model supplied nothing - so there is nothing
        here for Section 24 to protect against, and refusing would be
        overriding the owner to guess at their intent.
        """

        tool = argv_tool(literal)

        assert reported_argv(tool.execute("probe")) == [literal]


# ----------------------------------------------------------------------
# Reading the owner's config
# ----------------------------------------------------------------------


class TestWhatTheOwnerWroteIsReadCarefully:

    def test_a_bare_list_is_accepted_as_shorthand(self):

        tool = RunCommandTool({"v": ["git", "--version"]})

        assert tool.available == ["v"]
        assert tool.commands["v"].argv == ("git", "--version")

    def test_a_full_mapping_is_read_in_full(self):

        tool = RunCommandTool(
            {
                "find": {
                    "argv": ["git", "grep", "--", "{pattern}"],
                    "description": "Search",
                    "parameters": {"pattern": "The text"},
                    "timeout": 12,
                    "cwd": "D:/AURA",
                }
            }
        )

        command = tool.commands["find"]

        assert command.description == "Search"
        assert command.parameters == {"pattern": "The text"}
        assert command.timeout == 12.0
        assert command.cwd == "D:/AURA"

    def test_a_command_line_string_is_refused_not_split(self):
        """
        Splitting it would be this module writing a command line out of
        text, which is the thing Section 24 is about - and the owner would
        have no way to see where the split landed.
        """

        assert _normalise({"v": "git --version"}) == {}
        assert _normalise({"v": {"argv": "git --version"}}) == {}

    @pytest.mark.parametrize(
        "entry", ["git --version", {"argv": "git --version"}]
    )
    def test_the_string_refusal_says_how_to_write_it_instead(
        self, entry, caplog
    ):
        """
        A mutation caught this: disabling the string branch still refuses
        the command, because a string is not a list either - but the owner
        is then told "it has no argv list to run", which is true and
        useless. `applications` accepts exactly this shape, so writing a
        command line here is the likely mistake and the message has to name
        the fix.
        """

        with caplog.at_level("WARNING"):
            _normalise({"v": entry})

        assert "not one string" in caplog.text
        assert '["git", "status"]' in caplog.text

    @pytest.mark.parametrize(
        "entry",
        [
            {"argv": []},
            {"argv": None},
            {},
            {"argv": ["  "]},
            {"argv": [["nested"]]},
            {"argv": [{"a": 1}]},
            {"argv": [True]},
            42,
            None,
        ],
    )
    def test_an_unusable_entry_is_dropped(self, entry):

        assert _normalise({"bad": entry}) == {}

    def test_a_slot_in_the_program_position_is_refused(self):
        """
        The model must never choose *what* runs, only what is passed to it.
        """

        assert _normalise({"bad": {"argv": ["{program}", "--help"]}}) == {}

    def test_a_non_mapping_section_is_dropped_whole(self):

        assert _normalise(["git", "status"]) == {}
        assert _normalise("git status") == {}
        assert _normalise(None) == {}
        assert _normalise({}) == {}

    def test_names_are_lowercased_and_stripped(self):

        tool = RunCommandTool({"  Repo_Status  ": ["git", "status"]})

        assert tool.available == ["repo_status"]
        assert tool.execute("REPO_STATUS") is not None

    def test_an_empty_name_is_dropped(self):

        assert _normalise({"   ": ["git", "status"]}) == {}

    def test_one_bad_command_does_not_take_the_good_ones_with_it(self):

        declared = _normalise(
            {
                "good": ["git", "--version"],
                "bad": "git status",
                "also_good": ["git", "status"],
            }
        )

        assert sorted(declared) == ["also_good", "good"]

    def test_malformed_parameters_cost_a_description_not_the_command(self):
        """
        `parameters` is documentation. What a command accepts is decided by
        its argv, so a broken description block must not remove a command
        the owner declared.
        """

        declared = _normalise(
            {"find": {"argv": ["git", "grep", "{p}"], "parameters": ["p"]}}
        )

        assert "find" in declared
        assert declared["find"].parameters == {}
        assert declared["find"].slots == ("p",)

    def test_an_unreadable_timeout_falls_back_rather_than_raising(self):

        declared = _normalise(
            {"v": {"argv": ["git", "--version"], "timeout": "soon"}}
        )

        assert declared["v"].timeout == DEFAULT_COMMAND_TIMEOUT

    def test_a_negative_timeout_falls_back(self):

        declared = _normalise(
            {"v": {"argv": ["git", "--version"], "timeout": -5}}
        )

        assert declared["v"].timeout == DEFAULT_COMMAND_TIMEOUT

    def test_zero_timeout_is_honoured_because_it_is_the_owners_call(self):
        """
        `tools/timeout.py` documents 0 as "no bound". Clamping it here
        would be the silent override Section 2 forbids.
        """

        declared = _normalise(
            {"v": {"argv": ["git", "--version"], "timeout": 0}}
        )

        assert declared["v"].timeout == 0


class TestSlotsComeFromTheArgv:

    def test_slots_are_derived_from_argv_not_from_parameters(self):
        """
        The argv is what actually gets filled in, so it is the authority.
        A `parameters` block that disagrees changes nothing that runs.
        """

        command = Command(
            name="x",
            argv=("prog", "{a}", "{b}"),
            parameters={"c": "not a slot"},
        )

        assert command.slots == ("a", "b")

    def test_slots_keep_argv_order_and_appear_once(self):

        command = Command(
            name="x", argv=("prog", "{b}", "{a}", "{b}", "--x={a}")
        )

        assert command.slots == ("b", "a")

    @pytest.mark.parametrize(
        "element", ["{}", "{1}", "{a-b}", "{ a }", "{a.b}", "plain"]
    )
    def test_a_brace_that_is_not_an_identifier_is_not_a_slot(self, element):
        """
        A program that legitimately wants a literal brace should not have
        it silently eaten.
        """

        assert Command(name="x", argv=("prog", element)).slots == ()

    def test_describe_lists_the_commands_and_their_slots(self):

        tool = RunCommandTool(
            {
                "find": {
                    "argv": ["git", "grep", "{pattern}"],
                    "description": "Search the project",
                },
                "plain": ["git", "--version"],
            }
        )

        described = tool.describe()

        assert "find" in described
        assert "Search the project" in described
        assert "values: pattern" in described
        assert "plain" in described

    def test_describe_says_so_when_nothing_is_declared(self):

        assert "no commands configured" in RunCommandTool({}).describe()


# ----------------------------------------------------------------------
# Warn, do not override (Section 2)
# ----------------------------------------------------------------------


class TestTheOwnerIsWarnedRatherThanOverridden:

    def test_a_shell_interpreter_with_a_slot_is_warned_about_not_refused(
        self, caplog
    ):
        """
        Section 2: the owner may mean it. It is the one declaration where
        the model's value reaches something whose job is to interpret text,
        so it is said as loudly as a warning can be said - and then
        allowed.
        """

        with caplog.at_level("WARNING"):
            declared = _normalise(
                {"shell": {"argv": ["cmd", "/c", "{text}"]}}
            )

        assert "shell" in declared

        logged = caplog.text.lower()

        assert "interprets whatever text" in logged
        assert "text" in logged

    @pytest.mark.parametrize(
        "program",
        ["cmd", "cmd.exe", "powershell", "pwsh", "bash", "sh", "wscript"],
    )
    def test_every_named_interpreter_is_recognised(self, program, caplog):

        with caplog.at_level("WARNING"):
            _normalise({"shell": {"argv": [program, "{text}"]}})

        assert "interprets whatever text" in caplog.text

    def test_an_interpreter_with_no_slot_is_not_warned_about(self, caplog):
        """
        Nothing the model supplies reaches it, so there is nothing to warn
        about - and a warning that fires when it need not is a warning
        people learn to ignore.
        """

        with caplog.at_level("WARNING"):
            _normalise({"shell": {"argv": ["cmd", "/c", "dir"]}})

        assert "interprets whatever text" not in caplog.text

    def test_an_interpreter_is_matched_by_stem_not_by_the_whole_path(
        self, caplog
    ):

        with caplog.at_level("WARNING"):
            _normalise(
                {"shell": {"argv": [r"C:\Windows\System32\cmd.exe", "{t}"]}}
            )

        assert "interprets whatever text" in caplog.text

    def test_an_undescribed_slot_is_warned_about(self, caplog):

        with caplog.at_level("WARNING"):
            _normalise({"find": {"argv": ["git", "grep", "{pattern}"]}})

        assert "has no description" in caplog.text

    def test_a_described_slot_is_not_warned_about(self, caplog):

        with caplog.at_level("WARNING"):
            _normalise(
                {
                    "find": {
                        "argv": ["git", "grep", "{pattern}"],
                        "parameters": {"pattern": "text to find"},
                    }
                }
            )

        assert "has no description" not in caplog.text

    def test_a_parameter_the_argv_never_uses_is_warned_about(self, caplog):

        with caplog.at_level("WARNING"):
            _normalise(
                {
                    "find": {
                        "argv": ["git", "status"],
                        "parameters": {"pattern": "never used"},
                    }
                }
            )

        assert "never used in the argv" in caplog.text

    @pytest.mark.parametrize(
        "element", ["{ pattern }", "{pattern }", "{my-pattern}", "{a.b}"]
    )
    def test_a_brace_that_looks_like_a_misspelled_value_is_warned_about(
        self, element, caplog
    ):
        """
        The owner meant a slot and will otherwise wonder why the program
        searched for the word "pattern" in curly braces.
        """

        with caplog.at_level("WARNING"):
            _normalise({"x": {"argv": ["prog", element]}})

        assert "meant to be a value" in caplog.text

    @pytest.mark.parametrize(
        "element", ["{}", "a{2,3}", "{name: .n}", "{1bad}"]
    )
    def test_ordinary_program_syntax_is_not_warned_about(
        self, element, caplog
    ):
        """
        A warning that fires on `find`, `grep -E` and `jq` is a warning
        people learn to scroll past.
        """

        with caplog.at_level("WARNING"):
            _normalise({"x": {"argv": ["prog", element]}})

        assert "meant to be a value" not in caplog.text

    def test_a_valid_slot_is_not_reported_as_a_misspelled_one(self):
        """
        The regression this guards: the near-slot pattern also matches a
        correct `{pattern}`, so checking it before substitution reported
        every properly written slot as broken.
        """

        import logging

        records: list[str] = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        from core.logger import logger as aura_logger

        handler = Capture()
        aura_logger.addHandler(handler)

        try:
            _normalise(
                {
                    "find": {
                        "argv": ["git", "grep", "{pattern}"],
                        "parameters": {"pattern": "text"},
                    }
                }
            )
        finally:
            aura_logger.removeHandler(handler)

        assert not any("meant to be a value" in line for line in records)

    def test_no_time_limit_is_warned_about(self, caplog):

        with caplog.at_level("WARNING"):
            _normalise({"v": {"argv": ["git", "--version"], "timeout": 0}})

        assert "no time limit" in caplog.text


# ----------------------------------------------------------------------
# Section 30: credentials do not travel downward
# ----------------------------------------------------------------------


class TestCredentialsAreWithheldFromTheChild:

    def test_the_provider_key_names_are_read_from_the_repository(self):
        """
        Imported rather than copied, so a provider added later is covered
        without an edit here. This asserts the import actually works - a
        silently failing one would leave the pattern sweep as the only
        defence.
        """

        from brain.router import PROVIDER_KEYS

        environment = {name: "x" for name in PROVIDER_KEYS.values() if name}

        withheld = set(_credential_names(environment))

        assert withheld == set(environment)

    def test_a_future_provider_key_is_covered_without_an_edit_here(
        self, monkeypatch
    ):
        """
        The reason the names are imported instead of listed.

        A mutation caught this: deleting the import changes nothing today,
        because every name in `PROVIDER_KEYS` happens to contain "KEY" and
        the pattern sweep catches it anyway. So the mechanism that actually
        matters - a provider added later being covered without an edit to
        this module - was verified by nothing. This name is one the pattern
        would miss, so only the import can withhold it.
        """

        from brain import router

        monkeypatch.setitem(router.PROVIDER_KEYS, "newprovider", "ACME_SESAME")

        assert _credential_names({"ACME_SESAME": "x"}) == ["ACME_SESAME"]

    def test_the_secret_variable_names_are_read_from_the_repository(self):

        from core.credentials import SECRET_ENV_VARS

        environment = {name: "x" for name in SECRET_ENV_VARS}

        assert set(_credential_names(environment)) == set(environment)

    @pytest.mark.parametrize(
        "name",
        [
            "GEMINI_API_KEY",
            "OPENROUTER_API_KEY",
            "AURA_SECRET_KEY",
            "AURA_SERVER_AUTH_TOKEN",
            "GITHUB_TOKEN",
            "AWS_SECRET_ACCESS_KEY",
            "DB_PASSWORD",
            "my_credential_store",
            "anthropic_auth_token",
        ],
    )
    def test_a_credential_shaped_name_is_withheld(self, name):

        assert _credential_names({name: "x"}) == [name]

    @pytest.mark.parametrize(
        "name",
        ["PATH", "SystemRoot", "TEMP", "PATHEXT", "COMSPEC", "HOME", "LANG"],
    )
    def test_the_environment_a_command_needs_is_kept(self, name):

        assert _credential_names({name: "x"}) == []

    def test_the_ssh_agent_socket_is_kept_with_its_reason(self):
        """
        It matches the pattern and is not a secret: it is the path of a
        socket, not the contents of one, and git over SSH stops working
        without it.
        """

        assert _credential_names({"SSH_AUTH_SOCK": "/tmp/s"}) == []

    def test_the_child_environment_is_the_real_one_minus_the_secrets(
        self, monkeypatch
    ):

        monkeypatch.setenv("GEMINI_API_KEY", "must-not-appear")
        monkeypatch.setenv("SOMETHING_ORDINARY", "keep-me")

        environment = _child_environment()

        assert "GEMINI_API_KEY" not in environment
        assert environment.get("SOMETHING_ORDINARY") == "keep-me"
        assert "must-not-appear" not in environment.values()

    def test_a_command_that_prints_its_environment_cannot_print_a_key(
        self, monkeypatch
    ):
        """
        The end-to-end version, which is the one that matters.

        Section 30 says a key must never appear in chat history, and this
        tool's output goes into a prompt. `core/credentials.py` puts stored
        keys into `os.environ` deliberately, so without the scrub a single
        declared command would be enough to leak one.
        """

        secret = "sk-test-must-never-be-printed"

        monkeypatch.setenv("GEMINI_API_KEY", secret)
        monkeypatch.setenv("AURA_SERVER_AUTH_TOKEN", secret)
        monkeypatch.setenv("SOMETHING_ORDINARY", "keep-me")

        tool = RunCommandTool({"env": [sys.executable, "-c", ENV_REPORT]})

        result = tool.execute("env")

        assert result.ok, result.error
        assert secret not in result.output

        # Asked from inside the child, which is the only place that can
        # answer it. Nothing credential-shaped arrived under any name...
        assert "CREDENTIALS: 0 |" in result.output
        # ...and the value did not arrive under some other name either.
        assert "LEAK: False" in result.output

        # And it is a real environment, not an empty one: a command that
        # cannot find its own PATH would fail for a confusing reason.
        assert "PATH: True" in result.output
        assert "ORDINARY: keep-me" in result.output


# ----------------------------------------------------------------------
# The tool's place in the framework
# ----------------------------------------------------------------------


class TestItFitsTheExistingBoundary:

    def test_it_satisfies_the_protocol_without_widening_it(self):

        assert isinstance(RunCommandTool({}), ToolProtocol)

    def test_it_is_dangerous(self):

        assert RunCommandTool({}).risk is ToolRisk.DANGEROUS

    def test_it_offers_no_verify_and_that_is_deliberate(self):
        """
        The exit status is the postcondition, read back from the world.
        Re-asking afterwards would mean re-running the command, which
        doubles the side effects to learn nothing new - and this tool does
        not know what any given command was supposed to change.
        """

        assert not hasattr(RunCommandTool({}), "verify")

    def test_only_the_name_is_required(self):

        assert RunCommandTool({}).required_parameters() == ["name"]

    def test_the_executor_refuses_it_without_a_human(self):
        """
        Gate 4 defaults to refusal, and DANGEROUS is not auto approved in
        the shipped config. No confirm callback means no.
        """

        registry = ToolRegistry()
        registry.register(RunCommandTool({"v": [sys.executable, "-c", "print(1)"]}))

        executor = ToolExecutor(
            registry=registry,
            policy=ToolPolicy(
                enabled=True, allowed=["run_command"], auto_approve=["safe"]
            ),
        )

        result = executor.execute("run_command", {"name": "v"})

        assert not result.ok
        assert "permission denied" in result.error

    def test_the_executor_runs_it_when_a_human_says_yes(self):

        registry = ToolRegistry()
        registry.register(
            RunCommandTool({"v": [sys.executable, "-c", "print('ran')"]})
        )

        executor = ToolExecutor(
            registry=registry,
            policy=ToolPolicy(
                enabled=True, allowed=["run_command"], auto_approve=["safe"]
            ),
            confirm=lambda tool, arguments: True,
        )

        result = executor.execute("run_command", {"name": "v"})

        assert result.ok, result.error
        assert "ran" in result.output

    def test_the_tools_own_bound_sits_outside_the_commands_bound(self):
        """
        The executor bounds `execute` on a daemon thread it cannot kill, so
        that bound has to be the outer one. Inside, the thread would be
        abandoned mid-kill and the process it was killing would survive
        with nobody watching.
        """

        tool = RunCommandTool(
            {
                "slow": {"argv": ["git", "--version"], "timeout": 40.0},
                "quick": {"argv": ["git", "--version"], "timeout": 1.0},
            }
        )

        assert tool.timeout > 40.0

    def test_an_unbounded_command_makes_the_tool_unbounded_too(self):

        tool = RunCommandTool(
            {"forever": {"argv": ["git", "--version"], "timeout": 0}}
        )

        assert tool.timeout == 0

    def test_with_no_commands_the_bound_is_the_default_not_an_error(self):

        assert RunCommandTool({}).timeout > DEFAULT_COMMAND_TIMEOUT


class TestTheFactoryGate:

    def test_no_declared_commands_means_no_tool_at_all(self):
        """
        A tool that refuses every name it is given advertises a capability
        that does not exist, and the model spends turns discovering that.
        Absence is the clearer answer.
        """

        assert "run_command" not in build_registry({}).names()
        assert "run_command" not in build_registry({"commands": {}}).names()
        assert "run_command" not in build_registry({"commands": []}).names()

    def test_a_declared_command_registers_the_tool(self):

        registry = build_registry({"commands": {"v": ["git", "--version"]}})

        assert "run_command" in registry.names()

    def test_declarations_that_all_fail_validation_register_nothing(self):
        """
        The per-command warnings say why each was dropped. This says what
        it cost, which is not something they add up to on their own.
        """

        registry = build_registry({"commands": {"v": "git --version"}})

        assert "run_command" not in registry.names()

    def test_the_cost_of_dropping_everything_is_logged(self, caplog):

        with caplog.at_level("WARNING"):
            build_registry({"commands": {"v": "git --version"}})

        assert "run_command not registered" in caplog.text

    def test_declaring_nothing_is_not_reported_as_a_failure(self, caplog):
        """
        A mutation caught this: removing the `if commands:` gate still
        registers no tool, because a tool with no usable commands is
        rejected by the check below it. What the gate actually decides is
        whether the owner is told "none of the 0 declared command(s) could
        be used" - a warning about a problem they do not have, on every
        startup, in a log where a real one has to stand out.
        """

        with caplog.at_level("WARNING"):
            build_registry({"commands": {}})
            build_registry({})

        assert "run_command not registered" not in caplog.text


class TestTheShippedConfigGrantsNothing:
    """
    Section 2 cuts both ways: the owner must be able to enable this, and
    must not find it already enabled.

    These read `config.yaml` from disk rather than through the loader, so a
    defaulting bug in the loader cannot hide a silent enable.
    """

    @pytest.fixture
    def shipped(self) -> dict:

        with io.open("config.yaml", encoding="utf-8") as handle:
            return yaml.safe_load(handle)["tools"]

    def test_no_command_is_declared(self, shipped):

        assert shipped.get("commands") == {}

    def test_run_command_is_not_allowed(self, shipped):

        assert "run_command" not in (shipped.get("allowed") or [])

    def test_dangerous_is_not_auto_approved(self, shipped):

        assert "dangerous" not in (shipped.get("auto_approve") or [])

    def test_the_shipped_config_registers_no_command_tool(self, shipped):

        assert "run_command" not in build_registry(shipped).names()

    def test_commands_cannot_be_declared_over_the_settings_api(self):
        """
        The sharpest version of the capability rule. A settable `commands`
        would let anything holding the bearer token declare
        `["cmd", "/c", "{x}"]` and then fill in `{x}` - arbitrary shell
        execution reached through the settings API instead of through the
        tool boundary.
        """

        from core.settings_store import ALLOWED

        assert "tools.commands" not in ALLOWED
        assert not any(
            str(path).startswith("tools.commands") for path in ALLOWED
        )

    def test_the_documented_shape_in_the_config_comment_actually_works(self):
        """
        The example in `config.yaml` is what the owner will copy, so it has
        to survive `_normalise` rather than merely read well.
        """

        example = {
            "repo_status": {
                "argv": ["git", "status", "--short"],
                "description": "Which files in the project have changed",
                "cwd": ".",
            },
            "find_text": {
                "argv": ["git", "grep", "-n", "--", "{pattern}"],
                "description": "Search the project for a piece of text",
                "parameters": {"pattern": "The text to look for"},
                "timeout": 20,
            },
        }

        declared = _normalise(example)

        assert sorted(declared) == ["find_text", "repo_status"]
        assert declared["find_text"].slots == ("pattern",)
        assert declared["repo_status"].slots == ()
