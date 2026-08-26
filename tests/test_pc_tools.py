"""
Windows / PC agent tests (section 24).

Section 24 asks for a permission-aware tool layer over the owner's
machine, and says the thing to avoid is "arbitrary LLM text direct
unrestricted shell execution without a controlled tool boundary". The
boundary already existed - five gates in ToolExecutor, tested in
test_tools.py - so most of what follows tests the two things that are new:

    * that each tool tells the truth about what it read, including when
      it read nothing, and
    * that `focus_window` proves its postcondition instead of trusting
      that a call which returned did what it asked.

That second one is section 11 exactly. `SetForegroundWindow` returns zero
and changes nothing under Windows' foreground lock, which from the calling
side is indistinguishable from success - so "the command executed without
throwing" is precisely the evidence section 11 forbids resting on, and
there is a test below for the case where execute succeeds and verify
catches it.

Every test drives a mock source. The real readings are exercised too, but
only for shape - a test that asserted this machine has sixteen processors
would be a test about the machine.
"""

import os

import pytest

from tools.base import ToolProtocol, ToolRisk
from tools.builtins.desktop import (
    FocusWindowTool,
    ListWindowsTool,
    MockWindowSource,
    WindowInfo,
    WindowsWindowSource,
    WindowSource,
    _as_pid,
    default_window_source,
)
from tools.builtins.system import (
    MAX_PROCESSES,
    ListProcessesTool,
    MockProcessSource,
    MockSystemFacts,
    ProcessInfo,
    ProcessSource,
    SystemFacts,
    SystemInformationTool,
    _ceiling,
    _parse_tasklist,
    default_process_source,
)
from tools.executor import ToolExecutor
from tools.factory import build_registry
from tools.registry import ToolRegistry


ON_WINDOWS = os.name == "nt"

windows_only = pytest.mark.skipif(
    not ON_WINDOWS, reason="reads this machine through the Windows API"
)


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------

@pytest.fixture
def desktop() -> MockWindowSource:
    """A small desktop: one window in front, one minimised behind it."""

    return MockWindowSource([
        WindowInfo(handle=1, title="Editor - main.py", pid=100,
                   foreground=True),
        WindowInfo(handle=2, title="Browser", pid=200, minimised=True),
    ])


@pytest.fixture
def twins() -> MockWindowSource:
    """Two windows with byte-identical titles, which this machine had."""

    return MockWindowSource([
        WindowInfo(handle=1, title="Settings", pid=100, foreground=True),
        WindowInfo(handle=2, title="Settings", pid=200),
    ])


# ----------------------------------------------------------------------
# system_information
# ----------------------------------------------------------------------

def test_system_information_reads_through_its_source():

    source = MockSystemFacts()

    tool = SystemInformationTool(source=source)

    result = tool.execute()

    assert result.ok
    assert source.reads == 1
    assert "TestOS" in result.output


def test_system_information_renders_every_fact_it_has():

    tool = SystemInformationTool(source=MockSystemFacts(SystemFacts(
        system="Windows",
        release="11",
        version="10.0.26200",
        machine="AMD64",
        processors=16,
        memory_total_gb=15.7,
        memory_available_gb=4.2,
        disk_total_gb=350.0,
        disk_free_gb=337.9,
        disk_path="D:/AURA",
        uptime_hours=300.4,
        python_version="3.11.15",
    )))

    output = tool.execute().output

    assert "Windows 11" in output
    assert "10.0.26200" in output
    assert "AMD64" in output
    assert "16" in output
    assert "15.7 GB total" in output
    assert "4.2 GB available" in output
    assert "337.9 GB free" in output
    assert "300.4 hours" in output
    assert "3.11.15" in output


def test_a_fact_that_could_not_be_read_is_omitted_not_zeroed():
    """
    The difference between "could not be read" and "is zero".

    A machine whose memory could not be read must not be described as
    having no memory. Every optional field is absent by emptiness, and
    `render` leaves the line out rather than printing a confident 0.0.
    """

    tool = SystemInformationTool(source=MockSystemFacts(SystemFacts(
        system="Linux",
        release="6.1",
    )))

    output = tool.execute().output

    assert "Linux 6.1" in output
    assert "memory" not in output
    assert "disk" not in output
    assert "uptime" not in output
    assert "0.0" not in output


def test_a_reading_with_nothing_in_it_says_so():

    tool = SystemInformationTool(source=MockSystemFacts(SystemFacts()))

    result = tool.execute()

    assert result.ok
    assert result.output == "nothing about this system could be read"


