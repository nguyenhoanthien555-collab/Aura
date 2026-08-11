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

from dataclasses import dataclass, field
from typing import Callable

from core.logger import logger
from events.types import ToolCompletedEvent, ToolInvokedEvent
from tools.base import ToolProtocol, ToolResult, ToolRisk, describe_tool, fail, ok
from tools.registry import ToolRegistry
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
        ]

    def catalogue(self) -> str:
        """
        The available tools, described, for a prompt.

        `available()` and not the whole registry: a model offered a tool
        it is not permitted to run will request it, be denied, and have
        spent a turn learning something the policy already knew.

        The text comes from `describe_tool`, which is what the registry
        and the CLI already use, so a tool is described in exactly one
        place no matter who is asking.
        """

        described = [
            describe_tool(tool)
            for tool in (self.registry.get(name) for name in self.available())
            if tool is not None
        ]

        return "\n".join(part for part in described if part)

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

        self._emit(
            ToolInvokedEvent(
                name=name,
                arguments=self._safe_arguments(arguments),
            )
        )

        reason = self.check(name)

        if reason:
            return self._finish(name, fail(reason, tool=name))

        tool = self.registry.get(name)

        problem = self._validate(tool, arguments)

        if problem:
            return self._finish(name, fail(problem, tool=name))

        if not self._approved(tool, arguments):
            return self._finish(
                name,
                fail(f"permission denied for {name}", tool=name),
            )

        return self._finish(name, self._run(tool, arguments))

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
            # than to go looking for a bug in the tool.
            return fail(str(error), tool=name)

        except Exception as error:
            logger.warning("Tool %s failed: %s", name, error)
            return fail(f"{type(error).__name__}: {error}", tool=name)

        return self._normalise(tool, result)

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
                return ToolResult(
                    ok=result.ok,
                    output=result.output[:MAX_OUTPUT] + " ...(truncated)",
                    error=result.error,
                    tool=tool.name,
                )

            return result

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

    def _finish(self, name: str, result: ToolResult) -> ToolResult:

        self.history.append((name, result.ok))

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
