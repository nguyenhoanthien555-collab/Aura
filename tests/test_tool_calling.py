"""
Tool calling: reading a request, running it, and answering from the result.

The rule these tests exist to hold is one sentence: Aura may not tell the
user something happened unless a tool ran and said it happened. Everything
below is a way that rule could be broken.

Four layers, and a break in any one of them puts a fabricated success in
front of the user:

    brain/tool_calling.py   reading a request out of a reply
    ConversationManager     the bounded loop, and what it does with a result
    PromptBuilder           where the catalogue and the results are rendered
    tools/builtins/apps.py  the launcher that used to say "Opened chrome"
                            whether or not anything opened

The regression contract, pinned first because both other phases edit this
same prompt path: with no runner attached, the prompt is byte-identical to
the one Aura built before tool calling existed.
"""

import subprocess
import sys

import pytest

from brain.conversation import ConversationManager
from brain.message import Message
from brain.prompt_builder import PromptBuilder
from brain.prompt_sections import TOOLS, TOOL_RESULTS
from brain.providers.base import split_prompt
from brain.ports import ToolRunner
from brain.tool_calling import (
    TOOL_CALL_LIMIT,
    Malformed,
    ToolCall,
    call_key,
    extract_json_object,
    read_tool_call,
)

from tools.base import Parameter, Tool, ToolResult, ToolRisk, fail, ok
from tools.builtins.apps import OpenApplicationTool
from tools.executor import ToolExecutor, ToolPolicy
from tools.factory import build_registry, build_tools
from tools.registry import ToolRegistry


# ----------------------------------------------------------------------
# Doubles
#
# Deterministic by construction: nothing here reaches a network, a real
# provider, or any application that happens to be installed on the machine
# running the suite.
# ----------------------------------------------------------------------

class ScriptedLLM:
    """
    Returns a fixed sequence of replies, one per call.

    A tool-calling turn asks the provider more than once, so a test has to
    be able to say "first a request, then an answer". Running off the end
    is a test-authoring bug rather than a silent repeat, so it raises.
    """

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)

        if not self.replies:
            raise AssertionError(
                f"provider asked {len(self.prompts)} times, "
                "more than the test scripted"
            )

        return self.replies.pop(0)

    @property
    def calls(self) -> int:
        return len(self.prompts)


class StubbornLLM:
    """
    Asks for a tool whenever one is on offer, and answers when none is.

    The faithful shape of the thing the loop has to bound. A stub that
    returned JSON even when the prompt offered no tools would be testing a
    model that does not exist, and would hide the fact that withholding the
    catalogue is what makes the last round end in a sentence.
    """

    def __init__(self, reply="Enough.", request=None):
        self.reply = reply
        self.request = request or '{"tool": "echo", "arguments": {"text": "{n}"}}'
        self.prompts = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)

        if TOOLS not in prompt:
            return self.reply

        # A distinct request each round when the template asks for one, so
        # the limit is what stops the loop rather than the repeat check.
        if "{n}" in self.request:
            return self.request.replace("{n}", str(len(self.prompts)))

        return self.request

    @property
    def calls(self) -> int:
        return len(self.prompts)


class FakeStore:

    def __init__(self):
        self.saved = []

    def save(self, role, content):
        self.saved.append((role, content))

    def get_recent(self, limit=10):
        return []


class RecordingRunner:
    """
    A ToolRunner that records what it was asked to run.

    Structural, not a subclass: the port is the contract, and a test double
    that satisfies it is proof the contract is satisfiable without
    inheriting anything.
    """

    def __init__(self, catalogue="current_time: what time it is", result=None):
        self._catalogue = catalogue
        self.result = result if result is not None else ok("12:00", tool="x")
        self.calls = []

    def available(self):
        return ["current_time"] if self._catalogue else []

    def catalogue(self):
        return self._catalogue

    def execute(self, name, arguments=None):
        self.calls.append((name, dict(arguments or {})))
        return self.result


class RaisingRunner(RecordingRunner):
    """A runner whose execute blows up - the injected-object boundary."""

    def execute(self, name, arguments=None):
        self.calls.append((name, dict(arguments or {})))
        raise RuntimeError("runner exploded")


class EchoTool(Tool):

    name = "echo"
    description = "Echo a phrase back"
    risk = ToolRisk.SAFE
    parameters = (Parameter(name="text", description="what to echo"),)

    def execute(self, text: str) -> ToolResult:
        return ok(text, tool=self.name)


def manager(llm, tools=None, store=None, **kwargs):

    return ConversationManager(
        memory=store if store is not None else FakeStore(),
        builder=PromptBuilder(),
        llm=llm,
        tools=tools,
        **kwargs,
    )


