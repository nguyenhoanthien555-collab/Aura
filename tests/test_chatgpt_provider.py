"""
Unit tests for ChatGPTProvider and ChatGPT Main Brain integration.
"""

from unittest.mock import patch

import pytest

from brain.providers.capabilities import CapabilityStatus, capabilities_for
from brain.providers.chatgpt import ChatGPTProvider
from brain.providers.errors import ProviderRateLimitError, ProviderUnavailableError
from brain.router import BrainRouter


@pytest.fixture(autouse=True)
def mock_openai_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-mock-key")
    monkeypatch.setattr(
        "brain.providers.http_chat.load_dotenv", lambda *a, **k: None
    )


def test_chatgpt_provider_defaults():
    """Verify ChatGPTProvider properties and default model."""
    provider = ChatGPTProvider()
    assert provider.provider_name == "chatgpt"
    assert provider.label == "ChatGPT"
    assert provider.model == "gpt-4o"
    assert provider.token_field == "max_completion_tokens"


def test_chatgpt_provider_custom_model():
    """Verify custom model parameter overrides default."""
    provider = ChatGPTProvider(model="o3-mini")
    assert provider.model == "o3-mini"


def test_chatgpt_capabilities_registered():
    """Verify ChatGPT is registered as function-calling capable."""
    caps = capabilities_for("chatgpt")
    assert caps.function_calling == CapabilityStatus.UNKNOWN  # Structurally capable


def test_chatgpt_router_instantiation():
    """Verify BrainRouter resolves chatgpt provider when key is provided."""
    from brain.providers.fallback import FallbackProvider

    router = BrainRouter(provider_name="chatgpt")
    assert router.provider_name == "chatgpt"
    actual = router.provider.providers[0] if isinstance(router.provider, FallbackProvider) else router.provider
    assert isinstance(actual, ChatGPTProvider)
    assert actual.model == "gpt-4o"


def test_chatgpt_generate_mock():
    """Verify generate sends standard payload and extracts response."""
    provider = ChatGPTProvider()

    mock_resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Chào bạn! Mình là Aura đây.",
                }
            }
        ]
    }

    with patch.object(provider, "_send", return_value=mock_resp) as mock_send:
        reply = provider.generate("Xin chào")
        assert reply == "Chào bạn! Mình là Aura đây."
        assert mock_send.call_count == 1
        payload = mock_send.call_args[0][0]
        assert payload["model"] == "gpt-4o"
        assert payload["messages"][-1]["content"] == "Xin chào"


def test_chatgpt_generate_with_tools_mock():
    """Verify native function calling payload and turn extraction."""
    provider = ChatGPTProvider()

    mock_resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_123",
                            "type": "function",
                            "function": {
                                "name": "current_time",
                                "arguments": "{}",
                            },
                        }
                    ],
                }
            }
        ]
    }

    with patch.object(provider, "_send", return_value=mock_resp) as mock_send:
        turn = provider.generate_with_tools(
            system="You are Aura.",
            messages=[{"role": "user", "content": "Mấy giờ rồi?"}],
            tools=[{"type": "function", "function": {"name": "current_time"}}],
        )
        assert len(turn.tool_calls) == 1
        assert turn.tool_calls[0].name == "current_time"
        assert turn.tool_calls[0].call_id == "call_123"
        payload = mock_send.call_args[0][0]
        assert payload["tool_choice"] == "auto"
        assert len(payload["tools"]) == 1


from brain.providers.http_chat import classify_failure


def test_chatgpt_error_mapping():
    """Verify rate limit and server error classifications."""
    provider = ChatGPTProvider()

    # 429 Rate limit
    err_429 = classify_failure(provider.label, 429, '{"error": {"message": "Rate limit exceeded"}}')
    assert isinstance(err_429, ProviderRateLimitError)

    # 500 Server error
    err_500 = classify_failure(provider.label, 500, '{"error": {"message": "Internal error"}}')
    assert isinstance(err_500, ProviderUnavailableError)


def test_chatgpt_reasoning_model_payload_adaptation():
    """LLM-COMPAT-005: Reasoning models (o1, o3) omit temperature and map system to developer role."""
    provider = ChatGPTProvider(model="o3-mini")
    assert provider.is_reasoning_model is True

    payload = provider._payload("System instructions here", "User query")
    assert "temperature" not in payload
    assert payload["messages"][0]["role"] == "developer"
    assert payload["messages"][0]["content"] == "System instructions here"
    assert payload["messages"][1]["role"] == "user"


def test_chatgpt_reasoning_model_send_adaptation():
    """LLM-COMPAT-005: _send ensures reasoning payload has no temperature and maps system to developer."""
    provider = ChatGPTProvider(model="o1")
    assert provider.is_reasoning_model is True

    mock_resp = {
        "choices": [{"message": {"role": "assistant", "content": "Reasoning response"}}]
    }

    with patch("brain.providers.http_chat.HttpChatProvider._send", return_value=mock_resp) as mock_super_send:
        raw_payload = {
            "model": "o1",
            "messages": [{"role": "system", "content": "Rules"}, {"role": "user", "content": "Question"}],
            "temperature": 0.7,
        }
        res = provider._send(raw_payload)
        assert res == mock_resp
        dispatched_payload = mock_super_send.call_args[0][0]
        assert "temperature" not in dispatched_payload
        assert dispatched_payload["messages"][0]["role"] == "developer"


def test_chatgpt_unsupported_reasoning_tools_rejected():
    """LLM-COMPAT-005: Models without tool support (o1-mini, o1-preview) fail with ProviderUnavailableError."""
    provider = ChatGPTProvider(model="o1-mini")
    with pytest.raises(ProviderUnavailableError) as exc_info:
        provider.generate_with_tools("System", [{"role": "user", "content": "test"}], [{"name": "tool"}])
    assert "does not support tools" in str(exc_info.value)

