from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from scripts import aura_android
from server import auth
from server.config import ServerSettings
from server.routes.device import router as device_router

FAKE_TOKEN = "fake-device-token-for-tests"

def test_cli_reads_server_token_from_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("AURA_TOKEN", raising=False)
    monkeypatch.delenv("AURA_SERVER_AUTH_TOKEN", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(f"AURA_SERVER_AUTH_TOKEN={FAKE_TOKEN}\n", encoding="utf-8")
    assert aura_android.configured_auth_token(env_file) == FAKE_TOKEN

def test_cli_legacy_override_is_backwards_compatible(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("AURA_SERVER_AUTH_TOKEN=from-file\n", encoding="utf-8")
    monkeypatch.setenv("AURA_SERVER_AUTH_TOKEN", "server-export")
    monkeypatch.setenv("AURA_TOKEN", "legacy-export")
    assert aura_android.configured_auth_token(env_file) == "legacy-export"

def test_server_project_env_is_not_cwd_dependent(monkeypatch, tmp_path):
    monkeypatch.delenv("AURA_SERVER_AUTH_TOKEN", raising=False)
    project_env = Path(__file__).resolve().parent.parent / ".env"
    monkeypatch.chdir(tmp_path)
    configured = ServerSettings()
    expected = aura_android.configured_auth_token(project_env)
    assert expected
    assert configured.auth_token == expected

def test_device_routes_auth_and_no_secret_leakage(monkeypatch):
    monkeypatch.setattr(auth.settings, "auth_token", FAKE_TOKEN)
    app = FastAPI()
    app.include_router(device_router)
    with TestClient(app) as client:
        missing = client.post("/api/device/poll", json={"device_id": "test"})
        wrong = client.post("/api/device/poll", json={"device_id": "test"}, headers={"Authorization": "Bearer wrong-token"})
        valid = client.post("/api/device/poll", json={"device_id": "test"}, headers={"Authorization": f"Bearer {FAKE_TOKEN}"})
    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert valid.status_code == 200
    assert valid.json() == {"invocations": []}
    assert FAKE_TOKEN not in missing.text + wrong.text + valid.text


def test_device_poll_supports_long_polling(monkeypatch):
    import threading
    import time
    from server.device_gateway import configure_device_gateway, DeviceGateway

    monkeypatch.setattr(auth.settings, "auth_token", FAKE_TOKEN)
    gw = DeviceGateway()
    configure_device_gateway(gw)

    app = FastAPI()
    app.include_router(device_router)

    with TestClient(app) as client:
        # 1. Immediate poll with timeout_s=0
        r = client.post(
            "/api/device/poll",
            json={"device_id": "test", "timeout_s": 0.0},
            headers={"Authorization": f"Bearer {FAKE_TOKEN}"},
        )
        assert r.status_code == 200
        assert r.json() == {"invocations": []}

        # 2. Long poll woken by submit
        received = []

        def poll_worker():
            resp = client.post(
                "/api/device/poll",
                json={"device_id": "test", "timeout_s": 2.0},
                headers={"Authorization": f"Bearer {FAKE_TOKEN}"},
            )
            received.append(resp.json())

        t = threading.Thread(target=poll_worker)
        t.start()

        time.sleep(0.05)
        gw.submit(tool="android.tap", arguments={"text": "OK"}, timeout_s=0.5)
        t.join(timeout=3.0)

        assert len(received) == 1
        invs = received[0].get("invocations", [])
        assert len(invs) == 1
        assert invs[0]["tool"] == "android.tap"
