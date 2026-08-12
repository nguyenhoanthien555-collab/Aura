"""
Cerebras. Registered in Phase 11, after the defect that kept it out.

HISTORY, BECAUSE THE ABSENCE WAS DELIBERATE
-------------------------------------------
This file sat here unregistered from Phase 5 to Phase 11 (AURA-P2-003).
Naming `cerebras` in `fallback_providers` got it skipped as "unknown
provider" rather than used, and that was the correct outcome at the time,
because its `generate` sent the whole prompt as a single user message
instead of splitting it. Aura's prompt is built to be split - the system
slot carries the instructions, including the device-action boundary from
`prompts/system.md` - and sent unsplit those instructions arrive as
ordinary conversational text. The old docstring called that "a real defect,
not a style difference", and it listed what had to be true before the
provider could be wired:

    1. `generate` must call `split_prompt` like its siblings.
    2. There must be failover tests.

Both now hold, and not by editing this file's own copy of the request
logic: the whole client is `OpenAICompatibleProvider`, where `generate`
splits the prompt for every provider in the package and a subclass cannot
opt out. The second precondition is `tests/test_cloud_providers.py`, which
pins the split, the payload, the auth header and the error classification.

The third thing the old docstring said is still true and is not a
precondition: nobody has ever run this against the live API, because this
deployment has no Cerebras key. Registration means Aura will build it and
try it when configured, which is what `POST /api/providers/test` is for.
"""

from brain.providers.openai_compatible import OpenAICompatibleProvider


class CerebrasProvider(OpenAICompatibleProvider):

    provider_name = "cerebras"
    label = "Cerebras"

    api_key_env = "CEREBRAS_API_KEY"
    base_url_env = "CEREBRAS_BASE_URL"

    default_url = "https://api.cerebras.ai/v1/chat/completions"

    # Unchanged from the unregistered version, so registering it did not
    # quietly also change which model it asks for.
    default_model = "llama-3.3-70b"
