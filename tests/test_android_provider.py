"""
Regression tests for the Android capability provider (migration
PARTS 2, 3, 4, 16, 31).

The properties under test: one registry holds the whole android.*
family; schemas are valid for native function calling; dispatch through
a bridge produces structured results; failures carry stable codes; and
the policy gates apply to Android exactly as to every other tool.
"""

from tools.base import ToolRisk
from tools.executor import ToolExecutor, ToolPolicy
from tools.providers.android_bridge import LoopbackDeviceBridge
from tools.providers.android_provider import AndroidProvider
from tools.registry import ToolRegistry
from tools.schema import openai_function_schema


EXPECTED_TOOLS = {
    "android.get_foreground_app",
    "android.get_ui_tree",
    "android.find_node",
    "android.screenshot",
    "android.tap",
    "android.long_press",
    "android.swipe",
    "android.type_text",
    "android.press_key",
    "android.back",
    "android.home",
    "android.launch_app",
    "android.wait_for",
    "android.verify",
    "android.list_apps",
}


def make_executor(bridge=None):
    """A registry + executor with every Android tool allowed."""

    bridge = bridge or LoopbackDeviceBridge()
    registry = ToolRegistry()

    count = AndroidProvider(bridge).register_into(registry)

    policy = ToolPolicy.from_config({
        "enabled": True,
        "allowed": sorted(EXPECTED_TOOLS),
        "auto_approve": ["safe", "sensitive", "dangerous"],
    })

    executor = ToolExecutor(registry=registry, policy=policy)

    return bridge, registry, executor, count


# ----------------------------------------------------------------------
# Registry discovery (PART 2)
# ----------------------------------------------------------------------

def test_the_whole_android_family_registers_into_one_registry():

    _, registry, _, count = make_executor()

    assert count == len(EXPECTED_TOOLS)
    assert set(registry.names()) == EXPECTED_TOOLS


def test_a_provider_without_a_bridge_registers_nothing():

    registry = ToolRegistry()

    assert AndroidProvider(None).register_into(registry) == 0
    assert len(registry) == 0


# ----------------------------------------------------------------------
# Schemas for native function calling (PART 1)
# ----------------------------------------------------------------------

def test_every_android_tool_exports_a_valid_openai_schema():

    _, registry, _, _ = make_executor()

    for name in EXPECTED_TOOLS:
        schema = openai_function_schema(registry.get(name))

        function = schema["function"]

        assert schema["type"] == "function"
        assert function["name"] == name
        assert function["parameters"]["type"] == "object"
        assert isinstance(function["description"], str)


def test_required_arguments_appear_in_the_schema():

    _, registry, _, _ = make_executor()

    launch = openai_function_schema(registry.get("android.launch_app"))

    assert launch["function"]["parameters"]["required"] == ["package"]


def test_tap_advertises_both_targeting_modes_but_requires_neither():
    """node_id or text - a schema requiring both would be wrong."""

    _, registry, _, _ = make_executor()

    tap = openai_function_schema(registry.get("android.tap"))["function"]
    parameters = tap["parameters"]

    assert set(parameters["properties"]) == {"node_id", "text"}
    assert "required" not in parameters


# ----------------------------------------------------------------------
# Risk classification (PART 16)
# ----------------------------------------------------------------------

def test_reads_are_safe_pixels_sensitive_and_mutations_dangerous():

    _, registry, _, _ = make_executor()

    assert registry.get("android.get_foreground_app").risk is ToolRisk.SAFE
    assert registry.get("android.wait_for").risk is ToolRisk.SAFE

    assert registry.get("android.screenshot").risk is ToolRisk.SENSITIVE


    for mutation in ("android.tap", "android.launch_app", "android.type_text"):
        assert registry.get(mutation).risk is ToolRisk.DANGEROUS

# ----------------------------------------------------------------------
# Dispatch and structured results (PARTS 4, 8)
# ----------------------------------------------------------------------

def test_read_dispatch_returns_structured_result():

    bridge, _, executor, _ = make_executor()
    bridge.foreground_package = "com.google.android.youtube"

    result = executor.execute("android.get_foreground_app")

    assert result.ok
    assert '"com.google.android.youtube"' in result.output
    assert result.data["ok"] is True
    assert result.data["result"]["package"] == "com.google.android.youtube"


