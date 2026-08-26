"""
The device-action boundary (AURA-P0-005).

Aura runs on a server. She has no route, no transport and no tool that
can reach the user's physical PC, and Phase 6 is where that changes. Until
then the only failure available to this codebase is a *spoken* one: the
model describing an action it never performed, which the server then
relays verbatim as `reply`.

Two separate things hold that line, and these tests pin both.

**The structural half - nothing can execute.** No `/device` route exists,
and the shipped policy grants exactly one SAFE clock tool. There is no
code path from "open YouTube on my PC" to anything running, so a
*verified* false success is impossible rather than unlikely. This half was
already true; it is tested here so Phase 6 cannot quietly remove it.

**The spoken half - the model is told so.** This is the half that was
missing. The rule "never say you did something unless you were shown the
result" lived only in `PromptBuilder._build_tools`, which renders nothing
when the catalogue is empty, so the rule vanished in exactly the
configurations where Aura can do least:

    /api/chat/stream and the WebSocket   `offer_tools=False` always
    tools disabled, or an empty allow list

The statement now lives in `prompts/system.md`, which every conversational
turn loads unconditionally, so it no longer depends on a tool happening to
be allowed. `_build_tools` keeps its own copy: with a catalogue present
the rule is worth repeating next to the thing it governs.

Machine turns are deliberately excluded. The Android accessibility agent
really does have hands, and really does verify what it did
(`verifyOpenApp`), so telling it that it cannot act would be false - see
`brain/agent_mode.py`.
"""

import pytest

from brain.agent_mode import is_machine_turn
from brain.message import Message
from brain.prompt_builder import PromptBuilder
from brain.prompt_sections import SYSTEM, TOOLS
from brain.providers.base import split_prompt

from tools.executor import ToolExecutor, ToolPolicy
from tools.factory import build_registry

from core.config import load_config


# A physical-action request, in both languages the project is used in.
# "mở youtube" is the Phase 2 scenario, and it is here because a boundary
# stated only in English is a boundary that leaks on the first Vietnamese
# turn.
DEVICE_REQUESTS = [
    "Open YouTube on my PC.",
    "mở youtube trên máy tính của tôi",
    "launch spotify on my computer",
]


# Phrases from the standing rule. Substrings rather than the whole
# paragraph: this asserts the rule is *stated*, not that it is worded
# exactly one way, so an editorial pass over the prompt does not fail the
# suite while a deletion still does.
NO_HANDS = "You have no hands on any machine"

EVIDENCE_ONLY = "Never report an action as done"

UNCONDITIONAL = "whether or not any tools are listed"


CATALOGUE = "current_time: Get the current local date and time"


def prompt_for(text, tools=None, context=None, builder=None):
    """One ordinary conversational turn, rendered."""

    builder = builder or PromptBuilder()

    return builder.build(
        history=[],
        user_message=Message(role="user", content=text),
        tools=tools,
        context=context,
    )


# ======================================================================
# 1. The rule is stated, and does not depend on tools existing
# ======================================================================

@pytest.mark.parametrize("request_text", DEVICE_REQUESTS)
def test_a_device_request_is_answered_with_the_boundary_in_the_prompt(request_text):
    prompt = prompt_for(request_text)

    assert NO_HANDS in prompt
    assert EVIDENCE_ONLY in prompt


def test_the_boundary_survives_an_empty_catalogue():
    # The regression this phase exists for. Tools switched off, no runner
    # attached and an empty allow list all arrive as a falsy catalogue,
    # and all three used to strip the honesty rule out of the prompt
    # along with the TOOLS section.
    for empty in (None, "", "   "):
        prompt = prompt_for(DEVICE_REQUESTS[0], tools=empty)

        assert TOOLS not in prompt, empty
        assert NO_HANDS in prompt, empty
        assert EVIDENCE_ONLY in prompt, empty


def test_the_boundary_is_also_present_when_tools_are_offered():
    prompt = prompt_for(DEVICE_REQUESTS[0], tools=CATALOGUE)

    assert TOOLS in prompt
    assert NO_HANDS in prompt
    # The catalogue keeps its own statement of the same rule.
    assert "Never tell the user you have done something unless" in prompt


