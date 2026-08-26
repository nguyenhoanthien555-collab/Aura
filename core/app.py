"""
Aura Core Application
"""

from core.logger import logger
from core.config import load_config
from core.temporal import TemporalClock

from brain.chat_engine import ChatEngine
from brain.response import Response


class Aura:

    def __init__(self, engine: ChatEngine | None = None):
        self.config = load_config()

        # ChatEngine is the composition root: it owns memory,
        # the prompt builder and the LLM router.
        #
        # The clock is handed in rather than defaulted inside the engine.
        # `ChatEngine` leaves `clock` as None on purpose, so that a bare
        # engine stays byte-for-byte the Sprint 4 prompt pipeline that a
        # good number of tests depend on; the consequence is that every
        # faculty gated on it arrives from a composition root or not at
        # all. Until this was here, `python main.py` held a whole
        # conversation whose prompt had no CURRENT TIME section, and a
        # model with no date in its prompt does not decline to answer
        # "what day is it" - it invents one (section 16).
        #
        # Built from the config this root already loaded, so
        # `temporal.timezone` is honoured here exactly as it is under the
        # server. A second `load_config()` would work today and drift the
        # first time one of them is passed an override.
        self.engine = engine or ChatEngine(
            clock=TemporalClock.from_config(self.config)
        )

    def chat(self, message: str) -> Response:
        """
        Send a message to Aura and get her reply.
        """
        return self.engine.chat(message)

    def start(self):
        logger.info("=" * 40)
        logger.info(
            f"{self.config['app']['name']} v{self.config['app']['version']}"
        )
        logger.info("Starting Aura...")
        logger.info("Configuration loaded.")

        logger.info("Memory initialized.")

        logger.info(
            "LLM provider: "
            + getattr(
                self.engine.conversation.llm,
                "provider_name",
                "unknown",
            )
        )

        logger.info("Aura is ready!")
        logger.info("=" * 40)
