"""
DeepSeek.

OpenAI-compatible: `https://api.deepseek.com/v1/chat/completions`, bearer
auth, the same shapes.

This file closes AURA-P2-004 from the other direction. That audit item was
"DEEPSEEK_API_KEY appears in a real .env and no code reads it", and the
Phase 5 answer was correct at the time: a key must never conjure a
provider, so the key was documented as inert rather than guessed at. Phase
11 was asked for DeepSeek support, so the provider now exists and the key
is read *because there is an implementation behind it* - which is the
invariant that mattered, not the absence.

`deepseek-reasoner` emits a separate `reasoning_content` field alongside
`content`. Only `content` is read: the reasoning trace is not the reply,
and putting it in the transcript would show the user the model thinking out
loud and then store it as something Aura said.

Not verified against the live API - this deployment has no DeepSeek key.
"""

from brain.providers.openai_compatible import OpenAICompatibleProvider


class DeepSeekProvider(OpenAICompatibleProvider):

    provider_name = "deepseek"
    label = "DeepSeek"

    api_key_env = "DEEPSEEK_API_KEY"
    base_url_env = "DEEPSEEK_BASE_URL"

    default_url = "https://api.deepseek.com/v1/chat/completions"

    default_model = "deepseek-chat"