def call(name="echo", **arguments):
    """A reply that is a well-formed tool request."""

    import json

    return json.dumps({"tool": name, "arguments": arguments})


# ======================================================================
# 1. The regression contract
#
# Phases 2 and 3 both edit the shared prompt path. This is what stops
# either of them from silently changing ordinary conversation.
# ======================================================================

def _plain_prompt(builder, **extra):

    return builder.build(
        history=[],
        user_message=Message(role="user", content="hello"),
        **extra,
    )


def test_a_prompt_with_no_tools_is_byte_identical_to_one_built_without_them():
    builder = PromptBuilder()

    assert _plain_prompt(builder, tools=None, tool_results=None) == _plain_prompt(builder)


@pytest.mark.parametrize("empty", [None, "", "   ", "\n\t "])
def test_an_empty_catalogue_adds_nothing_at_all(empty):
    # No runner, tools switched off and an empty allow list all arrive here
    # as falsy text, and all three must be indistinguishable.
    builder = PromptBuilder()

    assert _plain_prompt(builder, tools=empty) == _plain_prompt(builder)


@pytest.mark.parametrize("empty", [None, [], ["", "  "]])
def test_empty_tool_results_add_nothing_at_all(empty):
    builder = PromptBuilder()

    assert _plain_prompt(builder, tool_results=empty) == _plain_prompt(builder)


def test_a_manager_with_no_runner_asks_the_provider_exactly_once():
    llm = ScriptedLLM("Hey, I'm here.")

    reply = manager(llm, tools=None).chat("hello")

    assert reply.text == "Hey, I'm here."
    assert llm.calls == 1


def test_a_reply_that_looks_like_json_is_left_alone_when_no_runner_is_attached():
    # Without a runner nothing is parsed, so a model talking about JSON is
    # answered with, not interpreted.
    llm = ScriptedLLM(call("echo", text="hi"))

    reply = manager(llm, tools=None).chat("show me the format")

    assert reply.text == call("echo", text="hi")
    assert llm.calls == 1


def test_a_runner_with_an_empty_catalogue_never_reaches_the_loop():
    llm = ScriptedLLM(call("echo", text="hi"))
    runner = RecordingRunner(catalogue="")

    reply = manager(llm, tools=runner).chat("hello")

    assert runner.calls == []
    assert llm.calls == 1
    assert reply.text == call("echo", text="hi")


def test_a_catalogue_that_raises_is_treated_as_no_tools():
    class BrokenRunner(RecordingRunner):
        def catalogue(self):
            raise RuntimeError("registry is on fire")

    llm = ScriptedLLM("ordinary reply")

    reply = manager(llm, tools=BrokenRunner()).chat("hello")

    assert reply.text == "ordinary reply"
    assert llm.calls == 1


# ======================================================================
# 2. Reading a request out of a reply
# ======================================================================

def test_a_bare_object_is_a_request():
    request = read_tool_call('{"tool": "echo", "arguments": {"text": "hi"}}')

    assert request == ToolCall(name="echo", arguments={"text": "hi"})


def test_a_fenced_object_is_a_request():
    # What most models actually emit, instructions notwithstanding.
    request = read_tool_call('```json\n{"tool": "echo", "arguments": {}}\n```')

    assert isinstance(request, ToolCall)
    assert request.name == "echo"


def test_an_object_with_prose_around_it_is_a_request():
    request = read_tool_call('Sure! {"tool": "echo", "arguments": {}} - one moment.')

    assert isinstance(request, ToolCall)


def test_a_missing_arguments_key_means_no_arguments():
    request = read_tool_call('{"tool": "echo"}')

    assert request == ToolCall(name="echo", arguments={})


def test_a_null_arguments_value_means_no_arguments():
    request = read_tool_call('{"tool": "echo", "arguments": null}')

    assert request == ToolCall(name="echo", arguments={})


def test_a_brace_inside_a_string_does_not_end_the_object():
    request = read_tool_call('{"tool": "echo", "arguments": {"text": "use {} here"}}')

    assert request.arguments == {"text": "use {} here"}


def test_an_escaped_quote_inside_a_string_does_not_end_it():
    request = read_tool_call(r'{"tool": "echo", "arguments": {"text": "say \"hi\""}}')

    assert request.arguments == {"text": 'say "hi"'}


@pytest.mark.parametrize(
    "reply",
    [
        "",
        None,
        "   ",
        "Just an ordinary sentence.",
        "I could open that for you, but I need permission first.",
        '{"action": "click", "node_id": "n7"}',   # the Android protocol
        '{"note": "no tool key here"}',
    ],
)
def test_a_reply_that_asks_for_nothing_is_not_a_request(reply):
    assert read_tool_call(reply) is None


