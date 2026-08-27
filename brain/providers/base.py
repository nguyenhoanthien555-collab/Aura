"""
Base class for all LLM providers.
"""

from abc import ABC, abstractmethod
import re


class BaseProvider(ABC):

    @abstractmethod
    def generate(self, prompt: str) -> str:
        """
        Generate a response from the prompt.
        """
        ...


def split_prompt(prompt: str) -> tuple[str, str]:
    """
    Split the prompt into system instruction and user content.

    System instruction gathers SYSTEM, PERSONALITY, IDENTITY, and STYLE sections.
    User content contains the remaining sections (CONTEXT, MEMORY, VISION, HISTORY, USER, etc.).

    The split is by what a section *is*, not where it sits: an
    instruction telling the model how to answer belongs in the system
    slot, and everything the model is answering *about* - the transcript,
    the screen, the accessibility tree - belongs in the user slot. That
    is why AGENT RULES and INTENT RULES are here and DEVICE STATE is not.

    TOOLS and TOOL RESULTS divide along the same line, and they divide:
    the catalogue of what may be requested is an instruction, while what
    running one actually produced is evidence about this turn.
    """
    # Match any section header: "===== [A-Z_ ]+ ====="
    pattern = re.compile(r"^(===== [A-Z_ ]+ =====)\s*$", re.MULTILINE)

    parts = pattern.split(prompt)

    system_sections = []
    user_sections = []

    system_headers = {
        "===== SYSTEM =====",
        "===== PERSONALITY =====",
        "===== PERSONA =====",
        "===== WHO YOU ARE =====",
        "===== RESPONSE STYLE =====",
        "===== AGENT RULES =====",
        "===== INTENT RULES =====",
        "===== TOOLS =====",
        "===== LIVE CAPABILITIES =====",
        "===== CAPABILITIES =====",
    }

    if parts[0].strip():
        user_sections.append(parts[0].strip())

    i = 1
    while i < len(parts):
        header = parts[i].strip()
        content = parts[i+1].strip() if i+1 < len(parts) else ""

        if header in system_headers:
            system_sections.append(f"{header}\n{content}")
        else:
            user_sections.append(f"{header}\n{content}")
        i += 2

    system_prompt = "\n\n".join(system_sections)
    user_prompt = "\n\n".join(user_sections)
    return system_prompt, user_prompt


def split_prompt_to_messages(prompt: str):
    """
    Split the prompt into system instruction and canonical Message objects.

    System instruction gathers standing rules, personality, context, memory facts,
    vision, identity, style, tools, and agent/intent rules into the system slot.

    Canonical messages parse previous dialogue turns from RECENT CONVERSATION,
    tool results, and the current user message into discrete Message(role, content)
    objects. The current user message is always the final user turn.
    """
    from brain.message import Message

    pattern = re.compile(r"^(===== [A-Z_ ]+ =====)\s*$", re.MULTILINE)
    parts = pattern.split(prompt)

    system_sections = []
    history_content = ""
    tool_results_content = ""
    user_content = ""

    system_headers = {
        "===== SYSTEM =====",
        "===== PERSONALITY =====",
        "===== PERSONA =====",
        "===== WHO YOU ARE =====",
        "===== RESPONSE STYLE =====",
        "===== AGENT RULES =====",
        "===== INTENT RULES =====",
        "===== TOOLS =====",
        "===== LIVE CAPABILITIES =====",
        "===== CAPABILITIES =====",
        "===== CONTEXT =====",
        "===== CURRENT TIME =====",
        "===== MEMORY =====",
        "===== VISION =====",
        "===== DESKTOP STATE =====",
        "===== DEVICE STATE =====",
        "===== ACCESSIBILITY TREE =====",
        "===== LAST ACTION ERROR =====",
    }

    if len(parts) == 1:
        user_content = parts[0].strip()
    else:
        if parts[0].strip():
            system_sections.append(parts[0].strip())

        i = 1
        while i < len(parts):
            header = parts[i].strip()
            content = parts[i + 1].strip() if i + 1 < len(parts) else ""

            if header in ("===== RECENT CONVERSATION =====", "===== HISTORY ====="):
                history_content = content
            elif header in ("===== CURRENT USER MESSAGE =====", "===== USER ====="):
                user_content = content
            elif header == "===== TOOL RESULTS =====":
                tool_results_content = content
            elif header in system_headers:
                if content:
                    system_sections.append(f"{header}\n{content}")
                else:
                    system_sections.append(header)
            else:
                if content:
                    system_sections.append(f"{header}\n{content}")
                else:
                    system_sections.append(header)
            i += 2

    system_instruction = "\n\n".join(system_sections)


    messages: list[Message] = []

    if history_content and history_content != "(No previous conversation)":
        current_role = None
        current_lines = []
        for line in history_content.splitlines():
            if line.startswith("user: "):
                if current_role and current_lines:
                    messages.append(Message(role=current_role, content="\n".join(current_lines).strip()))
                current_role = "user"
                current_lines = [line[6:]]
            elif line.startswith("assistant: "):
                if current_role and current_lines:
                    messages.append(Message(role=current_role, content="\n".join(current_lines).strip()))
                current_role = "assistant"
                current_lines = [line[11:]]
            else:
                if current_role is not None:
                    current_lines.append(line)
        if current_role and current_lines:
            messages.append(Message(role=current_role, content="\n".join(current_lines).strip()))


    if tool_results_content:
        messages.append(Message(role="user", content=f"===== TOOL RESULTS =====\n{tool_results_content}"))

    if user_content:
        messages.append(Message(role="user", content=user_content))
    elif not messages:
        messages.append(Message(role="user", content=""))

    return system_instruction, messages