"""
Prompt builder.
"""

from brain.message import Message
from brain.ports import VisionContextLike

from brain.system import SystemPrompt
from brain.personality import Personality
from brain.context_loader import ContextLoader

from brain.agent_mode import is_agent_tick, is_intent_probe

from brain.prompt_sections import (
    ACCESSIBILITY_TREE,
    AGENT_RULES,
    DEVICE_STATE,
    INTENT_RULES,
    LAST_ACTION_ERROR,
    SYSTEM,
    PERSONALITY,
    CONTEXT,
    TIME,
    MEMORY,
    VISION,
    HISTORY,
    USER,
    IDENTITY,
    STYLE,
    TOOLS,
    TOOL_RESULTS,
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


    def _build_time(self, temporal=None):
        """
        When "now" is.

        `temporal` arrives as an already-rendered list of lines, exactly
        as `knowledge` does - the builder never sees a datetime, never
        formats one, and never calls a clock. That last point is what
        makes a prompt reproducible in a test: the time in it came from
        the caller's clock, so a pinned clock produces a pinned prompt.

        Above MEMORY because a recalled line dated "yesterday" cannot be
        read without knowing today's date, and a model that has to guess
        the date will confidently invent one.
        """

        if not temporal:
            return []

        section = [TIME]

        for line in temporal:

            text = str(line).strip()

            if text:
                section.append(text)

        if len(section) == 1:
            return []

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


    def _build_tools(self, tools: str | None = None):
        """
        What Aura can actually do this turn, and how to ask for it.

        `tools` arrives as finished text from `ToolRunner.catalogue()`,
        which is `describe_tool` over the tools policy currently allows -
        the same description the registry and the CLI show. The builder
        never sees the registry, never learns what a tool is, and no tool
        is described twice.

        Absent when nothing is available, and that is the ordinary case:
        with no tools the prompt is byte-identical to the one Aura built
        before tool calling existed.
        """

        text = (tools or "").strip()

        if not text:
            return []

        return [
            TOOLS,
            "You can run these tools:\n"
            f"\n{text}\n"
            "\n"
            "To run one, reply with nothing but a JSON object:\n"
            '{"tool": "<name>", "arguments": {"<name>": "<value>"}}\n'
            "\n"
            "You will then be shown what it actually returned, and you "
            "write your reply to the user after that.\n"
            "\n"
            "Never tell the user you have done something unless you have "
            "been shown the result of doing it. If a tool fails, say so "
            "plainly - do not describe the outcome you intended.\n"
            "\n"
            "Most messages need no tool at all. Reply normally when none "
            "is needed.",
        ]


    def _build_tool_results(self, tool_results: list[str] | None = None):
        """
        What running a tool actually produced.

        The only thing in this prompt entitled to make Aura say an action
        succeeded. Lines arrive already rendered from the conversation
        layer, which built them from a real ToolResult - a failure is
        written as plainly as a success, because a model shown nothing
        about a failed call will describe the call it meant to make.
        """

        if not tool_results:
            return []

        section = [TOOL_RESULTS]

        for line in tool_results:

            text = str(line).strip()

            if text:
                section.append(text)

        if len(section) == 1:
            return []

        return section


    def _build_agent_prompt(
        self,
        user_message: Message,
        context: dict,
    ) -> str:
        """
        One step of the Android device agent.

        Deliberately not the conversational prompt with a section bolted
        on the end. The reply to this is a JSON object that an
        accessibility service parses and executes, so every section that
        exists to make Aura sound like herself - SYSTEM, PERSONALITY,
        MEMORY, VISION, the transcript, the identity anchor, the response
        style - is absent rather than overridden.

        Leaving them in is what produced a prompt asking for warm
        conversational prose and raw JSON in the same breath, and a model
        that reasonably answered with prose (AURA-P0-007). An instruction
        the model has to choose between is worse than one it never saw.

        The persona survives in exactly one place: the `message` field of
        the `complete` action, which is the only text here a person ever
        reads.
        """

        import json

        device = context.get("device") or {}
        app = context.get("app") or {}
        tree = context.get("accessibility_tree") or {}
        user_req = context.get("user_request") or user_message.content

        prompt: list[str] = []

        # 1. Device state
        device_state_lines = []
        if app.get("package"):
            device_state_lines.append(f"Package: {app['package']}")
        if app.get("activity"):
            device_state_lines.append(f"Activity: {app['activity']}")
        if device.get("width") and device.get("height"):
            device_state_lines.append(
                f"Dimensions: {device['width']}x{device['height']}"
            )

        if device_state_lines:
            prompt.extend([
                DEVICE_STATE,
                "\n".join(device_state_lines),
            ])

        # 2. Accessibility tree
        prompt.extend([
            ACCESSIBILITY_TREE,
            json.dumps(tree, indent=2),
        ])

        # 2.5 Why the last step did not work. The agent's only channel for
        # learning from a failed action, a repeated no-op, or a reply it
        # could not parse.
        if context.get("last_action_error"):
            prompt.extend([
                LAST_ACTION_ERROR,
                context["last_action_error"],
            ])

        # 3. Agent rules
        prompt.extend([
            AGENT_RULES,
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


    def _build_intent_prompt(self, user_message: Message) -> str:
        """
        One question: does this message want something done, or said?

        Asked of the model rather than answered by a keyword table on the
        phone, because the alternative fails on the first sentence it was
        not written for. "mở youtube", "put on some music", "can you get
        me into settings" and "what's the weather" have no shared surface
        feature to key on, and a list that grew to cover them would still
        be wrong in the next language somebody types.

        The reply is one word, read by `agent_mode.read_intent`, which
        treats anything ambiguous as conversation.
        """

        return "\n\n".join([
            INTENT_RULES,
            "You are the intent router for an assistant that can operate "
            "the user's Android phone.\n"
            "Classify the user's message below into exactly one label.\n"
            "\n"
            "action        The user is asking you to DO something on the "
            "phone: open or launch an app, tap something, type something, "
            "search inside an app, scroll, play something, or change a "
            "setting.\n"
            "conversation  Anything else: greetings, small talk, questions, "
            "requests for information or an opinion, or anything you can "
            "answer by replying.\n"
            "\n"
            "The message may be in any language. Judge what the user wants "
            "to happen, not which words they used.\n"
            "\n"
            "If you are not sure, answer conversation.\n"
            "\n"
            "Answer with exactly one word: action or conversation.",
            USER,
            user_message.content,
        ])


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
        tools: str | None = None,
        tool_results: list[str] | None = None,
        temporal: list[str] | None = None,
    ):
        """
        Render the full prompt.

        Section order is fixed:
            SYSTEM, PERSONALITY, TOOLS, CONTEXT, TIME, MEMORY, VISION,
            HISTORY, TOOL RESULTS, IDENTITY, STYLE, USER

        IDENTITY and STYLE sit between the history and the user's message
        on purpose: they are short restatements, put where recency makes
        a model most likely to still be following them.

        TIME sits immediately above MEMORY because recalled lines are
        dated relatively - "yesterday", "last week" - and those words
        mean nothing without today's date directly above them.

        TOOLS is up with the other standing instructions; TOOL RESULTS
        sits after the transcript, because it is what happened during this
        turn rather than a rule about how to answer. `split_prompt` sends
        them to different slots for the same reason.

        Every section except HISTORY and USER is omitted entirely when it
        has nothing to say, so an unused subsystem costs zero tokens - a
        prompt built without tools is byte-identical to one built before
        tool calling existed.

        A turn carrying a machine context - a device snapshot, or an
        intent probe - is not this prompt at all. It routes to a prompt
        built for a parser, with none of the sections above; see
        `brain/agent_mode.py`.
        """

        if is_agent_tick(context):
            return self._build_agent_prompt(user_message, context)

        if is_intent_probe(context):
            return self._build_intent_prompt(user_message)

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
            self._build_tools(tools)
        )


        prompt.extend(
            self._build_contexts(contexts)
        )


        prompt.extend(
            self._build_time(temporal)
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
            self._build_tool_results(tool_results)
        )


        prompt.extend(
            self._build_identity(identity)
        )


        prompt.extend(
            self._build_style(style)
        )


        prompt.extend(
            self._build_user(user_message)
        )


        return "\n\n".join(prompt)