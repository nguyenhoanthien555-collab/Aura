"""
The Android capability provider.

Android becomes an ordinary set of tools here: `android.tap`,
`android.launch_app`, `android.wait_for` and the rest, declared with
schemas, risk levels and parameter types like any other tool in the
registry. The provider holds no Android logic of its own - every call is
forwarded to a DeviceBridge, which knows the device.

What this buys over the old action-string pathway:

    * One declaration per capability, read identically by the model's
      function-calling schema, the policy gates and the CLI harness.
    * Structured results end to end. A bridge report's error codes
      (NODE_NOT_FOUND, INVALID_ARGUMENTS) survive into the ToolResult's
      data payload instead of being flattened into prose the model must
      re-guess.
    * Postconditions travel with results. A mutating tool that verified
      says so; one that could not says which check failed - evidence,
      not vibes.

Risk mapping follows PART 16 of the migration brief, expressed through
the existing ToolRisk ladder so the executor's approval gates need no
change: reads are SAFE, pixels are SENSITIVE (they leave nothing on the
machine but can carry private content), mutations are DANGEROUS.
"""

import json

from core.logger import logger
from tools.base import Parameter, Tool, ToolResult, ToolRisk
from tools.providers.android_bridge import DeviceBridge
from tools.providers.base import CapabilityProvider
from tools.registry import ToolRegistry


def tool_result_from_report(report: dict) -> ToolResult:
    """
    A bridge report as a ToolResult.

    The structured report rides along intact in `data`; `output` renders
    the result values compactly for prompts and logs, and failures carry
    their stable error code so the next reasoning round can branch on it
    rather than parse a sentence.
    """

    tool = str(report.get("tool", ""))

    if report.get("ok"):
        result = report.get("result") or {}
        return ToolResult(
            ok=True,
            output=json.dumps(result, ensure_ascii=False, default=str),
            tool=tool,
            data=report,
        )

    error = report.get("error") or {}
    code = str(error.get("code", "UNKNOWN"))
    message = str(error.get("message", ""))

    return ToolResult(
        ok=False,
        error=f"{code}: {message}" if message else code,
        tool=tool,
        data=report,
    )


class AndroidTool(Tool):
    """
    One Android capability, forwarded to the bridge.

    Subclasses exist only to declare name/description/parameters/risk;
    execution is this single forward-and-convert for all of them, which
    is why adding an Android capability is one small class and nothing
    else.
    """

    timeout = 30.0

    def __init__(self, bridge: DeviceBridge):
        self.bridge = bridge

    def execute(self, **arguments) -> ToolResult:

        try:
            report = self.bridge.invoke(self.name, arguments)
        except Exception as error:  # bridge failure, not tool failure
            payload = {
                "ok": False,
                "tool": self.name,
                "error": {"code": "BRIDGE_ERROR", "message": str(error)},
            }
            return ToolResult(
                ok=False,
                error=f"BRIDGE_ERROR: {error}",
                tool=self.name,
                data=payload,
            )

        return tool_result_from_report(report)


class _Read(AndroidTool):
    """A pure observation: no side effects on the device."""

    risk = ToolRisk.SAFE


class _Mutation(AndroidTool):
    """Changes something on the device; runs behind the approval gates."""

    risk = ToolRisk.DANGEROUS


class GetForegroundApp(_Read):

    name = "android.get_foreground_app"
    description = (
        "The app currently in the foreground, from accessibility metadata "
        "(package, label). Answers 'what app am I in' without any vision."
    )


class GetUITree(_Read):

    name = "android.get_ui_tree"
    description = (
        "The current accessibility tree: visible nodes with id, text, "
        "bounds and clickability. This is how current UI state is read."
    )
    parameters = (
        Parameter(name="max_depth", type="integer", required=False,
                  description="Limit tree depth."),
    )


class FindNode(_Read):

    name = "android.find_node"
    description = (
        "Find a visible node by text or content description. Returns its "
        "id so a later android.tap can target it precisely."
    )
    parameters = (
        Parameter(name="text", type="string",
                  description="Text or content description to match."),
    )


