"""
The device gateway - how the server reaches a phone that only polls.

The transport constraint this module solves: FastAPI cannot push to the
Android app, but `/api/device/invoke` needs synchronous semantics -
submit an invocation, get the structured result back before responding.
The bridge is a pending queue plus correlation:

    CLI / AgentRuntime          DEVICE
    ------------------          ------
    POST /api/device/invoke ──► enqueue, wait on condition
                                      ▲
    POST /api/device/poll  ◄──────────┘ every ~1s while connected
    (returns one invocation)
                                      │ execute deterministically
    POST /api/device/results ◄───────┘ structured report
    (resolves the waiter)
    invoke responds with the report or TIMEOUT

Every wait is bounded; a phone that disconnects mid-invocation produces
a TIMEOUT report to the caller rather than a hung request, and any
pending invocations of a cancelled run are resolved as CANCELLED so no
orphaned work survives its run.
"""

import threading
import time
import uuid
from dataclasses import dataclass, field

from core.logger import logger


def new_invocation_id() -> str:
    return "invo_" + uuid.uuid4().hex[:16]


@dataclass
class PendingInvocation:
    """One queued request, as the device will receive it."""

    invocation_id: str
    tool: str
    arguments: dict
    run_id: str = ""
    tool_call_id: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "invocation_id": self.invocation_id,
            "run_id": self.run_id,
            "tool_call_id": self.tool_call_id,
            "tool": self.tool,
            "arguments": self.arguments,
        }


def _failure(invocation: PendingInvocation, code: str, message: str) -> dict:
    """The caller-facing shape of a failure the gateway itself owns."""

    return {
        "ok": False,
        "run_id": invocation.run_id,
        "tool_call_id": invocation.tool_call_id,
        "tool": invocation.tool,
        "error": {"code": code, "message": message},
    }


class DeviceGateway:
    """
    The single rendezvous between server-side callers and the polling
    device. One instance per process; all state is guarded by one lock,
    because the interesting events (submit, poll, complete) each touch
    both queues.
    """

    def __init__(self, clock=time.time):
        self._clock = clock
        self._condition = threading.Condition(threading.Lock())
        self._pending: list[PendingInvocation] = []
        self._results: dict[str, dict] = {}

        self.submitted = 0
        self.completed = 0
        self.timed_out = 0

    # ------------------------------------------------------------------
    # Caller side
    # ------------------------------------------------------------------

    def submit(
        self,
        tool: str,
        arguments: dict | None = None,
        run_id: str = "",
        tool_call_id: str = "",
        timeout_s: float = 30.0,
    ) -> dict:
        """
        Queue one invocation and block until the device answers.

        Returns the device's structured report verbatim, or a structured
        TIMEOUT/CANCELLED failure this module authored - never prose,
        never an exception crossing the HTTP boundary.
        """

        invocation = PendingInvocation(
            invocation_id=new_invocation_id(),
            tool=tool,
            arguments=dict(arguments or {}),
            run_id=run_id,
            tool_call_id=tool_call_id,
            created_at=self._clock(),
        )

        with self._condition:
            self._pending.append(invocation)
            self.submitted += 1
            self._condition.notify_all()

            deadline = self._clock() + max(0.1, float(timeout_s))

            while invocation.invocation_id not in self._results:
                remaining = deadline - self._clock()

                if remaining <= 0:
                    self.timed_out += 1
                    self._pending = [
                        item for item in self._pending
                        if item.invocation_id != invocation.invocation_id
                    ]
                    logger.warning(
                        "Device invocation %s (%s) timed out after %.1fs",
                        invocation.invocation_id, tool, timeout_s,
                    )
                    return _failure(
                        invocation, "TIMEOUT",
                        f"device did not answer within {timeout_s:.0f}s",
                    )

                self._condition.wait(timeout=remaining)

            return self._results.pop(invocation.invocation_id)

    def cancel_run(self, run_id: str) -> int:
        """
        Resolve everything still pending for a cancelled run.

        The count returned lets the caller (and a test) see that nothing
        orphaned survived the cancellation.
        """

        with self._condition:
            doomed = [
                invocation for invocation in self._pending
                if invocation.run_id == run_id
            ]

            for invocation in doomed:
                self._results[invocation.invocation_id] = _failure(
                    invocation, "CANCELLED",
                    f"run {run_id} was cancelled",
                )

            self._pending = [
                invocation for invocation in self._pending
                if invocation.run_id != run_id
            ]

            if doomed:
                self._condition.notify_all()

        return len(doomed)

    # ------------------------------------------------------------------
    # Device side
    # ------------------------------------------------------------------

    def poll(self) -> PendingInvocation | None:
        """The next queued invocation, oldest first, or None."""

        with self._condition:
            return self._pending[0] if self._pending else None

    def complete(self, invocation_id: str, report: dict) -> bool:
        """
        Accept the device's structured report for one invocation.

        Unknown ids are refused: a result that matches nothing is either
        a replay or a bug, and accepting either would hand a caller an
        answer to a question it did not ask.
        """

        with self._condition:
            known = (
                invocation_id in self._results
                or any(item.invocation_id == invocation_id
                       for item in self._pending)
            )

            if not known:
                return False

            self._results[invocation_id] = dict(report)
            self.completed += 1
            self._pending = [
                item for item in self._pending
                if item.invocation_id != invocation_id
            ]
            self._condition.notify_all()

        return True

    def pending_count(self) -> int:

        with self._condition:
            return len(self._pending)


# One gateway per process, shared by the invoke route, the poll route and
# the results route - they are only useful as three ends of one queue.
_gateway: DeviceGateway | None = None
_gateway_lock = threading.Lock()


def get_device_gateway() -> DeviceGateway:

    global _gateway

    with _gateway_lock:
        if _gateway is None:
            _gateway = DeviceGateway()
        return _gateway


def configure_device_gateway(gateway: DeviceGateway | None) -> None:
    """Swap the process gateway (tests install their own)."""

    global _gateway

    with _gateway_lock:
        _gateway = gateway