def test_a_source_that_raises_is_a_failure_not_an_empty_description():
    """
    A failed reading is reported as one.

    The tempting alternative - catch the error and return empty facts -
    produces "nothing about this system could be read" for a bug, which
    reads like an answer about the machine rather than an admission that
    the tool broke.
    """

    class Broken:
        def read(self):
            raise OSError("no")

    result = SystemInformationTool(source=Broken()).execute()

    assert not result.ok
    assert "OSError" in result.error
    assert "system information" in result.error


def test_system_information_is_sensitive_not_safe():
    """
    It changes nothing, and it is still not SAFE.

    Its output is written into a prompt, so it leaves the machine. That is
    a disclosure, and the owner should have to permit it deliberately
    rather than get it under the auto-approved SAFE tier.
    """

    assert SystemInformationTool().risk is ToolRisk.SENSITIVE


def test_system_information_takes_no_arguments():

    assert SystemInformationTool().parameters == ()
    assert SystemInformationTool().required_parameters() == []


def test_the_real_reading_does_not_name_the_owner_or_their_machine():
    """
    Section 30's habit applied to a tool that is not about credentials.

    Hostname, username and the home directory path are all trivially
    readable here and none of them is reported, because this output goes
    into a prompt and therefore leaves the machine. The owner already
    knows which computer they are sitting at, so there is nothing to gain
    against a disclosure that cannot be taken back.

    Asserted against the real reading rather than a mock, because a mock
    cannot leak a hostname the mock does not have. `platform.node()` is
    the one that would slip in by accident - it sits directly beside
    `platform.system()` in the same module.
    """

    import getpass
    import platform

    output = SystemInformationTool().execute().output.lower()

    hostname = (platform.node() or "").strip().lower()

    if hostname:
        assert hostname not in output

    try:
        user = (getpass.getuser() or "").strip().lower()
    except Exception:
        user = ""

    if user and len(user) > 2:
        assert user not in output

    home = (os.path.expanduser("~") or "").strip().lower()

    if home:
        assert home not in output


def test_the_real_reading_names_this_platform():
    """
    Shape, not content: the real source answers something about this
    machine, whatever machine that is.
    """

    import platform

    result = SystemInformationTool().execute()

    assert result.ok
    assert platform.system().lower() in result.output.lower()
    assert platform.python_version() in result.output


# ----------------------------------------------------------------------
# list_processes
# ----------------------------------------------------------------------

def test_processes_are_listed_largest_first():

    source = MockProcessSource([
        ProcessInfo(pid=1, name="small.exe", memory_kb=100),
        ProcessInfo(pid=2, name="huge.exe", memory_kb=900_000),
        ProcessInfo(pid=3, name="medium.exe", memory_kb=5_000),
    ])

    output = ListProcessesTool(source=source).execute().output

    assert output.index("huge.exe") < output.index("medium.exe")
    assert output.index("medium.exe") < output.index("small.exe")


def test_processes_can_be_filtered_by_name():

    source = MockProcessSource([
        ProcessInfo(pid=1, name="python.exe", memory_kb=10),
        ProcessInfo(pid=2, name="chrome.exe", memory_kb=20),
    ])

    result = ListProcessesTool(source=source).execute(name="PYTHON")

    assert result.ok
    assert "python.exe" in result.output
    assert "chrome.exe" not in result.output


def test_a_filter_that_matches_nothing_is_an_answer_not_a_failure():
    """
    "Nothing called that is running" is a true and useful answer.

    Distinct from the unfiltered empty case below, which is not.
    """

    source = MockProcessSource([ProcessInfo(pid=1, name="python.exe")])

    result = ListProcessesTool(source=source).execute(name="nosuchthing")

    assert result.ok
    assert "nosuchthing" in result.output


def test_an_empty_listing_with_no_filter_is_a_failure():
    """
    Every operating system has processes.

    So an empty unfiltered listing cannot mean "nothing is running" - it
    means the reading failed, and reporting it as a successful empty list
    would be a wrong answer where a missing one was available.
    """

    result = ListProcessesTool(source=MockProcessSource([])).execute()

    assert not result.ok
    assert "no processes could be read" in result.error


def test_a_truncated_listing_says_that_it_was_truncated():
    """
    A silently shortened list reads as a complete one.
    """

    source = MockProcessSource([
        ProcessInfo(pid=index, name=f"p{index}.exe", memory_kb=index)
        for index in range(1, MAX_PROCESSES + 15)
    ])

    output = ListProcessesTool(source=source).execute().output

    assert "more not listed" in output
    assert "14 more" in output


