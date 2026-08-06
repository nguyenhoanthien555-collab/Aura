"""
Gemini Provider.
"""

import os

from dotenv import load_dotenv
from google import genai

from core.config import load_config
from brain.providers.base import BaseProvider


class GeminiProvider(BaseProvider):

    def __init__(self):

        load_dotenv()

        config = load_config()

        api_key = os.getenv("GEMINI_API_KEY")

        if not api_key:
            raise ValueError("GEMINI_API_KEY not found in .env")

        self.client = genai.Client(api_key=api_key)

        self.model = config["llm"]["model"]

    def generate(self, prompt: str) -> str:

        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
        )

        # Gemini returns None when a response is blocked or empty.
        # Providers must honour the `-> str` contract, so normalise here
        # rather than letting None leak into the pipeline and the database.
        return response.text or ""