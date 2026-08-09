"""
Authentication for the Aura API.

A single bearer token, held server-side, for the personal MVP.

Two rules this module exists to enforce:

  * The token is never logged, never echoed in an error body, and never
    returned by any endpoint.
  * Comparison is constant-time, so a wrong token leaks no information
    about how much of it was right.
"""
import secrets

from fastapi import Header, HTTPException, status

from server.config import settings


def _unauthorized(detail: str) -> HTTPException:
    """401 with the challenge header, and no echo of what was sent."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def check_bearer(authorization: str | None) -> str:
    """
    Validate an `Authorization: Bearer <token>` header value.

    Returns the token on success. Raises HTTPException(401) otherwise.
    Shared by the HTTP dependency and the WebSocket handshake so both
    paths accept exactly the same thing.
    """

    if not settings.auth_token:
        # No token configured - dev mode, local only.
        return "dev"

    if not authorization:
        raise _unauthorized("Missing authorization header")

    parts = authorization.split()

    if len(parts) != 2:
        raise _unauthorized("Invalid authorization header format")

    scheme, token = parts

    if scheme.lower() != "bearer":
        raise _unauthorized("Invalid authentication scheme")

    if not secrets.compare_digest(token, settings.auth_token):
        raise _unauthorized("Invalid token")

    return token


async def verify_token(authorization: str = Header(None)) -> str:
    """
    FastAPI dependency: verify the bearer token on an HTTP request.
    """
    return check_bearer(authorization)
