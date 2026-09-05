"""Small failover wrapper for cloud LLM providers."""

from core.logger import logger
from brain.providers.errors import ProviderRateLimitError, ProviderUnavailableError


# The one category that stops failover rather than continuing it, named
# so the check below cannot drift from the string that produces it.
ACCOUNT_LIMIT = "account/project quota exhaustion"


def _category_of(error: Exception) -> str:
    """
    Why a provider failed, in the terms failover cares about.

    Only the typed provider errors can be categorised with any
    confidence. Anything else is a provider raising something this layer
    was never taught to read - a urllib3 timeout, a JSON decode failure,
    a genuine misconfiguration - and calling that "authentication error"
    sends whoever reads the log looking for a key problem that is not
    there. The exception type is reported instead, by the caller, and
    the category says plainly that it is unclassified.
    """

    if isinstance(error, ProviderRateLimitError):

        if getattr(error, "is_account_limit", False):
            return ACCOUNT_LIMIT

        return "model/provider rate limit"

    if isinstance(error, ProviderUnavailableError):
        return "transient/unavailable"

    return "unclassified provider error"


class FallbackProvider:
    """Try a configured cloud provider once, then the next one.

    This intentionally has no retry loop: a daily-quota 429 cannot be
    repaired by waiting, and each retry burns another request.
    """

    def __init__(self, providers: list, provider_name: str):
        self.providers = providers
        self.provider_name = provider_name
        self.active_provider_name = provider_name.split("->", 1)[0]

        # One record per request attempt, in order: (provider, outcome,
        # error_type). The provider trace the diagnostics file wants -
        # "which provider was attempted and why it failed" - is exactly
        # this list, and the caller that owns the request context is the
        # one that can attach it to a trace line.
        self.attempts: list[tuple[str, str, str]] = []

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
                self.attempts.append((p_name, "ok", ""))
                return reply
            except Exception as error:
                last_error = error

                category = _category_of(error)
                self.attempts.append((p_name, category, type(error).__name__))

                logger.warning(
                    "Provider failed: %s | Failure category: %s | %s: %s",
                    p_name,
                    category,
                    type(error).__name__,
                    str(error)
                )

                # If it's an account-level limit, do NOT try next providers. Stop immediately!
                if category == ACCOUNT_LIMIT:
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

    def generate_with_tools(self, system: str, messages: list, tools: list):
        last_error = None
        for index, provider in enumerate(self.providers):
            if not hasattr(provider, "generate_with_tools"):
                continue
            p_name = getattr(provider, "provider_name", type(provider).__name__)
            try:
                turn = provider.generate_with_tools(system, messages, tools)
                self.active_provider_name = p_name
                self.attempts.append((p_name, "ok", ""))
                return turn
            except Exception as error:
                last_error = error
                category = _category_of(error)
                self.attempts.append((p_name, category, type(error).__name__))
                logger.warning(
                    "Provider failed generate_with_tools: %s | Failure category: %s | %s: %s",
                    p_name,
                    category,
                    type(error).__name__,
                    str(error),
                )
                if category == ACCOUNT_LIMIT:
                    break
        if isinstance(last_error, Exception):
            raise last_error
        raise ProviderUnavailableError("No cloud provider is configured or supports function calling")
