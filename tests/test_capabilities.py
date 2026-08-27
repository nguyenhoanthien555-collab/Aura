import pytest
from core.capabilities import registry, permissions, health, resolve_capability
from core.capabilities.models import Capability, CapabilityState
from core.capabilities.discovery import discovery
from core.capabilities.adapters import OpenVikingAdapter, MCPAdapter
from core.capabilities.introspection import get_introspection_service, CapabilityIntrospectionService
from tools.base import Tool, ToolResult, ToolRisk
from tools.executor import ToolExecutor, ToolPolicy
from tools.registry import ToolRegistry
from brain.prompt_builder import PromptBuilder
from brain.message import Message


class DummyTool(Tool):
    name = "dummy_tool"
    description = "A dummy tool"
    risk = ToolRisk.SAFE
    capability = "dummy.cap"
    
    def execute(self, **kwargs):
        return "Dummy execution"


class LegacyTool(Tool):
    name = "legacy_tool"
    description = "A legacy tool with no capability"
    risk = ToolRisk.SAFE
    # No capability attribute
    
    def execute(self, **kwargs):
        return "Legacy execution"


@pytest.fixture()
def reset_registry():
    registry.clear()
    permissions._granted_permissions.clear()
    permissions._checks.clear()
    health._checks.clear()
    yield


def test_capabilities_tool_without_capability_is_blocked(reset_registry):
    policy = ToolPolicy(enabled=True, allowed=frozenset(["legacy_tool"]), auto_approve=frozenset([ToolRisk.SAFE]))
    tool_reg = ToolRegistry([LegacyTool()])
    executor = ToolExecutor(registry=tool_reg, policy=policy)
    
    result = executor.execute("legacy_tool")
    assert result.ok is False
    assert "not implemented or registered" in result.error
    assert result.execution == "not_attempted"


def test_capabilities_missing_permission_is_blocked(reset_registry):
    cap = Capability(capability_id="dummy.cap", name="Dummy", description="", category="", required_permissions=["dummy_perm"])
    registry.register(cap)
    
    state = resolve_capability("dummy.cap")
    assert state == CapabilityState.BLOCKED_PERMISSION
    
    policy = ToolPolicy(enabled=True, allowed=frozenset(["dummy_tool"]), auto_approve=frozenset([ToolRisk.SAFE]))
    tool_reg = ToolRegistry([DummyTool()])
    executor = ToolExecutor(registry=tool_reg, policy=policy)
    
    result = executor.execute("dummy_tool")
    assert result.ok is False
    assert "permission denied for capability dummy.cap" in result.error
    assert result.authorization == "missing"


def test_capabilities_unhealthy_dependency_is_blocked(reset_registry):
    cap = Capability(capability_id="dummy.cap", name="Dummy", description="", category="")
    registry.register(cap)
    
    health.register_check("dummy.cap", lambda: {"healthy": False, "reason": "Database down"})
    
    state = resolve_capability("dummy.cap")
    assert state == CapabilityState.UNHEALTHY
    
    policy = ToolPolicy(enabled=True, allowed=frozenset(["dummy_tool"]), auto_approve=frozenset([ToolRisk.SAFE]))
    tool_reg = ToolRegistry([DummyTool()])
    executor = ToolExecutor(registry=tool_reg, policy=policy)
    
    result = executor.execute("dummy_tool")
    assert result.ok is False
    assert "is currently unhealthy" in result.error


def test_capabilities_healthy_authorized_capability_executes(reset_registry):
    cap = Capability(capability_id="dummy.cap", name="Dummy", description="", category="")
    registry.register(cap)
    
    policy = ToolPolicy(enabled=True, allowed=frozenset(["dummy_tool"]), auto_approve=frozenset([ToolRisk.SAFE]))
    tool_reg = ToolRegistry([DummyTool()])
    executor = ToolExecutor(registry=tool_reg, policy=policy)
    
    result = executor.execute("dummy_tool")
    assert result.ok is True
    assert result.output == "Dummy execution"
    assert result.capability == "dummy.cap"
    assert result.authorization == "granted"
    assert result.execution == "completed"


def test_capabilities_capability_discovery_ranking(reset_registry):
    registry.register(Capability(capability_id="cap1", name="Read Screen", description="Reads the computer screen", category="vision"))
    registry.register(Capability(capability_id="cap2", name="Read File", description="Reads a file from disk", category="filesystem"))
    
    results = discovery.discover("I want to see what is on my screen")
    assert len(results) > 0
    assert results[0]["capability_id"] == "cap1"