@pytest.mark.parametrize(
    "reply",
    [
        '{"tool": "echo", "arguments":',          # cut off by a token limit
        '{"tool": "echo" "arguments": {}}',       # missing comma
    ],
)
def test_a_broken_object_that_was_reaching_for_a_tool_is_malformed(reply):
    request = read_tool_call(reply)

    assert isinstance(request, Malformed)
    assert request.reason


@pytest.mark.parametrize("name", [123, None, True, "", "   ", ["echo"]])
def test_a_tool_name_that_is_not_a_usable_string_is_malformed(name):
    import json

    request = read_tool_call(json.dumps({"tool": name}))

    # A non-string name is a request that cannot be honoured; `None` and
    # `false` are indistinguishable from absence and read as no request.
    assert isinstance(request, (Malformed, type(None)))

    if isinstance(request, Malformed):
        assert request.reason


@pytest.mark.parametrize("arguments", ["a string", 42, ["a", "list"]])
def test_arguments_that_are_not_an_object_are_malformed(arguments):
    import json

    request = read_tool_call(json.dumps({"tool": "echo", "arguments": arguments}))

    assert isinstance(request, Malformed)


def test_a_tool_name_is_stripped():
    assert read_tool_call('{"tool": "  echo  "}').name == "echo"


def test_extract_returns_none_when_an_object_never_closes():
    assert extract_json_object('{"tool": "echo"') is None


def test_extract_finds_the_first_whole_object():
    assert extract_json_object('x {"a": 1} y {"b": 2}') == '{"a": 1}'


def test_two_calls_that_differ_only_in_key_order_have_the_same_identity():
    left = ToolCall(name="echo", arguments={"a": 1, "b": 2})
    right = ToolCall(name="echo", arguments={"b": 2, "a": 1})

    assert call_key(left) == call_key(right)


def test_different_arguments_are_different_calls():
    assert call_key(ToolCall("echo", {"a": 1})) != call_key(ToolCall("echo", {"a": 2}))


def test_an_unserialisable_argument_still_yields_an_identity():
    # Never reaches a real tool - the executor rejects non-plain data - but
    # call_key runs before that and must not be the thing that raises.
    key = call_key(ToolCall("echo", {"x": object()}))

    assert isinstance(key, str) and key


# ======================================================================
# 3. The prompt: catalogue in, results in, and in the right slots
# ======================================================================

def test_the_catalogue_appears_in_the_prompt():
    prompt = _plain_prompt(PromptBuilder(), tools="echo: Echo a phrase back")

    assert TOOLS in prompt
    assert "echo: Echo a phrase back" in prompt


def test_the_prompt_states_the_call_format_and_the_honesty_rule():
    prompt = _plain_prompt(PromptBuilder(), tools="echo: Echo a phrase back")

    assert '{"tool":' in prompt
    assert "Never tell the user you have done something unless" in prompt


def test_tool_results_appear_under_their_own_heading():
    prompt = _plain_prompt(PromptBuilder(), tool_results=["echo ran successfully."])

    assert TOOL_RESULTS in prompt
    assert "echo ran successfully." in prompt


def test_results_sit_after_the_transcript_and_before_the_user_message():
    builder = PromptBuilder()

    prompt = builder.build(
        history=[Message(role="user", content="earlier thing")],
        user_message=Message(role="user", content="what time is it"),
        tools="current_time: the time",
        tool_results=["current_time ran successfully. It returned: 12:00"],
    )

    assert prompt.index(TOOLS) < prompt.index(TOOL_RESULTS)
    assert prompt.index("earlier thing") < prompt.index(TOOL_RESULTS)
    assert prompt.index(TOOL_RESULTS) < prompt.index("what time is it")


def test_the_catalogue_is_an_instruction_and_the_result_is_evidence():
    # split_prompt sends them to different slots on purpose: what may be
    # requested is a standing rule, what one returned is about this turn.
    prompt = _plain_prompt(
        PromptBuilder(),
        tools="echo: Echo a phrase back",
        tool_results=["echo ran successfully. It returned: hi"],
    )

    system, user = split_prompt(prompt)

    assert "Echo a phrase back" in system
    assert "echo ran successfully" in user
    assert "echo ran successfully" not in system


# ======================================================================
# 4. The loop
# ======================================================================

def test_a_request_is_executed_and_answered_from_the_real_result():
    llm = ScriptedLLM(call("echo", text="hi"), "It said hi.")
    runner = RecordingRunner(result=ok("hi", tool="echo"))

    reply = manager(llm, tools=runner).chat("echo hi")

    assert runner.calls == [("echo", {"text": "hi"})]
    assert reply.text == "It said hi."
    assert llm.calls == 2


