"""
ChatGPT Main Brain Provider for Aura.

Uses OpenAI's Chat Completions REST API (https://api.openai.com/v1/chat/completions)
with native tool calling, streaming, and reasoning tiers (e.g. gpt-4o, gpt-4o-mini, o1, o3-mini).

IMPORTANT ARCHITECTURAL DISCLOSURE:
  This provider communicates directly with OpenAI's developer API using an OPENAI_API_KEY.
  It is NOT a web browser automation wrapper for ChatGPT Web (chatgpt.com) or consumer
  ChatGPT Desktop application sessions.

Configured with:
  OPENAI_API_KEY: Standard OpenAI API key.
  OPENAI_BASE_URL: Optional custom gateway or proxy URL (defaults to official API).
  llm.chatgpt_model: Override default model (defaults to gpt-4o).

Zero external dependencies: uses Python urllib and standard JSON libraries,
matching the hermetic deployment architecture of Aura.
"""

from typing import Any
from brain.providers.errors import ProviderUnavailableError
from brain.providers.openai_compatible import OpenAICompatibleProvider


class ChatGPTProvider(OpenAICompatibleProvider):
    """
    ChatGPT Main Brain Provider for Aura.
    """

    provider_name = "chatgpt"
    label = "ChatGPT"

    api_key_env = "OPENAI_API_KEY"
    base_url_env = "OPENAI_BASE_URL"

    default_url = "https://api.openai.com/v1/chat/completions"

    # Default to production flagship gpt-4o for robust reasoning, planning, and coding
    default_model = "gpt-4o"

    # OpenAI reasoning models use max_completion_tokens; standard models accept it too
    token_field = "max_completion_tokens"

    @property
    def is_reasoning_model(self) -> bool:
        """True if model belongs to OpenAI reasoning series (o1, o3, etc.)."""
        model_lower = (self.model or "").lower()
        return model_lower.startswith(("o1", "o3"))

    def _payload(self, system_instruction: str, user_content: Any) -> dict:
        """
        Build request payload, adapting system instructions and parameters
        for reasoning models.
        """
        payload = super()._payload(system_instruction, user_content)
        if self.is_reasoning_model:
            # 1. Reasoning models reject custom temperature
            payload.pop("temperature", None)
            # 2. Reasoning models use "developer" role instead of "system"
            messages = payload.get("messages")
            if isinstance(messages, list):
                for msg in messages:
                    if isinstance(msg, dict) and msg.get("role") == "system":
                        msg["role"] = "developer"
        return payload

    def _send(self, payload: dict) -> dict:
        """
        Ensure reasoning model adaptations are applied before wire dispatch.
        """
        if self.is_reasoning_model:
            payload.pop("temperature", None)
            messages = payload.get("messages")
            if isinstance(messages, list):
                for msg in messages:
                    if isinstance(msg, dict) and msg.get("role") == "system":
                        msg["role"] = "developer"
        return super()._send(payload)

    def generate_with_tools(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
    ) -> Any:
        """
        Generate turn with native tools, enforcing model capability constraints.
        """
        model_lower = (self.model or "").lower()
        if model_lower in ("o1-preview", "o1-mini"):
            raise ProviderUnavailableError(
                f"OpenAI reasoning model '{self.model}' does not support tools/function calling. "
                "Use 'o1', 'o3-mini', or 'gpt-4o'."
            )
        return super().generate_with_tools(system, messages, tools)
