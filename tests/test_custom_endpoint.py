"""
The owner's own endpoint: a key and a URL that travel together.

Aura's providers were all vendors. Each one had a name, a hard-coded URL
and a key variable, and the three were welded together in a file. That is
correct for Anthropic and useless for a gateway nobody here has seen -
an AgentRouter deployment, a company proxy, vLLM on the owner's own
machine, LiteLLM in front of five of them.

So there is now a provider whose endpoint is not knowable in advance, and
this file pins the two properties that make that safe rather than
dangerous:

  * A key and an endpoint are one fact, not two. The single worst outcome
    available to a provider layer is sending the owner's custom-gateway key
    to Anthropic because the *model name* looked like a Claude model. The
    tests below make that a structural impossibility rather than a rule
    somebody has to remember.

  * Nothing is invented. There is no default URL, because there is no
    such thing as a default custom endpoint, and no default model, for the
    same reason. An absent one is a missing precondition explained by
    name - never a guess, and never a POST to "".

Nothing here opens a socket.
"""

import json

import pytest

from brain.providers.custom import CustomProvider
from brain.providers.http_chat import HttpChatProvider
from brain.providers.openai_compatible import OpenAICompatibleProvider
from brain.router import (
    HTTP_CHAT_PROVIDERS,
    OWNER_DEFINED_ENDPOINTS,
    PROVIDER_KEYS,
    BrainRouter,
)
from core.config import DEFAULT_CONFIG
from core.settings_store import ALLOWED, SettingsError


GATEWAY = "https://gateway.example.test/v1"


@pytest.fixture(autouse=True)
def no_ambient_config(monkeypatch):
    """
    No key, no endpoint, and no .env filling either back in.

    `load_dotenv()` runs inside every constructor and does not override
    the environment but does complete it, so deleting a variable is not
    enough on its own - the repository's own .env would supply it again
    and these tests would pass according to whose machine they ran on.
    """

    for variable in (
        "CUSTOM_API_KEY", "CUSTOM_BASE_URL",
        "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL",
    ):
        monkeypatch.delenv(variable, raising=False)

    monkeypatch.setattr(
        "brain.providers.http_chat.load_dotenv", lambda *a, **k: None
    )


class Capture:
    """`urlopen`, recorded. Same shape as tests/test_cloud_providers.py."""

    def __init__(self, payload=None):
        self.body = json.dumps(
            payload or {"choices": [{"message": {"content": "ok"}}]}
        )
        self.request = None

    def __call__(self, request, timeout=None):
        self.request = request
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.body.encode("utf-8")

    @property
    def url(self) -> str:
        return self.request.full_url

    @property
    def sent(self) -> dict:
        return json.loads(self.request.data.decode("utf-8"))

    def header(self, name: str) -> str:
        return self.request.headers.get(name.capitalize()) or ""


def install(monkeypatch, capture=None):
    capture = capture or Capture()
    monkeypatch.setattr("brain.providers.http_chat.urlopen", capture)
    return capture


def configured(monkeypatch, key="dummy-custom-key", url=GATEWAY):
    if key is not None:
        monkeypatch.setenv("CUSTOM_API_KEY", key)
    if url is not None:
        monkeypatch.setenv("CUSTOM_BASE_URL", url)


# ======================================================================
# 1. Nothing is invented
# ======================================================================

class TestNothingIsInvented:

    def test_there_is_no_default_endpoint(self):
        # The failure mode this prevents: a placeholder URL that resolves,
        # answers 404, and reads to the owner exactly like an outage at a
        # provider they never configured.
        assert CustomProvider.default_url == ""
        assert CustomProvider.default_model == ""

    def test_the_endpoint_is_declared_as_required(self):
        assert CustomProvider.requires_base_url is True

    def test_a_vendor_provider_still_has_its_own_endpoint(self):
        # The flag is opt-in. Every existing provider knows where it goes
        # and must keep going there with no extra configuration.
        from brain.providers.deepseek import DeepSeekProvider

        assert DeepSeekProvider.requires_base_url is False
        assert DeepSeekProvider.default_url

    def test_no_endpoint_means_no_provider_rather_than_a_post_to_nowhere(
        self, monkeypatch
    ):
        monkeypatch.setenv("CUSTOM_API_KEY", "dummy-custom-key")

        with pytest.raises(ValueError) as raised:
            CustomProvider(model="some-model")

        # Actionable: it names what to set, in both spellings the owner
        # has available to them.
        message = str(raised.value)
        assert "CUSTOM_BASE_URL" in message
        assert "llm.custom_base_url" in message

    def test_no_key_means_no_provider(self, monkeypatch):
        monkeypatch.setenv("CUSTOM_BASE_URL", GATEWAY)

        with pytest.raises(ValueError) as raised:
            CustomProvider(model="some-model")

        assert "CUSTOM_API_KEY" in str(raised.value)