def test_the_real_result_is_put_in_front_of_the_model():
    llm = ScriptedLLM(call("echo", text="hi"), "It said hi.")
    runner = RecordingRunner(result=ok("the actual output", tool="echo"))

    manager(llm, tools=runner).chat("echo hi")

    assert "the actual output" in llm.prompts[1]
    assert TOOL_RESULTS in llm.prompts[1]


def test_the_users_request_is_never_the_raw_json():
    llm = ScriptedLLM(call("echo", text="hi"), "It said hi.")

    reply = manager(llm, tools=RecordingRunner()).chat("echo hi")

    assert '"tool"' not in reply.text


def test_only_the_final_answer_is_remembered():
    # A saved request would come back next turn as something Aura said.
    store = FakeStore()
    llm = ScriptedLLM(call("echo", text="hi"), "It said hi.")

    manager(llm, tools=RecordingRunner(), store=store).chat("echo hi")

    assert store.saved == [("user", "echo hi"), ("assistant", "It said hi.")]


def test_a_failure_is_reported_as_a_failure_and_not_as_the_intended_outcome():
    llm = ScriptedLLM(call("echo", text="hi"), "Sorry, that didn't work.")
    runner = RecordingRunner(result=fail("no such thing", tool="echo"))

    manager(llm, tools=runner).chat("echo hi")

    second = llm.prompts[1]

    assert "FAILED" in second
    assert "no such thing" in second
    assert "Tell the user it failed" in second


def test_two_different_tools_can_run_in_one_turn():
    llm = ScriptedLLM(
        call("echo", text="one"),
        call("echo", text="two"),
        "Both done.",
    )
    runner = RecordingRunner()

    reply = manager(llm, tools=runner).chat("do both")

    assert runner.calls == [("echo", {"text": "one"}), ("echo", {"text": "two"})]
    assert reply.text == "Both done."


def test_every_result_so_far_stays_in_front_of_the_model():
    llm = ScriptedLLM(
        call("echo", text="one"),
        call("echo", text="two"),
        "Both done.",
    )

    class Counter(RecordingRunner):
        def execute(self, name, arguments=None):
            self.calls.append((name, dict(arguments or {})))
            return ok(f"result {len(self.calls)}", tool=name)

    manager(llm, tools=Counter()).chat("do both")

    assert "result 1" in llm.prompts[2]
    assert "result 2" in llm.prompts[2]


def test_the_turn_stops_at_the_call_limit():
    # A model that asks for another tool every time it is offered one must
    # still stop, and must stop having said something to the user.
    llm = StubbornLLM()
    runner = RecordingRunner()

    reply = manager(llm, tools=runner).chat("go")

    assert len(runner.calls) == TOOL_CALL_LIMIT
    assert llm.calls == TOOL_CALL_LIMIT + 1
    assert reply.text == "Enough."


def test_the_last_round_is_offered_no_tools_so_the_turn_ends_in_a_sentence():
    llm = StubbornLLM()

    manager(llm, tools=RecordingRunner()).chat("go")

    assert TOOLS not in llm.prompts[-1]
    assert all(TOOLS in prompt for prompt in llm.prompts[:-1])


def test_an_identical_repeat_is_refused_rather_than_run_twice():
    llm = ScriptedLLM(call("echo", text="hi"), call("echo", text="hi"), "Done.")
    runner = RecordingRunner()

    reply = manager(llm, tools=runner).chat("echo hi")

    assert runner.calls == [("echo", {"text": "hi"})]
    assert reply.text == "Done."


def test_a_refused_repeat_ends_the_turn_immediately():
    # Settled: the model already has what it asked for, so it is asked once
    # more with no tools rather than being allowed to loop.
    llm = ScriptedLLM(call("echo", text="hi"), call("echo", text="hi"), "Done.")

    manager(llm, tools=RecordingRunner()).chat("echo hi")

    assert "has already run this turn" in llm.prompts[2]
    assert TOOLS not in llm.prompts[2]


def test_a_repeat_with_different_arguments_is_not_a_repeat():
    llm = ScriptedLLM(call("echo", text="one"), call("echo", text="two"), "Done.")
    runner = RecordingRunner()

    manager(llm, tools=runner).chat("go")

    assert len(runner.calls) == 2


def test_a_malformed_request_never_reaches_the_runner():
    llm = ScriptedLLM('{"tool": "echo", "arguments":', "Sorry, I garbled that.")
    runner = RecordingRunner()

    reply = manager(llm, tools=runner).chat("echo hi")

    assert runner.calls == []
    assert reply.text == "Sorry, I garbled that."


def test_a_malformed_request_is_corrected_rather_than_ignored():
    llm = ScriptedLLM('{"tool": "echo", "arguments":', "Sorry, I garbled that.")

    manager(llm, tools=RecordingRunner()).chat("echo hi")

    assert "No tool ran." in llm.prompts[1]


