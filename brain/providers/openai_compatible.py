"""
The OpenAI chat-completions wire format, once.

Most hosted models now speak it: OpenAI itself, xAI, DeepSeek, Cerebras,
Alibaba's DashScope compatibility endpoint, Groq, Mistral, OpenRouter and
almost every self-hosted gateway. A provider that speaks it needs to
declare five strings and nothing else, which is the whole point - the
provider files in this package are meant to be too small to hide a bug in.

    POST {base}/chat/completions
    {"model": …, "messages": [{"role": "system"|"user", "content": …}], …}
    -> {"choices": [{"message": {"content": …}}]}

Streaming is the same request with `stream: true`, answered as
server-sent events carrying `choices[0].delta.content`.

Anthropic is NOT here. `/v1/messages` takes `system` as a top-level field,
authenticates with `x-api-key`, requires `max_tokens` and answers with a
list of content blocks. It shares `HttpChatProvider` and nothing else -
see `brain/providers/anthropic.py`.
"""

import json
from typing import Iterator

from brain.providers.base import split_prompt
from brain.providers.http_chat import HttpChatProvider


class OpenAICompatibleProvider(HttpChatProvider):
    """
    A provider that speaks OpenAI's chat-completions format.

    Subclasses set `provider_name`, `label`, `api_key_env`, `base_url_env`,
    `default_url` and `default_model`. Everything below is shared and is
    the same code for all of them, which means a fix to any of it is a fix
    to all of them.
    """

    endpoint_path = "/chat/completions"

    # Which field carries the output cap. OpenAI's reasoning models renamed
    # it and reject the old spelling; every other compatible endpoint knows
    # only the old one. So the default is the portable spelling and
    # `OpenAIProvider` overrides it - and if either guess is wrong for a
    # given model, `_retry_payload` swaps them once rather than failing.
    token_field = "max_tokens"

    ALTERNATE_TOKEN_FIELD = {
        "max_tokens": "max_completion_tokens",
        "max_completion_tokens": "max_tokens",
    }

    droppable = ("temperature", "max_tokens", "max_completion_tokens")

    def _payload(self, system_instruction: str, user_content: str) -> dict:
        """
        The two prompt halves as a system message and a user message.

        The system message is omitted entirely when there is nothing to put
        in it. An empty system message is not free: some models treat it as
        an instruction to be terse.
        """

        messages = []

        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})

        messages.append({"role": "user", "content": user_content})

        payload = {
            "model": self.model,
            "messages": messages,
            self.token_field: self.max_tokens,
        }

        if self.temperature is not None:
            payload["temperature"] = self.temperature

        return payload

    def _extract(self, data: dict) -> str:
        """
        The reply text. `None` content becomes "", never None.

        Providers return a null `content` for a filtered or empty
        completion, and the `-> str` contract is what keeps that out of the
        transcript and the database.
        """

        return data["choices"][0]["message"]["content"] or ""

    def _retry_payload(self, payload: dict, parameter: str) -> dict | None:
        """
        Rename a refused token field; otherwise drop the refused field.

        Dropping the cap would let the model answer at its own maximum
        length, which on a phone screen is a different bug rather than a
        fix, so the two spellings are tried before anything is given up.
        """

        alternate = self.ALTERNATE_TOKEN_FIELD.get(parameter)

        if alternate and parameter in payload:
            retry = dict(payload)
            retry[alternate] = retry.pop(parameter)
            return retry

        return super()._retry_payload(payload, parameter)

    def stream(self, prompt: str) -> Iterator[str]:
        """
        The same reply, yielded as it is written.

        An optional capability found by `brain.streaming.can_stream`, not
        part of the LLM protocol - see `brain/streaming.py`. Because this
        lives on the shared class, every provider built from it streams,
        and `PROVIDER_CAPABILITIES` in `server/routes/settings.py` may
        honestly say so.

        A chunk that carries no text - the opening `{"role": "assistant"}`
        delta, a keep-alive comment, a usage summary - is skipped rather
        than yielded as an empty fragment.
        """

        system_instruction, user_content = split_prompt(prompt)

        payload = dict(
            self._payload(system_instruction, user_content), stream=True
        )

        with self._open(payload) as response:

            for raw in response:

                line = raw.decode("utf-8", "replace").strip()

                if not line.startswith("data:"):
                    continue

                chunk = line[5:].strip()

                if chunk == "[DONE]":
                    break

                try:
                    delta = json.loads(chunk)["choices"][0]["delta"]
                    text = delta.get("content") or ""
                except Exception:
                    # A malformed or unfamiliar event is not a failed
                    # reply. The stream continues; a real transport failure
                    # raises from the iteration itself.
                    continue

                if text:
                    yield text
