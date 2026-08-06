"""
Tool base types.

A tool is a capability Aura can be granted: reading a file, opening an
application, later moving the mouse. Every tool declares what it is,
what it needs, and how much damage it could do.

The risk level is not decoration. ToolExecutor refuses to run anything
above SAFE without an explicit approval, so a tool author cannot opt out
of the permission system by forgetting to ask for it - the default for
an unlabelled tool is the strictest one that still runs.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class ToolRisk(str, Enum):
    """
    How much a tool can affect the user's machine.

    SAFE       no side effects outside Aura   (what time is it)
    SENSITIVE  reads user data                (read a file)
    DANGEROUS  changes the machine or world   (launch an app, click)
    """

    SAFE = "safe"
    SENSITIVE = "sensitive"
    DANGEROUS = "dangerous"


@dataclass(frozen=True)
class ToolResult:
    """
    What happened when a tool ran.

    Tools report failure by returning `ok=False`, not by raising. The
    executor converts stray exceptions into this shape anyway, so a
    caller never has to wrap a tool call in try/except.
    """

    ok: bool
    output: str = ""
    error: str = ""
    tool: str = ""

    def __bool__(self) -> bool:
        return self.ok

    def render(self) -> str:
        """One line summary, for prompts and logs."""

        if self.ok:
            return self.output

        return f"error: {self.error}"


def ok(output: str = "", tool: str = "") -> ToolResult:
    return ToolResult(ok=True, output=output, tool=tool)


def fail(error: str, tool: str = "") -> ToolResult:
    return ToolResult(ok=False, error=error, tool=tool)


@dataclass(frozen=True)
class Parameter:
    """One argument a tool accepts."""

    name: str
    description: str = ""
    required: bool = True


class Tool(ABC):
    """
    Base class for every tool.

    Subclasses set `name`, `description` and `risk`, then implement
    `execute`. `execute` may return a ToolResult or a plain string; the
    executor normalises both.
    """

    name: str = ""
    description: str = ""
    risk: ToolRisk = ToolRisk.DANGEROUS

    # A tuple, not a list: this is a class attribute shared by every
    # instance, and an immutable default cannot be appended to by
    # accident from one instance and observed by another.
    parameters: tuple[Parameter, ...] = ()

    @abstractmethod
    def execute(self, **arguments):
        """Do the thing. Called only through ToolExecutor."""

        raise NotImplementedError

    # ------------------------------------------------------------------

    def describe(self) -> str:
        """
        Human readable signature.

        This is what gets shown to a model when tool calling is wired up,
        which is why it lists parameters rather than just the name.
        """

        parts = [f"{self.name}: {self.description}"]

        for parameter in self.parameters or []:

            flag = "" if parameter.required else " (optional)"

            parts.append(
                f"    - {parameter.name}{flag}: {parameter.description}"
            )

        return "\n".join(parts)

    def required_parameters(self) -> list[str]:

        return [
            parameter.name
            for parameter in (self.parameters or [])
            if parameter.required
        ]

    def __repr__(self) -> str:
        return f"<Tool {self.name} risk={self.risk.value}>"
