"""Cloud failover and screenshot-vision behavior without real credentials."""

import io

import pytest
from PIL import Image

from brain.providers.errors import ProviderRateLimitError, ProviderUnavailableError
from brain.providers.fallback import FallbackProvider
from vision.capture import Frame
from vision.cloud_processor import CloudVisionProcessor


class ReplyProvider:
    provider_name = "fallback-cloud"

    def generate(self, prompt):
        return "fallback reply"


class LimitedProvider:
    provider_name = "primary-cloud"

    def generate(self, prompt):
        raise ProviderRateLimitError("daily quota reached")


def test_chat_429_uses_the_configured_cloud_fallback_once():
    provider = FallbackProvider([LimitedProvider(), ReplyProvider()], "primary-cloud->fallback-cloud")

    assert provider.generate("hello") == "fallback reply"
    assert provider.active_provider_name == "fallback-cloud"


def test_both_cloud_providers_unavailable_is_an_honest_failure():
    class Down:
        provider_name = "down"

        def generate(self, prompt):
            raise ProviderUnavailableError("down")

    with pytest.raises(ProviderUnavailableError):
        FallbackProvider([Down(), Down()], "a->b").generate("hello")


def _png(width=2400, height=1600):
    image = Image.new("RGB", (width, height), color="navy")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_vision_downscales_and_uses_a_vision_capable_fallback():
    class LimitedVision:
        def describe_image(self, prompt, image, mime):
            raise ProviderRateLimitError("quota")

    class WorkingVision:
        def __init__(self):
            self.image = None
            self.mime = None

        def describe_image(self, prompt, image, mime):
            self.image, self.mime = image, mime
            return "A blue Android screen"

    fallback = WorkingVision()
    processor = CloudVisionProcessor([LimitedVision(), fallback], max_pixels=100_000)

    assert processor.describe(Frame(data=_png(), image_format="png")) == "A blue Android screen"
    assert fallback.mime == "image/jpeg"
    assert len(fallback.image) < len(_png())


def test_invalid_image_is_not_sent_to_any_provider():
    processor = CloudVisionProcessor([object()])

    with pytest.raises(ProviderUnavailableError):
        processor.describe(Frame(data=b"not-an-image", image_format="png"))


def test_no_vision_provider_is_a_clear_failure():
    processor = CloudVisionProcessor([])

    with pytest.raises(ProviderUnavailableError):
        processor.describe(Frame(data=_png(), image_format="png"))


def test_openrouter_vision_provider_fallback_list(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-key")

    called_models = []

    def mock_request(self, messages):
        called_models.append(self.model)
        if self.model == "google/gemma-4-31b-it:free":
            raise ProviderRateLimitError("429 rate limit")
        elif self.model == "nvidia/nemotron-nano-12b-v2-vl:free":
            return {
                "choices": [{
                    "message": {
                        "content": "A beautiful view"
                    }
                }]
            }
        return {"choices": []}

    from vision.cloud_processor import OpenRouterVisionProvider
    monkeypatch.setattr(OpenRouterVisionProvider, "_request", mock_request)

    provider = OpenRouterVisionProvider(model="openrouter/free")
    description = provider.describe_image("describe this", b"dummy-data", "image/png")

    assert description == "A beautiful view"
    assert called_models == ["google/gemma-4-31b-it:free", "nvidia/nemotron-nano-12b-v2-vl:free"]


def test_upload_screenshot_route_success(monkeypatch):
    from fastapi.testclient import TestClient
    from server.main import app
    from server.auth import verify_token

    app.dependency_overrides[verify_token] = lambda: "dummy-token"

    class MockRuntime:
        def __init__(self):
            self.screen_enabled = True
            class MockVision:
                class MockProcessor:
                    last_error = None
                processor = MockProcessor()
            self.vision = MockVision()
            self.observed = []

        def observe_screen(self, observation):
            self.observed.append(observation)
            return {"accepted": True, "decision": {"should_notify": True, "message": "hello"}}

    mock_runtime = MockRuntime()
    monkeypatch.setattr("server.routes.screen.get_runtime", lambda: mock_runtime)

    client = TestClient(app)
    img_data = _png(100, 100)
    response = client.post(
        "/api/screen/upload",
        data={
            "session_id": "test-session",
            "device_id": "test-device",
            "application": "TestApp",
            "package": "com.test",
            "timestamp": 12345.6
        },
        files={"screenshot": ("screenshot.png", img_data, "image/png")}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "accepted"
    assert data["accepted"] is True
    assert data["decision"]["should_notify"] is True

    app.dependency_overrides.clear()


def test_upload_screenshot_disabled(monkeypatch):
    from fastapi.testclient import TestClient
    from server.main import app
    from server.auth import verify_token

    app.dependency_overrides[verify_token] = lambda: "dummy-token"

    class MockRuntime:
        screen_enabled = False

    monkeypatch.setattr("server.routes.screen.get_runtime", lambda: MockRuntime())

    client = TestClient(app)
    response = client.post(
        "/api/screen/upload",
        data={"session_id": "test"},
        files={"screenshot": ("screenshot.png", b"data", "image/png")}
    )
    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "screen_disabled"

    app.dependency_overrides.clear()


def test_upload_screenshot_oversized(monkeypatch):
    from fastapi.testclient import TestClient
    from server.main import app
    from server.auth import verify_token

    app.dependency_overrides[verify_token] = lambda: "dummy-token"

    class MockRuntime:
        screen_enabled = True

    monkeypatch.setattr("server.routes.screen.get_runtime", lambda: MockRuntime())

    from server.config import settings
    original_max = settings.max_upload_bytes
    settings.max_upload_bytes = 10

    client = TestClient(app)
    response = client.post(
        "/api/screen/upload",
        data={"session_id": "test"},
        files={"screenshot": ("screenshot.png", b"longer than 10 bytes", "image/png")}
    )
    assert response.status_code == 413
    assert response.json()["detail"]["error"] == "screenshot_too_large"

    settings.max_upload_bytes = original_max
    app.dependency_overrides.clear()


def test_upload_screenshot_unsupported_format(monkeypatch):
    from fastapi.testclient import TestClient
    from server.main import app
    from server.auth import verify_token

    app.dependency_overrides[verify_token] = lambda: "dummy-token"

    class MockRuntime:
        screen_enabled = True

    monkeypatch.setattr("server.routes.screen.get_runtime", lambda: MockRuntime())

    client = TestClient(app)
    response = client.post(
        "/api/screen/upload",
        data={"session_id": "test"},
        files={"screenshot": ("screenshot.svg", b"<svg></svg>", "image/svg+xml")}
    )
    assert response.status_code == 415
    assert response.json()["detail"]["error"] == "unsupported_image_type"

    app.dependency_overrides.clear()


def test_upload_screenshot_vision_unavailable(monkeypatch):
    from fastapi.testclient import TestClient
    from server.main import app
    from server.auth import verify_token

    app.dependency_overrides[verify_token] = lambda: "dummy-token"

    class MockRuntime:
        def __init__(self):
            self.screen_enabled = True
            class MockVision:
                class MockProcessor:
                    last_error = ProviderUnavailableError("quota reached")
                processor = MockProcessor()
            self.vision = MockVision()

        def observe_screen(self, observation):
            return {"accepted": True, "decision": {}}

    monkeypatch.setattr("server.routes.screen.get_runtime", lambda: MockRuntime())

    client = TestClient(app)
    response = client.post(
        "/api/screen/upload",
        data={"session_id": "test"},
        files={"screenshot": ("screenshot.png", _png(50, 50), "image/png")}
    )
    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "vision_unavailable"

    app.dependency_overrides.clear()
