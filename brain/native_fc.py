"""
Native function calling - the types.

The old protocol asked the model for a JSON action string and hoped a
parser could recover an intent from whatever came back. Native function
calling replaces that hope with an API contract: the platform declares
each tool's schema up front, the provider returns tool calls as structured
fields of the reply object, and the arguments arrive as data rather than
as text to be excavated.

This module holds the vocabulary both sides of that contract share. It
imports nothing from providers or tools, so it can be read by either
without creating a cycle.
"""

import json
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolCallRequest:
    """
    One tool call, as the model asked for it.

    `call_id` is the provider's own correlation id (OpenAI-style), echoed
    back in the next request so the API can pair results with requests.
    The agent runtime mints its own ids as well; both travel, because the
    provider's id means nothing to the phone and ours means nothing to
    the provider.
    """

    call_id: str
    name: str
    arguments: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ModelTurn:
    """
    What one round of the model produced.

    Exactly one of the two fields is meaningful:

        text        a final answer; the loop can converge on this once
                    verification agrees
        tool_calls  zero or more calls to execute before asking again

    Both empty is a degenerate turn and is treated as an error by the
    runtime, not silently accepted as completion - silence was how the
    old loop stalled to its step ceiling.
    """

    text: str = ""
    tool_calls: tuple[ToolCallRequest, ...] = ()

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)

    @property
    def is_empty(self) -> bool:
        return not self.text.strip() and not self.tool_calls


def parse_tool_call(raw_call: dict) -> ToolCallRequest | None:
    """
    One raw provider tool-call object as a ToolCallRequest, or None when
    it cannot be read at all.

    Arguments are decoded leniently: a provider that hands back invalid
    JSON in `arguments` still yields a request with empty arguments,
    because executing the named tool with no arguments produces a
    structured INVALID_ARGUMENTS failure the model can correct - which is
    strictly better than discarding the round.
    """

    function = raw_call.get("function") or {}

    name = str(function.get("name") or "").strip()
    call_id = str(raw_call.get("id") or "")

    if not name:
        return None

    raw_arguments = function.get("arguments")

    if isinstance(raw_arguments, dict):
        arguments = raw_arguments
    elif isinstance(raw_arguments, str) and raw_arguments.strip():
        try:
            decoded = json.loads(raw_arguments)
            arguments = decoded if isinstance(decoded, dict) else {}
        except ValueError:
            arguments = {}
    else:
        arguments = {}

    return ToolCallRequest(
        call_id=call_id, name=name, arguments=arguments, raw=raw_call
    )


def extract_turn(message: dict) -> ModelTurn:
    """
    A provider reply message as a ModelTurn.

    Reads both shapes a compliant endpoint can answer with: content text,
    tool_calls, or both at once (a model may narrate and call).
    """

    text = str(message.get("content") or "")
    calls = []

    for raw_call in message.get("tool_calls") or []:
        parsed = parse_tool_call(raw_call)

        if parsed is not None:
            calls.append(parsed)

    return ModelTurn(text=text, tool_calls=tuple(calls))
