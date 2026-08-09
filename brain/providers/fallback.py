"""Small failover wrapper for cloud LLM providers."""

from core.logger import logger
from brain.providers.errors import ProviderUnavailableError


class FallbackProvider:
    """Try a configured cloud provider once, then the next one.

    This intentionally has no retry loop: a daily-quota 429 cannot be
    repaired by waiting, and each retry burns another request.
    """

    def __init__(self, providers: list, provider_name: str):
        self.providers = providers
        self.provider_name = provider_name
        self.active_provider_name = provider_name.split("->", 1)[0]

    def generate(self, prompt: str) -> str:
        last_error = None
        for index, provider in enumerate(self.providers):
            try:
                reply = provider.generate(prompt)
                self.active_provider_name = getattr(provider, "provider_name", type(provider).__name__)
                return reply
            except ProviderUnavailableError as error:
                last_error = error
                if index + 1 < len(self.providers):
                    logger.warning(
                        "Cloud provider %s unavailable; trying configured fallback",
                        getattr(provider, "provider_name", type(provider).__name__),
                    )
                    continue
                raise
        raise last_error or ProviderUnavailableError("No cloud provider is configured")
