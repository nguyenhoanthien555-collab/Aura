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