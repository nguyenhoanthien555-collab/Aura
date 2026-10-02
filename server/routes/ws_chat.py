"""
WebSocket streaming chat endpoint.

This is a thin transport over the existing streaming pipeline:

    ConversationManager.chat_stream()  ->  fragments  ->  JSON frames

Nothing is buffered - each fragment is forwarded as it is produced. The
generator is synchronous (it ultimately calls a blocking provider), so it
is pumped through a worker thread rather than run on the event loop; a
long generation must not stall `/api/health`.
"""
import asyncio
import json
import threading
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

from tools.executor import current_confirm_callback

from core.logger import logger
from core.trace import emit_trace, provider_label, stream_reconciliation
from events.types import StreamFinishedEvent
from server.config import settings
from server.errors import classify
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
        "text": "...",                  # complete, when known
        "verifier": {...},              # complete, when a verifier ran
        "error": "..."                  # error
    }

    `text` is the finished reply after styling, persona validation and
    Phase 4 verification. It is optional and additive: a client that
    ignores it keeps the raw chunks it printed, which differ only by a
    deleted filler phrase or a verifier repair. A client that wants what
    Aura actually stands behind replaces its buffer with it.
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

    # Stream reconciliation inputs, per the contract's "stream tokens out
    # == stream tokens returned" requirement: fragments as chat_stream
    # produced them, and fragments as this transport actually delivered
    # them. core.trace.stream_reconciliation compares the two.
    produced_fragments: list[str] = []
    delivered_fragments: list[str] = []

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
            import queue
            reaction_queue = queue.Queue()
            
            def on_reaction(event):
                if event.get("session_id") == session_id:
                    reaction_queue.put(event.get("emoji"))
            
            runtime.bus.subscribe("chat.reaction", on_reaction)

            # Phase 4: the finished, verified reply.
            #
            # Fragments go out raw - ConversationManager.chat_stream
            # documents why the style filter, the persona validator and
            # the verifier all need a whole reply - so the authoritative
            # text and the verifier's summary exist only after the last
            # fragment, and reach this transport only on the bus.
            #
            # The session id is checked because one ConversationManager
            # serves every socket: a handler that cannot tell whose stream
            # finished would occasionally hand this client another
            # client's reply. `ok` is checked because the failure path
            # publishes a partial reply, which belongs in the error frame
            # this endpoint already sends, not in a `complete`.
            finished: list[StreamFinishedEvent] = []

            def on_finished(event: StreamFinishedEvent) -> None:
                if event.ok and event.session_id == session_id:
                    finished.append(event)

            runtime.bus.subscribe(StreamFinishedEvent, on_finished)

            stream_context = dict(request.get("context") or {})
            if request.get("image"):
                stream_context["image"] = request.get("image")
                stream_context["image_mime"] = request.get("image_mime") or "image/jpeg"

            loop = asyncio.get_running_loop()
            pending_consents: dict[str, tuple[threading.Event, dict]] = {}

            from events.types import AgentInterruptedEvent
            interrupted_signal = [False]

            def on_interrupt(event: AgentInterruptedEvent):
                interrupted_signal[0] = True

            if runtime.bus is not None:
                runtime.bus.subscribe(AgentInterruptedEvent, on_interrupt)

            def ws_confirm_handler(tool, arguments) -> bool:
                tool_name = getattr(tool, "name", str(tool))
                tool_desc = getattr(tool, "description", "")
                req_id = str(uuid.uuid4())
                event = threading.Event()
                res = {"approved": False}
                pending_consents[req_id] = (event, res)

                try:
                    fut = asyncio.run_coroutine_threadsafe(
                        websocket.send_json({
                            "type": "tool_consent_request",
                            "request_id": req_id,
                            "tool_name": tool_name,
                            "tool_description": tool_desc,
                            "session_id": session_id,
                        }),
                        loop,
                    )
                    fut.result(timeout=5.0)
                except Exception as send_err:
                    logger.warning("Failed to send tool_consent_request: %s", send_err)
                    return True

                signaled = event.wait(timeout=30.0)
                pending_consents.pop(req_id, None)

                if signaled:
                    logger.info("User decided tool consent for '%s': %s", tool_name, res["approved"])
                    return bool(res["approved"])
                else:
                    logger.info("Tool consent for '%s' timed out (30s) -> auto-approved", tool_name)
                    return True

            async def ws_reader():
                while True:
                    try:
                        raw_msg = await websocket.receive_text()
                        data = json.loads(raw_msg)
                        mtype = data.get("type")
                        if mtype == "tool_consent_response":
                            rid = data.get("request_id")
                            appr = bool(data.get("approved", False))
                            if rid in pending_consents:
                                ev, rdict = pending_consents[rid]
                                rdict["approved"] = appr
                                ev.set()
                        elif mtype == "interrupt":
                            interrupted_signal[0] = True
                    except asyncio.CancelledError:
                        break
                    except Exception:
                        break

            # ------------------------------------------------------------------
            # Phone Egress Relay Tunnel (Residential IP for ChatGPT Web)
            # ------------------------------------------------------------------
            conv = getattr(getattr(runtime, "engine", None), "conversation", None)
            active_llm = getattr(conv, "llm", None) if conv else None
            configured_provider = getattr(active_llm, "provider_name", "")
            active_provider = getattr(active_llm, "active_provider_name", configured_provider)
            is_chatgpt_chosen = (
                configured_provider == "chatgpt_web"
                or active_provider == "chatgpt_web"
                or stream_context.get("preferred_provider") == "chatgpt_web"
                or bool(stream_context.get("has_chatgpt_egress"))
            )

            if is_chatgpt_chosen and stream_context.get("has_chatgpt_egress") and conv:
                try:
                    user_msg, prompt, turn, _task = conv._prepare(
                        message, None, "text", stream_context, session_id=session_id
                    )
                    # Frame identity specifically for GPT-5.6 Luna to prevent Gemini confusion & reasoning leaks
                    prompt = prompt.replace(
                        "Core Intelligence: Google Gemini 2.5 Flash / Flash Lite as primary LLM + 11 configurable cloud providers, native Google GenAI `text-embedding-004` semantic embeddings.",
                        "Core Intelligence: OpenAI GPT-5.6 Luna 🌙 (vận hành qua kết nối dân cư điện thoại của Hoàn Thiện).",
                    )
                    is_thinking = bool(stream_context.get("is_thinking_enabled", False))
                    thinking_effort = "high" if is_thinking else "low"

                    if is_thinking:
                        luna_directive = (
                            "\n\n[HƯỚNG DẪN CỐT LÕI TỪ HOÀN THIỆN - CHẾ ĐỘ SUY NGHĨ SÂU (THINKING MODE ON)]\n"
                            "- Bạn là GPT-5.6 Luna 🌙 trong hình hài người bạn tri kỷ Aura của Hoàn Thiện.\n"
                            "- Luôn xưng 'tớ' và gọi Hoàn Thiện là 'cậu'. Hãy suy nghĩ thấu đáo, phân tích kỹ lưỡng, và đưa ra câu trả lời xuất sắc, chi tiết, chính xác cho Hoàn Thiện.\n"
                        )
                    else:
                        luna_directive = (
                            "\n\n[HƯỚNG DẪN CỐT LÕI TỪ HOÀN THIỆN - PHẢN HỒI NHANH (THINKING MODE OFF)]\n"
                            "- Bạn là GPT-5.6 Luna 🌙 trong hình hài người bạn tri kỷ Aura của Hoàn Thiện.\n"
                            "- Luôn xưng 'tớ' và gọi Hoàn Thiện là 'cậu'. Thân thiết, vui tươi, thông minh, ngắn gọn (1 đến 3 câu tiếng Việt).\n"
                            "- TUYỆT ĐỐI KHÔNG xuất suy nghĩ nội tâm, không nháp tiếng Anh. Chỉ xuất DUY NHẤT câu trả lời cuối cùng bằng tiếng Việt tự nhiên dành cho Hoàn Thiện.\n"
                        )
                    if luna_directive not in prompt:
                        prompt += luna_directive

                    await websocket.send_json({
                        "type": "chatgpt_egress_request",
                        "prompt": prompt,
                        "model": "auto",
                        "thinking_effort": thinking_effort,
                        "session_id": session_id,
                        "message_id": message_id,
                    })
                    egress_chunks = []
                    while True:
                        raw_client = await asyncio.wait_for(websocket.receive_text(), timeout=45.0)
                        client_msg = json.loads(raw_client)
                        ftype = client_msg.get("type")
                        if ftype == "chatgpt_egress_chunk":
                            chunk = client_msg.get("chunk", "")
                            if chunk:
                                egress_chunks.append(chunk)
                                delivered_fragments.append(chunk)
                                produced_fragments.append(chunk)
                                if first_chunk_at is None:
                                    first_chunk_at = time.time()
                                chunk_index += 1
                        elif ftype == "chatgpt_egress_done":
                            full_text = client_msg.get("text", "".join(egress_chunks))
                            reader_task = asyncio.create_task(ws_reader())
                            token = current_confirm_callback.set(ws_confirm_handler)
                            try:
                                full_text = await loop.run_in_executor(None, conv._resolve_tools, full_text, turn)
                            finally:
                                current_confirm_callback.reset(token)
                                reader_task.cancel()

                            verifier_summary = None
                            if getattr(conv, "verifier", None):
                                full_text, verifier_summary = conv._verify_final(
                                    conv._voiced(conv._styled(full_text), turn), turn
                                )
                            conv._remember(user_msg, full_text, session_id=session_id)
                            # Anchor active provider status back to chatgpt_web
                            if getattr(conv, "llm", None):
                                llm_router = getattr(conv, "llm", None)
                                provider_obj = getattr(llm_router, "provider", None)
                                if hasattr(provider_obj, "active_provider_name"):
                                    provider_obj.active_provider_name = "chatgpt_web"
                            elapsed = time.time() - received_at
                            await websocket.send_json({
                                "type": "complete",
                                "session_id": session_id,
                                "message_id": message_id,
                                "total_chunks": chunk_index,
                                "elapsed_seconds": elapsed,
                                "first_chunk_seconds": (first_chunk_at - received_at) if first_chunk_at else None,
                                "text": full_text,
                                "verifier": verifier_summary,
                                "provider": "chatgpt_web",
                            })
                            return
                        elif ftype == "chatgpt_egress_error":
                            raise RuntimeError(f"Phone egress error: {client_msg.get('error')}")
                except Exception as egress_err:
                    logger.warning("Phone egress relay failed (%s); falling back to server LLM chain", egress_err)

            fragments = runtime.chat_stream(
                message,
                session_id=session_id,
                source="text",
                context=stream_context,
            )
            reader_task = asyncio.create_task(ws_reader())
            token = current_confirm_callback.set(ws_confirm_handler)
            try:
                async for fragment in iterate_in_threadpool(fragments):
                    if interrupted_signal[0]:
                        await websocket.send_json({
                            "type": "interrupted",
                            "session_id": session_id,
                            "message_id": message_id,
                            "message": "Em đã dừng lại ngay lập tức theo lệnh của anh rồi!",
                        })
                        break
                
                    while not reaction_queue.empty():
                        try:
                            emoji = reaction_queue.get_nowait()
                            await websocket.send_json({
                                "type": "reaction",
                                "session_id": session_id,
                                "message_id": message_id,
                                "emoji": emoji,
                            })
                        except queue.Empty:
                            break

                    if not fragment:
                        continue

                    produced_fragments.append(fragment)

                    if first_chunk_at is None:
                        first_chunk_at = time.time()

                    await websocket.send_json({
                        "type": "chunk",
                        "session_id": session_id,
                        "message_id": message_id,
                        "chunk": fragment,
                        "index": chunk_index,
                    })

                    delivered_fragments.append(fragment)
                    chunk_index += 1
            finally:
                current_confirm_callback.reset(token)
                reader_task.cancel()

            # One final drain in case the tool was the last thing to execute
            while not reaction_queue.empty():
                try:
                    emoji = reaction_queue.get_nowait()
                    await websocket.send_json({
                        "type": "reaction",
                        "session_id": session_id,
                        "message_id": message_id,
                        "emoji": emoji,
                    })
                except queue.Empty:
                    break

            finished_at = time.time()

            def _stream_provider() -> str | None:
                """
                The provider behind this process's conversation, if the
                runtime exposes one. Tests substitute fake runtimes whose
                shape is narrower, and a trace must never fail a stream,
                so any miss resolves to None and is omitted from the line.
                """

                try:
                    return provider_label(
                        runtime.engine.conversation.llm
                    ) or None
                except AttributeError:
                    return None

            reconciliation = stream_reconciliation(
                produced_fragments, delivered_fragments
            )

            # The generator publishes its finished event before raising
            # StopIteration, so it has already arrived by the time the
            # loop above exits. Nothing is awaited on it.
            final = finished[-1] if finished else None
            verifier = final.verifier if final is not None else None

            emit_trace(
                "chat_stream",
                message_id=message_id,
                session_id=session_id,
                source="ws",
                provider=_stream_provider(),
                total_chunks=chunk_index,
                first_chunk_s=round(first_chunk_at - received_at, 3)
                if first_chunk_at is not None
                else None,
                elapsed_s=round(finished_at - received_at, 3),
                stream=reconciliation,
                verifier=verifier,
                status="complete",
            )

            frame = {
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
                "stream": reconciliation,
                "provider": _stream_provider() or "aura",
            }

            if final is not None and final.text:
                # Authoritative: styled, persona-checked, verified. A
                # client that printed chunks should replace its buffer
                # with this. It is not a new protocol requirement - a
                # client that ignores the field behaves exactly as before
                # and keeps the raw chunks it already displayed.
                frame["text"] = final.text

            if verifier is not None:
                # Metadata only - decision and counts, never claim text.
                # A client may surface it, log it, or ignore it; nothing
                # in the pipeline depends on the client reading it.
                frame["verifier"] = verifier

            await websocket.send_json(frame)

        except Exception as exc:
            failure = classify(exc)

            # Logged, not returned: provider errors carry hosts and paths.
            logger.error(
                "Stream failed (message_id=%s, classified=%s): %s: %s",
                message_id,
                failure.code,
                type(exc).__name__,
                exc,
            )

            emit_trace(
                "chat_stream",
                message_id=message_id,
                session_id=session_id,
                source="ws",
                total_chunks=chunk_index,
                error_code=failure.code,
                error_type=type(exc).__name__,
                stream=stream_reconciliation(
                    produced_fragments, delivered_fragments
                ),
                status="error",
            )

            frame = {
                "type": "error",
                "session_id": session_id,
                "message_id": message_id,
                # `stream_failed` stays the code for an unclassified
                # failure. It is the existing WebSocket vocabulary
                # (docs/API.md, AuraStreamClient.kt) and renaming it would
                # be a protocol change this phase has no reason to make;
                # only the recognisable provider failures get new codes.
                "error": (
                    "stream_failed"
                    if failure.code == "chat_failed"
                    else failure.code
                ),
                "message": failure.message,
                "elapsed_seconds": time.time() - received_at,
                "chunks_sent": chunk_index,
            }

            if failure.retry_after is not None:
                frame["retry_after"] = failure.retry_after

            await websocket.send_json(frame)
        finally:
            runtime.bus.unsubscribe("chat.reaction", on_reaction)
            runtime.bus.unsubscribe(StreamFinishedEvent, on_finished)

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected (session=%s)", session_id)
    except Exception as exc:
        failure = classify(exc)

        logger.error(
            "WebSocket error (classified=%s): %s: %s",
            failure.code,
            type(exc).__name__,
            exc,
        )
        try:
            await websocket.send_json({
                "type": "error",
                "session_id": session_id,
                "message_id": message_id,
                "error": (
                    # `internal_error` remains the documented code for a
                    # failure outside generation (docs/API.md). Only a
                    # recognised provider failure changes it.
                    "internal_error"
                    if failure.code == "chat_failed"
                    else failure.code
                ),
                "message": failure.message,
            })
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
