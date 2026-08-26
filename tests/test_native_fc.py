"""
Tests for native function calling (migration PART 1, 31).

The contract: tool calls arrive as structured provider fields, never as
prose to be excavated. A malformed argument payload degrades to a call
with empty arguments - which produces a structured INVALID_ARGUMENTS
failure downstream - rather than discarding a round the model spent.
"""

from brain.native_fc import extract_turn, parse_tool_call
from brain.providers.openai_compatible import OpenAICompatibleProvider


def raw_call(name="android.tap", arguments='{"text": "Search"}', id_="call_1"):
    return {
        "id": id_,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def test_a_well_formed_tool_call_parses_completely():

    request = parse_tool_call(raw_call())

    assert request.name == "android.tap"
    assert request.arguments == {"text": "Search"}
    assert request.call_id == "call_1"


def test_arguments_arrive_as_json_strings_and_are_decoded():

    request = parse_tool_call(
        raw_call(name="android.launch_app",
                 arguments='{"package": "com.google.android.youtube"}')
    )

    assert request.arguments["package"] == "com.google.android.youtube"


def test_invalid_argument_json_degrades_to_empty_not_discarded():

    request = parse_tool_call(raw_call(arguments="{not json"))

    assert request is not None          # the round survives
    assert request.name == "android.tap"
    assert request.arguments == {}


def test_a_call_without_a_name_cannot_be_read():

    assert parse_tool_call(raw_call(name="")) is None
    assert parse_tool_call({}) is None


def test_extract_turn_reads_text_and_tool_calls_together():

    turn = extract_turn({
        "content": "Opening YouTube.",
        "tool_calls": [
            raw_call(id_="c1"),
            raw_call(name="android.wait_for",
                     arguments='{"condition": "foreground=com.y"}',
                     id_="c2"),
        ],
    })

    assert turn.text == "Opening YouTube."
    assert len(turn.tool_calls) == 2
    assert turn.wants_tools
    assert not turn.is_empty


def test_extract_turn_of_a_plain_answer_has_no_calls():

    turn = extract_turn({"content": "Done.", "tool_calls": []})

    assert turn.text == "Done."
    assert not turn.wants_tools
    assert turn.is_empty is False


class _StubProvider(OpenAICompatibleProvider):
    """Bypasses the constructor; only the FC method under test runs."""

    def __init__(self):
        self.model = "test-model"
        self.max_tokens = 512
        self.temperature = None
        self.label = "Test"


def test_generate_with_tools_sends_tools_and_system_and_parses_the_reply():

    provider = _StubProvider()

    captured = {}

    def fake_send(payload):
        captured.update(payload)
        return {
            "choices": [{
                "message": {
                    "content": "",
                    "tool_calls": [raw_call()],
                },
            }],
        }

    provider._send = fake_send

    tools = [{"type": "function", "function": {"name": "android.tap"}}]
    messages = [{"role": "user", "content": "go"}]

    turn = provider.generate_with_tools("system rules", messages, tools)

    # The declared catalogue travels verbatim; the system instruction
    # leads; the caller's transcript follows unmodified.
    assert captured["tools"] == tools
    assert captured["messages"][0] == {
        "role": "system", "content": "system rules"
    }
    assert captured["messages"][1] == messages[0]

    assert turn.wants_tools
    assert turn.tool_calls[0].name == "android.tap"
    assert turn.tool_calls[0].arguments == {"text": "Search"}