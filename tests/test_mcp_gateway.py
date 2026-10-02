"""
Unit and integration tests for Aura MCP (Model Context Protocol) Gateway.
"""

import json
import pytest
from fastapi.testclient import TestClient

from server.config import settings
from server.main import app
from server.routes.mcp import (
    execute_mcp_tool,
    get_all_mcp_tools,
    handle_jsonrpc_request,
)


@pytest.fixture
def client():
    # Use TestClient with auth disabled or configured
    orig_token = settings.auth_token
    settings.auth_token = "test-mcp-secret"
    try:
        with TestClient(app) as tc:
            yield tc
    finally:
        settings.auth_token = orig_token


def test_mcp_tool_collection():
    """Verify tool collection aggregates PC and system tools."""
    tools = get_all_mcp_tools()
    names = {t.name for t in tools}
    assert "current_time" in names or "system_information" in names
    # Ensure all tools have valid name and input schema
    for t in tools:
        assert getattr(t, "name", "") != ""


def test_mcp_initialize():
    """Test standard MCP initialize handshake."""
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0"},
        },
    }
    resp = handle_jsonrpc_request(req)
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    assert "result" in resp
    result = resp["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert "tools" in result["capabilities"]
    assert result["serverInfo"]["name"] == "Aura Cloud MCP Gateway"


def test_mcp_ping_and_notifications():
    """Test ping and notification handling."""
    ping_resp = handle_jsonrpc_request({"jsonrpc": "2.0", "id": "p1", "method": "ping"})
    assert ping_resp["jsonrpc"] == "2.0"
    assert ping_resp["id"] == "p1"
    assert ping_resp["result"] == {}

    init_resp = handle_jsonrpc_request({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert init_resp["jsonrpc"] == "2.0"
    assert init_resp["result"] == {}


def test_mcp_tools_list():
    """Test tools/list returns MCP compliant format."""
    req = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
    }
    resp = handle_jsonrpc_request(req)
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 2
    assert "result" in resp
    tools = resp["result"]["tools"]
    assert isinstance(tools, list)
    assert len(tools) > 0

    # Verify MCP tool shape: name, description, inputSchema
    sample = tools[0]
    assert "name" in sample
    assert "description" in sample
    assert "inputSchema" in sample
    assert sample["inputSchema"]["type"] == "object"


def test_mcp_tools_call_success():
    """Test tools/call execution with a safe builtin tool."""
    req = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "current_time",
            "arguments": {},
        },
    }
    resp = handle_jsonrpc_request(req)
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 3
    assert "result" in resp
    res = resp["result"]
    assert res["isError"] is False
    assert len(res["content"]) > 0
    assert res["content"][0]["type"] == "text"
    assert len(res["content"][0]["text"]) > 0
    assert "_aura_meta" in res
    assert res["_aura_meta"]["status"] == "SUCCESS"


def test_mcp_tools_call_unknown():
    """Test tools/call with non-existent tool."""
    req = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "non_existent_tool_xyz",
            "arguments": {},
        },
    }
    resp = handle_jsonrpc_request(req)
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 4
    assert "result" in resp
    res = resp["result"]
    assert res["isError"] is True


def test_mcp_endpoint_auth(client):
    """Test endpoint rejects unauthenticated calls and accepts valid bearer."""
    # 1. Reject without token
    resp = client.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert resp.status_code == 401

    # 2. Reject with bad token
    resp = client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={"Authorization": "Bearer bad-token"},
    )
    assert resp.status_code == 401

    # 3. Accept with valid token
    resp = client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={"Authorization": "Bearer test-mcp-secret"},
    )
    assert resp.status_code == 200
    assert resp.json()["result"] == {}

    # 4. Accept with query token (?token=...)
    resp = client.post(
        "/api/mcp?token=test-mcp-secret",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
    )
    assert resp.status_code == 200
    assert resp.json()["result"] == {}


def test_mcp_tools_rest_endpoint(client):
    """Test GET /api/mcp/tools endpoint."""
    resp = client.get(
        "/api/mcp/tools",
        headers={"Authorization": "Bearer test-mcp-secret"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "tools" in data
    assert data["count"] > 0
