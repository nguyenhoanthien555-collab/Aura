"""
Notification collection.

The companion engine decides Aura should say something; this is where a
device comes to find out. Polling rather than pushing, for the MVP: a
phone that has been asleep for ten minutes reconnects, asks once, and
gets whatever is still worth hearing.

Collection is destructive. A notification handed to a device is gone, so
a device that polls twice never sees the same remark twice.
"""

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from server.auth import verify_token
from server.runtime import get_runtime


router = APIRouter(prefix="/api", tags=["notifications"])


class NotificationsResponse(BaseModel):
    """Everything waiting for one device."""

    notifications: List[Dict[str, Any]] = Field(default_factory=list)
    count: int = 0
    companion_enabled: bool = False


@router.get("/notifications", response_model=NotificationsResponse)
async def get_notifications(
    device_id: str = Query(default="", max_length=128),
    token: str = Depends(verify_token),
):
    """
    Collect pending notifications for this device.

    Returns an empty list rather than an error when the companion is off,
    so a client can poll unconditionally without branching on server
    configuration.
    """

    runtime = get_runtime()

    pending = runtime.notifications.drain(device_id)

    return NotificationsResponse(
        notifications=[item.as_dict() for item in pending],
        count=len(pending),
        companion_enabled=runtime.companion_engine is not None,
    )
