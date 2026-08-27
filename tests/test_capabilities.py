import pytest
from core.capabilities import registry, permissions, health, resolve_capability
from core.capabilities.models import Capability, CapabilityState
from core.capabilities.discovery import discovery
from core.capabilities.adapters import OpenVikingAdapter, MCPAdapter
from tools.base import Tool, ToolResult, ToolRisk
from tools.executor import ToolExecutor, ToolPolicy
from tools.registry import ToolRegistry

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
    # Reset permissions and health manually
    permissions._granted_permissions.clear()
    health._checks.clear()
    yield

def test_capabilities_tool_without_capability_is_blocked(reset_registry):
    # Legacy tool has no capability registered
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
    
    # Do NOT grant permission
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
