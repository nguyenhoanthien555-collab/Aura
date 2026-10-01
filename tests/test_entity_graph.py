import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from memory.graph import EntityGraphStore, normalize_entity_name, normalize_relation_type
from memory.models import Base
from memory.sqlite import init_graph_tables


@pytest.fixture
def graph_store():
    test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(test_engine)
    TestingSessionLocal = sessionmaker(bind=test_engine)
    session = TestingSessionLocal()
    store = EntityGraphStore(session=session)
    yield store
    session.close()


def test_add_and_get_entity(graph_store):
    node = graph_store.add_entity("Anh Thien", entity_type="PERSON", description="Owner of Aura")
    assert node is not None
    assert node.name == "Anh Thien"
    assert node.entity_type == "PERSON"
    assert node.description == "Owner of Aura"

    fetched = graph_store.get_entity("anh thien")
    assert fetched is not None
    assert fetched.id == node.id

    # Update entity
    updated = graph_store.add_entity("Anh Thien", description="Creator and Lead Engineer")
    assert updated.description == "Creator and Lead Engineer"


def test_add_relation_and_subgraph(graph_store):
    rel = graph_store.add_relation("Anh Thien", "CREATED", "AURA", confidence=1.0)
    assert rel is not None
    assert rel.relation == "CREATED"

    rel2 = graph_store.add_relation("Anh Thien", "LIKES", "Bac Xiu Coffee", confidence=0.95)
    assert rel2 is not None

    rel3 = graph_store.add_relation("AURA", "WRITTEN_IN", "Python", confidence=1.0)
    assert rel3 is not None

    # Query 1-hop subgraph for Anh Thien
    subgraph = graph_store.query_subgraph(["Anh Thien"])
    assert len(subgraph) == 2
    relations = {r["relation"] for r in subgraph}
    assert "CREATED" in relations
    assert "LIKES" in relations

    # Query 1-hop for AURA
    aura_subgraph = graph_store.query_subgraph(["AURA"])
    assert len(aura_subgraph) == 2  # (Anh Thien -> CREATED -> AURA) and (AURA -> WRITTEN_IN -> Python)


def test_delete_entity_cascades(graph_store):
    graph_store.add_relation("Thien", "WORKS_ON", "Project Alpha")
    assert len(graph_store.list_relations()) == 1

    deleted = graph_store.delete_entity("Project Alpha")
    assert deleted is True
    assert graph_store.get_entity("Project Alpha") is None
    # Cascading relation deletion
    assert len(graph_store.list_relations()) == 0


def test_stats_and_purge(graph_store):
    graph_store.add_relation("A", "REL", "B")
    graph_store.add_relation("B", "REL", "C")
    stats = graph_store.stats()
    assert stats["total_entities"] == 3
    assert stats["total_relations"] == 2

    purged = graph_store.purge()
    assert purged == 3
    assert graph_store.stats()["total_entities"] == 0
    assert graph_store.stats()["total_relations"] == 0
