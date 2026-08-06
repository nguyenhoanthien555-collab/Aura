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
"""

import shutil
import subprocess

from core.logger import logger
from tools.base import Parameter, Tool, ToolRisk


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

    def execute(self, name: str) -> str:

        key = str(name).strip().lower()

        command = self.applications.get(key)

        if command is None:
            raise PermissionError(
                f"'{name}' is not an allowed application"
            )

        executable = shutil.which(command[0]) or command[0]

        logger.info("Launching application: %s", key)

        # shell=False, argv as a list: the arguments are the ones from
        # config, not from the caller.
        subprocess.Popen(
            [executable, *command[1:]],
            shell=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        return f"Opened {key}"


def _normalise(applications: dict | None) -> dict[str, list[str]]:
    """
    Turn config into `{nickname: [executable, *arguments]}`.

    Accepts a plain string or an argv list per entry. Anything else is
    dropped with a warning rather than coerced, because a half
    understood launch command is worse than a missing one.
    """

    result: dict[str, list[str]] = {}

    for key, value in (applications or {}).items():

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
