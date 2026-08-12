"""Provider failures that may safely trigger a different cloud provider."""


class ProviderUnavailableError(RuntimeError):
    """A transient cloud-provider failure (network, 5xx, or overload)."""


class ProviderAuthError(ValueError):
    """
    A provider refused the credentials (401/403).

    NOT a `ProviderUnavailableError`: nothing about waiting or retrying
    fixes a wrong key, and classifying it as transient would have the
    chain report "unavailable" for a provider that is answering perfectly
    and simply does not know the caller.

    A `ValueError` subclass because that is already what every provider in
    this package raises for a key problem - `ValueError("GROQ_API_KEY is
    not configured")` - so anything that already treats a ValueError from
    a provider as "the credentials are wrong" keeps working, including
    `POST /api/providers/test`. Failover still continues past it: a bad
    key on the primary is exactly when the fallback earns its place.
    """


class ProviderParameterError(RuntimeError):
    """
    The request was well formed and one *optional* field was refused.

    Its own type because it is the one 4xx that can be repaired without a
    human: OpenAI's reasoning models reject `temperature` and rename
    `max_tokens` to `max_completion_tokens`, so a chat that would
    otherwise 400 forever succeeds by sending the request again without
    the field, or with the other spelling. `parameter` names the field the
    provider objected to; the retry lives in
    `brain/providers/http_chat.py` and happens at most once.
    """

    def __init__(self, message: str, parameter: str = ""):
        super().__init__(message)
        self.parameter = parameter


class ProviderRateLimitError(ProviderUnavailableError):
    """A provider rejected the request due to quota or rate limiting."""

    def __init__(self, message: str, retry_after: float | None = None, is_account_limit: bool = False):
        super().__init__(message)
        self.retry_after = retry_after
        self.is_account_limit = is_account_limit
