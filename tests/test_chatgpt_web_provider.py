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

from brain.providers.chatgpt_web import ChatGPTWebProvider, solve_sentinel_pow, token_fingerprint
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
    """Expired or revoked session cookie raises ProviderAuthError with AUTH_INVALID."""
    provider = ChatGPTWebProvider(session_token="expired-token")

    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.headers = {}
    mock_resp.text = "Unauthorized"

    with patch("httpx.Client.get", return_value=mock_resp):
        with pytest.raises(ProviderAuthError) as exc_info:
            provider._ensure_access_token()
        assert exc_info.value.reason == AUTH_INVALID
        assert exc_info.value.http_status == 401
        assert "invalid or expired" not in str(exc_info.value)


def test_chatgpt_web_stream_and_generate():
    """Tests SSE stream parsing and aggregation into generate()."""
    provider = ChatGPTWebProvider(session_token="dummy-token", bridge_url="")
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
    provider = ChatGPTWebProvider(session_token="dummy-token", bridge_url="")
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

    # Single short token
    assert format_chatgpt_cookie("short_token") == "__Secure-next-auth.session-token=short_token"


def test_chatgpt_web_bridge_stream_and_generate():
    """Verify routing through local browser bridge when active."""
    provider = ChatGPTWebProvider(session_token="dummy", bridge_url="http://127.0.0.1:8765")

    with patch.object(provider, "_is_bridge_active", return_value=True):
        # Test generate via bridge
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"text": "Xin chào từ ChatGPT Web!", "provider": "chatgpt_web"}
        with patch("httpx.Client.post", return_value=mock_resp):
            reply = provider.generate("Chào bạn")
            assert reply == "Xin chào từ ChatGPT Web!"

        # Test stream via bridge
        mock_stream_resp = MagicMock(status_code=200)
        mock_stream_resp.iter_lines.return_value = [
            'data: {"chunk": "Xin chào "}',
            'data: {"chunk": "Thiện!"}',
            'data: [DONE]',
        ]
        with patch("httpx.Client.stream") as mock_stream_ctx:
            mock_stream_ctx.return_value.__enter__.return_value = mock_stream_resp
            deltas = list(provider.stream("Chào bạn"))
            assert "".join(deltas) == "Xin chào Thiện!"


def test_chatgpt_web_missing_token():
    """Missing token raises ProviderAuthError with AUTH_MISSING and does not make HTTP calls."""
    provider = ChatGPTWebProvider(session_token="")
    with patch("httpx.Client.get") as mock_get:
        with pytest.raises(ProviderAuthError) as exc_info:
            provider._ensure_access_token()
        assert exc_info.value.reason == AUTH_MISSING
        assert exc_info.value.detail == "token_missing"
        mock_get.assert_not_called()


def test_chatgpt_web_session_403_cloudflare_challenge():
    """Session 403 with Cloudflare challenge headers classifies as AUTH_FORBIDDEN cloudflare_challenge."""
    provider = ChatGPTWebProvider(session_token="test-token")
    mock_resp = MagicMock(status_code=403)
    mock_resp.headers = {"cf-mitigated": "challenge", "server": "cloudflare"}
    mock_resp.text = "Just a moment... Please verify you are human"

    with patch("httpx.Client.get", return_value=mock_resp):
        with pytest.raises(ProviderAuthError) as exc_info:
            provider._ensure_access_token()
        err = exc_info.value
        assert err.reason == AUTH_FORBIDDEN
        assert err.detail == "cloudflare_challenge"
        assert err.http_status == 403
        assert "expired" not in str(err).lower()
        assert "invalid or expired" not in str(err)


def test_chatgpt_web_session_403_plain():
    """Session 403 without challenge markers classifies as AUTH_FORBIDDEN http_403."""
    provider = ChatGPTWebProvider(session_token="test-token")
    mock_resp = MagicMock(status_code=403)
    mock_resp.headers = {"server": "cloudflare"}
    mock_resp.text = "Forbidden"

    with patch("httpx.Client.get", return_value=mock_resp):
        with pytest.raises(ProviderAuthError) as exc_info:
            provider._ensure_access_token()
        err = exc_info.value
        assert err.reason == AUTH_FORBIDDEN
        assert err.detail == "http_403"
        assert err.http_status == 403


def test_chatgpt_web_conversation_403_unusual_activity():
    """Conversation 403 with unusual activity detail classifies as AUTH_FORBIDDEN unusual_activity."""
    provider = ChatGPTWebProvider(session_token="test-token", bridge_url="")
    provider._access_token = "valid-jwt"
    provider._token_expires = 9999999999

    mock_resp = MagicMock(status_code=403)
    mock_resp.headers = {}
    mock_resp.text = json.dumps({"detail": "Unusual activity has been detected from your device. Try again later."})
    mock_resp.json.return_value = {"detail": "Unusual activity has been detected from your device. Try again later."}

    with patch.object(provider, "_get_sentinel_tokens", return_value=("req_tok", "proof_tok")):
        with patch("httpx.Client.stream") as mock_stream_ctx:
            mock_stream_ctx.return_value.__enter__.return_value = mock_resp
            with pytest.raises(ProviderAuthError) as exc_info:
                list(provider.stream("hello"))
            err = exc_info.value
            assert err.reason == AUTH_FORBIDDEN
            assert err.detail == "unusual_activity"
            assert err.http_status == 403
            assert provider._access_token is None


