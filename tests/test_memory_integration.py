"""
Integration tests for the wired memory path.

`tests/test_memory_2.py` tests the pipeline in isolation and
`tests/test_temporal.py` tests the clock. This file tests the thing
neither can: that a real `ConversationManager` turn reaches them, and
that the things which must NOT reach them still don't.

The Phase 7 rule is the one worth guarding hardest. Machine turns - a
device agent step, an intent probe - are answered by a parser, not read
by a person, and a system that remembers its own JSON actions starts
citing them back as things the user said. That rule was enforced at the
transcript; Memory 2.0 added a second store, and a second store is a
second place for the rule to be forgotten. Hence the tests below that
assert on *both*.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from brain.conversation import ConversationManager
from brain.message import Message
from brain.prompt_builder import PromptBuilder
from brain.prompt_sections import MEMORY, TIME
from core.temporal import TemporalClock
from launcher.services import build_services
from memory.models import Base
from memory.pipeline import MemoryPipeline


NOW = datetime(2026, 8, 11, 14, 30, 0)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    made = sessionmaker(bind=engine)()
    yield made
    made.close()


@pytest.fixture
def clock():
    return TemporalClock(now=lambda: NOW)


@pytest.fixture
def pipeline(session, clock):
    return MemoryPipeline(session=session, clock=clock)


class StubLLM:
    """Records the prompt it was given and returns a fixed reply."""

    def __init__(self, reply="Right, noted."):
        self.reply = reply
        self.prompts = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


class StubStore:
    """The transcript, in a list."""

    def __init__(self):
        self.saved = []

    def save(self, role, content):
        self.saved.append((role, content))

    def get_recent(self, limit=20):
        return []


def manager_with(pipeline=None, clock=None, llm=None, store=None):

    return ConversationManager(
        memory=store or StubStore(),
        builder=PromptBuilder(),
        llm=llm or StubLLM(),
        pipeline=pipeline,
        clock=clock,
    )


# ======================================================================
# The TIME section
# ======================================================================

def test_the_prompt_carries_the_current_time(clock):

    llm = StubLLM()

    manager_with(clock=clock, llm=llm).chat("hey")

    prompt = llm.prompts[0]

    assert TIME in prompt
    assert "Tuesday 11 August 2026" in prompt
    assert "14:30" in prompt


def test_no_clock_means_no_time_section():
    """A prompt built the way it was built before Phase 8."""

    llm = StubLLM()

    manager_with(llm=llm).chat("hey")

    assert TIME not in llm.prompts[0]


def test_the_time_in_the_prompt_comes_from_the_injected_clock():
    """
    Not from `datetime.now()` inside the builder. This is what makes
    a prompt reproducible, and it is why the clock is a constructor
    argument rather than a module import.
    """

    llm = StubLLM()

    frozen = TemporalClock(now=lambda: datetime(2001, 1, 1, 9, 0))

    manager_with(clock=frozen, llm=llm).chat("hey")

    assert "January 2001" in llm.prompts[0]


def test_a_broken_clock_costs_the_time_not_the_reply():

    class Broken:
        def context(self):
            raise RuntimeError("no clock")

    llm = StubLLM()

    response = manager_with(clock=Broken(), llm=llm).chat("hey")

    assert response.text
    assert TIME not in llm.prompts[0]


# ======================================================================
# Writing: what a real turn puts into memory
# ======================================================================

def test_a_meaningful_turn_reaches_the_episodic_store(pipeline, clock):

    manager_with(pipeline=pipeline, clock=clock).chat(
        "I'm learning Japanese because I want to read manga untranslated"
    )

    stored = pipeline.episodic.recent()

    assert len(stored) == 1
    assert "Japanese" in stored[0].content


def test_a_trivial_turn_reaches_nothing(pipeline, clock):

    manager_with(pipeline=pipeline, clock=clock).chat("ok thanks")

    assert len(pipeline.episodic) == 0


def test_the_assistants_reply_never_enters_long_term_memory(pipeline, clock):
    """
    Aura's own output is not evidence about the user. A system that
    remembers what it said starts citing itself within a few turns.
    """

    llm = StubLLM(reply="I'm learning Japanese and I love manga")

    store = StubStore()

    manager_with(
        pipeline=pipeline, clock=clock, llm=llm, store=store
    ).chat("ok thanks")

    # The transcript takes both sides - that is what a transcript is.
    assert ("assistant", llm.reply) in store.saved

    # Long term memory takes neither, here: the user's turn was trivial
    # and the assistant's turn is never offered at all.
    assert len(pipeline.episodic) == 0


def test_only_the_user_turn_is_offered_to_the_pipeline(pipeline, clock):

    seen = []

    original = pipeline.observe

    def recording(role, content):
        seen.append(role)
        return original(role, content)

    pipeline.observe = recording

    manager_with(pipeline=pipeline, clock=clock).chat("I use Vietnamese daily")

    assert seen == ["user"]


def test_a_broken_pipeline_costs_the_memory_not_the_reply(clock):

    class Exploding:
        def observe(self, role, content):
            raise RuntimeError("database is gone")

        def memory_lines(self, query, **kwargs):
            raise RuntimeError("database is still gone")

    response = manager_with(pipeline=Exploding(), clock=clock).chat(
        "I'm rewriting the retriever this week"
    )

    assert response.text


def test_stored_memory_is_dated_by_the_injected_clock(pipeline, clock):

    manager_with(pipeline=pipeline, clock=clock).chat(
        "I started learning Japanese"
    )

    assert pipeline.episodic.recent()[0].occurred_at.startswith("2026-08-11")


# ======================================================================
# Machine turn isolation - the Phase 7 rule, on the new store
# ======================================================================

AGENT_CONTEXT = {
    "device": {"width": 1080, "height": 2400},
    "accessibility_tree": {"nodes": []},
    "user_request": "open youtube",
}

PROBE_CONTEXT = {"intent_probe": True}


@pytest.mark.parametrize(
    "context", [AGENT_CONTEXT, PROBE_CONTEXT], ids=["agent_tick", "intent_probe"]
)
def test_a_machine_turn_reaches_neither_store(pipeline, clock, context):

    store = StubStore()

    manager_with(pipeline=pipeline, clock=clock, store=store).chat(
        "I'm going to rewrite the retriever", context=context
    )

    assert store.saved == []
    assert len(pipeline.episodic) == 0
    assert len(pipeline.temporary) == 0


def test_a_machine_turn_prompt_has_no_memory_section(pipeline, clock):
    """
    The reply to this is parsed by an accessibility service. Every
    section that exists to make Aura sound like herself is absent, and
    recalled memory joins that list rather than becoming an exception to
    it - a device step that quotes the owner's private facts at a JSON
    parser has spent them for nothing and gained no accuracy.

    This test asserted `TIME not in prompt` as well, and that half was
    reversed on purpose in Phase 10. The rule this docstring names is
    what reversed it: the sections a tick strips are stripped for
    existing to make Aura sound like herself, and the time is not one of
    those. It is a fact about the present, the same category as DEVICE
    STATE, which this prompt has always carried. The two arrived in the
    same sprint and were pinned together for that reason rather than
    because one rule covered both.

    What made it urgent: the request reaches the model in the owner's own
    words, so "hom nay" and "tomorrow morning" arrive with it, and
    `input_text` takes free text - a model asked to type a date with no
    date in its prompt does not decline, it invents one (section 16).
    `TestATickKnowsWhenItIs` in `tests/test_machine_turns.py` owns that
    half now, including the byte-identical-without-a-clock guarantee.
    """

    llm = StubLLM(reply='{"action": "home"}')

    manager_with(pipeline=pipeline, clock=clock, llm=llm).chat(
        "open youtube", context=AGENT_CONTEXT
    )

    assert MEMORY not in llm.prompts[0]


# ======================================================================
# The recall gate, end to end
# ======================================================================

def test_recall_off_keeps_episodes_out_of_a_real_prompt(pipeline, clock):
    """
    The whole chain, not the gate on its own: a stored episode, the
    owner's setting off, and a real `ConversationManager` turn whose
    prompt does not carry it.

    This is the test that would have caught the original bug, and the
    reason it did not exist is instructive - the unit tests asserted
    that `build_memory_pipeline` *stores* `recall_enabled`, and the
    settings tests asserted that PATCH *reports* `memory.recall` as
    applied. Both passed. Nothing asked whether a turn changed.
    """

    llm = StubLLM()

    pipeline.observe("user", "I just finished the sqlite migration")
    pipeline.recall_enabled = False

    manager_with(pipeline=pipeline, clock=clock, llm=llm).chat(
        "how did the migration go"
    )

    # "sqlite" and not "migration": the query carries "migration" into the
    # prompt as the user's own message whatever recall does, so asserting
    # on it would fail with recall correctly off - and, in the paired test
    # below, would pass with recall entirely broken. "sqlite" appears only
    # in the stored episode, so it is the one word here that discriminates.
    assert "sqlite" not in llm.prompts[0].lower()


def test_recall_on_puts_them_in_the_same_prompt(pipeline, clock):
    """The pair to the above: same episode, same query, gate open. A
    fix that silences recall outright must fail here."""

    llm = StubLLM()

    pipeline.observe("user", "I just finished the sqlite migration")
    pipeline.recall_enabled = True

    manager_with(pipeline=pipeline, clock=clock, llm=llm).chat(
        "how did the migration go"
    )

    prompt = llm.prompts[0]

    assert MEMORY in prompt
    assert "sqlite" in prompt.lower()


def test_recall_off_still_carries_who_the_owner_is(pipeline, clock):
    """
    Turning recall off must not cost Aura the owner's identity. The
    section survives with the user model in it, because `memory.recall`
    gates the episodic search and not the tier that holds "she speaks
    Vietnamese" - a checkbox named recall that also erased identity
    would be one control silently meaning three.
    """

    llm = StubLLM()

    pipeline.user_model.confirm(
        "identity.primary_language", "Vietnamese", category="identity"
    )
    pipeline.observe("user", "I just finished the sqlite migration")
    pipeline.recall_enabled = False

    manager_with(pipeline=pipeline, clock=clock, llm=llm).chat(
        "what language do I speak"
    )

    prompt = llm.prompts[0]

    assert MEMORY in prompt
    assert "Vietnamese" in prompt
    assert "sqlite" not in prompt.lower()


def test_a_machine_turn_cannot_be_recalled_later(pipeline, clock):
    """
    The failure this prevents: an agent step stored as memory, recalled
    on the next real turn, and read by the model as something the user
    said out loud.
    """

    manager = manager_with(pipeline=pipeline, clock=clock)

    manager.chat("open youtube", context=AGENT_CONTEXT)

    lines = pipeline.memory_lines("youtube")

    assert not any("youtube" in line.lower() for line in lines)


def test_a_streamed_machine_turn_reaches_neither_store(pipeline, clock):

    store = StubStore()

    manager = manager_with(pipeline=pipeline, clock=clock, store=store)

    list(manager.chat_stream("open youtube", context=AGENT_CONTEXT))

    assert store.saved == []
    assert len(pipeline.episodic) == 0


def test_a_streamed_conversation_turn_does_reach_memory(pipeline, clock):
    """The other half: streaming must not silently skip memory."""

    manager = manager_with(pipeline=pipeline, clock=clock)

    list(manager.chat_stream("I'm learning Japanese for manga"))

    assert len(pipeline.episodic) == 1


# ======================================================================
# Reading: recall reaching the prompt
# ======================================================================

def test_a_stored_memory_comes_back_on_a_later_turn(pipeline, clock):

    llm = StubLLM()

    manager = manager_with(pipeline=pipeline, clock=clock, llm=llm)

    manager.chat("I'm learning Japanese because I want to read manga")
    manager.chat("what was I saying about Japanese")

    assert "Japanese" in llm.prompts[1]
    assert MEMORY in llm.prompts[1]


def test_recall_is_dated_relative_to_now(session):
    """
    A recalled line says when it happened, and "yesterday" is computed
    against the clock rather than stored in the row.
    """

    yesterday = TemporalClock(now=lambda: NOW - timedelta(days=1))

    pipeline = MemoryPipeline(session=session, clock=yesterday)

    manager_with(pipeline=pipeline, clock=yesterday).chat(
        "I'm learning Japanese for manga"
    )

    # Same store, read a day later.
    pipeline.clock = TemporalClock(now=lambda: NOW)
    pipeline.retriever.clock = pipeline.clock.now

    lines = pipeline.memory_lines("Japanese")

    assert any("yesterday" in line for line in lines)


def test_recall_is_bounded(pipeline, clock):
    """Never the whole database, whatever is in it."""

    manager = manager_with(pipeline=pipeline, clock=clock)

    for index in range(30):
        manager.chat(f"I'm learning Japanese lesson {index} about grammar")

    lines = pipeline.memory_lines("Japanese", max_episodic=3, max_user_model=0)

    assert len(lines) <= 3


def test_an_unrelated_query_does_not_drag_everything_in(pipeline, clock):

    manager = manager_with(pipeline=pipeline, clock=clock)

    manager.chat("I'm learning Japanese because I want to read manga")

    lines = pipeline.memory_lines(
        "what is the capital of France", max_user_model=0
    )

    assert not any("manga" in line for line in lines)


def test_the_knowledge_provider_and_the_pipeline_both_contribute(pipeline, clock):
    """
    Phase 8 added a source; it did not replace the existing one. Both
    reach the prompt, and the older provider keeps working untouched.
    """

    class StubKnowledge:
        def get_knowledge(self, query):
            return ["the user's name is Ember"]

    llm = StubLLM()

    manager = ConversationManager(
        memory=StubStore(),
        builder=PromptBuilder(),
        llm=llm,
        knowledge=StubKnowledge(),
        pipeline=pipeline,
        clock=clock,
    )

    manager.chat("I'm learning Japanese for manga")
    manager.chat("remind me what I'm learning")

    prompt = llm.prompts[1]

    assert "Ember" in prompt
    assert "Japanese" in prompt


# ======================================================================
# The composition root
# ======================================================================

@pytest.fixture
def services():
    """
    A services bundle built against a throwaway database.

    `build_services` with no memory opens the real `data/memory.db`; the
    tests below build the pipeline, which seeds the profile and creates
    the Phase 8 tables. Against the real database that is a write these
    tests have no business making, so an in-memory database is injected
    instead - which is also why this is a fixture rather than four
    identical calls.
    """

    from memory.manager import MemoryManager

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    made = sessionmaker(bind=engine)()

    yield build_services(
        {"llm": {"provider": ""}, "avatar": {"enabled": False}},
        memory=MemoryManager(session=made),
    )

    made.close()


def test_build_services_wires_the_clock_pipeline_and_proactive(services):

    assert services.clock is not None
    assert services.pipeline is not None
    assert services.proactive is not None

    # One clock, shared. Two would let the time in the prompt disagree
    # with the time on a stored memory.
    assert services.pipeline.clock is services.clock

    # Including the retriever, which captures `clock.now` as a bound
    # method. It held a clock the composition root had already thrown
    # away - the same wall time in production, and immune to an
    # injected clock, which is the part that made it worth fixing.
    assert services.pipeline.retriever.clock.__self__ is services.clock

    conversation = services.engine.conversation

    assert conversation.clock is services.clock
    assert conversation.pipeline is services.pipeline


def test_build_services_wires_one_cognitive_store(services):
    """
    One record of what the agent has already done.

    Two would be worse than none: the engine reading a store the device
    never wrote to is how an app gets opened twice after a verified
    launch, which is the failure section 10 names outright.
    """

    assert services.cognitive is not None
    assert services.engine.conversation.cognitive is services.cognitive

    # And it borrows the process clock rather than starting a seventh
    # source of "now" - so when an action was recorded and what time the
    # prompt says it is are the same reading.
    assert services.cognitive.for_session("s").now == services.clock.now()


def test_the_pipeline_can_be_switched_off(services):

    rebuilt = build_services(
        {
            "llm": {"provider": ""},
            "avatar": {"enabled": False},
            "memory": {"pipeline": False},
        },
        memory=services.memory,
    )

    assert rebuilt.pipeline is None
    assert rebuilt.engine.conversation.pipeline is None


def test_proactive_is_off_by_default(services):
    """The default deployment does not speak first."""

    assert services.proactive.policy.settings.enabled is False


def test_the_proactive_engine_reads_the_pipelines_own_store(services):
    """
    One store. A reminder about work recorded in a database nobody reads
    is the failure this prevents.
    """

    assert services.proactive.pending_tasks.store is services.pipeline.episodic


# ======================================================================
# The suite's own isolation
# ======================================================================

def test_the_shared_engine_is_not_the_users_database():
    """
    The guard in `tests/conftest.py`, asserted rather than trusted.

    A test that builds a whole runtime without injecting a session gets
    the module-level engine, and building the pipeline seeds the user
    model - so an unisolated suite writes profile rows into the real
    database and Aura reads them back as confirmed facts on the user's
    next conversation. That happened; this is what keeps it from
    happening again quietly.
    """

    from memory import sqlite

    assert "memory.db" not in str(sqlite.engine.url)

    # The stores captured `SessionLocal` at import time, so redirecting
    # the engine alone would not move them. This is the half that does.
    assert sqlite.SessionLocal.kw["bind"] is sqlite.engine
