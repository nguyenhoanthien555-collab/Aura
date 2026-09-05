"""
The device bridge - how Android tools reach a phone.

A bridge is the entire surface the AndroidProvider knows about the
device: one method, `invoke(tool, arguments)`, taking a fully-named tool
and plain-data arguments, returning a structured report. Everything else
- accessibility trees, gestures, intents, settle waits - lives behind it.

The protocol is deliberately the shape of the wire message the Android
app will answer, because in this deployment the server has no push
channel to the phone: the app polls. The bridge is what makes that a
transport detail instead of an architecture constraint. A bridge can be:

    * the step-endpoint relay (the device executes on its next poll)
    * an adb-backed direct harness (CLI validation, no LLM involved)
    * the LoopbackDeviceBridge below (deterministic, in-process)
    * a mock in tests

All four satisfy one Protocol, so the provider, the agent loop and the
CLI cannot tell them apart - which is exactly what lets Android execution
be debugged without the model in the loop.

Report shape (both directions of failure are structured, never prose):

    {"ok": True,
     "tool": "android.launch_app",
     "result": {...},
     "postcondition": {...} | None,
     "observation": {...} | None}

    {"ok": False,
     "tool": "android.tap",
     "error": {"code": "NODE_NOT_FOUND", "message": "..."}}
"""

import time
from contextvars import ContextVar
from typing import Protocol


class DeviceBridge(Protocol):
    """What any Android backend must provide. Nothing more."""

    def invoke(self, tool: str, arguments: dict) -> dict:
        """Run one named tool; return a structured report."""
        ...

    def status(self) -> dict:
        """Return live availability, health, and permission facts."""
        ...


class BridgeError(Exception):
    """Raised when the bridge itself fails, as opposed to the tool."""


class LoopbackMiss(TypeError):
    """
    A lookup that found nothing, raised so `invoke` can map it to the
    stable NODE_NOT_FOUND code instead of the generic invalid-arguments
    one - the distinction between 'the screen has no such node' and
    'the call was malformed' matters to the next reasoning round.
    """


def failure(tool: str, code: str, message: str) -> dict:
    """A structured failure report, with stable machine-readable codes."""

    return {
        "ok": False,
        "tool": tool,
        "error": {"code": code, "message": message},
    }


class DeclaredOnlyBridge:
    """
    A bridge that declares capabilities but cannot execute them.

    Exists for deferred (device-polling) mode on the server: the android.*
    schemas must be advertised to the model there even though this
    process holds no device - execution happens on the phone, which
    receives the directives. If such a tool somehow runs here anyway, the
    structured BRIDGE_UNAVAILABLE failure says exactly what happened,
    which is far more diagnosable than the tool being mysteriously
    absent from the catalogue.
    """

    def invoke(self, tool: str, arguments: dict) -> dict:
        return failure(
            tool,
            "BRIDGE_UNAVAILABLE",
            "android tools execute on the device in deferred mode; "
            "this process only advertises them",
        )

    def status(self) -> dict:
        return {
            "state": "AVAILABLE",
            "healthy": True,
            "reason": "declared-only test bridge",
            "permissions": {
                "android.accessibility": True,
                "android.screen_capture": True,
            },
        }