def test_malformed_requests_count_towards_the_limit():
    # Otherwise a model emitting only broken JSON would never terminate.
    llm = StubbornLLM(
        reply="Giving up.",
        request='{"tool": "echo", "arguments":',
    )
    runner = RecordingRunner()

    reply = manager(llm, tools=runner).chat("go")

    assert runner.calls == []
    assert llm.calls == TOOL_CALL_LIMIT + 1
    assert reply.text == "Giving up."


def test_a_runner_that_raises_does_not_break_the_turn():
    llm = ScriptedLLM(call("echo", text="hi"), "That didn't work.")
    runner = RaisingRunner()

    reply = manager(llm, tools=runner).chat("echo hi")

    assert reply.text == "That didn't work."
    assert "FAILED" in llm.prompts[1]
    assert "Nothing happened" in llm.prompts[1]


def test_a_runner_returning_nothing_is_a_failure_not_a_success():
    class NullRunner(RecordingRunner):
        def execute(self, name, arguments=None):
            self.calls.append((name, dict(arguments or {})))
            return None

    llm = ScriptedLLM(call("echo", text="hi"), "Nope.")

    manager(llm, tools=NullRunner()).chat("echo hi")

    assert "FAILED" in llm.prompts[1]


def test_a_denial_from_the_executor_reads_as_a_failure():
    # Permission lives behind execute, and a denial is an ordinary result.
    llm = ScriptedLLM(call("echo", text="hi"), "I'm not allowed to.")
    runner = RecordingRunner(result=fail("tool not allowed by policy: echo"))

    manager(llm, tools=runner).chat("echo hi")

    assert "FAILED" in llm.prompts[1]
    assert "not allowed by policy" in llm.prompts[1]


def test_a_successful_result_with_no_output_still_reads_as_success():
    llm = ScriptedLLM(call("echo"), "Done.")
    runner = RecordingRunner(result=ok("", tool="echo"))

    manager(llm, tools=runner).chat("go")

    assert "ran successfully" in llm.prompts[1]


def test_streaming_offers_no_tools():
    # JSON would already be on the user's screen before it could be read.
    llm = ScriptedLLM("Hey there.")
    runner = RecordingRunner()

    list(manager(llm, tools=runner).chat_stream("hello"))

    assert runner.calls == []
    assert TOOLS not in llm.prompts[0]


def test_a_machine_turn_is_never_offered_tools():
    # The Android agent has its own protocol and its own executor.
    llm = ScriptedLLM('{"action": "complete"}')
    runner = RecordingRunner()

    reply = manager(llm, tools=runner).chat(
        "agent_tick",
        context={"accessibility_tree": {"nodes": []}, "user_request": "mở youtube"},
    )

    assert runner.calls == []
    assert reply.text == '{"action": "complete"}'
    assert TOOLS not in llm.prompts[0]


# ======================================================================
# 5. The executor as the runner
#
# The port is satisfied structurally, and the loop must work against the
# real thing rather than only against a double.
# ======================================================================

def test_the_executor_satisfies_the_runner_port():
    assert isinstance(ToolExecutor(), ToolRunner)


def test_the_catalogue_lists_only_what_policy_allows():
    registry = ToolRegistry()
    registry.register(EchoTool())

    allowed = ToolExecutor(
        registry=registry,
        policy=ToolPolicy(enabled=True, allowed=frozenset({"echo"})),
    )
    forbidden = ToolExecutor(
        registry=registry,
        policy=ToolPolicy(enabled=True, allowed=frozenset()),
    )

    assert "echo" in allowed.catalogue()
    assert forbidden.catalogue() == ""


def test_a_disabled_executor_offers_an_empty_catalogue():
    registry = ToolRegistry()
    registry.register(EchoTool())

    executor = ToolExecutor(
        registry=registry,
        policy=ToolPolicy(enabled=False, allowed=frozenset({"echo"})),
    )

    assert executor.catalogue() == ""


def test_a_whole_turn_runs_against_the_real_executor():
    registry = ToolRegistry()
    registry.register(EchoTool())

    executor = ToolExecutor(
        registry=registry,
        policy=ToolPolicy(enabled=True, allowed=frozenset({"echo"})),
    )

    llm = ScriptedLLM(call("echo", text="hello there"), "It echoed hello there.")

    reply = manager(llm, tools=executor).chat("echo hello there")

    assert reply.text == "It echoed hello there."
    assert executor.history == [("echo", True)]
    assert "hello there" in llm.prompts[1]


