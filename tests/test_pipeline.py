"""
Sprint 4 foundation tests.

Covers the required pipeline:

    "Hello Aura"
        -> brain.message.Message
        -> PromptBuilder
        -> BrainRouter
        -> Provider
        -> brain.response.Response
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from brain.adapters import record_to_message, records_to_messages
from brain.chat_engine import ChatEngine
from brain.conversation import ConversationManager
from brain.message import Message
from brain.prompt_builder import PromptBuilder
from brain.prompt_sections import HISTORY, USER
from brain.response import Response
from brain.router import BrainRouter
from brain.providers.mock import MockProvider

from memory.manager import MemoryManager
from memory.models import Base
# Imported under an alias purely to assert the two Message types stay distinct.
from memory.models import Message as DBMessage


# ----------------------------------------------------------------------
# Test doubles
# ----------------------------------------------------------------------

class FakeRecord:
    """
    Minimal stored-record stand-in.

    A plain object, not an ORM row: it proves the brain only ever needs
    .role / .content and never depends on SQLAlchemy.
    """

    def __init__(self, role, content):
        self.role = role
        self.content = content


class FakeStore:
    """In-memory ConversationStore. Returns newest-first, like the real one."""

    def __init__(self, records=None):
        self.records = list(records or [])
        self.saved = []

    def save(self, role, content):
        self.saved.append((role, content))
        self.records.append(FakeRecord(role, content))

    def get_recent(self, limit=10):
        return list(reversed(self.records))[:limit]


class RecordingLLM:
    """Captures the prompt it was handed and returns a fixed reply."""

    def __init__(self, reply="Hey bro, I'm here."):
        self.reply = reply
        self.prompt = None
        self.calls = 0

    def generate(self, prompt: str) -> str:
        self.prompt = prompt
        self.calls += 1
        return self.reply


@pytest.fixture
def llm():
    return RecordingLLM()


@pytest.fixture
def store():
    return FakeStore()


@pytest.fixture
def manager(store, llm):
    return ConversationManager(
        memory=store,
        builder=PromptBuilder(),
        llm=llm,
    )


@pytest.fixture
def db_session():
    """Isolated in-memory database - never touches data/memory.db."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


# ----------------------------------------------------------------------
# Required Sprint 4 pipeline test
# ----------------------------------------------------------------------

def test_hello_aura_returns_response(manager, llm):
    """User text -> Message -> PromptBuilder -> Router -> Provider -> Response."""

    result = manager.chat("Hello Aura")

    assert isinstance(result, Response)
    assert result.text == llm.reply
    assert llm.calls == 1


def test_prompt_reaches_provider_with_user_message(manager, llm):
    manager.chat("Hello Aura")

    assert USER in llm.prompt
    assert "Hello Aura" in llm.prompt


def test_prompt_contains_required_sections(manager, llm):
    manager.chat("Hello Aura")

    assert HISTORY in llm.prompt
    assert "(No previous conversation)" in llm.prompt


def test_full_pipeline_through_router_and_provider(store):
    """End to end with the real BrainRouter and MockProvider."""

    router = BrainRouter(provider=MockProvider())

    manager = ConversationManager(
        memory=store,
        builder=PromptBuilder(),
        llm=router,
    )

    result = manager.chat("Hello Aura")

    assert isinstance(result, Response)
    assert result.text == "Mock response generated."


# ----------------------------------------------------------------------
# Memory boundary
# ----------------------------------------------------------------------

def test_history_returns_brain_messages_not_orm_rows(store, manager):
    store.save("user", "earlier question")
    store.save("assistant", "earlier answer")

    history = manager.history()

    assert history, "history should not be empty"
    for msg in history:
        assert isinstance(msg, Message)
        assert not isinstance(msg, DBMessage)


def test_history_is_oldest_first(store, manager):
    store.save("user", "first")
    store.save("assistant", "second")
    store.save("user", "third")

    history = manager.history()

    assert [m.content for m in history] == ["first", "second", "third"]


def test_history_respects_limit(store, llm):
    for i in range(10):
        store.save("user", f"msg-{i}")

    manager = ConversationManager(
        memory=store,
        builder=PromptBuilder(),
        llm=llm,
        history_limit=3,
    )

    history = manager.history()

    assert len(history) == 3
    assert [m.content for m in history] == ["msg-7", "msg-8", "msg-9"]