class LoopbackDeviceBridge:
    """
    A deterministic fake Android device, good enough to reason about.

    It exists for three users, and all three matter:

        * tests, which need tap/type/launch behaviour that never flakes
        * the CLI harness, where a loopback bridge gives a developer a
          runnable end-to-end check without a phone
        * the agent benchmarks, whose conversational cases must not
          depend on hardware

    State it models: the foreground package and one screen of nodes keyed
    by id. Mutations follow the real settle semantics - `launch_app`
    changes the foreground only after its settle delay, `tap` requires
    the node to be present and clickable - because a simulation that
    always succeeded would verify nothing.
    """

    # Short enough that tests stay fast, long enough that anything which
    # forgot to wait would observe the pre-settle state.
    SETTLE_S = 0.05

    # The inventory reported when no caller overrides `installed_apps`.
    DEFAULT_APPS = (
        {"package": "com.aura.companion", "label": "Aura",
         "launchable": True, "enabled": True},
        {"package": "com.android.chrome", "label": "Chrome",
         "launchable": True, "enabled": True},
    )

    def __init__(self, clock=time.time):
        self._clock = clock
        self._pending_launch: tuple[str, float] | None = None
        self.foreground_package = "com.aura.companion"
        self.nodes: dict[str, dict] = {}
        self.invocations: list[tuple[str, dict]] = []
        self.installed_apps: list[dict] | None = None

    def status(self) -> dict:
        return {
            "state": "AVAILABLE",
            "healthy": True,
            "reason": "loopback bridge is active",
            "permissions": {
                "android.accessibility": True,
                "android.screen_capture": True,
            },
        }

    def install_screen(self, package: str, nodes: dict[str, dict]) -> None:
        """Replace the whole visible screen."""

        self.foreground_package = package
        self.nodes = {
            node_id: dict(node, node_id=node_id)
            for node_id, node in nodes.items()
        }

    def _settle_launches(self) -> None:

        if (
            self._pending_launch
            and self._clock() >= self._pending_launch[1]
        ):
            self.foreground_package = self._pending_launch[0]
            self._pending_launch = None

    @staticmethod
    def _find_by_text(nodes: dict, needle: str) -> str | None:

        lowered = (needle or "").strip().lower()

        for node_id, node in nodes.items():
            texts = (
                str(node.get("text", "")),
                str(node.get("content_description", "")),
            )

            if lowered and any(lowered in text.lower() for text in texts):
                return node_id

        return None

    # ------------------------------------------------------------------
    # The protocol
    # ------------------------------------------------------------------

    def invoke(self, tool: str, arguments: dict) -> dict:

        self._settle_launches()
        self.invocations.append((tool, dict(arguments)))

        handler = getattr(self, f"_do_{tool.split('.', 1)[-1]}", None)

        if handler is None:
            return failure(tool, "UNSUPPORTED_TOOL", f"loopback has no {tool}")

        try:
            result, postcondition = handler(**(arguments or {}))
        except TypeError as error:
            return failure(
                tool,
                getattr(error, "code", "INVALID_ARGUMENTS"),
                str(error),
            )

        observation = (
            {
                "kind": "app_inventory",
                "data": {
                    "count": len((result or {}).get("packages", [])),
                    "observed_at": (result or {}).get("observed_at", ""),
                    "device_id": (result or {}).get("device_id", ""),
                },
            }
            if tool == "android.list_apps"
            else {
                "kind": "foreground_app",
                "data": {
                    "package": self.foreground_package,
                    "node_count": len(self.nodes),
                },
            }
        )

        report = {
            "ok": True,
            "tool": tool,
            "result": result,
            "postcondition": postcondition,
            "observation": observation,
        }

        if postcondition is not None:
            report["verified"] = postcondition.get("verified", False)

        return report

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def _do_get_foreground_app(self):

        return (
            {"package": self.foreground_package},
            None,
        )

    def _do_get_ui_tree(self):

        return ({"nodes": list(self.nodes.values())}, None)

    def _do_list_apps(self):

        apps = self.installed_apps if self.installed_apps is not None \
            else list(self.DEFAULT_APPS)

        return (
            {
                "packages": [dict(app) for app in apps],
                "count": len(apps),
                "observed_at": str(self._clock()),
                "device_id": "loopback-device",
                "source": "android.package_manager",
            },
            None,
        )

    def _do_find_node(self, text: str = "", **_):

        node_id = self._find_by_text(self.nodes, text)

        if node_id is None:
            miss = LoopbackMiss(f"no node matching text {text!r}")
            miss.code = "NODE_NOT_FOUND"
            raise miss

        return (self.nodes[node_id], None)

    def _do_verify(self, check: str = "", **_):
        """
        The same check vocabulary the real device tool accepts:
        package_is=, text_visible=, node_exists=.
        """

        kind, _, value = (check or "").partition("=")
        value = value.strip()

        if kind == "package_is":
            met = self.foreground_package == value
        elif kind == "text_visible":
            met = self._find_by_text(self.nodes, value) is not None
        elif kind == "node_exists":
            met = value in self.nodes
        else:
            miss = LoopbackMiss(
                f"unknown verify check {check!r} "
                "(use package_is=, text_visible= or node_exists=)"
            )
            miss.code = "INVALID_ARGUMENTS"
            raise miss

        return (
            {"check": check, "met": met},
            {"verified": met, "check": check},
        )

    def _do_screenshot(self):

        return (
            {"format": "png", "size_bytes": 0, "reference": "loopback-frame"},
            None,
        )

    def _do_wait_for(self, condition: str = "", **_):

        self._settle_launches()

        if condition.startswith("foreground="):
            target = condition.split("=", 1)[1]
            met = self.foreground_package == target

            return (
                {"condition": condition, "met": met},
                {"verified": met, "expected_package": target},
            )

        return ({"condition": condition, "met": True}, {"verified": True})

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def _do_tap(self, node_id: str = "", text: str = "", **_):

        target = node_id or self._find_by_text(self.nodes, text) or ""
        node = self.nodes.get(target)

        if node is None:
            miss = LoopbackMiss(f"no visible node matching "
                                f"{node_id or text!r}")
            miss.code = "NODE_NOT_FOUND"
            raise miss

        if not node.get("clickable", False):
            raise TypeError(f"node {target} is not clickable")

        return ({"tapped": target}, {"verified": True})

    _do_long_press = _do_tap

    def _do_type_text(self, text: str = "", **_):

        return (
            {"typed": len(text or ""), "field_text": text or ""},
            {"verified": True, "expected_text": text or ""},
        )

    def _do_press_key(self, key: str = "", **_):

        if key == "enter":
            # A submit moves to results: same fingerprint-change rule the
            # device uses.
            self.nodes = {
                node_id: dict(node, text=f"result-{node_id}")
                for node_id, node in self.nodes.items()
            }

        return ({"pressed": key}, {"verified": True})

    def _do_back(self):

        return ({"navigated": "back"}, {"verified": True})

    def _do_home(self):

        self.foreground_package = "com.android.launcher"

        return (
            {"screen": "home"},
            {"verified": True, "foreground_package": self.foreground_package},
        )

    def _do_launch_app(self, package: str = "", **_):

        if not (package or "").strip():
            raise TypeError("launch_app needs a package")

        self._pending_launch = (package, self._clock() + self.SETTLE_S)

        return (
            {"launched": package},
            {
                "verified": False,
                "expected_package": package,
                "note": "settles asynchronously; use android.wait_for",
            },
        )


