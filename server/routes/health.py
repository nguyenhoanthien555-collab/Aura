"""
Health check endpoints.

Two questions, deliberately kept apart:

  * liveness  - is this process running? (`/` and `/api/health`)
  * readiness - can it serve a chat turn? (`/api/ready`)

`/api/health` is authenticated and its key set is a pinned contract, so
readiness is a separate route rather than more keys on that one. It is
unauthenticated for the same reason `/` is: a container healthcheck and
Render's probe cannot hold a bearer token, and the response is a boolean
plus failure categories - no configuration, no versions, no secrets.
"""
from fastapi import APIRouter, Depends, Response
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


@router.get("/ready")
async def readiness_check(response: Response):
    """
    Readiness: can this process actually answer a chat request?

    503 when it cannot, so an orchestrator can act on it without parsing
    the body. Optional collaborators (vision, voice, screen, companion)
    are excluded on purpose - none of them stops a chat turn, and a probe
    that fails when TTS is off would restart a healthy server forever.
    """

    state = get_runtime().readiness()

    if not state["ready"]:
        response.status_code = 503

    return state
