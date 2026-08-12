"""
Anthropic Claude, through `/v1/messages`.

The one new provider in Phase 11 that is not OpenAI-compatible, so it
subclasses `HttpChatProvider` and stops there. Four differences, each of
which would be a silent failure if this file inherited the OpenAI format
instead:

    auth        `x-api-key` and `anthropic-version`, not a bearer token
    system      a top-level `system` field, not a message with that role
    max_tokens  required, so it is not droppable
    response    a list of content blocks, not `choices[0].message`

No `anthropic` package. urllib and json, like the rest of the package, so
this added nothing to `requirements.txt`.

THE API VERSION IS PINNED ON PURPOSE
------------------------------------
`anthropic-version: 2023-06-01` is a required header and Anthropic's
versioning contract is that a pinned date keeps its behaviour. Reading it
from the environment would make the request shape that
`tests/test_cloud_providers.py` pins depend on the deploy environment,
which is the opposite of what a version pin is for.

TEMPERATURE IS CLAMPED, NOT REJECTED
------------------------------------
`llm.temperature` accepts 0.0-2.0 because that is OpenAI's range;
Anthropic's ceiling is 1.0 and a higher value is a 400. A setting the
Control Hub allowed must not turn every Claude reply into an error, so it
is clamped here and the clamp is tested. Nothing else rewrites a user's
setting silently - this is the exception, and the alternative is a request
that cannot succeed.

Not verified against the live API: this deployment has no Anthropic key.
"""

import json
from typing import Iterator

from brain.providers.base import split_prompt
from brain.providers.http_chat import HttpChatProvider


class AnthropicProvider(HttpChatProvider):

    provider_name = "anthropic"
    label = "Anthropic"

    api_key_env = "ANTHROPIC_API_KEY"
    base_url_env = "ANTHROPIC_BASE_URL"

    default_url = "https://api.anthropic.com/v1/messages"
    endpoint_path = "/messages"

    # `llm.anthropic_model` overrides this and is free text.
    default_model = "claude-sonnet-5"

    API_VERSION = "2023-06-01"

    # Temperature only. `max_tokens` is a required field on this API, so
    # dropping it on a 400 would replace one error with another.
    droppable = ("temperature",)

    # Anthropic's documented maximum.
    MAX_TEMPERATURE = 1.0

    def _headers(self) -> dict:
        """`x-api-key`, not `Authorization` - the base class default is wrong here."""

        return {
            "x-api-key": self.api_key,
            "anthropic-version": self.API_VERSION,
            "Content-Type": "application/json",
        }

    def _payload(self, system_instruction: str, user_content) -> dict:
        """
        The instruction half in `system`, the content half in `messages`.

        This is the difference that matters for Aura specifically: the
        device-action boundary in `prompts/system.md` is an instruction, and
        on this API instructions belong in the top-level `system` field.
        Sent as a `{"role": "system"}` message they would be rejected
        outright - Anthropic's roles are `user` and `assistant` only.

        `system` is omitted when empty rather than sent as "".
        """

        messages = []
        if isinstance(user_content, list):
            for msg in user_content:
                messages.append({"role": msg.role, "content": msg.content})
        else:
            messages.append({"role": "user", "content": str(user_content)})

        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": messages,
        }

        if system_instruction:
            payload["system"] = system_instruction

        if self.temperature is not None:
            payload["temperature"] = min(self.temperature, self.MAX_TEMPERATURE)

        return payload


    def _extract(self, data: dict) -> str:
        """
        The text blocks, joined.

        `content` is a list because a reply can carry more than text -
        `thinking` blocks on the reasoning models, `tool_use` blocks. Only
        `type == "text"` is read: a thinking block is the model working, not
        the answer, and putting it in the transcript would show the user
        Claude reasoning out loud and then store it as something Aura said.
        """

        blocks = data["content"]

        return "".join(
            str(block.get("text") or "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        )

    def stream(self, prompt: str) -> Iterator[str]:
        """
        The same reply, yielded as it is written.

        Anthropic's event stream is typed, and the type is inside the `data`
        payload as well as in the `event:` line, so only `data:` needs
        parsing. `text_delta` is yielded; `thinking_delta` is not, for the
        reason `_extract` gives.
        """

        system_instruction, user_content = split_prompt(prompt)

        payload = dict(
            self._payload(system_instruction, user_content), stream=True
        )

        with self._open(payload) as response:

            for raw in response:

                line = raw.decode("utf-8", "replace").strip()

                if not line.startswith("data:"):
                    # `event:` lines and the blank separators. The type is
                    # repeated in the payload, so nothing is lost.
                    continue

                chunk = line[5:].strip()

                try:
                    event = json.loads(chunk)
                except Exception:
                    # An unfamiliar or truncated event is not a failed
                    # reply; a real transport failure raises from the
                    # iteration itself.
                    continue

                if not isinstance(event, dict):
                    continue

                if event.get("type") == "message_stop":
                    break

                if event.get("type") != "content_block_delta":
                    continue

                delta = event.get("delta")

                if not isinstance(delta, dict):
                    continue

                if delta.get("type") != "text_delta":
                    continue

                text = delta.get("text") or ""

                if text:
                    yield text
