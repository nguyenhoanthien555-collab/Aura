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

    def stream(self, prompt: str):
        """
        The same reply, yielded as it is generated.

        Optional capability, found by `brain.streaming.can_stream` rather
        than declared on the LLM protocol - a provider that only has
        `generate` must remain a valid LLM, and widening that protocol
        would break every one that does.

        Empty pieces are skipped. Gemini emits them around safety
        annotations and at the end of a stream, and a consumer counting
        chunks should not see them as fragments of a reply.
        """

        stream = self.client.models.generate_content_stream(
            model=self.model,
            contents=prompt,
        )

        for piece in stream:

            text = getattr(piece, "text", None)

            if text:
                yield text