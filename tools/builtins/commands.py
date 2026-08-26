"""
Running a command the owner declared.

Section 24's hard line, verbatim: *"Do not give arbitrary LLM text direct
unrestricted shell execution without a controlled tool boundary."* This is
the tool that would violate it if it were built the obvious way, so it is
not built the obvious way: **the model never supplies a command line.**

The owner writes named commands in config, argv as a list:

    commands:
      repo_status:
        argv: ["git", "status", "--short"]
        description: "Which files in the project have changed"
        cwd: "D:/AURA"

      find_text:
        argv: ["git", "grep", "-n", "--", "{pattern}"]
        description: "Search the project for a piece of text"
        parameters:
          pattern: "The text to look for"
        timeout: 20

The model may ask for `find_text` with `pattern: "def execute"`. It cannot
ask for `git push --force`, and it cannot append `&& rm -rf` to anything,
because it never writes an argv element - it fills in the slots the owner
left, one value per element, and `shell=False` means nothing it supplies is
ever parsed by a shell. This is `open_application` widened by exactly one
degree: that tool takes a nickname and no arguments, this one takes a
nickname and the arguments the owner declared as fillable.

WHY A `values` MAPPING AND NOT FLAT KEYWORD ARGUMENTS

A tool has one parameter list, and each declared command has different
slots, so flat arguments could not be declared at all - `parameters` would
have to change identity depending on which command was chosen. One
`values` mapping keeps the signature fixed and the slots explicit.

WHAT WAS MEASURED, NOT ASSUMED

Three decisions here rest on probes run on this machine rather than on
reputation, because each of them looks like superstition until the numbers
are written down. Python was 3.11.15.

1.  **A resolved `.bat` or `.cmd` re-parses its arguments through cmd.exe,
    even under `shell=False`, and a literal double quote in a value
    escapes the quoting Python applies.** Passing
    `x" & echo B > canaryB.txt & "` to a batch file created the file.
    `& && | ^ >` on their own are neutralised - Python's fix for
    CVE-2024-1874 is present and quotes them - but a `"` breaks out of
    that quoting and the rest of the string runs as commands. `%CD%` and
    `%PATH:~0,12%` also expand inside the batch file, which leaks the
    machine's paths into an argument. The same payloads against a real
    `.exe` arrive in `sys.argv` byte for byte with nothing created and
    nothing expanded, so the hole is batch-specific, not general.

    This is why a resolved `.bat`/`.cmd` with a model-fillable slot is
    **refused**, and why that is not paranoia about a fixed CVE: it is a
    live result on the interpreter this runs on. It is also not exotic -
    `shutil.which` resolves `npm`, `npx` and `code` to `.CMD` shims on
    this machine, so the common case is the dangerous one.

2.  **Output goes to temporary files, never to pipes.** A command whose
    child outlives it holds the write end of an inherited pipe open, and
    the read never finishes. `subprocess.run(timeout=1.0)` on a batch file
    that leaves a `ping` running returned after **29.25 seconds** - the
    timeout could not fire while a grandchild held the pipe. The same
    command through temporary files, with `wait(timeout)` then `kill()`
    then a process-tree kill, returned in **1.08 seconds** with the output
    captured. `open_application` already reaches for a temporary file for
    the same reason; this needs it for stdout too, and needs the tree kill
    on top.

3.  **A process is killed by its tree, not by its handle.** `Popen.kill`
    ends the process named and leaves its children running, which is how
    the pipe above stayed open. On Windows that means `taskkill /F /T`;
    on POSIX it means a new session and `killpg`.

WHAT SECTION 11 MEANS FOR A COMMAND

`verify()` is deliberately absent, and the reason is the opposite of the
usual one. Section 11 forbids resting on *"the command executed without
throwing"* - and nothing here does. A command reports on itself: the exit
status is the program's own verdict on whether it did what it was asked,
and a non-zero status is returned as a failure with the status and stderr
rather than as a success with sad text attached. That is a postcondition
read back from the world, not an absence of an exception.

Re-asking afterwards would be the dishonest move, because this tool does
not know what any given command was supposed to change. It cannot check
that `git commit` committed without knowing it was git. Inventing a
postcondition it cannot actually test - re-running the command, say - would
be worse than having none, and it would double the side effects. Absence
means "execute already told the whole truth", exactly as it does on
`open_application`.

CREDENTIALS DO NOT TRAVEL DOWNWARD

Section 30 requires that API keys never appear in logs, chat history or
prompts. `core/credentials.py` puts stored keys **into `os.environ`** on
purpose, so that providers can keep reading `os.getenv`. A child process
inherits that environment by default, and this tool's output is read by a
model and lands in the transcript - so `run_command` would be a way to
print Aura's own keys into chat history by asking a command to echo its
environment. The child therefore gets an environment with the credentials
removed: the exact names Aura itself sets (`PROVIDER_KEYS` and
`SECRET_ENV_VARS`, imported rather than copied, so a provider added later
is covered without a change here) plus anything else whose name reads like
a secret.
"""

