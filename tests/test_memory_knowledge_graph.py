import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from memory.graph import EntityGraphStore
from memory.knowledge import MemoryKnowledgeProvider
from memory.models import Base
from memory.profile import ProfileStore
from memory.retrieval import NullRetriever


@pytest.fixture
def memory_setup():
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(test_engine)
    TestingSessionLocal = sessionmaker(bind=test_engine)
    session = TestingSessionLocal()
    profile = ProfileStore(session=session)
    graph = EntityGraphStore(session=session)
    yield profile, graph
    session.close()


def test_knowledge_provider_with_graph(memory_setup):
    profile, graph = memory_setup

    profile.remember("user_name", "Anh Thien")
    graph.add_relation("Thien", "LIKES", "Bac Xiu Coffee")
    graph.add_relation("Thien", "WORKS_ON", "Aura System")

    provider = MemoryKnowledgeProvider(
        profile=profile,
        retriever=NullRetriever(),
        graph_store=graph,
    )

    # Query without matching entity
    knowledge_unrelated = provider.get_knowledge("What is the weather today?")
    assert any("user name: Anh Thien" in line for line in knowledge_unrelated)
    assert not any("knowledge - " in line for line in knowledge_unrelated)

    # Query mentioning Thien
    knowledge_related = provider.get_knowledge("Anh Thien có thích uống gì không?")
    assert any("user name: Anh Thien" in line for line in knowledge_related)
    assert any("knowledge - Thien likes Bac Xiu Coffee" in line for line in knowledge_related)


def test_reflection_worker(memory_setup):
    from memory.reflection import EpisodicReflectionWorker

    _, graph = memory_setup
    worker = EpisodicReflectionWorker(graph_store=graph)

    # Reflect on Vietnamese preference
    triples = worker.reflect_turn(
        user_message="Anh thích ăn Phở Bò Tái Nạm vào buổi sáng.",
        assistant_reply="Dạ em nhớ rồi ạ!",
        user_name="Thien",
    )
    assert len(triples) >= 1
    assert any(t["relation"] == "LIKES" for t in triples)

    # Verify persisted in graph
    subgraph = graph.query_subgraph(["Thien"])
    assert any(r["relation"] == "LIKES" and "Phở Bò" in r["target"] for r in subgraph)

    # Sensitive turn is ignored
    sensitive_triples = worker.reflect_turn(
        user_message="mật khẩu là: 123456 và tôi thích hack",
        assistant_reply="Dạ vâng.",
        user_name="Thien",
    )
    assert len(sensitive_triples) == 0

