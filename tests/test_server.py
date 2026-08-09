"""
API server tests.

Everything here runs against an isolated stack:

  * `llm.provider = "mock"`  - no network, no API key
  * an in-memory SQLite database - never touches data/memory.db
  * no vision, no TTS, no STT, no avatar

so a failure here is a failure in the server layer, not in the machine
the tests happen to run on.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from memory.manager import MemoryManager
from memory.models import Base
from server import config as server_config
from server.main import app
from server.runtime import init_runtime, shutdown_runtime
from server.session import session_manager


TEST_CONFIG = {
    "app": {"name": "Aura", "version": "0.2.0"},
    "llm": {"provider": "mock"},
    "memory": {
        "history_limit": 20,
        "profile": True,
        "recall": False,
        "companion": True,
    },
    "personality": {},
    "voice": {"tts": {"enabled": False}, "stt": {"enabled": False}},
    "vision": {"enabled": False},
    "avatar": {"enabled": False},
    "tools": {"enabled": False},
    "plugins": {"enabled": False},
}


@pytest.fixture(scope="module")
def isolated_memory():
    """
    A MemoryManager on an in-memory database.

    Both connect arguments are load-bearing, and neither is a way of
    making a threading problem quieter.

    TestClient runs the app on a worker thread, and streaming pushes the
    blocking generator onto a threadpool thread on top of that. An
    in-memory SQLite database belongs to its *connection*, and for
    `:memory:` SQLAlchemy picks SingletonThreadPool, which opens one
    connection per thread - so the worker thread would open a second,
    empty database and every query would fail with "no such table:
    messages". StaticPool keeps exactly one connection, so every thread
    sees the one database the tables were created in, which is what the
    single file database gives the real application for free.

    `check_same_thread=False` then permits that connection to be used
    from the thread the request landed on. It is safe here for the same
    reason it is safe in production: MemoryManager serialises access to
    its session, so only one thread is inside SQLite at a time.
    """

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    # Matches memory/sqlite.py, so these tests exercise the session
    # semantics the application actually runs with.
    session = sessionmaker(bind=engine, expire_on_commit=False)()

    yield MemoryManager(session=session)

    session.close()


@pytest.fixture(scope="module")
def client(isolated_memory):
    """Test client over a runtime wired to the mock provider."""

    # General route tests describe the unauthenticated development mode.
    # Keep that contract independent of a developer's real `.env`; the
    # `auth_enabled` fixture below covers the authenticated path explicitly.
    previous_token = server_config.settings.auth_token
    server_config.settings.auth_token = ""

    init_runtime(dict(TEST_CONFIG), memory=isolated_memory)

    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        shutdown_runtime()
        server_config.settings.auth_token = previous_token



@pytest.fixture
def auth_enabled():
    """Turn on bearer auth for one test, then put it back."""

    previous = server_config.settings.auth_token
    server_config.settings.auth_token = "test-token-not-a-real-secret"

    yield server_config.settings.auth_token

    server_config.settings.auth_token = previous


# ----------------------------------------------------------------------
# Deployment configuration
# ----------------------------------------------------------------------

def test_render_port_takes_precedence_over_the_namespaced_local_port(monkeypatch):
    """Render injects `PORT`; local Docker may still set the old name."""

    monkeypatch.setenv("PORT", "14321")
    monkeypatch.setenv("AURA_SERVER_PORT", "8000")

    settings = server_config.ServerSettings(_env_file=None)

    assert settings.port == 14321


# ----------------------------------------------------------------------
# Health
# ----------------------------------------------------------------------

def test_health_reports_a_running_runtime(client):

    response = client.get("/api/health")

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "healthy"
    assert data["version"] == "0.2.0"
    assert data["uptime_seconds"] >= 0
    assert data["runtime"]["llm_provider"] == "mock"
    assert data["runtime"]["memory"] == "connected"


def test_root_accepts_render_head_health_probes(client):
    assert client.head("/").status_code == 200


def test_health_does_not_start_a_second_runtime(client):
    """Two calls, one runtime: uptime moves forward, it does not reset."""

    first = client.get("/api/health").json()["uptime_seconds"]
    second = client.get("/api/health").json()["uptime_seconds"]

    assert second >= first


# ----------------------------------------------------------------------
# Chat
# ----------------------------------------------------------------------

def test_chat_returns_a_reply(client):

    response = client.post(
        "/api/chat",
        json={"session_id": "test-session-1", "message": "Hello, Aura!"},
    )

    assert response.status_code == 200

    data = response.json()

    assert data["session_id"] == "test-session-1"
    assert data["reply"]
    assert data["message_id"]
    assert data["metadata"]["provider"] == "mock"
    assert data["metadata"]["elapsed_seconds"] >= 0


def test_chat_keeps_the_same_session_across_turns(client):

    first = client.post(
        "/api/chat",
        json={"session_id": "test-session-2", "message": "First"},
    )
    second = client.post(
        "/api/chat",
        json={"session_id": "test-session-2", "message": "Second"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["session_id"] == "test-session-2"

    info = client.get("/api/sessions/test-session-2").json()

    assert info["message_count"] == 2


def test_chat_creates_a_session_when_none_is_given(client):

    response = client.post("/api/chat", json={"message": "Auto session"})

    assert response.status_code == 200

    session_id = response.json()["session_id"]

    assert session_id
    assert session_manager.get_session(session_id) is not None


def test_chat_message_ids_are_unique(client):

    ids = {
        client.post(
            "/api/chat",
            json={"session_id": "test-ids", "message": f"m{n}"},
        ).json()["message_id"]
        for n in range(3)
    }

    assert len(ids) == 3


def test_sessions_do_not_share_activity_counters(client):

    client.post("/api/chat", json={"session_id": "iso-a", "message": "a"})
    client.post("/api/chat", json={"session_id": "iso-b", "message": "b"})
    client.post("/api/chat", json={"session_id": "iso-b", "message": "b2"})

    assert client.get("/api/sessions/iso-a").json()["message_count"] == 1
    assert client.get("/api/sessions/iso-b").json()["message_count"] == 2


# ----------------------------------------------------------------------
# Input validation
# ----------------------------------------------------------------------

def test_chat_rejects_a_missing_message(client):

    response = client.post("/api/chat", json={"session_id": "bad"})

    assert response.status_code == 422


def test_chat_rejects_an_empty_message(client):

    response = client.post(
        "/api/chat", json={"session_id": "bad", "message": ""}
    )

    assert response.status_code == 422


def test_chat_rejects_an_oversized_message(client):

    oversized = "x" * (server_config.settings.max_message_length + 1)

    response = client.post(
        "/api/chat", json={"session_id": "bad", "message": oversized}
    )

    assert response.status_code == 422


def test_chat_rejects_a_non_string_message(client):

    response = client.post(
        "/api/chat", json={"session_id": "bad", "message": {"not": "a string"}}
    )

    assert response.status_code == 422


# ----------------------------------------------------------------------
# Sessions
# ----------------------------------------------------------------------

def test_session_lifecycle(client):

    client.post(
        "/api/chat", json={"session_id": "session-info-test", "message": "Test"}
    )

    info = client.get("/api/sessions/session-info-test")

    assert info.status_code == 200
    assert info.json()["session_id"] == "session-info-test"
    assert info.json()["message_count"] >= 1

    assert client.delete("/api/sessions/session-info-test").status_code == 200
    assert client.get("/api/sessions/session-info-test").status_code == 404


def test_deleting_an_unknown_session_is_404(client):

    assert client.delete("/api/sessions/never-existed").status_code == 404


# ----------------------------------------------------------------------
# Provider failure
# ----------------------------------------------------------------------

def test_chat_returns_500_without_leaking_the_provider_error(client, monkeypatch):
    """
    A provider blowing up must not put its exception text on the wire -
    it carries hosts, ports and filesystem paths.
    """

    from server.runtime import get_runtime

    conversation = get_runtime().engine.conversation

    def explode(*args, **kwargs):
        raise RuntimeError(
            "Ollama provider failed: <urlopen error [WinError 10061] "
            "C:\\Users\\secret\\path>"
        )

    monkeypatch.setattr(conversation, "chat", explode)

    response = client.post(
        "/api/chat", json={"session_id": "boom", "message": "hi"}
    )

    assert response.status_code == 500

    body = response.text

    assert "chat_failed" in body
    assert "WinError" not in body
    assert "C:\\\\Users" not in body and "C:\\Users" not in body


# ----------------------------------------------------------------------
# Authentication
# ----------------------------------------------------------------------

def test_health_requires_a_token_when_one_is_configured(client, auth_enabled):

    assert client.get("/api/health").status_code == 401


def test_chat_requires_a_token_when_one_is_configured(client, auth_enabled):

    response = client.post(
        "/api/chat", json={"session_id": "authz", "message": "hi"}
    )

    assert response.status_code == 401


def test_chat_accepts_the_correct_bearer_header(client, auth_enabled):

    response = client.post(
        "/api/chat",
        headers={"Authorization": f"Bearer {auth_enabled}"},
        json={"session_id": "swagger-auth", "message": "Hello"},
    )

    assert response.status_code == 200


def test_a_wrong_token_is_rejected(client, auth_enabled):

    response = client.get(
        "/api/health", headers={"Authorization": "Bearer wrong-token"}
    )

    assert response.status_code == 401


def test_a_non_bearer_scheme_is_rejected(client, auth_enabled):

    response = client.get(
        "/api/health", headers={"Authorization": f"Basic {auth_enabled}"}
    )

    assert response.status_code == 401


def test_a_malformed_header_is_rejected(client, auth_enabled):

    response = client.get(
        "/api/health", headers={"Authorization": auth_enabled}
    )

    assert response.status_code == 401


def test_the_correct_token_is_accepted(client, auth_enabled):

    response = client.get(
        "/api/health", headers={"Authorization": f"Bearer {auth_enabled}"}
    )

    assert response.status_code == 200


def test_a_rejection_never_echoes_the_expected_token(client, auth_enabled):

    response = client.get(
        "/api/health", headers={"Authorization": "Bearer wrong-token"}
    )

    assert auth_enabled not in response.text
    assert auth_enabled not in str(response.headers)


def test_openapi_declares_bearer_security_for_authenticated_chat(client):

    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/chat"]["post"]
    scheme = schema["components"]["securitySchemes"]["AuraBearer"]

    assert scheme["type"] == "http"
    assert scheme["scheme"] == "bearer"
    assert {"AuraBearer": []} in operation["security"]
    assert not any(
        parameter["name"] == "authorization"
        for parameter in operation.get("parameters", [])
    )


# ----------------------------------------------------------------------
# Secret leakage
# ----------------------------------------------------------------------

SENSITIVE_MARKERS = (
    "api_key",
    "apikey",
    "auth_token",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "secret",
    "password",
    "D:\\AURA",
    "D:/AURA",
    "/home/",
    "C:\\Users",
)


@pytest.mark.parametrize("path", ["/", "/api/health"])
def test_public_payloads_carry_no_secrets_or_paths(client, path):

    body = client.get(path).text

    for marker in SENSITIVE_MARKERS:
        assert marker.lower() not in body.lower(), f"{path} leaked {marker!r}"


def test_chat_payload_carries_no_secrets_or_paths(client):

    body = client.post(
        "/api/chat", json={"session_id": "leak", "message": "hi"}
    ).text

    for marker in SENSITIVE_MARKERS:
        assert marker.lower() not in body.lower(), f"chat leaked {marker!r}"


def test_health_exposes_only_the_documented_keys(client):

    data = client.get("/api/health").json()

    assert set(data) == {"status", "version", "uptime_seconds", "runtime"}

    # The exact contract documented in docs/API.md. `screen` and
    # `companion` are intentional: a client needs to know whether screen
    # observation will be accepted and whether unprompted messages can
    # arrive, before it offers either as a setting.
    assert set(data["runtime"]) == {
        "llm_provider",
        "memory",
        "vision",
        "voice_output",
        "voice_input",
        "screen",
        "companion",
    }


# ----------------------------------------------------------------------
# Streaming
# ----------------------------------------------------------------------

def test_stream_sends_started_chunks_and_complete(client):

    with client.websocket_connect(
        "/api/chat/stream?session_id=stream-1"
    ) as socket:

        socket.send_json({"message": "Hello streaming"})

        started = socket.receive_json()

        assert started["type"] == "started"
        assert started["session_id"] == "stream-1"

        chunks = []

        while True:
            frame = socket.receive_json()

            if frame["type"] == "chunk":
                chunks.append(frame)
                continue

            break

        assert frame["type"] == "complete", frame
        assert chunks, "no chunks were streamed"
        assert [c["index"] for c in chunks] == list(range(len(chunks)))
        assert frame["total_chunks"] == len(chunks)
        assert frame["elapsed_seconds"] >= 0
        assert frame["first_chunk_seconds"] is not None
        assert "".join(c["chunk"] for c in chunks)


def test_stream_rejects_an_empty_message(client):

    with client.websocket_connect(
        "/api/chat/stream?session_id=stream-empty"
    ) as socket:

        socket.send_json({"message": "   "})

        frame = socket.receive_json()

        assert frame["type"] == "error"
        assert frame["error"] == "empty_message"


def test_stream_rejects_invalid_json(client):

    with client.websocket_connect(
        "/api/chat/stream?session_id=stream-bad"
    ) as socket:

        socket.send_text("not json at all")

        frame = socket.receive_json()

        assert frame["type"] == "error"
        assert frame["error"] == "invalid_json"


def test_stream_rejects_an_oversized_message(client):

    with client.websocket_connect(
        "/api/chat/stream?session_id=stream-big"
    ) as socket:

        socket.send_json(
            {"message": "x" * (server_config.settings.max_message_length + 1)}
        )

        frame = socket.receive_json()

        assert frame["type"] == "error"
        assert frame["error"] == "message_too_long"


def test_stream_without_a_token_is_refused(client, auth_enabled):

    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/chat/stream") as socket:
            socket.receive_json()


def test_stream_with_a_wrong_token_is_refused(client, auth_enabled):

    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/api/chat/stream?token=wrong-token"
        ) as socket:
            socket.receive_json()


def test_stream_with_the_correct_token_connects(client, auth_enabled):

    with client.websocket_connect(
        f"/api/chat/stream?token={auth_enabled}&session_id=stream-auth"
    ) as socket:

        socket.send_json({"message": "hi"})

        assert socket.receive_json()["type"] == "started"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