@pytest.mark.parametrize(
    "limit, expected",
    [
        (3, 3),
        ("3", 3),
        (None, MAX_PROCESSES),
        (0, MAX_PROCESSES),
        (-5, MAX_PROCESSES),
        ("lots", MAX_PROCESSES),
        (True, MAX_PROCESSES),
        (MAX_PROCESSES + 100, MAX_PROCESSES),
    ],
)
def test_the_limit_is_honoured_and_cannot_be_raised(limit, expected):
    """
    A caller may ask for fewer rows and may not ask for more.

    The ceiling exists to protect the prompt, so it is not the caller's to
    move. An unreadable limit is a request for the default rather than an
    error: the argument is optional, and failing a whole call over a
    malformed optional argument spends a turn teaching the model nothing.
    """

    source = MockProcessSource([
        ProcessInfo(pid=index, name=f"p{index}.exe", memory_kb=index)
        for index in range(1, MAX_PROCESSES + 50)
    ])

    output = ListProcessesTool(source=source).execute(limit=limit).output

    named = [line for line in output.splitlines() if "not listed" not in line]

    assert len(named) == expected


def test_ceiling_never_exceeds_the_maximum():

    for value in (1, 5, MAX_PROCESSES, MAX_PROCESSES + 1, 10_000):
        assert _ceiling(value) <= MAX_PROCESSES


def test_a_source_that_raises_is_reported_as_a_failed_reading():

    class Broken:
        def processes(self):
            raise PermissionError("denied")

    result = ListProcessesTool(source=Broken()).execute()

    assert not result.ok
    assert "PermissionError" in result.error


def test_list_processes_is_sensitive():

    assert ListProcessesTool(source=MockProcessSource()).risk is (
        ToolRisk.SENSITIVE
    )


def test_a_process_with_no_memory_reading_still_renders():

    output = ListProcessesTool(
        source=MockProcessSource([ProcessInfo(pid=7, name="x.exe")])
    ).execute().output

    assert "x.exe" in output
    assert "pid 7" in output
    assert "KB" not in output


# ----------------------------------------------------------------------
# tasklist parsing
# ----------------------------------------------------------------------

def test_tasklist_rows_become_processes():

    text = (
        '"chrome.exe","1234","Console","1","123,456 K"\n'
        '"python.exe","5678","Console","1","12,000 K"\n'
    )

    found = _parse_tasklist(text)

    assert [process.name for process in found] == ["chrome.exe", "python.exe"]
    assert [process.pid for process in found] == [1234, 5678]
    assert found[0].memory_kb == 123456
    assert found[1].memory_kb == 12000


def test_a_localised_memory_column_is_read_digit_by_digit():
    """
    `tasklist` formats the memory cell for the machine's locale.

    A German or Vietnamese Windows prints `123.456 K`, and `int()` on
    either form raises. Every non-digit is dropped instead, which is
    correct for every separator any locale uses because none of them is a
    decimal point in this column - tasklist reports whole kilobytes.
    """

    for cell in ("123,456 K", "123.456 K", "123 456 K", "123'456 K",
                 "123456 K"):

        found = _parse_tasklist(f'"a.exe","1","Console","1","{cell}"')

        assert found[0].memory_kb == 123456, cell


def test_pid_zero_is_a_real_process_and_survives_parsing():
    """
    Windows reports "System Idle Process" at pid 0, so the pid column being
    zero cannot be used as a "this row is not a process" signal. What the
    parser rejects is a pid that is not a *number* - which is what a header
    row has.
    """

    found = _parse_tasklist('"System Idle Process","0","Console","1","8 K"')

    assert [process.pid for process in found] == [0]
    assert found[0].name == "System Idle Process"


def test_a_header_row_is_not_a_process():
    """
    `/nh` should suppress it. If it ever does not, a row whose pid column
    is not a number is skipped rather than reported as a process called
    "Image Name".
    """

    text = (
        '"Image Name","PID","Session Name","Session#","Mem Usage"\n'
        '"chrome.exe","1234","Console","1","123,456 K"\n'
    )

    found = _parse_tasklist(text)

    assert [process.name for process in found] == ["chrome.exe"]


def test_short_and_empty_rows_are_skipped():

    text = '\n"only one column"\n"a.exe","2"\n\n'

    found = _parse_tasklist(text)

    assert [process.pid for process in found] == [2]
    assert found[0].memory_kb == 0


@windows_only
def test_the_real_process_source_reads_this_machine():
    """
    Shape only: this machine is running something, and one of those
    things is the interpreter running this test.
    """

    source = default_process_source()

    assert source is not None

    found = source.processes()

    assert len(found) > 5
    assert any("python" in process.name.lower() for process in found)

    # Not `> 0`. Windows really does report a process at pid 0 - "System
    # Idle Process" - and this assertion caught the parser being right
    # about a row a positive-pid assumption would have thrown away.
    assert all(process.pid >= 0 for process in found)
    assert all(process.name.strip() for process in found)


# ----------------------------------------------------------------------
# list_windows
# ----------------------------------------------------------------------