from dataclasses import dataclass, field
from pathlib import Path
import os
import re
import shutil
import signal
import subprocess
import tempfile

from core.logger import logger
from tools.base import Parameter, Tool, ToolResult, ToolRisk, fail, ok
from tools.builtins.apps import resolve_executable
from tools.timeout import seconds_or


# How long a command may run when the owner did not say. Long enough for a
# git operation on a cold disk, short enough that a wedged command is a
# failed reply rather than a lost conversation.
DEFAULT_COMMAND_TIMEOUT = 20.0

# How long to wait for a killed process to actually die before giving up on
# it. Reaching this means the kill did not work, which is worth saying.
KILL_GRACE = 5.0

# How much of a command's output to hand back. This text goes into a
# prompt, so it is bounded; truncation is announced rather than silent.
MAX_OUTPUT = 2000

# Enough of stderr to diagnose a non-zero exit without pasting somebody
# else's stack trace into the conversation.
MAX_ERROR = 600

# An argv element may contain `{slot}` markers. A slot name is an
# identifier, so `{}` and `{1}` and `{a-b}` are not slots - which matters,
# because a program that legitimately wants a literal brace should not have
# it silently eaten.
SLOT = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

# A brace that is not a slot but looks like it was meant to be one, such as
# `{ pattern }` or `{my-pattern}`. Worth a word to the owner, because the
# model will supply nothing for it and the program will receive the braces.
#
# Deliberately narrow. An earlier version matched *any* pair of braces and
# refused to run the command, which broke three ordinary things: `find . -exec
# rm {} \;`, `grep -E "a{2,3}"`, and `jq "{name: .n}"`. None of those is a
# misspelled slot, and none of them is the model's text - the owner typed the
# braces themselves, so there is nothing here for Section 24 to protect
# against. Passing them through is what the owner asked for.
NEAR_SLOT = re.compile(r"\{\s*[A-Za-z_][A-Za-z0-9_.\-]*\s*\}")

# The suffixes cmd.exe re-parses arguments for. See probe 1 above.
BATCH_SUFFIXES = (".bat", ".cmd")

# Programs whose whole job is to run whatever text they are given. An owner
# may declare one deliberately - Section 2 says that is their call - but a
# fillable slot in one of these hands the model most of what Section 24
# forbids, so it is warned about loudly at load rather than discovered
# later. Matched on the resolved stem, so `C:\Windows\System32\cmd.exe`
# and a bare `cmd` are the same entry.
SHELL_STEMS = frozenset(
    {
        "cmd",
        "command",
        "powershell",
        "pwsh",
        "sh",
        "bash",
        "zsh",
        "ksh",
        "csh",
        "fish",
        "wscript",
        "cscript",
        "mshta",
        "rundll32",
        "regsvr32",
    }
)

# Names that read like a secret. Deliberately broad, because the cost of
# withholding one variable from a command is a command that has to be
# declared differently, while the cost of leaking one is a key in the
# transcript.
CREDENTIAL_NAME = re.compile(
    r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH",
    re.IGNORECASE,
)

