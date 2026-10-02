"""
Unit tests for ChatGPTWebProvider.
Verifies automated session rollover, Proof-of-Work Sentinel solver, SSE parsing,
tool calling extraction, and provider error classification.
"""

import base64
import hashlib
import json
from unittest.mock import MagicMock, patch
import pytest

from brain.providers.chatgpt_web import ChatGPTWebProvider, solve_sentinel_pow
from brain.providers.errors import (
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


def test_solve_sentinel_pow():
    """Verify solve_sentinel_pow solves difficulty constraint."""
    seed = "test-seed-12345"
    # Low difficulty target (starts with high hex char so it solves in <5 iterations)
    difficulty = "ffff"
    encoded_token = solve_sentinel_pow(seed, difficulty, max_iterations=100)
    assert encoded_token != "0"

    decoded_json = base64.b64decode(encoded_token.encode("utf-8")).decode("utf-8")
    payload = json.loads(decoded_json)
    nonce, out_seed = payload[0], payload[1]
    assert out_seed == seed

    # Verify sha256
    candidate = f"{seed}{nonce}".encode("utf-8")
    digest = hashlib.sha256(candidate).hexdigest()
    assert digest[:len(difficulty)] <= difficulty


def test_chatgpt_web_auth_missing_token():
    """Missing session token raises ProviderAuthError."""
    provider = ChatGPTWebProvider(session_token="")
    with pytest.raises(ProviderAuthError) as exc_info:
        provider._ensure_access_token()
    assert "CHATGPT_SESSION_TOKEN is not configured" in str(exc_info.value)


def test_chatgpt_web_auth_refresh_success():
    """Valid session token fetches accessToken via /api/auth/session and caches it."""
    provider = ChatGPTWebProvider(session_token="valid-cookie-token")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "user": {"name": "Aura Clone"},
        "expires": "2026-10-15T00:00:00.000Z",
        "accessToken": "ey-jwt-fresh-access-token",
    }

    with patch("httpx.Client.get", return_value=mock_resp):
        tok = provider._ensure_access_token()
        assert tok == "ey-jwt-fresh-access-token"
        assert provider._access_token == "ey-jwt-fresh-access-token"

        # Subsequent call uses cache without making HTTP request
        with patch("httpx.Client.get") as second_get:
            assert provider._ensure_access_token() == "ey-jwt-fresh-access-token"
            second_get.assert_not_called()


def test_chatgpt_web_auth_refresh_unauthorized():
    """Expired or revoked session cookie raises ProviderAuthError."""
    provider = ChatGPTWebProvider(session_token="expired-token")

    mock_resp = MagicMock()
    mock_resp.status_code = 401

    with patch("httpx.Client.get", return_value=mock_resp):
        with pytest.raises(ProviderAuthError) as exc_info:
            provider._ensure_access_token()
        assert "invalid or expired" in str(exc_info.value)


def test_chatgpt_web_stream_and_generate():
    """Tests SSE stream parsing and aggregation into generate()."""
    provider = ChatGPTWebProvider(session_token="dummy-token")
    provider._access_token = "dummy-access"
    provider._token_expires = 9999999999

    sse_lines = [
        b'data: {"message": {"content": {"parts": ["Ch\xc3\xa0o "]}}}',
        b'data: {"message": {"content": {"parts": ["Ch\xc3\xa0o Thi\xe1\xbb\x87n!"]}}}',
        b"data: [DONE]",
    ]

    mock_stream_resp = MagicMock()
    mock_stream_resp.status_code = 200
    mock_stream_resp.iter_lines.return_value = [l.decode("utf-8") for l in sse_lines]

    with patch.object(provider, "_get_sentinel_tokens", return_value=(None, None)):
        with patch("httpx.Client.stream") as mock_stream_ctx:
            mock_stream_ctx.return_value.__enter__.return_value = mock_stream_resp

            # Stream test
            deltas = list(provider.stream("Chào bạn"))
            assert "".join(deltas) == "Chào Thiện!"

            # Generate test
            full = provider.generate("Chào bạn")
            assert full == "Chào Thiện!"


def test_chatgpt_web_tool_call_extraction():
    """Verify tool calls formatted as ```tool_call are intercepted and parsed."""
    provider = ChatGPTWebProvider(session_token="dummy-token")
    provider._access_token = "dummy-access"
    provider._token_expires = 9999999999

    model_output = """
    Tôi sẽ mở Discord cho bạn ngay.
    ```tool_call
    {"tool": "android.app_launch", "arguments": {"package_name": "com.discord"}}
    ```
    """

    with patch.object(provider, "generate", return_value=model_output):
        result = provider.generate_with_tools(
            system_prompt="You are Aura.",
            messages=[{"role": "user", "content": "Mở Discord giúp tôi"}],
            tools=[{"name": "android.app_launch", "description": "Launch app", "parameters": {}}],
        )

        assert result["type"] == "tool_call"
        assert result["tool"] == "android.app_launch"
        assert result["arguments"] == {"package_name": "com.discord"}


def test_chatgpt_web_natural_response_without_tools():
    """Verify direct natural conversational response when no tool is invoked."""
    provider = ChatGPTWebProvider(session_token="dummy-token")
    provider._access_token = "dummy-access"
    provider._token_expires = 9999999999

    model_output = "Chào bạn! Hôm nay tôi có thể giúp gì cho bạn?"

    with patch.object(provider, "generate", return_value=model_output):
        result = provider.generate_with_tools(
            system_prompt="You are Aura.",
            messages=[{"role": "user", "content": "Chào Aura"}],
            tools=[{"name": "android.app_launch", "description": "Launch app", "parameters": {}}],
        )

        assert result["type"] == "message"
        assert result["content"] == model_output


def test_chatgpt_web_error_mapping():
    """Verify status code mapping to Aura provider error classes."""
    provider = ChatGPTWebProvider(session_token="dummy-token")
    provider._access_token = "dummy-access"
    provider._token_expires = 9999999999

    # 429 Rate limit
    resp_429 = MagicMock(status_code=429)
    with patch.object(provider, "_get_sentinel_tokens", return_value=(None, None)):
        with patch("httpx.Client.stream") as mock_stream_ctx:
            mock_stream_ctx.return_value.__enter__.return_value = resp_429
            with pytest.raises(ProviderRateLimitError):
                provider.generate("test")

    # 500 Server error
    resp_500 = MagicMock(status_code=500)
    with patch.object(provider, "_get_sentinel_tokens", return_value=(None, None)):
        with patch("httpx.Client.stream") as mock_stream_ctx:
            mock_stream_ctx.return_value.__enter__.return_value = resp_500
            with pytest.raises(ProviderUnavailableError):
                provider.generate("test")


def test_format_chatgpt_cookie():
    from brain.providers.chatgpt_web import format_chatgpt_cookie

    # Direct cookie string
    raw = "__Secure-next-auth.session-token.0=part0; __Secure-next-auth.session-token.1=part1"
    assert format_chatgpt_cookie(raw) == raw

    # Semicolon separated chunks
    chunks_semi = "val0;val1"
    formatted = format_chatgpt_cookie(chunks_semi)
    assert "__Secure-next-auth.session-token.0=val0" in formatted
    assert "__Secure-next-auth.session-token.1=val1" in formatted
    assert "__Secure-next-auth.session-token=val0val1" in formatted

    # Single short token
    assert format_chatgpt_cookie("short_token") == "__Secure-next-auth.session-token=short_token"

