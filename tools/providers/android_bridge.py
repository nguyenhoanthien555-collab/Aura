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
from typing import Protocol


class DeviceBridge(Protocol):
    """What any Android backend must provide. Nothing more."""

    def invoke(self, tool: str, arguments: dict) -> dict:
        """Run one named tool; return a structured report."""
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

    def __init__(self, clock=time.time):
        self._clock = clock
        self._pending_launch: tuple[str, float] | None = None
        self.foreground_package = "com.aura.companion"
        self.nodes: dict[str, dict] = {}
        self.invocations: list[tuple[str, dict]] = []

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

        observation = {
            "kind": "foreground_app",
            "data": {
                "package": self.foreground_package,
                "node_count": len(self.nodes),
            },
        }

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