def test_brain_message_and_db_message_are_distinct_types():
    assert Message is not DBMessage


def test_record_to_message_conversion():
    record = DBMessage(role="user", content="hi")

    msg = record_to_message(record)

    assert isinstance(msg, Message)
    assert (msg.role, msg.content) == ("user", "hi")


def test_records_to_messages_preserves_order():
    records = [
        DBMessage(role="user", content="a"),
        DBMessage(role="assistant", content="b"),
    ]

    messages = records_to_messages(records)

    assert [m.content for m in messages] == ["a", "b"]
    assert all(isinstance(m, Message) for m in messages)


def test_memory_manager_round_trip(db_session):
    """Real MemoryManager against an isolated database."""

    memory = MemoryManager(session=db_session)

    memory.save("user", "hello")
    memory.save("assistant", "hi bro")

    recent = memory.get_recent(10)

    # Storage contract: newest first.
    assert [r.content for r in recent] == ["hi bro", "hello"]

    messages = records_to_messages(recent)
    assert all(isinstance(m, Message) for m in messages)


def test_conversation_persists_both_turns(store, manager, llm):
    manager.chat("Hello Aura")

    assert store.saved == [
        ("user", "Hello Aura"),
        ("assistant", llm.reply),
    ]


def test_history_feeds_next_prompt(manager, llm):
    manager.chat("first question")
    manager.chat("second question")

    assert "first question" in llm.prompt
    assert llm.reply in llm.prompt
    assert "second question" in llm.prompt


# ----------------------------------------------------------------------
# PromptBuilder
# ----------------------------------------------------------------------

def test_prompt_builder_accepts_brain_messages():
    builder = PromptBuilder()

    # Distinctive tokens: plain words like "now" appear inside prompt
    # files (e.g. the substring in "know"), which would break index().
    prompt = builder.build(
        history=[
            Message(role="user", content="zzz-older"),
            Message(role="assistant", content="zzz-newer"),
        ],
        user_message=Message(role="user", content="zzz-current"),
        contexts=[],
    )

    assert (
        prompt.index("zzz-older")
        < prompt.index("zzz-newer")
        < prompt.index("zzz-current")
    )


def test_prompt_builder_does_not_reorder_history():
    builder = PromptBuilder()

    prompt = builder.build(
        history=[
            Message(role="user", content=f"zzz-m{i}")
            for i in range(3)
        ],
        user_message=Message(role="user", content="zzz-current"),
    )

    assert (
        prompt.index("zzz-m0")
        < prompt.index("zzz-m1")
        < prompt.index("zzz-m2")
    )


def test_prompt_builder_contexts_default_to_empty():
    builder = PromptBuilder()

    prompt = builder.build(
        history=[],
        user_message=Message(role="user", content="hi"),
    )

    assert USER in prompt


# ----------------------------------------------------------------------
# Composition root
# ----------------------------------------------------------------------

def test_chat_engine_injects_dependencies(store, llm):
    engine = ChatEngine(
        memory=store,
        builder=PromptBuilder(),
        llm=llm,
    )

    result = engine.chat("Hello Aura")

    assert isinstance(result, Response)
    assert result.text == llm.reply
    assert engine.conversation.memory is store
    assert engine.conversation.llm is llm


def test_chat_engine_returns_response_not_string(store, llm):
    engine = ChatEngine(memory=store, builder=PromptBuilder(), llm=llm)

    assert not isinstance(engine.chat("hi"), str)


# ----------------------------------------------------------------------
# Router
# ----------------------------------------------------------------------

def test_router_uses_injected_provider():
    router = BrainRouter(provider=MockProvider())

    assert router.generate("prompt") == "Mock response generated."


def test_router_provider_is_lazy():
    """Constructing the router must not build a provider (no API key needed)."""

    router = BrainRouter(provider_name="mock")

    assert router._provider is None
    assert isinstance(router.provider, MockProvider)


def test_router_rejects_unknown_provider():
    router = BrainRouter(provider_name="does-not-exist")

    with pytest.raises(ValueError):
        router.generate("prompt")


# ----------------------------------------------------------------------
# Data objects
# ----------------------------------------------------------------------

def test_message_shape():
    msg = Message(role="user", content="hi")

    assert (msg.role, msg.content) == ("user", "hi")


def test_response_shape():
    assert Response(text="hi").text == "hi"
