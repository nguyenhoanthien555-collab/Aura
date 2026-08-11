"""
Reading a tool request out of a reply.

No provider Aura talks to has a native function-calling API - `BrainRouter`
and every provider under it take a rendered prompt and return a string
(brain/router.py). So a tool request travels as text, and this module is
where that text is read. It is the Python counterpart of
`AgentActionParser` on the phone, and it exists for the same reason: a
model that answers correctly but wraps it in a code fence must not have
its answer thrown away.

Three outcomes, and the difference between them is what the loop does
next:

    None        no tool was asked for - this is an ordinary reply
    ToolCall    a readable request, to be handed to ToolExecutor
    Malformed   something that meant to be a request and cannot be read;
                the reason goes back to the model as a failure

Nothing here executes anything, imports anything from tools/, or knows
what a tool is. It reads text.
"""

from dataclasses import dataclass, field
import json


# How many tool calls one turn may make. A hard ceiling, not a hint: past
# it the model is asked once more with no tools on offer, so the turn
# always ends in a sentence rather than in another request.
TOOL_CALL_LIMIT = 3


@dataclass(frozen=True)
class ToolCall:
    """A request Aura is willing to hand to the executor."""

    name: str
    arguments: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Malformed:
    """
    A reply that was reaching for a tool and did not arrive.

    `reason` is written to the model, not to a log: it is the only channel
    the model has to correct itself, exactly like `last_action_error` on
    the Android side.
    """

    reason: str


def read_tool_call(reply: str | None) -> ToolCall | Malformed | None:
    """
    What, if anything, this reply is asking to run.

    Deliberately lenient about packaging and strict about content. A
    fenced object, an object with prose around it and a bare object all
    read the same, because which of those a model produces is not a
    decision it makes on purpose. What it says inside the object is.

    The discriminator is a string `tool` key. That does mean a reply
    quoting this format while talking *about* it would be read as a
    request - the cost of which is one gated tool call, since nothing here
    decides whether the call is permitted. Rejecting fenced or prefaced
    JSON, which is what most models actually emit, would cost every real
    request.
    """

    text = _strip_fences(reply or "")

    if not text:
        return None

    body = extract_json_object(text)

    if body is None:
        # Only worth a correction when the reply was visibly trying: an
        # object that starts and never closes is a token limit, not prose.
        if text.startswith("{"):
            return Malformed(
                "That JSON object is incomplete. Reply with one whole "
                'object: {"tool": "<name>", "arguments": {...}}'
            )
        return None

    try:
        parsed = json.loads(body)
    except ValueError:
        if text.startswith("{"):
            return Malformed(
                "That was not readable JSON. Reply with one object: "
                '{"tool": "<name>", "arguments": {...}}'
            )
        return None

    if not isinstance(parsed, dict) or "tool" not in parsed:
        return None

    name = parsed.get("tool")

    if not isinstance(name, str) or not name.strip():
        return Malformed(
            f'"tool" must be the name of a tool as a string, not '
            f"{type(name).__name__}."
        )

    arguments = parsed.get("arguments", {})

    if arguments is None:
        arguments = {}

    if not isinstance(arguments, dict):
        return Malformed(
            f'"arguments" must be an object of name/value pairs, not '
            f"{type(arguments).__name__}."
        )

    if any(not isinstance(key, str) for key in arguments):
        return Malformed('every name in "arguments" must be a string.')

    return ToolCall(name=name.strip(), arguments=arguments)


def call_key(call: ToolCall) -> str:
    """
    An identity for a call, so an identical repeat can be recognised.

    Sorted keys, because `{"a": 1, "b": 2}` and `{"b": 2, "a": 1}` are the
    same request and a model has no reason to be consistent about order.
    """

    try:
        arguments = json.dumps(call.arguments, sort_keys=True, default=str)
    except (TypeError, ValueError):
        arguments = repr(sorted(call.arguments.items(), key=lambda item: item[0]))

    return f"{call.name}:{arguments}"


def extract_json_object(text: str | None) -> str | None:
    """
    The first balanced `{...}` in `text`, or None.

    Brace matched rather than regex matched, and aware of strings and
    escapes, so a brace inside a value does not end the object early -
    `{"tool": "x", "arguments": {"text": "use {} here"}}` is one object.
    An object that never closes returns None; half a request is not a
    request.
    """

    if not text:
        return None

    start = text.find("{")

    if start < 0:
        return None

    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(text)):

        char = text[index]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]

    return None


def _strip_fences(reply: str) -> str:
    """
    Drop a surrounding markdown code fence.

    Only the fence markers, and only when the reply is wrapped in one.
    Anything else is left exactly as written - guessing at a reply's
    structure is how a correct answer gets discarded.
    """

    text = reply.strip()

    if not text.startswith("```"):
        return text

    lines = text.splitlines()

    if len(lines) < 2:
        return text

    # The opening fence, with or without a language tag.
    lines = lines[1:]

    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]

    return "\n".join(lines).strip()
