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

    The poll is also what gives the proactive engine its chance to speak,
    and it happens *before* the drain so that a message decided on this
    poll leaves on this poll rather than waiting for the next one. This
    reuses the transport a client already runs instead of adding a second
    one; the cost is that Aura can only consider speaking while something
    is polling, which `ServerRuntime.consider_proactive` documents.

    A proactive decision is not reported in the response. A device needs
    the notifications it has to display, not the reasoning behind the
    silence it mostly gets.
    """

    runtime = get_runtime()

    runtime.consider_proactive()

    pending = runtime.notifications.drain(device_id)

    return NotificationsResponse(
        notifications=[item.as_dict() for item in pending],
        count=len(pending),
        companion_enabled=runtime.companion_engine is not None,
    )
