"""
Tool schemas for native function calling.

Native function calling changes who parses what. In the old design the
model emitted prose-or-JSON and a parser on each side of the wire tried
to recover an action from it; in this one the platform declares each tool
as JSON Schema up front and the provider API guarantees the reply's tool
calls match that schema. Validation moves from "whatever arrived" to
"what was advertised".

This module is the bridge between the two worlds' vocabularies: tools are
still declared as `Parameter` tuples on the class (which is what
`describe()` renders for text prompts), and the same declaration exports
as a JSON Schema here. One source of truth, two renderings - the reason
there is no second place where a tool's arguments are described.

Types come from `Parameter.type`. Anything outside the six JSON Schema
primitive names is refused loudly rather than silently stringified: a
schema the provider rejects turns into a failed request halfway through a
task, which is far harder to diagnose than an error at registration.
"""

from core.logger import logger
from tools.base import ToolProtocol


# The type names JSON Schema itself defines. A Parameter outside this set
# would render a schema no provider accepts.
JSON_TYPES = ("string", "number", "integer", "boolean", "object", "array")


def to_json_schema(tool: ToolProtocol) -> dict:
    """
    The input schema of one tool, as a JSON Schema object.

    Tools written against ToolProtocol alone may not declare parameters
    at all (`required_parameters` is optional on the interface); such a
    tool gets an empty object schema, which is the honest reading - the
    provider will pass `{}` and the tool will complain about specifics
    if it disagrees.
    """

    properties: dict[str, dict] = {}
    required: list[str] = []

    declared = getattr(tool, "parameters", ()) or ()

    for parameter in declared:

        if parameter.type not in JSON_TYPES:
            raise ValueError(
                f"Tool '{getattr(tool, 'name', tool)}' parameter "
                f"'{parameter.name}' has type {parameter.type!r}, which "
                f"is not a JSON Schema type (one of {', '.join(JSON_TYPES)})"
            )

        entry: dict = {"type": parameter.type}

        if parameter.description:
            entry["description"] = parameter.description

        properties[parameter.name] = entry

        if parameter.required:
            required.append(parameter.name)

    schema: dict = {"type": "object", "properties": properties}

    if required:
        schema["required"] = required

    return schema


def function_name(tool: ToolProtocol) -> str:
    """The name a provider sees. Namespaced names pass through as-is."""

    return getattr(tool, "name", "") or ""


def openai_function_schema(tool: ToolProtocol) -> dict:
    """
    One tool in OpenAI chat-completions `tools:` form.

    This is the shape every OpenAI-compatible endpoint accepts (OpenAI,
    Groq, Mistral, DeepSeek, xAI, Cerebras, OpenRouter, self-hosted
    gateways) - which is why it lives here once rather than once per
    provider.
    """

    return {
        "type": "function",
        "function": {
            "name": function_name(tool),
            "description": getattr(tool, "description", "") or "",
            "parameters": to_json_schema(tool),
        },
    }


def openai_tools_payload(tools: list[ToolProtocol]) -> list[dict]:
    """
    Every offered tool, in request form. Skips - and warns about - any
    tool that cannot render, because one malformed declaration must not
    take down the whole catalogue.
    """

    payload: list[dict] = []

    for tool in tools:
        try:
            payload.append(openai_function_schema(tool))
        except ValueError as error:
            logger.warning("Skipping tool in schema export: %s", error)

    return payload
