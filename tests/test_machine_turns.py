"""
Machine turns: the agent loop and the intent probe.

Two things go through ConversationManager that are not conversation. The
Android accessibility service sends a screen and gets back a JSON action
a parser executes; before that, the app asks one question - "is this
something to do, or something to say?" - and gets back one word.

Both are read by a program. Both must therefore skip everything that
exists to make Aura sound like herself, and neither may leave a trace in
the transcript the next real turn is built from. These tests hold that
line at all three places it can be crossed: which sections the prompt
gets, what the manager does with the reply, and how the reply is read.
"""

import pytest

from brain.agent_mode import (
    ACTION,
    AGENT_TICK_KEYS,
    CONVERSATION,
    INTENT_PROBE_KEY,
    is_agent_tick,
    is_intent_probe,
    is_machine_turn,
    read_intent,
)
from brain.conversation import ConversationManager
from brain.message import Message
from brain.prompt_builder import PromptBuilder
from brain.providers.base import split_prompt
from brain.response import Response

from events.types import (
    ErrorEvent,
    ResponseEvent,
    StreamChunkEvent,
    StreamFinishedEvent,
    StreamStartedEvent,
    ThinkingEvent,
    UserInputEvent,
)


# ----------------------------------------------------------------------
# Doubles
# ----------------------------------------------------------------------

class FakeLLM:
    """Records the prompt it was given and returns a fixed reply."""

    def __init__(self, reply='{"action": "complete"}'):
        self.reply = reply
        self.prompts = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply

    def generate_stream(self, prompt: str):
        self.prompts.append(prompt)
        yield self.reply


class FakeStore:

    def __init__(self):
        self.saved = []

    def save(self, role, content):
        self.saved.append((role, content))

    def get_recent(self, limit):
        return []


class FakeBus:

    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)

    def kinds(self):
        return {type(event) for event in self.events}


class ShoutyStyle:
    """A styler that visibly mangles anything a parser would need."""

    def style(self, text: str) -> str:
        return text.upper().replace('"', "")


def manager(llm=None, store=None, bus=None, style=None):

    return ConversationManager(
        memory=store if store is not None else FakeStore(),
        builder=PromptBuilder(),
        llm=llm if llm is not None else FakeLLM(),
        events=bus,
        style=style,
    )


AGENT_CONTEXT = {
    "device": {"width": 1080, "height": 2400},
    "app": {"package": "com.example.app", "activity": "ExampleActivity"},
    "accessibility_tree": {"package": "com.example.app", "nodes": []},
    "user_request": "mở youtube",
}

PROBE_CONTEXT = {INTENT_PROBE_KEY: True}


# ----------------------------------------------------------------------
# The shared predicate
#
# One definition, because two copies that drifted apart is how a JSON
# action ended up in the conversation transcript.
# ----------------------------------------------------------------------

@pytest.mark.parametrize("key", AGENT_TICK_KEYS)
def test_either_device_key_alone_marks_an_agent_tick(key):
    # A tick taken before the tree could be serialised still carries the
    # device, and vice versa.
    assert is_agent_tick({key: {}}) is True


@pytest.mark.parametrize("context", [None, {}, {"source": "voice"}, "agent_tick", 42])
def test_non_device_contexts_are_not_agent_ticks(context):
    assert is_agent_tick(context) is False


def test_the_literal_message_agent_tick_is_not_what_marks_a_tick():
    # The Android service sends "agent_tick" as the message body, but
    # that string is a log label. Detection is by context key only, so a
    # user who types the words gets a conversation.
    assert is_agent_tick({"user_request": "agent_tick"}) is False
    assert is_machine_turn(None) is False


def test_intent_probe_is_detected_and_is_a_machine_turn():
    assert is_intent_probe(PROBE_CONTEXT) is True
    assert is_machine_turn(PROBE_CONTEXT) is True
    assert is_intent_probe({INTENT_PROBE_KEY: False}) is False
    assert is_agent_tick(PROBE_CONTEXT) is False


# ----------------------------------------------------------------------
# Reading the routing answer
# ----------------------------------------------------------------------

@pytest.mark.parametrize("reply", ["action", "ACTION", " action \n", "Action."])
def test_a_clear_action_reply_routes_to_the_agent(reply):
    assert read_intent(reply) == ACTION