def test_the_foreground_window_is_named_first(desktop):
    """
    "What is the user looking at" is the question this answers most of the
    time, and burying it in an alphabetical list makes the model hunt.
    """

    source = MockWindowSource([
        WindowInfo(handle=1, title="Aardvark", pid=1),
        WindowInfo(handle=2, title="Zebra", pid=2, foreground=True),
    ])

    lines = ListWindowsTool(source=source).execute().output.splitlines()

    assert lines[0].startswith("Zebra")
    assert "in front" in lines[0]


def test_a_minimised_window_is_marked_as_minimised(desktop):

    output = ListWindowsTool(source=desktop).execute().output

    assert "Browser" in output
    assert "minimised" in output


def test_windows_can_be_filtered_by_title(desktop):

    result = ListWindowsTool(source=desktop).execute(title="BROWSER")

    assert result.ok
    assert "Browser" in result.output
    assert "Editor" not in result.output


def test_a_window_filter_that_matches_nothing_is_an_answer(desktop):

    result = ListWindowsTool(source=desktop).execute(title="nosuchwindow")

    assert result.ok
    assert "nosuchwindow" in result.output


def test_an_empty_desktop_is_a_success_unlike_an_empty_process_list():
    """
    The one place these two tools deliberately disagree.

    Every OS has processes, so an empty process listing means the reading
    broke. A session genuinely can have no visible titled window - a
    freshly booted desktop with everything closed, or a disconnected
    remote session - so calling that a failure would report a broken tool
    on a working machine.
    """

    windows = ListWindowsTool(source=MockWindowSource([])).execute()
    processes = ListProcessesTool(source=MockProcessSource([])).execute()

    assert windows.ok
    assert "no windows are open" in windows.output

    assert not processes.ok


def test_a_window_source_that_raises_is_a_failed_reading():

    class Broken:
        def windows(self):
            raise OSError("nope")

        def focus(self, handle):
            return False

    result = ListWindowsTool(source=Broken()).execute()

    assert not result.ok
    assert "OSError" in result.error


def test_a_long_window_list_says_it_was_truncated():

    from tools.builtins.desktop import MAX_WINDOWS

    source = MockWindowSource([
        WindowInfo(handle=index, title=f"Window {index:03d}", pid=index)
        for index in range(MAX_WINDOWS + 5)
    ])

    output = ListWindowsTool(source=source).execute().output

    assert "5 more not listed" in output


def test_list_windows_is_sensitive_and_focus_is_dangerous():
    """
    Reading the desktop discloses; changing it acts. Different tiers.
    """

    assert ListWindowsTool(source=MockWindowSource()).risk is (
        ToolRisk.SENSITIVE
    )
    assert FocusWindowTool(source=MockWindowSource()).risk is (
        ToolRisk.DANGEROUS
    )


def test_window_handles_are_never_shown_to_a_caller():
    """
    A handle is a number that means nothing to a reader and goes stale the
    moment the window closes. The pid is what a caller can act on, so the
    pid is what gets printed.
    """

    source = MockWindowSource([
        WindowInfo(handle=987654321, title="Editor", pid=42),
    ])

    output = ListWindowsTool(source=source).execute().output

    assert "987654321" not in output
    assert "42" in output


# ----------------------------------------------------------------------
# focus_window: matching
# ----------------------------------------------------------------------

def test_focusing_asks_the_source_for_the_matching_handle(desktop):

    result = FocusWindowTool(source=desktop).execute(title="browser")

    assert result.ok
    assert desktop.focused == [2]


def test_focusing_does_not_claim_the_window_arrived(desktop):
    """
    Wording, and it is load-bearing.

    At the end of `execute` all that is known is that user32 accepted the
    request. `verify` is what turns that into a claim, so `execute` says
    "asked for" and nothing stronger.
    """

    output = FocusWindowTool(source=desktop).execute(title="browser").output

    assert "asked for" in output
    assert "brought" not in output


def test_a_title_matching_nothing_is_refused(desktop):

    result = FocusWindowTool(source=desktop).execute(title="nosuchwindow")

    assert not result.ok
    assert "no open window matches" in result.error
    assert desktop.focused == []


def test_an_empty_title_is_refused(desktop):

    result = FocusWindowTool(source=desktop).execute(title="   ")

    assert not result.ok
    assert "title is required" in result.error
    assert desktop.focused == []


def test_an_ambiguous_title_is_refused_rather_than_guessed(twins):
    """
    Picking the first of two matches would look like it worked and would
    put the wrong window in front roughly half the time.
    """

    result = FocusWindowTool(source=twins).execute(title="Settings")

    assert not result.ok
    assert twins.focused == []
    assert "matches 2 windows" in result.error


