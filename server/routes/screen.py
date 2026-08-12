"""
Screen observation endpoints.

A phone reports what is on its screen; the server records it and decides
whether that is worth saying anything about.

The route stays thin. It validates, authenticates, converts the request
into a `ScreenObservation`, and hands it to the runtime. Every decision
about relevance, throttling, duplicate suppression and cooldown lives in
`companion/`, and every decision about *seeing* lives in `vision/`.

Screen observation is off unless the server config turns it on, and a
disabled server answers 503 rather than silently accepting data it will
never look at.
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from core.logger import logger
from server.auth import verify_token
from server.config import settings
from server.runtime import get_runtime


router = APIRouter(prefix="/api", tags=["screen"])


class ScreenContextRequest(BaseModel):
    """One screen, as reported by a device."""

    session_id: str = Field(default="", max_length=128)
    device_id: str = Field(default="", max_length=128)
    application: Optional[str] = Field(default=None, max_length=256)
    package: Optional[str] = Field(default=None, max_length=256)
    screen_text: Optional[str] = Field(
        default=None,
        max_length=settings.max_screen_text_length,
    )
    accessibility_context: Optional[Dict[str, Any]] = None
    timestamp: Optional[float] = None


class ScreenDecision(BaseModel):
    """What Aura decided to do about a screen."""

    should_notify: bool = False
    reason: str = ""
    priority: str = "normal"
    message: str = ""
    confidence: float = 0.0
    cooldown: float = 0.0


class ScreenContextResponse(BaseModel):
    """
    The outcome of one observation.

    `decision` is returned even when Aura stayed quiet, and `reason`
    carries which gate stopped it. Without that the only way to tune
    thresholds would be to guess.
    """

    session_id: str
    status: str
    accepted: bool = False
    observation_id: Optional[str] = None
    decision: Optional[ScreenDecision] = None


def _flatten(context: Optional[Dict[str, Any]], limit: int) -> str:
    """
    An accessibility tree, as one line of text.

    Android hands over a nested structure whose shape varies by app. The
    companion only ever reads it as prose, so it is flattened here rather
    than modelled - a schema for "whatever the accessibility API returned"
    would be fiction.
    """

    if not context:
        return ""

    parts: list[str] = []

    def walk(value: Any, depth: int = 0) -> None:

        if depth > 6 or len(parts) > 400:
            return

        if isinstance(value, dict):
            for key, item in value.items():
                walk(item, depth + 1)

        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item, depth + 1)

        elif isinstance(value, str):
            text = value.strip()
            if text:
                parts.append(text)

        elif isinstance(value, (int, float, bool)):
            parts.append(str(value))

    walk(context)

    return " ".join(parts)[:limit]


# What a phone is allowed to upload. A deny-by-default list rather than a
# `startswith("image/")` check: SVG is an image by MIME type and a script
# host in practice, and nothing in the vision pipeline needs it.
ALLOWED_IMAGE_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/webp": "webp",
}


def _image_format(content_type: Optional[str]) -> str:
    """
    The format of an upload, or a 415 if we do not accept it.

    Validated rather than trusted: the content type decides which decoder
    the vision processor reaches for, so accepting an arbitrary string
    means accepting whatever the caller wants that decoder to be.
    """

    normalised = (content_type or "").split(";", 1)[0].strip().lower()

    if normalised not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=415,
            detail={
                "error": "unsupported_image_type",
                "allowed": sorted(ALLOWED_IMAGE_TYPES),
            },
        )

    return ALLOWED_IMAGE_TYPES[normalised]


def _require_screen(runtime):

    if not runtime.screen_enabled:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "screen_disabled",
                "message": (
                    "Screen observation is off. Enable server.screen.enabled "
                    "on the server to turn it on."
                ),
            },
        )


@router.post("/screen", response_model=ScreenContextResponse)
async def screen_context(
    request: ScreenContextRequest,
    token: str = Depends(verify_token),
):
    """
    Receive screen context from a device.

    The observation feeds the existing Vision pipeline - so the next turn
    of conversation already knows what the user is looking at - and then
    the companion engine decides, separately, whether it is worth
    interrupting them over.
    """

    runtime = get_runtime()

    _require_screen(runtime)

    from vision.remote import ScreenObservation

    observation = ScreenObservation(
        application=(request.application or "").strip(),
        package=(request.package or "").strip(),
        screen_text=(request.screen_text or "").strip(),
        accessibility_context=_flatten(
            request.accessibility_context,
            settings.max_screen_text_length,
        ),
        device_id=request.device_id,
        received_at=request.timestamp or 0.0,
    )

    try:
        result = runtime.observe_screen(observation)

    except Exception as error:
        # Same rule as chat: the text of a failure can carry internal
        # paths and hosts, so it is logged and never returned.
        logger.error(
            "Screen observation failed: %s: %s",
            type(error).__name__,
            error,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": "screen_failed"},
        )

    decision = result.get("decision")

    return ScreenContextResponse(
        session_id=request.session_id,
        status="accepted" if result["accepted"] else "ignored",
        accepted=bool(result["accepted"]),
        observation_id=(
            f"obs-{runtime.screen_source.submissions}"
            if result["accepted"] else None
        ),
        decision=ScreenDecision(**decision) if decision else None,
    )


@router.post("/screen/upload")
async def upload_screenshot(
    session_id: str = Form(default=""),
    device_id: str = Form(default=""),
    application: str = Form(default=""),
    package: str = Form(default=""),
    timestamp: float = Form(default=0.0),
    screenshot: UploadFile = File(...),
    token: str = Depends(verify_token),
):
    """
    Upload a screenshot alongside the structured context.

    Pixels are optional. The text pipeline works without them, and a
    screenshot only earns its bandwidth when a vision model is configured
    to read one - so this stores the frame and lets the processor decide.
    """

    runtime = get_runtime()

    _require_screen(runtime)

    # Checked before the body is read, so a rejected type costs no
    # bandwidth beyond the headers.
    image_format = _image_format(screenshot.content_type)

    image_data = await screenshot.read()

    if len(image_data) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail={
                "error": "screenshot_too_large",
                "max_bytes": settings.max_upload_bytes,
            },
        )

    if not image_data:
        raise HTTPException(
            status_code=422,
            detail={"error": "empty_screenshot"},
        )

    from vision.capture import Frame
    from vision.remote import ScreenObservation

    frame = Frame(
        data=image_data,
        image_format=image_format,
        source="phone",
    )

    observation = ScreenObservation(
        application=application.strip(),
        package=package.strip(),
        device_id=device_id,
        received_at=timestamp or 0.0,
        frame=frame,
    )

    try:
        # Off the event loop, because with a frame attached this call is no
        # longer cheap: `observe_screen` hands the frame to the vision
        # manager, and the cloud processor describes it with a synchronous
        # model request. On the loop that froze every other route for the
        # length of a VLM call - the same defect `/api/chat` had. It only
        # became reachable when a phone started actually uploading pixels;
        # `/api/screen` carries no frame, so `describe()` returns immediately
        # there and it stays on the loop.
        result = await run_in_threadpool(runtime.observe_screen, observation)

    except Exception as error:
        logger.error(
            "Screenshot observation failed: %s: %s",
            type(error).__name__,
            error,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": "screen_failed"},
        )

    processor = getattr(runtime.vision, "processor", None)
    if getattr(processor, "last_error", None) is not None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "vision_unavailable",
                "message": "No configured cloud vision provider could process the screenshot.",
            },
        )

    decision = result.get("decision")

    return {
        "session_id": session_id,
        "status": "accepted" if result["accepted"] else "ignored",
        "accepted": bool(result["accepted"]),
        "size_bytes": len(image_data),
        "decision": decision,
    }
