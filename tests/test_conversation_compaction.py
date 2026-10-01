"""
Unit tests for conversational context compaction and host environment prompt injection.
"""

from unittest.mock import MagicMock
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from brain.compaction import ConversationCompactor
from brain.conversation import ConversationManager
from brain.message import Message
from brain.prompt_builder import PromptBuilder
from brain.prompt_sections import HOST_ENVIRONMENT
from memory.manager import MemoryManager
from memory.models import Base
from memory.profile import ProfileStore


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sess = sessionmaker(bind=engine)()
    yield sess
    sess.close()


@pytest.fixture
def profile_store(session):
    return ProfileStore(session=session)


@pytest.fixture
def memory_manager(session):
    return MemoryManager(session=session)


def test_compactor_should_compact():
    compactor = ConversationCompactor(threshold=10, keep_recent=4)
    short_history = [Message(role="user", content=f"msg {i}") for i in range(10)]
    assert not compactor.should_compact(short_history)

    long_history = [Message(role="user", content=f"msg {i}") for i in range(11)]
    assert compactor.should_compact(long_history)


def test_compactor_compact_noop_when_not_needed():
    compactor = ConversationCompactor(threshold=10, keep_recent=4)
    history = [Message(role="user", content="hello"), Message(role="assistant", content="hi")]
    compacted, did_compact = compactor.compact(history, force=False)
    assert not did_compact
    assert len(compacted) == 2
    assert compacted[0].content == "hello"


def test_compactor_compact_keeps_recent_verbatim():
    compactor = ConversationCompactor(threshold=6, keep_recent=3)
    history = [
        Message(role="user", content="Turn 1: How's the weather?"),
        Message(role="assistant", content="Turn 1: Sunny!"),
        Message(role="user", content="Turn 2: Set a timer for 10m"),
        Message(role="assistant", content="Turn 2: Timer set."),
        Message(role="user", content="Turn 3: What's on my calendar?"),
        Message(role="assistant", content="Turn 3: Meeting at 3pm."),
        Message(role="user", content="Turn 4: Thanks!"),
    ]
    assert compactor.should_compact(history)

    compacted, did_compact = compactor.compact(history)
    assert did_compact
    # Expected: 1 synopsis system message + 3 recent verbatim turns
    assert len(compacted) == 4
    assert compacted[0].role == "system"
    assert "[COMPACTED CONTEXT]" in compacted[0].content
    assert "Turn 1" in compacted[0].content or "Turn 2" in compacted[0].content

    # The 3 recent messages must be Turn 3 user, Turn 3 assistant, Turn 4 user
    assert compacted[-3].content == "Turn 3: What's on my calendar?"
    assert compacted[-2].content == "Turn 3: Meeting at 3pm."
    assert compacted[-1].content == "Turn 4: Thanks!"


def test_compactor_with_llm_synthesis():
    mock_llm = MagicMock()
    mock_llm.generate.return_value = "- User asked for weather and timer.\n- Assistant set both."

    compactor = ConversationCompactor(threshold=4, keep_recent=2, llm=mock_llm)
    history = [
        Message(role="user", content="hello"),
        Message(role="assistant", content="hi"),
        Message(role="user", content="help me"),
        Message(role="assistant", content="sure"),
        Message(role="user", content="final question"),
    ]
    compacted, did_compact = compactor.compact(history)
    assert did_compact
    assert mock_llm.generate.called
    assert "User asked for weather" in compacted[0].content


def test_conversation_manager_compact_command(memory_manager):
    mock_llm = MagicMock()
    conv = ConversationManager(
        memory=memory_manager,
        builder=PromptBuilder(),
        llm=mock_llm,
    )
    # Populate memory with some turns
    for i in range(10):
        memory_manager.save("user", f"query {i}")
        memory_manager.save("assistant", f"reply {i}")

    reply = conv.chat("/compact")
    assert "nén ngữ cảnh" in reply.text.lower()
    # LLM was invoked once specifically for the compaction summary
    assert mock_llm.generate.call_count == 1
    prompt_arg = mock_llm.generate.call_args[0][0]
    assert "Summarize the following prior conversation" in prompt_arg


def test_prompt_builder_includes_host_environment():
    builder = PromptBuilder()
    host_env_lines = [
        "Machine: DevRig (ASUS ProArt)",
        "OS: Windows 11 Pro",
        "CPU: AMD Ryzen 9 7950X (32 vCPUs)",
        "RAM: 64.0 GB",
        "GPU: NVIDIA GeForce RTX 4090",
    ]
    prompt = builder.build(
        history=[],
        user_message=Message(role="user", content="Hello Aura"),
        host_environment=host_env_lines,
    )
    assert HOST_ENVIRONMENT in prompt
    assert "Machine: DevRig" in prompt
    assert "RTX 4090" in prompt
    assert "AMD Ryzen 9" in prompt
