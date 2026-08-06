"""
Personality loader.
"""

from core.paths import PROMPTS_DIR


class Personality:

    def __init__(self):
        self.path = PROMPTS_DIR / "personality.md"

    def load(self) -> str:
        if not self.path.exists():
            return ""

        return self.path.read_text(
            encoding="utf-8"
        )