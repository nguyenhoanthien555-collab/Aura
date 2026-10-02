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
    ):
        raw_token = session_token or os.environ.get("CHATGPT_SESSION_TOKEN", "")
        self.session_token = raw_token.strip()
        self.model = model or "auto"
        self.timeout = float(timeout)
        self.base_url = base_url.rstrip("/")
        self.provider_name = "chatgpt_web"

        self._access_token: Optional[str] = None
        self._token_expires: float = 0
        self._device_id: str = str(uuid.uuid4())

    def _ensure_access_token(self) -> str:
        """
        Retrieves or refreshes the JWT accessToken via /api/auth/session.
        Implements 24/7 continuous session rollover.
        """
        now = time.time()
        if self._access_token and (self._token_expires - now > 300):
            return self._access_token

        if not self.session_token:
            raise ProviderAuthError(
                "CHATGPT_SESSION_TOKEN is not configured. Set your clone account "
                "__Secure-next-auth.session-token cookie in environment variables."
            )

        session_url = f"{self.base_url}/api/auth/session"
        headers = {
            "User-Agent": DEFAULT_CHATGPT_USER_AGENT,
            "Accept": "application/json",
            "Cookie": f"__Secure-next-auth.session-token={self.session_token}",
        }

        try:
            with httpx.Client(timeout=15.0, headers=headers, follow_redirects=True) as client:
                resp = client.get(session_url)
                if resp.status_code in (401, 403):
                    raise ProviderAuthError(
                        f"ChatGPT Web session token is invalid or expired (HTTP {resp.status_code}). "
                        "Please update CHATGPT_SESSION_TOKEN."
                    )
                if resp.status_code != 200:
                    raise ProviderUnavailableError(
                        f"ChatGPT Web /api/auth/session returned HTTP {resp.status_code}"
                    )

                data = resp.json()
                token = data.get("accessToken")
                if not token:
                    raise ProviderAuthError(
                        "ChatGPT Web session response did not contain an accessToken. "
                        "The session token may have been revoked."
                    )

                self._access_token = str(token)
                # NextAuth access tokens typically live 10-30 days; refresh every 12 hours
                self._token_expires = now + 43200
                logger.info("ChatGPT Web access token refreshed successfully.")
                return self._access_token

        except (ProviderAuthError, ProviderUnavailableError):
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

    def stream(self, prompt: str, **kwargs: Any) -> Iterator[str]:
        """
        Streams response fragments from ChatGPT Web SSE stream.
        """
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
                        raise ProviderAuthError(f"ChatGPT Web rejected access token: HTTP {response.status_code}")
                    if response.status_code == 429:
                        raise ProviderRateLimitError("ChatGPT Web rate limit exceeded.")
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
        Synchronously gathers the complete response from ChatGPT Web.
        """
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
