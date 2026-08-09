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
        pass


def split_prompt(prompt: str) -> tuple[str, str]:
    """
    Split the prompt into system instruction and user content.

    System instruction gathers SYSTEM, PERSONALITY, IDENTITY, and STYLE sections.
    User content contains the remaining sections (CONTEXT, MEMORY, VISION, HISTORY, USER, etc.).
    """
    # Match any section header: "===== [A-Z_ ]+ ====="
    pattern = re.compile(r"^(===== [A-Z_ ]+ =====)\s*$", re.MULTILINE)

    parts = pattern.split(prompt)

    system_sections = []
    user_sections = []

    system_headers = {
        "===== SYSTEM =====",
        "===== PERSONALITY =====",
        "===== WHO YOU ARE =====",
        "===== RESPONSE STYLE ====="
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