@pytest.mark.parametrize(
    "reply",
    [
        "conversation",
        "",
        "   ",
        None,
        "I think this is an action, but it could be conversation.",
        "unsure",
        "actionable",
    ],
)
def test_anything_unclear_stays_a_conversation(reply):
    # Biased on purpose. Misrouting a conversation costs a screen capture
    # and up to ten silent steps; misrouting an action costs one sentence
    # and the user rephrases.
    assert read_intent(reply) == CONVERSATION


# ----------------------------------------------------------------------
# Prompt isolation (AURA-P0-007)
#
# The old agent prompt was the conversational prompt with a rules section
# appended, so it asked for warm prose and raw JSON in the same breath.
# ----------------------------------------------------------------------

CONVERSATIONAL_SECTIONS = [
    "===== SYSTEM =====",
    "===== PERSONALITY =====",
    "===== WHO YOU ARE =====",
    "===== RESPONSE STYLE =====",
    "===== MEMORY =====",
    "===== VISION =====",
    "===== RECENT CONVERSATION =====",
]


def test_agent_prompt_contains_no_conversational_section():

    prompt = PromptBuilder().build(
        history=[Message(role="user", content="earlier turn")],
        user_message=Message(role="user", content="agent_tick"),
        knowledge=["the user's name is Ember"],
        identity="You are Aura.",
        style="Keep it short.",
        context=AGENT_CONTEXT,
    )

    for section in CONVERSATIONAL_SECTIONS:
        assert section not in prompt

    assert "===== AGENT RULES =====" in prompt
    assert "===== ACCESSIBILITY TREE =====" in prompt


def test_agent_prompt_drops_history_and_recalled_facts():
    # Passing them and finding them absent is the point: the builder
    # ignores conversational material on a tick rather than relying on
    # the caller to withhold it.
    prompt = PromptBuilder().build(
        history=[Message(role="assistant", content="I like strawberries")],
        user_message=Message(role="user", content="agent_tick"),
        knowledge=["the user lives in Hanoi"],
        context=AGENT_CONTEXT,
    )

    assert "strawberries" not in prompt
    assert "Hanoi" not in prompt


def test_intent_prompt_is_only_rules_and_the_message():

    prompt = PromptBuilder().build(
        history=[Message(role="assistant", content="I like strawberries")],
        user_message=Message(role="user", content="mở youtube"),
        knowledge=["the user lives in Hanoi"],
        identity="You are Aura.",
        style="Keep it short.",
        context=PROBE_CONTEXT,
    )

    for section in CONVERSATIONAL_SECTIONS:
        assert section not in prompt

    assert "===== INTENT RULES =====" in prompt
    assert "mở youtube" in prompt
    assert "strawberries" not in prompt
    assert "Hanoi" not in prompt

    # No device sections either: routing is decided from the sentence
    # alone, before any screen has been captured.
    assert "===== ACCESSIBILITY TREE =====" not in prompt
    assert "===== AGENT RULES =====" not in prompt


def test_intent_rules_are_a_system_instruction_and_the_message_is_not():

    prompt = PromptBuilder().build(
        history=[],
        user_message=Message(role="user", content="mở youtube"),
        context=PROBE_CONTEXT,
    )

    system, user = split_prompt(prompt)

    assert "===== INTENT RULES =====" in system
    assert "mở youtube" not in system
    assert "mở youtube" in user


# ----------------------------------------------------------------------
# Manager isolation (AURA-P0-006)
# ----------------------------------------------------------------------

@pytest.mark.parametrize("context", [AGENT_CONTEXT, PROBE_CONTEXT])
def test_a_machine_reply_is_returned_exactly_as_the_model_wrote_it(context):

    llm = FakeLLM('{"action": "click", "node_id": "node_1"}')

    response = manager(llm=llm, style=ShoutyStyle()).chat("agent_tick", context=context)

    assert response.text == '{"action": "click", "node_id": "node_1"}'


def test_the_style_layer_would_otherwise_break_the_parser():
    # Establishes that the assertion above is load-bearing: the same
    # styler applied to a conversational turn really does mangle it.
    llm = FakeLLM('{"action": "click"}')

    response = manager(llm=llm, style=ShoutyStyle()).chat("hello")

    assert response.text == '{ACTION: CLICK}'


