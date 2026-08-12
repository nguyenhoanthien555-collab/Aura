"""
Brain Router.

Selects and owns the configured LLM provider, and exposes the single
generate(prompt) entry point the rest of the brain depends on.

The provider is created lazily on first use so that constructing the
router (and therefore booting Aura) never requires network access or
API keys.
"""

import os
from importlib import import_module

from core.config import load_config
from core.logger import logger

from brain.ports import LLM


def _optional_float(value) -> float | None:
    """
    `value` as a float, or None if it is unusable.

    Only `llm.temperature` reaches this. `core/settings_store.py` range-checks
    it on write, but config.yaml can be edited by hand, and a provider that
    refuses to construct because the file says `temperature: warm` would be
    reported as a missing key. None means "do not send the field", which is a
    working request using the provider's own default.
    """

    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        logger.warning(
            "llm.temperature must be a number, got %r - sending no temperature",
            value,
        )
        return None


# The environment variable each cloud provider needs before it can be
# built. Used only to explain a skipped provider by naming the variable -
# never to read or report a value.
PROVIDER_KEYS = {
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "xai": "XAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "qwen": "QWEN_API_KEY",
}

# Ollama takes no key, only a reachable host. Listed separately so that
# `provider: ollama` still resolves through the chain builder (and can
# therefore carry cloud fallbacks) while _skip_reason knows it needs no
# secret to explain.
KEYLESS_PROVIDERS = ("ollama",)

# The providers built on `brain/providers/http_chat.py`, which take an
# identical constructor. Registering one is a row here and a small file,
# and this row is the only place that names the module - so
# `_instantiate_provider` gained one generic branch instead of six
# near-identical ones, and the five hand-written branches below it were
# left exactly as they were.
#
#   name -> (module, class, the llm.* config key holding the model)
#
# Every name here must also be in PROVIDER_KEYS, and every name in
# PROVIDER_KEYS must be buildable; `tests/test_provider_resolution.py`
# asserts both, because a half-registered provider is reported to the
# phone as "unknown provider" and looks like a typo by the operator.
HTTP_CHAT_PROVIDERS = {
    "openai": ("brain.providers.openai", "OpenAIProvider", "openai_model"),
    "anthropic": (
        "brain.providers.anthropic", "AnthropicProvider", "anthropic_model",
    ),
    "cerebras": (
        "brain.providers.cerebras", "CerebrasProvider", "cerebras_model",
    ),
    "xai": ("brain.providers.xai", "XAIProvider", "xai_model"),
    "deepseek": (
        "brain.providers.deepseek", "DeepSeekProvider", "deepseek_model",
    ),
    "qwen": ("brain.providers.qwen", "QwenProvider", "qwen_model"),
}


