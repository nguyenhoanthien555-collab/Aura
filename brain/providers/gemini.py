"""
Gemini Provider.
"""

import os

from dotenv import load_dotenv
from google import genai

from core.config import load_config
from brain.providers.base import BaseProvider
from brain.providers.errors import ProviderRateLimitError, ProviderUnavailableError


class GeminiProvider(BaseProvider):
    provider_name = "gemini"
    supports_text = True
    supports_vision = True

    def __init__(self, model: str | None = None):

        load_dotenv()

        config = load_config()

        api_key = os.getenv("GEMINI_API_KEY")

        if not api_key:
            raise ValueError("GEMINI_API_KEY not found in .env")

        self.client = genai.Client(api_key=api_key)

        self.model = model or config["llm"]["model"]
        self.max_output_tokens = int(config["llm"].get("max_output_tokens", 768))
        self.temperature = float(config["llm"].get("temperature", 0.7))

    def generate(self, prompt: str) -> str:

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config={
                    "max_output_tokens": self.max_output_tokens,
                    "temperature": self.temperature,
                },
            )
        except Exception as error:
            self._raise_cloud_error(error)

        # Gemini returns None when a response is blocked or empty.
        # Providers must honour the `-> str` contract, so normalise here
        # rather than letting None leak into the pipeline and the database.
        return response.text or ""

    @staticmethod
    def _raise_cloud_error(error):
        status = getattr(error, "code", None) or getattr(error, "status_code", None)
        text = str(error).lower()
        if status == 429 or "resource_exhausted" in text or "quota" in text:
            raise ProviderRateLimitError("Gemini quota/rate limit reached") from error
        if (isinstance(status, int) and status >= 500) or any(word in text for word in ("unavailable", "timeout", "connection")):
            raise ProviderUnavailableError("Gemini is unavailable") from error
        raise error

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
