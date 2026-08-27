"""
What machine Aura is running on, and what is running on it.

The two read-only halves of Section 24's PC layer: `system_information`
answers "what is this computer" and `list_processes` answers "what is it
doing". Both are SENSITIVE rather than SAFE - neither changes anything,
but both describe the owner's machine to whichever model is answering,
and that is a disclosure the owner should have to permit on purpose.

Neither offers a `verify()`, and the reason is the one `open_application`
already documents: a read's postcondition *is* its return value. There is
no separate condition to re-ask - asking again would only produce a
second reading, and two readings agreeing proves nothing a single reading
did not. Absence here means "execute already told the whole truth", never
"unverified".

Each observation is taken through a small source object rather than
inline, for the same reason `vision/capture.py` puts `ScreenCapture`
behind a Protocol with a mock beside it: the real reading depends on
which OS is underneath, and a test that asserts against the real one
passes or fails according to the machine it runs on rather than according
to the code.

What is deliberately not reported: hostname, username, and the home
directory path. The output of this tool is written into a prompt, so it
leaves the machine, and none of those three help answer "what system am I
on" - the owner already knows which computer they are sitting at. That is
a choice about what the tool says, not a restriction on what the owner
may configure.
"""

import csv
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from core.logger import logger
from tools.base import Parameter, Tool, ToolResult, ToolRisk, fail, ok


# How many processes a single listing may name. A machine with 300
# processes would otherwise spend a prompt's whole budget on a list the
# model reads once.
MAX_PROCESSES = 40

# Bytes of `tasklist` output to read. A 300 process machine produced
# 11,589 bytes in testing, so this is roughly a five times margin and
# still small enough that a wedged tasklist cannot fill memory.
MAX_TASKLIST_BYTES = 200_000

# How long the process listing subprocess may take. Measured at 0.27s on
# a 16 core Windows machine with ~300 processes; ten seconds is a wide
# margin for a busy or cold machine.
PROCESS_LIST_TIMEOUT = 10.0

BYTES_PER_GB = 1024 ** 3


# ----------------------------------------------------------------------
# System information
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class SystemFacts:
    """
    One reading of what this machine is.

    Every field after `system` is optional by emptiness rather than by
    default value, because "could not be read" and "is zero" are
    different answers and a machine with no readable memory figure must
    not be described as having none. `render` omits what it does not
    have instead of printing a zero.
    """

    system: str = ""
    release: str = ""
    version: str = ""
    machine: str = ""
    processors: int = 0
    memory_total_gb: float = 0.0
    memory_available_gb: float = 0.0
    disk_total_gb: float = 0.0
    disk_free_gb: float = 0.0
    disk_path: str = ""
    uptime_hours: float = 0.0
    python_version: str = ""

    def render(self) -> str:
        """One fact per line, and no line for a fact that is missing."""

        lines: list[str] = []

        name = " ".join(
            part for part in (self.system, self.release) if part
        ).strip()

        if name:
            lines.append(f"operating system: {name}")

        if self.version:
            lines.append(f"version: {self.version}")

        if self.machine:
            lines.append(f"architecture: {self.machine}")

        if self.processors:
            lines.append(f"processors: {self.processors}")

        if self.memory_total_gb:

            memory = f"memory: {self.memory_total_gb:.1f} GB total"

            if self.memory_available_gb:
                memory += f", {self.memory_available_gb:.1f} GB available"

            lines.append(memory)

        if self.disk_total_gb:

            where = f" on {self.disk_path}" if self.disk_path else ""

            lines.append(
                f"disk{where}: {self.disk_total_gb:.1f} GB total, "
                f"{self.disk_free_gb:.1f} GB free"
            )

        if self.uptime_hours:
            lines.append(f"uptime: {self.uptime_hours:.1f} hours")

        if self.python_version:
            lines.append(f"python: {self.python_version}")

        if not lines:
            return "nothing about this system could be read"

        return "\n".join(lines)


@runtime_checkable
class SystemFactsSource(Protocol):

    def read(self) -> SystemFacts:
        ...