class BrainRouter:

    def __init__(
        self,
        provider: LLM | None = None,
        provider_name: str | None = None,
    ):

        if provider is not None:
            self._provider = provider
            self.provider_name = (
                provider_name or type(provider).__name__
            )
            return

        self._provider = None

        if provider_name is None:
            config = load_config()
            provider_name = config["llm"]["provider"]

        self.provider_name = provider_name


    @property
    def provider(self) -> LLM:
        """
        The active provider, created on first access.
        """

        if self._provider is None:
            self._provider = self._create_provider(
                self.provider_name
            )

        return self._provider


    def _create_provider(self, name: str) -> LLM:

        if name == "mock":
            from brain.providers.mock import MockProvider
            return MockProvider()

        config = load_config().get("llm") or {}

        primary = self._instantiate_provider(name, config)
        if primary is None:
            raise ValueError(f"Primary provider {name} could not be initialized (missing API key or config)")

        fallback_names = self._fallback_names(config)

        providers = [primary]
        provider_names = [name]

        # Why a provider was left out, so a chain that silently collapsed
        # to one provider can be diagnosed from the log rather than by
        # reading this function.
        skipped: list[str] = []

        for fb_name in fallback_names:
            if fb_name == name:
                skipped.append(f"{fb_name} (already the primary)")
                continue

            try:
                fb_provider = self._instantiate_provider(fb_name, config)
            except Exception as error:
                # A fallback is optional by definition. One that raises -
                # a provider whose __init__ validates its key, a bad model
                # string - must not take the process down with it, or an
                # optional provider becomes a mandatory one and Aura will
                # not boot at all. Reported as failed, not merely skipped:
                # the two have different fixes.
                skipped.append(
                    f"{fb_name} (initialization raised {type(error).__name__})"
                )
                logger.debug(
                    "Fallback %s raised during initialization", fb_name,
                    exc_info=True,
                )
                continue

            if fb_provider is not None:
                providers.append(fb_provider)
                provider_names.append(fb_name)
            else:
                skipped.append(f"{fb_name} ({self._skip_reason(fb_name)})")

        logger.info(
            "Provider chain requested: %s | initialized: %s",
            ", ".join([name, *fallback_names]) or "none",
            ", ".join(provider_names),
        )

        if skipped:
            # Warning, not debug: a configured fallback that never became
            # a provider means the failover the operator asked for does
            # not exist, and they will otherwise only find out during an
            # outage.
            logger.warning(
                "Fallback providers not available: %s",
                "; ".join(skipped),
            )

        if len(providers) == 1 and fallback_names:
            # Every fallback was skipped. The chain the operator wrote
            # exists only in config.yaml, and saying so once at boot is
            # cheaper than discovering it mid-outage.
            logger.warning(
                "No fallback provider was initialized: %s runs with no failover",
                name,
            )

        if len(providers) > 1:
            from brain.providers.fallback import FallbackProvider
            chain_name = "->".join(provider_names)
            return FallbackProvider(providers, chain_name)

        return primary

    @staticmethod
    def _fallback_names(config: dict) -> list[str]:
        """
        The fallback chain, from exactly one authoritative setting.

        `fallback_providers` is that setting. `fallback_provider` is its
        singular predecessor and is still read, because deleting it
        outright would turn an old config.yaml into a silent loss of
        failover - the worst of the three possible outcomes.

        The subtlety this exists for: `fallback_providers` is in
        DEFAULT_CONFIG, so after the deep merge the key is *always*
        present, just empty. The old `is None` test therefore never fired
        and an operator who wrote only `fallback_provider: openrouter`
        got no fallback at all and no warning (AURA-P2-010). Emptiness,
        not absence, is what has to be tested.

        `fallback_model` is a different setting and is untouched here -
        it names OpenRouter's model, not a provider.
        """

        names = config.get("fallback_providers") or []

        if not isinstance(names, list):
            logger.warning(
                "llm.fallback_providers must be a list, got %s - ignoring it",
                type(names).__name__,
            )
            names = []

        names = [str(entry) for entry in names if entry]

        legacy = config.get("fallback_provider")

        if legacy and not names:
            # Honoured, so an old config keeps working, and said out loud,
            # so it stops being invisible.
            logger.warning(
                "llm.fallback_provider is the superseded singular form; "
                "using it as the chain. Replace it with "
                "llm.fallback_providers: [%s]",
                legacy,
            )
            return [str(legacy)]

        if legacy and names and str(legacy) not in names:
            # Both set and disagreeing. The list wins - one of them has to,
            # and it is the one the operator is being told to use - but a
            # dropped provider is never dropped quietly.
            logger.warning(
                "llm.fallback_provider (%s) is ignored: llm.fallback_providers "
                "is authoritative and does not list it",
                legacy,
            )

        return names

    @staticmethod
    def _skip_reason(name: str) -> str:
        """
        Why `name` could not be built, without revealing any key.

        Names the environment variable that is missing, never its value,
        so this is safe to log and still actionable.
        """

        if name in KEYLESS_PROVIDERS:
            # No key to be missing, so a failure here is the host being
            # wrong or unreachable, not a secret being absent.
            return "initialization failed (needs no API key - check the host)"

        required = PROVIDER_KEYS.get(name)

        if required is None:
            return "unknown provider"

        if not os.getenv(required):
            return f"{required} is not set"

        return "initialization failed"

    @staticmethod
    def _instantiate_provider(name: str, config: dict) -> LLM | None:
        """
        One provider, built from config, or None if it cannot be.

        A staticmethod because it uses no router state and because
        `server/settings_service.test_provider` calls it unbound - as
        `BrainRouter._instantiate_provider(name, config)` - to build the
        provider the same way the router would. As an instance method that
        call bound `self=name` and raised TypeError, which the caller
        reported as "not configured": every `POST /api/providers/test`
        answered "not configured" no matter which provider was asked for
        or whether its key was present.
        """

        if name == "ollama":
            # Needs no key, so there is nothing to check first: it is
            # built, and a wrong host surfaces when a request is made.
            # Reachability is deliberately not probed here - construction
            # must not require network access (see the module docstring).
            from brain.providers.ollama import OllamaProvider
            return OllamaProvider()

        if name == "gemini":
            if not os.getenv("GEMINI_API_KEY"):
                return None
            from brain.providers.gemini import GeminiProvider
            return GeminiProvider()

        if name == "groq":
            if not os.getenv("GROQ_API_KEY"):
                return None
            from brain.providers.groq import GroqProvider
            return GroqProvider(
                model=config.get("groq_model") or os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
                timeout=float(config.get("timeout", 45.0)),
                max_tokens=int(config.get("max_output_tokens", 768)),
            )

        if name == "mistral":
            if not os.getenv("MISTRAL_API_KEY"):
                return None
            from brain.providers.mistral import MistralProvider
            return MistralProvider(
                model=config.get("mistral_model") or os.getenv("MISTRAL_MODEL", "open-mistral-7b"),
                timeout=float(config.get("timeout", 45.0)),
                max_tokens=int(config.get("max_output_tokens", 768)),
            )

        if name == "openrouter":
            if not os.getenv("OPENROUTER_API_KEY"):
                return None
            from brain.providers.openrouter import OpenRouterProvider
            return OpenRouterProvider(
                model=config.get("fallback_model") or config.get("openrouter_model") or "openrouter/free",
                timeout=float(config.get("timeout", 45.0)),
                max_tokens=int(config.get("max_output_tokens", 768)),
            )

        spec = HTTP_CHAT_PROVIDERS.get(name)

        if spec is not None:
            module_path, class_name, model_key = spec

            if not os.getenv(PROVIDER_KEYS[name]):
                return None

            provider_class = getattr(import_module(module_path), class_name)

            # An empty model means "use the class default", which is where
            # the per-provider default lives. Passing "" rather than the
            # default from here keeps one source of truth for it.
            return provider_class(
                model=config.get(model_key) or "",
                timeout=float(config.get("timeout", 45.0)),
                max_tokens=int(config.get("max_output_tokens", 768)),
                temperature=_optional_float(config.get("temperature")),
            )

        return None


    def active_chain(self) -> str:
        """
        The chain that was actually built, e.g. "gemini->groq".

        `provider_name` is what was *asked for* and stays that way - it is
        read before the provider is lazily created, and changing it would
        make the router's identity depend on whether anyone had used it
        yet. This is what was *obtained*, which is the question a health
        check is really asking. Building the provider is a side effect of
        calling it, so this is not for a hot path.
        """

        return getattr(
            self.provider, "provider_name", self.provider_name
        )


    def generate(self, prompt: str) -> str:
        """
        Generate a response using the configured provider.
        """
        return self.provider.generate(prompt)