def test_the_ambiguous_refusal_gives_advice_that_can_be_followed():
    """
    The bug a real desktop found.

    This machine had two windows both titled exactly `Settings`. The first
    version of this refusal said "name one of them more precisely", which
    is impossible advice - no title is more precise than an exact one. The
    pid distinguishes them, is in every line `list_windows` prints, and is
    therefore something a caller can actually supply.
    """

    source = MockWindowSource([
        WindowInfo(handle=1, title="Settings", pid=100, foreground=True),
        WindowInfo(handle=2, title="Settings", pid=200),
    ])

    error = FocusWindowTool(source=source).execute(title="Settings").error

    assert "pid 100" in error
    assert "pid 200" in error
    assert "more precisely" not in error


def test_a_pid_resolves_an_otherwise_ambiguous_title(twins):

    result = FocusWindowTool(source=twins).execute(title="Settings", pid=200)

    assert result.ok
    assert twins.focused == [2]


def test_a_pid_that_matches_no_window_with_that_title_is_refused(twins):

    result = FocusWindowTool(source=twins).execute(title="Settings", pid=999)

    assert not result.ok
    assert "999" in result.error
    assert twins.focused == []


def test_a_pid_arriving_as_a_string_still_works(twins):
    """
    A model writes JSON, and JSON numbers arrive as whatever the model
    wrote. Coerced rather than rejected.
    """

    result = FocusWindowTool(source=twins).execute(title="Settings", pid="200")

    assert result.ok
    assert twins.focused == [2]


@pytest.mark.parametrize(
    "value, expected",
    [(0, 0), (-1, 0), (None, 0), (True, 0), (False, 0),
     ("21132", 21132), ("abc", 0), ([1], 0), (3.7, 3)],
)
def test_as_pid_treats_a_bad_value_as_no_value(value, expected):
    """
    `True` is excluded explicitly, because `int(True)` is 1 and pid 1 is a
    real process on every OS that has one.
    """

    assert _as_pid(value) == expected


def test_a_source_that_refuses_outright_reports_that_nothing_changed(
    desktop,
):
    """
    user32 returning false is a real refusal, and there is nothing for the
    postcondition to wait for - so this fails immediately rather than
    spending FOCUS_TIMEOUT confirming a switch that was never accepted.
    """

    refusing = MockWindowSource(
        [WindowInfo(handle=2, title="Browser", pid=200)], accept=False
    )

    result = FocusWindowTool(source=refusing).execute(title="browser")

    assert not result.ok
    assert "refused" in result.error
    assert "Nothing changed" in result.error


def test_a_raising_source_during_matching_is_a_failure():

    class Broken:
        def windows(self):
            raise OSError("desktop gone")

        def focus(self, handle):
            raise AssertionError("must not be reached")

    result = FocusWindowTool(source=Broken()).execute(title="anything")

    assert not result.ok
    assert "OSError" in result.error


# ----------------------------------------------------------------------
# focus_window: the postcondition (section 11)
# ----------------------------------------------------------------------

def test_verify_confirms_a_window_that_came_forward(desktop):

    tool = FocusWindowTool(source=desktop)

    assert tool.execute(title="browser").ok

    verdict = tool.verify(title="browser")

    assert verdict is not None
    assert verdict.ok
    assert "in front" in verdict.output


def test_verify_catches_a_focus_that_was_accepted_and_did_nothing():
    """
    Section 11, in the one case that matters.

    `SetForegroundWindow` returns zero *and changes nothing* under
    Windows' foreground lock - a process that does not own the current
    foreground window is refused, and the refusal is indistinguishable
    from success at the call site. `honour_focus=False` is exactly that
    machine: the call is accepted, the desktop does not move.

    So `execute` succeeds and `verify` is the only thing standing between
    that and a confident lie.
    """

    stuck = MockWindowSource([
        WindowInfo(handle=1, title="Editor", pid=100, foreground=True),
        WindowInfo(handle=2, title="Browser", pid=200),
    ], honour_focus=False)

    tool = FocusWindowTool(source=stuck)

    assert tool.execute(title="browser").ok          # accepted...

    verdict = tool.verify(title="browser")

    assert not verdict.ok                            # ...and a lie.
    assert "did not come to the front" in verdict.error
    assert "Editor" in verdict.error


def test_the_executor_downgrades_a_focus_that_failed_verification():
    """
    The whole point, end to end through the framework.

    Not asserted on the tool but on the ToolExecutor, because a
    postcondition the tool checks and the framework ignores protects
    nobody. Gate 4 has to be satisfied for this to run at all, which is
    why the confirmation callback is here.
    """

    stuck = MockWindowSource([
        WindowInfo(handle=1, title="Editor", pid=100, foreground=True),
        WindowInfo(handle=2, title="Browser", pid=200),
    ], honour_focus=False)

    registry = ToolRegistry()
    registry.register(FocusWindowTool(source=stuck))

    executor = ToolExecutor(
        registry=registry,
        policy=None,
        confirm=lambda tool, arguments: True,
    )
    executor.policy.enabled = True
    executor.policy.allowed = {"focus_window"}

    result = executor.execute("focus_window", {"title": "browser"})

    assert not result.ok
    assert "did not come to the front" in result.error


