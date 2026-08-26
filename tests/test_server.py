"""
API server tests.

Everything here runs against an isolated stack:

  * `llm.provider = "mock"`  - no network, no API key
  * an in-memory SQLite database - never touches data/memory.db
  * no vision, no TTS, no STT, no avatar

so a failure here is a failure in the server layer, not in the machine
the tests happen to run on.
"""

import asyncio
import os
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from memory.manager import MemoryManager
from memory.models import Base
from server import config as server_config
from server.main import app
from server.runtime import init_runtime, shutdown_runtime, get_runtime
from server.session import session_manager, SessionManager


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

    # Since AURA-P1-008, an unauthenticated server refuses to start unless
    # that was asked for. These tests are asking for it: the opt-in is the
    # supported way to run without a token, so stating it here is what the
    # development mode being tested actually looks like.
    previous_insecure = os.environ.get(server_config.INSECURE_ENV_VAR)
    os.environ[server_config.INSECURE_ENV_VAR] = "1"

    init_runtime(dict(TEST_CONFIG), memory=isolated_memory)

    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        shutdown_runtime()
        server_config.settings.auth_token = previous_token
        if previous_insecure is None:
            os.environ.pop(server_config.INSECURE_ENV_VAR, None)
        else:
            os.environ[server_config.INSECURE_ENV_VAR] = previous_insecure



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

def test_health_reports_tools_and_plugins_off(client):
    """
    The stock shape: tools off, plugins unconfigured.

    Both keys exist even when their subsystem does not - a client renders
    the runtime map without special-casing, so absence would read as a
    broken document rather than a switched-off subsystem.
    """

    data = client.get("/api/health").json()

    assert data["runtime"]["tools"] == "disabled"
    assert data["runtime"]["plugins"] == "off"


def test_health_counts_only_tools_the_owner_allowed(isolated_memory):
    """
    Registered is not offered.

    The factory registers every builtin whose dependencies exist, but the
    catalogue filters through `tools.allowed`. The health label reports the
    filtered count - the number an operator can act on - not the size of
    the registry.
    """

    from server.runtime import ServerRuntime

    config = dict(TEST_CONFIG)

    config["tools"] = {"enabled": True, "allowed": ["current_time"]}

    runtime = ServerRuntime(config, memory=isolated_memory)

    try:
        assert runtime.health_status()["runtime"]["tools"] == "1 available"
    finally:
        runtime.stop()


def test_health_names_a_plugin_that_failed_to_initialize(tmp_path, isolated_memory):
    """
    A plugin enabled in config whose initialize raised must be visible by
    name, not folded into a count that merely got smaller.

    This is also the test that pins `PluginManager.status` to a production
    caller: before the health label existed, nothing outside the suite ever
    asked which plugins were broken.
    """

    from server.runtime import ServerRuntime

    (tmp_path / "echo_plug.py").write_text(
        "from tools.base import ToolRisk\n"
        "\n"
        "\n"
        "class EchoTool:\n"
        "    name = 'echo_tool'\n"
        "    description = 'echo'\n"
        "    risk = ToolRisk.SAFE\n"
        "\n"
        "    def execute(self, **arguments):\n"
        "        return ''\n"
        "\n"
        "\n"
        "class EchoPlugin:\n"
        "    name = 'echo'\n"
        "    version = '1.0.0'\n"
        "\n"
        "    def initialize(self, context):\n"
        "        if context.tools is not None:\n"
        "            context.tools.register(EchoTool())\n"
        "\n"
        "    def shutdown(self):\n"
        "        pass\n"
        "\n"
        "\n"
        "def plugin():\n"
        "    return EchoPlugin()\n",
        encoding="utf-8",
    )

    (tmp_path / "bad_plug.py").write_text(
        "from plugins.base import Plugin\n"
        "\n"
        "\n"
        "class BadPlugin(Plugin):\n"
        "    name = 'bad_plug'\n"
        "    version = '1.0.0'\n"
        "\n"
        "    def initialize(self, context):\n"
        "        raise RuntimeError('initialize failed on purpose')\n",
        encoding="utf-8",
    )

    config = dict(TEST_CONFIG)

    config["tools"] = {"enabled": True, "allowed": []}
    config["plugins"] = {
        "enabled": ["echo", "bad_plug"],
        "directory": str(tmp_path),
    }

    runtime = ServerRuntime(config, memory=isolated_memory)

    try:
        label = runtime.health_status()["runtime"]["plugins"]

        assert label == "1/3 plugins active (bad_plug failed to initialize)"
    finally:
        runtime.stop()




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