# ======================================================================
# 2. The key and the endpoint are one fact
# ======================================================================

class TestTheKeyNeverLeavesItsEndpoint:

    def test_the_custom_key_goes_only_to_the_custom_url(self, monkeypatch):
        configured(monkeypatch, key="sk-owner-gateway-secret")
        capture = install(monkeypatch)

        CustomProvider(model="some-model").generate("===== USER =====\nhi\n")

        assert capture.url.startswith("https://gateway.example.test")
        assert capture.header("authorization") == "Bearer sk-owner-gateway-secret"

    def test_a_claude_shaped_model_name_does_not_redirect_the_request(
        self, monkeypatch
    ):
        """
        The specific catastrophe this provider could have caused.

        An owner points a gateway at Anthropic and names the model
        `claude-sonnet-5`. Nothing anywhere may read that name and decide
        the request belongs at api.anthropic.com with Anthropic's key -
        the endpoint comes from the endpoint setting and from nowhere
        else.
        """
        configured(monkeypatch, key="sk-owner-gateway-secret")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-a-different-secret")
        capture = install(monkeypatch)

        CustomProvider(model="claude-sonnet-5").generate("===== USER =====\nhi\n")

        assert "anthropic.com" not in capture.url
        assert "sk-ant-a-different-secret" not in capture.header("authorization")
        assert capture.sent["model"] == "claude-sonnet-5"

    def test_anthropic_is_unaffected_by_a_custom_key_being_present(
        self, monkeypatch
    ):
        # And the converse. Configuring a custom endpoint must not change
        # where a real vendor provider sends anything.
        from brain.providers.anthropic import AnthropicProvider

        configured(monkeypatch, key="sk-owner-gateway-secret")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-a-different-secret")
        capture = install(
            monkeypatch,
            Capture({"content": [{"type": "text", "text": "ok"}]}),
        )

        AnthropicProvider().generate("===== USER =====\nhi\n")

        assert "gateway.example.test" not in capture.url
        assert "sk-owner-gateway-secret" not in json.dumps(dict(
            capture.request.headers
        ))

    def test_the_key_is_never_in_the_error(self, monkeypatch):
        import urllib.error

        secret = "sk-do-not-print-this-value"
        configured(monkeypatch, key=secret)

        def refuse(request, timeout=None):
            raise urllib.error.HTTPError(
                GATEWAY, 401, "Unauthorized", {}, None
            )

        monkeypatch.setattr("brain.providers.http_chat.urlopen", refuse)

        with pytest.raises(Exception) as raised:
            CustomProvider(model="some-model").generate("hi")

        assert secret not in str(raised.value)
        assert secret not in repr(raised.value)


# ======================================================================
# 3. Where the endpoint comes from
# ======================================================================

