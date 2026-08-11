"""
Chat endpoints (non-streaming).
"""
import time
import uuid
from fastapi import APIRouter, Depends, HTTPException
from server.runtime import get_runtime
from server.session import session_manager
from server.auth import verify_token
from server.errors import classify
from server.models import ChatRequest, ChatResponse
from core.logger import logger


router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, token: str = Depends(verify_token)):
    """
    Process a chat message.

    Enters the existing Aura Brain/conversation pipeline.
    Reuses the existing ChatEngine and ConversationManager.
    """
    runtime = get_runtime()

    session = session_manager.ensure_session(request.session_id)

    session_manager.update_activity(session.session_id)

    # Generate message ID
    message_id = str(uuid.uuid4())

    start_time = time.time()

    try:
        # Call the existing Aura chat pipeline
        # This goes through: ChatEngine -> ConversationManager -> LLM -> Memory
        response = runtime.chat(
            request.message,
            session_id=session.session_id,
            source="text",
            context=request.context,
        )

        elapsed = time.time() - start_time

        return ChatResponse(
            session_id=session.session_id,
            reply=response.text,
            message_id=message_id,
            metadata={
                "elapsed_seconds": elapsed,
                "provider": getattr(
                    runtime.engine.conversation.llm,
                    "provider_name",
                    "unknown"
                ),
            }
        )

    except Exception as e:
        elapsed = time.time() - start_time

        failure = classify(e)

        # The exception text can carry internal hosts, filesystem paths or
        # provider key fragments, so it is logged and never returned. The
        # log is where "which provider, which exception" lives; the client
        # gets the category and the message_id that ties the two together.
        logger.error(
            "Chat failed (message_id=%s, classified=%s): %s: %s",
            message_id,
            failure.code,
            type(e).__name__,
            e,
        )

        detail = {
            "error": failure.code,
            "message": failure.message,
            "elapsed_seconds": elapsed,
            "message_id": message_id,
        }

        headers = None

        if failure.retry_after is not None:
            # Standard, and the reason a 429 is worth distinguishing at
            # all: a client can obey it without parsing the body.
            headers = {"Retry-After": str(int(failure.retry_after))}

        raise HTTPException(
            status_code=failure.status,
            detail=detail,
            headers=headers,
        )


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, token: str = Depends(verify_token)):
    """Get session info."""
    session = session_manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    return {
        "session_id": session.session_id,
        "created_at": session.created_at,
        "last_activity": session.last_activity,
        "message_count": session.message_count,
    }


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, token: str = Depends(verify_token)):
    """Delete a session."""
    if session_manager.delete_session(session_id):
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Session not found")