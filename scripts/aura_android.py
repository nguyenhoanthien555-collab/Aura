"""
aura-android - the Android capability harness (migration PARTS 19-21).

A developer-facing CLI over the same AndroidProvider the agent uses,
existing so Android execution can be validated WITHOUT the LLM in the
loop. When something misbehaves on a device, this tool answers the first
question - is it the provider or the reasoning? - because everything
here runs deterministically:

    aura-android app current
    aura-android ui find --text "Search"
    aura-android ui tap --text "Search"
    aura-android input type "Minecraft"
    aura-android nav back
    aura-android screen capture

Add --json anywhere for machine-readable output (the structured report,
not a string rendering of it), and --dry-run on mutating commands to see
what would be invoked without invoking it.

Bridges:
    loopback   deterministic in-process device (default) - always works
    http       POST the invocation to a running Aura server, which
               relays it through its registry

The REPL (`aura-android repl` or just `aura-android`) keeps one bridge in
a session so a screen can be inspected, tapped, typed into and verified
interactively - the inspect/mutate/verify loop from the CLI-Anything
playbook.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import dotenv_values

# Runnable from anywhere: the harness is a script, and a developer will
# invoke it from whatever directory they are debugging in.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.providers.android_bridge import LoopbackDeviceBridge
from tools.providers.android_provider import AndroidProvider
from tools.base import ToolRisk
from tools.executor import ToolExecutor, ToolPolicy
from tools.registry import ToolRegistry


MUTATING = {
    "android.tap", "android.long_press", "android.swipe",
    "android.type_text", "android.press_key", "android.back",
    "android.home", "android.launch_app",
}


# ----------------------------------------------------------------------
# Bridge construction
# ----------------------------------------------------------------------

def build_bridge(name: str):
    """The transport behind the tools. Loopback by default."""

    if name == "loopback":
        return LoopbackDeviceBridge()

    if name == "http":

        class HttpRelay:
            """
            Forwards invocations to a running Aura server, which queues
            them for the connected phone via its device gateway.

            Authentication is the server's authoritative bearer token,
            `AURA_SERVER_AUTH_TOKEN`, loaded from the project-root `.env`
            just as it is by the server. `AURA_TOKEN` remains an explicit
            compatibility override for older automation. The token travels
            only in the Authorization header and is never logged.
            """

            def __init__(self, base_url: str):
                import urllib.error       # noqa: F401 (used below)
                import urllib.request

                self.base_url = base_url.rstrip("/")
                self.token = configured_auth_token()

            def invoke(self, tool: str, arguments: dict) -> dict:

                import urllib.request

                payload = json.dumps(
                    {"tool": tool, "arguments": arguments}
                ).encode("utf-8")

                headers = {"Content-Type": "application/json"}

                if self.token:
                    headers["Authorization"] = f"Bearer {self.token}"

                request = urllib.request.Request(
                    f"{self.base_url}/api/device/invoke",
                    data=payload,
                    headers=headers,
                    method="POST",
                )

                try:
                    with urllib.request.urlopen(request, timeout=90) as r:
                        return json.loads(r.read().decode("utf-8"))

                except urllib.error.HTTPError as error:
                    # The server answers a refusal WITH its envelope -
                    # 504 TIMEOUT, 404 TOOL_NOT_FOUND, 422
                    # INVALID_ARGUMENTS, 409 STALE_RUN. Reading only the
                    # status would turn every one of them into a generic
                    # transport failure and lose the reason.
                    body = self._body_of(error)

                    if isinstance(body, dict) and body.get("error"):
                        return body

                    if error.code == 401:
                        return _relay_failure(
                            tool, "AUTH_REQUIRED",
                            "the server rejected the configured bearer token "
                            "(check AURA_SERVER_AUTH_TOKEN in the project .env)",
                        )

                    detail = ""

                    if isinstance(body, dict):
                        detail = str(body.get("detail", ""))

                    return _relay_failure(
                        tool,
                        "INVALID_ARGUMENTS" if error.code == 422
                        else "EXECUTION_FAILED",
                        detail or f"server returned HTTP {error.code}",
                    )

                except Exception as error:
                    return _relay_failure(
                        tool, "BRIDGE_UNREACHABLE",
                        f"{type(error).__name__}: {error}",
                    )

            def status(self) -> dict:
                """Mirror the server's live capability inventory.

                The HTTP harness still uses the local ToolExecutor gate, so
                its gate must be grounded in the same runtime facts as the
                server route.  This is a read-only inventory request; it
                never fabricates device availability when the server has no
                companion heartbeat.
                """

                import urllib.error
                import urllib.request

                request = urllib.request.Request(
                    f"{self.base_url}/api/capabilities",
                    headers=self._headers(),
                    method="GET",
                )

                try:
                    with urllib.request.urlopen(request, timeout=10) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                except Exception as error:
                    return {
                        "state": "UNKNOWN",
                        "healthy": False,
                        "reason": f"capability inventory unavailable: {type(error).__name__}",
                        "permissions": {},
                    }

                capabilities = {}
                permissions = {}
                for capability_id, value in payload.items():
                    if not isinstance(value, dict):
                        continue
                    capabilities[capability_id] = {
                        "state": value.get("state", "UNKNOWN"),
                        "healthy": value.get("health") == "healthy",
                        "reason": value.get("reason", ""),
                    }
                    for permission in value.get("required_permissions", []):
                        permissions[permission] = (
                            value.get("authorization") == "granted"
                        )

                states = {
                    str(value.get("state", "UNKNOWN"))
                    for value in capabilities.values()
                }
                if "AVAILABLE" in states:
                    state = "AVAILABLE"
                elif "UNAVAILABLE" in states:
                    state = "UNAVAILABLE"
                elif "BLOCKED_PERMISSION" in states:
                    state = "BLOCKED_PERMISSION"
                else:
                    state = "UNKNOWN"

                return {
                    "state": state,
                    "healthy": any(
                        bool(value.get("healthy"))
                        for value in capabilities.values()
                    ),
                    "reason": "server capability inventory",
                    "permissions": permissions,
                    "capabilities": capabilities,
                }

            def _headers(self) -> dict:
                headers = {"Content-Type": "application/json"}
                if self.token:
                    headers["Authorization"] = f"Bearer {self.token}"
                return headers

            @staticmethod
            def _body_of(error):
                """The JSON body of an error response, or None."""

                try:
                    return json.loads(error.read().decode("utf-8"))
                except Exception:
                    return None

        return HttpRelay(_env("AURA_SERVER_URL", "http://127.0.0.1:8000"))

    print(f"unknown bridge {name!r} (use loopback or http)", file=sys.stderr)
    raise SystemExit(2)


def _env(key: str, default: str) -> str:
    return os.environ.get(key) or default


def configured_auth_token(env_file: Path | None = None) -> str:
    """Resolve the server token without copying it into another shell."""

    # Preserve the old automation variable as the highest-priority explicit
    # override, then use the server's real environment name. Only if neither
    # is exported do we read the same project-root .env the server reads.
    exported = os.environ.get("AURA_TOKEN") or os.environ.get(
        "AURA_SERVER_AUTH_TOKEN"
    )
    if exported:
        return exported

    path = env_file or Path(__file__).resolve().parent.parent / ".env"
    configured = dotenv_values(path).get("AURA_SERVER_AUTH_TOKEN")
    return str(configured or "")


def _relay_failure(tool: str, code: str, message: str) -> dict:
    """A relay-authored failure, in the same envelope the device uses."""

    return {
        "ok": False,
        "tool": tool,
        "error": {"code": code, "message": message},
    }


# PART 18: a script must be able to branch on WHICH failure happened
# without parsing prose, so the distinctions that call for different
# handling get different codes. Everything unrecognised is 1 - a
# structured failure whose class this table has no opinion about.
EXIT_CODES = {
    "BRIDGE_UNREACHABLE": 3,          # no server / no route
    "AUTH_REQUIRED": 4,               # wrong or missing token
    "TOOL_NOT_FOUND": 5,              # not in the registry
    "UNKNOWN_TOOL": 5,
    "INVALID_ARGUMENTS": 6,           # the call was malformed
    "TIMEOUT": 7,                     # device never answered
    "CAPABILITY_UNAVAILABLE": 8,      # device cannot do this right now
    "SCREEN_CAPTURE_UNAVAILABLE": 8,
    "ACCESSIBILITY_ROOT_UNAVAILABLE": 8,
    "BRIDGE_UNAVAILABLE": 8,
    "CANCELLED": 9,                   # the run went away
    "STALE_RUN": 9,
}


def exit_code_for(report: dict) -> int:
    """The process exit code this report deserves."""

    if report.get("ok"):
        return 0

    code = str((report.get("error") or {}).get("code", ""))

    return EXIT_CODES.get(code, 1)


def build_registry(bridge) -> ToolRegistry:

    registry = ToolRegistry()
    AndroidProvider(bridge).register_into(registry)
    return registry


def build_executor(bridge) -> ToolExecutor:
    """Build the same executor gate used by the server device route."""
    registry = build_registry(bridge)
    return ToolExecutor(
        registry=registry,
        policy=ToolPolicy(
            enabled=True,
            allowed=frozenset(registry.names()),
            auto_approve=frozenset({
                ToolRisk.SAFE, ToolRisk.SENSITIVE, ToolRisk.DANGEROUS,
            }),
        ),
    )


# ----------------------------------------------------------------------
# Command surface - CLI verbs map onto registry tools, never around them
# ----------------------------------------------------------------------

# Every CLI verb is an already-declared tool plus an argument mapping.
# Adding a capability on the device surfaces here automatically through
# `tools`; this table only exists to give humans short verbs.
COMMANDS = {
    ("app", "current"):   ("android.get_foreground_app", {}),
    ("app", "launch"):    ("android.launch_app", {"package": "package"}),
    ("ui", "tree"):       ("android.get_ui_tree", {}),
    ("ui", "find"):       ("android.find_node", {"text": "text"}),
    ("ui", "tap"):        ("android.tap", {"text": "text",
                                          "node_id": "node"}),
    ("ui", "long-press"): ("android.long_press", {"text": "text",
                                                  "node_id": "node"}),
    ("ui", "swipe"):      ("android.swipe", {"direction": "direction"}),
    ("input", "type"):    ("android.type_text", {"text": "text"}),
    ("input", "key"):     ("android.press_key", {"key": "key"}),
    ("nav", "back"):      ("android.back", {}),
    ("nav", "home"):      ("android.home", {}),
    ("screen", "capture"): ("android.screenshot", {}),
    ("wait",):            ("android.wait_for", {"condition": "condition"}),
    ("verify",):          ("android.verify", {"check": "check"}),
}


def resolve(group, verb):
    """The tool a CLI verb maps to, or None."""

    if verb is None:
        return COMMANDS.get((group,))

    return COMMANDS.get((group, verb))


def invoke(executor, tool_name, arguments, as_json: bool,
           dry_run: bool) -> int:
    """
    One invocation, reported either for humans or for machines.

    Exit codes are part of the contract: 0 success, 1 structured failure
    - so shell scripts can branch on outcomes without parsing prose,
    exactly the property the agent's own envelopes have.
    """

    tool = executor.registry.get(tool_name)

    if tool is None:
        report = {"ok": False, "tool": tool_name,
                  "error": {"code": "TOOL_NOT_FOUND",
                            "message": f"no tool {tool_name!r}"}}
        _emit(report, as_json)
        return exit_code_for(report)

    if dry_run and tool_name in MUTATING:
        _emit({"ok": True, "tool": tool_name, "dry_run": True,
               "would_invoke": {"arguments": arguments}},
              as_json)
        return 0

    result = executor.execute(tool_name, arguments)
    report = dict(result.data) if result.data else {
        "ok": bool(result.ok), "tool": tool_name,
    }

    if not result.ok and "error" not in report:
        report["error"] = {"code": "TOOL_ERROR", "message": result.error}

    _emit(report, as_json)

    return exit_code_for(report)


def _emit(report: dict, as_json: bool) -> None:

    # A real accessibility tree is allowed to contain any Unicode the
    # foreground app exposes. Windows PowerShell commonly starts Python with
    # a cp1252 stdout, which made a successful device call fail while merely
    # rendering (for example) Vietnamese UI text. Keep the wire report
    # unchanged and make the CLI's text boundary UTF-8 when the stream
    # supports reconfiguration; test capture streams do not need it.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    if as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return

    if not report.get("ok"):
        error = report.get("error") or {}
        print(f"FAILED {report.get('tool')}: "
              f"{error.get('code')} - {error.get('message', '')}")
        return

    if report.get("dry_run"):
        print(f"[dry-run] would invoke {report['tool']} "
              f"{json.dumps(report['would_invoke']['arguments'])}")
        return

    for key, value in (report.get("result") or {}).items():
        print(f"{key}: {value}")

    postcondition = report.get("postcondition")

    if isinstance(postcondition, dict):
        print(f"verified: {bool(postcondition.get('verified'))}")


# ----------------------------------------------------------------------
# The REPL (PART 21)
# ----------------------------------------------------------------------

def repl(bridge, as_json: bool) -> int:
    """
    An interactive session over one bridge.

    Lines are plain invocations - `tap Search`, `type Minecraft`,
    `verify text_visible=Minecraft` - so a developer can walk a real
    workflow step by step and watch state change between steps.
    """

    executor = build_executor(bridge)
    registry = executor.registry

    print("aura-android REPL - try: current | find Search | tap Search "
          "| type Minecraft | verify text_visible=Minecraft | quit")

    while True:
        try:
            line = input("aura-android> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not line:
            continue

        if line in ("quit", "exit"):
            return 0

        if line == "tools":
            for name in registry.names():
                tool = registry.get(name)
                print(f"{name} ({getattr(tool.risk, 'value', '?')})")
            continue

        parts = line.split(maxsplit=1)
        verb = parts[0].lower()
        argument_text = parts[1] if len(parts) > 1 else ""

        mapped = {
            "current": ("android.get_foreground_app", {}),
            "tree": ("android.get_ui_tree", {}),
            "find": ("android.find_node", {"text": argument_text}),
            "tap": ("android.tap", {"text": argument_text}),
            "type": ("android.type_text", {"text": argument_text}),
            "key": ("android.press_key", {"key": argument_text}),
            "back": ("android.back", {}),
            "home": ("android.home", {}),
            "launch": ("android.launch_app", {"package": argument_text}),
            "swipe": ("android.swipe", {"direction": argument_text}),
            "capture": ("android.screenshot", {}),
            "wait": ("android.wait_for", {"condition": argument_text}),
            "verify": ("android.verify", {"check": argument_text}),
        }

        entry = mapped.get(verb)

        if entry is None:
            print(f"unknown command {verb!r} - try 'tools'")
            continue

        # A failed step is information in a session, not a reason to
        # evict the user from it.
        invoke(executor, entry[0], entry[1], as_json, dry_run=False)


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

def main(argv=None) -> int:

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    parser = argparse.ArgumentParser(
        prog="aura-android",
        description="Android capability harness - the agent's tools "
                    "without the LLM",
    )

    parser.add_argument("--json", action="store_true",
                        help="machine-readable output")
    parser.add_argument("--dry-run", action="store_true",
                        help="mutations report what would happen")
    parser.add_argument("--bridge", default="loopback",
                        choices=["loopback", "http"],
                        help="device transport (default loopback)")
    parser.add_argument("--demo", action="store_true",
                        help="seed the loopback with a YouTube screen")

    parser.add_argument("group", help="app|ui|input|nav|screen|wait|"
                                     "verify|tools|repl")
    parser.add_argument("verb", nargs="?", default=None)
    parser.add_argument("value", nargs="?", default=None)

    args = parser.parse_args(argv)

    if args.group == "repl":
        return repl(build_bridge(args.bridge), args.json)

    bridge = build_bridge(args.bridge)
    executor = build_executor(bridge)

    if args.demo and hasattr(bridge, "install_screen"):
        bridge.install_screen("com.android.launcher", {
            "yt_icon": {"text": "YouTube", "clickable": True},
            "search_btn": {"text": "Search", "clickable": True},
        })

    if args.group == "tools":
        # Discover: what this deployment can do, and at what risk.
        if args.json:
            print(json.dumps([
                {"name": name,
                 "risk": getattr(registry.get(name).risk, "value")}
                for name in registry.names()
            ], indent=2))
        else:
            for name in registry.names():
                tool = registry.get(name)
                print(f"{name} ({getattr(tool.risk, 'value', '?')})")
        return 0

    entry = resolve(args.group, args.verb)

    if entry is None:
        parser.error(f"unknown command {args.group} {args.verb or ''}")

    tool_name, argument_names = entry

    arguments = {}
    if args.value is not None:
        first_key = next(iter(argument_names.values()))
        arguments[first_key] = args.value

    return invoke(executor, tool_name, arguments, args.json, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
