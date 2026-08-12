"""
OpenAI.

Registered as `openai` and reachable from `llm.provider` or
`llm.fallback_providers`. `docs/DEPLOYMENT.md` listed "OpenAI not wired" as
a known limitation from Phase 7 until Phase 11; the placeholder file that
used to sit here was empty, which is the part that had to change.

No `openai` package. This posts JSON with urllib like every other provider
in the package, so adding OpenAI support added no dependency to the deploy.

TWO OPENAI-SPECIFIC FACTS, BOTH HANDLED IN THE SHARED CLASS
-----------------------------------------------------------
The reasoning models (`gpt-5*`, `o*`) renamed `max_tokens` to
`max_completion_tokens` and reject an explicit `temperature`. Rather than
matching model-name patterns - a guess that ages badly, and this file
cannot know what OpenAI ships next month - the request is sent with the
newer spelling and `HttpChatProvider._send` repairs a refusal once, using
the field name the provider itself named. So a temperature set in the
Control Hub is honoured by the models that support it and silently ignored
by the ones that do not, instead of turning every reply into a 400.

Not verified against the live API: this deployment has no OpenAI key. The
request shape is the documented one and is pinned by
`tests/test_cloud_providers.py`.
"""

from brain.providers.openai_compatible import OpenAICompatibleProvider


class OpenAIProvider(OpenAICompatibleProvider):

    provider_name = "openai"
    label = "OpenAI"

    api_key_env = "OPENAI_API_KEY"
    base_url_env = "OPENAI_BASE_URL"

    default_url = "https://api.openai.com/v1/chat/completions"

    # `llm.openai_model` overrides this and is free text, because a model
    # list hardcoded here would reject one released this morning.
    default_model = "gpt-5.1"

    # The spelling the current API wants. Swapped for `max_tokens` on
    # refusal, which is what makes an OpenAI-compatible gateway behind
    # OPENAI_BASE_URL work too.
    token_field = "max_completion_tokens"
