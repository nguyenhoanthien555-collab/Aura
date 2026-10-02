"""
ChatGPT Main Brain Provider.

Uses the OpenAI Chat Completions API with native tool calling, streaming,
and reasoning tiers (e.g. gpt-4o, gpt-4o-mini, o1, o3-mini).

Configured with:
  OPENAI_API_KEY: Standard OpenAI API key.
  OPENAI_BASE_URL: Optional custom gateway or proxy URL (defaults to official API).
  llm.chatgpt_model: Override default model (defaults to gpt-4o).

Zero external dependencies: uses Python urllib and standard JSON libraries,
matching the hermetic deployment architecture of Aura.
"""

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
