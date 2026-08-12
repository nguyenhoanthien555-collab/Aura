"""
Focused tests for Phase 2: Session Scoping & Database Isolation.
"""

import uuid
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from memory.models import Base, Message
from memory.manager import MemoryManager
from memory.sqlite import init_database
from brain.message import Message as PipelineMessage
from brain.prompt_builder import PromptBuilder
from brain.conversation import ConversationManager
from server.runtime import ServerRuntime


class DummyLLM:
    def __init__(self, reply: str = "Test reply"):
        self.reply = reply
        self.last_prompt = None

    def generate(self, prompt: str) -> str:
        self.last_prompt = prompt
        return self.reply


def test_session_isolation_in_memory_manager():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    mem = MemoryManager(session=session)

    session_a = str(uuid.uuid4())
    session_b = str(uuid.uuid4())

    mem.save("user", "Hello from A", session_id=session_a)
    mem.save("assistant", "Response A", session_id=session_a)

    mem.save("user", "Hello from B", session_id=session_b)
    mem.save("assistant", "Response B", session_id=session_b)

    history_a = mem.get_recent(limit=10, session_id=session_a)
    history_b = mem.get_recent(limit=10, session_id=session_b)

    # 1. Session A cannot see Session B history & Session B cannot see Session A history
    assert len(history_a) == 2
    assert [m.content for m in history_a] == ["Response A", "Hello from A"]

    assert len(history_b) == 2
    assert [m.content for m in history_b] == ["Response B", "Hello from B"]


def test_new_uuid_session_starts_with_empty_history():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    mem = MemoryManager(session=session)
    session_a = str(uuid.uuid4())
    mem.save("user", "Existing message", session_id=session_a)

    new_session = str(uuid.uuid4())

    # 3. New UUID session has empty history
    history_new = mem.get_recent(limit=10, session_id=new_session)
    assert len(history_new) == 0


def test_database_migration_preserves_legacy_rows():
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE messages (id INTEGER PRIMARY KEY, role VARCHAR(20), content TEXT, timestamp VARCHAR(30))"))
        conn.execute(text("INSERT INTO messages (role, content, timestamp) VALUES ('user', 'Legacy msg', '2026-01-01T00:00:00')"))
        conn.commit()

    # Run migration logic
    with engine.connect() as conn:
        columns = [row[1] for row in conn.execute(text("PRAGMA table_info(messages)"))]
        if "session_id" not in columns:
            conn.execute(text("ALTER TABLE messages ADD COLUMN session_id VARCHAR(128) DEFAULT 'default'"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_messages_session_id ON messages (session_id)"))
            conn.commit()

    Session = sessionmaker(bind=engine)
    session = Session()
    mem = MemoryManager(session=session)

    # 5. Legacy rows survive as 'default'
    legacy_history = mem.get_recent(limit=10, session_id="default")
    assert len(legacy_history) == 1
    assert legacy_history[0].content == "Legacy msg"

    # Legacy rows do NOT appear in a new session
    new_session = str(uuid.uuid4())
    assert len(mem.get_recent(limit=10, session_id=new_session)) == 0


def test_clearing_session_a_does_not_delete_session_b():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    mem = MemoryManager(session=session)

    session_a = str(uuid.uuid4())
    session_b = str(uuid.uuid4())

    mem.save("user", "Msg A", session_id=session_a)
    mem.save("user", "Msg B", session_id=session_b)

    mem.clear(session_id=session_a)

    # 7. Clearing session A does not delete session B
    assert len(mem.get_recent(limit=10, session_id=session_a)) == 0
    assert len(mem.get_recent(limit=10, session_id=session_b)) == 1


def test_integration_conversation_manager_session_isolation():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    mem = MemoryManager(session=session)
    llm = DummyLLM("Replied by assistant")
    builder = PromptBuilder()

    conv = ConversationManager(memory=mem, builder=builder, llm=llm)

    session_1 = str(uuid.uuid4())
    session_2 = str(uuid.uuid4())

    # Request under session 1
    conv.chat("Message for 1", session_id=session_1)

    # Request under session 2
    conv.chat("Message for 2", session_id=session_2)

    hist_1 = conv.history(session_id=session_1)
    hist_2 = conv.history(session_id=session_2)

    # 8 & 10. Integration test proving session 1 cannot see session 2 history and vice versa
    assert len(hist_1) == 2
    assert [m.content for m in hist_1] == ["Message for 1", "Replied by assistant"]

    assert len(hist_2) == 2
    assert [m.content for m in hist_2] == ["Message for 2", "Replied by assistant"]


def test_server_runtime_forwards_session_id():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    mem = MemoryManager(session=session)
    llm = DummyLLM("Runtime reply")
    builder = PromptBuilder()

    conv = ConversationManager(memory=mem, builder=builder, llm=llm)

    class MockEngine:
        def __init__(self, conversation):
            self.conversation = conversation
            self.llm = llm
        def chat(self, message, contexts=None, source="text", context=None, session_id="default"):
            return self.conversation.chat(message, contexts, source=source, context=context, session_id=session_id)

    class MockServices:
        def __init__(self, conversation):
            self.engine = MockEngine(conversation)
            self.proactive = None
            self.bus = None

    runtime = ServerRuntime.__new__(ServerRuntime)
    runtime.companion_engine = None
    runtime.services = MockServices(conv)

    sess_id = str(uuid.uuid4())
    resp = runtime.chat("Testing runtime forwarding", session_id=sess_id)

    assert resp.text == "Runtime reply"

    # 9. ServerRuntime actually forwards session_id and persists under that session_id
    recent = mem.get_recent(limit=10, session_id=sess_id)
    assert len(recent) == 2
    assert recent[1].content == "Testing runtime forwarding"
