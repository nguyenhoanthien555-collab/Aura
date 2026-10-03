"""
Diagnostic tool for ChatGPT Web authentication and connectivity.
Executes the direct authentication and conversation pipeline step-by-step
without fallback, printing safe fingerprints and sanitized response metadata.

Zero secret leakage: NEVER prints request headers, cookies, or raw tokens.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
import httpx

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from brain.providers.chatgpt_web import (
    ChatGPTWebProvider,
    DEFAULT_CHATGPT_USER_AGENT,
    format_chatgpt_cookie,
    solve_sentinel_pow,
    token_fingerprint,
)
from brain.providers.errors import ProviderAuthError


def sanitize_body(body_text: str, max_chars: int = 300) -> str:
    cleaned = (body_text or "").replace("\r", " ").replace("\n", " ")
    if len(cleaned) > max_chars:
        return cleaned[:max_chars] + f"... [truncated {len(cleaned) - max_chars} chars]"
    return cleaned


def filter_headers(headers: httpx.Headers) -> dict[str, str]:
    safe_keys = {"server", "cf-mitigated", "cf-ray", "content-type", "retry-after", "date"}
    return {k: v for k, v in headers.items() if k.lower() in safe_keys}


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose ChatGPT Web direct authentication pipeline.")
    parser.add_argument("--base-url", default="https://chatgpt.com", help="ChatGPT Web base URL")
    parser.add_argument("--timeout", type=float, default=15.0, help="HTTP timeout in seconds")
    args = parser.parse_args()

    token = os.environ.get("CHATGPT_SESSION_TOKEN", "").strip()
    fp = token_fingerprint(token)

    print("=" * 60)
    print("CHATGPT WEB AUTHENTICATION FORENSIC DIAGNOSTIC")
    print("=" * 60)
    print(f"Token configured:   {bool(token)}")
    print(f"Token length:       {len(token)}")
    print(f"Token fingerprint:  {fp or 'none'}")
    print(f"Target base URL:    {args.base_url}")
    print("=" * 60)

    if not token:
        print("\n[!] ERROR: CHATGPT_SESSION_TOKEN is empty. Set it in environment first.")
        return 1

    provider = ChatGPTWebProvider(session_token=token, base_url=args.base_url, bridge_url="")

    # STEP 1: /api/auth/session
    print("\n--> STEP 1: Probing GET /api/auth/session...")
    session_url = f"{args.base_url}/api/auth/session"
    headers_step1 = {
        "User-Agent": DEFAULT_CHATGPT_USER_AGENT,
        "Accept": "application/json",
        "Cookie": format_chatgpt_cookie(token),
    }

    access_token = None
    try:
        with httpx.Client(timeout=args.timeout, headers=headers_step1, follow_redirects=True) as client:
            resp1 = client.get(session_url)
            print(f"    HTTP Status:       {resp1.status_code}")
            print(f"    Sanitized Headers: {filter_headers(resp1.headers)}")
            print(f"    Response Body:     {sanitize_body(resp1.text)}")

            if resp1.status_code in (401, 403):
                err = provider._classify_auth_failure(resp1, "/api/auth/session")
                print(f"\n[!] STEP 1 AUTH FAILURE:")
                print(f"    Reason:   {err.reason}")
                print(f"    Detail:   {err.detail}")
                print(f"    Message:  {err.message}")
                return 1

            if resp1.status_code != 200:
                print(f"\n[!] STEP 1 ERROR: Unexpected HTTP status {resp1.status_code}")
                return 1

            data1 = resp1.json()
            access_token = data1.get("accessToken")
            if not access_token:
                err = provider._classify_auth_failure(resp1, "/api/auth/session")
                print(f"\n[!] STEP 1 MISSING ACCESS TOKEN: {err.reason} ({err.detail})")
                return 1

            print(f"    [+] Access Token extracted! (Length: {len(access_token)})")
    except Exception as exc:
        print(f"\n[!] STEP 1 EXCEPTION: {type(exc).__name__}: {exc}")
        return 1

    # STEP 2: /backend-api/sentinel/chat-requirements
    print("\n--> STEP 2: Probing POST /backend-api/sentinel/chat-requirements...")
    req_url = f"{args.base_url}/backend-api/sentinel/chat-requirements"
    device_id = str(uuid.uuid4())
    headers_step2 = {
        "User-Agent": DEFAULT_CHATGPT_USER_AGENT,
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "oai-device-id": device_id,
    }

    req_token = None
    proof_token = None
    try:
        with httpx.Client(timeout=args.timeout, headers=headers_step2) as client:
            resp2 = client.post(req_url, json={"p": ""})
            print(f"    HTTP Status:       {resp2.status_code}")
            print(f"    Sanitized Headers: {filter_headers(resp2.headers)}")
            print(f"    Response Body:     {sanitize_body(resp2.text)}")

            if resp2.status_code == 200:
                data2 = resp2.json()
                req_token = data2.get("token")
                turnstile = data2.get("turnstile") or {}
                if turnstile.get("required"):
                    print(f"    [!] Cloudflare Turnstile Bytecode is REQUIRED! (Direct Python path will be blocked)")
                pow_spec = data2.get("proofofwork") or {}
                if pow_spec.get("required") and pow_spec.get("seed"):
                    seed = str(pow_spec["seed"])
                    difficulty = str(pow_spec.get("difficulty", "000032"))
                    print(f"    Solving Proof-of-Work (seed={seed}, diff={difficulty})...")
                    proof_token = solve_sentinel_pow(seed, difficulty)
                    print(f"    [+] Proof-of-Work solved! (Proof length: {len(proof_token)})")
            else:
                print(f"    [!] Sentinel check returned non-200 status: {resp2.status_code}")
    except Exception as exc:
        print(f"\n[!] STEP 2 EXCEPTION: {type(exc).__name__}: {exc}")

    # STEP 3: /backend-api/conversation probe
    print("\n--> STEP 3: Probing POST /backend-api/conversation (test message)...")
    conv_url = f"{args.base_url}/backend-api/conversation"
    headers_step3 = {
        "User-Agent": DEFAULT_CHATGPT_USER_AGENT,
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "oai-device-id": device_id,
    }
    if req_token:
        headers_step3["openai-sentinel-chat-requirements-token"] = req_token
    if proof_token:
        headers_step3["openai-sentinel-proof-token"] = proof_token

    payload = {
        "action": "next",
        "messages": [
            {
                "id": str(uuid.uuid4()),
                "author": {"role": "user"},
                "content": {"content_type": "text", "parts": ["Reply with: ok"]},
                "metadata": {},
            }
        ],
        "parent_message_id": str(uuid.uuid4()),
        "model": "auto",
        "timezone_offset_min": -420,
        "history_and_training_disabled": True,
        "conversation_mode": {"kind": "primary_assistant"},
    }

    try:
        with httpx.Client(timeout=args.timeout) as client:
            with client.stream("POST", conv_url, headers=headers_step3, json=payload) as resp3:
                print(f"    HTTP Status:       {resp3.status_code}")
                print(f"    Sanitized Headers: {filter_headers(resp3.headers)}")

                if resp3.status_code in (401, 403):
                    resp3.read()
                    print(f"    Response Body:     {sanitize_body(resp3.text)}")
                    err = provider._classify_auth_failure(resp3, "/backend-api/conversation")
                    print(f"\n[!] STEP 3 AUTH FAILURE:")
                    print(f"    Reason:   {err.reason}")
                    print(f"    Detail:   {err.detail}")
                    print(f"    Message:  {err.message}")
                    return 1

                if resp3.status_code == 200:
                    first_line = ""
                    for line in resp3.iter_lines():
                        if line and line.startswith("data: ") and line != "data: [DONE]":
                            first_line = line[:200]
                            break
                    print(f"    [+] Conversation stream active! First line: {first_line}")
                    print("\n[SUCCESS] Direct ChatGPT Web connection verified end-to-end!")
                    return 0
                else:
                    resp3.read()
                    print(f"    Response Body:     {sanitize_body(resp3.text)}")
                    print(f"\n[!] STEP 3 ERROR: Status {resp3.status_code}")
                    return 1
    except Exception as exc:
        print(f"\n[!] STEP 3 EXCEPTION: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
