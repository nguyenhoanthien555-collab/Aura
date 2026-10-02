"""
Standalone MCP (Model Context Protocol) Bridge Runner for Aura.

Enables STDIO communication between local AI clients (ChatGPT Desktop,
Claude Desktop, Cursor, local Codex bridges) and the Aura Tool Ecosystem.

Usage:
  # Local in-process mode (reads directly from Aura's local registry):
  python scripts/run_mcp_bridge.py

  # Remote relay mode (forwards calls to always-online Aura Cloud on Render):
  python scripts/run_mcp_bridge.py --remote https://aura-xwm4.onrender.com --token <AURA_TOKEN>

Standard MCP configuration (e.g. claude_desktop_config.json):
{
  "mcpServers": {
    "aura": {
      "command": "python",
      "args": ["D:\\AURA\\scripts\\run_mcp_bridge.py"]
    }
  }
}
"""

import argparse
import json
import os
import sys
import urllib.request


def run_remote_bridge(remote_url: str, token: str):
    """
    Forward JSON-RPC STDIO messages to the remote Aura Cloud MCP Gateway.
    """
    endpoint = f"{remote_url.rstrip('/')}/api/mcp"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req_data = json.loads(line)
        except Exception:
            continue

        req = urllib.request.Request(
            endpoint,
            data=json.dumps(req_data).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                resp_data = resp.read().decode("utf-8")
                sys.stdout.write(resp_data + "\n")
                sys.stdout.flush()
        except urllib.error.HTTPError as err:
            err_body = err.read().decode("utf-8")
            sys.stdout.write(err_body + "\n")
            sys.stdout.flush()
        except Exception as exc:
            err_resp = {
                "jsonrpc": "2.0",
                "id": req_data.get("id"),
                "error": {"code": -32000, "message": f"Bridge relay error: {exc}"},
            }
            sys.stdout.write(json.dumps(err_resp) + "\n")
            sys.stdout.flush()


def run_local_bridge():
    """
    Execute tools locally in-process through server.routes.mcp.
    """
    # Ensure project root is in sys.path
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from server.routes.mcp import handle_jsonrpc_request

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req_data = json.loads(line)
        except Exception:
            err_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error: invalid JSON"},
            }
            sys.stdout.write(json.dumps(err_resp) + "\n")
            sys.stdout.flush()
            continue

        response = handle_jsonrpc_request(req_data)
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


def main():
    parser = argparse.ArgumentParser(description="Aura MCP Bridge Runner")
    parser.add_argument("--remote", type=str, default="", help="Remote Aura Cloud URL (e.g. https://aura-xwm4.onrender.com)")
    parser.add_argument("--token", type=str, default=os.getenv("AURA_TOKEN", ""), help="Aura bearer token")
    args = parser.parse_args()

    if args.remote:
        run_remote_bridge(args.remote, args.token)
    else:
        run_local_bridge()


if __name__ == "__main__":
    main()
