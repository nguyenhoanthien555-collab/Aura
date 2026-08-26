"""
Server settings.

Everything here comes from the environment (prefix `AURA_SERVER_`) or a
local `.env` file. Nothing is hardcoded and nothing is committed - see
`.env.example` for the shape and `docs/SECURITY.md` for the rules.
"""

from typing import List

import os
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


# The opt-in that permits running with no authentication. Named without
# the `AURA_SERVER_` prefix because it is a deliberate, one-off developer
# action rather than part of the server's normal configuration - and
# because that is the name `docs/` and the repair plan already use.
INSECURE_ENV_VAR = "AURA_ALLOW_INSECURE"


class InsecureConfigurationError(RuntimeError):
    """
    Raised at startup when the server would accept unauthenticated requests.

    A distinct type so a caller can present it as a configuration problem
    rather than a crash. It carries no secret: there is, by definition,
    no token to leak when this is raised.
    """


class ServerSettings(BaseSettings):
    """Configuration for the API process itself (not for Aura's brain)."""

    model_config = SettingsConfigDict(
        env_prefix="AURA_SERVER_",
        case_sensitive=False,
        env_file=PROJECT_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "0.0.0.0"
    # Render supplies its listener port as `PORT`, while local Docker and
    # existing installs use the namespaced setting. Prefer Render's value
    # when both are present so `python -m server.main` is deployable as-is.
    port: int = Field(
        default=8000,
        validation_alias=AliasChoices("PORT", "AURA_SERVER_PORT"),
    )

    # Empty means "dev mode, no auth". Anything reachable off localhost
    # must set this; see `is_auth_enabled`.
    auth_token: str = ""

    cors_origins: List[str] = ["*"]
    log_level: str = "INFO"

    # Request limits
    max_message_length: int = 8000
    max_screen_text_length: int = 20000
    max_upload_bytes: int = 8 * 1024 * 1024

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value):
        """
        Accept `AURA_SERVER_CORS_ORIGINS=https://a.example,https://b.example`.

        pydantic-settings would otherwise try to JSON-decode the string and
        fail on anything that is not a JSON list.
        """
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                return value
            return [item.strip() for item in text.split(",") if item.strip()]
        return value

    @property
    def is_auth_enabled(self) -> bool:
        """True when a bearer token is configured."""
        return bool(self.auth_token)

    @property
    def allow_insecure(self) -> bool:
        """
        True when the operator has explicitly opted out of authentication.

        Read from the environment on each access rather than captured as a
        field, so that this is never inherited from `.env` parsing order or
        cached across a test that sets it. Only the exact values `1`,
        `true` and `yes` count - a typo means "no", which is the safe
        direction to fail in.
        """
        value = os.getenv(INSECURE_ENV_VAR, "").strip().lower()
        return value in {"1", "true", "yes"}

    @property
    def binds_publicly(self) -> bool:
        """
        True when the configured host is reachable from off this machine.

        Used only to sharpen the wording of the insecure-mode warning. It
        is not an exemption: an unauthenticated server on loopback is still
        an unauthenticated server, and Docker port mapping makes "loopback"
        a claim about the container, not about the network.
        """
        return self.host.strip() not in {"127.0.0.1", "::1", "localhost"}


settings = ServerSettings()


def cors_policy(active: ServerSettings | None = None) -> dict:
    """
    CORS middleware arguments that can never be wildcard-plus-credentials.

    AURA-P1-007 was that pairing. It is not merely a lint: with
    `allow_origins=["*"]` and `allow_credentials=True`, Starlette sets
    `preflight_explicit_allow_origin`, so a *preflight* response echoes
    whichever origin asked - `Access-Control-Allow-Origin: https://evil.example`
    together with `Access-Control-Allow-Credentials: true`, which browsers
    do honour. (The simple-request path sends a literal `*`, which they
    reject. The hole opens through preflight, not through GET.)

    The rule, in one place:

      * explicit origins  -> credentials allowed. The operator has named
        who may call, so cookies and the Authorization header can cross.
      * wildcard origins  -> credentials refused. "Anyone may call" and
        "callers may carry the user's credentials" must not both be true.

    Wildcard is not blocked outright, because Aura authenticates with a
    bearer token that JavaScript sets explicitly; such a request does not
    need credentials mode, so the wildcard remains useful for local
    development without weakening anything.
    """

    active = active if active is not None else settings

    origins = [origin for origin in active.cors_origins if origin]
    wildcard = "*" in origins

    return {
        "allow_origins": ["*"] if wildcard else origins,
        "allow_credentials": not wildcard,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
    }


def enforce_auth_policy(active: ServerSettings | None = None) -> str | None:
    """
    Refuse to start an unauthenticated server unless that was asked for.

    Returns a warning string when insecure mode was explicitly enabled (so
    the caller can log it in its own voice), or None when a token is set.
    Raises `InsecureConfigurationError` when no token is configured and no
    opt-in was given - which is the case AURA-P1-008 is about: the server
    used to log a warning and then serve every request anyway, so a missing
    environment variable on a public host silently published an LLM.

    Nothing here reads, logs or returns the token itself; the only inputs
    are whether it is empty and whether the opt-in is present.
    """

    active = active if active is not None else settings

    if active.is_auth_enabled:
        return None

    if not active.allow_insecure:
        raise InsecureConfigurationError(
            "AURA_SERVER_AUTH_TOKEN is not set, so the API would accept every "
            "request. Set a token, or set "
            f"{INSECURE_ENV_VAR}=1 to allow this deliberately for local "
            "development."
        )

    exposure = (
        f"host {active.host} is not loopback"
        if active.binds_publicly
        else f"host {active.host}"
    )

    return (
        f"{INSECURE_ENV_VAR} is set: the API is UNAUTHENTICATED and will "
        f"accept every request ({exposure}). Never use this on a deployed "
        "server."
    )
