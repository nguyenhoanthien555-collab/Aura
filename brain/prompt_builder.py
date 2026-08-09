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
    IDENTITY,
    STYLE,
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


    def _build_identity(self, identity: str | None = None):
        """
        Who Aura is, restated after the transcript.

        Arrives finished from the consistency layer, exactly as `style`
        does, so the builder imports neither and a caller with no guard
        pays nothing. Empty for a short conversation, which is why this
        section is absent from most prompts rather than merely short.
        """

        text = (identity or "").strip()

        if not text:
            return []

        return [
            IDENTITY,
            text,
        ]


    def _build_style(self, style: str | None = None):
        """
        How this particular reply should be written.

        Arrives as a finished string from the style layer, so the builder
        never imports it and a caller with no styler pays nothing.
        """

        text = (style or "").strip()

        if not text:
            return []

        return [
            STYLE,
            text,
        ]


    def build(
        self,
        history: list[Message],
        user_message: Message,
        contexts=None,
        vision: VisionContextLike | None = None,
        knowledge: list[str] | None = None,
        identity: str | None = None,
        style: str | None = None,
        context: dict | None = None,
    ):
        """
        Render the full prompt.

        Section order is fixed:
            SYSTEM, PERSONALITY, CONTEXT, MEMORY, VISION, HISTORY,
            IDENTITY, STYLE, USER

        IDENTITY and STYLE sit between the history and the user's message
        on purpose: they are short restatements, put where recency makes
        a model most likely to still be following them.

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
            self._build_identity(identity)
        )


        prompt.extend(
            self._build_style(style)
        )


        if context and ("accessibility_tree" in context or "device" in context):
            device = context.get("device") or {}
            app = context.get("app") or {}
            tree = context.get("accessibility_tree") or {}
            user_req = context.get("user_request") or user_message.content

            # 1. Device State
            device_state_lines = []
            if app.get("package"):
                device_state_lines.append(f"Package: {app['package']}")
            if app.get("activity"):
                device_state_lines.append(f"Activity: {app['activity']}")
            if device.get("width") and device.get("height"):
                device_state_lines.append(f"Dimensions: {device['width']}x{device['height']}")

            if device_state_lines:
                prompt.extend([
                    "===== DEVICE STATE =====",
                    "\n".join(device_state_lines)
                ])

            # 2. Accessibility Tree
            import json
            prompt.extend([
                "===== ACCESSIBILITY TREE =====",
                json.dumps(tree, indent=2)
            ])

            # 2.5 Last Action Error
            if context.get("last_action_error"):
                prompt.extend([
                    "===== LAST ACTION ERROR =====",
                    context["last_action_error"]
                ])

            # 3. Agent Rules
            prompt.extend([
                "===== AGENT RULES =====",
                f'You are operating in Android Agentic Jarvis mode.\n'
                f'The user has requested: "{user_req}".\n'
                f'Based on the current device state and accessibility tree, decide the next action.\n'
                f'If the task is complete, or you cannot proceed further, output a "complete" action:\n'
                f'{{\n'
                f'  "action": "complete",\n'
                f'  "message": "<friendly response in Gen-Z style completing the request>"\n'
                f'}}\n'
                f'Otherwise, output exactly one action from the supported set in JSON format:\n'
                f'{{\n'
                f'  "action": "click" | "long_click" | "input_text" | "clear_text" | "scroll" | "scroll_screen" | "back" | "home" | "open_notifications" | "open_quick_settings" | "open_app" | "focus",\n'
                f'  "node_id": "<node_id>",\n'
                f'  "text": "<text to input>",\n'
                f'  "direction": "up" | "down" | "left" | "right",\n'
                f'  "package": "<app package to open>"\n'
                f'}}\n'
                f'Only output the raw JSON block without any conversational text.'
            ])

        prompt.extend(
            self._build_user(user_message)
        )


        return "\n\n".join(prompt)