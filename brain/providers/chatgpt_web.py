"""
ChatGPT Web Provider for Aura.

Enables Aura to use ChatGPT Web (chatgpt.com) as its 24/7 Main Brain via a dedicated clone
account session token (__Secure-next-auth.session-token).

Architecture & Invariants:
1. Automated Session Rollover: Contacts https://chatgpt.com/api/auth/session to mint fresh JWT
   accessTokens and maintain the session 24/7 without manual relogin.
2. Pure-Python Proof-of-Work (Sentinel) challenge solver for cloud/datacenter environment compatibility.
3. Realtime Server-Sent Events (SSE) stream parsing.
4. Tool calling support via structured ````tool_call```` extraction, delegating execution
   strictly to Aura's ToolExecutor and evidence verification pipeline.
5. Fully conforms to LLM and StreamingLLM protocols with automatic failover via ProviderUnavailableError.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
import uuid
from typing import Any, Iterator, Optional

import httpx

from core.logger import logger
from brain.ports import LLM
from brain.providers.errors import (
    AUTH_CONTEXT_INVALID,
    AUTH_EXPIRED,
    AUTH_FORBIDDEN,
    AUTH_INVALID,
    AUTH_MISSING,
    AUTH_UNKNOWN,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from brain.streaming import StreamingLLM

DEFAULT_CHATGPT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

DEFAULT_CHATGPT_WEB_BASE = "https://chatgpt.com"


def token_fingerprint(token: str | None) -> str:
    """
    Calculates a truncated SHA-256 fingerprint for token identity verification.
    Returns 'sha256:<12 hex>' or '' if empty/None. Zero secret leakage.
    """
    raw = (token or "").strip()
    if not raw:
        return ""
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"sha256:{digest}"


def solve_sentinel_pow(seed: str, difficulty: str, max_iterations: int = 500_000) -> str:
    """
    Solves the OpenAI Sentinel Proof-of-Work challenge in pure Python.
    Iterates nonces until sha256(seed + nonce) meets the difficulty constraint.
    """
    diff_len = len(difficulty)
    for nonce in range(max_iterations):
        candidate = f"{seed}{nonce}".encode("utf-8")
        h = hashlib.sha256(candidate).hexdigest()
        if h[:diff_len] <= difficulty:
            payload = json.dumps([str(nonce), seed])
            return base64.b64encode(payload.encode("utf-8")).decode("utf-8")
    return "0"


def format_chatgpt_cookie(raw_token: str) -> str:
    """
    Formats the Cookie header for ChatGPT NextAuth session token.
    Handles:
    1. Full cookie string (e.g. '__Secure-next-auth.session-token.0=...; __Secure-next-auth.session-token.1=...')
    2. Chunked tokens joined with ';' or '\n'
    3. Single unchunked JWT string (or merged chunked string)
    """
    raw = (raw_token or "").strip()
    if not raw:
        return ""
    if "__Secure-next-auth.session-token" in raw and "=" in raw:
        return raw

    if ";" in raw:
        parts = [p.strip() for p in raw.split(";") if p.strip()]
    elif "\n" in raw:
        parts = [p.strip() for p in raw.splitlines() if p.strip()]
    else:
        parts = [raw]

    if len(parts) >= 2:
        cookies = []
        for idx, part in enumerate(parts):
            cookies.append(f"__Secure-next-auth.session-token.{idx}={part}")
        return "; ".join(cookies)

    return f"__Secure-next-auth.session-token={parts[0]}"


class ChatGPTWebProvider(LLM, StreamingLLM):
    """
    LLM provider connecting Aura Cloud directly to ChatGPT Web backend.
    """

    label = "chatgpt_web"

    def __init__(
        self,
        session_token: Optional[str] = None,
        model: str = "auto",
        timeout: float = 60.0,
        base_url: str = DEFAULT_CHATGPT_WEB_BASE,
        bridge_url: Optional[str] = None,
    ):
        self._explicit_token: Optional[str] = session_token
        self.model = model or "auto"
        self.timeout = float(timeout)
        self.base_url = base_url.rstrip("/")
        self.bridge_url = (os.environ.get("CHATGPT_BRIDGE_URL", "http://127.0.0.1:8765") if bridge_url is None else bridge_url).rstrip("/")
        self.provider_name = "chatgpt_web"

        self._access_token: Optional[str] = None
        self._token_expires: float = 0
        self._device_id: str = str(uuid.uuid4())
        self._last_fingerprint: str = self.credential_fingerprint()
        self._last_auth_reason: Optional[str] = None
        self._last_http_status: Optional[int] = None
        self._last_error_at: Optional[float] = None
        self._last_sentinel: dict[str, Any] = {"status": None, "turnstile_required": False}

        logger.info(
            "chatgpt_web: token_present=%s token_length=%d token_fingerprint=%s source=%s",
            bool(self.session_token),
            len(self.session_token),
            self._last_fingerprint or "none",
            "explicit" if self._explicit_token is not None else "env",
        )

    @property
    def session_token(self) -> str:
        if self._explicit_token is not None:
            token = self._explicit_token.strip()
        else:
            token = os.environ.get("CHATGPT_SESSION_TOKEN", "").strip()

        fp = token_fingerprint(token)
        if fp != getattr(self, "_last_fingerprint", None):
            logger.info(
                "chatgpt_web: token fingerprint changed from %s to %s; resetting cached session",
                getattr(self, "_last_fingerprint", "none") or "none",
                fp or "none",
            )
            self._last_fingerprint = fp
            self._access_token = None
            self._token_expires = 0
            self._device_id = str(uuid.uuid4())
        return token

    @session_token.setter
    def session_token(self, value: Optional[str]) -> None:
        self._explicit_token = value
        _ = self.session_token

    def credential_fingerprint(self) -> str:
        return token_fingerprint(self.session_token)

    def diagnostics(self) -> dict[str, Any]:
        token = self.session_token
        return {
            "provider": "chatgpt_web",
            "enabled": bool(token),
            "token_present": bool(token),
            "token_length": len(token),
            "token_fingerprint": self.credential_fingerprint(),
            "token_source": "explicit" if self._explicit_token is not None else "env",
            "base_url": self.base_url,
            "endpoints": {
                "session": "/api/auth/session",
                "requirements": "/backend-api/sentinel/chat-requirements",
                "conversation": "/backend-api/conversation",
            },
            "auth_mode": "cookie->bearer",
            "bridge_url": self.bridge_url,
            "bridge_active": self._is_bridge_active(),
            "access_token_cached": bool(self._access_token),
            "access_token_expires_in_s": max(0, int(self._token_expires - time.time())) if self._token_expires else 0,
            "last_auth_reason": self._last_auth_reason,
            "last_http_status": self._last_http_status,
            "last_error_at": self._last_error_at,
        }

    def _classify_auth_failure(self, resp: Any, endpoint: str) -> ProviderAuthError:
        status = getattr(resp, "status_code", None)
        self._last_http_status = status
        self._last_error_at = time.time()

        headers = getattr(resp, "headers", {}) or {}
        cf_mitigated = ""
        if hasattr(headers, "get"):
            cf_mitigated = str(headers.get("cf-mitigated", "") or "")

        body_text = ""
        if hasattr(resp, "text") and isinstance(resp.text, str):
            body_text = resp.text[:2000]
        elif hasattr(resp, "content") and isinstance(resp.content, (bytes, bytearray)):
            try:
                body_text = resp.content.decode("utf-8", errors="replace")[:2000]
            except Exception:
                body_text = ""

        detail_field = ""
        error_field = ""
        data: dict[str, Any] = {}
        if hasattr(resp, "json"):
            try:
                parsed = resp.json()
                if isinstance(parsed, dict):
                    data = parsed
                    detail_field = str(data.get("detail", ""))
                    error_field = str(data.get("error", ""))
            except Exception:
                pass

        reason = AUTH_UNKNOWN
        detail = ""

        if not self.session_token:
            reason = AUTH_MISSING
            detail = "token_missing"
        elif status == 401:
            reason = AUTH_INVALID
            detail = "http_401"
        elif status == 403:
            cf_challenge = (
                cf_mitigated == "challenge"
                or "Just a moment" in body_text
                or "cf_chl" in body_text
                or "challenge-platform" in body_text
            )
            unusual_act = (
                "unusual activity" in detail_field.lower()
                or "unusual activity" in body_text.lower()
            )
            sentinel_missing = (
                endpoint == "/backend-api/conversation"
                and not getattr(self, "_last_sentinel", {}).get("status")
            )

            if cf_challenge:
                reason = AUTH_FORBIDDEN
                detail = "cloudflare_challenge"
            elif unusual_act:
                reason = AUTH_FORBIDDEN
                detail = "unusual_activity"
            elif sentinel_missing:
                reason = AUTH_CONTEXT_INVALID
                detail = "sentinel_missing"
            else:
                reason = AUTH_FORBIDDEN
                detail = "http_403"
        elif "RefreshAccessTokenError" in body_text or "RefreshAccessTokenError" in error_field:
            reason = AUTH_EXPIRED
            detail = "refresh_error"
        elif "expired" in body_text.lower() or "revoked" in body_text.lower() or "expired" in error_field.lower():
            reason = AUTH_EXPIRED
            detail = "expired"
        elif status == 200 and endpoint == "/api/auth/session" and (body_text.strip() in ("{}", "") or (data and not data.get("accessToken"))):
            reason = AUTH_INVALID
            detail = "empty_session"
        else:
            reason = AUTH_UNKNOWN
            detail = f"http_{status}" if status else "unknown"

        self._last_auth_reason = reason

        if reason == AUTH_EXPIRED:
            msg = f"ChatGPT Web {endpoint} reported session token expired or revoked ({reason}: {detail})"
        elif reason == AUTH_FORBIDDEN:
            msg = f"ChatGPT Web {endpoint} returned HTTP {status} ({reason}: {detail})"
        elif reason == AUTH_INVALID:
            msg = f"ChatGPT Web {endpoint} unauthorized (HTTP {status}) ({reason}: {detail})"
        elif reason == AUTH_MISSING:
            msg = "ChatGPT Web session token is missing"
        elif reason == AUTH_CONTEXT_INVALID:
            msg = f"ChatGPT Web {endpoint} missing required request context ({reason}: {detail})"
        else:
            msg = f"ChatGPT Web {endpoint} auth failure (HTTP {status}) ({reason}: {detail})"

        return ProviderAuthError(
            message=msg,
            reason=reason,
            http_status=status,
            detail=detail,
            endpoint=endpoint,
        )

    def _ensure_access_token(self) -> str:
        """
        Retrieves or refreshes the JWT accessToken via /api/auth/session.
        Implements 24/7 continuous session rollover.
        """
        now = time.time()
        if self._access_token and (self._token_expires - now > 300):
            return self._access_token

        if not self.session_token:
            self._last_auth_reason = AUTH_MISSING
            self._last_http_status = None
            self._last_error_at = time.time()
            raise ProviderAuthError(
                "CHATGPT_SESSION_TOKEN is not configured. Set your clone account "
                "__Secure-next-auth.session-token cookie in environment variables.",
                reason=AUTH_MISSING,
                http_status=None,
                detail="token_missing",
                endpoint="/api/auth/session",
            )

        session_url = f"{self.base_url}/api/auth/session"
        headers = {
            "User-Agent": DEFAULT_CHATGPT_USER_AGENT,
            "Accept": "application/json",
            "Cookie": format_chatgpt_cookie(self.session_token),
        }

        try:
            with httpx.Client(timeout=15.0, headers=headers, follow_redirects=True) as client:
                resp = client.get(session_url)
                if resp.status_code in (401, 403):
                    raise self._classify_auth_failure(resp, "/api/auth/session")
                if resp.status_code == 429:
                    ra_hdr = resp.headers.get("Retry-After")
                    ra = float(ra_hdr) if ra_hdr and ra_hdr.isdigit() else None
                    raise ProviderRateLimitError(
                        f"ChatGPT Web /api/auth/session rate limited (HTTP 429)",
                        retry_after=ra,
                    )
                if resp.status_code != 200:
                    raise ProviderUnavailableError(
                        f"ChatGPT Web /api/auth/session returned HTTP {resp.status_code}"
                    )

                data = resp.json()
                token = data.get("accessToken")
                if not token:
                    raise self._classify_auth_failure(resp, "/api/auth/session")

                self._access_token = str(token)
                # NextAuth access tokens typically live 10-30 days; refresh every 12 hours
                self._token_expires = now + 43200
                self._last_http_status = 200
                self._last_auth_reason = None
                logger.info("ChatGPT Web access token refreshed successfully.")
                return self._access_token

        except (ProviderAuthError, ProviderRateLimitError, ProviderUnavailableError):
            raise
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"ChatGPT Web auth session timed out: {exc}") from exc
        except Exception as exc:
            raise ProviderUnavailableError(f"ChatGPT Web auth connection failed: {exc}") from exc

    def _get_sentinel_tokens(self, access_token: str) -> tuple[Optional[str], Optional[str]]:
        """
        Fetches Sentinel chat-requirements and solves Proof-of-Work if requested.
        Returns (requirements_token, proof_token).
        """
        requirements_url = f"{self.base_url}/backend-api/sentinel/chat-requirements"
        headers = {
            "User-Agent": DEFAULT_CHATGPT_USER_AGENT,
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "oai-device-id": self._device_id,
        }

        try:
            with httpx.Client(timeout=10.0, headers=headers) as client:
                resp = client.post(requirements_url, json={"p": ""})
                turnstile_req = False
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        turnstile_req = bool(data.get("turnstile", {}).get("required"))
                    except Exception:
                        pass
                self._last_sentinel = {
                    "status": resp.status_code,
                    "turnstile_required": turnstile_req,
                }
                if turnstile_req:
                    logger.info("ChatGPT Web sentinel check: turnstile required (bytecode)")
                if resp.status_code != 200:
                    return None, None

                data = resp.json()
                req_token = data.get("token")
                proof_token = None

                pow_spec = data.get("proofofwork") or {}
                if pow_spec.get("required") and pow_spec.get("seed"):
                    seed = str(pow_spec["seed"])
                    difficulty = str(pow_spec.get("difficulty", "000032"))
                    proof_token = solve_sentinel_pow(seed, difficulty)

                return req_token, proof_token
        except Exception as err:
            logger.debug("ChatGPT Web sentinel check skipped/failed: %s", err)
            return None, None

    def _is_bridge_active(self) -> bool:
        """Checks if local browser bridge is responsive."""
        if not self.bridge_url:
            return False
        try:
            with httpx.Client(timeout=0.8) as client:
                resp = client.get(f"{self.bridge_url}/health")
                return resp.status_code == 200
        except Exception:
            return False

    def _stream_via_bridge(self, prompt: str, **kwargs: Any) -> Iterator[str]:
        """Streams response tokens from local browser bridge."""
        chat_url = f"{self.bridge_url}/chat"
        payload = {"prompt": prompt, "stream": True, "model": self.model}
        try:
            with httpx.Client(timeout=self.timeout) as client:
                with client.stream("POST", chat_url, json=payload) as resp:
                    if resp.status_code != 200:
                        raise ProviderUnavailableError(f"Browser bridge error HTTP {resp.status_code}")
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        line = line.strip()
                        if line == "data: [DONE]":
                            break
                        if line.startswith("data: "):
                            try:
                                data = json.loads(line[6:].strip())
                                if "error" in data:
                                    raise ProviderUnavailableError(data["error"])
                                if "chunk" in data:
                                    yield data["chunk"]
                            except json.JSONDecodeError:
                                continue
        except (ProviderAuthError, ProviderRateLimitError, ProviderUnavailableError):
            raise
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"Browser bridge stream timed out: {exc}") from exc
        except Exception as exc:
            raise ProviderUnavailableError(f"Browser bridge stream error: {exc}") from exc

    def _generate_via_bridge(self, prompt: str, **kwargs: Any) -> str:
        """Generates full response from local browser bridge."""
        chat_url = f"{self.bridge_url}/chat"
        payload = {"prompt": prompt, "stream": False, "model": self.model}
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(chat_url, json=payload)
                if resp.status_code != 200:
                    raise ProviderUnavailableError(f"Browser bridge error HTTP {resp.status_code}")
                data = resp.json()
                return data.get("text", "")
        except (ProviderAuthError, ProviderRateLimitError, ProviderUnavailableError):
            raise
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"Browser bridge timed out: {exc}") from exc
        except Exception as exc:
            raise ProviderUnavailableError(f"Browser bridge call failed: {exc}") from exc

    def stream(self, prompt: str, **kwargs: Any) -> Iterator[str]:
        """
        Streams response fragments from ChatGPT Web SSE stream or local browser bridge.
        """
        if self._is_bridge_active():
            yield from self._stream_via_bridge(prompt, **kwargs)
            return

        access_token = self._ensure_access_token()
        req_token, proof_token = self._get_sentinel_tokens(access_token)

        conversation_url = f"{self.base_url}/backend-api/conversation"
        headers = {
            "User-Agent": DEFAULT_CHATGPT_USER_AGENT,
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "oai-device-id": self._device_id,
        }
        if req_token:
            headers["openai-sentinel-chat-requirements-token"] = req_token
        if proof_token:
            headers["openai-sentinel-proof-token"] = proof_token

        message_id = str(uuid.uuid4())
        parent_id = str(uuid.uuid4())

        payload = {
            "action": "next",
            "messages": [
                {
                    "id": message_id,
                    "author": {"role": "user"},
                    "content": {
                        "content_type": "text",
                        "parts": [prompt],
                    },
                    "metadata": {},
                }
            ],
            "parent_message_id": parent_id,
            "model": self.model,
            "timezone_offset_min": -420,
            "suggestions": [],
            "history_and_training_disabled": False,
            "conversation_mode": {"kind": "primary_assistant"},
            "force_paragen": False,
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                with client.stream("POST", conversation_url, headers=headers, json=payload) as response:
                    if response.status_code in (401, 403):
                        self._access_token = None
                        try:
                            response.read()
                        except Exception:
                            pass
                        raise self._classify_auth_failure(response, "/backend-api/conversation")
                    if response.status_code == 429:
                        ra_hdr = response.headers.get("Retry-After")
                        ra = float(ra_hdr) if ra_hdr and ra_hdr.isdigit() else None
                        raise ProviderRateLimitError("ChatGPT Web rate limit exceeded.", retry_after=ra)
                    if response.status_code != 200:
                        raise ProviderUnavailableError(f"ChatGPT Web conversation failed with HTTP {response.status_code}")

                    last_emitted_text = ""
                    for raw_line in response.iter_lines():
                        if not raw_line:
                            continue
                        line = raw_line.strip()
                        if line == "data: [DONE]":
                            break
                        if line.startswith("data: "):
                            data_str = line[6:].strip()
                            try:
                                chunk = json.loads(data_str)
                            except Exception:
                                continue

                            # Standard ChatGPT conversation payload: message.content.parts[0]
                            msg = chunk.get("message")
                            if msg and isinstance(msg, dict):
                                content = msg.get("content")
                                if content and isinstance(content, dict):
                                    parts = content.get("parts")
                                    if parts and isinstance(parts[0], str):
                                        full_text = parts[0]
                                        if len(full_text) > len(last_emitted_text):
                                            delta = full_text[len(last_emitted_text):]
                                            last_emitted_text = full_text
                                            yield delta

        except (ProviderAuthError, ProviderRateLimitError, ProviderUnavailableError):
            raise
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"ChatGPT Web stream timed out: {exc}") from exc
        except Exception as exc:
            raise ProviderUnavailableError(f"ChatGPT Web stream error: {exc}") from exc

    def generate(self, prompt: str) -> str:
        """
        Synchronously gathers the complete response from ChatGPT Web or local browser bridge.
        """
        if self._is_bridge_active():
            return self._generate_via_bridge(prompt)
        chunks = list(self.stream(prompt))
        return "".join(chunks).strip()

    def generate_with_tools(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: list[dict],
    ) -> dict:
        """
        Tool calling interface grounded in Aura's ToolExecutor.
        Formats the tool specifications and intercepts ````tool_call```` blocks.
        """
        # 1. Format tool specifications
        tool_descriptions = []
        for t in tools:
            name = t.get("name", "")
            desc = t.get("description", "")
            params = t.get("parameters", {})
            tool_descriptions.append(f"- {name}: {desc}\n  Schema: {json.dumps(params)}")

        prompt_parts = []
        if system_prompt:
            prompt_parts.append(f"[SYSTEM INSTRUCTIONS]\n{system_prompt}")

        if tools:
            tools_block = "\n".join(tool_descriptions)
            prompt_parts.append(
                f"[AVAILABLE TOOLS]\n{tools_block}\n\n"
                "If you need to invoke an Aura tool to answer or act in the physical world, "
                "output ONLY a JSON block formatted exactly as:\n"
                "```tool_call\n"
                '{"tool": "<tool_name>", "arguments": {<key_value_pairs>}}\n'
                "```\n"
                "Do not invent tools not listed. If no tool is required, respond directly with natural text."
            )

        prompt_parts.append("[CONVERSATION]")
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            prompt_parts.append(f"{role.capitalize()}: {content}")

        full_prompt = "\n\n".join(prompt_parts)
        response_text = self.generate(full_prompt)

        # 2. Extract tool_call if present
        tool_call_match = re.search(r"```(?:tool_call|json)?\s*(\{\s*\"tool\".*?\})\s*```", response_text, re.DOTALL)
        if tool_call_match:
            try:
                call_data = json.loads(tool_call_match.group(1))
                tool_name = call_data.get("tool")
                tool_args = call_data.get("arguments", {})
                if tool_name:
                    return {
                        "type": "tool_call",
                        "tool": tool_name,
                        "arguments": tool_args,
                        "raw_response": response_text,
                    }
            except Exception as parse_err:
                logger.debug("Failed to parse tool_call JSON from ChatGPT Web: %s", parse_err)

        return {
            "type": "message",
            "content": response_text,
        }
