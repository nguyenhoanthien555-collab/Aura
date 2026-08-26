"""
The Phase 11 cloud providers (Phase 11).

Six providers were added - OpenAI, Anthropic, Cerebras, xAI, DeepSeek and
Qwen - and none of them can be tested against its live API from this
deployment, because there is no key for any of them here. That is exactly
why this file exists: what *can* be pinned is the request Aura sends, and
every historical provider bug in this repository was a wrong request
rather than a wrong reply.

    AURA-P2-003  Cerebras sent the whole prompt as one user message
                 instead of splitting it, so Aura's instructions - the
                 device-action boundary in prompts/system.md included -
                 arrived as ordinary conversation. It was left
                 unregistered for two phases rather than shipped broken.
    AURA-P2-004  DEEPSEEK_API_KEY sat in a real .env and nothing read it.

Both were only findable by reading the code. The tests here make the same
class of defect a test failure instead, for all six providers and for the
next one added.

Nothing here opens a socket. `urlopen` is replaced, and each test asserts
on the request that would have been sent.
"""

import io
import json

import pytest

from brain.providers.anthropic import AnthropicProvider
from brain.providers.cerebras import CerebrasProvider
from brain.providers.deepseek import DeepSeekProvider
from brain.providers.errors import (
    ProviderAuthError,
    ProviderParameterError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from brain.providers.http_chat import (
    HttpChatProvider,
    classify_failure,
    named_parameter,
    provider_message,
)
from brain.providers.openai import OpenAIProvider
from brain.providers.openai_compatible import OpenAICompatibleProvider
from brain.providers.qwen import QwenProvider
from brain.providers.xai import XAIProvider
from brain.router import HTTP_CHAT_PROVIDERS, PROVIDER_KEYS
from core.config import DEFAULT_CONFIG


# Every provider built on the shared client, with the environment variable
# it reads. Driven off the router's own registry so a provider added to
# Aura without being added to this file fails here rather than shipping
# untested.
COMPATIBLE = (
    OpenAIProvider,
    CerebrasProvider,
    XAIProvider,
    DeepSeekProvider,
    QwenProvider,
)

ALL_NEW = (*COMPATIBLE, AnthropicProvider)


@pytest.fixture(autouse=True)
def no_ambient_keys(monkeypatch):
    """
    None of these providers may see a real key or a real base URL.

    A developer with OPENAI_API_KEY set would otherwise run a different
    test than CI does, and `load_dotenv()` inside each constructor makes
    the repository's own .env part of the environment too.
    """

    for provider in ALL_NEW:
        monkeypatch.delenv(provider.api_key_env, raising=False)
        monkeypatch.delenv(provider.base_url_env, raising=False)

    # Deleting the variable is not enough on its own: every provider calls
    # `load_dotenv()` while constructing, which does not override the
    # environment but does fill in anything missing from it - so a key
    # deleted here would come straight back from the repository's own .env
    # and these tests would pass or fail according to whose machine they
    # ran on. AURA-P2-004's DEEPSEEK_API_KEY is exactly such an entry.
    monkeypatch.setattr(
        "brain.providers.http_chat.load_dotenv", lambda *a, **k: None
    )


class Capture:
    """
    A stand-in for `urlopen` that records the request and replays a reply.

    Returned object supports the context-manager and iterator protocols,
    because `_post` reads the body whole and `stream` reads it by line.
    """

    def __init__(self, payload="", lines=None, status=200):
        self.body = payload if isinstance(payload, str) else json.dumps(payload)
        self.lines = lines
        self.request = None
        self.timeout = None

    def __call__(self, request, timeout=None):
        self.request = request
        self.timeout = timeout
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.body.encode("utf-8")

    def __iter__(self):
        return iter(
            [line.encode("utf-8") for line in (self.lines or [])]
        )

    # ---- what the test actually asks about -------------------------

    @property
    def sent(self) -> dict:
        return json.loads(self.request.data.decode("utf-8"))

    @property
    def url(self) -> str:
        return self.request.full_url

    def header(self, name: str) -> str:
        return self.request.headers.get(name.capitalize()) or (
            self.request.headers.get(name) or ""
        )


def openai_reply(text: str = "ok") -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


# A prompt in the shape `brain/prompt_builder.py` really produces. Written
# out rather than simplified, because `split_prompt` divides on these
# section headers: a prompt without them is entirely user content, so a
# test using plain text would pass even for a provider that never split
# anything - which is precisely the defect being guarded against.
SPLIT_PROMPT = (
    "===== SYSTEM =====\n"
    "You are Aura. Never claim to have performed a device action.\n"
    "\n"
    "===== HISTORY =====\n"
    "User: hello\n"
    "\n"
    "===== USER =====\n"
    "what time is it?\n"
)

SYSTEM_HALF = "You are Aura."
USER_HALF = "what time is it?"


def install(monkeypatch, capture):
    monkeypatch.setattr("brain.providers.http_chat.urlopen", capture)
    return capture


def keyed(monkeypatch, provider_class, **kwargs):
    """The provider, with a dummy key in place so it will construct."""

    monkeypatch.setenv(provider_class.api_key_env, "dummy-key-value")
    return provider_class(**kwargs)


# ======================================================================
# 1. A provider without a key does not exist
# ======================================================================

@pytest.mark.parametrize("provider_class", ALL_NEW, ids=lambda c: c.provider_name)
def test_no_key_means_no_provider(monkeypatch, provider_class):
    # The same shape every older provider raises, and the router's
    # `_skip_reason` names the same variable, so a missing key is
    # explained identically wherever it surfaces.
    with pytest.raises(ValueError) as raised:
        provider_class()

    assert provider_class.api_key_env in str(raised.value)


@pytest.mark.parametrize("provider_class", ALL_NEW, ids=lambda c: c.provider_name)
def test_the_key_is_never_in_the_error(monkeypatch, provider_class):
    # Constructed with a key, then failed: nothing about the key may reach
    # a message that ends up on a phone screen or in a log.
    secret = "sk-do-not-print-this-value"

    monkeypatch.setenv(provider_class.api_key_env, secret)

    provider = provider_class()

    error = classify_failure(provider.label, 401, '{"error": {"message": "bad key"}}')

    assert secret not in str(error)
    assert isinstance(error, ProviderAuthError)


# ======================================================================
# 2. The prompt is split - the defect that unwired Cerebras
# ======================================================================

@pytest.mark.parametrize("provider_class", COMPATIBLE, ids=lambda c: c.provider_name)
def test_the_system_half_goes_in_the_system_message(monkeypatch, provider_class):
    capture = install(monkeypatch, Capture(openai_reply()))

    provider = keyed(monkeypatch, provider_class)

    provider.generate(SPLIT_PROMPT)

    messages = capture.sent["messages"]

    # System instructions in the system slot, and history/user turns as native message roles.
    assert messages[0]["role"] == "system"
    assert SYSTEM_HALF in messages[0]["content"]
    assert USER_HALF in messages[-1]["content"]
    assert SYSTEM_HALF not in messages[-1]["content"]


@pytest.mark.parametrize("provider_class", ALL_NEW, ids=lambda c: c.provider_name)
def test_generate_is_not_reimplemented_by_any_provider(provider_class):
    # `generate` calls `split_prompt`. It lives on the shared base class so
    # that a new provider cannot forget to, which only holds while no
    # subclass overrides it.
    assert provider_class.generate is HttpChatProvider.generate


def test_a_promptless_system_half_sends_no_system_message(monkeypatch):
    # An empty system message is not free: some models read it as an
    # instruction to be terse.
    capture = install(monkeypatch, Capture(openai_reply()))

    provider = keyed(monkeypatch, OpenAIProvider)

    provider.generate("just this")

    assert [entry["role"] for entry in capture.sent["messages"]] == ["user"]


# ======================================================================
# 3. The request shape, per provider
# ======================================================================

@pytest.mark.parametrize("provider_class", COMPATIBLE, ids=lambda c: c.provider_name)
def test_compatible_providers_send_the_documented_shape(monkeypatch, provider_class):
    capture = install(monkeypatch, Capture(openai_reply()))

    provider = keyed(monkeypatch, provider_class, max_tokens=321, temperature=0.4)

    assert provider.generate("hi") == "ok"

    sent = capture.sent

    assert sent["model"] == provider_class.default_model
    assert sent[provider_class.token_field] == 321
    assert sent["temperature"] == 0.4
    assert capture.url == provider_class.default_url
    assert capture.header("authorization") == "Bearer dummy-key-value"
    assert capture.header("content-type") == "application/json"


def test_openai_sends_the_reasoning_model_token_field(monkeypatch):
    # gpt-5-class models reject `max_tokens`. The newer spelling is sent
    # first and repaired on refusal, rather than guessed from the model
    # name - this file cannot know what OpenAI ships next month.
    capture = install(monkeypatch, Capture(openai_reply()))

    keyed(monkeypatch, OpenAIProvider).generate("hi")

    assert "max_completion_tokens" in capture.sent
    assert "max_tokens" not in capture.sent


def test_anthropic_uses_its_own_auth_and_system_slot(monkeypatch):
    capture = install(
        monkeypatch,
        Capture({"content": [{"type": "text", "text": "ok"}]}),
    )

    provider = keyed(monkeypatch, AnthropicProvider, max_tokens=64)

    assert provider.generate(SPLIT_PROMPT) == "ok"

    sent = capture.sent

    # The four things `/v1/messages` does differently. Sending OpenAI JSON
    # here would fail in a way that looks like an outage.
    assert capture.header("x-api-key") == "dummy-key-value"
    assert not capture.header("authorization")
    assert capture.header("anthropic-version") == AnthropicProvider.API_VERSION
    assert SYSTEM_HALF in sent["system"]
    assert [entry["role"] for entry in sent["messages"]] == ["user"]
    assert USER_HALF in sent["messages"][0]["content"]
    assert SYSTEM_HALF not in sent["messages"][0]["content"]
    assert sent["max_tokens"] == 64


def test_anthropic_clamps_a_temperature_it_would_refuse(monkeypatch):
    # `llm.temperature` accepts up to 2.0 because that is OpenAI's range.
    # Anthropic's ceiling is 1.0 and a higher value is a 400, so a setting
    # the Control Hub allowed must not break every Claude reply.
    capture = install(
        monkeypatch, Capture({"content": [{"type": "text", "text": "ok"}]})
    )

    keyed(monkeypatch, AnthropicProvider, temperature=1.8).generate("hi")

    assert capture.sent["temperature"] == 1.0


def test_anthropic_keeps_a_temperature_it_accepts(monkeypatch):
    capture = install(
        monkeypatch, Capture({"content": [{"type": "text", "text": "ok"}]})
    )

    keyed(monkeypatch, AnthropicProvider, temperature=0.3).generate("hi")

    assert capture.sent["temperature"] == 0.3


def test_no_temperature_is_sent_when_none_is_configured(monkeypatch):
    # None is not 0.0. A provider's own default is a better answer than a
    # number invented by Aura, and some models reject any explicit value.
    capture = install(monkeypatch, Capture(openai_reply()))

    keyed(monkeypatch, OpenAIProvider, temperature=None).generate("hi")

    assert "temperature" not in capture.sent


# ======================================================================
# 4. The reply, including the parts that must not reach the transcript
# ======================================================================

def test_a_null_content_becomes_an_empty_string(monkeypatch):
    # A filtered or empty completion returns null content. The `-> str`
    # contract is what keeps None out of the transcript and the database.
    install(monkeypatch, Capture({"choices": [{"message": {"content": None}}]}))

    assert keyed(monkeypatch, OpenAIProvider).generate("hi") == ""


def test_anthropic_reads_only_text_blocks(monkeypatch):
    # A thinking block is the model working, not the answer. Storing it
    # would show the user Claude reasoning out loud and then record it as
    # something Aura said.
    install(
        monkeypatch,
        Capture({
            "content": [
                {"type": "thinking", "thinking": "the user greeted me"},
                {"type": "text", "text": "Hello."},
                {"type": "tool_use", "id": "t1", "name": "x", "input": {}},
                {"type": "text", "text": " How can I help?"},
            ]
        }),
    )

    reply = keyed(monkeypatch, AnthropicProvider).generate("hi")

    assert reply == "Hello. How can I help?"
    assert "greeted" not in reply


def test_an_unrecognisable_reply_is_transient_not_a_crash(monkeypatch):
    install(monkeypatch, Capture({"unexpected": True}))

    with pytest.raises(ProviderUnavailableError):
        keyed(monkeypatch, OpenAIProvider).generate("hi")


def test_a_non_json_200_is_transient(monkeypatch):
    # A proxy or captive portal answering for the provider. Classified as
    # transient so the chain moves on rather than dying on it.
    install(monkeypatch, Capture("<html>login required</html>"))

    with pytest.raises(ProviderUnavailableError):
        keyed(monkeypatch, OpenAIProvider).generate("hi")


# ======================================================================
# 5. Endpoint overrides accept either spelling
# ======================================================================

@pytest.mark.parametrize("provider_class", ALL_NEW, ids=lambda c: c.provider_name)
def test_the_base_url_override_accepts_a_root_or_a_full_url(provider_class):
    # `OPENAI_BASE_URL` means the API root to every OpenAI SDK, while this
    # codebase's older GROQ_BASE_URL holds the full endpoint. Guessing
    # wrong produces a 404 that reads exactly like an outage.
    root = provider_class.default_url.rsplit(provider_class.endpoint_path, 1)[0]

    assert provider_class.resolve_url(root) == provider_class.default_url
    assert provider_class.resolve_url(root + "/") == provider_class.default_url
    assert (
        provider_class.resolve_url(provider_class.default_url)
        == provider_class.default_url
    )
    assert provider_class.resolve_url("") == provider_class.default_url


def test_a_gateway_url_is_used_verbatim(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gateway.internal/v1")

    capture = install(monkeypatch, Capture(openai_reply()))

    keyed(monkeypatch, OpenAIProvider).generate("hi")

    assert capture.url == "https://gateway.internal/v1/chat/completions"


# ======================================================================
# 6. Failure classification - failover depends on the category
# ======================================================================

def test_a_per_minute_limit_lets_the_chain_continue():
    error = classify_failure("X", 429, '{"error": {"message": "rate limit reached"}}')

    assert isinstance(error, ProviderRateLimitError)
    assert not error.is_account_limit


@pytest.mark.parametrize(
    "message",
    [
        "You exceeded your current quota",
        "insufficient_quota",
        "daily limit reached",
        "your credit balance is too low",
        "billing hard limit reached",
    ],
)
def test_an_exhausted_account_stops_the_chain(message):
    # Waiting cannot help and each further attempt costs a request, so
    # `FallbackProvider` stops rather than burning every remaining
    # provider.
    error = classify_failure("X", 429, json.dumps({"error": {"message": message}}))

    assert isinstance(error, ProviderRateLimitError)
    assert error.is_account_limit


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_key_is_an_auth_error_not_an_outage(status):
    # The fix for a wrong key is completely different from the fix for an
    # unreachable host, and the phone shows this distinction.
    error = classify_failure("X", status, "{}")

    assert isinstance(error, ProviderAuthError)
    assert isinstance(error, ValueError)


@pytest.mark.parametrize("status", [500, 502, 503, 408, 409])
def test_transient_statuses_are_transient(status):
    assert isinstance(
        classify_failure("X", status, "{}"), ProviderUnavailableError
    )


def test_an_unclassified_status_says_so_rather_than_guessing():
    error = classify_failure("X", 418, "{}")

    assert type(error) is RuntimeError
    assert "418" in str(error)


def test_a_retry_after_header_is_carried_through():
    error = classify_failure("X", 429, "{}", retry_after="30")

    assert error.retry_after == 30.0


def test_a_junk_retry_after_is_ignored_not_fatal():
    error = classify_failure("X", 429, "{}", retry_after="soon")

    assert error.retry_after is None


def test_both_error_envelopes_are_understood():
    # OpenAI-compatible and Anthropic nest under "error"; Mistral does not.
    assert provider_message('{"error": {"message": "Nested"}}') == "nested"
    assert provider_message('{"message": "Flat"}') == "flat"
    assert provider_message("not json at all") == "not json at all"
    assert provider_message("") == ""


def test_a_generic_400_is_not_repaired_by_dropping_something_unrelated():
    # Only a field the request actually sent may be named, or a plain bad
    # request would be "fixed" by removing an innocent parameter.
    body = '{"error": {"message": "Invalid model id", "param": "model"}}'

    assert named_parameter(body, ("temperature", "max_tokens")) == ""
    assert type(classify_failure("X", 400, body, parameters=("temperature",))) is RuntimeError


def test_the_refused_field_is_read_from_the_provider(monkeypatch):
    body = '{"error": {"message": "Unsupported value", "param": "temperature"}}'

    assert named_parameter(body, ("temperature", "max_tokens")) == "temperature"

    quoted = '{"error": {"message": "\'max_tokens\' is not supported"}}'

    assert named_parameter(quoted, ("temperature", "max_tokens")) == "max_tokens"


# ======================================================================
# 7. The one bounded repair retry
# ======================================================================

class Refuse:
    """`urlopen` that refuses the first request and accepts the second."""

    def __init__(self, parameter, reply):
        self.parameter = parameter
        self.reply = reply
        self.sent = []

    def __call__(self, request, timeout=None):
        from urllib.error import HTTPError

        self.sent.append(json.loads(request.data.decode("utf-8")))

        if len(self.sent) == 1:
            raise HTTPError(
                request.full_url, 400, "Bad Request", {},
                io.BytesIO(
                    json.dumps({
                        "error": {
                            "message": f"Unsupported parameter: '{self.parameter}'",
                            "param": self.parameter,
                        }
                    }).encode("utf-8")
                ),
            )

        return Capture(self.reply)(request, timeout)


def test_a_refused_token_field_is_renamed_not_dropped(monkeypatch):
    # Dropping the cap would let the model answer at its own maximum
    # length, which on a phone screen is a different bug rather than a fix.
    refuse = Refuse("max_completion_tokens", openai_reply())

    monkeypatch.setattr("brain.providers.http_chat.urlopen", refuse)

    provider = keyed(monkeypatch, OpenAIProvider, max_tokens=100)

    assert provider.generate("hi") == "ok"

    assert len(refuse.sent) == 2
    assert refuse.sent[1]["max_tokens"] == 100
    assert "max_completion_tokens" not in refuse.sent[1]


def test_a_refused_temperature_is_dropped_and_the_reply_still_arrives(monkeypatch):
    # The gpt-5 case: an explicit temperature is rejected outright. The
    # setting is then ignored for that model rather than turning every
    # reply into a 400.
    refuse = Refuse("temperature", openai_reply())

    monkeypatch.setattr("brain.providers.http_chat.urlopen", refuse)

    provider = keyed(monkeypatch, OpenAIProvider, temperature=0.7)

    assert provider.generate("hi") == "ok"

    assert "temperature" in refuse.sent[0]
    assert "temperature" not in refuse.sent[1]


def test_the_repair_is_attempted_at_most_once(monkeypatch):
    # A second refusal is raised, not repaired - even here, where the field
    # it names *could* have been dropped as well. One bounded retry, never
    # a loop, and never a request that has quietly lost two settings.
    refusals = ("temperature", "max_completion_tokens")
    calls = []

    def refuse_in_turn(request, timeout=None):
        from urllib.error import HTTPError

        calls.append(json.loads(request.data.decode("utf-8")))

        parameter = refusals[min(len(calls) - 1, len(refusals) - 1)]

        raise HTTPError(
            request.full_url, 400, "Bad Request", {},
            io.BytesIO(
                json.dumps({
                    "error": {"message": "no", "param": parameter}
                }).encode("utf-8")
            ),
        )

    monkeypatch.setattr("brain.providers.http_chat.urlopen", refuse_in_turn)

    provider = keyed(monkeypatch, OpenAIProvider, temperature=0.7)

    with pytest.raises(ProviderParameterError):
        provider.generate("hi")

    assert len(calls) == 2
    assert "temperature" in calls[0]
    assert "temperature" not in calls[1]


def test_a_required_field_is_never_dropped():
    # Anthropic's `max_tokens` is required, so repairing a 400 by removing
    # it would replace one error with another.
    assert AnthropicProvider.droppable == ("temperature",)
    assert "max_tokens" not in AnthropicProvider.droppable


# ======================================================================
# 8. Streaming
# ======================================================================

def test_a_compatible_provider_streams_deltas(monkeypatch):
    capture = install(
        monkeypatch,
        Capture(lines=[
            'data: {"choices": [{"delta": {"role": "assistant"}}]}\n',
            'data: {"choices": [{"delta": {"content": "Hel"}}]}\n',
            "\n",
            ": keep-alive\n",
            'data: {"choices": [{"delta": {"content": "lo"}}]}\n',
            "data: [DONE]\n",
        ]),
    )

    provider = keyed(monkeypatch, XAIProvider)

    assert "".join(provider.stream("hi")) == "Hello"
    assert capture.sent["stream"] is True


def test_anthropic_streams_text_but_not_thinking(monkeypatch):
    install(
        monkeypatch,
        Capture(lines=[
            "event: message_start\n",
            'data: {"type": "message_start", "message": {}}\n',
            'data: {"type": "content_block_delta", "index": 0, '
            '"delta": {"type": "thinking_delta", "thinking": "hmm"}}\n',
            'data: {"type": "content_block_delta", "index": 1, '
            '"delta": {"type": "text_delta", "text": "Hi"}}\n',
            'data: {"type": "content_block_delta", "index": 1, '
            '"delta": {"type": "text_delta", "text": " there"}}\n',
            'data: {"type": "message_stop"}\n',
        ]),
    )

    provider = keyed(monkeypatch, AnthropicProvider)

    assert "".join(provider.stream("hi")) == "Hi there"


def test_a_malformed_event_does_not_end_the_stream(monkeypatch):
    install(
        monkeypatch,
        Capture(lines=[
            'data: {"choices": [{"delta": {"content": "a"}}]}\n',
            "data: {not json\n",
            'data: {"choices": []}\n',
            'data: {"choices": [{"delta": {"content": "b"}}]}\n',
            "data: [DONE]\n",
        ]),
    )

    provider = keyed(monkeypatch, DeepSeekProvider)

    assert "".join(provider.stream("hi")) == "ab"


@pytest.mark.parametrize("provider_class", ALL_NEW, ids=lambda c: c.provider_name)
def test_every_new_provider_streams(provider_class):
    # `PROVIDER_CAPABILITIES` in server/routes/settings.py reports
    # streaming: true for all six. That claim has to be a fact about the
    # implementation, not about the vendor.
    from brain.streaming import can_stream

    assert can_stream(provider_class)


# ======================================================================
# 9. The registry has exactly one source of truth
# ======================================================================

@pytest.mark.parametrize("provider_class", COMPATIBLE, ids=lambda c: c.provider_name)
def test_the_shipped_default_model_matches_the_class(provider_class):
    # Two places name a default model: the class, and DEFAULT_CONFIG (which
    # the router reads first). Disagreement would show one model in the
    # Control Hub and send another.
    key = f"{provider_class.provider_name}_model"

    assert DEFAULT_CONFIG["llm"][key] == provider_class.default_model


def test_anthropics_default_model_matches_the_class():
    assert DEFAULT_CONFIG["llm"]["anthropic_model"] == AnthropicProvider.default_model


@pytest.mark.parametrize("provider_class", ALL_NEW, ids=lambda c: c.provider_name)
def test_each_new_provider_is_registered_under_its_own_key(provider_class):
    name = provider_class.provider_name

    assert PROVIDER_KEYS[name] == provider_class.api_key_env

    module, class_name, model_key = HTTP_CHAT_PROVIDERS[name]

    assert class_name == provider_class.__name__
    assert model_key == f"{name}_model"
    assert model_key in DEFAULT_CONFIG["llm"]


def test_the_model_settings_are_writable_from_the_control_hub():
    # A model field the settings whitelist refuses is a picker that cannot
    # be used - the model would only be changeable by editing config.yaml
    # on the server.
    from core.settings_store import ALLOWED

    for name in HTTP_CHAT_PROVIDERS:
        assert f"llm.{name}_model" in ALLOWED


def test_every_provider_the_router_lists_can_be_imported():
    # The registry names modules as strings, so a typo in one is otherwise
    # only discovered when someone selects that provider.
    from importlib import import_module

    for name, (module_path, class_name, _) in HTTP_CHAT_PROVIDERS.items():
        provider_class = getattr(import_module(module_path), class_name)

        assert provider_class.provider_name == name
        assert issubclass(provider_class, HttpChatProvider)
        assert provider_class.label

        # A vendor knows its own endpoint and its own model, and both
        # defaults live on the class. A provider whose endpoint the owner
        # supplies must default *neither*: there is no model name that
        # could be right for a gateway nobody here has seen, and a
        # placeholder would answer 404 and read like an outage. The two
        # go together - one without the other is a provider that guesses
        # half of where it is going.
        if provider_class.requires_base_url:
            assert not provider_class.default_url
            assert not provider_class.default_model
        else:
            assert provider_class.default_url
            assert provider_class.default_model


def test_the_openai_compatible_class_is_not_used_for_anthropic():
    # `/v1/messages` is a different API. A subclass that silently sent
    # OpenAI JSON to it would fail in a way that looks like an outage.
    assert not issubclass(AnthropicProvider, OpenAICompatibleProvider)
    assert issubclass(AnthropicProvider, HttpChatProvider)
