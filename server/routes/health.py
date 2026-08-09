"""
Health check endpoint.
"""
from fastapi import APIRouter, Depends
from server.runtime import get_runtime
from server.auth import verify_token


router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health_check(token: str = Depends(verify_token)):
    """
    Health check endpoint.

    Returns:
        status: "healthy" or "starting"
        version: App version
        uptime_seconds: Server uptime
        runtime: Component statuses
    """
    runtime = get_runtime()
    return runtime.health_status()