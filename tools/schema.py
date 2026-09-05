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


# ----------------------------------------------------------------------
# Output schemas and the canonical definition (Phase 3)
# ----------------------------------------------------------------------

def output_schema(tool: ToolProtocol) -> dict | None:
    """
    The declared shape of a successful result's `data`, or None.

    None means undeclared, and that is honest: most tools return prose
    into `output` and have nothing to validate. A tool that declares a
    schema opts its output into executor validation, where a malformed
    result stops being SUCCESS.

    A declared schema that is not a dict is refused loudly here, for the
    same reason `to_json_schema` refuses a bad parameter type - a schema
    a validator cannot read is a delayed failure halfway through a task.
    """

    declared = getattr(tool, "output_schema", None)

    if declared is None:
        return None

    if not isinstance(declared, dict):
        raise ValueError(
            f"Tool '{getattr(tool, 'name', tool)}' output_schema must be "
            f"a dict, got {type(declared).__name__}"
        )

    return declared


def tool_definition(tool: ToolProtocol) -> dict:
    """
    One tool's canonical machine-readable definition.

    Everything the runtime, the registry and a future discovery question
    ("what can AURA actually do right now?") need, read from the one
    place each fact is declared: parameters from the Parameter tuple,
    output from `output_schema`, retry semantics from `side_effect`,
    permission from `risk`, identity from name and version.

    This is the export format; no other module re-derives it.
    """

    return {
        "name": function_name(tool),
        "description": getattr(tool, "description", "") or "",
        "input_schema": to_json_schema(tool),
        "output_schema": output_schema(tool),
        "risk_level": getattr(tool.risk, "value", str(tool.risk)),
        "side_effect": getattr(
            getattr(tool, "side_effect", None), "value", "UNKNOWN"
        ),
        "capability": getattr(tool, "capability", None) or "",
        "timeout": getattr(tool, "timeout", None),
        "version": getattr(tool, "version", "1.0") or "1.0",
    }


def mcp_export(tools: list[ToolProtocol]) -> list[dict]:
    """
    The registry in MCP `tools/list` conceptual form.

    MCP-compatible: name, description and inputSchema, which is the
    whole of the MCP tool shape that concerns this codebase. The Aura
    fields the standard has no slot for (output schema, risk, side
    effect) stay OUT of the export rather than being smuggled in - this
    payload is meant to be readable by an MCP client as-is. The full
    contract is `tool_definition`; the registry serves that separately.

    Skips and warns about a tool that cannot render, like the OpenAI
    payload does: one malformed declaration must not take down the list.
    """

    payload: list[dict] = []

    for tool in tools:
        try:
            payload.append({
                "name": function_name(tool),
                "description": getattr(tool, "description", "") or "",
                "inputSchema": to_json_schema(tool),
            })
        except ValueError as error:
            logger.warning("Skipping tool in MCP export: %s", error)

    return payload


def validate_output(tool: ToolProtocol, data) -> str:
    """
    Whether `data` matches the tool's declared output schema, or why not.

    A deliberately small JSON Schema subset - type on the top level,
    required and property types one level deep, items type for arrays -
    which covers every schema a tool realistically declares about its own
    result and costs microseconds. A full JSON Schema validator is a
    dependency the runtime does not need to read its own tools' output.

    Returns "" for a match, or the reason it does not. An undeclared
    schema returns "" too: nothing was promised, so nothing can be
    violated.
    """

    schema = output_schema(tool)

    if schema is None:
        return ""

    if data is None:
        return "tool declared an output schema but returned no data"

    expected = schema.get("type")

    if expected and not _matches_type(data, expected):
        return (
            f"output type is {type(data).__name__}, "
            f"schema declares {expected!r}"
        )

    if isinstance(data, dict):

        for name in schema.get("required", []) or []:
            if name not in data:
                return f"output is missing required key {name!r}"

        properties = schema.get("properties", {}) or {}

        for name, value in data.items():

            expected_prop = (properties.get(name) or {}).get("type")

            if expected_prop and not _matches_type(value, expected_prop):
                return (
                    f"output key {name!r} is {type(value).__name__}, "
                    f"schema declares {expected_prop!r}"
                )

    if isinstance(data, list):

        expected_item = (schema.get("items") or {}).get("type")

        if expected_item:
            for index, item in enumerate(data):
                if not _matches_type(item, expected_item):
                    return (
                        f"output item {index} is {type(item).__name__}, "
                        f"schema declares {expected_item!r}"
                    )

    return ""


def _matches_type(value, expected: str) -> bool:

    checks = {
        "string": lambda v: isinstance(v, str),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "null": lambda v: v is None,
    }

    check = checks.get(expected)

    # An unknown type name cannot be checked here; a schema promising a
    # type this table has never heard of validates nothing.
    return check(value) if check else False


# Public alias: the executor's argument gate checks declared Parameter
# types with the same table the output validator uses. One vocabulary.
matches_type = _matches_type
