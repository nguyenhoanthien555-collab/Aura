"""
Tool base types.

A tool is a capability Aura can be granted: reading a file, opening an
application, later moving the mouse. Every tool declares what it is,
what it needs, and how much damage it could do.

The risk level is not decoration. ToolExecutor refuses to run anything
above SAFE without an explicit approval, so a tool author cannot opt out
of the permission system by forgetting to ask for it - the default for
an unlabelled tool is the strictest one that still runs.

Two shapes exist for the same reason LLM and StreamingLLM both do: the
ABC is the convenience base class every builtin inherits from, and the
Protocol is the structural contract that lets a plain object with the
right attributes qualify without subclassing anything.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

from core.logger import logger


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

    `output` is the human/model-readable line. `data` is the structured
    payload behind it - result values, postconditions, verification
    evidence - for callers that need facts rather than prose (the agent
    runtime's structured envelopes, the CLI's --json mode). Empty for
    tools that have nothing structured to say; never a second, divergent
    account of the outcome.
    """

    ok: bool
    output: str = ""
    error: str = ""
    tool: str = ""
    capability: str = "unknown"
    authorization: str = "unknown"
    execution: str = "completed"
    data: dict = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.ok

    def render(self) -> str:
        """One line summary, for prompts and logs."""
        
        import json
        structured = {
            "success": self.ok,
            "capability": self.capability,
            "authorization": self.authorization,
            "execution": self.execution,
        }
        if self.ok:
            structured["result"] = self.output
        else:
            structured["reason"] = self.error
            
        return json.dumps(structured)


def ok(output: str = "", tool: str = "", capability: str = "unknown", authorization: str = "granted", execution: str = "completed") -> ToolResult:
    return ToolResult(ok=True, output=output, tool=tool, capability=capability, authorization=authorization, execution=execution)


def fail(error: str, tool: str = "", capability: str = "unknown", authorization: str = "granted", execution: str = "not_attempted") -> ToolResult:
    return ToolResult(ok=False, error=error, tool=tool, capability=capability, authorization=authorization, execution=execution)


@dataclass(frozen=True)
class Parameter:
    """One argument a tool accepts."""

    name: str
    description: str = ""
    required: bool = True

    # The JSON Schema type of the argument, for native function calling.
    # A string default keeps every existing declaration valid: most tool
    # arguments are strings, and the schema exporter (tools/schema.py)
    # reads this rather than guessing from the description text.
    type: str = "string"


@runtime_checkable
class ToolProtocol(Protocol):
    """
    Structural contract for anything that can act as a tool.

    A plain object with these three members qualifies - no subclassing,
    no import of this module at all. The ABC below is a convenience base
    class, not the interface; this is the interface, and it is what the
    registry and the executor type against.

    Three members, and the shortness is the design. Every name here is a
    constraint on every tool that will ever exist, including ones written
    outside this repository, so the list is what the framework genuinely
    reads:

        name       the registry key, and what appears in logs and events
        risk       gate 4 - what approval this call needs
        execute    gate 5 - the call itself

    Everything else a tool may offer is optional by absence, looked up
    with getattr where it is used, exactly as `set_pacing` is on a TTS
    provider and `stop` is on an audio player:

        description           read by describe_tool()
        parameters            read by describe_tool()
        describe()            preferred over describe_tool() when present
        required_parameters() consulted by gate 5 when present
        timeout               overrides the policy timeout when present
        verify()              re-asked after a successful execute()

    `verify()` is the one Section 11 adds, and it is optional for a
    reason. It re-asks the condition the call was meant to establish -
    `remember` reads the fact back out - and the executor downgrades a
    success it denies. A tool whose `execute` already proves what it
    claims needs none: `open_application` resolves the executable before
    spawning and watches the process through a grace period, and its
    postcondition cannot honestly be re-asked afterwards because the
    evidence is gone. Absence means "execute already told the whole
    truth", never "unverified".

    runtime_checkable, so the registry can reject a malformed tool at the
    boundary rather than failing halfway through a call. isinstance only
    - a Protocol with non-method members cannot be used with issubclass,
    and it checks that the attributes exist, never their types.
    """

    name: str
    risk: ToolRisk

    def execute(self, **arguments):
        """Do the thing. Called only through ToolExecutor."""
        ...


def describe_tool(tool) -> str:
    """
    A tool's signature, whether or not it inherits `describe`.

    The Protocol does not require `describe`, so the registry cannot
    assume it. A tool that has one knows itself best and is asked first;
    anything else gets the name and description the framework can see.
    """

    own = getattr(tool, "describe", None)

    if callable(own):
        try:
            return own()
        except Exception as error:
            logger.debug("Tool %r could not describe itself: %s", tool, error)

    name = getattr(tool, "name", "") or "unnamed"
    description = getattr(tool, "description", "") or ""

    return f"{name}: {description}".rstrip(": ")


class Tool(ABC):
    """
    Base class for every tool.

    Subclasses set `name`, `description` and `risk`, then implement
    `execute`. `execute` may return a ToolResult or a plain string; the
    executor normalises both.

    Convenience, not obligation. Everything the framework requires is in
    ToolProtocol above, so a tool that would rather not inherit anything
    does not have to; what this class adds is sensible defaults and the
    parameter handling most tools would otherwise write themselves.
    """

    name: str = ""
    description: str = ""
    risk: ToolRisk = ToolRisk.DANGEROUS
    capability: str = None

    # A tuple, not a list: this is a class attribute shared by every
    # instance, and an immutable default cannot be appended to by
    # accident from one instance and observed by another.
    parameters: tuple[Parameter, ...] = ()

    # How long this tool may take, when the policy's limit is wrong for
    # it. None defers to the policy, which is what almost every tool
    # wants. 0 means unbounded, for the rare tool that legitimately
    # blocks and gains nothing from a deadline it cannot enforce.
    timeout: float | None = None

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

