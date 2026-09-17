"""
Local Model Provider for AURA 2.0 (Phase 5B).

Enables local, offline, and self-hosted inference (Ollama /v1, vLLM, LM Studio,
llama.cpp server, LocalAI) through the same OpenAI chat-completions interface
used by cloud providers, supporting native function calling, streaming, and vision.
"""

from brain.providers.openai_compatible import OpenAICompatibleProvider


class LocalProvider(OpenAICompatibleProvider):
    """
    Local model provider connecting to an HTTP inference engine running on the local host
    or private network.
    """

    provider_name = "local"
    label = "Local Inference Server"

    api_key_env = "LOCAL_API_KEY"
    base_url_env = "LOCAL_BASE_URL"
    default_url = "http://127.0.0.1:11434/v1"
    default_model = "qwen2.5:7b"