def test_chat_does_not_run_on_the_event_loop(client, monkeypatch):
    """
    A turn must not block the one ASGI loop while it waits for a model.

    Every step of `runtime.chat` is synchronous and one of them is a
    network call bounded only by `llm.timeout`. Awaited inline in an
    `async def` route it holds the loop for that whole time, so nothing
    else is served meanwhile - not `/api/health`, not
    `/api/notifications`, and not the phone's next agent tick.

    Asserted from inside the call rather than by timing two requests:
    `get_running_loop` succeeds only on a thread that is running the
    loop, so this fails precisely when the route stops offloading and
    for no other reason.
    """

    runtime = get_runtime()
    original = runtime.chat
    landed_on_the_loop: list[bool] = []

    def recording_chat(*args, **kwargs):
        try:
            asyncio.get_running_loop()
            landed_on_the_loop.append(True)
        except RuntimeError:
            landed_on_the_loop.append(False)
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime, "chat", recording_chat)

    response = client.post(
        "/api/chat",
        json={"session_id": "test-session-offload", "message": "Hello"},
    )

    assert response.status_code == 200
    assert landed_on_the_loop == [False]


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


def test_sessions_have_isolated_memory_stores(client, isolated_memory):
    """
    Session scoping enforced (Phase 2).

    `session_id` partitions memory so that requests in session A do not leak
    into session B history.
    """

    client.post("/api/chat", json={"session_id": "tenant-a", "message": "from a"})
    client.post("/api/chat", json={"session_id": "tenant-b", "message": "from b"})

    stored_a = [record.content for record in isolated_memory.get_recent(limit=50, session_id="tenant-a")]

    stored_b = [record.content for record in isolated_memory.get_recent(limit=50, session_id="tenant-b")]

    assert "from a" in stored_a
    assert "from b" not in stored_a

    assert "from b" in stored_b
    assert "from a" not in stored_b




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


# ----------------------------------------------------------------------
# Error taxonomy over HTTP (AURA-P1-014)
# ----------------------------------------------------------------------

def test_a_rate_limited_provider_is_429(client, monkeypatch):
    """
    The existing typed provider error maps to HTTP 429, and the
    provider-supplied Retry-After reaches the client as a header - which
    is the reason a 429 is worth distinguishing at all.
    """

    from brain.providers.errors import ProviderRateLimitError
    from server.runtime import get_runtime

    conversation = get_runtime().engine.conversation

    def limit(*args, **kwargs):
        raise ProviderRateLimitError("rate limit hit", retry_after=42)

    monkeypatch.setattr(conversation, "chat", limit)

    response = client.post(
        "/api/chat", json={"session_id": "rl", "message": "hi"}
    )

    assert response.status_code == 429
    assert response.json()["detail"]["error"] == "rate_limited"
    assert response.headers["retry-after"] == "42"


def test_an_unavailable_provider_is_503(client, monkeypatch):
    """A transient provider outage is HTTP 503, not an opaque 500."""

    from brain.providers.errors import ProviderUnavailableError
    from server.runtime import get_runtime

    conversation = get_runtime().engine.conversation

    def down(*args, **kwargs):
        raise ProviderUnavailableError("connection refused at 10.0.0.5:11434")

    monkeypatch.setattr(conversation, "chat", down)

    response = client.post(
        "/api/chat", json={"session_id": "down", "message": "hi"}
    )

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "provider_unavailable"

    # The new taxonomy must not become a new leak: the category is public,
    # the provider's own words are not.
    assert "10.0.0.5" not in response.text


