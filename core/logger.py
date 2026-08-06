"""
Aura Logger
Centralized logging configuration.
"""

import logging
from rich.logging import RichHandler


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("Aura")

    # Tránh thêm handler nhiều lần nếu import nhiều nơi
    if logger.hasHandlers():
        return logger

    logger.setLevel(logging.INFO)

    handler = RichHandler(
        rich_tracebacks=True,
        show_path=False,
    )

    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)

    logger.addHandler(handler)

    return logger


logger = setup_logger()