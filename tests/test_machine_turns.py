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
    absorb,
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

from core.cognitive import CognitiveState, CognitiveStore

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


def manager(
    llm=None, store=None, bus=None, style=None, cognitive=None, clock=None
):

    return ConversationManager(
        memory=store if store is not None else FakeStore(),
        builder=PromptBuilder(),
        llm=llm if llm is not None else FakeLLM(),
        events=bus,
        style=style,
        cognitive=cognitive,
        clock=clock,
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


# ----------------------------------------------------------------------
# The one world fact a tick does need
#
# Everything the agent prompt strips is a section that exists to make Aura
# sound like herself. The time is not one of those - it is a fact about the
# present, the same category as DEVICE STATE, which the prompt already
# carries. Section 16 says never rely on the model guessing the current
# time, and "hôm nay", "today" and "tomorrow" are ordinary things to put in
# a request that a model with no date in its prompt can only invent.
# ----------------------------------------------------------------------

class TestATickKnowsWhenItIs:

    def build(self, temporal):

        return PromptBuilder().build(
            history=[],
            user_message=Message(role="user", content="agent_tick"),
            contexts=[],
            context=AGENT_CONTEXT,
            temporal=temporal,
        )

    def test_the_time_reaches_the_agent_prompt(self):

        prompt = self.build(["Today is Monday, 24 August 2026.", "It is 14:05."])

        assert "===== CURRENT TIME =====" in prompt
        assert "24 August 2026" in prompt

    def test_it_sits_next_to_the_request_that_needs_it(self):

        # Adjacent to AGENT RULES rather than at the top, because the
        # request text lives in AGENT RULES and a date is needed exactly
        # when that sentence is read. The accessibility tree is large; a
        # date placed above it is a date read a long way from its use.
        prompt = self.build(["Today is Monday, 24 August 2026."])

        assert prompt.index("===== CURRENT TIME =====") > prompt.index(
            "===== ACCESSIBILITY TREE ====="
        )
        assert prompt.index("===== CURRENT TIME =====") < prompt.index(
            "===== AGENT RULES ====="
        )

    def test_no_clock_leaves_the_agent_prompt_byte_identical(self):

        # The guarantee every other optional section here makes: an unused
        # subsystem costs zero tokens, and a deployment with no clock gets
        # exactly the prompt it got before this existed.
        for nothing in (None, [], [""], ["   "]):
            assert self.build(nothing) == self.build(None)

        assert "===== CURRENT TIME =====" not in self.build(None)

    def test_the_prompt_stays_reproducible(self):

        # `_build_time`'s promise, which must survive reaching a second
        # caller: the builder never sees a datetime and never calls a
        # clock, so the same lines twice are the same prompt twice.
        lines = ["Today is Monday, 24 August 2026."]

        assert self.build(lines) == self.build(list(lines))

    def test_a_tick_through_the_manager_carries_the_clock(self):

        from core.temporal import TemporalClock

        llm = FakeLLM()

        manager(llm=llm, clock=TemporalClock()).chat(
            "agent_tick", context=AGENT_CONTEXT
        )

        assert "===== CURRENT TIME =====" in llm.prompts[0]

    def test_a_tick_without_a_clock_carries_no_time(self):

        llm = FakeLLM()

        manager(llm=llm).chat("agent_tick", context=AGENT_CONTEXT)

        assert "===== CURRENT TIME =====" not in llm.prompts[0]

    def test_the_date_is_read_off_the_clock_and_not_hard_coded(self):

        # Section 16's second prohibition. A literal date would pass every
        # test above and be wrong on the second day.
        from datetime import datetime

        from core.temporal import TemporalClock

        llm = FakeLLM()

        manager(llm=llm, clock=TemporalClock()).chat(
            "agent_tick", context=AGENT_CONTEXT
        )

        section = llm.prompts[0].split("===== CURRENT TIME =====")[1]
        now = datetime.now()

        assert str(now.year) in section
        assert str(now.day) in section

    def test_a_broken_clock_costs_the_time_and_not_the_tick(self):

        # The same bargain `_temporal_lines` already makes on the
        # conversational path. A tick is the device waiting on an action;
        # failing it over a clock would strand the task.
        class Exploding:
            def context(self):
                raise RuntimeError("no timezone database")

        llm = FakeLLM()

        reply = manager(llm=llm, clock=Exploding()).chat(
            "agent_tick", context=AGENT_CONTEXT
        )

        assert "===== CURRENT TIME =====" not in llm.prompts[0]
        assert "===== AGENT RULES =====" in llm.prompts[0]
        assert reply.text == '{"action": "complete"}'


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

    def chat(self, message, source="text", context=None, **kwargs):
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


# ======================================================================
# Recording what a tick reported
# ======================================================================


class TestAbsorbingATick:
    """
    The tick is where reality arrives, so it is where reality gets
    recorded.

    Today the device state in a tick is read once, rendered into a prompt
    by `PromptBuilder._build_agent_prompt`, and dropped. The phone is the
    only thing that remembers what has already been done, and it tells the
    model in prose - `AuraAccessibilityService` literally sends "This
    action was already successfully executed. Do not repeat it." as an
    error string. That works exactly as well as the model's willingness to
    believe it.

    `absorb` writes the same report into the session's `CognitiveState`
    instead, so "have I already opened YouTube?" becomes a lookup rather
    than a sentence in a prompt. The parse follows the format
    `AuraAccessibilityService.formatActionHistory` actually emits and
    `AccessibilityAgentTest` pins - `kind(args) [VERIFIED]` - and nothing
    beyond it.
    """

    def test_it_records_the_foreground_application(self):
        state = CognitiveState()

        absorb(state, {"device": {}, "app": {"package": "com.android.chrome"}})

        assert state.focus.application == "com.android.chrome"

    def test_it_records_the_screen_when_the_tick_carries_one(self):
        state = CognitiveState()

        absorb(state, {
            "device": {},
            "app": {"package": "com.android.chrome", "activity": ".Main"},
        })

        assert state.focus.screen == ".Main"

    def test_it_keeps_the_request_in_the_owners_own_words(self):
        # Verbatim. Section 23 says the query typed into a search box is
        # `Minecraft` and not `search for Minecraft`; that trimming is a
        # decision for whoever builds the action, and storing a
        # pre-trimmed goal would hide where it happened.
        state = CognitiveState()

        absorb(state, {
            "device": {},
            "user_request": "mở youtube tìm Minecraft",
        })

        assert state.goal == "mở youtube tìm Minecraft"

    def test_a_verified_action_is_recorded_as_succeeded(self):
        state = CognitiveState()

        absorb(state, {
            "device": {},
            "completed_actions": ["open_app(com.google.android.youtube) [VERIFIED]"],
        })

        assert state.has_succeeded("open_app", "com.google.android.youtube")

    def test_an_action_with_no_argument_is_recorded_with_an_empty_target(self):
        state = CognitiveState()

        absorb(state, {"device": {}, "completed_actions": ["home() [VERIFIED]"]})

        assert state.has_succeeded("home")

    def test_input_text_is_keyed_by_the_node_it_typed_into(self):
        # The emitted form is `input_text(node, "text")`. The node is the
        # identity - the same box typed into twice is one action - and the
        # text is not, because a retry with corrected text is still the
        # same step of the task.
        state = CognitiveState()

        absorb(state, {
            "device": {},
            "completed_actions": ['input_text(search_box, "Minecraft") [VERIFIED]'],
        })

        assert state.has_succeeded("input_text", "search_box")

    def test_the_older_action_history_key_is_read_too(self):
        # `PromptBuilder` accepts either name, so this must accept both or
        # the two disagree about what happened.
        state = CognitiveState()

        absorb(state, {"device": {}, "action_history": ["back() [VERIFIED]"]})

        assert state.has_succeeded("back")

    def test_the_same_tick_absorbed_twice_does_not_double_the_attempts(self):
        # Every tick re-sends the whole history. If absorbing it counted
        # attempts, a ten-step task would exhaust any retry bound on step
        # two through nothing but repetition of the report.
        state = CognitiveState()
        context = {
            "device": {},
            "completed_actions": ["open_app(com.google.android.youtube) [VERIFIED]"],
        }

        absorb(state, context)
        absorb(state, context)

        assert state.attempts_for("open_app", "com.google.android.youtube") == 1

    def test_it_reports_whether_anything_changed(self):
        # The tick loop needs to tell a screen that moved from one that
        # did not, which is the same reason `observe` returns a bool.
        state = CognitiveState()
        context = {"device": {}, "app": {"package": "com.android.chrome"}}

        assert absorb(state, context) is True
        assert absorb(state, context) is False

    def test_an_unparseable_entry_is_skipped_rather_than_guessed_at(self):
        # The history is prose built for a prompt. A line that does not
        # match the emitted format is not evidence of anything, and
        # inventing a record from it would put a fact in the state that no
        # device ever reported.
        state = CognitiveState()

        absorb(state, {
            "device": {},
            "completed_actions": ["something happened", "back() [VERIFIED]"],
        })

        assert [record.kind for record in state.succeeded] == ["back"]

    def test_a_conversation_turn_is_left_alone(self):
        # No device keys, no tick, nothing to record. A chat message must
        # not silently become a goal.
        state = CognitiveState()

        assert absorb(state, {"user_request": "chào cậu"}) is False
        assert state.goal == ""

    def test_a_missing_context_is_not_an_error(self):
        state = CognitiveState()

        assert absorb(state, None) is False


class TestTheManagerRecordsTheTick:
    """
    The wiring, end to end.

    `absorb` being correct proves nothing on its own if no live turn ever
    calls it. These go through `ConversationManager.chat` with a real
    store attached, which is the only way to show that a tick arriving
    over HTTP leaves a trace.
    """

    def test_a_tick_through_the_manager_lands_in_the_store(self):
        store = CognitiveStore()

        manager(cognitive=store).chat(
            "agent_tick", context=AGENT_CONTEXT, session_id="phone"
        )

        state = store.peek("phone")

        assert state is not None
        assert state.focus.application == "com.example.app"
        assert state.goal == "mở youtube"

    def test_two_ticks_of_one_task_share_one_record(self):
        # The property the whole phase is for. The second tick can see
        # what the first one accomplished, without asking a model.
        store = CognitiveStore()
        conversation = manager(cognitive=store)

        conversation.chat(
            "agent_tick",
            context={**AGENT_CONTEXT, "completed_actions": [
                "open_app(com.google.android.youtube) [VERIFIED]",
            ]},
            session_id="phone",
        )
        conversation.chat(
            "agent_tick", context=AGENT_CONTEXT, session_id="phone"
        )

        assert store.peek("phone").has_succeeded(
            "open_app", "com.google.android.youtube"
        )

    def test_two_phones_do_not_share_a_record(self):
        store = CognitiveStore()
        conversation = manager(cognitive=store)

        conversation.chat(
            "agent_tick", context=AGENT_CONTEXT, session_id="phone-a"
        )
        conversation.chat(
            "agent_tick",
            context={**AGENT_CONTEXT, "app": {"package": "com.android.chrome"}},
            session_id="phone-b",
        )

        assert store.peek("phone-a").focus.application == "com.example.app"
        assert store.peek("phone-b").focus.application == "com.android.chrome"

    def test_a_conversation_turn_leaves_no_cognitive_trace(self):
        # Absorbing is for ticks. A spoken turn has no device state to
        # record, and recording the message as a goal would make every
        # greeting look like a task.
        store = CognitiveStore()

        manager(cognitive=store).chat("chào cậu", session_id="phone")

        assert store.peek("phone") is None

    def test_a_manager_with_no_store_still_serves_ticks(self):
        # Every existing caller passes no store. The tick must work
        # exactly as it did before.
        response = manager().chat("agent_tick", context=AGENT_CONTEXT)

        assert response.text

    def test_a_broken_store_does_not_break_the_turn(self):
        # Bookkeeping alongside the turn, not part of it. An agent that
        # stops mid-task because its notebook tore is worse than one
        # working from a stale note.
        class Exploding:
            def for_session(self, session_id):
                raise RuntimeError("disk on fire")

        response = manager(cognitive=Exploding()).chat(
            "agent_tick", context=AGENT_CONTEXT
        )

        assert response.text


# ======================================================================
# Telling the model where it is
# ======================================================================


class TestThePlanReachesThePrompt:
    """
    The point of phase 5, end to end.

    Absorbing a tick made progress a lookup, but nothing read it back:
    `prompt_builder` does not import `core.cognitive`, so the state was
    write-mostly and the model still re-derived the whole task every step
    from a flat list of completed actions. These tests pin the return
    path - request in, plan out, progress marked - through the manager
    rather than the planner, because the planner passing its own unit
    tests while nothing consumed it is exactly the gap being closed.
    """

    def build(self, request, cognitive=None, llm=None):
        llm = llm or FakeLLM()
        store = cognitive if cognitive is not None else CognitiveStore()
        context = dict(AGENT_CONTEXT, user_request=request)

        manager(llm=llm, cognitive=store).chat(
            "agent_tick", context=context, session_id="s1"
        )

        return llm.prompts[-1], store

    def test_a_plannable_request_gains_a_plan_section(self):
        prompt, _ = self.build("open YouTube and search Minecraft")

        assert "===== PLAN =====" in prompt

    def test_the_plan_lists_the_steps_the_model_must_take(self):
        prompt, _ = self.build("open YouTube and search Minecraft")

        assert "Open YouTube" in prompt
        assert 'Type only "Minecraft" into the search box' in prompt
        assert "Submit the search" in prompt

    def test_the_plan_sits_between_the_history_and_the_rules(self):
        # Order is the whole reason it works: what has happened, then what
        # remains, then the rules for choosing the next action. Rules
        # first would make the plan look like new information arriving
        # after the instructions for using it.
        prompt, _ = self.build("open YouTube and search Minecraft")

        assert prompt.index("===== PLAN =====") < prompt.index(
            "===== AGENT RULES ====="
        )

    def test_progress_reported_by_the_device_shows_up_as_progress(self):
        # The two halves meeting. The device says it launched YouTube, the
        # tick absorbs that, and the plan the next prompt carries has the
        # launch struck off - without anyone counting steps.
        llm = FakeLLM()
        store = CognitiveStore()
        context = dict(
            AGENT_CONTEXT,
            user_request="open YouTube and search Minecraft",
            completed_actions=["open_app(com.google.android.youtube) [VERIFIED]"],
        )

        manager(llm=llm, cognitive=store).chat(
            "agent_tick", context=context, session_id="s1"
        )

        plan = llm.prompts[-1].split("===== PLAN =====")[1]
        assert "Open YouTube  [DONE]" in plan
        assert "Focus the search box  <- NOW" in plan

    def test_exactly_one_step_is_marked_now(self):
        prompt, _ = self.build("open YouTube and search Minecraft")

        plan = prompt.split("===== PLAN =====")[1].split("=====")[0]
        assert plan.count("<- NOW") == 1

    def test_an_unplannable_request_leaves_the_prompt_alone(self):
        # No section at all, not an empty one. A request the planner does
        # not understand must cost the tick nothing, or every phrasing it
        # cannot parse becomes a regression.
        prompt, _ = self.build("what is the weather like")

        assert "===== PLAN =====" not in prompt

    def test_a_manager_with_no_cognitive_store_still_works(self):
        # The store is optional everywhere else it is touched, and a
        # caller that never wired one up must not lose the agent loop.
        llm = FakeLLM()

        manager(llm=llm).chat(
            "agent_tick",
            context=dict(AGENT_CONTEXT, user_request="open YouTube and search X"),
            session_id="s1",
        )

        assert "===== PLAN =====" not in llm.prompts[-1]
        assert "===== AGENT RULES =====" in llm.prompts[-1]

    def test_the_query_reaches_the_prompt_without_the_verb(self):
        # Section 23, now enforced on the way in rather than repaired on
        # the way out by `sanitizeSearchQuery`.
        prompt, _ = self.build("open YouTube and search for Minecraft")

        plan = prompt.split("===== PLAN =====")[1].split("=====")[0]
        assert '"Minecraft"' in plan
        assert "search for Minecraft" not in plan

    def test_the_plan_is_recorded_on_the_state_too(self):
        # `set_plan` and `enter_node` have existed since phase 4 and were
        # never called by anything in production. They are the state's
        # answer to "what are we doing and where are we", and a plan that
        # lived only in a rendered prompt string would leave phase 6 with
        # nothing to build a task graph from.
        _, store = self.build("open YouTube and search Minecraft")
        state = store.peek("s1")

        assert state.plan == (
            "open_app",
            "focus_search",
            "enter_query",
            "submit_search",
            "await_results",
        )
        assert state.task_node == "open_app"

    def test_a_conversational_turn_is_not_planned(self):
        # The planner is for the agent loop. A chat turn that happened to
        # contain the word "search" must not acquire a device plan.
        llm = FakeLLM(reply="sure thing")
        store = CognitiveStore()

        manager(llm=llm, cognitive=store).chat(
            "can you search for a good recipe", session_id="s1"
        )

        assert "===== PLAN =====" not in llm.prompts[-1]
        assert store.peek("s1") is None

    def test_a_planning_failure_does_not_take_down_the_turn(self):
        # Same bargain `_absorb` makes: an agent mid-task must not stop
        # because its notebook tore. The device is waiting for an action.
        class Exploding:
            def for_session(self, session_id):
                raise RuntimeError("store is unwell")

            def peek(self, session_id):
                return None

        llm = FakeLLM()

        reply = manager(llm=llm, cognitive=Exploding()).chat(
            "agent_tick",
            context=dict(AGENT_CONTEXT, user_request="open YouTube and search X"),
            session_id="s1",
        )

        assert reply.text == '{"action": "complete"}'
        assert "===== AGENT RULES =====" in llm.prompts[-1]


# ----------------------------------------------------------------------
# The agent says what it is doing (section 18)
#
# Phases 4-11 built a cognitive state, a planner, a task graph, a
# verification pass and a recovery engine, and none of them published a
# single event. So the one thing a person actually wants to watch - an
# agent working through a task on a phone - was the one thing the bus
# could not see, and the only trace of a task was whatever the model
# happened to be told next.
#
# Edge triggered, not per tick. `_plan` recomputes the whole graph every
# tick because `plan_for` is pure, so a naive publish would emit the same
# step over and over and reproduce as noise the very repetition section
# 10 exists to prevent. An event here means something moved.
# ----------------------------------------------------------------------

class Recorder:
    """A bus that keeps what it was given."""

    def __init__(self):
        self.events = []

    def publish(self, event) -> None:
        self.events.append(event)

    def of(self, kind):
        return [event for event in self.events if isinstance(event, kind)]


class TestTheAgentAnnouncesItsProgress:

    def tick(self, bus, store, request, llm=None, **context):
        manager(llm=llm or FakeLLM(), cognitive=store, bus=bus).chat(
            "agent_tick",
            context=dict(AGENT_CONTEXT, user_request=request, **context),
            session_id="s1",
        )

    def test_entering_a_step_is_announced(self):
        from events.types import TaskStepChangedEvent

        bus, store = Recorder(), CognitiveStore()

        self.tick(bus, store, "open YouTube and search Minecraft")

        moves = bus.of(TaskStepChangedEvent)

        assert len(moves) == 1
        assert moves[0].step == "open_app"

    def test_the_event_says_where_in_the_plan_it_is(self):
        from events.types import TaskStepChangedEvent

        bus, store = Recorder(), CognitiveStore()

        self.tick(bus, store, "open YouTube and search Minecraft")

        move = bus.of(TaskStepChangedEvent)[0]

        assert move.index == 0
        assert move.total == 5
        assert move.goal

        # And it has to move. A first tick sits at index 0, so asserting
        # only that much is satisfied by a hard-coded zero - which is what
        # a mutation replacing the whole lookup with `0` proved, by
        # surviving. A second tick after a verified launch is the cheapest
        # assertion that cannot be met by a constant.
        self.tick(
            bus, store, "open YouTube and search Minecraft",
            completed_actions=["open_app(com.google.android.youtube) [VERIFIED]"],
        )

        moved = bus.of(TaskStepChangedEvent)[-1]

        assert moved.step == "focus_search"
        assert moved.index == 1
        assert moved.total == 5

    def test_a_tick_that_changes_nothing_announces_nothing(self):
        """
        The whole reason this is edge triggered.

        Two identical ticks are the normal case while an action is still
        in flight, and a publish per tick would fill the bus with the
        same step - which is what section 10's repeat loop looks like from
        the outside, arriving as noise rather than as a fault.
        """

        from events.types import TaskStepChangedEvent

        bus, store = Recorder(), CognitiveStore()

        self.tick(bus, store, "open YouTube and search Minecraft")
        self.tick(bus, store, "open YouTube and search Minecraft")

        assert len(bus.of(TaskStepChangedEvent)) == 1

    def test_real_progress_is_announced_again(self):
        """
        The other direction, so the test above cannot be satisfied by
        publishing once and never again.
        """

        from events.types import TaskStepChangedEvent

        bus, store = Recorder(), CognitiveStore()

        self.tick(bus, store, "open YouTube and search Minecraft")
        self.tick(
            bus,
            store,
            "open YouTube and search Minecraft",
            completed_actions=[
                "open_app(com.google.android.youtube) [VERIFIED]"
            ],
        )

        moves = bus.of(TaskStepChangedEvent)

        assert [move.step for move in moves] == ["open_app", "focus_search"]

    def test_an_unplannable_request_announces_nothing(self):
        from events.types import TaskStepChangedEvent

        bus, store = Recorder(), CognitiveStore()

        self.tick(bus, store, "how are you feeling today")

        assert bus.of(TaskStepChangedEvent) == []

    def test_a_finished_task_is_announced_once(self):
        from events.types import TaskFinishedEvent

        bus, store = Recorder(), CognitiveStore()
        done = [
            "open_app(com.google.android.youtube) [VERIFIED]",
            "input_text(search_box) [VERIFIED]",
            "submit() [VERIFIED]",
            "wait() [VERIFIED]",
            "click(result_0) [VERIFIED]",
        ]

        self.tick(
            bus, store, "open YouTube and search lofi and pick the first result",
            completed_actions=done,
        )
        self.tick(
            bus, store, "open YouTube and search lofi and pick the first result",
            completed_actions=done,
        )

        assert len(bus.of(TaskFinishedEvent)) == 1

    def test_a_task_that_cannot_go_on_is_announced_as_stuck(self):
        """
        The other way a task ends, and the one that matters more.

        Finished and stuck are both "no current node", so a subscriber
        that could not tell them apart would congratulate the owner on a
        search that never happened. This is the half that had no test at
        all until a mutation of the `is_stuck` branch was checked and
        found to be logically equivalent - equivalent mutations survive
        without proving anything, and the branch underneath was untested.

        The retry limit is exhausted by asking `may_retry` rather than by
        writing the number down, so the test still means what it says if
        the policy in `brain/recovery.py` changes. Section 33: behaviour,
        not implementation details.
        """

        from brain.recovery import may_retry
        from events.types import TaskStepChangedEvent, TaskStuckEvent

        bus, store = Recorder(), CognitiveStore()
        request = "open YouTube and search Minecraft"
        app = "com.google.android.youtube"

        # One ordinary tick first, so there is a `was` to report. A first
        # tick that is already hopeless has no previous step to name.
        self.tick(bus, store, request)

        moves = bus.of(TaskStepChangedEvent)
        assert moves and moves[-1].step == "open_app"

        state = store.for_session("s1")

        for _ in range(12):
            if not may_retry(state, "open_app", app):
                break
            state.begin_action("open_app", app)
            state.fail_action("open_app", app, "app not found")
        else:
            raise AssertionError("the retry bound never ran out")

        self.tick(bus, store, request)

        stuck = bus.of(TaskStuckEvent)

        assert len(stuck) == 1
        assert stuck[0].step == "open_app"
        assert stuck[0].goal == request

    def test_a_stuck_task_is_not_announced_as_finished(self):
        """
        The confusion named above, asserted rather than described.
        """

        from brain.recovery import may_retry
        from events.types import TaskFinishedEvent

        bus, store = Recorder(), CognitiveStore()
        request = "open YouTube and search Minecraft"
        app = "com.google.android.youtube"

        self.tick(bus, store, request)

        state = store.for_session("s1")

        for _ in range(12):
            if not may_retry(state, "open_app", app):
                break
            state.begin_action("open_app", app)
            state.fail_action("open_app", app, "app not found")

        self.tick(bus, store, request)

        assert bus.of(TaskFinishedEvent) == []

    def test_a_planning_failure_still_announces_nothing_and_survives(self):
        """
        Same bargain the rest of `_plan` makes. A device is waiting for an
        action, and telling the bus about a task is worth strictly less
        than the action.
        """

        from events.types import TaskStepChangedEvent

        class Exploding:
            def for_session(self, session_id):
                raise RuntimeError("store is unwell")

            def peek(self, session_id):
                return None

        bus = Recorder()
        llm = FakeLLM()

        reply = manager(llm=llm, cognitive=Exploding(), bus=bus).chat(
            "agent_tick",
            context=dict(AGENT_CONTEXT, user_request="open YouTube and search X"),
            session_id="s1",
        )

        assert reply.text == '{"action": "complete"}'
        assert bus.of(TaskStepChangedEvent) == []

    def test_the_goal_is_not_written_to_the_log(self):
        """
        Section 30 reaching a new event without anyone deciding it should.

        A goal is built from the owner's own request, so it is exactly the
        text the event log must not copy into a file. Nothing was added to
        `SAFE_FIELDS` for these events, and default-deny is what makes
        that the outcome rather than an oversight.
        """

        from events.log import describe
        from events.types import TaskStepChangedEvent

        line = describe(
            TaskStepChangedEvent(
                goal="open YouTube and search Minecraft",
                step="open_app",
                index=0,
                total=6,
            )
        )

        assert "Minecraft" not in line
        assert "step=open_app" in line
        assert "index=0" in line
