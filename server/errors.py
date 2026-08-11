"""
How a failed turn is described to a client.

`/api/chat` used to answer every failure with the same opaque 500
(AURA-P1-014), so a client could not tell "wait and retry" from "this
will never work", and an operator could not tell them apart either
without server logs. This module maps the provider errors that already
exist (`brain/providers/errors.py`) onto HTTP and WebSocket vocabulary.

It deliberately does *not* define new exception types. The brain owns the
error hierarchy; this is a presentation layer over it, and it is shared by
the HTTP route and the WebSocket route so the two cannot drift apart.

Two rules govern what may cross the boundary:

  * The exception's own text never does. Provider messages carry hosts,
    ports, filesystem paths, model identifiers and occasionally key
    fragments. Every message below is a constant written here.
  * The taxonomy is coarse on purpose. "Which provider failed" and "why"
    are operator facts and stay in the log, keyed by `message_id`.
"""

from dataclasses import dataclass

from brain.providers.errors import (
    ProviderRateLimitError,
    ProviderUnavailableError,
)


@dataclass(frozen=True)
class Failure:
    """A classified failure, in terms safe to send to a client."""

    status: int
    code: str
    message: str
    retry_after: float | None = None


# The generic case, and the one the pre-existing contract pins: anything
# this layer cannot recognise is an internal error, not a provider one.
# Guessing "provider problem" for an unrecognised exception would be the
# same mistake `_category_of` was fixed for in Phase 1.
UNEXPECTED = Failure(
    status=500,
    code="chat_failed",
    message="The request could not be completed.",
)

RATE_LIMITED = Failure(
    status=429,
    code="rate_limited",
    message="The language model is rate limited. Try again shortly.",
)

PROVIDER_UNAVAILABLE = Failure(
    status=503,
    code="provider_unavailable",
    message="The language model is temporarily unavailable.",
)


def classify(error: BaseException) -> Failure:
    """
    Describe `error` without quoting it.

    Order matters: `ProviderRateLimitError` is a subclass of
    `ProviderUnavailableError`, so the narrower case is tested first.
    """

    if isinstance(error, ProviderRateLimitError):
        retry_after = getattr(error, "retry_after", None)

        if retry_after is None:
            return RATE_LIMITED

        # The provider told us how long to wait, which is a fact about
        # scheduling rather than about the provider's internals, so it is
        # safe to pass on and genuinely useful to a client.
        return Failure(
            status=RATE_LIMITED.status,
            code=RATE_LIMITED.code,
            message=RATE_LIMITED.message,
            retry_after=float(retry_after),
        )

    if isinstance(error, ProviderUnavailableError):
        return PROVIDER_UNAVAILABLE

    return UNEXPECTED