# The one name that matches the pattern above and is not a secret. It is
# the path of a socket, not the contents of one, and `git` over SSH stops
# working without it. Listed explicitly with its reason rather than left to
# a wider pattern, because every exception here is a hole and a hole should
# be readable.
CREDENTIAL_EXCEPTIONS = frozenset({"SSH_AUTH_SOCK"})


@dataclass(frozen=True)
class Command:
    """
    One command the owner declared, after validation.

    `slots` is derived from `argv`, not from `parameters`: the argv is what
    actually gets filled in, so it is the authority on what this command
    takes. `parameters` only supplies the descriptions a model reads.
    """

    name: str
    argv: tuple[str, ...]
    description: str = ""
    parameters: dict[str, str] = field(default_factory=dict)
    timeout: float = DEFAULT_COMMAND_TIMEOUT
    cwd: str = ""

    @property
    def slots(self) -> tuple[str, ...]:
        """Slot names in argv order, each once, in first-seen order."""

        seen: list[str] = []

        for element in self.argv:
            for slot in SLOT.findall(element):
                if slot not in seen:
                    seen.append(slot)

        return tuple(seen)

    def render(self) -> str:
        """One line for `describe`, so a model can see what it may ask."""

        parts = [f"    - {self.name}"]

        if self.description:
            parts.append(f": {self.description}")

        if self.slots:
            parts.append(f" (values: {', '.join(self.slots)})")

        return "".join(parts)


class RunCommandTool(Tool):

    name = "run_command"
    description = "Run one of the commands the user has declared"
    risk = ToolRisk.DANGEROUS

    parameters = (
        Parameter(
            name="name",
            description="Name of a declared command",
        ),
        Parameter(
            name="values",
            description="Values for the command's slots, as a mapping",
            required=False,
        ),
    )

    def __init__(self, commands: dict | None = None):

        self.commands = _normalise(commands)

        # The executor bounds `execute` on a daemon thread it cannot kill,
        # so that bound must sit *outside* this tool's own bound rather
        # than inside it. Inside, the thread would be abandoned mid-kill
        # and the process it was killing would survive with nobody
        # watching. Outside, this tool's timeout-and-kill runs to
        # completion and the executor's limit is only a backstop for the
        # kill path itself wedging.
        longest = max(
            (command.timeout for command in self.commands.values()),
            default=DEFAULT_COMMAND_TIMEOUT,
        )

        # 0 means unbounded in this codebase (`tools/timeout.py` documents
        # it as the hatch for a call that legitimately blocks). An owner
        # who declared that gets it, on both bounds - clamping it here
        # would be the silent override Section 2 forbids.
        if any(command.timeout == 0 for command in self.commands.values()):
            self.timeout = 0.0
        else:
            self.timeout = longest + KILL_GRACE + 5.0

    @property
    def available(self) -> list[str]:
        return sorted(self.commands)

    def describe(self) -> str:
        """List the declared commands, so a model cannot guess a name."""

        base = super().describe()

        if not self.commands:
            return f"{base}\n    (no commands configured)"

        lines = [base, "    declared commands:"]

        lines.extend(
            self.commands[name].render() for name in self.available
        )

        return "\n".join(lines)

    # ------------------------------------------------------------------

    def execute(self, name: str, values: dict | None = None) -> ToolResult:
        """
        Run a declared command and report what it actually did.

        Failures come back as failed results rather than exceptions,
        because each is something the model or the owner has to be told
        accurately: a slot left empty, a program that is not installed, a
        non-zero exit, a command that had to be killed.

        An unknown name still raises, exactly as `open_application` does.
        That is not a command that failed, it is a request for something
        the owner never declared, and it should read like the permission
        error it is.
        """

        key = str(name).strip().lower()

        command = self.commands.get(key)

        if command is None:
            raise PermissionError(
                f"'{name}' is not a declared command"
            )

        argv, problem = _fill(command, values)

        if problem:
            return fail(problem, tool=self.name)

        executable = resolve_executable(argv[0])

        if executable is None:
            return fail(
                f"cannot run {key}: '{argv[0]}' was not found on this "
                f"machine. Nothing was run.",
                tool=self.name,
            )

        # Checked here and not at load: a batch file can appear on PATH
        # after Aura started, and the resolution is what decides this.
        if command.slots and _is_batch(executable):
            logger.warning(
                "Refusing %s: it resolves to the batch file %s and has "
                "fillable values",
                key,
                Path(executable).name,
            )

            return fail(
                f"cannot run {key}: it resolves to '{Path(executable).name}', "
                f"a batch file, and batch files re-parse their arguments "
                f"through cmd.exe - a value containing a quote can run "
                f"commands of its own. Declare the underlying program "
                f"directly, or remove the fillable values. Nothing was run.",
                tool=self.name,
            )

        directory, problem = _directory(command)

        if problem:
            return fail(problem, tool=self.name)

        return _run(
            key,
            executable,
            argv,
            directory,
            command.timeout,
            tool=self.name,
        )


