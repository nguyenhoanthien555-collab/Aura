"""
Brain Router.

Selects and owns the configured LLM provider, and exposes the single
generate(prompt) entry point the rest of the brain depends on.

The provider is created lazily on first use so that constructing the
router (and therefore booting Aura) never requires network access or
API keys.
"""

import os

from core.config import load_config

from brain.ports import LLM


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

        if name == "ollama":
            from brain.providers.ollama import OllamaProvider
            return OllamaProvider()

        config = load_config().get("llm") or {}

        primary = self._instantiate_provider(name, config)
        if primary is None:
            raise ValueError(f"Primary provider {name} could not be initialized (missing API key or config)")

        fallback_names = config.get("fallback_providers")
        if fallback_names is None:
            old_fallback = config.get("fallback_provider")
            if old_fallback:
                fallback_names = [old_fallback]
            else:
                fallback_names = ["groq", "mistral", "openrouter"]

        providers = [primary]
        provider_names = [name]

        for fb_name in fallback_names:
            if fb_name == name:
                continue
            fb_provider = self._instantiate_provider(fb_name, config)
            if fb_provider is not None:
                providers.append(fb_provider)
                provider_names.append(fb_name)

        if len(providers) > 1:
            from brain.providers.fallback import FallbackProvider
            chain_name = "->".join(provider_names)
            return FallbackProvider(providers, chain_name)

        return primary

    def _instantiate_provider(self, name: str, config: dict) -> LLM | None:
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

        return None


    def generate(self, prompt: str) -> str:
        """
        Generate a response using the configured provider.
        """
        return self.provider.generate(prompt)
