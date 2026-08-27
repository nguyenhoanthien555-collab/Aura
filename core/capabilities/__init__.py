from core.capabilities.models import Capability, CapabilityState
from core.capabilities.registry import CapabilityRegistry
from core.capabilities.permissions import PermissionResolver
from core.capabilities.health import HealthCheckRegistry

# Global instances for the capability architecture
registry = CapabilityRegistry()
permissions = PermissionResolver()
health = HealthCheckRegistry()

def resolve_capability(capability_id: str) -> CapabilityState:
    """
    Evaluates permission, dependency, and health states to determine
    the final availability state of a capability.
    """
    cap = registry.get(capability_id)
    if not cap:
        return CapabilityState.NOT_IMPLEMENTED
        
    missing_perms = permissions.check_capabilities_permissions(cap.required_permissions)
    if missing_perms:
        registry.update_state(capability_id, CapabilityState.BLOCKED_PERMISSION, f"Missing permissions: {', '.join(missing_perms)}")
        return CapabilityState.BLOCKED_PERMISSION
        
    health_result = health.run_check(capability_id)
    if not health_result.get("healthy", True):
        reason = health_result.get("reason", "Unknown health error")
        if "heartbeat" in reason.lower() or "gateway" in reason.lower():
            registry.update_state(capability_id, CapabilityState.UNAVAILABLE, reason)
            return CapabilityState.UNAVAILABLE
        else:
            registry.update_state(capability_id, CapabilityState.UNHEALTHY, reason)
            return CapabilityState.UNHEALTHY
        
    registry.update_state(capability_id, CapabilityState.AVAILABLE, "")
    return CapabilityState.AVAILABLE