def test_chatgpt_web_session_200_empty_json():
    """Session 200 with empty JSON dict classifies as AUTH_INVALID empty_session."""
    provider = ChatGPTWebProvider(session_token="test-token")
    mock_resp = MagicMock(status_code=200)
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.text = "{}"
    mock_resp.json.return_value = {}

    with patch("httpx.Client.get", return_value=mock_resp):
        with pytest.raises(ProviderAuthError) as exc_info:
            provider._ensure_access_token()
        err = exc_info.value
        assert err.reason == AUTH_INVALID
        assert err.detail == "empty_session"


def test_chatgpt_web_session_refresh_token_error():
    """Session response containing RefreshAccessTokenError classifies as AUTH_EXPIRED refresh_error."""
    provider = ChatGPTWebProvider(session_token="test-token")
    mock_resp = MagicMock(status_code=200)
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.text = json.dumps({"error": "RefreshAccessTokenError"})
    mock_resp.json.return_value = {"error": "RefreshAccessTokenError"}

    with patch("httpx.Client.get", return_value=mock_resp):
        with pytest.raises(ProviderAuthError) as exc_info:
            provider._ensure_access_token()
        err = exc_info.value
        assert err.reason == AUTH_EXPIRED
        assert err.detail == "refresh_error"


def test_chatgpt_web_conversation_403_sentinel_missing():
    """Conversation 403 when sentinel token was missing classifies as AUTH_CONTEXT_INVALID sentinel_missing."""
    provider = ChatGPTWebProvider(session_token="test-token", bridge_url="")
    provider._access_token = "valid-jwt"
    provider._token_expires = 9999999999
    provider._last_sentinel = {"status": None, "turnstile_required": True}

    mock_resp = MagicMock(status_code=403)
    mock_resp.headers = {}
    mock_resp.text = "Forbidden"
    mock_resp.json.return_value = {}

    with patch.object(provider, "_get_sentinel_tokens", return_value=(None, None)):
        with patch("httpx.Client.stream") as mock_stream_ctx:
            mock_stream_ctx.return_value.__enter__.return_value = mock_resp
            with pytest.raises(ProviderAuthError) as exc_info:
                list(provider.stream("hello"))
            err = exc_info.value
            assert err.reason == AUTH_CONTEXT_INVALID
            assert err.detail == "sentinel_missing"


def test_chatgpt_web_session_429_retry_after():
    """Session 429 parses Retry-After header into ProviderRateLimitError."""
    provider = ChatGPTWebProvider(session_token="test-token")
    mock_resp = MagicMock(status_code=429)
    mock_resp.headers = {"Retry-After": "30"}

    with patch("httpx.Client.get", return_value=mock_resp):
        with pytest.raises(ProviderRateLimitError) as exc_info:
            provider._ensure_access_token()
        assert exc_info.value.retry_after == 30.0


def test_chatgpt_web_session_503_unavailable():
    """Session 503 raises ProviderUnavailableError."""
    provider = ChatGPTWebProvider(session_token="test-token")
    mock_resp = MagicMock(status_code=503)

    with patch("httpx.Client.get", return_value=mock_resp):
        with pytest.raises(ProviderUnavailableError):
            provider._ensure_access_token()


def test_chatgpt_web_session_timeout():
    """Session timeout raises ProviderTimeoutError."""
    import httpx
    provider = ChatGPTWebProvider(session_token="test-token")

    with patch("httpx.Client.get", side_effect=httpx.TimeoutException("Read timed out")):
        with pytest.raises(ProviderTimeoutError):
            provider._ensure_access_token()


def test_chatgpt_web_env_token_change_resets_cache(monkeypatch):
    """When CHATGPT_SESSION_TOKEN env var changes, fingerprint updates and token cache resets."""
    monkeypatch.setenv("CHATGPT_SESSION_TOKEN", "initial_secret_token_111")
    provider = ChatGPTWebProvider(session_token=None)
    fp1 = provider.credential_fingerprint()
    assert fp1.startswith("sha256:")

    provider._access_token = "cached_jwt"
    provider._token_expires = 9999999999

    # Change env var
    monkeypatch.setenv("CHATGPT_SESSION_TOKEN", "second_secret_token_222")
    fp2 = provider.credential_fingerprint()
    assert fp2.startswith("sha256:")
    assert fp1 != fp2

    # Cached access token must have been cleared
    assert provider._access_token is None
    assert provider._token_expires == 0


def test_chatgpt_web_zero_token_leakage(caplog):
    """Raw session token must never appear in diagnostics, error messages, or logs."""
    import logging
    caplog.set_level(logging.DEBUG)

    raw_secret = "EXTREMELY_SECRET_TEST_TOKEN_XYZ987654321"
    provider = ChatGPTWebProvider(session_token=raw_secret)

    diag = provider.diagnostics()
    diag_str = json.dumps(diag)
    assert raw_secret not in diag_str
    assert diag["token_fingerprint"] == token_fingerprint(raw_secret)

    mock_resp = MagicMock(status_code=403)
    mock_resp.headers = {"cf-mitigated": "challenge"}
    mock_resp.text = "Just a moment"

    with patch("httpx.Client.get", return_value=mock_resp):
        with pytest.raises(ProviderAuthError) as exc_info:
            provider._ensure_access_token()
        assert raw_secret not in str(exc_info.value)

    # Check all captured log output
    full_log = caplog.text
    assert raw_secret not in full_log
    assert token_fingerprint(raw_secret) in full_log


