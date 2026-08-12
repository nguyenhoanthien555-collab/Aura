"""
The thinking budget, and what a truncated reply must not look like.

Gemini 3 reasons before it answers and bills that reasoning against
`max_output_tokens`. Aura's config has always treated that number as the
length of the *reply*, so on the shipped 768-token budget an ordinary
question spent ~700 tokens thinking and delivered ~60 tokens of answer,
cut off mid-sentence. Measured against the live API, two of two ordinary
questions finished on MAX_TOKENS.

Nothing in the pipeline noticed. `response.text or ""` discards the
finish reason, so a truncated reply and a short one are the same string,
an empty one is a successful turn, and the fallback chain is never
offered the outage it just had.

These tests pin both halves: the request carries the configured thinking
level, and a truncated response is reported rather than swallowed. No
network - the client is a stub, exactly as the failover tests do it.
"""

import logging

import pytest

from brain.providers.errors import ProviderUnavailableError


class _FinishReason:
    """A finish reason as the SDK spells it: an enum with a `.name`."""

    def __init__(self, name: str):
        self.name = name


class _Candidate:
    def __init__(self, reason: str):
        self.finish_reason = _FinishReason(reason)


class _Usage:
    def __init__(self, thoughts: int):
        self.thoughts_token_count = thoughts


class _Response:
    def __init__(self, text, reason="STOP", thoughts=0):
        self.text = text
        self.candidates = [_Candidate(reason)]
        self.usage_metadata = _Usage(thoughts)


class _Models:
    """Records the config it was called with and returns what it was given."""

    def __init__(self, response):
        self._response = response
        self.calls: list[dict] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return self._response

    def generate_content_stream(self, **kwargs):
        self.calls.append(kwargs)
        return iter([])


def _provider(monkeypatch, response, thinking_level="low", budget=768):
    """A GeminiProvider whose client never leaves the process."""

    monkeypatch.setenv("GEMINI_API_KEY", "dummy-gemini-key")

    from brain.providers import gemini as gemini_module

    monkeypatch.setattr(
        gemini_module.genai, "Client", lambda **kwargs: object()
    )

    config = {
        "llm": {
            "model": "gemini-3.6-flash",
            "max_output_tokens": budget,
            "temperature": 0.7,
            "thinking_level": thinking_level,
        }
    }

    monkeypatch.setattr(gemini_module, "load_config", lambda: config)

    provider = gemini_module.GeminiProvider()

    models = _Models(response)
    provider.client = type("_Client", (), {"models": models})()

    return provider, models


def test_configured_thinking_level_reaches_the_request(monkeypatch):
    """The whole point: the model is told how much to think."""

    provider, models = _provider(monkeypatch, _Response("hello"))

    provider.generate("===== SYSTEM =====\nbe Aura\n\n===== CURRENT USER MESSAGE =====\nhi")

    config = models.calls[0]["config"]

    assert config["thinking_config"] == {"thinking_level": "low"}
    assert config["max_output_tokens"] == 768
    assert config["temperature"] == 0.7


def test_empty_thinking_level_sends_no_thinking_config(monkeypatch):
    """
    "" means "let the model decide", and must send nothing at all.

    A `thinking_config` of None is not the same request as no
    `thinking_config`, and a provider without the knob would reject it.
    """

    provider, models = _provider(monkeypatch, _Response("hello"), thinking_level="")

    provider.generate("===== CURRENT USER MESSAGE =====\nhi")

    assert "thinking_config" not in models.calls[0]["config"]


def test_streaming_sends_the_thinking_level_but_no_budget(monkeypatch):
    """
    Both paths agree on the thinking; only `generate` caps the length.

    Streaming has never sent `max_output_tokens` and must not start: the
    reply is already on the user's screen when a budget would run out.
    """

    provider, models = _provider(monkeypatch, _Response("hello"))

    list(provider.stream("===== CURRENT USER MESSAGE =====\nhi"))

    config = models.calls[0]["config"]

    assert config["thinking_config"] == {"thinking_level": "low"}
    assert "max_output_tokens" not in config
    assert "temperature" not in config


def test_truncated_reply_is_kept_but_logged(monkeypatch, caplog):
    """
    Half an answer beats none, and the user is reading an unfinished one.

    Returned rather than raised - the text is real - but a warning names
    the budget and the two settings that fix it, because from the outside
    this is indistinguishable from Aura simply answering badly.
    """

    provider, _ = _provider(
        monkeypatch,
        _Response("For a small app, SQLite, hands", reason="MAX_TOKENS", thoughts=686),
    )

    with caplog.at_level(logging.WARNING):
        reply = provider.generate("===== CURRENT USER MESSAGE =====\nsqlite or postgres?")

    assert reply == "For a small app, SQLite, hands"
    assert "truncated" in caplog.text
    assert "768" in caplog.text
    assert "686" in caplog.text
    assert "thinking_level" in caplog.text


def test_budget_spent_entirely_on_thinking_is_an_outage(monkeypatch):
    """
    No answer at all is a provider that failed, not a turn that succeeded.

    This is the case that made Aura look broken rather than slow: an
    empty string was saved as an assistant turn, published to the UI, and
    the fallback chain was never told there was anything to fall back
    from.
    """

    provider, _ = _provider(
        monkeypatch,
        _Response("", reason="MAX_TOKENS", thoughts=768),
    )

    with pytest.raises(ProviderUnavailableError) as raised:
        provider.generate("===== CURRENT USER MESSAGE =====\nexplain event loops")

    message = str(raised.value)

    assert "768" in message
    assert "thinking_level" in message or "max_output_tokens" in message


def test_an_empty_reply_that_finished_normally_is_still_a_reply(monkeypatch):
    """
    Only MAX_TOKENS is an outage. A blocked or empty STOP is unchanged.

    Gemini returns None for a blocked response, and that has always
    normalised to "" rather than raising. Widening the new rule to cover
    it would turn a safety block into a failover, and the next provider
    would be asked the same blocked question.
    """

    provider, _ = _provider(monkeypatch, _Response(None, reason="STOP"))

    assert provider.generate("===== CURRENT USER MESSAGE =====\nhi") == ""


def test_a_response_without_candidates_is_not_an_error(monkeypatch):
    """
    The stub SDK shape is not a contract. Absent metadata means silence.

    A response object that carries no candidates - a shape the SDK is
    free to return, and one every existing test double already uses -
    must take the ordinary path rather than crash the turn.
    """

    provider, _ = _provider(monkeypatch, _Response("fine"))

    provider.client.models._response.candidates = []

    assert provider.generate("===== CURRENT USER MESSAGE =====\nhi") == "fine"


def test_the_shipped_config_bounds_the_thinking(monkeypatch):
    """
    config.yaml and DEFAULT_CONFIG must both answer this, not just one.

    The defect was a missing setting, so a test that supplies its own
    config would pass on a tree where the fix was never shipped.
    """

    from core.config import DEFAULT_CONFIG, load_config

    assert DEFAULT_CONFIG["llm"]["thinking_level"] == "low"
    assert load_config()["llm"]["thinking_level"] == "low"
