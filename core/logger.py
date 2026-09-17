"""
Aura Logger
Centralized logging configuration.

The level is readable from two places, because logging has to work
before configuration has been loaded:

  * `AURA_LOG_LEVEL` in the environment, read at import time. This is
    what governs anything logged during startup, and it is the only
    control a deployed container has before config.yaml is parsed.

  * `logging.level` in config.yaml, applied by `apply_config_level` once
    the composition root has a config dict. The environment variable
    wins when both are set, so verbosity can be raised on a running
    instance without editing the config file it shipped with.

This module deliberately does not import core.config: config imports
*this* module to report its own problems, and a cycle would break both.
"""

import logging
import os
import re
from rich.logging import RichHandler


# Mask Bearer tokens, API keys, and sensitive credentials in logs
SENSITIVE_PATTERNS = [
    (re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.]{12,}", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"([?&](?:api_?key|token|auth|secret)=)[^&\s]+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(?:AIzaSy[A-Za-z0-9_-]{33}|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,})"), "[REDACTED_KEY]"),
    (re.compile(r"(['\"](?:api_key|auth_token|token|password|secret)['\"]\s*:\s*['\"])[^'\"]+(['\"])", re.IGNORECASE), r"\1[REDACTED]\2"),
]


def redact_secrets(text: str) -> str:
    """Sanitize sensitive secrets from log strings."""
    if not isinstance(text, str):
        return text
    for pattern, repl in SENSITIVE_PATTERNS:
        text = pattern.sub(repl, text)
    return text


class SecretMaskingFilter(logging.Filter):
    """Logging filter that scrubs authentication tokens, API keys, and passwords."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_secrets(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: redact_secrets(v) if isinstance(v, str) else v for k, v in record.args.items()}
            elif isinstance(record.args, (list, tuple)):
                record.args = tuple(redact_secrets(a) if isinstance(a, str) else a for a in record.args)
        return True


# The variable an operator sets to see debug output before config.yaml
# has been read.
LOG_LEVEL_ENV = "AURA_LOG_LEVEL"

DEFAULT_LEVEL = logging.INFO

# Spelled out rather than resolved through `logging` attribute lookup,
# which would happily return a module-level list for a name like
# "handlers" and set an unusable level from it.
LEVELS = {
    "CRITICAL": logging.CRITICAL,
    "FATAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
    "NOTSET": logging.NOTSET,
}


def resolve_level(value) -> int | None:
    """
    Turn a configured level into a logging constant, or None.

    Accepts "debug", "DEBUG", "10" or 10. Returns None for anything
    unreadable so the caller keeps its current level and can say what it
    ignored - a typo in config.yaml must not stop Aura from starting.
    """

    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, int):
        return value

    text = str(value).strip()

    if not text:
        return None

    if text.isdigit():
        return int(text)

    return LEVELS.get(text.upper())


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("Aura")

    # Re-evaluate the startup environment on every explicit setup call.
    # This matters to launchers that set the variable after importing a
    # module, and it is independent of whether a handler already exists.
    level = resolve_level(os.getenv(LOG_LEVEL_ENV))
    logger.setLevel(DEFAULT_LEVEL if level is None else level)

    # Only Aura's own application handler counts for idempotence. Pytest
    # temporarily attaches LogCaptureHandlers to this logger; treating
    # those as ours both suppresses normal output and makes setup depend
    # on whether capture happens to be active.
    if any(getattr(handler, "_aura_application_handler", False)
           for handler in logger.handlers):
        return logger

    handler = RichHandler(
        rich_tracebacks=True,
        show_path=False,
    )
    setattr(handler, "_aura_application_handler", True)

    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)

    logger.addHandler(handler)
    logger.addFilter(SecretMaskingFilter())

    return logger


logger = setup_logger()


def apply_config_level(config: dict | None) -> int:
    """
    Apply `logging.level` from a loaded config dict.

    Called by the composition root once a config exists, which is the
    only moment both the file and the environment are known. Returns the
    level in force afterwards, so a caller can report it.
    """

    if os.getenv(LOG_LEVEL_ENV):
        # Set explicitly by whoever started the process. It outranks the
        # file, and saying so once is more useful than silently ignoring
        # a config value the user may be watching.
        logger.debug(
            "%s is set; ignoring logging.level from config",
            LOG_LEVEL_ENV,
        )
        return logger.level

    configured = ((config or {}).get("logging") or {}).get("level")

    level = resolve_level(configured)

    if level is None:

        if configured is not None:
            logger.warning(
                "Unreadable logging level %r; staying at %s",
                configured,
                logging.getLevelName(logger.level),
            )

        return logger.level

    logger.setLevel(level)

    return level
