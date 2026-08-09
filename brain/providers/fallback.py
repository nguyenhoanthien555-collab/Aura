"""Small failover wrapper for cloud LLM providers."""

from core.logger import logger
from brain.providers.errors import ProviderRateLimitError, ProviderUnavailableError


class FallbackProvider:
    """Try a configured cloud provider once, then the next one.

    This intentionally has no retry loop: a daily-quota 429 cannot be
    repaired by waiting, and each retry burns another request.
    """

    def __init__(self, providers: list, provider_name: str):
        self.providers = providers
        self.provider_name = provider_name
        self.active_provider_name = provider_name.split("->", 1)[0]

    @property
    def supports_text(self) -> bool:
        return any(getattr(p, "supports_text", False) for p in self.providers)

    @property
    def supports_vision(self) -> bool:
        return any(getattr(p, "supports_vision", False) for p in self.providers)

    def generate(self, prompt: str) -> str:
        last_error = None
        for index, provider in enumerate(self.providers):
            p_name = getattr(provider, "provider_name", type(provider).__name__)
            logger.info("Provider selected: %s", p_name)
            try:
                reply = provider.generate(prompt)
                self.active_provider_name = p_name
                return reply
            except Exception as error:
                last_error = error

                if isinstance(error, ProviderRateLimitError):
                    if getattr(error, "is_account_limit", False):
                        category = "account/project quota exhaustion"
                    else:
                        category = "model/provider rate limit"
                elif isinstance(error, ProviderUnavailableError):
                    category = "transient/unavailable"
                else:
                    category = "authentication/configuration error"

                logger.warning(
                    "Provider failed: %s | Failure category: %s | Error: %s",
                    p_name,
                    category,
                    str(error)
                )

                # If it's an account-level limit, do NOT try next providers. Stop immediately!
                if category == "account/project quota exhaustion":
                    logger.warning("Account-level quota exhaustion detected; stopping provider failover immediately.")
                    break

                if index + 1 < len(self.providers):
                    next_p = getattr(self.providers[index + 1], "provider_name", type(self.providers[index + 1]).__name__)
                    logger.info("Fallback provider selected: %s", next_p)
                    continue
                else:
                    break

        if isinstance(last_error, Exception):
            raise last_error
        raise ProviderUnavailableError("No cloud provider is configured")
