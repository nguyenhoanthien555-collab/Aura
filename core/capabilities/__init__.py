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

    if cap.discovery_metadata.get("implemented") is False:
        registry.update_state(
            capability_id,
            CapabilityState.NOT_IMPLEMENTED,
            cap.discovery_metadata.get("state_reason", "implementation missing"),
        )
        cap.authorization_state = "unknown"
        cap.health_state = "unknown"
        cap.execution_state = "not_attempted"
        return CapabilityState.NOT_IMPLEMENTED
        
    missing = permissions.missing_details(cap.required_permissions)
    if missing:
        details = []
        for permission, reason in missing:
            details.append(f"{permission} ({reason})" if reason else permission)
        registry.update_state(
            capability_id,
            CapabilityState.BLOCKED_PERMISSION,
            f"Missing permissions: {', '.join(details)}",
        )
        cap.authorization_state = "missing"
        cap.health_state = "unknown"
        cap.execution_state = "not_attempted"
        return CapabilityState.BLOCKED_PERMISSION
        
    health_result = health.run_check(capability_id)
    reported_state = health_result.get("state")
    if reported_state:
        try:
            state = CapabilityState(str(reported_state))
        except ValueError:
            state = CapabilityState.UNKNOWN
        if state != CapabilityState.AVAILABLE:
            registry.update_state(capability_id, state, health_result.get("reason", ""))
            cap.authorization_state = "granted"
            cap.health_state = state.value.lower()
            cap.execution_state = "not_attempted"
            return state

    if not health_result.get("healthy", True):
        registry.update_state(capability_id, CapabilityState.UNHEALTHY, health_result.get("reason", "Unknown health error"))
        cap.authorization_state = "granted"
        cap.health_state = "unhealthy"
        cap.execution_state = "not_attempted"
        return CapabilityState.UNHEALTHY
        
    registry.update_state(capability_id, CapabilityState.AVAILABLE)
    cap.authorization_state = "granted"
    cap.health_state = "healthy"
    cap.execution_state = "available"
    return CapabilityState.AVAILABLE
