"""
Qwen, through Alibaba Cloud's OpenAI-compatible endpoint.

DashScope publishes two APIs. This uses the compatibility one -
`/compatible-mode/v1/chat/completions` - because it is the same wire format
as every other provider here, so Qwen costs one small file instead of a
second client.

WHICH REGION, AND WHY THAT IS A SETTING
---------------------------------------
DashScope has separate international and Beijing endpoints, and an API key
issued in one region does not work against the other. The international
host is the default because it is the one reachable from the platforms
`docs/DEPLOYMENT.md` targets; `QWEN_BASE_URL` switches it, and either the
API root or the full endpoint URL is accepted.

    Beijing:  https://dashscope.aliyuncs.com/compatible-mode/v1

ONE VARIABLE, NOT TWO
---------------------
The key is read from `QWEN_API_KEY`, following the `{PROVIDER}_API_KEY`
convention the whole package uses, and `DASHSCOPE_API_KEY` is deliberately
not read as an alias. `brain.router.PROVIDER_KEYS` maps a provider to
exactly one variable, and `core/credentials.py` uses that mapping to decide
whether a key is configured, where it came from and what to unset on
delete. A second accepted name would make all three of those answers
wrong for the case that matters - a key present under the other name.

Not verified against the live API - this deployment has no Qwen key.
"""

from brain.providers.openai_compatible import OpenAICompatibleProvider


class QwenProvider(OpenAICompatibleProvider):

    provider_name = "qwen"
    label = "Qwen"

    api_key_env = "QWEN_API_KEY"
    base_url_env = "QWEN_BASE_URL"

    default_url = (
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"
    )

    default_model = "qwen-plus"