def test_a_tool_the_policy_forbids_is_denied_through_the_real_executor():
    registry = ToolRegistry()
    registry.register(EchoTool())

    class Secret(Tool):
        name = "secret"
        description = "Not allowed"
        risk = ToolRisk.SAFE

        def execute(self) -> ToolResult:
            raise AssertionError("a forbidden tool must never run")

    registry.register(Secret())

    executor = ToolExecutor(
        registry=registry,
        policy=ToolPolicy(enabled=True, allowed=frozenset({"echo"})),
    )

    llm = ScriptedLLM(call("secret"), "I can't do that.")

    manager(llm, tools=executor).chat("do the secret thing")

    assert "FAILED" in llm.prompts[1]
    assert executor.history == [("secret", False)]


def test_an_unknown_tool_is_refused_through_the_real_executor():
    executor = ToolExecutor(
        registry=build_registry({}),
        policy=ToolPolicy(enabled=True, allowed=frozenset({"current_time"})),
    )

    llm = ScriptedLLM(call("delete_everything"), "No such tool.")

    manager(llm, tools=executor).chat("go")

    assert "FAILED" in llm.prompts[1]
    assert "unknown tool" in llm.prompts[1]


def test_a_dangerous_tool_cannot_run_with_no_one_to_ask():
    # Server mode has no human, so DANGEROUS is unreachable there by design.
    registry = ToolRegistry()

    class Danger(Tool):
        name = "danger"
        description = "Changes the machine"
        risk = ToolRisk.DANGEROUS

        def execute(self) -> ToolResult:
            raise AssertionError("must not run without approval")

    registry.register(Danger())

    executor = ToolExecutor(
        registry=registry,
        policy=ToolPolicy(enabled=True, allowed=frozenset({"danger"})),
        confirm=None,
    )

    llm = ScriptedLLM(call("danger"), "I need permission for that.")

    manager(llm, tools=executor).chat("do it")

    assert "permission denied" in llm.prompts[1]


# ======================================================================
# 6. OpenApplicationTool
#
# The tool that used to return "Opened chrome" whether or not anything
# opened. Every case uses this interpreter or a name that cannot exist,
# so nothing here depends on what is installed.
# ======================================================================

PYTHON = sys.executable


def test_an_unknown_nickname_is_a_permission_error_not_a_failed_launch():
    tool = OpenApplicationTool({"notepad": ["notepad.exe"]})

    with pytest.raises(PermissionError):
        tool.execute(name="literally-anything-else")


def test_a_nickname_is_a_key_and_never_a_command():
    # The whole point of the mapping: the caller's string is looked up, not
    # parsed, joined or handed to a shell.
    tool = OpenApplicationTool({"safe": [PYTHON, "-c", "pass"]})

    with pytest.raises(PermissionError):
        tool.execute(name="cmd.exe /c del *.*")


def test_a_missing_executable_fails_honestly_and_launches_nothing():
    tool = OpenApplicationTool({"ghost": ["definitely-not-a-real-program-xyz"]})

    result = tool.execute(name="ghost")

    assert result.ok is False
    assert "was not found" in result.error
    assert "Nothing was launched" in result.error


def test_a_program_that_exits_non_zero_is_reported_as_a_failure():
    tool = OpenApplicationTool({"failing": [PYTHON, "-c", "import sys; sys.exit(3)"]})

    result = tool.execute(name="failing")

    assert result.ok is False
    assert "status 3" in result.error
    assert "Nothing is open" in result.error


def test_what_a_failing_program_said_on_stderr_is_reported():
    tool = OpenApplicationTool(
        {
            "noisy": [
                PYTHON,
                "-c",
                "import sys; sys.stderr.write('could not find display'); sys.exit(1)",
            ]
        }
    )

    result = tool.execute(name="noisy")

    assert result.ok is False
    assert "could not find display" in result.error


def test_a_program_that_exits_cleanly_is_a_success_that_claims_nothing_extra():
    tool = OpenApplicationTool({"handoff": [PYTHON, "-c", "pass"]})

    result = tool.execute(name="handoff")

    assert result.ok is True
    assert "status 0" in result.output


def test_a_program_still_running_after_the_grace_period_is_a_success():
    tool = OpenApplicationTool({"gui": [PYTHON, "-c", "import time; time.sleep(30)"]})

    result = tool.execute(name="gui")

    assert result.ok is True
    assert "still running" in result.output


def test_a_successful_launch_does_not_claim_a_window_appeared():
    # What is unknowable is stated as unknowable.
    tool = OpenApplicationTool({"gui": [PYTHON, "-c", "import time; time.sleep(30)"]})

    result = tool.execute(name="gui")

    assert "cannot be confirmed" in result.output


def test_the_tool_is_dangerous_and_therefore_needs_approval():
    assert OpenApplicationTool({}).risk is ToolRisk.DANGEROUS