class MockSystemFacts:
    """
    Facts that do not depend on the machine the test runs on.

    Used by every test, and it is the reason those tests assert the same
    thing on Windows and on a Linux runner.
    """

    def __init__(self, facts: SystemFacts | None = None):

        self.facts = facts or SystemFacts(
            system="TestOS",
            release="1.0",
            machine="x86_64",
            processors=4,
        )

        self.reads = 0

    def read(self) -> SystemFacts:

        self.reads += 1

        return self.facts


class LocalSystemFacts:
    """
    The real reading, and every part of it is allowed to be missing.

    `platform` and `os` are standard library and cannot fail in any way
    worth handling. Memory and uptime have no portable standard library
    answer at all, so each is attempted through the cheapest route that
    exists on this machine and left absent when none does - an absent
    memory figure is a fact about what could be read, and inventing a
    zero would be the same lie `open_application` used to tell.
    """

    def read(self) -> SystemFacts:

        total = available = 0.0

        memory = _memory_bytes()

        if memory:
            total = memory[0] / BYTES_PER_GB
            available = memory[1] / BYTES_PER_GB

        disk_total = disk_free = 0.0
        where = ""

        try:
            where = os.getcwd()
            usage = shutil.disk_usage(where)
            disk_total = usage.total / BYTES_PER_GB
            disk_free = usage.free / BYTES_PER_GB

        except OSError as error:
            logger.debug("Disk usage unreadable: %s", error)
            where = ""

        return SystemFacts(
            system=platform.system(),
            release=platform.release(),
            version=platform.version(),
            machine=platform.machine(),
            processors=os.cpu_count() or 0,
            memory_total_gb=total,
            memory_available_gb=available,
            disk_total_gb=disk_total,
            disk_free_gb=disk_free,
            disk_path=where,
            uptime_hours=_uptime_hours(),
            python_version=platform.python_version(),
        )


