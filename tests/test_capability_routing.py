"""
The provider capability registry and capability-first routing.

Pins what the contract's Phase 1 asked for and the audit's gap 1
described:

    * every buildable provider has a registry row (parity, like
      PROVIDER_KEYS is pinned in test_provider_resolution.py);
    * capability comes from structure and declaration, never from hope -
      providers whose classes implement generate_with_tools are UNKNOWN
      until a real request succeeds, and ones that do not are
      UNSUPPORTED;
    * routing skips UNSUPPORTED providers before a request is built and
      raises CapabilityUnavailableError when nothing capable remains,
      which is NOT a provider-unavailability error (failover cannot fix
      a capability gap);
    * a real generate_with_tools round trip promotes its provider to
      VERIFIED - the only path into that status.
"""

import pytest

from brain.native_fc import ModelTurn
from brain.providers import capabilities as capmod
from brain.providers.capabilities import (
    CapabilityStatus,
    ProviderCapabilities,
    capabilities_for,
    describe_capabilities,
    mark_function_calling_verified,
    supports_function_calling,
)
from brain.providers.errors import (
    CapabilityUnavailableError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from brain.providers.fallback import FallbackProvider
from server.errors import classify
from server.routes.agent import RouterToolCallingLLM


# Registry rows a test promotes are added through monkeypatch.setitem,
# so every mutation is undone between tests and no state leaks into the
# wider suite.
def add_row(monkeypatch, name, status):
    row = ProviderCapabilities(name=name, function_calling=status)
    monkeypatch.setitem(capmod._REGISTRY, name, row)
    return row


# ----------------------------------------------------------------------
# Registry contents
# ----------------------------------------------------------------------

def test_every_buildable_provider_has_a_row():
    from brain.router import PROVIDER_KEYS

    for name in [*PROVIDER_KEYS, "ollama", "mock"]:
        assert capabilities_for(name).name == name, (
            f"{name} is buildable but absent from the capability registry"
        )


def test_structurally_capable_providers_are_unknown_not_verified():
    # The evidence-first rule: capability means a real request succeeded.
    # No provider here has exchanged one, so NONE may be VERIFIED at
    # import time - UNKNOWN is the honest state.
    for name in ("gemini", "openai", "cerebras", "custom", "deepseek",
                 "qwen", "xai"):
        row = capabilities_for(name)

        assert row.function_calling is CapabilityStatus.UNKNOWN
        assert supports_function_calling(name)


def test_structurally_incapable_providers_are_unsupported():
    # Read from the code: Groq, Mistral, OpenRouter, Ollama, Anthropic
    # and Mock define no generate_with_tools, so a tool catalogue sent
    # to them has nowhere to land.
    for name in ("groq", "mistral", "openrouter", "ollama", "anthropic",
                 "mock"):
        row = capabilities_for(name)

        assert row.function_calling is CapabilityStatus.UNSUPPORTED
        assert not supports_function_calling(name)


def test_an_unregistered_name_is_unknown_not_unsupported():
    # UNSUPPORTED is a structural finding about a provider this module
    # has read. Inventing it for a name the registry never saw would be
    # a guess - the one thing the contract forbids.
    assert capabilities_for("brand_new_provider").function_calling is (
        CapabilityStatus.UNKNOWN
    )


def test_a_real_request_promotes_unknown_to_verified(monkeypatch):
    add_row(monkeypatch, "fake_probe", CapabilityStatus.UNKNOWN)

    mark_function_calling_verified("fake_probe")
    mark_function_calling_verified("fake_probe")  # idempotent

    assert capabilities_for("fake_probe").function_calling is (
        CapabilityStatus.VERIFIED
    )


def test_the_registry_renders_for_diagnostics(monkeypatch):
    add_row(monkeypatch, "fake_probe", CapabilityStatus.VERIFIED)

    rendered = describe_capabilities()

    assert rendered["providers"]["fake_probe"] == {
        "name": "fake_probe",
        "function_calling": "verified",
        "streaming": "unknown",
        "vision": "unknown",
    }


# ----------------------------------------------------------------------
# Capability-first routing
# ----------------------------------------------------------------------

class FakeFCProvider:
    """Structurally capable: implements generate_with_tools."""

    provider_name = "fake_fc"

    def __init__(self):
        self.calls = 0

    def generate_with_tools(self, system, messages, tools):
        self.calls += 1
        return ModelTurn(text="ok")


class FakeNoFCProvider:
    """Structurally incapable, and declared so in the registry."""

    provider_name = "fake_nofc"


class Chain:
    """FallbackProvider's shape, without its behaviour."""

    def __init__(self, providers):
        self.providers = providers


def test_routing_skips_an_unsupported_provider_and_uses_a_capable_one(
    monkeypatch,
):
    add_row(monkeypatch, "fake_fc", CapabilityStatus.UNKNOWN)
    add_row(monkeypatch, "fake_nofc", CapabilityStatus.UNSUPPORTED)

    capable = FakeFCProvider()
    adapter = RouterToolCallingLLM(Chain([FakeNoFCProvider(), capable]))

    turn = adapter.generate_with_tools("sys", [], [])

    assert turn.text == "ok"
    assert capable.calls == 1
    assert capabilities_for("fake_fc").function_calling is (
        CapabilityStatus.VERIFIED
    )


def test_routing_through_no_capable_provider_raises_the_capability_error(
    monkeypatch,
):
    add_row(monkeypatch, "fake_nofc", CapabilityStatus.UNSUPPORTED)

    adapter = RouterToolCallingLLM(Chain([FakeNoFCProvider()]))

    with pytest.raises(CapabilityUnavailableError):
        adapter.generate_with_tools("sys", [], [])


def test_the_capability_error_is_not_a_transient_unavailability():
    # Failover and retry answer unavailability; nothing answers a
    # capability gap. Subclassing ProviderUnavailableError would route
    # this through the 503 "try again shortly" path - advice that can
    # never become true.
    assert not isinstance(
        CapabilityUnavailableError("x"), ProviderUnavailableError
    )


def test_the_capability_error_maps_to_501_capability_unavailable():
    failure = classify(CapabilityUnavailableError("x"))

    assert failure.status == 501
    assert failure.code == "capability_unavailable"


def test_a_bare_capable_provider_passes_through_and_is_promoted(
    monkeypatch,
):
    add_row(monkeypatch, "fake_fc", CapabilityStatus.UNKNOWN)

    capable = FakeFCProvider()
    adapter = RouterToolCallingLLM(capable)

    adapter.generate_with_tools("sys", [], [])

    assert capabilities_for("fake_fc").function_calling is (
        CapabilityStatus.VERIFIED
    )


# ----------------------------------------------------------------------
# Provider attempt records (the provider trace)
# ----------------------------------------------------------------------

class FailingProvider:

    provider_name = "fake_dead"

    def generate(self, prompt):
        raise ProviderRateLimitError("quota exhausted")


class WorkingProvider:

    provider_name = "fake_alive"

    def generate(self, prompt):
        return "hello"


def test_the_chain_records_every_attempt_in_order():
    chain = FallbackProvider(
        [FailingProvider(), WorkingProvider()], "fake_dead->fake_alive"
    )

    assert chain.generate("hi") == "hello"

    assert chain.attempts == [
        ("fake_dead", "model/provider rate limit", "ProviderRateLimitError"),
        ("fake_alive", "ok", ""),
    ]