def test_verify_checks_the_pid_when_one_was_given(twins):
    """
    The reason the argument exists.

    Two windows share a title, so a title comparison would happily confirm
    that the wrong one of the two came forward. Here pid 100 is in front
    and pid 200 is what was asked for.
    """

    tool = FocusWindowTool(source=twins)

    by_title_only = tool.verify(title="Settings")

    assert by_title_only.ok                    # would be a false pass

    with_pid = tool.verify(title="Settings", pid=200)

    assert not with_pid.ok
    assert "pid 200" in with_pid.error
    assert "pid 100" in with_pid.error


def test_verify_asserts_nothing_when_the_desktop_cannot_be_read():
    """
    None, not a failure.

    The executor reads None as "no postcondition offered", and an
    unreadable desktop genuinely offers none. Reporting a failure here
    would blame the focus for a broken enumeration - which is a different
    bug, in a different place, and would send whoever reads the message
    looking in the wrong one.
    """

    class Unreadable:
        def __init__(self):
            self.calls = 0

        def windows(self):
            self.calls += 1
            if self.calls == 1:
                return [WindowInfo(handle=2, title="Browser", pid=200)]
            raise OSError("desktop gone")

        def focus(self, handle):
            return True

    tool = FocusWindowTool(source=Unreadable())

    assert tool.execute(title="browser").ok

    assert tool.verify(title="browser") is None


def test_verify_asserts_nothing_on_an_empty_desktop():

    tool = FocusWindowTool(source=MockWindowSource([]))

    assert tool.verify(title="anything") is None


def test_verify_accepts_exactly_the_arguments_execute_does():
    """
    A structural guard, not a behaviour test.

    The executor calls `verify(**arguments)` with the same dict it passed
    to `execute`, so a verify whose signature has drifted raises TypeError,
    and `_verified` fails closed on a raise - which would turn every
    successful focus into "ran but could not be verified". A signature
    mismatch is therefore silent until someone focuses a window, and this
    catches it at import time instead.
    """

    import inspect

    tool = FocusWindowTool(source=MockWindowSource())

    execute_arguments = set(
        inspect.signature(tool.execute).parameters
    )
    verify_arguments = set(
        inspect.signature(tool.verify).parameters
    )

    assert execute_arguments == verify_arguments

    declared = {parameter.name for parameter in tool.parameters}

    assert declared <= execute_arguments


def test_a_verification_that_never_settles_is_bounded():
    """
    The poll has a deadline, so a switch that never happens fails rather
    than hanging. Asserted as an upper bound with room to spare - this is
    a wall-clock test and a loaded machine is allowed to be slow.
    """

    import time

    from tools.builtins.desktop import FOCUS_TIMEOUT

    stuck = MockWindowSource([
        WindowInfo(handle=1, title="Editor", pid=100, foreground=True),
        WindowInfo(handle=2, title="Browser", pid=200),
    ], honour_focus=False)

    tool = FocusWindowTool(source=stuck)

    started = time.monotonic()

    verdict = tool.verify(title="browser")

    elapsed = time.monotonic() - started

    assert not verdict.ok
    assert FOCUS_TIMEOUT <= elapsed < FOCUS_TIMEOUT + 2.0


def test_a_minimised_window_is_restored_before_being_focused(desktop):
    """
    A minimised window cannot come to the front while it is minimised, so
    the source restores it first. Observable here as the flag clearing.
    """

    assert any(window.minimised for window in desktop.windows())

    tool = FocusWindowTool(source=desktop)

    assert tool.execute(title="browser").ok

    browser = [
        window for window in desktop.windows() if window.title == "Browser"
    ][0]

    assert not browser.minimised
    assert browser.foreground


# ----------------------------------------------------------------------
# Registration, and the owner's control over it (section 2)
# ----------------------------------------------------------------------

def test_system_information_is_always_registered():
    """
    No config gates it, because there is no configuration to get wrong.
    `platform`, `os` and `shutil` answer on every platform Aura runs on.
    """

    assert build_registry({}).has("system_information")


@windows_only
def test_the_pc_tools_are_registered_on_this_machine():

    names = build_registry({}).names()

    assert "list_processes" in names
    assert "list_windows" in names
    assert "focus_window" in names


def test_the_window_tools_are_registered_together_or_not_at_all():
    """
    `focus_window` matches against the same listing `list_windows` shows.
    Offering one without the other gives a caller a way to act on a
    desktop it cannot see, or to see one it cannot act on.
    """

    names = build_registry({}).names()

    assert ("list_windows" in names) == ("focus_window" in names)