def test_an_unexpected_error_is_still_a_500(client, monkeypatch):
    """
    Everything unrecognised keeps the existing contract: 500 with
    `chat_failed`, and no trace of the provider's exception text.
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
        "/api/chat", json={"session_id": "boom2", "message": "hi"}
    )

    assert response.status_code == 500
    assert response.json()["detail"]["error"] == "chat_failed"
    assert "WinError" not in response.text
    assert "C:\\Users" not in response.text


# ----------------------------------------------------------------------
# Readiness (STEP 6)
# ----------------------------------------------------------------------

def test_ready_is_public_and_reports_a_working_stack(client):
    """
    Unauthenticated on purpose - a container healthcheck holds no bearer
    token - and it answers a real question rather than "HTTP is up".
    """

    response = client.get("/api/ready")

    assert response.status_code == 200

    body = response.json()

    assert body["ready"] is True
    assert body["problems"] == []


def test_ready_reports_503_when_the_provider_chain_cannot_be_built(
    client, monkeypatch
):
    """
    The case the old healthcheck could not see: the HTTP server is
    perfectly alive and the process cannot answer a single chat turn.
    """

    from server.runtime import get_runtime

    runtime = get_runtime()

    class DeadLLM:
        def active_chain(self):
            raise RuntimeError("GEMINI_API_KEY is not set")

    monkeypatch.setattr(runtime.engine.conversation, "llm", DeadLLM())

    response = client.get("/api/ready")

    assert response.status_code == 503

    body = response.json()

    assert body["ready"] is False
    assert any("provider chain unavailable" in p for p in body["problems"])

    # Categories, not exception text: this route is public.
    assert "GEMINI_API_KEY" not in response.text


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

    # The exact contract documented in docs/API.md. `screen`, `companion`
    # and `proactive` are intentional: a client needs to know whether
    # screen observation will be accepted and whether unprompted messages
    # can arrive, before it offers either as a setting. `tools` and
    # `plugins` joined in phase 22 for the same reason: what the executor
    # would actually run, and which plugin failed to initialize, are
    # runtime facts an operator can act on - not visible anywhere else.
    assert set(data["runtime"]) == {
        "llm_provider",
        "memory",
        "vision",
        "voice_output",
        "voice_input",
        "screen",
        "companion",
        "proactive",
        "tools",
        "plugins",
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


def test_a_rate_limited_stream_says_so_instead_of_stream_failed(
    client, monkeypatch
):
    """
    The WebSocket taxonomy was verified separately from HTTP, because the
    two paths are not the same code and did not have the same vocabulary.
    A recognised provider failure now names itself here too.
    """

    from brain.providers.errors import ProviderRateLimitError
    from server.runtime import get_runtime

    runtime = get_runtime()

    def limit(*args, **kwargs):
        raise ProviderRateLimitError("quota for key sk-live-abc", retry_after=7)

    monkeypatch.setattr(runtime, "chat_stream", limit)

    with client.websocket_connect(
        "/api/chat/stream?session_id=stream-rl"
    ) as socket:

        socket.send_json({"message": "hi"})

        assert socket.receive_json()["type"] == "started"

        frame = socket.receive_json()

    assert frame["type"] == "error"
    assert frame["error"] == "rate_limited"
    assert frame["retry_after"] == 7.0
    assert "sk-live" not in str(frame)


def test_an_unrecognised_stream_failure_keeps_the_documented_code(
    client, monkeypatch
):
    """
    `stream_failed` is the existing WebSocket vocabulary (docs/API.md and
    AuraStreamClient.kt map it). Only recognised provider errors get a new
    code; renaming the generic one would be a protocol change this phase
    has no reason to make.
    """

    from server.runtime import get_runtime

    runtime = get_runtime()

    def explode(*args, **kwargs):
        raise RuntimeError("[WinError 10061] C:\\Users\\secret\\path")

    monkeypatch.setattr(runtime, "chat_stream", explode)

    with client.websocket_connect(
        "/api/chat/stream?session_id=stream-boom"
    ) as socket:

        socket.send_json({"message": "hi"})

        assert socket.receive_json()["type"] == "started"

        frame = socket.receive_json()

    assert frame["error"] == "stream_failed"
    assert "WinError" not in str(frame)
    assert "C:\\Users" not in str(frame)


# ----------------------------------------------------------------------
# Session expiry (AURA-P1-006)
# ----------------------------------------------------------------------
#
# `cleanup_old` existed with no caller anywhere in the server, so every
# client-supplied session_id stayed in the dict for the life of the
# process. These pin that it is now reachable from a normal request.


def test_stale_sessions_are_dropped_by_a_create():
    """The leak itself: an idle session goes when the next one arrives."""

    manager = SessionManager(max_age_seconds=0.01, sweep_interval_seconds=0)

    stale = manager.ensure_session("stale-id")

    time.sleep(0.02)

    manager.ensure_session("fresh-id")

    assert manager.get_session(stale.session_id) is None
    assert manager.get_session("fresh-id") is not None


def test_active_sessions_survive_a_sweep():
    """Expiry is on idle time, so a session still in use is not collected."""

    manager = SessionManager(max_age_seconds=60, sweep_interval_seconds=0)

    manager.ensure_session("busy")

    for _ in range(3):
        manager.update_activity("busy")
        manager.ensure_session("other")

    assert manager.get_session("busy") is not None


def test_sweeping_is_throttled():
    """
    A long interval means the create path does not scan every request.

    Without the throttle this is O(n) per call on a dict that only ever
    grows.
    """

    manager = SessionManager(max_age_seconds=0.01, sweep_interval_seconds=3600)

    manager.ensure_session("first")

    time.sleep(0.02)

    manager.ensure_session("second")

    # Idle past max_age, but the interval has not elapsed - still there.
    assert manager.get_session("first") is not None


def test_cleanup_old_still_reports_its_count():
    """The public entry point keeps working for a caller that wants now."""

    manager = SessionManager(max_age_seconds=0.01, sweep_interval_seconds=3600)

    manager.ensure_session("a")
    manager.ensure_session("b")

    time.sleep(0.02)

    assert manager.cleanup_old() == 2
    assert manager.cleanup_old() == 0


def test_cleanup_old_does_not_deadlock():
    """
    The lock is not reentrant.

    `cleanup_old` and the sweep share an unlocked `_expire` for exactly
    this reason; if either ever calls the other's public method instead,
    this hangs rather than fails.
    """

    manager = SessionManager(max_age_seconds=0, sweep_interval_seconds=0)

    manager.ensure_session("x")

    assert manager.cleanup_old(max_age_seconds=999) == 0


def test_expiry_drops_no_conversation_history(client):
    """
    A Session is metadata. History lives in memory/, so a swept session
    does not take the transcript with it - the next turn on the same id
    continues where it left off.
    """

    client.post("/api/chat", json={"session_id": "keeps-history", "message": "one"})

    # A negative age, not zero. `_expire` sweeps what is idle *longer than*
    # `max_age_seconds`, and `time.time()` on Windows advances in ~15.6ms
    # steps - so a session created and swept inside one tick has an age of
    # exactly 0.0, which `> 0` does not match. This test then failed about
    # one run in four, and only when other sessions were present to satisfy
    # the `>= 1` on their own. Anything negative is unambiguously "all of
    # them" without depending on the clock.
    assert session_manager.cleanup_old(max_age_seconds=-1) >= 1
    assert session_manager.get_session("keeps-history") is None

    again = client.post(
        "/api/chat",
        json={"session_id": "keeps-history", "message": "two"},
    )

    assert again.status_code == 200
    assert again.json()["session_id"] == "keeps-history"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])