# ----------------------------------------------------------------------
# Filling in the owner's argv
# ----------------------------------------------------------------------


def _fill(
    command: Command, values: dict | None
) -> tuple[tuple[str, ...], str]:
    """
    The argv to run, or a reason it cannot be built.

    Every substitution is a whole value into one argv element. Nothing is
    split, nothing is joined, and no shell ever sees it - so a value
    containing a space, a semicolon or a newline is one argument with a
    space, semicolon or newline in it, which is the entire point.

    An unexpected key is a refusal rather than something ignored. A model
    that sent `patern` instead of `pattern` has to be told, or it will read
    the command's output as an answer to the question it thought it asked.
    """

    if values is None:
        values = {}

    if not isinstance(values, dict):
        return (), (
            f"cannot run {command.name}: values must be a mapping of slot "
            f"name to value, not {type(values).__name__}. Nothing was run."
        )

    supplied = {str(key): value for key, value in values.items()}

    slots = command.slots

    missing = [slot for slot in slots if slot not in supplied]

    unexpected = [key for key in sorted(supplied) if key not in slots]

    # Reported together, not one at a time. A misspelled slot produces both
    # at once - `patern` is unexpected and `pattern` is missing - and naming
    # only the missing one sends the caller back to fill in `pattern` while
    # still passing `patern`, which fails again for a reason it was never
    # told. Saying both makes the typo visible in one reply.
    if missing and unexpected:
        return (), (
            f"cannot run {command.name}: no value for "
            f"{', '.join(missing)}, and it does not take "
            f"{', '.join(unexpected)}"
            + (
                " - the names may be misspelled"
                if len(missing) == len(unexpected)
                else ""
            )
            + ". Nothing was run."
        )

    if missing:
        return (), (
            f"cannot run {command.name}: no value for "
            f"{', '.join(missing)}. Nothing was run."
        )

    if unexpected:
        wanted = ", ".join(slots) if slots else "no values at all"

        return (), (
            f"cannot run {command.name}: it does not take "
            f"{', '.join(unexpected)}. It takes {wanted}. Nothing was run."
        )

    text: dict[str, str] = {}

    for slot in slots:

        value = supplied[slot]

        if isinstance(value, bool) or not isinstance(
            value, (str, int, float)
        ):
            # A list or a mapping has no single obvious spelling as one
            # argv element, and guessing one is how a value stops meaning
            # what the caller meant. A bool is excluded on purpose: "True"
            # is almost never the flag the program wants.
            return (), (
                f"cannot run {command.name}: {slot} must be text or a "
                f"number, not {type(value).__name__}. Nothing was run."
            )

        rendered = str(value)

        if "\x00" in rendered:
            # A NUL cannot survive the OS boundary, and Popen raises on it
            # after the process may already exist on some platforms.
            return (), (
                f"cannot run {command.name}: {slot} contains a null "
                f"character. Nothing was run."
            )

        text[slot] = rendered

    argv = tuple(
        SLOT.sub(lambda match: text[match.group(1)], element)
        for element in command.argv
    )

    # No check for braces left in the rendered argv, on purpose. Whatever
    # survives substitution is text the *owner* typed, and a program that
    # wants a literal brace - `find -exec rm {}`, `grep -E "a{2,3}"`, `jq
    # "{n: .name}"` - is entitled to receive one. `_warn_about` says
    # something at load if it looks like a misspelled slot; refusing here
    # would override the owner to guess at their intent.
    return argv, ""