class Screenshot(AndroidTool):
    """
    Pixels, distinct from metadata on purpose: a screenshot answers
    'what is visible', which foreground-app metadata cannot, and it may
    carry private content off the machine when upload processors run -
    hence SENSITIVE rather than SAFE.
    """

    name = "android.screenshot"
    description = (
        "Capture the current screen as an image. Visual observation; use "
        "get_foreground_app instead when only app identity is needed."
    )
    risk = ToolRisk.SENSITIVE


class Tap(_Mutation):

    name = "android.tap"
    description = (
        "Tap a node by id or by visible text. Prefer text when the node "
        "was just seen in a fresh tree."
    )
    parameters = (
        Parameter(name="node_id", required=False,
                  description="Node id from get_ui_tree/find_node."),
        Parameter(name="text", required=False,
                  description="Visible text to tap."),
    )


class LongPress(Tap):

    name = "android.long_press"
    description = "Long-press a node by id or visible text."


class Swipe(_Mutation):

    name = "android.swipe"
    description = "Swipe the screen in a direction."
    parameters = (
        Parameter(name="direction", description="up, down, left or right."),
    )


class TypeText(_Mutation):

    name = "android.type_text"
    description = (
        "Type text into the focused field, optionally focusing one first."
    )
    parameters = (
        Parameter(name="text", description="The text to enter."),
        Parameter(name="node_id", required=False,
                  description="Field node id, if not already focused."),
    )


class PressKey(_Mutation):

    name = "android.press_key"
    description = "Press a key such as enter or delete."
    parameters = (
        Parameter(name="key", description='Key name, e.g. "enter".'),
    )


class Back(_Mutation):

    name = "android.back"
    description = "Press the system back button."


class Home(_Mutation):

    name = "android.home"
    description = "Go to the home screen."


class LaunchApp(_Mutation):

    name = "android.launch_app"
    description = (
        "Launch an app by package name. Returns while the app settles; "
        "follow with android.wait_for('foreground=<package>')."
    )
    parameters = (
        Parameter(
            name="package",
            description="Android package name, e.g. "
                        "com.google.android.youtube.",
        ),
    )


class WaitFor(_Read):
    """
    Bounded polling, not sleep. The condition vocabulary matches what the
    device can actually observe; 'foreground=' proves a launch landed.
    """

    name = "android.wait_for"
    description = (
        "Wait until a condition holds: foreground=<package>, "
        "text_exists=<text>, node_gone=<id>, activity_changed. Bounded "
        "timeout; never a fixed sleep."
    )
    parameters = (
        Parameter(name="condition",
                  description="e.g. foreground=com.google.android.youtube"),
        Parameter(name="timeout_ms", type="integer", required=False,
                  description="Give up after this long (default 3000)."),
    )


class Verify(_Read):

    name = "android.verify"
    description = (
        "Check a claim about current state: package_is=<pkg>, "
        "text_visible=<text>, node_exists=<id>. Verification is evidence; "
        "task completion requires it rather than the model saying complete."
    )
    parameters = (
        Parameter(name="check", description="What to verify."),
    )


class AndroidProvider(CapabilityProvider):
    """
    Every Android capability this deployment supports, registered as
    ordinary tools.

    `available()` is false until a bridge is supplied, so building the
    provider without a connected device registers nothing and advertises
    nothing - the same rule tools/factory.py applies to filesystem roots
    and application allow lists.
    """

    namespace = "android"

    TOOLS = (
        GetForegroundApp, GetUITree, FindNode, Screenshot,
        Tap, LongPress, Swipe, TypeText, PressKey,
        Back, Home, LaunchApp, WaitFor, Verify,
    )

    def __init__(self, bridge: DeviceBridge | None = None):
        self.bridge = bridge

    def available(self) -> bool:
        return self.bridge is not None

    def capabilities(self) -> list:
        if self.bridge is None:
            return []

        return [tool_cls(self.bridge) for tool_cls in self.TOOLS]

    def register_into(self, registry: ToolRegistry) -> int:

        if not self.available():
            logger.info(
                "AndroidProvider has no device bridge; registering nothing"
            )
            return 0

        return super().register_into(registry)


# Re-exported so callers can import everything Android from one module.
__all__ = [
    "AndroidProvider",
    "AndroidTool",
    "tool_result_from_report",
] + [cls.__name__ for cls in AndroidProvider.TOOLS]