"""
Aura Core Application
"""

from core.logger import logger
from core.config import load_config

from brain.chat_engine import ChatEngine
from brain.response import Response


class Aura:

    def __init__(self, engine: ChatEngine | None = None):
        self.config = load_config()

        # ChatEngine is the composition root: it owns memory,
        # the prompt builder and the LLM router.
        self.engine = engine or ChatEngine()

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