def _directory(command: Command) -> tuple[str | None, str]:
    """
    The working directory to run in, or a reason not to run.

    Checked at execute rather than at load, because a directory can appear
    or disappear while Aura is running. A declared directory that is not
    there is a failure and not a silent fallback to Aura's own directory:
    `git status` in the wrong repository answers confidently about the
    wrong thing.
    """

    if not command.cwd:
        return None, ""

    try:
        path = Path(command.cwd).expanduser()
        present = path.is_dir()
    except OSError as error:
        return None, (
            f"cannot run {command.name}: its directory "
            f"'{command.cwd}' could not be read ({error}). Nothing was run."
        )

    if not present:
        return None, (
            f"cannot run {command.name}: its directory "
            f"'{command.cwd}' does not exist. Nothing was run."
        )

    return str(path), ""


def _is_batch(executable: str) -> bool:
    """Whether cmd.exe will re-parse this program's arguments."""

    return Path(executable).suffix.lower() in BATCH_SUFFIXES


# ----------------------------------------------------------------------
# Running it
# ----------------------------------------------------------------------


def _run(
    key: str,
    executable: str,
    argv: tuple[str, ...],
    directory: str | None,
    timeout: float,
    tool: str,
) -> ToolResult:
    """
    Spawn, wait, and report - killing the whole tree if it overruns.

    Output goes to temporary files rather than pipes. See probe 2 in the
    module docstring: a grandchild holding an inherited pipe made a 1
    second timeout take 29.25 seconds, and no amount of care at this level
    fixes that, because the wait is inside the reader.
    """

    logger.info("Running declared command: %s", key)

    output = tempfile.TemporaryFile()
    errors = tempfile.TemporaryFile()

    try:
        process = subprocess.Popen(
            [executable, *argv[1:]],
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=errors,
            cwd=directory,
            env=_child_environment(),
            **_isolation(),
        )

    except OSError as error:
        output.close()
        errors.close()

        logger.info("Command %s refused by the OS: %s", key, error)

        return fail(
            f"cannot run {key}: {error}. Nothing was run.",
            tool=tool,
        )

    killed = False

    try:
        status = process.wait(timeout=timeout or None)

    except subprocess.TimeoutExpired:
        killed = True
        status = _kill_tree(process, key)

    stdout, truncated = _text(output, MAX_OUTPUT)
    stderr, _ = _text(errors, MAX_ERROR)

    output.close()
    errors.close()

    if killed:
        # Whatever it managed to print before the kill is the most useful
        # thing there is here, so it is reported rather than discarded.
        detail = f" It printed:\n{stdout}" if stdout else ""

        return fail(
            f"{key} did not finish within {timeout:g}s and was stopped."
            f"{detail}",
            tool=tool,
        )

    if status != 0:
        logger.info("Command %s exited with status %s", key, status)

        said = stderr or stdout

        return fail(
            f"{key} failed: exited with status {status}"
            + (f". It said: {said}" if said else " and said nothing")
            + ".",
            tool=tool,
        )

    if not stdout and not stderr:
        # Succeeded and printed nothing. Worth saying plainly rather than
        # returning an empty string a model would read as a broken tool.
        return ok(
            f"{key} finished successfully and produced no output.",
            tool=tool,
        )

    note = (
        f"\n(only the first {MAX_OUTPUT} characters are shown)"
        if truncated
        else ""
    )

    body = stdout or stderr

    return ok(f"{key} finished successfully:\n{body}{note}", tool=tool)


