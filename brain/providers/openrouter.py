"""OpenRouter cloud provider using its OpenAI-compatible chat endpoint."""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv

from brain.providers.base import BaseProvider
from brain.providers.errors import ProviderRateLimitError, ProviderUnavailableError


DEFAULT_URL = "https://openrouter.ai/api/v1/chat/completions"


def _failure(status: int, body: str, retry_after: str | None = None, model: str = "unknown", headers: dict = None):
    message = f"OpenRouter HTTP {status}"
    if status == 429:
        try:
            wait = float(retry_after) if retry_after else None
        except ValueError:
            wait = None

        is_account_limit = False
        err_code = 429
        try:
            err_data = json.loads(body)
            err_code = err_data.get("error", {}).get("code", 429)
            err_msg = err_data.get("error", {}).get("message", "").lower()
            err_msg_orig = err_data.get("error", {}).get("message", body)
        except Exception:
            err_msg = body.lower()
            err_msg_orig = body

        providers = ["google/", "nvidia/", "cohere/", "meta-llama/", "openai/", "anthropic/", "mistral/", "microsoft/"]
        has_provider = any(p in err_msg for p in providers)

        if "free-models-per-day" in err_msg:
            is_account_limit = True
        elif "slow down" in err_msg or "daily limit" in err_msg or "key limit" in err_msg or "account limit" in err_msg or "quota" in err_msg:
            if not has_provider:
                is_account_limit = True
        elif "limit_rpd" in err_msg or "limit_rpm" in err_msg:
            if not has_provider:
                is_account_limit = True

        if not err_msg or (not has_provider and "limit_" in err_msg):
            is_account_limit = True

        if headers:
            limit_val = headers.get("X-RateLimit-Limit") or headers.get("x-ratelimit-limit")
            rem_val = headers.get("X-RateLimit-Remaining") or headers.get("x-ratelimit-remaining")
            try:
                if limit_val and rem_val and int(limit_val) == 50 and int(rem_val) == 0:
                    is_account_limit = True
            except (ValueError, TypeError):
                pass

        classification = "account-level" if is_account_limit else "model/provider-level"

        safe_headers_dict = {}
        if headers:
            for k in ["X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset", "Retry-After"]:
                val = headers.get(k) or headers.get(k.lower())
                if val:
                    safe_headers_dict[k] = val

        from core.logger import logger
        logger.warning(
            "OpenRouter 429 Rate Limited - Model: %s, Status: %d, Code: %s, Message: %s, Headers: %s, Classification: %s",
            model,
            status,
            err_code,
            err_msg_orig,
            json.dumps(safe_headers_dict),
            classification
        )

        return ProviderRateLimitError(message, retry_after=wait, is_account_limit=is_account_limit)
    if status >= 500 or status in (408, 409):
        return ProviderUnavailableError(message)
    return RuntimeError(message)


class OpenRouterProvider(BaseProvider):
    provider_name = "openrouter"
    supports_text = True
    supports_vision = False

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
        models_to_try = [self.model]
        if self.model == "openrouter/free":
            models_to_try = [
                "google/gemma-4-31b-it:free",
                "nvidia/nemotron-nano-12b-v2-vl:free",
                "google/gemma-4-26b-a4b-it:free",
                "openrouter/free",
            ]
        elif self.model in ("google/gemma-4-31b-it:free", "nvidia/nemotron-nano-12b-v2-vl:free", "google/gemma-4-26b-a4b-it:free"):
            models_to_try = [self.model] + [m for m in [
                "google/gemma-4-31b-it:free",
                "nvidia/nemotron-nano-12b-v2-vl:free",
                "google/gemma-4-26b-a4b-it:free",
            ] if m != self.model]

        last_error = None
        for model in models_to_try:
            try:
                from brain.providers.base import split_prompt
                system_instruction, user_content = split_prompt(prompt)
                messages = []
                if system_instruction:
                    messages.append({"role": "system", "content": system_instruction})
                messages.append({"role": "user", "content": user_content})

                original_model = self.model
                self.model = model
                data = self._request(messages)
                self.model = original_model

                try:
                    return data["choices"][0]["message"]["content"] or ""
                except (KeyError, IndexError, TypeError) as error:
                    raise ProviderUnavailableError("OpenRouter returned an invalid response") from error
            except ProviderRateLimitError as error:
                if getattr(error, "is_account_limit", False):
                    raise
                last_error = error
                continue
            except ProviderUnavailableError as error:
                last_error = error
                continue

        if last_error:
            raise last_error
        raise ProviderUnavailableError("All eligible free models failed")

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
            raise _failure(
                error.code,
                error.read().decode("utf-8", "replace"),
                error.headers.get("Retry-After"),
                model=self.model,
                headers=error.headers
            ) from error
        except (URLError, TimeoutError) as error:
            raise ProviderUnavailableError("OpenRouter is unreachable") from error