def test_capabilities_external_capability_registration(reset_registry):
    adapter = MCPAdapter()
    adapter.sync_capabilities([{"name": "mcp_search", "description": "Search the web"}])
    
    cap = registry.get("mcp.mcp_search")
    assert cap is not None
    assert cap.name == "mcp_search"
    assert cap.category == "mcp"


def test_capabilities_runtime_capability_inventory(reset_registry):
    registry.register(Capability(capability_id="cap1", name="Read Screen", description="Reads the computer screen", category="vision"))
    
    desc = registry.describe()
    assert "Read Screen" in desc
    assert "AVAILABLE" in desc


def test_introspection_service_distinguishes_states(reset_registry):
    registry.register(Capability(capability_id="cap.avail", name="Avail", description="Available cap", category="test"))
    registry.register(Capability(capability_id="cap.perm", name="Perm", description="Blocked perm", category="test", required_permissions=["missing.perm"]))
    registry.register(Capability(capability_id="cap.unavail", name="Unavail", description="Unavailable cap", category="test"))
    health.register_check("cap.unavail", lambda: {"healthy": False, "reason": "No companion poll heartbeat detected"})

    service = CapabilityIntrospectionService()
    inv = service.get_inventory()

    assert inv["cap.avail"]["state"] == "AVAILABLE"
    assert inv["cap.perm"]["state"] == "BLOCKED_PERMISSION"
    assert "missing.perm" in inv["cap.perm"]["reason"]
    assert inv["cap.unavail"]["state"] == "UNAVAILABLE"
    assert "heartbeat" in inv["cap.unavail"]["reason"]

    summary = service.render_summary()
    assert "### AVAILABLE NOW (ACTUALLY EXECUTABLE):" in summary
    assert "Avail" in summary
    assert "### BLOCKED BY MISSING PERMISSION:" in summary
    assert "Perm" in summary
    assert "### CURRENTLY UNAVAILABLE:" in summary
    assert "Unavail" in summary


def test_dynamic_state_transitions_without_stale_cache(reset_registry):
    heartbeat_active = True
    permission_granted = True

    registry.register(Capability(
        capability_id="android.test_cap",
        name="Android Test",
        description="A test android capability",
        category="android",
        required_permissions=["android.accessibility"],
    ))

    def dynamic_health():
        if heartbeat_active:
            return {"healthy": True, "reason": ""}
        return {"healthy": False, "reason": "no Android companion poll heartbeat has been received"}

    def dynamic_perm():
        return permission_granted, "" if permission_granted else "user revoked accessibility"

    health.register_check("android.test_cap", dynamic_health)
    permissions.register_check("android.accessibility", dynamic_perm)

    service = CapabilityIntrospectionService()

    # State 1: All available
    inv1 = service.get_inventory()
    assert inv1["android.test_cap"]["state"] == "AVAILABLE"

    # State 2: Heartbeat lost
    heartbeat_active = False
    inv2 = service.get_inventory()
    assert inv2["android.test_cap"]["state"] == "UNAVAILABLE"
    assert "no Android companion poll heartbeat has been received" in inv2["android.test_cap"]["reason"]

    # State 3: Heartbeat restored
    heartbeat_active = True
    inv3 = service.get_inventory()
    assert inv3["android.test_cap"]["state"] == "AVAILABLE"
    assert inv3["android.test_cap"]["reason"] == ""

    # State 4: Permission revoked
    permission_granted = False
    inv4 = service.get_inventory()
    assert inv4["android.test_cap"]["state"] == "BLOCKED_PERMISSION"
    assert "android.accessibility" in inv4["android.test_cap"]["reason"]

    # State 5: Permission restored
    permission_granted = True
    inv5 = service.get_inventory()
    assert inv5["android.test_cap"]["state"] == "AVAILABLE"


def test_prompt_builder_injects_live_capabilities():
    builder = PromptBuilder()
    prompt = builder.build(
        history=[],
        user_message=Message(role="user", content="What can you do?"),
        capabilities="AVAILABLE NOW:\n- Android UI Tree: inspect screen",
    )

    assert "===== LIVE CAPABILITIES =====" in prompt
    assert "AVAILABLE NOW:" in prompt
    assert "Android UI Tree: inspect screen" in prompt
