import pytest
from fastapi.testclient import TestClient

from server.config import settings
from server.main import app


@pytest.fixture
def auth_client():
    settings.auth_token = "test-memory-token"
    client = TestClient(app)
    yield client, {"Authorization": "Bearer test-memory-token"}


def test_memory_routes_require_auth(auth_client):
    client, headers = auth_client

    # Unauthenticated requests must be rejected
    res = client.get("/api/memory/overview")
    assert res.status_code in (401, 403)

    res = client.get("/api/memory/facts")
    assert res.status_code in (401, 403)

    res = client.post("/api/memory/facts", json={"key": "test", "value": "val"})
    assert res.status_code in (401, 403)


def test_memory_facts_crud(auth_client):
    client, headers = auth_client

    # 1. Create a fact
    res = client.post(
        "/api/memory/facts",
        json={"key": "favorite_food", "value": "Bún Bò Huế", "category": "preference"},
        headers=headers,
    )
    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert res.json()["fact"]["key"] == "favorite_food"

    # 2. Get list of facts
    res_list = client.get("/api/memory/facts", headers=headers)
    assert res_list.status_code == 200
    facts = res_list.json()["facts"]
    assert any(f["key"] == "favorite_food" and f["value"] == "Bún Bò Huế" for f in facts)

    # 3. Search facts
    res_search = client.get("/api/memory/facts?q=Bún", headers=headers)
    assert res_search.status_code == 200
    assert len(res_search.json()["facts"]) >= 1

    # 4. Sensitive rejection
    res_sensitive = client.post(
        "/api/memory/facts",
        json={"key": "api_key", "value": "AIzaSyD-7389274928374923749238472938472"},
        headers=headers,
    )
    assert res_sensitive.status_code == 422
    assert "Refused" in res_sensitive.json()["detail"]

    # 5. Delete fact
    res_del = client.delete("/api/memory/facts/favorite_food", headers=headers)
    assert res_del.status_code == 200
    assert res_del.json()["ok"] is True


def test_memory_graph_and_overview(auth_client):
    client, headers = auth_client

    # 1. Create entities
    res_e1 = client.post(
        "/api/memory/entities",
        json={"name": "Aura", "entity_type": "PROJECT", "description": "AI Companion System"},
        headers=headers,
    )
    assert res_e1.status_code == 200

    res_e2 = client.post(
        "/api/memory/entities",
        json={"name": "Thien", "entity_type": "PERSON", "description": "Lead Engineer"},
        headers=headers,
    )
    assert res_e2.status_code == 200

    # 2. Create relation
    res_rel = client.post(
        "/api/memory/relations",
        json={"source": "Thien", "relation": "MAINTAINS", "target": "Aura", "confidence": 1.0},
        headers=headers,
    )
    assert res_rel.status_code == 200

    # 3. Overview stats
    res_overview = client.get("/api/memory/overview", headers=headers)
    assert res_overview.status_code == 200
    data = res_overview.json()
    assert data["total_entities"] >= 2
    assert data["total_relations"] >= 1

    # 4. Fetch graph
    res_graph = client.get("/api/memory/graph", headers=headers)
    assert res_graph.status_code == 200
    graph_data = res_graph.json()
    assert len(graph_data["entities"]) >= 2
    assert any(r["relation"] == "MAINTAINS" for r in graph_data["relations"])

    # 5. Purge graph
    res_purge = client.post("/api/memory/purge", json={"target": "graph"}, headers=headers)
    assert res_purge.status_code == 200
    assert res_purge.json()["purged"] == "graph"


def test_memory_export_endpoint(auth_client):
    client, headers = auth_client

    # 1. Seed a fact
    client.post(
        "/api/memory/facts",
        json={"key": "export_test_key", "value": "export_value", "category": "profile"},
        headers=headers,
    )

    # 2. Call export
    res = client.get("/api/memory/export?include_messages=true", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["version"] == "1.0"
    assert "facts" in data
    assert any(f["key"] == "export_test_key" for f in data["facts"])
    assert "graph" in data
    assert "episodes" in data
    assert "companion" in data
    assert "counts" in data
    assert data["counts"]["facts"] >= 1


def test_chat_history_endpoint(auth_client):
    client, headers = auth_client
    from memory.models import Message
    from memory.sqlite import SessionLocal, db_lock

    # Insert a test message
    with db_lock:
        session = SessionLocal()
        try:
            session.add(
                Message(
                    role="user",
                    content="Hello from test_chat_history",
                    session_id="test_hist_session",
                )
            )
            session.commit()
        finally:
            session.close()

    res = client.get("/api/chat/history?session_id=test_hist_session", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["total"] >= 1
    assert any(m["content"] == "Hello from test_chat_history" for m in data["messages"])