def _isolation() -> dict:
    """
    The platform flags that make a tree kill possible.

    On POSIX the child needs its own process group, or `killpg` would
    signal Aura as well. On Windows the equivalent is done by `taskkill /T`
    after the fact, and the flag here is only about not flashing a console
    window in front of the owner for a background command.
    """

    if os.name == "nt":

        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        return {"creationflags": flags} if flags else {}

    return {"start_new_session": True}


def _kill_tree(process: subprocess.Popen, key: str) -> int | None:
    """
    End an overrunning command and everything it started.

    `Popen.kill` alone is not enough, and that is the whole reason this
    function exists: it ends the process named and leaves the children
    running, which is exactly how an inherited pipe stays open long after
    the command was supposedly stopped.

    Returns the exit status if the process is gone, or None if it survived
    everything - which the caller reports as a stop rather than as a
    success, because a process that ignored a kill is not a finished one.
    """

    logger.warning("Command %s overran its time and is being stopped", key)

    if os.name == "nt":
        _taskkill(process.pid)
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (OSError, AttributeError) as error:
            logger.debug("Could not signal the group for %s: %s", key, error)

    try:
        process.kill()
    except OSError as error:
        logger.debug("Could not kill %s directly: %s", key, error)

    try:
        return process.wait(timeout=KILL_GRACE)
    except subprocess.TimeoutExpired:
        logger.warning("Command %s survived being killed", key)
        return None


def _taskkill(pid: int) -> None:
    """
    Windows' only way to end a process tree from outside it.

    Given its own bounded wait, because the thing being fixed here is a
    command that would not finish - a kill that hangs would reproduce the
    bug it is there to prevent. Its own output goes nowhere: it is not
    interesting when it works, and when it fails the caller already reports
    that the process survived.
    """

    if not shutil.which("taskkill"):
        logger.debug("taskkill is not available; killing the process alone")
        return

    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=KILL_GRACE,
        )

    except (OSError, subprocess.SubprocessError) as error:
        logger.debug("taskkill on %s did not complete: %s", pid, error)


def _text(stream, limit: int) -> tuple[str, bool]:
    """
    What the command printed, bounded, with line structure kept.

    Deliberately not `apps.py::_tail`, which collapses everything onto one
    line. That is right for a launch failure, where the only question is
    what went wrong, and wrong here: a model reading `git status` output
    needs to know which line each filename was on.

    Only read after the process is gone, so there is never a question of
    waiting for output that will not come.
    """

    try:
        stream.seek(0)
        raw = stream.read(limit + 1)
    except (OSError, ValueError):
        return "", False

    if not raw:
        return "", False

    if isinstance(raw, bytes):
        text = raw.decode("utf-8", "replace")
    else:
        text = str(raw)

    truncated = len(text) > limit

    if truncated:
        text = text[:limit]

    # Normalise line endings and drop trailing blank space, so the output
    # embedded in a prompt does not carry a stray \r into every line.
    lines = [line.rstrip() for line in text.splitlines()]

    while lines and not lines[-1]:
        lines.pop()

    return "\n".join(lines), truncated


def _child_environment() -> dict[str, str]:
    """
    Aura's environment with the credentials taken out.

    Section 30 says a key must never appear in chat history, and this
    tool's output goes to a model. `core/credentials.py` puts stored keys
    into `os.environ` deliberately, so a child inherits them and any
    command that prints its environment would print them into the
    transcript.

    The names Aura sets are imported rather than listed, so that a
    provider added to `PROVIDER_KEYS` later is covered without an edit
    here. The pattern sweep on top of that is for the owner's *other*
    secrets - a `GITHUB_TOKEN` in the transcript is no better than a
    Gemini key.
    """

    environment = dict(os.environ)

    for name in _credential_names(environment):
        environment.pop(name, None)

    return environment


def _credential_names(environment: dict) -> list[str]:
    """Which variables must not reach a child. Names only, never values."""

    names = set()

    for name in _aura_credential_names():
        if name in environment:
            names.add(name)

    for name in environment:
        if name in CREDENTIAL_EXCEPTIONS:
            continue

        if CREDENTIAL_NAME.search(name):
            names.add(name)

    return sorted(names)