def test_the_window_tools_share_one_source():
    """
    Two sources would be two enumerations of the same desktop, and
    `focus_window` resolving a title against a listing the owner never saw
    is how the wrong window gets brought forward.
    """

    from tools.factory import _pc_tools

    tools = {tool.name: tool for tool in _pc_tools()}

    if "list_windows" not in tools:
        pytest.skip("no window source on this platform")

    assert tools["list_windows"].source is tools["focus_window"].source


def test_none_of_the_pc_tools_are_enabled_by_the_shipped_config():
    """
    Section 2, in the direction that is easy to get wrong.

    The owner must be able to enable these freely - and must not find them
    already enabled without having said so. Registering a tool grants
    nothing; `tools.allowed` is the grant, and gate three is what stands
    between the two.

    Read out of config.yaml rather than out of a constant, because the
    shipped file is what an owner actually gets.
    """

    import yaml

    with open("config.yaml", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}

    allowed = set((config.get("tools") or {}).get("allowed") or [])

    for name in (
        "system_information",
        "list_processes",
        "list_windows",
        "focus_window",
    ):
        assert name not in allowed, f"{name} is enabled in the shipped config"


def test_a_registered_pc_tool_is_inert_until_it_is_allowed():
    """
    The same assertion as above, stated as behaviour rather than as config.
    """

    from tools.factory import build_tools

    executor = build_tools({
        "enabled": True,
        "allowed": ["current_time"],
        "auto_approve": ["safe", "sensitive", "dangerous"],
    })

    assert executor.registry.has("system_information")

    result = executor.execute("system_information", {})

    assert not result.ok
    assert "not allowed" in result.error


def test_the_dangerous_pc_tool_needs_a_human_even_when_it_is_allowed():
    """
    Gate 4 defaults to refusal, and section 24's whole point is that a
    machine-changing action goes through it. No confirmation callback here,
    which is the server-mode configuration.
    """

    from tools.factory import build_tools

    executor = build_tools({
        "enabled": True,
        "allowed": ["focus_window"],
        "auto_approve": ["safe", "sensitive"],
    })

    if not executor.registry.has("focus_window"):
        pytest.skip("no window source on this platform")

    result = executor.execute("focus_window", {"title": "anything"})

    assert not result.ok
    assert "permission denied" in result.error


def test_every_pc_tool_satisfies_the_protocol():

    from tools.factory import _pc_tools

    for tool in _pc_tools():
        assert isinstance(tool, ToolProtocol), tool


def test_the_mock_sources_satisfy_their_protocols():
    """
    So a test that passes a mock is testing the same contract the real
    source implements, rather than a shape that happens to work.
    """

    assert isinstance(MockWindowSource(), WindowSource)
    assert isinstance(MockProcessSource(), ProcessSource)


def test_a_platform_with_no_window_source_gets_no_window_tools(monkeypatch):
    """
    The factory's standing rule: a tool whose dependency is absent is not
    registered, so it is missing rather than present and broken. Asserted
    by claiming this is not Windows, which is the condition the real code
    checks.
    """

    monkeypatch.setattr("tools.builtins.desktop.os.name", "posix")

    assert default_window_source() is None

    from tools.factory import _pc_tools

    names = {tool.name for tool in _pc_tools()}

    assert "list_windows" not in names
    assert "focus_window" not in names
    assert "system_information" in names


def test_a_platform_with_no_process_source_gets_no_process_tool(monkeypatch):
    """
    The same rule as the window source, and it needed the same test.

    Mutation testing found this one: replacing `return None` with `return
    MockProcessSource()` in `default_process_source` survived the entire
    suite, because this machine always has `tasklist` and so never reaches
    the fallback. An unreachable branch is untested by definition - the
    only way to assert on it is to take both real routes away.

    What the mutation would have caused is the failure mode the factory's
    rule exists to prevent: `list_processes` registered on a platform where
    it can read nothing, answering "no processes could be read" forever
    instead of not being offered.
    """

    monkeypatch.setattr(
        "tools.builtins.system.PsutilProcessSource.is_available",
        lambda self: False,
    )
    monkeypatch.setattr(
        "tools.builtins.system.TasklistProcessSource.is_available",
        lambda self: False,
    )

    assert default_process_source() is None

    from tools.factory import _pc_tools

    names = {tool.name for tool in _pc_tools()}

    assert "list_processes" not in names
    assert "system_information" in names


