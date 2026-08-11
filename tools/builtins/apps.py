"""
Application launching.

The reference DANGEROUS tool, and the one that shows what "no direct
uncontrolled execution" means in practice.

It takes a nickname, not a command. The mapping from nickname to
executable lives in config, written by the user:

    applications:
      notepad: notepad.exe
      browser: ["chrome.exe", "--new-window"]

A caller can ask for "browser". It cannot ask for "cmd.exe /c del",
because the string it supplies is only ever used as a dictionary key -
it is never parsed, never joined into a command line, and never reaches
a shell. Adding a program is a deliberate edit to a config file.

It is also where Aura's central promise is either kept or broken. This
tool used to end with `Popen(...)` followed unconditionally by
`return f"Opened {key}"` (AURA-P0-003), which meant:

    * an executable that does not exist on this machine still reported
      "Opened chrome" - `shutil.which` fell back to the raw name, Popen
      raised, and even the exception was better than the alternative
    * a program that started and died immediately reported success, since
      Popen returns as soon as the process exists, not when it works

So nothing here says a thing happened without checking. The executable
is resolved before anything is spawned, the process is given a short
grace period to fall over, and a non-zero exit within it is reported as
a failure with the exit status and whatever the program said on stderr.

What is still honestly unknowable is stated as unknowable: that a process
is running is not proof that a window is in front of the user, and this
tool does not claim otherwise.
"""

from pathlib import Path
import shutil
import subprocess
import tempfile

from core.logger import logger
from tools.base import Parameter, Tool, ToolResult, ToolRisk, fail, ok


# How long to watch a freshly spawned process before calling it launched.
# Long enough to catch "exited instantly with status 1", short enough that
# a user waiting on a reply does not notice it. A GUI application is still
# running when this elapses, which is the successful case.
GRACE_SECONDS = 0.5

# Enough of stderr to diagnose a failed launch, not enough to flood a
# prompt with a stack trace from someone else's program.
MAX_STDERR = 500


class OpenApplicationTool(Tool):

    name = "open_application"
    description = "Launch one of the applications the user has allowed"
    risk = ToolRisk.DANGEROUS

    parameters = (
        Parameter(
            name="name",
            description="Nickname of an allowed application",
        ),
    )

    def __init__(self, applications: dict | None = None):

        self.applications = _normalise(applications)

    @property
    def available(self) -> list[str]:
        return sorted(self.applications)

    def describe(self) -> str:
        """Include the allowed nicknames, so a model cannot guess wrongly."""

        base = super().describe()

        if not self.applications:
            return f"{base}\n    (no applications configured)"

        return f"{base}\n    allowed: {', '.join(self.available)}"

    def execute(self, name: str) -> ToolResult:
        """
        Launch an allowed application, and report what actually happened.

        Returns a failed result rather than raising for anything about the
        launch itself - a missing executable, an OS refusal, an immediate
        non-zero exit - because those are outcomes the model has to be
        told about accurately, not surprises.

        An unknown nickname still raises. That is not a failed launch, it
        is a request for something the user never permitted, and it should
        read like the permission error it is.
        """

        key = str(name).strip().lower()

        command = self.applications.get(key)

        if command is None:
            raise PermissionError(
                f"'{name}' is not an allowed application"
            )

        program = command[0]

        executable = _resolve(program)

        if executable is None:
            # Resolved before spawning, not after. This is the case that
            # used to report success.
            logger.info("Cannot launch %s: %s not found", key, program)

            return fail(
                f"cannot open {key}: '{program}' was not found on this "
                f"machine. Nothing was launched.",
                tool=self.name,
            )

        logger.info("Launching application: %s", key)

        # A real temporary file rather than a pipe: nobody is going to sit
        # and read the other end of a pipe for a process that is meant to
        # keep running, and an unread pipe eventually blocks the child.
        errors = tempfile.TemporaryFile()

        try:
            # shell=False, argv as a list: the arguments are the ones from
            # config, not from the caller.
            process = subprocess.Popen(
                [executable, *command[1:]],
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=errors,
            )

        except OSError as error:
            errors.close()

            logger.info("Launch of %s refused by the OS: %s", key, error)

            return fail(
                f"cannot open {key}: {error}. Nothing was launched.",
                tool=self.name,
            )

        try:
            status = process.wait(timeout=GRACE_SECONDS)

        except subprocess.TimeoutExpired:
            # Still alive after the grace period, which for a normal
            # application is exactly what success looks like.
            errors.close()

            return ok(
                f"{key} was launched and is still running. The process "
                f"started successfully; whether its window is now in "
                f"front of the user cannot be confirmed from here.",
                tool=self.name,
            )

        detail = _tail(errors)

        errors.close()

        if status == 0:
            # Exited immediately and cleanly. Normal for a launcher that
            # hands off to an already running instance - and worth saying
            # so, rather than implying a window definitely appeared.
            return ok(
                f"{key} was launched. The process exited immediately with "
                f"status 0, which is normal for a launcher that hands off "
                f"to an already running application.",
                tool=self.name,
            )

        logger.info("Launch of %s exited with status %s", key, status)

        return fail(
            f"{key} did not open: '{program}' exited with status {status}"
            + (f" ({detail})" if detail else "")
            + ". Nothing is open.",
            tool=self.name,
        )


def _resolve(program: str) -> str | None:
    """
    The executable a configured name refers to, or None if there is none.

    `shutil.which` first, because a bare name like "notepad.exe" is meant
    to be found on PATH, and on Windows `which` is also what applies
    PATHEXT. A value with a path in it is not a PATH lookup, so it is
    checked as a file instead.

    Returning None rather than falling back to the raw name is the point:
    the fallback is what let an unlaunchable program report success.
    """

    found = shutil.which(program)

    if found:
        return found

    try:
        path = Path(program).expanduser()
    except (OSError, ValueError):
        return None

    try:
        if path.is_file():
            return str(path)
    except OSError:
        return None

    return None


def _tail(stream) -> str:
    """
    What the program said before it died, on one line and bounded.

    Only read after the process has exited, so there is no question of
    waiting on output that will never come.
    """

    try:
        stream.seek(0)
        raw = stream.read(MAX_STDERR)
    except (OSError, ValueError):
        return ""

    if not raw:
        return ""

    if isinstance(raw, bytes):
        text = raw.decode("utf-8", "replace")
    else:
        text = str(raw)

    return " ".join(text.split())


def _normalise(applications: dict | None) -> dict[str, list[str]]:
    """
    Turn config into `{nickname: [executable, *arguments]}`.

    Accepts a plain string or an argv list per entry. Anything else is
    dropped with a warning rather than coerced, because a half
    understood launch command is worse than a missing one.

    A non-mapping `applications:` section is dropped the same way, with a
    warning naming the type, because `applications: []` in config.yaml
    silently disabled this tool entirely (AURA-P0-004) - an empty list and
    an empty mapping are both falsy, so every `or {}` guard in the codebase
    read it as "none configured".
    """

    result: dict[str, list[str]] = {}

    if applications is None:
        return result

    if not isinstance(applications, dict):
        logger.warning(
            "applications must be a mapping of nickname to command, not "
            "%s - no applications configured",
            type(applications).__name__,
        )
        return result

    for key, value in applications.items():

        name = str(key).strip().lower()

        if not name:
            continue

        if isinstance(value, str):
            command = [value.strip()] if value.strip() else []

        elif isinstance(value, (list, tuple)):
            command = [str(part) for part in value if str(part).strip()]

        else:
            logger.warning("Ignoring application entry: %s", key)
            continue

        if command:
            result[name] = command

    return result
