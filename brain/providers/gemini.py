"""
Gemini Provider.
"""

import os

from dotenv import load_dotenv
from google import genai

from core.config import load_config
from core.logger import logger
from brain.providers.base import BaseProvider
from brain.providers.errors import ProviderRateLimitError, ProviderUnavailableError


# The finish reasons that mean "this reply is not the whole reply".
# Read from the response rather than inferred from its length, because a
# short answer and a truncated one are otherwise indistinguishable.
_TRUNCATED = "MAX_TOKENS"


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

        # Gemini 3 thinks before it answers, and those thoughts are billed
        # against `max_output_tokens`. Left unset, the model decides how
        # much to think and the answer gets whatever is left - which on
        # Aura's 768-token budget was measured at 59-78 tokens for an
        # ordinary question, arriving mid-sentence. See the config key.
        self.thinking_level = str(
            config["llm"].get("thinking_level") or ""
        ).strip().lower()

    def _request_config(self, budget: bool = True) -> dict:
        """
        The generation config, so both paths ask for the same thinking.

        `budget` is False for streaming, which has never sent
        `max_output_tokens` and does not start now: a streamed reply is
        already on the user's screen when the budget runs out, so a cap
        there would truncate in front of them. The thinking level applies
        to both, because unbounded reasoning delays the first token of a
        stream exactly as it eats the budget of a whole reply.
        """

        config: dict = {}

        if budget:
            config["max_output_tokens"] = self.max_output_tokens
            config["temperature"] = self.temperature

        if self.thinking_level:
            config["thinking_config"] = {
                "thinking_level": self.thinking_level,
            }

        return config

    def generate(self, prompt: str) -> str:
        from brain.providers.base import split_prompt_to_messages
        system_instruction, canonical_messages = split_prompt_to_messages(prompt)

        contents = []
        for msg in canonical_messages:
            role = "user" if msg.role == "user" else "model"
            contents.append({"role": role, "parts": [{"text": msg.content}]})

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config={
                    **self._request_config(),
                    "system_instruction": system_instruction or None,
                },
            )

        except Exception as error:
            self._raise_cloud_error(error)

        # Gemini returns None when a response is blocked or empty.
        # Providers must honour the `-> str` contract, so normalise here
        # rather than letting None leak into the pipeline and the database.
        text = response.text or ""

        self._check_truncation(response, text)

        return text

    def _check_truncation(self, response, text: str) -> None:
        """
        Say out loud when the budget ran out before the answer did.

        `response.text` carries no sign of this: a reply cut off after
        four words and a reply that finished in four words are the same
        string. So the model can spend the whole output budget thinking,
        return nothing, and the pipeline stores an empty assistant turn
        as a successful one - no exception, no log, no failover, and a
        conversation that reads as though Aura got worse.

        An empty truncated reply is raised as unavailable, because that is
        what it is: this provider produced no answer, and the fallback
        chain is entitled to try the next one. A partial reply is kept -
        some of the answer beats none - and logged as the warning it is.
        """

        reason = ""

        for candidate in getattr(response, "candidates", None) or []:
            finish = getattr(candidate, "finish_reason", None)
            reason = str(getattr(finish, "name", finish) or "")
            break

        if reason != _TRUNCATED:
            return

        usage = getattr(response, "usage_metadata", None)
        thoughts = getattr(usage, "thoughts_token_count", None) or 0

        if not text.strip():
            raise ProviderUnavailableError(
                f"Gemini returned no answer: the {self.max_output_tokens}"
                f"-token output budget was spent before any reply was "
                f"written ({thoughts} of it on reasoning). Raise "
                f"llm.max_output_tokens or lower llm.thinking_level."
            )

        logger.warning(
            "Gemini reply truncated at llm.max_output_tokens=%s "
            "(%s tokens went to reasoning). The user is reading an "
            "unfinished sentence; raise the budget or lower "
            "llm.thinking_level.",
            self.max_output_tokens,
            thoughts,
        )

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
        from brain.providers.base import split_prompt_to_messages
        system_instruction, canonical_messages = split_prompt_to_messages(prompt)

        contents = []
        for msg in canonical_messages:
            role = "user" if msg.role == "user" else "model"
            contents.append({"role": role, "parts": [{"text": msg.content}]})

        stream = self.client.models.generate_content_stream(
            model=self.model,
            contents=contents,
            config={
                **self._request_config(budget=False),
                "system_instruction": system_instruction or None,
            }
        )

        for piece in stream:

            text = getattr(piece, "text", None)

            if text:
                yield text

    def generate_with_tools(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
    ):
        """
        One round with tools offered natively via the Gemini OpenAI-compatible endpoint.
        """
        import json
        import urllib.request
        from brain.native_fc import extract_turn

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ProviderUnavailableError("GEMINI_API_KEY not found in .env")

        base_url = os.getenv("GEMINI_BASE_URL") or "https://generativelanguage.googleapis.com/v1beta/openai"
        url = f"{base_url.rstrip('/')}/chat/completions"

        wire_messages = []
        if system:
            wire_messages.append({"role": "system", "content": system})
        wire_messages.extend(messages)

        payload = {
            "model": self.model,
            "messages": wire_messages,
            "max_tokens": self.max_output_tokens,
            "tools": tools,
            "tool_choice": "auto",
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Aura/1.0",
            "Connection": "close",
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return extract_turn(data["choices"][0]["message"])
        except Exception as error:
            self._raise_cloud_error(error)