def test_the_rule_says_outright_that_it_does_not_depend_on_the_catalogue():
    # Without this sentence a model reading "no tools listed" can read the
    # absence as permission rather than as a further restriction.
    assert UNCONDITIONAL in prompt_for("hello")


def test_the_boundary_is_an_instruction_and_reaches_the_system_slot():
    system, user = split_prompt(prompt_for(DEVICE_REQUESTS[0]))

    assert NO_HANDS in system
    assert NO_HANDS not in user


# ======================================================================
# 2. Every conversational path carries it
# ======================================================================

def test_a_streamed_turn_carries_the_boundary():
    # chat_stream offers no tools by design, so before this phase a
    # streamed turn had no honesty rule in it at all. /api/chat/stream and
    # the WebSocket both take this path.
    prompt = prompt_for(DEVICE_REQUESTS[0], tools=None)

    assert NO_HANDS in prompt


def test_the_boundary_is_in_the_system_prompt_not_the_tool_section():
    # Where it lives is the whole fix: a section that renders
    # unconditionally, rather than one that renders only when a tool
    # happens to be allowed.
    from brain.system import SystemPrompt

    text = SystemPrompt().load()

    assert NO_HANDS in text
    assert EVIDENCE_ONLY in text


# ======================================================================
# 3. Machine turns are excluded, because they really can act
# ======================================================================

@pytest.mark.parametrize("context", [
    {"accessibility_tree": "<node/>", "device": {"foreground": "home"}},
    {"intent_probe": True},
])
def test_a_machine_turn_is_not_told_it_has_no_hands(context):
    # The Android agent executes and verifies for real. Handing it a rule
    # written for the server would make it refuse work it can do, and it
    # is answered by a parser that has no use for the sentence either.
    assert is_machine_turn(context)

    prompt = prompt_for("agent_tick", context=context)

    assert SYSTEM not in prompt
    assert NO_HANDS not in prompt


# ======================================================================
# 4. The structural half: nothing can execute
# ======================================================================

def test_device_execution_routes_are_authenticated():
    """The transport may exist, but no unauthenticated caller can use it."""
    from fastapi.testclient import TestClient

    from server.config import settings
    from server.main import app
    from server.route_introspection import iter_http_routes

    routes = [
        route for route in iter_http_routes(app)
        if route.path.startswith("/api/device")
    ]

    assert {route.path for route in routes} == {
        "/api/device/invoke",
        "/api/device/poll",
        "/api/device/results",
    }

    previous_token = settings.auth_token
    settings.auth_token = "device-boundary-test-token"

    try:
        client = TestClient(app)
        for route in routes:
            for method in route.methods - {"HEAD", "OPTIONS"}:
                response = client.request(method, route.path, json={})
                assert response.status_code in (401, 403), (
                    f"{method} {route.path} answered without authorization"
                )
    finally:
        settings.auth_token = previous_token


def test_the_shipped_policy_grants_nothing_that_can_touch_the_machine():
    # Not a duplicate of the Phase 3 config test, which pins the allow
    # list. This pins the *consequence*: whatever the list says, what is
    # actually runnable cannot reach the user's PC.
    tools_config = (load_config() or {}).get("tools") or {}

    executor = ToolExecutor(
        registry=build_registry(tools_config),
        policy=ToolPolicy.from_config(tools_config),
    )

    assert executor.available() == ["current_time"]

    for name in ("open_url", "open_application", "run_command", "click"):
        assert executor.check(name), f"{name} is runnable"


def test_a_hallucinated_device_tool_is_refused_rather_than_run():
    # The model asking for a tool that does not exist is the most likely
    # way a device request reaches the executor at all. It must come back
    # as a refusal the model is then obliged to report.
    tools_config = (load_config() or {}).get("tools") or {}

    executor = ToolExecutor(
        registry=build_registry(tools_config),
        policy=ToolPolicy.from_config(tools_config),
    )

    result = executor.execute("open_url", {"url": "https://youtube.com"})

    assert result.ok is False
    assert "unknown tool" in result.error
