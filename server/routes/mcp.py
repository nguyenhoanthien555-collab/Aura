"""
MCP (Model Context Protocol) Gateway for Aura Cloud.

Exposes Aura's tool ecosystem (PC tools, Workspace tools, Memory tools,
Android device tools) to external reasoning engines (ChatGPT, Claude,
Cursor, or MCP Bridges) through the standardized Model Context Protocol.

Complies with MCP specification (2024-11-05):
- POST /api/mcp: Direct JSON-RPC 2.0 endpoint (initialize, tools/list, tools/call, ping).
- GET  /api/mcp/sse: Server-Sent Events transport.
- POST /api/mcp/messages: JSON-RPC message receiver for active SSE sessions.
- GET  /api/mcp/tools: Convenient REST schema inspection.

All tool executions pass through ToolExecutor to enforce permissions,
capability health, timeouts, audit logs, and canonical Evidence generation.
"""

import asyncio
import json
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from core.logger import logger
from server.auth import check_bearer
from server.config import settings
from tools.base import ToolProtocol, ToolResult, ToolRisk
from tools.outcome import Evidence, ToolStatus
from tools.schema import mcp_export

router = APIRouter(prefix="/api/mcp", tags=["mcp"])

# Active SSE sessions: session_id -> asyncio.Queue
_sse_sessions: dict[str, asyncio.Queue] = {}
_sse_lock = asyncio.Lock()


def _authenticate_mcp_request(request: Request, token_query: Optional[str] = None) -> str:
    """
    Authenticate via Authorization header or fallback query param ?token=...
    (Required because standard EventSource in browsers cannot set headers).
    """
    if not settings.auth_token:
        return "dev"

    auth_header = request.headers.get("Authorization")
    if auth_header:
        try:
            return check_bearer(auth_header)
        except HTTPException:
            pass

    if token_query:
        if token_query == settings.auth_token:
            return token_query

    # Also check if token was passed as Bearer in query
    if token_query and token_query.startswith("Bearer "):
        actual_tok = token_query.split(" ", 1)[1]
        if actual_tok == settings.auth_token:
            return actual_tok

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized MCP request",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_all_mcp_tools() -> list[ToolProtocol]:
    """
    Collect all registered tools across PC, Workspace, Memory and Android bodies.
    Deduplicates by tool name.
    """
    seen: set[str] = set()
    tools: list[ToolProtocol] = []

    # 1. Android device tools (via GatewayDeviceBridge)
    try:
        from server.routes.agent import get_device_registry
        dev_reg = get_device_registry()
        for t in dev_reg.all():
            if t.name not in seen:
                seen.add(t.name)
                tools.append(t)
    except Exception as exc:
        logger.debug("MCP: failed to collect device tools: %s", exc)

    # 2. Runtime tools (system, workspace, memory, web, sandbox, clock, desktop)
    try:
        from server.runtime import get_runtime
        runtime = get_runtime()
        if runtime and runtime.services and runtime.services.tools:
            for t in runtime.services.tools.registry.all():
                if t.name not in seen:
                    seen.add(t.name)
                    tools.append(t)
    except Exception as exc:
        logger.debug("MCP: failed to collect runtime tools: %s", exc)

    # 3. Fallback to factory build_registry if runtime is not fully initialized
    if not tools:
        try:
            from tools.factory import build_registry
            reg = build_registry()
            for t in reg.all():
                if t.name not in seen:
                    seen.add(t.name)
                    tools.append(t)
        except Exception as exc:
            logger.debug("MCP: fallback registry build error: %s", exc)

    return tools


def execute_mcp_tool(name: str, arguments: dict) -> ToolResult:
    """
    Execute a single tool through the authorized ToolExecutor gate.
    """
    if name.startswith("android."):
        from server.routes.device import get_device_executor
        executor = get_device_executor()
        return executor.execute(name, **arguments)

    # Workspace, system, web, memory tools
    from server.runtime import get_runtime
    try:
        runtime = get_runtime()
        if runtime and runtime.services and runtime.services.tools:
            return runtime.services.tools.execute(name, **arguments)
    except Exception:
        pass

    # Local execution fallback
    from tools.factory import build_registry
    from tools.executor import ToolExecutor, ToolPolicy

    registry = build_registry()
    executor = ToolExecutor(
        registry=registry,
        policy=ToolPolicy(
            enabled=True,
            allowed=frozenset(registry.names()),
            auto_approve=frozenset({
                ToolRisk.SAFE, ToolRisk.SENSITIVE, ToolRisk.DANGEROUS,
            }),
        ),
    )
    return executor.execute(name, **arguments)


