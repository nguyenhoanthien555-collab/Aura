"""
Brain Router.

Selects and owns the configured LLM provider, and exposes the single
generate(prompt) entry point the rest of the brain depends on.

The provider is created lazily on first use so that constructing the
router (and therefore booting Aura) never requires network access or
API keys.
"""

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

        if name == "gemini":
            from brain.providers.gemini import GeminiProvider
            return GeminiProvider()

        raise ValueError(
            f"Unknown provider: {name}"
        )


    def generate(self, prompt: str) -> str:
        """
        Generate a response using the configured provider.
        """
        return self.provider.generate(prompt)
