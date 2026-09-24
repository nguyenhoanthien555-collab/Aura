"""
The confirmation channel.

A dangerous tool call does not run on the model's say-so. The agent pauses
the task, persists a DurableConfirmation, and waits. This is where a human
comes to see what Aura wants to do and say yes or no - the last mile that
was missing, and the reason DANGEROUS tools were simply refused in server
mode ("no human to ask"). Now there is somewhere to ask.

Two endpoints, both authenticated:

    GET  /api/confirmations                     what is waiting for a yes/no
    POST /api/confirmations/{id}/resolve         the human's answer

The list deliberately exposes only the REDACTED arguments - the same
redaction the record already carries - so a secret handed to a tool is not
re-surfaced to whatever renders the prompt. Resolving APPROVED lets the
paused task proceed; REJECTED ends that step honestly.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from server.auth import verify_token


router = APIRouter(prefix="/api", tags=["confirmations"])


class PendingConfirmation(BaseModel):
    confirmation_id: str
    task_id: str
    step_id: str = ""
    tool: str = ""
    risk: str = "dangerous"
    side_effect: str = "mutating"
    description: str = ""
    # Redacted only - never the raw arguments.
    arguments: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    expires_at: str = ""


class ConfirmationsResponse(BaseModel):
    confirmations: List[PendingConfirmation] = Field(default_factory=list)
    count: int = 0


class ResolveRequest(BaseModel):
    decision: str = Field(description="APPROVED or REJECTED")
    reason: Optional[str] = None


class ResolveResponse(BaseModel):
    confirmation_id: str
    status: str
    decision: str = ""


def _safe_view(conf) -> PendingConfirmation:
    return PendingConfirmation(
        confirmation_id=conf.confirmation_id,
        task_id=conf.task_id,
        step_id=conf.step_id,
        tool=conf.tool,
        risk=conf.risk,
        side_effect=conf.side_effect,
        description=conf.description,
        arguments=conf.redacted_arguments or {},
        created_at=conf.created_at,
        expires_at=conf.expires_at,
    )


@router.get("/confirmations", response_model=ConfirmationsResponse)
async def list_confirmations(token: str = Depends(verify_token)):
    """Everything Aura is waiting on a human yes/no for, oldest first."""
    from server.routes.agent import get_task_runtime

    runtime = get_task_runtime()
    pending = runtime.list_pending_confirmations()
    return ConfirmationsResponse(
        confirmations=[_safe_view(c) for c in pending],
        count=len(pending),
    )


@router.post("/confirmations/{confirmation_id}/resolve", response_model=ResolveResponse)
async def resolve_confirmation(
    confirmation_id: str,
    request: ResolveRequest,
    token: str = Depends(verify_token),
):
    """
    Record the human's yes/no. APPROVED releases the paused task to run the
    step; REJECTED ends it. Idempotent-ish: resolving an already-decided or
    unknown confirmation is a 404/409 rather than a silent success, so a
    double-tap on the phone cannot quietly re-approve.
    """
    decision = (request.decision or "").strip().upper()
    if decision not in ("APPROVED", "REJECTED"):
        raise HTTPException(status_code=422, detail="decision must be APPROVED or REJECTED")

    from server.routes.agent import get_task_runtime

    runtime = get_task_runtime()

    conf = runtime.get_confirmation(confirmation_id)
    if conf is None:
        raise HTTPException(status_code=404, detail="confirmation not found")
    if conf.status != "PENDING":
        raise HTTPException(
            status_code=409,
            detail=f"confirmation already {conf.status.lower()}",
        )

    try:
        resolved = runtime.resolve_confirmation(
            task_id=conf.task_id,
            decision=decision,
            confirmation_id=confirmation_id,
            decision_by="human",
            reason=request.reason,
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error))

    return ResolveResponse(
        confirmation_id=resolved.confirmation_id,
        status=resolved.status,
        decision=resolved.decision,
    )
