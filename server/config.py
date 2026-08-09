"""
Server settings.

Everything here comes from the environment (prefix `AURA_SERVER_`) or a
local `.env` file. Nothing is hardcoded and nothing is committed - see
`.env.example` for the shape and `docs/SECURITY.md` for the rules.
"""

from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerSettings(BaseSettings):
    """Configuration for the API process itself (not for Aura's brain)."""

    model_config = SettingsConfigDict(
        env_prefix="AURA_SERVER_",
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "0.0.0.0"
    port: int = 8000

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


settings = ServerSettings()