# ---------------------------------------------------------------------------
# The real device bridge
# ---------------------------------------------------------------------------

# The run/tool-call scope of the invocation currently being executed.
#
# `AndroidTool.execute(**arguments)` deliberately carries no run identity -
# a tool's signature is its declared parameters and nothing else - but the
# gateway needs one, because cancelling a run must resolve exactly that
# run's pending work (PART 11/TEST I) and a report must never be folded
# into a different run (TEST J). A ContextVar carries it without widening
# every tool signature, and `starlette.concurrency.run_in_threadpool`
# copies the context into the worker thread, so a scope set by the route
# is visible to the blocking submit underneath it.
# (run_id, tool_call_id, timeout_s | None). The deadline rides along
# because it belongs to the *caller* - `/api/device/invoke` accepts one
# per request - and a bridge default silently overriding it would make a
# 0.3s request wait 30s, which is how this was first caught.
device_call_scope: ContextVar[tuple[str, str, "float | None"]] = ContextVar(
    "device_call_scope", default=("", "", None),
)


class GatewayDeviceBridge:
    """
    The bridge to a real phone, over the polling gateway.

    This is the production `DeviceBridge`: the same object the CLI's
    `--bridge http` path and the server's AgentRuntime both execute
    through, which is what makes "it worked from the CLI" evidence about
    the agent path rather than about a parallel implementation.

    It owns no Android logic and no transport of its own. `invoke` queues
    the named tool on `server.device_gateway.DeviceGateway` and blocks
    until the handset's structured report comes back, or the bounded wait
    produces the gateway's own TIMEOUT report. Failure is always a report;
    nothing raises across this boundary.
    """

    def __init__(self, gateway=None, timeout_s: float = 30.0):
        self._gateway = gateway
        self.timeout_s = float(timeout_s)

    @property
    def gateway(self):
        """Resolved lazily so importing this module needs no server."""

        if self._gateway is not None:
            return self._gateway

        from server.device_gateway import get_device_gateway

        return get_device_gateway()

    def invoke(self, tool: str, arguments: dict) -> dict:

        run_id, tool_call_id, requested = device_call_scope.get()

        # `wait_for` owns its own deadline, so the transport must outlive
        # it or a legitimate wait would be reported as a device timeout.
        timeout_s = self.timeout_s if requested is None else float(requested)

        if tool.endswith(".wait_for"):
            requested_ms = arguments.get("timeout_ms") or 3000

            try:
                timeout_s = max(timeout_s, float(requested_ms) / 1000.0 + 5.0)
            except (TypeError, ValueError):
                pass

        report = self.gateway.submit(
            tool,
            arguments,
            run_id=run_id,
            tool_call_id=tool_call_id,
            timeout_s=timeout_s,
        )

        return normalise_device_report(report, tool)

    def status(self) -> dict:
        return self.gateway.device_status()


