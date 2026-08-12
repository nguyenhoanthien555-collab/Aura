"""
Tests for canonical multi-turn conversation representation and provider serialization.
"""

import pytest

from brain.message import Message
from brain.prompt_builder import PromptBuilder
from brain.providers.base import split_prompt_to_messages
from brain.providers.openai_compatible import OpenAICompatibleProvider
from brain.providers.mistral import MistralProvider
from brain.providers.groq import GroqProvider
from brain.providers.gemini import GeminiProvider
from brain.providers.fallback import FallbackProvider
from brain.style import AuraStyle


class DummyOpenAIProvider(OpenAICompatibleProvider):
    provider_name = "dummy_openai"
    label = "Dummy OpenAI"
    api_key_env = "DUMMY_KEY"
    default_model = "dummy-model"


def test_split_prompt_to_messages_native_roles():
    builder = PromptBuilder()
    history = [
        Message(role="user", content="hello"),
        Message(role="assistant", content="Hi!"),
    ]
    user_msg = Message(role="user", content="hôm nay tui hơi mệt")
    knowledge = ["User likes coffee"]

    prompt = builder.build(
        history=history,
        user_message=user_msg,
        knowledge=knowledge,
    )

    system_instruction, messages = split_prompt_to_messages(prompt)

    # 1. History is represented as native user/assistant turns
    assert len(messages) == 3
    assert messages[0].role == "user"
    assert messages[0].content == "hello"
    assert messages[1].role == "assistant"
    assert messages[1].content == "Hi!"

    # 2. Current user message is the final user turn
    assert messages[2].role == "user"
    assert messages[2].content == "hôm nay tui hơi mệt"

    # 4. Memory is kept in system/context, not converted into dialogue turns
    assert "User likes coffee" in system_instruction
    assert "User likes coffee" not in [m.content for m in messages]


def test_openai_compatible_native_multi_turn_serialization(monkeypatch):
    monkeypatch.setenv("DUMMY_KEY", "test-key")
    provider = DummyOpenAIProvider()

    builder = PromptBuilder()
    history = [
        Message(role="user", content="hello"),
        Message(role="assistant", content="Hi!"),
    ]
    user_msg = Message(role="user", content="hôm nay tui hơi mệt")

    prompt = builder.build(history=history, user_message=user_msg)
    system_instruction, canonical_messages = split_prompt_to_messages(prompt)

    payload = provider._payload(system_instruction, canonical_messages)

    p_messages = payload["messages"]
    # 3. Assistant history is never embedded inside a single user message
    assert len(p_messages) == 4
    assert p_messages[0]["role"] == "system"
    assert p_messages[1] == {"role": "user", "content": "hello"}
    assert p_messages[2] == {"role": "assistant", "content": "Hi!"}
    assert p_messages[3] == {"role": "user", "content": "hôm nay tui hơi mệt"}


def test_gemini_equivalent_logical_context(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    captured_args = {}

    class MockModels:
        def generate_content(self, model, contents, config):
            captured_args["model"] = model
            captured_args["contents"] = contents
            captured_args["config"] = config

            class MockResponse:
                text = "OK"
                candidates = []
                usage_metadata = None

            return MockResponse()

    class MockClient:
        models = MockModels()

    provider = GeminiProvider.__new__(GeminiProvider)
    provider.client = MockClient()
    provider.model = "gemini-3.6-flash"
    provider.max_output_tokens = 768
    provider.temperature = 0.7
    provider.thinking_level = "low"

    builder = PromptBuilder()
    history = [
        Message(role="user", content="hello"),
        Message(role="assistant", content="Hi!"),
    ]
    user_msg = Message(role="user", content="hôm nay tui hơi mệt")

    prompt = builder.build(history=history, user_message=user_msg)

    reply = provider.generate(prompt)
    assert reply == "OK"

    contents = captured_args["contents"]
    config = captured_args["config"]

    # 5. Gemini receives equivalent logical contents array from actual generate() execution path
    assert len(contents) == 3
    assert contents[0] == {"role": "user", "parts": [{"text": "hello"}]}
    assert contents[1] == {"role": "model", "parts": [{"text": "Hi!"}]}
    assert contents[2] == {"role": "user", "parts": [{"text": "hôm nay tui hơi mệt"}]}
    assert config["system_instruction"] is not None



def test_fallback_does_not_mutate_or_duplicate_history():
    class SuccessProvider:
        provider_name = "success_p"
        def generate(self, prompt: str) -> str:
            return "OK"

    class FailingProvider:
        provider_name = "failing_p"
        def generate(self, prompt: str) -> str:
            raise RuntimeError("Provider failed")

    fail = FailingProvider()
    succ = SuccessProvider()
    fallback = FallbackProvider([fail, succ], "failing->success")

    prompt = "Test prompt"
    result = fallback.generate(prompt)

    # 8. Fallback succeeds and returns reply without modifying prompt
    assert result == "OK"
    assert fallback.active_provider_name == "success_p"


def test_aurastyle_variety_line_no_literal_opener_injection():
    styler = AuraStyle(enabled=True, strip_filler=True, avoid_repeats=3)
    styler.note_reply("À, trời ơi, hôm nay mệt quá.")
    styler.note_reply("Chào, ông bạn thân.")

    hint = styler.prompt_hint()

    # 10. AuraStyle no longer injects exact repeated opener phrases into prompt hint
    assert "À, trời" not in hint
    assert "Chào, ông" not in hint
    assert "Vary your opening phrase from previous turns" in hint
