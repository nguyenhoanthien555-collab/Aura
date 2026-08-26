"""
The owner's own endpoint.

Every other provider in this package is a vendor: a name, one URL this
file knows, one key variable. That is right for Anthropic and useless for
the endpoints people actually run - an AgentRouter deployment, a company
proxy, LiteLLM in front of five vendors, vLLM or llama.cpp or LM Studio on
the owner's own machine. All of them speak OpenAI's chat-completions
format; none of them has an address that could be written down here.

So this provider declares the dialect and nothing else. The endpoint comes
from `llm.custom_base_url` or `CUSTOM_BASE_URL`, the model from
`llm.custom_model`, the key from `CUSTOM_API_KEY`, and there is no default
for any of the three.

WHY THERE IS NO DEFAULT URL
---------------------------
A placeholder would resolve, answer 404, and read to the owner exactly
like an outage at a provider they never configured. `requires_base_url`
makes the absence a named precondition instead: the router skips the
provider and says which setting is empty.

WHY THE MODEL IS REQUIRED TOO
-----------------------------
Same reason, one layer along. `{"model": ""}` earns a 400 from most
gateways, and "Custom endpoint HTTP 400" tells the owner nothing. The
router refuses to build the provider until `llm.custom_model` names
something, so the message is about the setting rather than about the
status code.

WHY THE KEY CANNOT REACH ANOTHER VENDOR
---------------------------------------
Because it is read here, from `CUSTOM_API_KEY`, and `self.url` is resolved
in the same constructor from the custom endpoint settings. Nothing in the
request path consults the model *name*. An owner may point this at a
gateway that proxies Anthropic and set `llm.custom_model` to
`claude-sonnet-5`; the request still goes to their gateway with their key,
because the name is a string in the payload and never an address. That is
a structural property of this file, not a rule someone has to remember -
`tests/test_custom_endpoint.py` pins it.

AGENTROUTER
-----------
The repository documents no AgentRouter endpoint or dialect, and inventing
one would produce a provider that fails in a way indistinguishable from an
outage. If AgentRouter speaks OpenAI chat-completions - as its
compatibility claims suggest - it needs no code at all: the owner sets
`llm.provider = custom`, pastes their base URL and key, and names their
model. The one external input still required is that base URL.
"""

from brain.providers.openai_compatible import OpenAICompatibleProvider


class CustomProvider(OpenAICompatibleProvider):

    provider_name = "custom"
    label = "Custom endpoint"

    api_key_env = "CUSTOM_API_KEY"
    base_url_env = "CUSTOM_BASE_URL"

    # Deliberately empty, both of them. See the module docstring.
    default_url = ""
    default_model = ""

    requires_base_url = True