def _format_mcp_result(result: ToolResult) -> dict:
    """
    Format a ToolResult into MCP CallToolResult.
    """
    content = []
    text_content = result.output
    if not text_content:
        if not result.ok:
            text_content = f"Error [{result.error_code or 'FAILED'}]: {result.error}"
        else:
            text_content = "Tool executed successfully (empty output)."

    content.append({
        "type": "text",
        "text": text_content,
    })

    # Prepare evidence metadata
    evidence_list = []
    if getattr(result, "evidence", None):
        for ev in result.evidence:
            if hasattr(ev, "to_dict"):
                evidence_list.append(ev.to_dict())
            else:
                evidence_list.append({
                    "kind": getattr(ev, "kind", str(ev)),
                    "verified": getattr(ev, "verified", None),
                    "source": getattr(ev, "source", ""),
                    "detail": getattr(ev, "detail", ""),
                })

    meta = {
        "status": getattr(result, "status", ToolStatus.SUCCESS.value if result.ok else ToolStatus.FAILED.value),
        "tool": getattr(result, "tool", ""),
        "evidence": evidence_list,
        "side_effect": getattr(result, "side_effect", ""),
    }
    if getattr(result, "data", None):
        meta["data"] = result.data

    return {
        "content": content,
        "isError": not result.ok,
        "_aura_meta": meta,
    }


def handle_jsonrpc_request(req_data: dict) -> dict:
    """
    Core dispatcher for JSON-RPC 2.0 messages.
    """
    req_id = req_data.get("id")
    method = req_data.get("method", "")
    params = req_data.get("params") or {}

    if not method:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32600, "message": "Invalid Request: missing method"},
        }

    # 1. initialize handshake
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": False},
                },
                "serverInfo": {
                    "name": "Aura Cloud MCP Gateway",
                    "version": "1.0.0",
                },
            },
        }

    # 2. notifications/initialized or ping
    if method in ("notifications/initialized", "initialized"):
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    # 3. tools/list
    if method == "tools/list":
        tools = get_all_mcp_tools()
        exported = mcp_export(tools)
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": exported,
            },
        }

    # 4. tools/call
    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments") or {}

        if not tool_name:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": "Invalid params: 'name' is required"},
            }

        try:
            result = execute_mcp_tool(tool_name, arguments)
            call_result = _format_mcp_result(result)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": call_result,
            }
        except Exception as error:
            logger.error("MCP tool execution failure for %s: %s", tool_name, error)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Execution exception: {error}"}],
                    "isError": True,
                },
            }

    # Unknown method
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: '{method}'"},
    }


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------

@router.post("")
async def mcp_direct_jsonrpc(
    request: Request,
    token: Optional[str] = Query(None),
):
    """
    Direct JSON-RPC 2.0 endpoint for MCP callers (ChatGPT, Claude, bridges).
    """
    _authenticate_mcp_request(request, token)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
        )

    # Support batch or single
    if isinstance(body, list):
        responses = [handle_jsonrpc_request(item) for item in body if isinstance(item, dict)]
        return JSONResponse(content=responses)

    response = handle_jsonrpc_request(body)
    return JSONResponse(content=response)


@router.get("/tools")
async def mcp_list_tools_rest(
    request: Request,
    token: Optional[str] = Query(None),
):
    """
    REST endpoint returning MCP-formatted tool catalogue.
    """
    _authenticate_mcp_request(request, token)
    tools = get_all_mcp_tools()
    return {
        "tools": mcp_export(tools),
        "count": len(tools),
    }


@router.get("/sse")
async def mcp_sse_transport(
    request: Request,
    token: Optional[str] = Query(None),
):
    """
    Server-Sent Events transport endpoint for standard MCP SSE clients.
    """
    _authenticate_mcp_request(request, token)

    session_id = uuid.uuid4().hex
    queue: asyncio.Queue = asyncio.Queue()

    async with _sse_lock:
        _sse_sessions[session_id] = queue

    logger.info("MCP SSE client connected: session_id=%s", session_id)

    async def event_generator():
        try:
            # First event tells the client where to post JSON-RPC messages
            endpoint_url = f"/api/mcp/messages?session_id={session_id}"
            yield f"event: endpoint\ndata: {endpoint_url}\n\n"

            while True:
                # Check for client disconnect
                if await request.is_disconnected():
                    break

                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"event: message\ndata: {json.dumps(msg)}\n\n"
                except asyncio.TimeoutError:
                    # Keep-alive comment
                    yield ": keepalive\n\n"
        finally:
            async with _sse_lock:
                _sse_sessions.pop(session_id, None)
            logger.info("MCP SSE client disconnected: session_id=%s", session_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/messages")
async def mcp_sse_post_message(
    request: Request,
    session_id: str = Query(...),
    token: Optional[str] = Query(None),
):
    """
    Endpoint for SSE clients to POST their JSON-RPC requests.
    Responses are pushed back through the open SSE stream.
    """
    _authenticate_mcp_request(request, token)

    async with _sse_lock:
        queue = _sse_sessions.get(session_id)

    if queue is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SSE session '{session_id}' not found or expired",
        )

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    response = handle_jsonrpc_request(body)
    await queue.put(response)

    return JSONResponse(status_code=202, content={"status": "accepted"})