class TestEndpointResolution:

    def test_a_root_gains_the_chat_completions_path(self, monkeypatch):
        configured(monkeypatch, url="https://gateway.example.test/v1")
        capture = install(monkeypatch)

        CustomProvider(model="m").generate("hi")

        assert capture.url == "https://gateway.example.test/v1/chat/completions"

    def test_a_full_url_is_left_alone(self, monkeypatch):
        configured(
            monkeypatch,
            url="https://gateway.example.test/v1/chat/completions",
        )
        capture = install(monkeypatch)

        CustomProvider(model="m").generate("hi")

        assert capture.url == "https://gateway.example.test/v1/chat/completions"
        assert capture.url.count("/chat/completions") == 1

    def test_a_trailing_slash_does_not_double_the_path(self, monkeypatch):
        configured(monkeypatch, url="https://gateway.example.test/v1/")
        capture = install(monkeypatch)

        CustomProvider(model="m").generate("hi")

        assert capture.url == "https://gateway.example.test/v1/chat/completions"

    def test_the_owners_setting_reaches_the_provider(self, monkeypatch):
        # The phone writes `llm.custom_base_url`. A provider that read only
        # an environment variable would be configurable from a shell on the
        # server and from nowhere else, which is the position section 39
        # exists to forbid.
        monkeypatch.setenv("CUSTOM_API_KEY", "dummy-custom-key")
        capture = install(monkeypatch)

        CustomProvider(
            model="m", base_url="https://from-settings.example.test/v1"
        ).generate("hi")

        assert capture.url.startswith("https://from-settings.example.test")

    def test_the_owners_setting_wins_over_the_environment(self, monkeypatch):
        # Both are the owner's, but the setting is the one they can see and
        # change from the app, so it is the more recent statement of intent.
        configured(monkeypatch, url="https://from-env.example.test/v1")
        capture = install(monkeypatch)

        CustomProvider(
            model="m", base_url="https://from-settings.example.test/v1"
        ).generate("hi")

        assert capture.url.startswith("https://from-settings.example.test")

    def test_an_empty_setting_falls_back_to_the_environment(self, monkeypatch):
        # A blank setting is "not configured here", not "configured to
        # nothing" - otherwise saving any other setting would break a
        # deployment whose endpoint came from its environment.
        configured(monkeypatch, url="https://from-env.example.test/v1")
        capture = install(monkeypatch)

        CustomProvider(model="m", base_url="").generate("hi")

        assert capture.url.startswith("https://from-env.example.test")


# ======================================================================
# 4. It speaks the dialect it claims to
# ======================================================================

class TestDialect:

    def test_it_is_the_shared_openai_client_and_not_a_copy_of_one(self):
        assert issubclass(CustomProvider, OpenAICompatibleProvider)
        assert issubclass(CustomProvider, HttpChatProvider)

    def test_the_system_half_of_the_prompt_stays_a_system_message(
        self, monkeypatch
    ):
        # AURA-P2-003: a provider that sends the whole prompt as one user
        # message delivers Aura's own instructions as conversation.
        configured(monkeypatch)
        capture = install(monkeypatch)

        CustomProvider(model="m").generate(
            "===== SYSTEM =====\nYou are Aura.\n"
            "\n"
            "===== USER =====\nwhat time is it?\n"
        )

        roles = [message["role"] for message in capture.sent["messages"]]
        assert roles[0] == "system"
        assert "You are Aura." in capture.sent["messages"][0]["content"]
        assert roles[-1] == "user"

    def test_it_can_stream(self, monkeypatch):
        from brain.streaming import can_stream

        configured(monkeypatch)

        assert can_stream(CustomProvider(model="m"))


# ======================================================================
# 5. The router builds it, and explains it when it cannot
# ======================================================================

