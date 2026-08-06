"""
Chat engine.

This is the single entry point for all interactions with Aura.

Composition root:
    ChatEngine builds MemoryManager, PromptBuilder, and BrainRouter,
    then injects them into ConversationManager. Nothing else creates
    these objects.

Sprint 5 added three optional collaborators - an event publisher, a
vision provider and a knowledge provider. All default to None, so
`ChatEngine()` still produces exactly the Sprint 4 pipeline. The
launcher is what supplies them.
"""

from brain.conversation import ConversationManager
from brain.ports import (
    ConversationStore,
    EventPublisher,
    KnowledgeProvider,
    LLM,
    VisionProvider,
)
from brain.prompt_builder import PromptBuilder
from brain.response import Response
from brain.router import BrainRouter
from core.config import load_config
from memory.manager import MemoryManager


class ChatEngine:

    def __init__(
        self,
        memory: ConversationStore | None = None,
        builder: PromptBuilder | None = None,
        llm: LLM | None = None,
        events: EventPublisher | None = None,
        vision: VisionProvider | None = None,
        knowledge: KnowledgeProvider | None = None,
    ):
        """
        Create the chat engine with dependency injection.

        Defaults create production components; tests can inject fakes.
        """

        if memory is None:
            memory = MemoryManager()

        if builder is None:
            builder = PromptBuilder()

        if llm is None:
            llm = BrainRouter()

        self.conversation = ConversationManager(
            memory=memory,
            builder=builder,
            llm=llm,
            history_limit=self._history_limit(),
            events=events,
            vision=vision,
            knowledge=knowledge,
        )

    @staticmethod
    def _history_limit() -> int:
        """
        Read the history limit from config, tolerating a missing or
        empty `memory:` section.
        """

        config = load_config() or {}

        memory_config = config.get("memory") or {}

        return memory_config.get("history_limit", 20)

    def chat(
        self,
        message: str,
        contexts: list[str] | None = None,
        source: str = "text",
    ) -> Response:
        """
        Process a user message and return the assistant's reply.

        `source` records how the message arrived ("text" or "voice") and
        is carried on the published UserInputEvent.
        """
        return self.conversation.chat(message, contexts, source=source)
