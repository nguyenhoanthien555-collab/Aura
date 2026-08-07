"""
Mock LLM Provider.
"""

from brain.providers.base import BaseProvider


class MockProvider(BaseProvider):

    def generate(self, prompt: str) -> str:

        print("\n========== PROMPT ==========\n")
        print(prompt)
        print("\n============================\n")

        return "Mock response generated."

    def stream(self, prompt: str):
        """
        The same reply, one word at a time.

        Here so the streaming path can be exercised end to end without a
        network, an API key or a real model. Words rather than characters
        because that is roughly the granularity a real provider emits,
        and it makes a chunk count in a test mean something.
        """

        text = self.generate(prompt)

        pieces = text.split(" ")

        for index, word in enumerate(pieces):
            yield word if index == len(pieces) - 1 else word + " "
