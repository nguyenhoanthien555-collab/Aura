"""
Provider resolution and failover (Phase 5).

One question this file exists to answer, permanently: *which provider does
Aura use, in what order, and what happens when one fails?* Before this
phase the honest answer was "it depends, and the configuration lies about
it in three separate ways".

    AURA-P1-009  `provider: ollama` returned early from _create_provider
                 and never built a chain, so the local provider - the one
                 most likely to be unreachable - was the only one with no
                 failover at all.
    AURA-P1-010  OLLAMA_HOST was documented in docs/DEPLOYMENT.md and
                 .env.example and read by nothing.
    AURA-P2-009  The Ollama model was inferred from `llm.model` by testing
                 `startswith("gemini")`, so any non-Gemini primary model
                 name was passed to Ollama as if it were a model tag.
    AURA-P2-010  `fallback_provider` (singular) was read only when
                 `fallback_providers` was None - but that key is in
                 DEFAULT_CONFIG, so after the deep merge it is never None.
                 An operator who wrote only the singular form got no
                 failover and no warning.

The tests are grouped by the promise they keep rather than by the defect
number, because the promises are what has to survive Phase 6.
"""

import copy
import logging

import pytest

from brain.router import KEYLESS_PROVIDERS, PROVIDER_KEYS, BrainRouter
from brain.providers.errors import ProviderRateLimitError, ProviderUnavailableError
from brain.providers.fallback import ACCOUNT_LIMIT, FallbackProvider, _category_of
from brain.providers.ollama import OllamaProvider

from core.config import DEFAULT_CONFIG


PROVIDER_ENV = (
    # Derived, not written out: the hand-written list was missing every
    # variable added after it, so a developer with one of those keys in
    # their own environment silently changed what these tests resolved.
    *PROVIDER_KEYS.values(),
    *(f"{name.upper()}_BASE_URL" for name in PROVIDER_KEYS),
    "OLLAMA_HOST",
)


@pytest.fixture(autouse=True)
def no_ambient_provider_config(monkeypatch):
    """
    No test here may depend on the developer's own keys or host.

    Without this the suite passes or fails according to what happens to be
    in the environment, which is the opposite of what these tests are for.
    """

    for variable in PROVIDER_ENV:
        monkeypatch.delenv(variable, raising=False)


def config_with(**llm):
    """A full merged config, as the router really receives it.

    Deliberately built from DEFAULT_CONFIG rather than from a bare dict:
    the P2-010 bug was *caused* by the deep merge supplying keys, and a
    test that hand-rolls a two-key dict cannot see it.
    """

    merged = copy.deepcopy(DEFAULT_CONFIG)
    merged["llm"].update(llm)
    return merged


def router_with(monkeypatch, provider_name, **llm):
    monkeypatch.setattr("brain.router.load_config", lambda: config_with(**llm))
    return BrainRouter(provider_name=provider_name)


