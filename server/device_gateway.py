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
    task_id: str = ""
    step_id: str = ""
    correlation_id: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "invocation_id": self.invocation_id,
            "run_id": self.run_id,
            "tool_call_id": self.tool_call_id,
            "task_id": self.task_id,
            "step_id": self.step_id,
            "correlation_id": self.correlation_id,
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

    def __init__(
        self,
        clock=time.time,
        require_heartbeat: bool = False,
        task_runtime=None,
    ):
        self._clock = clock
        self._require_heartbeat = require_heartbeat
        self._task_runtime = task_runtime
        self._condition = threading.Condition(threading.RLock())
        self._pending: list[PendingInvocation] = []
        self._results: dict[str, dict] = {}

        self.submitted = 0
        self.completed = 0
        self.timed_out = 0
        self._devices: dict[str, dict] = {}
        self._timed_out: dict[str, PendingInvocation] = {}
        self._late_reports: dict[str, dict] = {}
        self.invocation_ledger = None

    def _get_ledger(self):
        if self.invocation_ledger is None:
            try:
                from core.sync.invocation_ledger import DurableInvocationLedger
                self.invocation_ledger = DurableInvocationLedger()
            except Exception:
                pass
        return self.invocation_ledger

    # ------------------------------------------------------------------
    # Caller side
    # ------------------------------------------------------------------

    def submit(
        self,
        tool: str,
        arguments: dict | None = None,
        run_id: str = "",
        tool_call_id: str = "",
        task_id: str = "",
        step_id: str = "",
        correlation_id: str = "",
        timeout_s: float = 30.0,
    ) -> dict:
        """
        Queue one invocation and block until the device answers.

        Returns the device's structured report verbatim, or a structured
        TIMEOUT/CANCELLED failure this module authored - never prose,
        never an exception crossing the HTTP boundary.
        """
        ledger = self._get_ledger()
        if tool_call_id and ledger:
            cached = ledger.check_replay(tool_call_id)
            if cached is not None:
                logger.info("DeviceGateway: replay detected for %s; returning cached result", tool_call_id)
                return cached

        invocation = PendingInvocation(
            invocation_id=new_invocation_id(),
            tool=tool,
            arguments=dict(arguments or {}),
            run_id=run_id,
            tool_call_id=tool_call_id,
            task_id=task_id,
            step_id=step_id,
            correlation_id=correlation_id,
            created_at=self._clock(),
        )

        if ledger:
            try:
                ledger.record_received(
                    invocation_id=invocation.invocation_id,
                    tool=tool,
                    arguments=dict(arguments or {}),
                    run_id=run_id,
                    tool_call_id=tool_call_id,
                )
            except Exception as e:
                logger.debug("Failed recording received invocation: %s", e)

        with self._condition:
            self._pending.append(invocation)
            self.submitted += 1
            self._condition.notify_all()

            deadline = self._clock() + max(0.1, float(timeout_s))

            while invocation.invocation_id not in self._results:
                remaining = deadline - self._clock()

                if remaining <= 0:
                    self.timed_out += 1
                    self._timed_out[invocation.invocation_id] = invocation
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

    def cancel_all(self, reason: str = "emergency interruption") -> int:
        """
        Resolve all pending invocations across all runs and tasks as CANCELLED.
        Used by the emergency interrupt / barge-in mechanism.
        """
        with self._condition:
            doomed = list(self._pending)
            for invocation in doomed:
                self._results[invocation.invocation_id] = _failure(
                    invocation, "CANCELLED",
                    f"interrupted: {reason}",
                )
            self._pending.clear()
            if doomed:
                self._condition.notify_all()

        return len(doomed)

    # ------------------------------------------------------------------
    # Device side
    # ------------------------------------------------------------------

    def heartbeat(self, device_id: str, capabilities: dict | None = None) -> None:
        """Record runtime facts reported by a polling companion."""
        key = str(device_id or "unknown")
        with self._condition:
            self._devices[key] = {
                "last_seen": self._clock(),
                "capabilities": dict(capabilities or {}),
            }

    def device_status(self, device_id: str = "", max_age_s: float = 35.0) -> dict:
        """Return the freshest live companion status, or an explicit absence."""
        with self._condition:
            record = self._devices.get(device_id) if device_id else max(
                self._devices.values(),
                key=lambda item: item["last_seen"],
                default=None,
            )

            if record is None:
                if not self._require_heartbeat:
                    return {
                        "state": "AVAILABLE",
                        "healthy": True,
                        "reason": "unidentified in-process device test bridge",
                        "permissions": {
                            "android.accessibility": True,
                            "android.screen_capture": True,
                        },
                        "capabilities": {},
                    }
                return {
                    "state": "UNAVAILABLE",
                    "healthy": False,
                    "reason": "no Android companion poll heartbeat has been received",
                    "permissions": {},
                    "capabilities": {},
                }

            age = max(0.0, self._clock() - record["last_seen"])
            if age > max_age_s:
                return {
                    "state": "UNAVAILABLE",
                    "healthy": False,
                    "reason": f"Android companion heartbeat is {age:.1f}s old",
                    "permissions": {},
                    "capabilities": {},
                }

            capabilities = record["capabilities"]
            if self._require_heartbeat and not capabilities:
                return {
                    "state": "UNKNOWN",
                    "healthy": False,
                    "reason": "Android companion heartbeat contained no capability status",
                    "permissions": {},
                    "capabilities": {},
                }

            permissions = {}
            for value in capabilities.values():
                if isinstance(value, dict):
                    permissions.update(value.get("permissions") or {})

            return {
                "state": "AVAILABLE",
                "healthy": True,
                "reason": f"Android companion heartbeat received {age:.1f}s ago",
                "permissions": permissions,
                "capabilities": capabilities,
            }

    def poll(self, timeout_s: float = 0.0) -> PendingInvocation | None:
        """
        The next queued invocation, oldest first, or None.

        When timeout_s > 0, blocks up to timeout_s on the gateway condition
        for an invocation to be enqueued. Woken instantly by submit().
        """

        with self._condition:
            if not self._pending and timeout_s > 0:
                self._condition.wait(timeout=float(timeout_s))

            inv = self._pending[0] if self._pending else None
            if inv:
                ledger = self._get_ledger()
                if ledger:
                    try:
                        ledger.record_executing(inv.invocation_id)
                        if getattr(inv, "tool_call_id", ""):
                            ledger.record_executing(inv.tool_call_id)
                    except Exception as e:
                        logger.debug("Failed recording executing state on device poll: %s", e)
            return inv

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
                or invocation_id in self._timed_out
                or any(item.invocation_id == invocation_id
                       for item in self._pending)
            )

            if not known:
                return False

            if invocation_id in self._timed_out:
                if invocation_id in self._late_reports:
                    logger.debug("Duplicate late report for invocation %s ignored", invocation_id)
                    return True
                inv = self._timed_out[invocation_id]
                self._late_reports[invocation_id] = dict(report)
                self.completed += 1
                task_id = getattr(inv, "task_id", "") if inv else ""
                step_id = getattr(inv, "step_id", "") if inv else ""
                logger.info(
                    "Accepted late report for timed-out invocation %s (task=%s, step=%s)",
                    invocation_id, task_id, step_id,
                )
                should_settle = bool(task_id and step_id)

            else:
                should_settle = False
                self._results[invocation_id] = dict(report)
                self.completed += 1
                self._pending = [
                    item for item in self._pending
                    if item.invocation_id != invocation_id
                ]
                self._condition.notify_all()

        if should_settle:
            try:
                self.settle_step_from_late_report(invocation_id, task_runtime=self._task_runtime)
            except Exception as e:
                logger.warning("Auto-settling step from late report failed: %s", e)

        ledger = self._get_ledger()
        if ledger:
            try:
                ledger.record_completed(invocation_id, report)
                inv = next((item for item in self._pending if item.invocation_id == invocation_id), None)
                if not inv:
                    inv = self._timed_out.get(invocation_id)
                if inv and inv.tool_call_id:
                    ledger.record_completed(inv.tool_call_id, report)
            except Exception as e:
                logger.debug("Failed recording completed invocation in ledger: %s", e)

        return True

    def settle_step_from_late_report(
        self, invocation_id: str, task_runtime=None
    ) -> bool:
        """
        Settles an ambiguous step in TaskRuntime using its late device report.
        """
        with self._condition:
            report = self._late_reports.get(invocation_id)
            inv = self._timed_out.get(invocation_id)

        if not report or not inv or not inv.task_id or not inv.step_id:
            return False

        if task_runtime is None:
            task_runtime = self._task_runtime

        if task_runtime is None:
            try:
                from agent.task_runtime import TaskRuntime
                task_runtime = TaskRuntime()
            except Exception as e:
                logger.warning("Failed to get TaskRuntime for late report settlement: %s", e)
                return False

        ok = bool(report.get("ok", False))
        error = ""
        if not ok:
            err_dict = report.get("error")
            error = (
                err_dict.get("message")
                if isinstance(err_dict, dict)
                else str(err_dict or "Late report failed")
            )

        evidence = report.get("evidence") or []
        res = report.get("result") or report
        task_runtime.settle_ambiguous_step(
            task_id=inv.task_id,
            step_id=inv.step_id,
            ok=ok,
            result=res if isinstance(res, dict) else {"output": res},
            evidence=evidence if isinstance(evidence, list) else [],
            error=error,
        )
        logger.info(
            "Settled task %s step %s from late report for invocation %s (ok=%s)",
            inv.task_id, inv.step_id, invocation_id, ok,
        )
        return True

    def get_late_report(self, invocation_id: str) -> dict | None:
        with self._condition:
            return self._late_reports.get(invocation_id)

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
            _gateway = DeviceGateway(require_heartbeat=True)
        return _gateway


def configure_device_gateway(gateway: DeviceGateway | None) -> None:
    """Swap the process gateway (tests install their own)."""

    global _gateway

    with _gateway_lock:
        _gateway = gateway