def _memory_bytes() -> tuple[int, int] | None:
    """
    (total, available) in bytes, or None when neither route works.

    `psutil` first because it is the portable answer and it is what a
    machine that has it should use. On Windows without it, kernel32's
    GlobalMemoryStatusEx is standard-library reachable through ctypes and
    needs no install, which is the same reasoning that lets
    `WindowsWindowReader` read the foreground window with no dependency.
    """

    try:
        import psutil               # noqa: PLC0415  (optional dependency)

        memory = psutil.virtual_memory()

        return int(memory.total), int(memory.available)

    except Exception as error:
        logger.debug("psutil memory unavailable: %s", error)

    if os.name != "nt":
        return None

    try:
        import ctypes               # noqa: PLC0415

        class _Status(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _Status()
        status.dwLength = ctypes.sizeof(_Status)

        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(
            ctypes.byref(status)
        ):
            return None

        return int(status.ullTotalPhys), int(status.ullAvailPhys)

    except Exception as error:
        logger.debug("Memory status unreadable: %s", error)
        return None


def _uptime_hours() -> float:
    """
    How long this machine has been up, or 0.0 when that cannot be read.

    Zero is safe as the absent value here in a way it is not for memory:
    `render` treats it as missing, and a machine genuinely up for zero
    hours is one that has been running for under three minutes, where the
    line is not worth printing either way.
    """

    try:
        import psutil               # noqa: PLC0415

        import time

        return max(0.0, (time.time() - psutil.boot_time()) / 3600.0)

    except Exception as error:
        logger.debug("psutil boot time unavailable: %s", error)

    if os.name != "nt":
        return 0.0

    try:
        import ctypes               # noqa: PLC0415

        kernel32 = ctypes.windll.kernel32

        # The restype is load-bearing, not decoration. Undeclared, ctypes
        # reads the result as a signed 32-bit int, and the tick count
        # passes 2**31 milliseconds after 596.5 hours of uptime - at which
        # point this reports a negative number and `render` prints it.
        # Measured on the machine this was written on: 300.7 hours, so
        # roughly twelve days from being wrong.
        kernel32.GetTickCount64.restype = ctypes.c_ulonglong

        return kernel32.GetTickCount64() / 3_600_000.0

    except Exception as error:
        logger.debug("Tick count unreadable: %s", error)
        return 0.0


class SystemInformationTool(Tool):

    name = "system_information"
    description = "Describe the computer Aura is running on"
    risk = ToolRisk.SENSITIVE
    capability = 'system.info'

    parameters: tuple[Parameter, ...] = ()

    def __init__(self, source: SystemFactsSource | None = None):

        self.source = source or LocalSystemFacts()

    def execute(self) -> ToolResult:

        try:
            facts = self.source.read()

        except Exception as error:
            # A source that raises is a failed reading, not a machine
            # with no properties. Reported as the failure it is rather
            # than as an empty description that reads like an answer.
            return fail(
                f"could not read system information: "
                f"{type(error).__name__}: {error}",
                tool=self.name,
            )

        return ok(facts.render(), tool=self.name)


# ----------------------------------------------------------------------
# Process inspection
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class ProcessInfo:
    """One running process, as much of it as could be read."""

    pid: int = 0
    name: str = ""
    memory_kb: int = 0

    def render(self) -> str:

        if self.memory_kb:
            return f"{self.name} (pid {self.pid}, {self.memory_kb:,} KB)"

        return f"{self.name} (pid {self.pid})"


@runtime_checkable
class ProcessSource(Protocol):

    def processes(self) -> list[ProcessInfo]:
        ...


class MockProcessSource:

    def __init__(self, processes: list[ProcessInfo] | None = None):

        self.items = list(processes or [])
        self.reads = 0

    def processes(self) -> list[ProcessInfo]:

        self.reads += 1

        return list(self.items)


class PsutilProcessSource:
    """The portable reading, when psutil happens to be installed."""

    def is_available(self) -> bool:

        try:
            import psutil           # noqa: PLC0415, F401

            return True

        except Exception:
            return False

    def processes(self) -> list[ProcessInfo]:

        import psutil               # noqa: PLC0415

        found: list[ProcessInfo] = []

        for process in psutil.process_iter(["pid", "name", "memory_info"]):

            try:
                info = process.info

                memory = info.get("memory_info")

                found.append(
                    ProcessInfo(
                        pid=int(info.get("pid") or 0),
                        name=str(info.get("name") or ""),
                        memory_kb=int(getattr(memory, "rss", 0) or 0) // 1024,
                    )
                )

            except Exception:
                # A process that exited between the enumeration and the
                # read is not an error; it is the ordinary case on a busy
                # machine, and skipping it is the whole handling needed.
                continue

        return found


class TasklistProcessSource:
    """
    Process inspection through the `tasklist` Windows ships with.

    This is a subprocess, so it is worth being precise about why it does
    not weaken Section 24's boundary: the argv is written here in full and
    is the same on every call, `shell=False`, and nothing a model or the
    owner supplies reaches it. The controlled boundary is not "no
    subprocess ever" - `open_application` spawns one too - it is that the
    command is never assembled from text somebody else wrote.
    """

    ARGUMENTS = ("/fo", "csv", "/nh")

    def is_available(self) -> bool:

        return os.name == "nt" and shutil.which("tasklist") is not None

    def processes(self) -> list[ProcessInfo]:

        executable = shutil.which("tasklist")

        if not executable:
            return []

        try:
            completed = subprocess.run(
                [executable, *self.ARGUMENTS],
                shell=False,
                capture_output=True,
                timeout=PROCESS_LIST_TIMEOUT,
            )

        except (OSError, subprocess.SubprocessError) as error:
            logger.debug("tasklist failed: %s", error)
            return []

        if completed.returncode != 0:
            logger.debug("tasklist exited with %s", completed.returncode)
            return []

        text = completed.stdout[:MAX_TASKLIST_BYTES].decode(
            "utf-8", "replace"
        )

        return _parse_tasklist(text)


def _parse_tasklist(text: str) -> list[ProcessInfo]:
    """
    Rows of `tasklist /fo csv /nh` as ProcessInfo.

    The columns are name, pid, session, session number, memory - and the
    memory cell arrives as a localised string like `12,345 K`, so it is
    read digit by digit rather than parsed as a number. A row whose pid
    is not a number is skipped: tasklist prints a header row when `/nh`
    is somehow not honoured, and a header is not a process.
    """

    found: list[ProcessInfo] = []

    for row in csv.reader(text.splitlines()):

        if len(row) < 2:
            continue

        try:
            pid = int(row[1].strip())
        except (TypeError, ValueError):
            continue

        digits = "".join(
            character
            for character in (row[4] if len(row) > 4 else "")
            if character.isdigit()
        )

        found.append(
            ProcessInfo(
                pid=pid,
                name=row[0].strip(),
                memory_kb=int(digits) if digits else 0,
            )
        )

    return found


def default_process_source() -> ProcessSource | None:
    """
    The best process reading available on this machine, or None.

    psutil when installed, `tasklist` on Windows without it, and None on a
    platform neither route covers. None rather than an empty mock, because
    the caller who needs to know is the factory: its rule is that a tool
    whose dependency is absent is not registered, so that it is missing
    rather than present and broken, and a mock reporting an empty machine
    is exactly the present-and-broken case that rule exists to prevent.
    """

    psutil_source = PsutilProcessSource()

    if psutil_source.is_available():
        return psutil_source

    tasklist = TasklistProcessSource()

    if tasklist.is_available():
        return tasklist

    return None


class ListProcessesTool(Tool):

    name = "list_processes"
    description = "List the programs currently running on this computer"
    risk = ToolRisk.SENSITIVE
    capability = 'system.processes'

    parameters = (
        Parameter(
            name="name",
            description="Only processes whose name contains this",
            required=False,
        ),
        Parameter(
            name="limit",
            description=f"How many to name, at most {MAX_PROCESSES}",
            required=False,
        ),
    )

    def __init__(self, source: ProcessSource | None = None):

        # The empty mock is the last resort for a caller that built this
        # directly on a platform with no process reading. The factory
        # never reaches it: it asks `default_process_source()` itself and
        # declines to register the tool when the answer is None.
        self.source = (
            source or default_process_source() or MockProcessSource()
        )

    def execute(self, name: str = "", limit=None) -> ToolResult:
        """
        Name the running processes, largest first.

        Largest first because the question behind "what is running" is
        almost always about something the user can see or something eating
        the machine, and both are near the top of a memory ordering. A
        listing truncated at MAX_PROCESSES says so, because a silently
        shortened list reads as a complete one.
        """

        try:
            processes = self.source.processes()

        except Exception as error:
            return fail(
                f"could not list processes: "
                f"{type(error).__name__}: {error}",
                tool=self.name,
            )

        wanted = str(name or "").strip().lower()

        if wanted:
            processes = [
                process
                for process in processes
                if wanted in process.name.lower()
            ]

        if not processes:

            if wanted:
                return ok(
                    f"nothing running matches '{name}'",
                    tool=self.name,
                )

            # An empty listing with no filter is not "an idle machine" -
            # every OS has processes. It means the reading failed, and
            # saying so is the difference between a wrong answer and a
            # missing one.
            return fail(
                "no processes could be read on this machine",
                tool=self.name,
            )

        processes.sort(key=lambda process: (-process.memory_kb, process.name))

        ceiling = _ceiling(limit)

        shown = processes[:ceiling]

        lines = [process.render() for process in shown]

        if len(processes) > len(shown):
            lines.append(
                f"...and {len(processes) - len(shown)} more not listed"
            )

        return ok("\n".join(lines), tool=self.name)


def _ceiling(limit) -> int:
    """
    How many rows to show, given whatever the model asked for.

    Bounded above by MAX_PROCESSES whatever arrives, because the ceiling
    exists to protect the prompt and a caller cannot be allowed to raise
    it. An unreadable or absent limit is the ceiling, not an error: the
    argument is optional and a bad one is a request for the default.
    """

    if isinstance(limit, bool) or limit is None:
        return MAX_PROCESSES

    try:
        asked = int(limit)
    except (TypeError, ValueError):
        return MAX_PROCESSES

    if asked <= 0:
        return MAX_PROCESSES

    return min(asked, MAX_PROCESSES)


__all__ = [
    "ListProcessesTool",
    "MockProcessSource",
    "MockSystemFacts",
    "ProcessInfo",
    "ProcessSource",
    "SystemFacts",
    "SystemFactsSource",
    "SystemInformationTool",
    "default_process_source",
]