def test_a_tool_built_directly_with_no_source_still_works(monkeypatch):
    """
    The last-resort mock, which the factory never reaches.

    `ListProcessesTool()` with no argument on a platform with no process
    reading must not raise at construction - a caller outside the factory
    is allowed to build one, and the honest answer comes from `execute`
    reporting that nothing could be read.
    """

    monkeypatch.setattr(
        "tools.builtins.system.PsutilProcessSource.is_available",
        lambda self: False,
    )
    monkeypatch.setattr(
        "tools.builtins.system.TasklistProcessSource.is_available",
        lambda self: False,
    )
    monkeypatch.setattr("tools.builtins.desktop.os.name", "posix")

    result = ListProcessesTool().execute()

    assert not result.ok
    assert "no processes could be read" in result.error

    windows = ListWindowsTool().execute()

    assert windows.ok
    assert "no windows are open" in windows.output


@windows_only
def test_the_ctypes_signatures_are_declared():
    """
    A structural guard for a claim that is otherwise invisible.

    Mutation testing deleted `GetForegroundWindow.restype` and the whole
    suite still passed - correctly, as it turned out: Windows keeps USER
    handles inside 32 bits, so ctypes' default `c_int` return happens to
    be right and there is no behaviour to assert on. The first version of
    the module docstring claimed otherwise, and measuring it is what
    caught that.

    A declaration whose absence changes nothing today can only be guarded
    structurally, so that is what this does. It is worth guarding because
    the identical shortcut is a live bug in `_uptime_hours`, tested below.
    """

    import ctypes
    from ctypes import wintypes

    source = WindowsWindowSource()

    assert source.is_available()

    user32 = source._bind()

    # `argtypes` defaults to None, so its presence is observable for every
    # function. `restype` defaults to c_int, which is indistinguishable
    # from a deliberate c_int - so the restypes are checked by expected
    # value, and the two functions that genuinely return c_int are listed
    # as such rather than carved out of the loop.
    expected = {
        "EnumWindows": ctypes.c_bool,
        "IsWindowVisible": ctypes.c_bool,
        "IsIconic": ctypes.c_bool,
        "GetWindowTextLengthW": ctypes.c_int,
        "GetWindowTextW": ctypes.c_int,
        "GetWindowThreadProcessId": wintypes.DWORD,
        "GetForegroundWindow": wintypes.HWND,
        "SetForegroundWindow": ctypes.c_bool,
        "ShowWindow": ctypes.c_bool,
    }

    for name, restype in expected.items():

        entry = getattr(user32, name)

        assert entry.argtypes is not None, f"{name}: argtypes not declared"
        assert entry.restype is restype, f"{name}: restype is {entry.restype}"


@windows_only
def test_the_uptime_reading_is_not_truncated_to_32_bits():
    """
    The place where a missing `restype` is a real bug, not a habit.

    `GetTickCount64` returns milliseconds since boot. Read as a signed
    32-bit int - ctypes' default - it goes negative after 596.5 hours of
    uptime. The machine this was written on measured 300.7 hours, which is
    about twelve days short of reporting a negative uptime.

    Asserted as a property rather than against a number: uptime is not
    negative, and it is not the truncated reading either.
    """

    from tools.builtins.system import _uptime_hours

    hours = _uptime_hours()

    assert hours >= 0.0

    import ctypes

    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetTickCount64.restype = ctypes.c_ulonglong

    truthful = kernel32.GetTickCount64() / 3_600_000.0

    # Within a second of each other; the two calls are not simultaneous.
    assert abs(hours - truthful) < 0.001

    # What the 32-bit read would have produced, so the test says out loud
    # which value it is rejecting rather than only which one it wants.
    truncated = ctypes.c_int32(kernel32.GetTickCount64() & 0xFFFFFFFF).value

    if truncated < 0:
        # Only reachable past 596.5 hours of uptime. When it is reachable,
        # this is the assertion that matters.
        assert hours != truncated / 3_600_000.0

    # Until then the comparison above cannot fail, because the truncated
    # and truthful readings are the same number below 596.5 hours - which
    # mutation testing demonstrated by deleting the restype and watching
    # this test pass. So the declaration is also asserted structurally, on
    # the module's own call path: `ctypes.windll` caches the library, so
    # the assignment `_uptime_hours` makes is observable here afterwards.
    if psutil_is_installed():
        pytest.skip("psutil answers first, so the ctypes path is not taken")

    assert ctypes.windll.kernel32.GetTickCount64.restype is (
        ctypes.c_ulonglong
    )


def psutil_is_installed() -> bool:

    try:
        import psutil            # noqa: F401

        return True
    except Exception:
        return False


@windows_only
def test_the_real_window_source_reads_this_desktop():
    """
    Shape only. Every visible titled window has a pid and a handle, and
    at most one of them is in front.
    """

    source = default_window_source()

    assert source is not None

    found = source.windows()

    assert all(window.handle for window in found)
    assert all(window.pid > 0 for window in found)
    assert all(window.title.strip() for window in found)
    assert len([window for window in found if window.foreground]) <= 1