@pytest.mark.parametrize("context", [AGENT_CONTEXT, PROBE_CONTEXT])
def test_a_machine_turn_saves_nothing(context):

    store = FakeStore()

    manager(store=store).chat("agent_tick", context=context)

    assert store.saved == []


def test_a_conversational_turn_still_saves_both_halves():

    store = FakeStore()

    manager(llm=FakeLLM("hey"), store=store).chat("hello")

    assert store.saved == [("user", "hello"), ("assistant", "hey")]


@pytest.mark.parametrize("context", [AGENT_CONTEXT, PROBE_CONTEXT])
def test_a_machine_turn_announces_nothing_on_the_bus(context):

    bus = FakeBus()

    manager(bus=bus).chat("agent_tick", context=context)

    # ResponseEvent is what a UI prints and what TTS speaks; publishing
    # an agent step means the user sees or hears the internal JSON.
    assert bus.events == []


def test_a_conversational_turn_still_announces_itself():

    bus = FakeBus()

    manager(llm=FakeLLM("hey"), bus=bus).chat("hello")

    assert UserInputEvent in bus.kinds()
    assert ThinkingEvent in bus.kinds()
    assert ResponseEvent in bus.kinds()


def test_a_provider_failure_is_announced_even_on_a_machine_turn():
    # The exception to the silence. A provider being down is a fact about
    # Aura, not about whoever asked.
    class Broken:
        def generate(self, prompt):
            raise RuntimeError("provider down")

    bus = FakeBus()

    with pytest.raises(RuntimeError):
        manager(llm=Broken(), bus=bus).chat("agent_tick", context=AGENT_CONTEXT)

    assert [type(event) for event in bus.events] == [ErrorEvent]


# ----------------------------------------------------------------------
# The same rules, streaming
# ----------------------------------------------------------------------

def test_streaming_a_machine_turn_yields_but_neither_saves_nor_publishes():

    store = FakeStore()
    bus = FakeBus()

    fragments = list(
        manager(
            llm=FakeLLM('{"action": "back"}'),
            store=store,
            bus=bus,
            style=ShoutyStyle(),
        ).chat_stream("agent_tick", context=AGENT_CONTEXT)
    )

    assert "".join(fragments) == '{"action": "back"}'
    assert store.saved == []
    assert bus.events == []


def test_streaming_a_conversational_turn_is_unchanged():

    store = FakeStore()
    bus = FakeBus()

    fragments = list(
        manager(llm=FakeLLM("hey"), store=store, bus=bus).chat_stream("hello")
    )

    assert "".join(fragments) == "hey"
    assert store.saved == [("user", "hello"), ("assistant", "hey")]
    assert StreamStartedEvent in bus.kinds()
    assert StreamChunkEvent in bus.kinds()
    assert StreamFinishedEvent in bus.kinds()
    assert ResponseEvent in bus.kinds()


# ----------------------------------------------------------------------
# The runtime's half of the probe
# ----------------------------------------------------------------------

class StubEngine:

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def chat(self, message, source="text", context=None):
        self.calls.append((message, context))
        return Response(text=self.reply)


class StubServices:

    def __init__(self, engine):
        self.engine = engine

        # Matches the real `Services` default. A probe is a machine turn,
        # so nothing proactive may be nudged by one - `proactive=None`
        # keeps that assertion about the production path rather than
        # about a stub that happens to lack the attribute.
        self.proactive = None


def runtime_with(reply):

    from server.runtime import ServerRuntime

    runtime = ServerRuntime.__new__(ServerRuntime)
    runtime.companion_engine = None
    runtime.services = StubServices(StubEngine(reply))

    return runtime


@pytest.mark.parametrize(
    "reply,expected",
    [
        ("action", ACTION),
        ("Action.", ACTION),
        ("conversation", CONVERSATION),
        ("I'm not sure what you mean", CONVERSATION),
        ("", CONVERSATION),
    ],
)
def test_the_runtime_normalises_a_probe_reply_to_one_word(reply, expected):
    # Normalised on the server so the rule has one implementation and a
    # client only has to compare two strings.
    runtime = runtime_with(reply)

    assert runtime.chat("mở youtube", context=PROBE_CONTEXT).text == expected


def test_the_runtime_leaves_a_non_probe_reply_alone():

    runtime = runtime_with("action")

    assert runtime.chat("hello").text == "action"
    assert runtime.chat("agent_tick", context=AGENT_CONTEXT).text == "action"
