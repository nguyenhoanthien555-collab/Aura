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
from rich.logging import RichHandler


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

    # Avoid attaching the handler twice when imported from several
    # modules.
    if logger.hasHandlers():
        return logger

    level = resolve_level(os.getenv(LOG_LEVEL_ENV))

    logger.setLevel(DEFAULT_LEVEL if level is None else level)

    handler = RichHandler(
        rich_tracebacks=True,
        show_path=False,
    )

    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)

    logger.addHandler(handler)

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
