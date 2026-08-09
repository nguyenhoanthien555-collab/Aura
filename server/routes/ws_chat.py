"""
WebSocket streaming chat endpoint.

This is a thin transport over the existing streaming pipeline:

    ConversationManager.chat_stream()  ->  fragments  ->  JSON frames

Nothing is buffered - each fragment is forwarded as it is produced. The
generator is synchronous (it ultimately calls a blocking provider), so it
is pumped through a worker thread rather than run on the event loop; a
long generation must not stall `/api/health`.
"""
import json
import time
import uuid

from fastapi import (
    APIRouter,
    Depends,
    Query,
    WebSocket,
    WebSocketDisconnect,
    WebSocketException,
    status,
)
from starlette.concurrency import iterate_in_threadpool

from core.logger import logger
from server.config import settings
from server.runtime import get_runtime
from server.session import session_manager


router = APIRouter(prefix="/api", tags=["chat"])


async def verify_ws_token(token: str = Query(None)) -> str:
    """
    Verify the token supplied as a query parameter.

    Browsers and OkHttp cannot set an Authorization header on a WebSocket
    handshake, so the token travels as `?token=`. Rejection happens before
    `accept()`, so an unauthenticated client never gets an open socket.
    """

    if not settings.auth_token:
        return "dev"

    import secrets

    if not token or not secrets.compare_digest(token, settings.auth_token):
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)

    return token


@router.websocket("/chat/stream")
async def chat_stream(
    websocket: WebSocket,
    session_id: str = Query(None),
    token: str = Depends(verify_ws_token),
):
    """
    Streaming chat via WebSocket.

    Message format (client -> server):
    {
        "message": "Hello",
        "context": {},
        "metadata": {}
    }

    Response format (server -> client):
    {
        "type": "started" | "chunk" | "complete" | "error",
        "session_id": "...",
        "message_id": "...",
        "chunk": "...",                 # chunk
        "index": 0,                     # chunk
        "elapsed_seconds": 0.0,         # complete / error
        "first_chunk_seconds": 0.0,     # complete
        "total_chunks": 0,              # complete
        "error": "..."                  # error
    }
    """
    await websocket.accept()

    runtime = get_runtime()

    session = session_manager.ensure_session(session_id)
    session_id = session.session_id
    session_manager.update_activity(session_id)

    message_id = str(uuid.uuid4())
    received_at = time.time()
    first_chunk_at: float | None = None
    chunk_index = 0

    try:
        raw = await websocket.receive_text()

        try:
            request = json.loads(raw)
        except json.JSONDecodeError:
            await websocket.send_json({
                "type": "error",
                "session_id": session_id,
                "message_id": message_id,
                "error": "invalid_json",
            })
            return

        message = (request.get("message") or "").strip()

        if not message:
            await websocket.send_json({
                "type": "error",
                "session_id": session_id,
                "message_id": message_id,
                "error": "empty_message",
            })
            return

        if len(message) > settings.max_message_length:
            await websocket.send_json({
                "type": "error",
                "session_id": session_id,
                "message_id": message_id,
                "error": "message_too_long",
            })
            return

        await websocket.send_json({
            "type": "started",
            "session_id": session_id,
            "message_id": message_id,
        })

        try:
            fragments = runtime.chat_stream(
                message,
                session_id=session_id,
                source="text",
            )

            async for fragment in iterate_in_threadpool(fragments):

                if not fragment:
                    continue

                if first_chunk_at is None:
                    first_chunk_at = time.time()

                await websocket.send_json({
                    "type": "chunk",
                    "session_id": session_id,
                    "message_id": message_id,
                    "chunk": fragment,
                    "index": chunk_index,
                })

                chunk_index += 1

            finished_at = time.time()

            await websocket.send_json({
                "type": "complete",
                "session_id": session_id,
                "message_id": message_id,
                "elapsed_seconds": finished_at - received_at,
                "first_chunk_seconds": (
                    first_chunk_at - received_at
                    if first_chunk_at is not None
                    else None
                ),
                "total_chunks": chunk_index,
            })

        except Exception as exc:
            # Logged, not returned: provider errors carry hosts and paths.
            logger.error(
                "Stream failed (message_id=%s): %s: %s",
                message_id,
                type(exc).__name__,
                exc,
            )

            await websocket.send_json({
                "type": "error",
                "session_id": session_id,
                "message_id": message_id,
                "error": "stream_failed",
                "elapsed_seconds": time.time() - received_at,
                "chunks_sent": chunk_index,
            })

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected (session=%s)", session_id)
    except Exception as exc:
        logger.error("WebSocket error: %s: %s", type(exc).__name__, exc)
        try:
            await websocket.send_json({
                "type": "error",
                "session_id": session_id,
                "message_id": message_id,
                "error": "internal_error",
            })
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