def _aura_credential_names() -> tuple[str, ...]:
    """
    The environment variables Aura itself writes secrets into.

    Imported at call time rather than at module import, because
    `brain.router` pulls in the provider stack and this module is loaded by
    the tool factory during startup. A failure to read the table must not
    stop the tool from existing - the pattern sweep still covers every
    name in it, since all of them contain KEY or TOKEN - so it degrades to
    a debug line rather than an exception.
    """

    names: list[str] = []

    try:
        from brain.router import PROVIDER_KEYS

        names.extend(str(value) for value in PROVIDER_KEYS.values() if value)

    except Exception as error:
        logger.debug("Could not read the provider key table: %s", error)

    try:
        from core.credentials import SECRET_ENV_VARS

        names.extend(str(name) for name in SECRET_ENV_VARS if name)

    except Exception as error:
        logger.debug("Could not read the secret variable list: %s", error)

    return tuple(names)


# ----------------------------------------------------------------------
# Reading the owner's config
# ----------------------------------------------------------------------


def _normalise(commands: dict | None) -> dict[str, Command]:
    """
    Turn config into `{name: Command}`, dropping what cannot be trusted.

    Every rejection is logged at warning level with the name and the
    reason. Section 2 forbids silently mutating the owner's configuration,
    and dropping a command the owner wrote is a strong action - so it
    happens only where the alternative is running something different from
    what they asked for, and it always says so.

    A bare list is accepted as shorthand for `{argv: [...]}`, the same
    tolerance `applications` has for a plain string.
    """

    if not commands:
        return {}

    if not isinstance(commands, dict):
        logger.warning(
            "tools.commands should be a mapping of name to command, not %s; "
            "no commands are available",
            type(commands).__name__,
        )
        return {}

    declared: dict[str, Command] = {}

    for raw_name, entry in commands.items():

        name = str(raw_name).strip().lower()

        if not name:
            logger.warning("Ignoring a command with no name")
            continue

        command = _one(name, entry)

        if command is not None:
            declared[name] = command

    return declared


def _one(name: str, entry) -> Command | None:
    """One config entry as a Command, or None with a warning saying why."""

    if isinstance(entry, (list, tuple)):
        entry = {"argv": list(entry)}

    elif isinstance(entry, str):
        # Routed into `_argv` rather than rejected here, so that a plain
        # `name: "git status"` - the single most likely way to write this
        # wrong, because `applications` accepts exactly that - gets the
        # explanation of why a command line cannot be split, instead of a
        # message about mappings that does not say what to do.
        entry = {"argv": entry}

    if not isinstance(entry, dict):
        logger.warning(
            "Ignoring command %s: expected an argv list or a mapping, got %s",
            name,
            type(entry).__name__,
        )
        return None

    argv = _argv(name, entry.get("argv"))

    if argv is None:
        return None

    parameters = _parameters(name, entry.get("parameters"))

    command = Command(
        name=name,
        argv=argv,
        description=str(entry.get("description") or "").strip(),
        parameters=parameters,
        timeout=seconds_or(
            entry.get("timeout"), DEFAULT_COMMAND_TIMEOUT
        ),
        cwd=str(entry.get("cwd") or "").strip(),
    )

    _warn_about(command, parameters)

    return command


