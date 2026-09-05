"""
Provider capability registry.

What each LLM provider *can* do, expressed as data the router can read
before a request is spent - the capability-first half of routing. Two
vocabulary points matter:

    VERIFIED     demonstrated by an actual successful request with this
                 capability (a real generate_with_tools round trip).
    UNKNOWN      structurally present (the provider class implements the
                 method) but never demonstrated against the vendor. Per
                 the contract's evidence rule this is the honest initial
                 state: no provider here has exchanged a real FC request
                 with its vendor, so none is VERIFIED at import time.
    UNSUPPORTED  structurally absent. Read from the code, not assumed:
                 the classes without `generate_with_tools` are the ones
                 this registry marks UNSUPPORTED.

Fallback only fires on provider *error*. A capability gap is not an
error to retry past - it is a permanent shape of the provider - so
capability-first routing skips UNSUPPORTED providers before any request
is built, and raises CapabilityUnavailableError when nothing capable
remains, instead of degrading to a prose answer.
"""

from enum import Enum

from core.logger import logger


class CapabilityStatus(str, Enum):

    VERIFIED = "verified"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"


class ProviderCapabilities:

    def __init__(
        self,
        name: str,
        function_calling: CapabilityStatus,
        streaming: CapabilityStatus = CapabilityStatus.UNKNOWN,
        vision: CapabilityStatus = CapabilityStatus.UNKNOWN,
    ):
        self.name = name
        self.function_calling = function_calling
        self.streaming = streaming
        self.vision = vision

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "function_calling": self.function_calling.value,
            "streaming": self.streaming.value,
            "vision": self.vision.value,
        }


# Structural evidence, read from brain/providers: `generate_with_tools`
# is implemented by GeminiProvider and by OpenAICompatibleProvider (which
# OpenAI, Cerebras, Custom, DeepSeek, Qwen and XAI subclass). Groq,
# Mistral, OpenRouter, Ollama, Anthropic and Mock define no such method,
# so a tool catalogue sent to them would have nowhere to go.
#
# Everything capable is UNKNOWN, not VERIFIED: capability means a real
# request succeeded, and none has. `mark_function_calling_verified` is
# the only thing that flips a row to VERIFIED, and it is called only
# after generate_with_tools returned a real turn.
_FUNCTION_CAPABLE = frozenset({
    "gemini", "openai", "cerebras", "custom", "deepseek", "qwen", "xai",
})

_REGISTRY: dict[str, ProviderCapabilities] = {}

for _name in (
    "gemini", "groq", "mistral", "openrouter", "openai", "anthropic",
    "cerebras", "xai", "deepseek", "qwen", "custom", "ollama", "mock",
):
    _REGISTRY[_name] = ProviderCapabilities(
        name=_name,
        function_calling=(
            CapabilityStatus.UNKNOWN
            if _name in _FUNCTION_CAPABLE
            else CapabilityStatus.UNSUPPORTED
        ),
    )


def capabilities_for(name: str) -> ProviderCapabilities:
    """
    The row for `name`, or a fully-UNKNOWN row for an unregistered name.

    An unknown provider name is UNKNOWN, never UNSUPPORTED: UNSUPPORTED
    is a structural finding about a provider this module has read, and
    inventing it for a name the registry has never seen would be a guess.
    """

    row = _REGISTRY.get(name)

    if row is not None:
        return row

    return ProviderCapabilities(
        name=str(name),
        function_calling=CapabilityStatus.UNKNOWN,
    )


def supports_function_calling(provider_name: str) -> bool:
    """True when a function-calling request to `provider_name` may go out."""

    return capabilities_for(provider_name).function_calling is not (
        CapabilityStatus.UNSUPPORTED
    )


def mark_function_calling_verified(provider_name: str) -> None:
    """
    Record that a real generate_with_tools round trip just succeeded.

    The only transition into VERIFIED, and the reason the contract's
    "UNKNOWN until demonstrated" rule can hold without a manual
    bookkeeping step: the first working FC request promotes its provider
    on the spot. Idempotent by construction.
    """

    row = capabilities_for(provider_name)

    if row.function_calling is CapabilityStatus.VERIFIED:
        return

    row.function_calling = CapabilityStatus.VERIFIED

    logger.info(
        "Provider capability VERIFIED by a real request: %s supports "
        "function calling",
        provider_name,
    )


def describe_capabilities() -> dict:
    """The whole registry, for diagnostics and /api/capabilities-style reads."""

    return {
        "providers": {
            name: row.as_dict() for name, row in sorted(_REGISTRY.items())
        }
    }
