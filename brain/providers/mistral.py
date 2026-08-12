import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv

from brain.providers.base import BaseProvider
from brain.providers.errors import ProviderRateLimitError, ProviderUnavailableError

DEFAULT_URL = "https://api.mistral.ai/v1/chat/completions"

def _failure(status: int, body: str, retry_after: str | None = None):
    message = f"Mistral HTTP {status}"
    if status == 429:
        try:
            wait = float(retry_after) if retry_after else None
        except ValueError:
            wait = None

        is_account_limit = False
        try:
            err_data = json.loads(body)
            err_msg = err_data.get("message", "").lower()
        except Exception:
            err_msg = body.lower()

        if "quota" in err_msg or "daily" in err_msg or "account" in err_msg or "monthly" in err_msg:
            is_account_limit = True

        return ProviderRateLimitError(message, retry_after=wait, is_account_limit=is_account_limit)
    if status == 401 or status == 403:
        return ValueError("Mistral API authentication failed")
    if status >= 500 or status in (408, 409):
        return ProviderUnavailableError(message)
    return RuntimeError(message)

class MistralProvider(BaseProvider):
    provider_name = "mistral"
    supports_text = True
    supports_vision = False

    def __init__(self, model: str = "mistral-small-latest", timeout: float = 45.0, max_tokens: int = 768):
        load_dotenv()
        self.api_key = os.getenv("MISTRAL_API_KEY")
        if not self.api_key:
            raise ValueError("MISTRAL_API_KEY is not configured")
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.url = os.getenv("MISTRAL_BASE_URL", DEFAULT_URL)

    def generate(self, prompt: str) -> str:
        from brain.providers.base import split_prompt_to_messages
        system_instruction, canonical_messages = split_prompt_to_messages(prompt)
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        for msg in canonical_messages:
            messages.append({"role": msg.role, "content": msg.content})

        data = self._request(messages)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as error:
            raise ProviderUnavailableError("Mistral returned an invalid response") from error

    def _request(self, messages: list) -> dict:
        payload = json.dumps({
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
        }).encode("utf-8")
        request = Request(
            self.url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            raise _failure(
                error.code,
                error.read().decode("utf-8", "replace"),
                error.headers.get("Retry-After")
            ) from error
        except (URLError, TimeoutError) as error:
            raise ProviderUnavailableError("Mistral is unreachable") from error

    def stream(self, prompt: str):
        from brain.providers.base import split_prompt_to_messages
        system_instruction, canonical_messages = split_prompt_to_messages(prompt)
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        for msg in canonical_messages:
            messages.append({"role": msg.role, "content": msg.content})


        payload = json.dumps({
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "stream": True
        }).encode("utf-8")
        request = Request(
            self.url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                for line in response:
                    line = line.decode("utf-8").strip()
                    if not line:
                        continue
                    if line.startswith("data:"):
                        data_str = line[5:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            text = data["choices"][0]["delta"].get("content", "")
                            if text:
                                yield text
                        except Exception:
                            pass
        except HTTPError as error:
            raise _failure(
                error.code,
                error.read().decode("utf-8", "replace"),
                error.headers.get("Retry-After")
            ) from error
        except (URLError, TimeoutError) as error:
            raise ProviderUnavailableError("Mistral is unreachable") from error