def _argv(name: str, argv) -> tuple[str, ...] | None:
    """
    The argv list, or None with a warning.

    A string is refused rather than split. Splitting it would be this
    module writing a command line out of text, which is the thing Section
    24 is about - and the owner would have no way to see where the split
    landed.
    """

    if isinstance(argv, str):
        logger.warning(
            "Ignoring command %s: argv must be a list of separate "
            "arguments, not one string. Write [\"git\", \"status\"] rather "
            "than \"git status\" - a single string would have to be split "
            "by guessing, and the guess is what makes a command line "
            "dangerous.",
            name,
        )
        return None

    if not isinstance(argv, (list, tuple)) or not argv:
        logger.warning(
            "Ignoring command %s: it has no argv list to run", name
        )
        return None

    elements: list[str] = []

    for element in argv:

        if isinstance(element, bool) or not isinstance(
            element, (str, int, float)
        ):
            logger.warning(
                "Ignoring command %s: argv contains %s, which is not text "
                "or a number",
                name,
                type(element).__name__,
            )
            return None

        elements.append(str(element))

    if not elements[0].strip():
        logger.warning(
            "Ignoring command %s: the program to run is empty", name
        )
        return None

    if SLOT.search(elements[0]):
        # The program itself is never fillable. A slot here would let the
        # model choose what runs, which is precisely what the owner
        # declaring a name is meant to prevent.
        logger.warning(
            "Ignoring command %s: the program to run contains a fillable "
            "value (%s). The program must be fixed by you, or the model "
            "would be choosing what runs.",
            name,
            elements[0],
        )
        return None

    return tuple(elements)


def _parameters(name: str, parameters) -> dict[str, str]:
    """
    Slot descriptions, which are documentation and nothing more.

    What a command accepts is decided by its argv, not by this mapping, so
    a malformed `parameters` block costs the model a description and never
    changes what runs. It is dropped with a warning rather than refusing
    the command.
    """

    if not parameters:
        return {}

    if not isinstance(parameters, dict):
        logger.warning(
            "Command %s: parameters should be a mapping of value name to "
            "description, not %s; descriptions are ignored",
            name,
            type(parameters).__name__,
        )
        return {}

    return {
        str(key): str(value or "").strip()
        for key, value in parameters.items()
    }


def _warn_about(command: Command, parameters: dict[str, str]) -> None:
    """
    Say what looks wrong without refusing to run it.

    Each of these is a case where the owner may know exactly what they are
    doing, so Section 2 applies: warn, do not override. They are separated
    from the refusals above for that reason - a refusal is for when running
    the command would do something other than what was written.
    """

    slots = command.slots

    described = set(parameters)

    undescribed = [slot for slot in slots if slot not in described]

    if undescribed:
        logger.warning(
            "Command %s: %s has no description, so the model has to guess "
            "what to put there",
            command.name,
            ", ".join(undescribed),
        )

    unused = [key for key in sorted(described) if key not in slots]

    if unused:
        logger.warning(
            "Command %s: %s is described but never used in the argv, so "
            "nothing will be filled in with it",
            command.name,
            ", ".join(unused),
        )

    stem = Path(command.argv[0]).stem.lower()

    if slots and stem in SHELL_STEMS:
        # Not a refusal. An owner may genuinely want a declared PowerShell
        # one-liner with one value in it, and Section 2 says that is their
        # decision to make. But it is the one declaration where the model's
        # value reaches something whose job is to interpret text, so it is
        # said as loudly as a warning can be said.
        logger.warning(
            "Command %s runs %s, which interprets whatever text it is "
            "given, AND has fillable values (%s). Anything the model puts "
            "in them will be interpreted as %s code, not passed through as "
            "data. This is allowed because you declared it, but it gives up "
            "most of what naming a command protects.",
            command.name,
            command.argv[0],
            ", ".join(slots),
            stem,
        )

    if command.timeout == 0:
        logger.warning(
            "Command %s has no time limit (timeout: 0), so a command that "
            "never finishes will hold a thread until Aura restarts",
            command.name,
        )

    # Valid slots have to come out first, or every correctly written
    # `{pattern}` would be reported as a misspelled one.
    near = [
        element
        for element in command.argv
        if NEAR_SLOT.search(SLOT.sub("", element))
    ]

    if near:
        logger.warning(
            "Command %s: %r looks like it was meant to be a value but is "
            "not one - a value name is written {name}, with no spaces or "
            "punctuation inside the braces. As written, the braces will be "
            "passed to the program as they are",
            command.name,
            near[0],
        )


__all__ = [
    "DEFAULT_COMMAND_TIMEOUT",
    "MAX_OUTPUT",
    "Command",
    "RunCommandTool",
]
