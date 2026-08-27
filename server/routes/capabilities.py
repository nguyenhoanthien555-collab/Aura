from fastapi import APIRouter, Depends
from server.auth import verify_token
from core.capabilities import registry, resolve_capability

router = APIRouter(prefix="/api/capabilities", tags=["capabilities"])

@router.get("")
async def list_capabilities(token: str = Depends(verify_token)):
    """
    Returns the live capability inventory.
    """
    # The Android registry is provider-owned, but the inventory endpoint is
    # itself a supported discovery surface. Initialize it here so a fresh
    # server reports live per-tool states before an agent step is requested.
    from server.routes.agent import get_device_registry
    get_device_registry()

    inventory = {}
    for cap in registry.all():
        # Resolve dynamic state before returning
        state = resolve_capability(cap.capability_id)
        
        inventory[cap.capability_id] = {
            "name": cap.name,
            "description": cap.description,
            "category": cap.category,
            "state": state.value,
            "required_permissions": cap.required_permissions,
            "required_dependencies": cap.required_dependencies,
            "authorization": cap.authorization_state,
            "health": cap.health_state,
            "execution": cap.execution_state,
            "reason": cap.discovery_metadata.get("state_reason", "")
        }
        
    return inventory
