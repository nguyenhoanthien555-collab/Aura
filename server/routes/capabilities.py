from fastapi import APIRouter, Depends
from server.auth import verify_token
from core.capabilities.introspection import get_introspection_service

router = APIRouter(prefix="/api/capabilities", tags=["capabilities"])

@router.get("")
async def list_capabilities(token: str = Depends(verify_token)):
    """
    Returns the live capability inventory.
    """
    service = get_introspection_service()
    return service.get_inventory()