def test_the_description_names_the_allowed_applications():
    tool = OpenApplicationTool({"notepad": ["notepad.exe"], "browser": ["chrome.exe"]})

    assert "browser" in tool.describe()
    assert "notepad" in tool.describe()


def test_a_launch_never_reaches_a_shell():
    launched = {}

    def fake_popen(command, **kwargs):
        launched.update(kwargs)
        launched["command"] = command
        raise OSError("stopped before launching")

    tool = OpenApplicationTool({"x": [PYTHON, "-c", "pass"]})

    original = subprocess.Popen
    subprocess.Popen = fake_popen
    try:
        tool.execute(name="x")
    finally:
        subprocess.Popen = original

    assert launched["shell"] is False
    assert isinstance(launched["command"], list)


def test_an_os_refusal_is_a_failed_result_not_a_crash():
    def fake_popen(command, **kwargs):
        raise OSError("access denied")

    tool = OpenApplicationTool({"x": [PYTHON, "-c", "pass"]})

    original = subprocess.Popen
    subprocess.Popen = fake_popen
    try:
        result = tool.execute(name="x")
    finally:
        subprocess.Popen = original

    assert result.ok is False
    assert "Nothing was launched" in result.error


# ======================================================================
# 7. Configuration as the safety boundary
#
# Four independent gates. Each one alone must be enough to stop a tool.
# ======================================================================

def test_applications_must_be_a_mapping_to_register_anything():
    # AURA-P0-004: `applications: []` is as falsy as `{}`, so the tool was
    # never registered and nothing said why.
    assert build_registry({"applications": {"notepad": ["notepad.exe"]}}).has(
        "open_application"
    )
    assert not build_registry({"applications": []}).has("open_application")
    assert not build_registry({"applications": {}}).has("open_application")


