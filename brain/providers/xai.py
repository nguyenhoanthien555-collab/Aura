"""
xAI (Grok).

OpenAI-compatible: `https://api.x.ai/v1/chat/completions`, bearer auth, the
same request and response shape, so this file is a name and four strings.

Not verified against the live API - this deployment has no xAI key. The
default model is what xAI documents as current; `llm.xai_model` overrides
it and is free text for exactly the reason a hardcoded list is not.
"""

from brain.providers.openai_compatible import OpenAICompatibleProvider


class XAIProvider(OpenAICompatibleProvider):

    provider_name = "xai"
    label = "xAI"

    api_key_env = "XAI_API_KEY"
    base_url_env = "XAI_BASE_URL"

    default_url = "https://api.x.ai/v1/chat/completions"

    default_model = "grok-4"
