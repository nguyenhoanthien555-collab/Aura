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
    router = BrainRouter(provider_name="chatgpt")
    assert router.provider_name == "chatgpt"
    assert isinstance(router.provider, ChatGPTProvider)
    assert router.provider.model == "gpt-4o"


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
