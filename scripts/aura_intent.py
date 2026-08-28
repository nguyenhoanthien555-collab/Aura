"""
aura-intent - drive AURA's autonomous natural-language intent loop.

One argument: the intent, in plain language. Nothing else is required:

    python scripts/aura_intent.py "What app is currently open on my phone?"
    python scripts/aura_intent.py "Go home."

The request hits POST /api/agent/intent on a running Aura server, which
performs skill discovery against live capability state, executes the
selected skill through ToolExecutor / GatewayDeviceBridge / the physical
companion, verifies mutating postconditions and returns a grounded reply
plus evidence metadata. This script adds NO execution logic of its own -
it cannot bypass anything by construction - so its output is exactly what
the runtime decided.

Authentication mirrors scripts/aura_android.py: `AURA_SERVER_AUTH_TOKEN`
from the project-root .env is authoritative, `AURA_TOKEN` an override.
"""

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import dotenv_values


def configured_auth_token() -> str:
    override = os.environ.get("AURA_TOKEN")
    if override:
        return override

    root_env = Path(__file__).resolve().parent.parent / ".env"
    if root_env.exists():
        value = dotenv_values(str(root_env)).get("AURA_SERVER_AUTH_TOKEN")
        if value:
            return str(value)

    raise SystemExit(
        "no auth token: set AURA_TOKEN or AURA_SERVER_AUTH_TOKEN in .env"
    )


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    parser = argparse.ArgumentParser(
        prog="aura-intent",
        description="Run one natural-language intent through AURA's "
                    "autonomous grounded execution loop",
    )
    parser.add_argument("intent", help="the natural-language request")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000",
                        help="Aura server base URL")
    parser.add_argument("--session-id", default="",
                        help="client session id (default: fresh)")
    args = parser.parse_args(argv)

    import urllib.error
    import urllib.request

    payload = json.dumps({
        "session_id": args.session_id or f"session_{uuid.uuid4().hex[:12]}",
        "intent": args.intent,
    }).encode("utf-8")

    request = urllib.request.Request(
        f"{args.base_url.rstrip('/')}/api/agent/intent",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {configured_auth_token()}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        print(json.dumps({
            "http_status": error.code,
            "error": detail,
        }, indent=2))
        return 1
    except Exception as error:
        print(json.dumps({"http_status": None,
                          "error": f"{type(error).__name__}: {error}"},
                         indent=2))
        return 1

    print(json.dumps(json.loads(body), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())