@pytest.fixture
def fake_cloud(monkeypatch):
    """
    Every cloud provider present and answering with its own name.

    Patches `generate` rather than the classes, so the router's real
    instantiation path - key check, model selection, constructor - runs.
    """

    from brain.providers.gemini import GeminiProvider
    from brain.providers.groq import GroqProvider
    from brain.providers.mistral import MistralProvider
    from brain.providers.openrouter import OpenRouterProvider

    for variable in ("GEMINI_API_KEY", "GROQ_API_KEY", "MISTRAL_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.setenv(variable, f"dummy-{variable.lower()}")

    for cls, name in (
        (GeminiProvider, "gemini"),
        (GroqProvider, "groq"),
        (MistralProvider, "mistral"),
        (OpenRouterProvider, "openrouter"),
    ):
        monkeypatch.setattr(cls, "generate", lambda self, prompt, _n=name: f"{_n} reply")

    monkeypatch.setattr(OllamaProvider, "generate", lambda self, prompt: "ollama reply")


# ======================================================================
# 1. Ollama is a provider like any other (AURA-P1-009)
# ======================================================================

def test_ollama_as_primary_builds_a_real_fallback_chain(monkeypatch, fake_cloud):
    # The defect: _create_provider returned OllamaProvider() before the
    # chain builder was reached, so this used to be a bare OllamaProvider
    # no matter what fallback_providers said.
    router = router_with(
        monkeypatch, "ollama",
        provider="ollama", fallback_providers=["gemini", "groq"],
    )

    assert isinstance(router.provider, FallbackProvider)
    assert router.active_chain() == "ollama->gemini->groq"


def test_ollama_can_be_a_fallback_member(monkeypatch, fake_cloud):
    # The other half of the same defect: _instantiate_provider had no
    # "ollama" branch, so naming it as a fallback silently dropped it.
    router = router_with(
        monkeypatch, "gemini",
        provider="gemini", fallback_providers=["ollama"],
    )

    assert router.active_chain() == "gemini->ollama"


def test_an_unreachable_ollama_falls_through_to_the_cloud(monkeypatch, fake_cloud):
    # The scenario the whole phase is for: the local box is off, and Aura
    # keeps answering instead of failing.
    monkeypatch.setattr(
        OllamaProvider, "generate",
        lambda self, prompt: (_ for _ in ()).throw(
            ProviderUnavailableError("Ollama is unreachable")
        ),
    )

    router = router_with(
        monkeypatch, "ollama",
        provider="ollama", fallback_providers=["gemini"],
    )

    assert router.generate("hello") == "gemini reply"


def test_ollama_needs_no_key_and_is_never_skipped_for_a_missing_one(monkeypatch, fake_cloud):
    # Ollama is keyless, so the key-based skip logic must not apply to it.
    assert "ollama" in KEYLESS_PROVIDERS
    assert "ollama" not in PROVIDER_KEYS

    reason = BrainRouter._skip_reason("ollama")

    assert "API key" in reason
    assert "not set" not in reason


def test_an_unreachable_ollama_is_classified_as_transient_not_unknown():
    # A bare RuntimeError reads as "unclassified provider error" to the
    # failover layer, which is the wrong description of the most ordinary
    # failure a local provider has.
    import urllib.error

    provider = OllamaProvider(host="http://127.0.0.1:9", model="qwen3:8b", timeout=0.2)

    with pytest.raises(ProviderUnavailableError) as raised:
        provider.generate("hello")

    assert _category_of(raised.value) == "transient/unavailable"


# ======================================================================
# 2. The Ollama host is configurable (AURA-P1-010)
# ======================================================================

def test_the_default_host_is_loopback(monkeypatch):
    monkeypatch.setattr("brain.providers.ollama.load_config", lambda: config_with())

    assert OllamaProvider().host == "http://127.0.0.1:11434"


def test_ollama_host_environment_variable_is_honoured(monkeypatch):
    # docs/DEPLOYMENT.md has documented this variable for four sections;
    # until this phase nothing read it.
    monkeypatch.setattr("brain.providers.ollama.load_config", lambda: config_with())
    monkeypatch.setenv("OLLAMA_HOST", "http://ollama.internal:11434")

    assert OllamaProvider().host == "http://ollama.internal:11434"


def test_an_explicit_host_beats_config_which_beats_the_environment(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://from-env:11434")
    monkeypatch.setattr(
        "brain.providers.ollama.load_config",
        lambda: config_with(host="http://from-config:11434"),
    )

    assert OllamaProvider().host == "http://from-config:11434"
    assert OllamaProvider(host="http://explicit:11434").host == "http://explicit:11434"


def test_a_trailing_slash_does_not_produce_a_double_slash_url(monkeypatch):
    monkeypatch.setattr("brain.providers.ollama.load_config", lambda: config_with())
    monkeypatch.setenv("OLLAMA_HOST", "http://ollama.internal:11434/")

    assert OllamaProvider().host == "http://ollama.internal:11434"


# ======================================================================
# 3. The Ollama model is configured, not inferred (AURA-P2-009)
# ======================================================================

@pytest.mark.parametrize("primary_model", [
    "gemini-3.6-flash",
    "claude-opus-5",
    "llama3",
    "",
])
def test_the_primary_model_name_never_leaks_into_ollama(monkeypatch, primary_model):
    # The old rule was `startswith("gemini")`, so every model name that
    # was not Gemini's - including another vendor's - was handed to Ollama
    # as if it were a local tag.
    monkeypatch.setattr(
        "brain.providers.ollama.load_config",
        lambda: config_with(model=primary_model),
    )

    assert OllamaProvider().model == "qwen3:8b"


def test_ollama_model_is_read_from_its_own_setting(monkeypatch):
    monkeypatch.setattr(
        "brain.providers.ollama.load_config",
        lambda: config_with(model="gemini-3.6-flash", ollama_model="mistral:7b"),
    )

    assert OllamaProvider().model == "mistral:7b"


def test_ollama_has_a_dedicated_setting_like_its_peers():
    # groq_model and mistral_model already existed; the absence of an
    # ollama_model is what forced the string hack in the first place.
    for setting in ("groq_model", "mistral_model", "ollama_model"):
        assert setting in DEFAULT_CONFIG["llm"], setting


# ======================================================================
# 4. One authoritative fallback setting (AURA-P2-010)
# ======================================================================

def test_fallback_providers_is_authoritative(monkeypatch, fake_cloud):
    router = router_with(
        monkeypatch, "gemini",
        provider="gemini", fallback_providers=["groq", "mistral"],
    )

    assert router.active_chain() == "gemini->groq->mistral"


def test_the_legacy_singular_key_still_produces_a_chain(monkeypatch, fake_cloud, caplog):
    # This is the bug. `fallback_providers` is in DEFAULT_CONFIG, so after
    # the deep merge it is present-but-empty and the old `is None` test
    # never fired: an operator who wrote only the singular form got a bare
    # primary and no warning.
    router = router_with(
        monkeypatch, "gemini",
        provider="gemini", fallback_provider="openrouter",
    )

    with caplog.at_level(logging.DEBUG, logger="Aura"):
        chain = router.active_chain()

    assert chain == "gemini->openrouter"
    assert "superseded" in caplog.text


def test_the_list_wins_when_both_keys_are_set_and_the_loser_is_named(monkeypatch, fake_cloud, caplog):
    router = router_with(
        monkeypatch, "gemini",
        provider="gemini",
        fallback_provider="openrouter",
        fallback_providers=["groq"],
    )

    with caplog.at_level(logging.DEBUG, logger="Aura"):
        chain = router.active_chain()

    assert chain == "gemini->groq"
    # Dropping a configured provider silently is the thing being fixed;
    # doing it while fixing it would be its own joke.
    assert "openrouter" in caplog.text
    assert "ignored" in caplog.text


def test_fallback_model_is_not_a_provider_name(monkeypatch, fake_cloud):
    # `fallback_model` names OpenRouter's model. It sits next to two keys
    # that name providers and must not be confused with them - it is the
    # one of the three that was never dead.
    router = router_with(
        monkeypatch, "gemini",
        provider="gemini",
        fallback_providers=["openrouter"],
        fallback_model="openrouter/free",
    )

    assert router.active_chain() == "gemini->openrouter"

    openrouter = router.provider.providers[-1]

    assert openrouter.model == "openrouter/free"


def test_a_wrongly_shaped_fallback_list_is_ignored_with_a_warning(monkeypatch, fake_cloud, caplog):
    router = router_with(
        monkeypatch, "gemini",
        provider="gemini", fallback_providers="groq",
    )

    with caplog.at_level(logging.DEBUG, logger="Aura"):
        chain = router.active_chain()

    # A string is iterable, so the old code would have tried to build
    # providers named "g", "r", "o", "q".
    assert chain == "gemini"
    assert "must be a list" in caplog.text


def test_the_shipped_config_states_the_chain_once():
    # config.yaml is committed, so this pins the file an operator reads.
    from core.config import load_config

    llm = (load_config() or {}).get("llm") or {}

    assert llm.get("fallback_providers") == ["groq", "mistral", "openrouter"]
    assert not llm.get("fallback_provider")


# ======================================================================
# 5. The chain that exists is observable (AURA-P1-012)
# ======================================================================

def test_health_reports_the_chain_that_was_built_not_the_one_configured(monkeypatch, fake_cloud):
    # `provider_name` is what was asked for and stays that way. Reporting
    # it as the runtime state made a collapsed chain indistinguishable
    # from a working one over HTTP.
    router = router_with(
        monkeypatch, "gemini",
        provider="gemini", fallback_providers=["groq"],
    )

    assert router.provider_name == "gemini"
    assert router.active_chain() == "gemini->groq"


def test_a_chain_with_no_surviving_fallback_says_so(monkeypatch, caplog):
    from brain.providers.gemini import GeminiProvider

    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    monkeypatch.setattr(GeminiProvider, "generate", lambda self, prompt: "gemini reply")

    router = router_with(
        monkeypatch, "gemini",
        provider="gemini", fallback_providers=["groq", "mistral"],
    )

    with caplog.at_level(logging.DEBUG, logger="Aura"):
        chain = router.active_chain()

    assert chain == "gemini"
    assert "no failover" in caplog.text


def test_a_fallback_that_raises_does_not_stop_the_server_booting(monkeypatch, caplog):
    # Every cloud provider validates its key in __init__ and raises. A
    # fallback is optional by definition, so one that raises must be
    # reported and skipped - turning it into a boot failure would make an
    # optional provider mandatory.
    from brain.providers.gemini import GeminiProvider
    from brain.providers.groq import GroqProvider

    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    monkeypatch.setenv("GROQ_API_KEY", "dummy")
    monkeypatch.setattr(GeminiProvider, "generate", lambda self, prompt: "gemini reply")
    monkeypatch.setattr(
        GroqProvider, "__init__",
        lambda self, **kwargs: (_ for _ in ()).throw(ValueError("boom")),
    )

    router = router_with(
        monkeypatch, "gemini",
        provider="gemini", fallback_providers=["groq"],
    )

    with caplog.at_level(logging.DEBUG, logger="Aura"):
        chain = router.active_chain()

    assert chain == "gemini"
    assert "initialization raised ValueError" in caplog.text


def test_a_missing_primary_key_is_still_a_hard_failure(monkeypatch):
    # The one case that must NOT degrade quietly: the explicitly selected
    # primary cannot be built, so there is nothing to be resilient with.
    router = router_with(monkeypatch, "gemini", provider="gemini")

    with pytest.raises(ValueError, match="Primary provider gemini"):
        _ = router.provider


def test_no_key_value_is_ever_logged(monkeypatch, caplog):
    monkeypatch.setenv("GEMINI_API_KEY", "sk-do-not-log-this-value")

    from brain.providers.gemini import GeminiProvider
    monkeypatch.setattr(GeminiProvider, "generate", lambda self, prompt: "ok")

    router = router_with(
        monkeypatch, "gemini",
        provider="gemini", fallback_providers=["groq", "mistral"],
    )

    with caplog.at_level(logging.DEBUG, logger="Aura"):
        router.active_chain()

    assert "sk-do-not-log-this-value" not in caplog.text
    assert "GROQ_API_KEY is not set" in caplog.text


# ======================================================================
# 6. Failure behaviour is unchanged (Phase 1 contract)
# ======================================================================

def test_a_rate_limited_provider_falls_through(monkeypatch, fake_cloud):
    from brain.providers.gemini import GeminiProvider

    monkeypatch.setattr(
        GeminiProvider, "generate",
        lambda self, prompt: (_ for _ in ()).throw(ProviderRateLimitError("429")),
    )

    router = router_with(
        monkeypatch, "gemini",
        provider="gemini", fallback_providers=["groq"],
    )

    assert router.generate("hello") == "groq reply"


def test_an_account_limit_stops_the_chain_rather_than_burning_it(monkeypatch, fake_cloud):
    # A daily quota is not repaired by asking a different key of the same
    # account, and each attempt costs a request.
    from brain.providers.gemini import GeminiProvider

    monkeypatch.setattr(
        GeminiProvider, "generate",
        lambda self, prompt: (_ for _ in ()).throw(
            ProviderRateLimitError("429", is_account_limit=True)
        ),
    )

    router = router_with(
        monkeypatch, "gemini",
        provider="gemini", fallback_providers=["groq"],
    )

    with pytest.raises(ProviderRateLimitError):
        router.generate("hello")


def test_the_account_limit_category_is_the_one_that_stops_failover():
    error = ProviderRateLimitError("429", is_account_limit=True)

    assert _category_of(error) == ACCOUNT_LIMIT


# ======================================================================
# 7. The registry is complete, and a key alone still conjures nothing
# ======================================================================
#
# This section used to assert the opposite for two names: that `cerebras`
# and `deepseek` were *absent* from the registry (AURA-P2-003, P2-004).
# Phase 11 was asked for both providers, so those two assertions are now
# false by design - but the promise underneath them is not, and it is
# kept here in the stronger form that made them worth writing:
#
#     a key must never conjure a provider, and a provider must never be
#     half-wired
#
# The old tests could only enforce that for the two names they mentioned.
# These enforce it for every name in the registry, including the next one
# added, which is what would have caught the original defect from the
# other side.

def test_every_registered_key_builds_a_real_provider(monkeypatch):
    # The "never half-wired" half. A name in PROVIDER_KEYS that
    # `_instantiate_provider` cannot build is reported as "unknown
    # provider" to the operator who configured it - indistinguishable
    # from a typo, which is how cerebras stayed broken for two phases.
    config = config_with()

    for name, variable in PROVIDER_KEYS.items():

        monkeypatch.setenv(variable, f"dummy-{name}")

        provider = BrainRouter._instantiate_provider(name, config["llm"])

        assert provider is not None, f"{name} is in PROVIDER_KEYS but cannot be built"
        assert getattr(provider, "provider_name", "") == name

        monkeypatch.delenv(variable, raising=False)


def test_every_registered_provider_can_be_a_fallback(monkeypatch, fake_cloud):
    # Buildable is not the same as reachable through the chain builder:
    # that was exactly the ollama defect (AURA-P1-009), where the provider
    # existed and `_instantiate_provider` had no branch for it.
    for name, variable in PROVIDER_KEYS.items():

        if name == "gemini":
            continue

        monkeypatch.setenv(variable, f"dummy-{name}")

        router = router_with(
            monkeypatch, "gemini",
            provider="gemini", fallback_providers=[name],
        )

        assert router.active_chain() == f"gemini->{name}"


def test_a_key_alone_still_conjures_no_provider(monkeypatch, fake_cloud):
    # AURA-P2-004's actual invariant, which outlived the DeepSeek example:
    # an API key in the environment is not evidence that Aura supports the
    # provider, and a name it does not implement must be skipped rather
    # than guessed at.
    monkeypatch.setenv("MYSTERYAI_API_KEY", "dummy")

    assert "mysteryai" not in PROVIDER_KEYS
    assert BrainRouter._skip_reason("mysteryai") == "unknown provider"
    assert BrainRouter._instantiate_provider("mysteryai", config_with()["llm"]) is None

    router = router_with(
        monkeypatch, "gemini",
        provider="gemini", fallback_providers=["mysteryai"],
    )

    assert router.active_chain() == "gemini"


def test_a_registered_provider_without_its_key_is_skipped_not_half_built(monkeypatch):
    # The other direction: registration must not make a provider build
    # itself out of nothing. Every one of them checks its key first and
    # explains the absence by naming the variable.
    config = config_with()

    for name, variable in PROVIDER_KEYS.items():

        monkeypatch.delenv(variable, raising=False)

        assert BrainRouter._instantiate_provider(name, config["llm"]) is None
        assert BrainRouter._skip_reason(name) == f"{variable} is not set"


def test_cerebras_cannot_regain_the_defect_that_unwired_it(monkeypatch):
    # AURA-P2-003 was not "cerebras is missing", it was "cerebras sends
    # the whole prompt as one user message", so Aura's instructions -
    # including the device-action boundary - arrived as conversation. The
    # file was left unregistered until that was fixed.
    #
    # It is fixed structurally, not by a corrected copy of the same code:
    # `generate` now lives on the shared base class, which splits the
    # prompt for every provider built from it. Asserting the method is not
    # overridden is what keeps the fix from being undone by the next
    # copy-paste.
    from brain.providers.cerebras import CerebrasProvider
    from brain.providers.http_chat import HttpChatProvider

    assert PROVIDER_KEYS["cerebras"] == "CEREBRAS_API_KEY"
    assert CerebrasProvider.generate is HttpChatProvider.generate


def test_groq_and_mistral_remain_supported_providers():
    # AURA-P2-003's neighbours. Their keys are absent from this
    # deployment, which is not the same as the providers being obsolete:
    # both are registered, both have model defaults, both are in the
    # shipped chain. Kept.
    assert PROVIDER_KEYS["groq"] == "GROQ_API_KEY"
    assert PROVIDER_KEYS["mistral"] == "MISTRAL_API_KEY"

    assert DEFAULT_CONFIG["llm"]["groq_model"]
    assert DEFAULT_CONFIG["llm"]["mistral_model"]


def test_an_unknown_provider_name_is_skipped_not_guessed(monkeypatch, fake_cloud):
    router = router_with(
        monkeypatch, "gemini",
        provider="gemini", fallback_providers=["not-a-provider"],
    )

    assert router.active_chain() == "gemini"
