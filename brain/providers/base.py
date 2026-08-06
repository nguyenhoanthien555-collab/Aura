"""
Base class for all LLM providers.
"""

from abc import ABC, abstractmethod


class BaseProvider(ABC):

    @abstractmethod
    def generate(self, prompt: str) -> str:
        """
        Generate a response from the prompt.
        """
        pass