"""
Conversation manager.

Owns one turn of the conversation:

    "hello"  ->  Message  ->  history  ->  PromptBuilder
             ->  LLM.generate()  ->  Response  ->  saved

It receives every dependency through the constructor and knows nothing
about providers, storage engines, UI, TTS or vision.

Optional collaborators (events, vision, knowledge) all default to None.
With none of them supplied this behaves exactly as it did in Sprint 4.

The events it publishes are facts ("the user said X", "a reply arrived"),
never UI state. Deriving a display state from those facts is the avatar's
job, so there is exactly one owner of that state.
"""

from brain.adapters import records_to_messages
from brain.message import Message
from brain.ports import (
    ConversationStore,
    EventPublisher,
    KnowledgeProvider,
    LLM,
    VisionProvider,
)
from brain.prompt_builder import PromptBuilder
from brain.response import Response

from events.types import (
    ErrorEvent,
    ResponseEvent,
    ThinkingEvent,
    UserInputEvent,
)


DEFAULT_HISTORY_LIMIT = 20


class ConversationManager:

    def __init__(
        self,
        memory: ConversationStore,
        builder: PromptBuilder,
        llm: LLM,
        history_limit: int = DEFAULT_HISTORY_LIMIT,
        events: EventPublisher | None = None,
        vision: VisionProvider | None = None,
        knowledge: KnowledgeProvider | None = None,
    ):

        self.memory = memory
        self.builder = builder
        self.llm = llm
        self.history_limit = history_limit
        self.events = events
        self.vision = vision
        self.knowledge = knowledge


    def chat(
        self,
        user_message: str,
        contexts: list[str] | None = None,
        source: str = "text",
    ) -> Response:
        """
        Process a user message and return the assistant's reply.
        """

        user_msg = Message(
            role="user",
            content=user_message,
        )

        self._emit(
            UserInputEvent(text=user_msg.content, source=source)
        )

        history = self.history()

        prompt = self.builder.build(
            history=history,
            user_message=user_msg,
            contexts=contexts or [],
            vision=self._vision_context(),
            knowledge=self._knowledge_for(user_msg.content),
        )

        self._emit(ThinkingEvent())

        try:
            response = Response(
                text=self.llm.generate(prompt)
            )

        except Exception as error:
            # Announce the failure and let the caller decide what to do.
            # Swallowing it here would hide provider outages behind an
            # empty reply.
            self._emit(
                ErrorEvent(message=str(error), source="llm")
            )
            raise

        # Persist only after a successful generation, so a provider
        # failure never leaves a user turn stranded without a reply.
        self.memory.save(
            user_msg.role,
            user_msg.content,
        )

        self.memory.save(
            "assistant",
            response.text,
        )

        self._emit(ResponseEvent(text=response.text))

        return response


    def history(self, limit: int | None = None) -> list[Message]:
        """
        Recent conversation as pipeline Messages, oldest first.

        The store returns newest-first; the flip happens here so the
        ordering contract is resolved at the boundary rather than
        being re-derived by every consumer.
        """

        if limit is None:
            limit = self.history_limit

        records = self.memory.get_recent(limit)

        messages = records_to_messages(records)

        messages.reverse()

        return messages


    # ------------------------------------------------------------------
    # Optional collaborators
    #
    # Each is wrapped so that a broken subsystem degrades the reply
    # instead of breaking the conversation.
    # ------------------------------------------------------------------

    def _emit(self, event) -> None:
        if self.events is None:
            return

        try:
            self.events.publish(event)
        except Exception:
            pass

    def _vision_context(self):
        if self.vision is None:
            return None

        try:
            return self.vision.get_context()
        except Exception:
            return None

    def _knowledge_for(self, query: str) -> list[str]:
        if self.knowledge is None:
            return []

        try:
            return self.knowledge.get_knowledge(query) or []
        except Exception:
            return []
