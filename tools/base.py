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
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol, runtime_checkable

from core.logger import logger
from tools.outcome import (
    SideEffect,
    ToolErrorCategory,
    ToolStatus,
    category_for_code,
    evidence_state,
    retryability_of,
)


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

    Phase 3 adds the canonical outcome contract on top, without moving
    the original fields: `status` is the ToolStatus vocabulary from
    tools/outcome.py, `error_code` its machine-readable error code,
    `evidence` the tuple of Evidence about what actually happened, and
    `execution_id`/`started_at`/`completed_at` the execution identity the
    diagnostics trace correlates. `side_effect` is the tool's declared
    SideEffect class, which drives the derived retryability.

    The one invariant enforced here rather than hoped for: `ok` is
    reconciled FROM `status`, and only SUCCESS may produce `ok=True`.
    An UNKNOWN outcome therefore cannot be read as a success by any
    construction path, however it was built.
    """

    ok: bool
    output: str = ""
    error: str = ""
    tool: str = ""
    capability: str = "unknown"
    authorization: str = "unknown"
    execution: str = "completed"
    data: dict = field(default_factory=dict)
    status: str = ""
    error_code: str = ""
    evidence: tuple = ()
    execution_id: str = ""
    started_at: str = ""
    completed_at: str = ""
    side_effect: str = ""

    def __post_init__(self):

        # Reconcile the two vocabularies. An explicit status always wins,
        # because it is the richer fact; only SUCCESS maps onto ok=True,
        # so a result cannot be both UNKNOWN and truthy. A result built
        # by the old two-argument shape (ok, error) gets its status
        # derived rather than left empty - downstream readers may rely on
        # the field always naming something.
        if self.status:
            canonical = ToolStatus(self.status)
            if canonical.ok != self.ok:
                object.__setattr__(self, "ok", canonical.ok)
        else:
            object.__setattr__(
                self,
                "status",
                (ToolStatus.SUCCESS if self.ok else ToolStatus.FAILED).value,
            )

        # A completed execution is stamped when the creator did not.
        if not self.completed_at:
            object.__setattr__(
                self,
                "completed_at",
                datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            )

    def __bool__(self) -> bool:
        return self.ok

    @property
    def status_enum(self) -> ToolStatus:

        return ToolStatus(self.status)

    @property
    def error_category(self) -> ToolErrorCategory:
        """
        The category of this failure, or UNKNOWN for a success.

        Derived from the code when the code is known; a success has no
        error, and pretending otherwise would be a second fabrication.
        """

        return category_for_code(self.error_code)

    @property
    def evidence_summary(self) -> str:
        """NONE / UNVERIFIED / VERIFIED / CONTRADICTED, one word."""

        return evidence_state(self.evidence)

    @property
    def retryability(self):
        """
        Whether re-issuing this call is safe, derived - never asserted.

        Reads (status, side_effect) through `retryability_of`, so the
        retry decision lives in exactly one place. An undeclared side
        effect reads as UNKNOWN, which no auto-retry may treat as a yes.
        """

        return retryability_of(
            self.status_enum,
            SideEffect(self.side_effect or SideEffect.UNKNOWN.value),
        )

    @property
    def structured(self) -> dict:
        """
        The machine-readable view, for envelopes, traces and --json.

        Present fields only: an execution with no id should not appear
        to have one, and a reader can tell "not claimed" from "claimed
        empty" only if the absent case is actually absent.
        """

        payload: dict = {
            "ok": self.ok,
            "status": self.status,
            "tool": self.tool,
            "capability": self.capability,
            "authorization": self.authorization,
            "execution": self.execution,
        }

        if self.error:
            payload["error"] = self.error

        if self.error_code:
            payload["error_code"] = self.error_code
            payload["error_category"] = self.error_category.value

        if self.evidence:
            payload["evidence"] = self.evidence_summary
            payload["evidence_items"] = [
                item.as_dict() for item in self.evidence
            ]

        if self.execution_id:
            payload["execution_id"] = self.execution_id

        if self.started_at:
            payload["started_at"] = self.started_at
            payload["completed_at"] = self.completed_at

        if self.side_effect:
            payload["side_effect"] = self.side_effect

        payload["retryable"] = self.retryability.value

        if self.data:
            payload["data"] = self.data

        return payload

    def render(self) -> str:
        """One line summary, for prompts and logs."""
        
        import json
        structured = {
            "success": self.ok,
            "status": self.status,
            "capability": self.capability,
            "authorization": self.authorization,
            "execution": self.execution,
        }
        if self.error_code:
            structured["error_code"] = self.error_code
            structured["error_category"] = self.error_category.value
        if self.evidence:
            structured["evidence"] = self.evidence_summary
        if self.execution_id:
            structured["execution_id"] = self.execution_id
        structured["retryable"] = self.retryability.value
        if self.ok:
            structured["result"] = self.output
        else:
            structured["reason"] = self.error
            
        return json.dumps(structured)


def ok(output: str = "", tool: str = "", capability: str = "unknown", authorization: str = "granted", execution: str = "completed", status: str = "", side_effect: str = "") -> ToolResult:
    return ToolResult(ok=True, output=output, tool=tool, capability=capability, authorization=authorization, execution=execution, status=status or ToolStatus.SUCCESS.value, side_effect=side_effect)


def fail(error: str, tool: str = "", capability: str = "unknown", authorization: str = "granted", execution: str = "not_attempted", status: str = "", error_code: str = "", side_effect: str = "", evidence: tuple = ()) -> ToolResult:
    return ToolResult(ok=False, error=error, tool=tool, capability=capability, authorization=authorization, execution=execution, status=status or ToolStatus.FAILED.value, error_code=error_code, side_effect=side_effect, evidence=evidence)


# Keys the executor folds into `data` for envelope callers. The model
# reads these same facts from the serializer's own structured lines, so
# the DATA line filters them out - no duplicated account, no execution
# internals in the prompt.
_CONTRACT_DATA_KEYS = frozenset({
    "status",
    "retryable",
    "evidence",
    "evidence_items",
    "side_effect",
    "execution_id",
    "error_code",
    "error_category",
    "started_at",
    "completed_at",
})


def serialize_for_model(result: "ToolResult") -> str:
    """
    The deterministic serialization layer between ToolResult and the model.

    Fixed keys, fixed order, one line each - the same outcome always
    renders identically, so a model that learned to read one result can
    read them all. Status first, because it is the fact every other line
    is read in the light of; evidence and retry state next, because they
    are what the response contract branches on; the error with its code
    and category, so a capability failure never has to be recovered from
    prose; then the tool's own output or data.

    The OUTCOME line keeps the established success and failure sentences
    on purpose: models are already calibrated to them, and the structured
    lines above say the same thing a machine could branch on.

    No secrets and no internals: arguments never appear here (they came
    from the model in the first place), and diagnostics fields the model
    cannot act on - timestamps, execution ids - stay out of the prompt.
    """

    lines = [
        f"STATUS: {result.status}",
        f"TOOL: {result.tool or 'unknown'}",
        f"EVIDENCE: {result.evidence_summary}",
        f"RETRY: {result.retryability.value}",
    ]

    if not result.ok:

        category = result.error_category.value
        reason = result.error or "no reason given"

        lines.append(f"OUTCOME: {result.tool or 'tool'} FAILED: {reason}")
        lines.append(
            f"ERROR: {result.error_code or 'UNKNOWN'}/{category}: {reason}"
        )

        if result.status_enum is ToolStatus.UNKNOWN:
            lines.append(
                "OUTCOME UNVERIFIED: the runtime cannot establish whether "
                "this happened. Say so; do not claim it succeeded."
            )
        else:
            lines.append(
                "This did not happen. Tell the user it failed, and why."
            )

    else:

        output = result.output or "(nothing)"

        lines.append(
            f"OUTCOME: {result.tool or 'tool'} ran successfully. "
            f"It returned: {output}"
        )

        if result.data:
            import json

            # The executor folds the contract keys into `data` for
            # envelope callers; the model already reads them from the
            # structured lines above, so the DATA line carries only what
            # the tool itself produced - and never execution internals.
            payload = {
                key: value
                for key, value in result.data.items()
                if key not in _CONTRACT_DATA_KEYS
            }

            if payload:
                try:
                    lines.append(f"DATA: {json.dumps(payload, default=str)}")
                except (TypeError, ValueError):
                    lines.append("DATA: (unserializable)")

        if result.evidence_summary in ("UNVERIFIED", "NONE"):
            lines.append(
                "NOTE: success is unverified - the call returned but "
                "nothing confirmed the result. Do not overstate it."
            )

    return "\n".join(lines)


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

    # The retry question, distinct from `risk` (the permission question).
    # UNKNOWN is the honest default and is treated as unsafe to repeat:
    # an unlabelled tool gets the strictest reading that still runs, the
    # same rule `risk` follows.
    side_effect: SideEffect = SideEffect.UNKNOWN

    # The declared shape of a successful result's `data`, as JSON Schema.
    # None (the default) means undeclared, and output validation is
    # skipped - the executor will not invent a schema a tool never stated.
    output_schema: dict | None = None

    # Version of this tool's contract, for registry and MCP-style export.
    version: str = "1.0"

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

