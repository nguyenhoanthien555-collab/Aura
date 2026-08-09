"""Provider failures that may safely trigger a different cloud provider."""


class ProviderUnavailableError(RuntimeError):
    """A transient cloud-provider failure (network, 5xx, or overload)."""


class ProviderRateLimitError(ProviderUnavailableError):
    """A provider rejected the request due to quota or rate limiting."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after
