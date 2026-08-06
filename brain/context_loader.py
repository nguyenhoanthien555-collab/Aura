"""
Context prompt loader.
"""

from core.paths import CONTEXTS_DIR


class ContextLoader:

    def __init__(self):
        self.base_path = CONTEXTS_DIR

    def load(self, contexts: list[str]) -> list[str]:

        prompts = []

        for context in contexts:

            file = self.base_path / f"{context}.md"

            if file.exists():
                prompts.append(
                    file.read_text(encoding="utf-8")
                )

        return prompts