def test_failed_lookup_returns_structured_node_not_found():

    bridge, _, executor, _ = make_executor()
    bridge.install_screen("com.aura.companion", {})

    result = executor.execute("android.find_node", {"text": "Search"})

    assert not result.ok
    assert result.data["error"]["code"] == "NODE_NOT_FOUND"
    assert "Search" in result.error


def test_mutation_carries_postcondition_evidence():

    bridge, _, executor, _ = make_executor()
    bridge.install_screen("com.example", {
        "ok_button": {"text": "OK", "clickable": True},
    })

    result = executor.execute("android.tap", {"text": "OK"})

    assert result.ok
    assert result.data["postcondition"] == {"verified": True}
    assert result.data["verified"] is True


def test_launch_settles_and_wait_for_proves_it():
    """
    The settle semantics the real device has: launch returns before the
    app is foreground, and wait_for is what proves it landed.
    """

    clock = {"now": 1000.0}
    bridge = LoopbackDeviceBridge(clock=lambda: clock["now"])

    _, _, executor, _ = make_executor(bridge)

    launch = executor.execute(
        "android.launch_app", {"package": "com.google.android.youtube"}
    )
    assert launch.ok
    assert launch.data["postcondition"]["verified"] is False

    # Before the settle elapses the foreground is still the old app.
    wait_early = executor.execute(
        "android.wait_for",
        {"condition": "foreground=com.google.android.youtube"},
    )
    assert wait_early.data["postcondition"]["verified"] is False

    clock["now"] += 1.0

    wait_settled = executor.execute(
        "android.wait_for",
        {"condition": "foreground=com.google.android.youtube"},
    )
    assert wait_settled.data["postcondition"]["verified"] is True


def test_verify_checks_map_to_loopback_state():

    bridge, _, executor, _ = make_executor()
    bridge.foreground_package = "com.google.android.youtube"
    bridge.install_screen("com.google.android.youtube", {
        "search": {"text": "Search", "clickable": True},
    })

    for check, expected in (
        ("package_is=com.google.android.youtube", True),
        ("package_is=com.other", False),
        ("text_visible=Search", True),
        ("node_exists=search", True),
        ("node_exists=nope", False),
    ):
        result = executor.execute("android.verify", {"check": check})
        assert result.ok, check
        assert result.data["result"]["met"] is expected, check


# ----------------------------------------------------------------------
# Policy gating (PARTS 16, 17)
# ----------------------------------------------------------------------

def test_unlisted_tools_are_denied_by_policy_not_silent():

    registry = ToolRegistry()
    AndroidProvider(LoopbackDeviceBridge()).register_into(registry)

    # enabled but nothing allowed: the shipped default posture.
    executor = ToolExecutor(
        registry=registry,
        policy=ToolPolicy(
            enabled=True,
            auto_approve=frozenset({ToolRisk.SAFE}),
        ),
    )

    result = executor.execute("android.get_foreground_app")

    assert not result.ok


def test_risky_calls_need_approval_when_not_auto_approved():
    """The gate is consulted; a refusal from it denies the call."""

    approvals = []

    def confirm(tool, arguments):
        approvals.append((tool.name if hasattr(tool, "name") else tool,
                          dict(arguments)))
        return False          # the owner says no

    registry = ToolRegistry()
    AndroidProvider(LoopbackDeviceBridge()).register_into(registry)

    executor = ToolExecutor(
        registry=registry,
        policy=ToolPolicy.from_config({
            "enabled": True,
            "allowed": ["android.launch_app"],
            "auto_approve": ["safe"],
        }),
        confirm=confirm,
    )

    result = executor.execute(
        "android.launch_app", {"package": "com.any"}
    )

    assert not result.ok
    assert len(approvals) == 1     # the gate was consulted


def test_unknown_tool_is_a_structured_failure_not_an_exception():

    _, _, executor, _ = make_executor()

    result = executor.execute("android.does_not_exist", {})

    assert not result.ok
