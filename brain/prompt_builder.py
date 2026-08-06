"""
Prompt builder.
"""

from brain.message import Message
from brain.ports import VisionContextLike

from brain.system import SystemPrompt
from brain.personality import Personality
from brain.context_loader import ContextLoader

from brain.prompt_sections import (
    SYSTEM,
    PERSONALITY,
    CONTEXT,
    MEMORY,
    VISION,
    HISTORY,
    USER,
)


class PromptBuilder:

    def __init__(
        self,
        system: SystemPrompt | None = None,
        personality: Personality | None = None,
        context_loader: ContextLoader | None = None,
    ):

        self.system = system or SystemPrompt()
        self.personality = personality or Personality()
        self.context_loader = context_loader or ContextLoader()


    def _build_system(self):

        text = self.system.load()

        if not text:
            return []

        return [
            SYSTEM,
            text,
        ]


    def _build_personality(self):

        text = self.personality.load()

        if not text:
            return []

        return [
            PERSONALITY,
            text,
        ]


    def _build_contexts(self, contexts):

        loaded = self.context_loader.load(contexts)

        if not loaded:
            return []

        section = [CONTEXT]

        section.extend(loaded)

        return section


    def _build_memory(self, knowledge=None):
        """
        Facts recalled about the user.

        `knowledge` arrives as already-rendered lines. The builder never
        sees rows, embeddings or scores - retrieval decides what is worth
        remembering, this only decides where it goes in the prompt.
        """

        if not knowledge:
            return []

        section = [MEMORY]

        for line in knowledge:

            text = str(line).strip()

            if text:
                section.append(f"- {text}")

        if len(section) == 1:
            return []

        return section


    def _build_vision(self, vision: VisionContextLike | None = None):
        """
        What Aura can currently see.

        Reads only `.source` and `.description`, so any object with that
        shape works and brain/ never imports vision/.
        """

        if vision is None:
            return []

        description = getattr(vision, "description", "")

        if not description:
            return []

        source = getattr(vision, "source", "unknown")

        return [
            VISION,
            f"[{source}] {description}",
        ]


    def _build_history(self, history: list[Message]):
        """
        Render history in reading order.

        `history` arrives oldest-first. Ordering is the caller's
        responsibility (see ConversationManager.history), so nothing
        is re-sorted here.
        """

        section = [HISTORY]

        if not history:
            section.append("(No previous conversation)")
            return section

        for msg in history:

            section.append(
                f"{msg.role}: {msg.content}"
            )

        return section


    def _build_user(self, user_message: Message):

        return [
            USER,
            user_message.content,
        ]


    def build(
        self,
        history: list[Message],
        user_message: Message,
        contexts=None,
        vision: VisionContextLike | None = None,
        knowledge: list[str] | None = None,
    ):
        """
        Render the full prompt.

        Section order is fixed:
            SYSTEM, PERSONALITY, CONTEXT, MEMORY, VISION, HISTORY, USER

        Every section except HISTORY and USER is omitted entirely when it
        has nothing to say, so an unused subsystem costs zero tokens.
        """

        if contexts is None:
            contexts = []


        prompt = []


        prompt.extend(
            self._build_system()
        )


        prompt.extend(
            self._build_personality()
        )


        prompt.extend(
            self._build_contexts(contexts)
        )


        prompt.extend(
            self._build_memory(knowledge)
        )


        prompt.extend(
            self._build_vision(vision)
        )


        prompt.extend(
            self._build_history(history)
        )


        prompt.extend(
            self._build_user(user_message)
        )


        return "\n\n".join(prompt)