def normalise_device_report(report: dict, tool: str) -> dict:
    """
    A device report in the bridge's report shape.

    The handset answers `/api/device/results` with the envelope it filled
    in; the gateway adds nothing but its own TIMEOUT/CANCELLED failures.
    This function is the one place that guarantees the provider sees the
    keys it documents - notably `tool`, which the device omits because the
    invocation id already identifies the call.

    A report that is neither ok nor carrying an error is a malformed
    answer, not a success: it becomes EXECUTION_FAILED rather than being
    handed upward as an empty result the model would read as "done".
    """

    if not isinstance(report, dict):
        return failure(
            tool, "EXECUTION_FAILED",
            f"device returned {type(report).__name__}, not a report",
        )

    normalised = dict(report)
    normalised["tool"] = normalised.get("tool") or tool

    if normalised.get("ok"):
        normalised.setdefault("result", {})
        normalised.setdefault("postcondition", None)

        postcondition = normalised.get("postcondition")

        if isinstance(postcondition, dict):
            normalised["verified"] = bool(postcondition.get("verified", False))

        if tool == "android.list_apps":
            # An inventory report must carry the shape the tool declares -
            # a packages list and an observation timestamp. Malformed
            # inventory is never success: it becomes EXECUTION_FAILED so
            # the failure class propagates instead of an empty-ish result
            # the model would read as "done". UNKNOWN stays UNKNOWN.
            if not _valid_inventory(normalised):
                return failure(
                    tool, "EXECUTION_FAILED",
                    "inventory report is malformed "
                    "(packages list or observed_at missing)",
                )

        return normalised

    error = normalised.get("error")

    if not isinstance(error, dict) or not error.get("code"):
        return failure(
            tool, "EXECUTION_FAILED",
            "device reported failure without an error code",
        )

    normalised["error"] = {
        "code": str(error.get("code")),
        "message": str(error.get("message", "")),
    }

    return normalised


def _valid_inventory(report: dict) -> bool:
    """Whether a report has the structure android.list_apps declares.

    Only shape, never content: packages must be a list and observed_at
    must be present. Individual package objects and field values are the
    device's responsibility; their correctness as facts is exactly what
    the response verifier grades, not this transport check.
    """

    result = report.get("result")

    if not isinstance(result, dict):
        return False

    if not isinstance(result.get("packages"), list):
        return False

    return bool(result.get("observed_at"))
