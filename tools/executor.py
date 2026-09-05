"""
Tool executor.

The only place in Aura where a tool actually runs, which makes it the
only place permission has to be enforced.

Every call passes five gates before anything executes:

    1. tools enabled at all              (off by default)
    2. the tool is registered
    3. the tool name is on the allow list (empty by default)
    4. its risk level is auto approved, or a human approved this call
    5. its arguments are plain data of the shape it declared

Gate 4 defaults to refusal. With no confirmation callback wired up, a
SENSITIVE or DANGEROUS tool cannot run - not because it fails, but
because nothing exists that could say yes. A model that asks Aura to
delete a directory gets a denial, not a directory.

Past the gates there is one more bound, and it is not a permission
question: a tool that runs forever stops the conversation rather than
failing it, so every call is given a deadline. What that can and cannot
do is documented in tools/timeout.py, and the short version is that the
wait is bounded, not the tool.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Callable

from core.ids import new_id
from core.logger import logger
from core.trace import emit_trace
from events.types import ToolCompletedEvent, ToolInvokedEvent
from tools.base import ToolProtocol, ToolResult, ToolRisk, describe_tool, fail, ok
from tools.outcome import (
    CODE_CONFIRMATION_REQUIRED,
    CODE_INTERNAL,
    CODE_INVALID_ARGUMENTS,
    CODE_NOT_ALLOWED,
    CODE_NOT_IMPLEMENTED,
    CODE_OUTPUT_SCHEMA,
    CODE_TIMEOUT,
    CODE_TOOL_ERROR,
    CODE_TOOL_NOT_FOUND,
    CODE_TOOLS_DISABLED,
    CODE_UNVERIFIED,
    Evidence,
    EvidenceKind,
    SideEffect,
    ToolStatus,
    retryability_of,
)
from tools.registry import ToolRegistry
from tools.schema import matches_type, validate_output
from tools.timeout import (
    DEFAULT_TOOL_TIMEOUT,
    ToolTimeout,
    call_with_timeout,
    seconds_or,
)


# Values a tool argument is allowed to be. Anything else - a callable, an
# open file, a live session - is rejected before it reaches a tool.
PRIMITIVES = (str, int, float, bool, type(None))

MAX_ARGUMENT_DEPTH = 3
MAX_OUTPUT = 4000


Confirm = Callable[[ToolProtocol, dict], bool]


@dataclass
class ToolPolicy:
    """
    What is permitted, expressed as data so it can come from config.

    Deliberately closed by default: `enabled` is False and `allowed` is
    empty, so a fresh install grants nothing at all. Turning tools on is
    a decision the user makes twice - once for the system, once per tool.
    """

    enabled: bool = False
    allowed: frozenset[str] = frozenset()
    auto_approve: frozenset[ToolRisk] = field(
        default_factory=lambda: frozenset({ToolRisk.SAFE})
    )
    timeout: float = DEFAULT_TOOL_TIMEOUT

    @classmethod
    def from_config(cls, config: dict | None) -> "ToolPolicy":

        config = config or {}

        risks = set()

        for value in config.get("auto_approve") or ["safe"]:
            try:
                risks.add(ToolRisk(str(value).strip().lower()))
            except ValueError:
                logger.warning("Unknown tool risk level: %s", value)

        return cls(
            enabled=bool(config.get("enabled", False)),
            allowed=frozenset(
                str(name).strip()
                for name in (config.get("allowed") or [])
                if str(name).strip()
            ),
            auto_approve=frozenset(risks),
            timeout=seconds_or(config.get("timeout"), DEFAULT_TOOL_TIMEOUT),
        )


from core.capabilities import resolve_capability, CapabilityState, registry as capability_registry

class ToolExecutor:

    def __init__(
        self,
        registry: ToolRegistry | None = None,
        policy: ToolPolicy | None = None,
        events=None,
        confirm: Confirm | None = None,
    ):

        self.registry = registry or ToolRegistry()
        self.policy = policy or ToolPolicy()
        self.events = events
        self.confirm = confirm

        self.history: list[tuple[str, bool]] = []

    # ------------------------------------------------------------------
    # Permission
    # ------------------------------------------------------------------

    def available(self) -> list[str]:
        """Registered tools that policy would currently allow."""

        if not self.policy.enabled:
            return []

        return [
            name
            for name in self.registry.names()
            if name in self.policy.allowed
            and getattr(self.registry.get(name), "capability", None)
            and resolve_capability(
                getattr(self.registry.get(name), "capability", None)
            ) == CapabilityState.AVAILABLE
        ]

    def catalogue(self) -> str:
        """
        The available tools, described, for a prompt.

        Modified: Now strictly grounds the LLM by explicitly noting if a tool's underlying 
        capability is currently BLOCKED or UNHEALTHY, so the LLM will not hallucinate 
        that it can use it, but will instead see exactly WHY it cannot be used.
        """

        if not self.policy.enabled:
            return ""

        lines = []
        for name in self.registry.names():
            if name not in self.policy.allowed:
                continue

            tool = self.registry.get(name)
            if tool is None:
                continue

            capability_id = getattr(tool, "capability", None)
            if not capability_id:
                continue
            cap_state = resolve_capability(capability_id)
            
            description = describe_tool(tool)
            
            # The model receives executable tools only. The live inventory
            # endpoint/system context carries blocked reasons separately;
            # exposing a blocked schema invites the model to call it.
            if cap_state == CapabilityState.AVAILABLE:
                lines.append(description)
                
        return "\n".join(lines)

    def check(self, name: str) -> str:
        """
        Why this tool may not run, or "" when it may.

        Separated from `execute` so a UI can grey out a button without
        attempting the call.
        """

        if not self.policy.enabled:
            return "tool use is disabled"

        tool = self.registry.get(name)

        if tool is None:
            return f"unknown tool: {name}"

        if name not in self.policy.allowed:
            return f"tool not allowed by policy: {name}"

        return ""

    def _approved(self, tool: ToolProtocol, arguments: dict) -> bool:
        """
        Whether this specific call may proceed.

        Auto approval covers whole risk levels. Anything outside it needs
        a live yes from the confirmation callback, and no callback means
        no.
        """

        if tool.risk in self.policy.auto_approve:
            return True

        if self.confirm is None:
            return False

        try:
            return bool(self.confirm(tool, arguments))
        except Exception as error:
            logger.warning("Tool confirmation failed: %s", error)
            return False

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(
        self,
        name: str,
        arguments: dict | None = None,
    ) -> ToolResult:
        """
        Run a tool by name. Never raises.

        Both the request and its outcome are published, so a denied call
        is as visible on the bus as a successful one.
        """

        arguments = arguments or {}
        started_at = datetime.now(timezone.utc).isoformat(
            timespec="milliseconds"
        )

        self._emit(
            ToolInvokedEvent(
                name=name,
                arguments=self._safe_arguments(arguments),
            )
        )

        reason = self.check(name)

        if reason:
            # The gate that refused decides the canonical status. Each
            # refusal reason is a different fact: a disabled subsystem is
            # policy, an unknown name is a malformed request, an unlisted
            # tool is policy. Nothing ran in any of them.
            if reason == "tool use is disabled":
                status, error_code = (
                    ToolStatus.DENIED.value,
                    CODE_TOOLS_DISABLED,
                )
            elif reason.startswith("unknown tool:"):
                status, error_code = (
                    ToolStatus.INVALID_ARGUMENTS.value,
                    CODE_TOOL_NOT_FOUND,
                )
            else:
                status, error_code = (
                    ToolStatus.DENIED.value,
                    CODE_NOT_ALLOWED,
                )

            return self._finish(
                name,
                fail(
                    reason,
                    tool=name,
                    execution="not_attempted",
                    status=status,
                    error_code=error_code,
                ),
                started_at,
            )

        tool = self.registry.get(name)
        capability_id = getattr(tool, "capability", None)
        if not capability_id:
            detail = (
                f"tool {name} has no registered capability; it is not "
                "implemented or registered"
            )
            return self._finish(name, ToolResult(
                ok=False,
                error=detail,
                tool=name,
                capability="",
                authorization="unknown",
                execution="not_attempted",
                status=ToolStatus.UNAVAILABLE.value,
                error_code=CODE_NOT_IMPLEMENTED,
                data={
                    "ok": False,
                    "tool": name,
                    "error": {"code": "NOT_IMPLEMENTED", "message": detail},
                    "capability": "",
                    "authorization": "unknown",
                    "execution": "not_attempted",
                },
            ), started_at)

        cap_state = resolve_capability(capability_id)
        if cap_state != CapabilityState.AVAILABLE:
            cap = capability_registry.get(capability_id)
            reason = (cap.discovery_metadata.get("state_reason", "") if cap else "")
            state_message = {
                CapabilityState.BLOCKED_PERMISSION: "permission denied for capability",
                CapabilityState.UNHEALTHY: "is currently unhealthy",
                CapabilityState.UNAVAILABLE: "currently unavailable",
                CapabilityState.UNKNOWN: "has unknown runtime state",
                CapabilityState.NOT_IMPLEMENTED: "is not implemented or registered",
            }.get(cap_state, f"is {cap_state.value.lower()}")
            if cap_state == CapabilityState.BLOCKED_PERMISSION:
                detail = f"permission denied for capability {capability_id}"
            else:
                detail = f"capability {capability_id} {state_message}"
            if reason:
                detail += f": {reason}"
            error_code = {
                CapabilityState.BLOCKED_PERMISSION: "BLOCKED_PERMISSION",
                CapabilityState.UNAVAILABLE: "CAPABILITY_UNAVAILABLE",
                CapabilityState.UNHEALTHY: "CAPABILITY_UNHEALTHY",
                CapabilityState.UNKNOWN: "CAPABILITY_UNKNOWN",
                CapabilityState.NOT_IMPLEMENTED: "NOT_IMPLEMENTED",
            }.get(cap_state, "CAPABILITY_UNAVAILABLE")
            # A missing permission is a DENIAL - a human must act, and
            # retrying cannot help. Every other blocked state is an
            # unavailability: the request was fine, the world is not
            # ready, and the device reconnecting may fix it outright.
            cap_status = (
                ToolStatus.DENIED.value
                if cap_state == CapabilityState.BLOCKED_PERMISSION
                else ToolStatus.UNAVAILABLE.value
            )
            return self._finish(name, ToolResult(
                ok=False,
                error=detail,
                tool=name,
                capability=capability_id,
                authorization=(
                    "missing"
                    if cap_state == CapabilityState.BLOCKED_PERMISSION
                    else "unknown"
                ),
                execution="not_attempted",
                status=cap_status,
                error_code=error_code,
                data={
                    "ok": False,
                    "tool": name,
                    "error": {"code": error_code, "message": detail},
                    "capability": capability_id,
                    "authorization": (
                        "missing"
                        if cap_state == CapabilityState.BLOCKED_PERMISSION
                        else "unknown"
                    ),
                    "execution": "not_attempted",
                },
            ), started_at)

        problem = self._validate(tool, arguments)

        if problem:
            return self._finish(
                name,
                fail(
                    problem,
                    tool=name,
                    capability=capability_id,
                    execution="not_attempted",
                    status=ToolStatus.INVALID_ARGUMENTS.value,
                    error_code=CODE_INVALID_ARGUMENTS,
                ),
                started_at,
            )

        if not self._approved(tool, arguments):
            return self._finish(
                name,
                fail(
                    f"permission denied for {name}",
                    tool=name,
                    capability=capability_id,
                    authorization="missing",
                    execution="not_attempted",
                    status=ToolStatus.DENIED.value,
                    error_code=CODE_CONFIRMATION_REQUIRED,
                ),
                started_at,
            )

        result = self._run(tool, arguments)
        
        # Inject capability context into normal returns. `replace`
        # re-runs __post_init__, so the status/ok reconciliation and the
        # completed_at stamp stay intact - rebuilding the dataclass by
        # hand here would have silently dropped every Phase 3 field.
        if isinstance(result, ToolResult):
            result = replace(
                result,
                capability=capability_id,
                authorization="granted",
                execution="completed" if result.ok else "failed",
            )

        result = self._verified(tool, arguments, result)

        # Output validation (Phase 3, section 13): a success whose data
        # violates the tool's own declared output schema is not a
        # success - the call ran, but its result cannot be trusted, and
        # that distinction is exactly what UNKNOWN exists to carry. An
        # empty payload counts as missing output: a tool that declared a
        # schema owes the shape it promised.
        if result.ok:
            try:
                mismatch = validate_output(tool, result.data)
            except ValueError as error:
                mismatch = ""
                logger.debug("Output schema unreadable for %s: %s", name, error)

            if mismatch:
                logger.info(
                    "Tool %s returned output its schema rejects: %s",
                    name,
                    mismatch,
                )
                result = replace(
                    result,
                    ok=False,
                    status=ToolStatus.UNKNOWN.value,
                    error_code=CODE_OUTPUT_SCHEMA,
                    error=(
                        f"{name} ran but its output did not match its "
                        f"declared schema: {mismatch}"
                    ),
                )

        return self._finish(name, result, started_at)

    def _run(self, tool: ToolProtocol, arguments: dict) -> ToolResult:

        name = getattr(tool, "name", "tool")

        try:
            result = call_with_timeout(
                lambda: tool.execute(**arguments),
                timeout=self._timeout_for(tool),
                name=name,
            )

        except ToolTimeout as error:
            # Not "the tool failed" but "we stopped waiting". Said
            # differently on purpose: the call may still be running, and
            # a user who sees "timed out" knows to raise the limit rather
            # than to go looking for a bug in the tool. Canonical status
            # TIMEOUT, whose whole meaning is "outcome genuinely unknown
            # - the wait is bounded, the tool is not".
            return fail(
                str(error),
                tool=name,
                execution="not_attempted",
                status=ToolStatus.TIMEOUT.value,
                error_code=CODE_TIMEOUT,
            )

        except Exception as error:
            logger.warning("Tool %s failed: %s", name, error)
            return fail(
                f"{type(error).__name__}: {error}",
                tool=name,
                execution="failed",
                status=ToolStatus.FAILED.value,
                error_code=CODE_TOOL_ERROR,
            )

        return self._normalise(tool, result)

    def _verified(
        self, tool: ToolProtocol, arguments: dict, result: ToolResult
    ) -> ToolResult:
        """
        Ask the tool's postcondition, and downgrade a success it denies.

        Section 11: verification "must not rely only on: the command
        executed without throwing". `_run` gives exactly that - a call
        that returned - so a tool may offer a `verify()` that re-asks the
        condition the call was supposed to establish, and a success it
        rejects is reported as the failure it is.

        Optional by absence, like `timeout` and `describe`: a tool with no
        `verify` is trusted at its word, and the Protocol stays three
        members wide (`test_the_protocol_stayed_narrow`). This is not the
        device verifier from `brain/recovery.py` - that reconciles actions
        a phone performed, reported back over the wire; this re-checks a
        local tool's own postcondition, and the two never see each other's
        work.

        Three deliberate limits:

          * A failure is never re-verified. There is no success to
            second-guess, and a postcondition asserted over a side effect
            that was never established would be checking the wrong thing.

          * A `verify` that raises fails closed. It gives precisely "it
            ran without throwing" - for the verify - which is the sentence
            Section 11 forbids trusting, so an unreadable postcondition is
            unverified, not confirmed.

          * `None` asserts nothing. A tool that has no postcondition to
            offer for these arguments returns None, and the result stands
            unchanged - absence of a check is not a failed check.
        """

        if not result.ok:
            return result

        check = getattr(tool, "verify", None)

        if not callable(check):
            return result

        name = getattr(tool, "name", "tool")

        try:
            verdict = check(**arguments)

        except Exception as error:
            logger.warning("Tool %s could not verify: %s", name, error)
            return fail(
                f"{name} ran but could not be verified: "
                f"{type(error).__name__}: {error}",
                tool=name,
                execution="failed",
                status=ToolStatus.FAILED.value,
                error_code=CODE_UNVERIFIED,
                evidence=(
                    Evidence(
                        EvidenceKind.POSTCONDITION,
                        source="verify",
                        verified=False,
                        detail=f"{type(error).__name__}: {error}",
                    ),
                ),
            )

        if verdict is None:
            # The tool asserts nothing; the result's evidence stays what
            # it was (see execute: a bare RETURN_VALUE), which is the
            # honest "it returned and nobody confirmed anything".
            return result

        if getattr(verdict, "ok", True):
            # A passed postcondition is real evidence, and attaching it
            # is what lets a caller read VERIFIED instead of guessing.
            return replace(
                result,
                evidence=result.evidence
                + (
                    Evidence(
                        EvidenceKind.POSTCONDITION,
                        source="verify",
                        verified=True,
                    ),
                ),
            )

        reason = str(getattr(verdict, "error", "") or "").strip()

        logger.info("Tool %s failed verification: %s", name, reason)

        return fail(
            reason or f"{name} ran but its result could not be verified",
            tool=name,
            execution="failed",
            status=ToolStatus.FAILED.value,
            error_code=CODE_UNVERIFIED,
            evidence=(
                Evidence(
                    EvidenceKind.POSTCONDITION,
                    source="verify",
                    verified=False,
                    detail=reason,
                ),
            ),
        )

    def _timeout_for(self, tool: ToolProtocol) -> float:
        """
        How long this particular tool may take.

        The policy sets the limit; a tool may override it, because only
        the tool knows that it shells out to something slow. Optional by
        absence, like every other capability in this codebase - a tool
        without a `timeout` is not a broken tool.
        """

        own = getattr(tool, "timeout", None)

        if own is None:
            return self.policy.timeout

        return seconds_or(own, self.policy.timeout)

    @staticmethod
    def _normalise(tool: ToolProtocol, result) -> ToolResult:
        """Accept a ToolResult or a plain string from `execute`."""

        if isinstance(result, ToolResult):

            if len(result.output) > MAX_OUTPUT:
                # `replace`, not a hand-built ToolResult: truncation must
                # not strip the status, evidence or execution identity.
                return replace(
                    result,
                    output=result.output[:MAX_OUTPUT] + " ...(truncated)",
                    tool=tool.name,
                )

            return replace(result, tool=tool.name or result.tool)

        text = "" if result is None else str(result)

        return ok(text[:MAX_OUTPUT], tool=tool.name)

    # ------------------------------------------------------------------
    # Argument validation
    # ------------------------------------------------------------------

    def _validate(self, tool: ToolProtocol, arguments: dict) -> str:

        if not isinstance(arguments, dict):
            return "arguments must be a mapping"

        for key, value in arguments.items():

            if not isinstance(key, str):
                return "argument names must be strings"

            if not self._is_plain(value, MAX_ARGUMENT_DEPTH):
                return f"argument '{key}' is not plain data"

        # Phase 3, section 12: a malformed argument must never reach the
        # tool. Declared parameters are checked here for two things the
        # old gate let through - a name the tool never declared, and a
        # value of the wrong type - both of which previously became a
        # TypeError raised *inside* the tool and surfaced as a generic
        # execution failure. An undeclared parameter set checks nothing:
        # a tool that never said what it takes cannot be accused of
        # taking something it should not.
        declared = getattr(tool, "parameters", ()) or ()

        if declared:

            known = {
                parameter.name: getattr(parameter, "type", "string")
                for parameter in declared
            }

            for key, value in arguments.items():

                if key not in known:
                    return f"unknown argument: {key}"

                expected = known[key]

                if not matches_type(value, expected):
                    return (
                        f"argument '{key}' must be {expected}, "
                        f"got {type(value).__name__}"
                    )

        missing = [
            name
            for name in self._required(tool)
            if name not in arguments
        ]

        if missing:
            return f"missing arguments: {', '.join(missing)}"

        return ""

    @staticmethod
    def _required(tool: ToolProtocol) -> list[str]:
        """
        Which arguments this tool insists on.

        `required_parameters` is not on the Protocol, so a tool written
        against the interface alone may not have one. No declaration
        means no requirement, which is the honest reading: a tool that
        never said what it needs has not been checked, and it will raise
        a TypeError of its own if something is genuinely missing. That
        becomes a failed result like any other.
        """

        declared = getattr(tool, "required_parameters", None)

        if not callable(declared):
            return []

        try:
            return list(declared() or [])
        except Exception as error:
            logger.debug(
                "Tool %s could not list its parameters: %s",
                getattr(tool, "name", tool),
                error,
            )
            return []

    @classmethod
    def _is_plain(cls, value, depth: int) -> bool:
        """
        Reject anything that is not JSON-shaped data.

        Handing a tool a live object would let a caller smuggle in
        behaviour - a file handle, a session, a lambda - through what is
        supposed to be an inert argument.
        """

        if isinstance(value, PRIMITIVES):
            return True

        if depth <= 0:
            return False

        if isinstance(value, (list, tuple)):
            return all(cls._is_plain(item, depth - 1) for item in value)

        if isinstance(value, dict):
            return all(
                isinstance(key, str) and cls._is_plain(item, depth - 1)
                for key, item in value.items()
            )

        return False

    @classmethod
    def _safe_arguments(cls, arguments) -> dict:
        """A copy of the arguments that is safe to put inside an event."""

        if not isinstance(arguments, dict):
            return {}

        return {
            str(key): value if cls._is_plain(value, 1) else "<omitted>"
            for key, value in arguments.items()
        }

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def _finish(
        self,
        name: str,
        result: ToolResult,
        started_at: str = "",
    ) -> ToolResult:
        """
        Stamp the execution contract onto a finished call, then report it.

        This is the one place every execution passes, so it is where the
        mechanical bookkeeping lives: the side-effect class read from the
        tool's own declaration, the execution identity, the evidence a
        bare success actually has (a RETURN_VALUE - the call came back,
        nothing more), and the structured contract folded into `data` so
        envelope-style callers get the vocabulary without re-deriving it.

        Existing `data` entries win over the contract's: a device report
        that already carries a richer `error` dict is not flattened by
        the stamp. Finally the execution is traced - identifiers,
        statuses and durations only, never arguments or content - and a
        diagnostics failure cannot touch the result (emit_trace already
        swallows, but the lookup around it is guarded on its own).
        """

        tool = self.registry.get(name)

        side_effect = ""
        if tool is not None:
            declared = getattr(tool, "side_effect", None)
            if declared is not None:
                side_effect = getattr(declared, "value", "")

        stamps: dict = {}

        if side_effect and not result.side_effect:
            stamps["side_effect"] = side_effect

        if not result.execution_id:
            stamps["execution_id"] = new_id("exec")

        if started_at and not result.started_at:
            stamps["started_at"] = started_at

        if result.ok and not result.evidence:
            # The evidence of a success nobody verified: the call
            # returned. Named so it can never masquerade as a check.
            stamps["evidence"] = (
                Evidence(
                    EvidenceKind.RETURN_VALUE,
                    source="execute",
                    verified=None,
                ),
            )

        if stamps:
            result = replace(result, **stamps)

        if isinstance(result.data, dict):

            contract: dict = {
                "status": result.status,
                "retryable": result.retryability.value,
                "evidence": result.evidence_summary,
                "side_effect": result.side_effect,
                "execution_id": result.execution_id,
            }

            if result.error_code:
                contract["error_code"] = result.error_code
                contract["error_category"] = result.error_category.value

            result = replace(result, data={**contract, **result.data})

        self.history.append((name, result.ok))

        duration_ms = ""
        try:
            if result.started_at:
                started = datetime.fromisoformat(result.started_at)
                duration_ms = round(
                    (
                        datetime.now(timezone.utc) - started
                    ).total_seconds()
                    * 1000
                )
        except ValueError:
            duration_ms = ""

        emit_trace(
            "tool_execution",
            execution_id=result.execution_id,
            tool=name,
            status=result.status,
            duration_ms=duration_ms,
            error_code=result.error_code or "",
            error_category=(
                result.error_category.value if result.error_code else ""
            ),
            evidence=result.evidence_summary,
            retryable=result.retryability.value,
            side_effect=result.side_effect,
            capability=result.capability or "",
            verification=(
                "verified"
                if result.evidence_summary == "VERIFIED"
                else result.evidence_summary.lower()
            ),
        )

        self._emit(
            ToolCompletedEvent(
                name=name,
                ok=result.ok,
                detail=result.error if not result.ok else "",
            )
        )

        if not result.ok:
            logger.info("Tool %s refused or failed: %s", name, result.error)

        return result

    def _emit(self, event) -> None:

        if self.events is None:
            return

        try:
            self.events.publish(event)
        except Exception as error:
            # The tool still ran, and its result still returns to the
            # caller - a broken bus must not turn a successful call into
            # a failed one. But a subscriber that stopped seeing tool
            # events is otherwise indistinguishable from no tools being
            # run at all, so say so once per failure.
            logger.debug(
                "Tool event publish failed (%s): %s",
                type(event).__name__,
                error,
            )