def test_a_wrongly_shaped_applications_section_is_reported(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        registry = build_registry({"applications": ["chrome"]})

    assert not registry.has("open_application")
    assert "must be a mapping" in caplog.text


def test_enabling_tools_without_allowing_any_is_reported(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        executor = build_tools({"enabled": True, "allowed": []})

    assert executor.available() == []
    assert "tools.allowed is empty" in caplog.text


def test_allowing_a_tool_that_does_not_exist_is_reported(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        build_tools({"enabled": True, "allowed": ["current_tiem"]})

    assert "not registered" in caplog.text


def test_registering_an_application_is_not_enough_to_run_it():
    # Two independent decisions: configure it, then allow it.
    executor = build_tools(
        {"enabled": True, "allowed": [], "applications": {"notepad": ["notepad.exe"]}}
    )

    assert executor.registry.has("open_application")
    assert "open_application" not in executor.available()


def test_allowing_an_application_is_not_enough_to_run_it_unattended():
    # DANGEROUS, and auto_approve is SAFE only, so there is still no yes.
    executor = build_tools(
        {
            "enabled": True,
            "allowed": ["open_application"],
            "applications": {"ghost": ["definitely-not-a-real-program-xyz"]},
        }
    )

    result = executor.execute("open_application", {"name": "ghost"})

    assert result.ok is False
    assert "permission denied" in result.error


def test_the_shipped_config_grants_exactly_one_safe_tool():
    from core.config import load_config

    executor = build_tools(load_config().get("tools") or {})

    assert executor.available() == ["current_time"]


# ======================================================================
# 6. remember, end to end (section 17)
#
# Registered is not reachable. Five gates sit between a tool existing and
# a fact landing in the database - enabled, registered, allowed, risk
# approved, arguments valid - and the semantic tier's whole problem was
# that the machinery existed while nothing crossed them. So these run the
# real conversation loop against the real executor and the real store,
# and check the database rather than the call log.
# ======================================================================

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.temporal import TemporalClock
from memory.models import Base
from memory.pipeline import MemoryPipeline
from memory.user_model import Status
from tools.builtins.memory import RememberTool


@pytest.fixture
def live_pipeline():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    yield MemoryPipeline(
        session=session,
        clock=TemporalClock(now=lambda: __import__("datetime").datetime(
            2026, 8, 24, 10, 30
        )),
    )

    session.close()


def remember_executor(pipeline) -> ToolExecutor:
    """The real executor, with the policy config.yaml actually ships."""

    return build_tools(
        {"enabled": True, "allowed": ["remember"], "auto_approve": ["safe"]},
        memory=pipeline,
    )


def test_a_conversation_can_put_a_fact_in_the_semantic_tier(live_pipeline):
    """
    The gap phase 11 part 2 exists to close, stated as a behaviour.

    The user says who they are, the model asks for `remember`, and the
    fact is in the user model afterwards - CONFIRMED, because they said
    it. Before this, every gate here passed except the last one: there
    was no tool to ask for, so the sentence was saved as something that
    happened and the fact it carried was lost.
    """

    llm = ScriptedLLM(
        call("remember", key="identity.name", value="Thien"),
        "được rồi, tớ nhớ nha.",
    )

    reply = manager(llm, tools=remember_executor(live_pipeline)).chat(
        "tên tớ là Thien nhé"
    )

    model = live_pipeline.user_model

    assert model.value_of("identity.name") == "Thien"
    assert model.status_of("identity.name") is Status.CONFIRMED

    # And the turn still ends in a sentence, not in the tool JSON.
    assert reply.text == "được rồi, tớ nhớ nha."


def test_the_stored_fact_reaches_the_next_prompt(live_pipeline):
    """
    Storing it is only half the point - a fact nothing reads is a row.

    The same pipeline that wrote it composes the memory section of a
    later prompt, so this checks the loop closes: remembered on one turn,
    present in the model's context on the next.
    """

    remember = RememberTool(live_pipeline)
    remember.execute(key="identity.name", value="Thien")

    lines = live_pipeline.memory_lines("what is my name")

    assert any("Thien" in line for line in lines)


def catalogue_of(prompt: str) -> str:
    """
    Just the TOOLS section, because the bare word will not do.

    `"remember" in prompt` looks like the obvious assertion and is
    useless: the persona and memory sections say "remember what matters
    across conversations" and "do not pretend to remember something that
    is not in front of you" in every prompt Aura ever builds. So the
    naive check passes with the catalogue entirely broken, and its
    negative fails with the catalogue correctly empty. Only the section
    the catalogue actually writes discriminates.
    """

    if TOOLS not in prompt:
        return ""

    return prompt.split(TOOLS, 1)[1].split("=====", 1)[0]


def test_the_model_is_told_the_tool_exists(live_pipeline):
    """
    A tool absent from the catalogue cannot be asked for, however well
    registered it is - which is what `allowed` decides, and what makes
    the config.yaml line load bearing rather than cosmetic.
    """

    llm = ScriptedLLM("nothing to do")

    manager(llm, tools=remember_executor(live_pipeline)).chat("hello")

    assert "remember" in catalogue_of(llm.prompts[0])


def test_a_conversation_cannot_reach_it_when_the_owner_unlists_it(
    live_pipeline,
):
    """
    The owner's half of section 2, and the documented way to turn this
    off: delete the line from `tools.allowed`. The tool stays registered
    and becomes inert - a refusal, not an error - so nothing crashes and
    nothing is stored.
    """

    executor = build_tools(
        {"enabled": True, "allowed": [], "auto_approve": ["safe"]},
        memory=live_pipeline,
    )

    llm = ScriptedLLM("ok")

    manager(llm, tools=executor).chat("tên tớ là Thien nhé")

    assert executor.registry.has("remember")
    assert catalogue_of(llm.prompts[0]) == ""
    assert len(live_pipeline.user_model) == 0


def test_no_pipeline_means_the_tool_is_absent_rather_than_broken():
    """
    Registration is gated on the dependency, like the filesystem tools
    are on `allowed_paths`. A `remember` registered with nothing behind
    it would accept a fact and drop it, reporting success.
    """

    registry = build_registry({"enabled": True, "allowed": ["remember"]})

    assert not registry.has("remember")


def test_the_composition_root_hands_the_pipeline_to_the_tools():
    """
    The wiring, not the function.

    `RememberTool` being correct is worth nothing if the one executor the
    process actually runs tools through was built without a pipeline: the
    tool would not register, `remember` would be absent from every real
    catalogue, and every test above would still pass, because they all
    build their own executor. Dropping the third argument from the
    `_build_tools` call in `launcher/services.py` was tried, and the full
    suite stayed green - which is how this test came to exist.

    That is the same shape of bug as the one this whole phase is about:
    machinery present, nothing reaching it, tests agreeing.
    """

    from launcher.services import build_services
    from memory.manager import MemoryManager

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    try:
        services = build_services(
            config={
                # Everything optional off: this is about one argument.
                "voice": {"tts": {"enabled": False}, "stt": {"enabled": False}},
                "vision": {"enabled": False},
                "avatar": {"enabled": False},
                "plugins": {"enabled": []},
                "tools": {"enabled": True, "allowed": ["remember"]},
                # The pipeline is what `remember` writes through, so the
                # tier it serves has to be switched on for it to exist.
                "memory": {"recall": False, "profile": False, "pipeline": True},
            },
            memory=MemoryManager(session=session),
        )

        assert services.tools.registry.has("remember")

        # And reachable, not merely registered - SAFE, auto approved, so
        # no confirmation handler is needed in a server deployment.
        assert "remember" in services.tools.available()

    finally:
        session.close()