class TestRouterIntegration:

    def test_it_is_registered_under_its_own_key(self):
        assert PROVIDER_KEYS["custom"] == "CUSTOM_API_KEY"

        module, class_name, model_key = HTTP_CHAT_PROVIDERS["custom"]

        assert class_name == "CustomProvider"
        assert model_key == "custom_model"

    def test_it_is_declared_as_owner_defined(self):
        assert OWNER_DEFINED_ENDPOINTS["custom"] == "CUSTOM_BASE_URL"

    def test_the_router_builds_it_from_the_owners_settings(self, monkeypatch):
        monkeypatch.setenv("CUSTOM_API_KEY", "dummy-custom-key")

        llm = dict(
            DEFAULT_CONFIG["llm"],
            custom_base_url=GATEWAY,
            custom_model="some-model",
        )

        provider = BrainRouter._instantiate_provider("custom", llm)

        assert provider is not None
        assert provider.provider_name == "custom"
        assert provider.model == "some-model"

    def test_a_missing_endpoint_is_a_skipped_provider_not_a_crash(
        self, monkeypatch
    ):
        # `None` from `_instantiate_provider` is the established way to say
        # "a precondition is absent", and it is what `_skip_reason`
        # explains. An exception here would be reported to the owner as
        # "initialization raised ValueError", which names nothing.
        monkeypatch.setenv("CUSTOM_API_KEY", "dummy-custom-key")

        llm = dict(DEFAULT_CONFIG["llm"], custom_model="some-model")

        assert BrainRouter._instantiate_provider("custom", llm) is None

        reason = BrainRouter._skip_reason("custom")
        assert "CUSTOM_BASE_URL" in reason
        assert "unknown provider" not in reason

    def test_a_missing_model_is_also_named(self, monkeypatch):
        monkeypatch.setenv("CUSTOM_API_KEY", "dummy-custom-key")
        monkeypatch.setenv("CUSTOM_BASE_URL", GATEWAY)

        llm = dict(DEFAULT_CONFIG["llm"], custom_model="")

        assert BrainRouter._instantiate_provider("custom", llm) is None
        assert "llm.custom_model" in BrainRouter._skip_reason("custom")

    def test_a_missing_key_is_still_the_first_thing_reported(self, monkeypatch):
        # Order matters for a useful message: with nothing configured at
        # all, the key is the thing to say, because it is the thing the
        # owner has to obtain from somewhere else.
        assert BrainRouter._skip_reason("custom") == "CUSTOM_API_KEY is not set"

    def test_it_can_serve_as_a_fallback(self, monkeypatch):
        monkeypatch.setenv("CUSTOM_API_KEY", "dummy-custom-key")
        monkeypatch.setenv("CUSTOM_BASE_URL", GATEWAY)
        monkeypatch.setenv("GEMINI_API_KEY", "dummy-gemini-key")

        llm = dict(
            DEFAULT_CONFIG["llm"],
            provider="gemini",
            fallback_providers=["custom"],
            custom_model="some-model",
        )
        monkeypatch.setattr("brain.router.load_config", lambda: {"llm": llm})

        router = BrainRouter(provider_name="gemini")

        assert router.active_chain() == "gemini->custom"


# ======================================================================
# 6. The owner can configure all of it from the app
# ======================================================================

class TestOwnerConfiguration:

    def test_the_endpoint_and_model_are_both_settable(self):
        assert "llm.custom_base_url" in ALLOWED
        assert "llm.custom_model" in ALLOWED

    def test_custom_is_selectable_as_the_provider(self):
        assert ALLOWED["llm.provider"]("custom", "llm.provider") == "custom"

    def test_the_defaults_are_empty_rather_than_guessed(self):
        assert DEFAULT_CONFIG["llm"]["custom_base_url"] == ""
        assert DEFAULT_CONFIG["llm"]["custom_model"] == ""

    def test_an_endpoint_can_be_cleared(self):
        # Retiring a custom endpoint has to be expressible, or an owner
        # who tries one can never go back to a vendor cleanly.
        assert ALLOWED["llm.custom_base_url"]("", "llm.custom_base_url") == ""

    def test_a_plain_hostname_is_refused(self):
        # Silently prepending a scheme would decide between http and https
        # on the owner's behalf, and getting that wrong sends their key
        # over the wire in cleartext.
        with pytest.raises(SettingsError):
            ALLOWED["llm.custom_base_url"](
                "gateway.example.test/v1", "llm.custom_base_url"
            )

    def test_a_non_http_scheme_is_refused(self):
        for bad in ("file:///etc/passwd", "ftp://gateway.example.test"):
            with pytest.raises(SettingsError):
                ALLOWED["llm.custom_base_url"](bad, "llm.custom_base_url")

    def test_a_localhost_endpoint_is_accepted(self):
        # The owner's own machine is a first-class case: vLLM, llama.cpp,
        # LM Studio and LiteLLM all live there, and plain http is correct
        # for a loopback address.
        assert ALLOWED["llm.custom_base_url"](
            "http://127.0.0.1:8000/v1", "llm.custom_base_url"
        ) == "http://127.0.0.1:8000/v1"

    def test_the_phone_is_told_which_variable_holds_the_endpoint(self):
        from server.routes.settings import (
            PROVIDER_BASE_URL_ENV, PROVIDER_CAPABILITIES,
        )

        assert PROVIDER_BASE_URL_ENV["custom"] == "CUSTOM_BASE_URL"

        row = PROVIDER_CAPABILITIES["custom"]

        assert row["api_key_env"] == "CUSTOM_API_KEY"
        assert row["model_setting"] == "llm.custom_model"
        # No vendor to claim a base for, and no model list to offer: the
        # UI must ask rather than present a choice that may not exist.
        assert row["api_base"] == ""
        assert row["models"] == []
