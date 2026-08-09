"""OpenRouter cloud provider using its OpenAI-compatible chat endpoint."""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv

from brain.providers.base import BaseProvider
from brain.providers.errors import ProviderRateLimitError, ProviderUnavailableError


DEFAULT_URL = "https://openrouter.ai/api/v1/chat/completions"


def _failure(status: int, body: str, retry_after: str | None = None):
    message = f"OpenRouter HTTP {status}"
    if status == 429:
        try:
            wait = float(retry_after) if retry_after else None
        except ValueError:
            wait = None
        return ProviderRateLimitError(message, retry_after=wait)
    if status >= 500 or status in (408, 409):
        return ProviderUnavailableError(message)
    return RuntimeError(message)


class OpenRouterProvider(BaseProvider):
    provider_name = "openrouter"

    def __init__(self, model: str, timeout: float = 45.0, max_tokens: int = 768):
        load_dotenv()
        self.api_key = os.getenv("OPENROUTER_API_KEY")
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY is not configured")
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.url = os.getenv("OPENROUTER_BASE_URL", DEFAULT_URL)

    def generate(self, prompt: str) -> str:
        data = self._request([{"role": "user", "content": prompt}])
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as error:
            raise ProviderUnavailableError("OpenRouter returned an invalid response") from error

    def _request(self, messages: list) -> dict:
        payload = json.dumps({
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
        }).encode("utf-8")
        request = Request(
            self.url,
            data=payload,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            raise _failure(error.code, error.read().decode("utf-8", "replace"), error.headers.get("Retry-After")) from error
        except (URLError, TimeoutError) as error:
            raise ProviderUnavailableError("OpenRouter is unreachable") from error
