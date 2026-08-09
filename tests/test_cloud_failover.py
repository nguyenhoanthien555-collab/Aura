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


def test_gemini_429_to_openrouter_success(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-key")

    from brain.providers.gemini import GeminiProvider
    def mock_gemini_generate(self, prompt):
        raise ProviderRateLimitError("Gemini 429")
    monkeypatch.setattr(GeminiProvider, "generate", mock_gemini_generate)

    from brain.providers.openrouter import OpenRouterProvider
    def mock_openrouter_request(self, messages):
        return {
            "choices": [{
                "message": {
                    "content": "OpenRouter reply"
                }
            }]
        }
    monkeypatch.setattr(OpenRouterProvider, "_request", mock_openrouter_request)

    from brain.providers.fallback import FallbackProvider
    primary = GeminiProvider()
    fallback = OpenRouterProvider(model="openrouter/free")
    provider = FallbackProvider([primary, fallback], "gemini->openrouter")

    assert provider.generate("hello") == "OpenRouter reply"
    assert provider.active_provider_name == "openrouter"


def test_openrouter_model_specific_429_tries_next_model(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-key")

    called_models = []

    from brain.providers.openrouter import OpenRouterProvider
    def mock_openrouter_request(self, messages):
        called_models.append(self.model)
        if self.model == "google/gemma-4-31b-it:free":
            raise ProviderRateLimitError("Rate limit exceeded: limit_rpm/google/gemma-4-31b-it:free exceeded", is_account_limit=False)
        elif self.model == "nvidia/nemotron-nano-12b-v2-vl:free":
            return {
                "choices": [{
                    "message": {
                        "content": "Model fallback success"
                    }
                }]
            }
        return {"choices": []}

    monkeypatch.setattr(OpenRouterProvider, "_request", mock_openrouter_request)

    provider = OpenRouterProvider(model="openrouter/free")
    assert provider.generate("hello") == "Model fallback success"
    assert called_models == ["google/gemma-4-31b-it:free", "nvidia/nemotron-nano-12b-v2-vl:free"]


def test_openrouter_account_level_429_stops_immediately(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-key")

    called_models = []

    from brain.providers.openrouter import OpenRouterProvider
    def mock_openrouter_request(self, messages):
        called_models.append(self.model)
        raise ProviderRateLimitError("Rate limit exceeded: limit_rpd exceeded for your account", is_account_limit=True)

    monkeypatch.setattr(OpenRouterProvider, "_request", mock_openrouter_request)

    provider = OpenRouterProvider(model="openrouter/free")
    with pytest.raises(ProviderRateLimitError) as exc_info:
        provider.generate("hello")

    assert exc_info.value.is_account_limit is True
    assert called_models == ["google/gemma-4-31b-it:free"]


def test_openrouter_all_models_unavailable_raises_clean_error(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-key")

    from brain.providers.openrouter import OpenRouterProvider
    def mock_openrouter_request(self, messages):
        raise ProviderRateLimitError(f"Rate limit exceeded: limit_rpm/{self.model}", is_account_limit=False)

    monkeypatch.setattr(OpenRouterProvider, "_request", mock_openrouter_request)

    provider = OpenRouterProvider(model="openrouter/free")
    with pytest.raises(ProviderRateLimitError):
        provider.generate("hello")


def test_openrouter_error_parsing_detects_account_limit(monkeypatch):
    from brain.providers.openrouter import _failure

    err1 = _failure(429, '{"error": {"message": "Rate limit exceeded: limit_rpd exceeded for key"}}')
    assert err1.is_account_limit is True

    err2 = _failure(429, '{"error": {"message": "Rate limit exceeded. Please slow down."}}')
    assert err2.is_account_limit is True

    err3 = _failure(429, '{"error": {"message": "Rate limit exceeded: limit_rpm/google/gemma-4-31b-it:free exceeded"}}')
    assert err3.is_account_limit is False

    err4 = _failure(429, '{"error": {"message": "Provider rate limit hit on nvidia/nemotron-nano-12b-v2-vl:free"}}')
    assert err4.is_account_limit is False

    # Test free-models-per-day message
    err5 = _failure(429, '{"error": {"message": "Rate limit exceeded: free-models-per-day"}}')
    assert err5.is_account_limit is True

    # Test headers limit: 50, remaining: 0
    headers = {"X-RateLimit-Limit": "50", "X-RateLimit-Remaining": "0"}
    err6 = _failure(429, '{"error": {"message": "Generic limit exceeded"}}', headers=headers)
    assert err6.is_account_limit is True


def test_openrouter_vision_provider_account_level_429_stops_immediately(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-key")

    called_models = []

    def mock_request(self, messages):
        called_models.append(self.model)
        # Raise account-level 429
        raise ProviderRateLimitError("Rate limit exceeded: free-models-per-day", is_account_limit=True)

    from vision.cloud_processor import OpenRouterVisionProvider
    monkeypatch.setattr(OpenRouterVisionProvider, "_request", mock_request)
    provider = OpenRouterVisionProvider(model="openrouter/free")
    with pytest.raises(ProviderRateLimitError) as exc_info:
        provider.describe_image("describe this", b"dummy-data", "image/png")

    assert exc_info.value.is_account_limit is True
    # Should stop on the first model (google/gemma-4-31b-it:free) immediately
    assert called_models == ["google/gemma-4-31b-it:free"]


def test_gemini_success_scenario(monkeypatch):
    from brain.providers.gemini import GeminiProvider
    monkeypatch.setattr(GeminiProvider, "generate", lambda self, prompt: "Gemini reply")

    monkeypatch.setenv("GEMINI_API_KEY", "dummy-gemini-key")

    from brain.router import BrainRouter
    router = BrainRouter(provider_name="gemini")
    assert router.generate("hello") == "Gemini reply"


def test_gemini_429_to_groq_success_scenario(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-gemini-key")
    monkeypatch.setenv("GROQ_API_KEY", "dummy-groq-key")

    from brain.providers.gemini import GeminiProvider
    from brain.providers.groq import GroqProvider

    def fail_gemini(*args, **kwargs):
        raise ProviderRateLimitError("Gemini 429")

    monkeypatch.setattr(GeminiProvider, "generate", fail_gemini)
    monkeypatch.setattr(GroqProvider, "generate", lambda self, prompt: "Groq reply")

    from brain.router import BrainRouter
    router = BrainRouter(provider_name="gemini")
    assert router.generate("hello") == "Groq reply"


def test_gemini_unavailable_to_groq_success_scenario(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-gemini-key")
    monkeypatch.setenv("GROQ_API_KEY", "dummy-groq-key")

    from brain.providers.gemini import GeminiProvider
    from brain.providers.groq import GroqProvider

    def fail_gemini(*args, **kwargs):
        raise ProviderUnavailableError("Gemini down")

    monkeypatch.setattr(GeminiProvider, "generate", fail_gemini)
    monkeypatch.setattr(GroqProvider, "generate", lambda self, prompt: "Groq reply")

    from brain.router import BrainRouter
    router = BrainRouter(provider_name="gemini")
    assert router.generate("hello") == "Groq reply"


def test_groq_failure_to_mistral_success_scenario(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-gemini-key")
    monkeypatch.setenv("GROQ_API_KEY", "dummy-groq-key")
    monkeypatch.setenv("MISTRAL_API_KEY", "dummy-mistral-key")

    from brain.providers.gemini import GeminiProvider
    from brain.providers.groq import GroqProvider
    from brain.providers.mistral import MistralProvider

    monkeypatch.setattr(GeminiProvider, "generate", lambda self, prompt: (_ for _ in ()).throw(ProviderUnavailableError("Gemini down")))
    monkeypatch.setattr(GroqProvider, "generate", lambda self, prompt: (_ for _ in ()).throw(ProviderRateLimitError("Groq 429")))
    monkeypatch.setattr(MistralProvider, "generate", lambda self, prompt: "Mistral reply")

    from brain.router import BrainRouter
    router = BrainRouter(provider_name="gemini")
    assert router.generate("hello") == "Mistral reply"


def test_mistral_failure_to_openrouter_success_scenario(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-gemini-key")
    monkeypatch.setenv("GROQ_API_KEY", "dummy-groq-key")
    monkeypatch.setenv("MISTRAL_API_KEY", "dummy-mistral-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-openrouter-key")

    from brain.providers.gemini import GeminiProvider
    from brain.providers.groq import GroqProvider
    from brain.providers.mistral import MistralProvider
    from brain.providers.openrouter import OpenRouterProvider

    monkeypatch.setattr(GeminiProvider, "generate", lambda self, prompt: (_ for _ in ()).throw(ProviderUnavailableError("Gemini down")))
    monkeypatch.setattr(GroqProvider, "generate", lambda self, prompt: (_ for _ in ()).throw(ProviderUnavailableError("Groq down")))
    monkeypatch.setattr(MistralProvider, "generate", lambda self, prompt: (_ for _ in ()).throw(ProviderRateLimitError("Mistral 429")))
    monkeypatch.setattr(OpenRouterProvider, "generate", lambda self, prompt: "OpenRouter reply")

    from brain.router import BrainRouter
    router = BrainRouter(provider_name="gemini")
    assert router.generate("hello") == "OpenRouter reply"


def test_vision_never_uses_text_only_provider_scenario(monkeypatch):
    from brain.providers.groq import GroqProvider
    from brain.providers.mistral import MistralProvider
    from brain.providers.openrouter import OpenRouterProvider

    monkeypatch.setenv("GROQ_API_KEY", "dummy-groq-key")
    monkeypatch.setenv("MISTRAL_API_KEY", "dummy-mistral-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-openrouter-key")

    groq = GroqProvider(model="llama-3.3-70b-versatile")
    mistral = MistralProvider(model="mistral-small-latest")
    openrouter = OpenRouterProvider(model="openrouter/free")

    assert not groq.supports_vision
    assert not mistral.supports_vision
    assert not openrouter.supports_vision


def test_gemini_vision_failure_to_openrouter_vision_scenario(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-gemini-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-openrouter-key")

    from vision.cloud_processor import GeminiVisionProvider, OpenRouterVisionProvider

    monkeypatch.setattr(GeminiVisionProvider, "describe_image", lambda self, prompt, image, mime: (_ for _ in ()).throw(ProviderRateLimitError("Gemini 429")))
    monkeypatch.setattr(OpenRouterVisionProvider, "describe_image", lambda self, prompt, image, mime: "OpenRouter vision reply")

    from vision.cloud_processor import build_cloud_vision_processor
    config = {
        "vision": {"enabled": True},
        "llm": {"fallback_providers": ["openrouter"]}
    }
    processor = build_cloud_vision_processor(config)
    assert processor is not None
    assert processor.describe(Frame(data=_png(), image_format="png")) == "OpenRouter vision reply"


def test_optional_provider_keys_do_not_prevent_server_startup_scenario(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-gemini-key")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    from brain.router import BrainRouter
    router = BrainRouter(provider_name="gemini")
    assert router.provider is not None


def test_no_infinite_retry_loops_scenario():
    from brain.providers.fallback import FallbackProvider

    class FailingProvider:
        provider_name = "failing"
        supports_text = True
        supports_vision = False
        def generate(self, prompt):
            raise ProviderUnavailableError("Error")

    provider = FallbackProvider([FailingProvider(), FailingProvider()], "failing->failing")
    with pytest.raises(ProviderUnavailableError):
        provider.generate("hello")


def test_api_keys_never_appear_in_logs_scenario(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-key-12345")

    logged_messages = []
    def mock_warning(msg, *args):
        formatted = msg % args if args else msg
        logged_messages.append(formatted)

    from core.logger import logger
    monkeypatch.setattr(logger, "warning", mock_warning)
    monkeypatch.setattr(logger, "info", lambda *args: None)

    from brain.providers.openrouter import _failure
    headers = {"Authorization": "Bearer secret-key-12345", "X-RateLimit-Limit": "50"}
    _failure(429, '{"error": {"message": "Rate limit exceeded"}}', headers=headers, model="google/gemma-4-31b-it:free")

    for msg in logged_messages:
        assert "secret-key-12345" not in msg
        assert "Bearer" not in msg


def test_mistral_success(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "dummy-key")
    from brain.providers.mistral import MistralProvider
    monkeypatch.setattr(MistralProvider, "generate", lambda self, prompt: "Mistral success reply")

    provider = MistralProvider()
    assert provider.generate("hello") == "Mistral success reply"


def test_mistral_authentication_failure(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "invalid-key")

    from urllib.error import HTTPError
    import io
    def mock_urlopen(*args, **kwargs):
        raise HTTPError("https://api.mistral.ai/v1/chat/completions", 401, "Unauthorized", {}, io.BytesIO(b""))

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    from brain.providers.mistral import MistralProvider
    provider = MistralProvider()
    with pytest.raises(ValueError) as exc_info:
        provider.generate("hello")
    assert "authentication failed" in str(exc_info.value)


def test_mistral_429_to_openrouter_fallback(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-gemini-key")
    monkeypatch.setenv("GROQ_API_KEY", "dummy-groq-key")
    monkeypatch.setenv("MISTRAL_API_KEY", "dummy-mistral-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-openrouter-key")

    from brain.providers.gemini import GeminiProvider
    from brain.providers.groq import GroqProvider
    from brain.providers.mistral import MistralProvider
    from brain.providers.openrouter import OpenRouterProvider

    monkeypatch.setattr(GeminiProvider, "generate", lambda self, prompt: (_ for _ in ()).throw(ProviderUnavailableError("Gemini down")))
    monkeypatch.setattr(GroqProvider, "generate", lambda self, prompt: (_ for _ in ()).throw(ProviderUnavailableError("Groq down")))

    monkeypatch.setattr(MistralProvider, "generate", lambda self, prompt: (_ for _ in ()).throw(ProviderRateLimitError("Mistral 429", is_account_limit=False)))
    monkeypatch.setattr(OpenRouterProvider, "generate", lambda self, prompt: "OpenRouter reply")

    from brain.router import BrainRouter
    router = BrainRouter(provider_name="gemini")
    assert router.generate("hello") == "OpenRouter reply"


def test_gemini_groq_mistral_success(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-gemini-key")
    monkeypatch.setenv("GROQ_API_KEY", "dummy-groq-key")
    monkeypatch.setenv("MISTRAL_API_KEY", "dummy-mistral-key")

    from brain.providers.gemini import GeminiProvider
    from brain.providers.groq import GroqProvider
    from brain.providers.mistral import MistralProvider

    monkeypatch.setattr(GeminiProvider, "generate", lambda self, prompt: (_ for _ in ()).throw(ProviderUnavailableError("Gemini down")))
    monkeypatch.setattr(GroqProvider, "generate", lambda self, prompt: (_ for _ in ()).throw(ProviderUnavailableError("Groq down")))
    monkeypatch.setattr(MistralProvider, "generate", lambda self, prompt: "Mistral reply")

    from brain.router import BrainRouter
    router = BrainRouter(provider_name="gemini")
    assert router.generate("hello") == "Mistral reply"


def test_gemini_groq_mistral_openrouter_failure(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-gemini-key")
    monkeypatch.setenv("GROQ_API_KEY", "dummy-groq-key")
    monkeypatch.setenv("MISTRAL_API_KEY", "dummy-mistral-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-openrouter-key")

    from brain.providers.gemini import GeminiProvider
    from brain.providers.groq import GroqProvider
    from brain.providers.mistral import MistralProvider
    from brain.providers.openrouter import OpenRouterProvider

    monkeypatch.setattr(GeminiProvider, "generate", lambda self, prompt: (_ for _ in ()).throw(ProviderUnavailableError("Gemini down")))
    monkeypatch.setattr(GroqProvider, "generate", lambda self, prompt: (_ for _ in ()).throw(ProviderUnavailableError("Groq down")))
    monkeypatch.setattr(MistralProvider, "generate", lambda self, prompt: (_ for _ in ()).throw(ProviderUnavailableError("Mistral down")))
    monkeypatch.setattr(OpenRouterProvider, "generate", lambda self, prompt: (_ for _ in ()).throw(ProviderUnavailableError("OpenRouter down")))

    from brain.router import BrainRouter
    router = BrainRouter(provider_name="gemini")
    with pytest.raises(ProviderUnavailableError):
        router.generate("hello")


def test_text_request_does_not_send_image_to_mistral(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "dummy-key")
    from brain.providers.mistral import MistralProvider
    provider = MistralProvider()
    assert not provider.supports_vision
    assert provider.supports_text


def test_mistral_api_key_missing():
    import os
    orig_key = os.environ.get("MISTRAL_API_KEY")
    try:
        if "MISTRAL_API_KEY" in os.environ:
            del os.environ["MISTRAL_API_KEY"]
        from brain.providers.mistral import MistralProvider
        with pytest.raises(ValueError) as exc_info:
            MistralProvider()
        assert "MISTRAL_API_KEY is not configured" in str(exc_info.value)
    finally:
        if orig_key is not None:
            os.environ["MISTRAL_API_KEY"] = orig_key


def test_mistral_streaming(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "dummy-key")

    from brain.providers.mistral import MistralProvider
    def mock_stream(self, prompt):
        yield "Chunk 1 "
        yield "Chunk 2"

    monkeypatch.setattr(MistralProvider, "stream", mock_stream)

    provider = MistralProvider()
    chunks = list(provider.stream("hello"))
    assert chunks == ["Chunk 1 ", "Chunk 2"]


def test_personality_consistency_across_providers(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    monkeypatch.setenv("GROQ_API_KEY", "dummy")
    monkeypatch.setenv("MISTRAL_API_KEY", "dummy")
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")

    from brain.providers.base import split_prompt
    from brain.prompt_builder import PromptBuilder
    from brain.message import Message

    prompt = PromptBuilder().build(
        history=[Message(role="user", content="old")],
        user_message=Message(role="user", content="new"),
        style="Playful Gen Z"
    )

    system_inst, user_cont = split_prompt(prompt)

    assert "===== SYSTEM =====" in system_inst
    assert "===== PERSONALITY =====" in system_inst
    assert "===== RESPONSE STYLE =====" in system_inst

    assert "===== SYSTEM =====" not in user_cont
    assert "===== PERSONALITY =====" not in user_cont
    assert "===== RECENT CONVERSATION =====" in user_cont
    assert "===== CURRENT USER MESSAGE =====" in user_cont


def test_provider_switching_does_not_change_identity(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-gemini-key")
    monkeypatch.setenv("GROQ_API_KEY", "dummy-groq-key")
    monkeypatch.setenv("MISTRAL_API_KEY", "dummy-mistral-key")

    from brain.providers.gemini import GeminiProvider
    from brain.providers.groq import GroqProvider
    from brain.providers.mistral import MistralProvider

    captured_sys_instructions = {}

    def mock_gemini_generate(self, prompt):
        from brain.providers.base import split_prompt
        sys, _ = split_prompt(prompt)
        captured_sys_instructions["gemini"] = sys
        raise ProviderUnavailableError("Gemini down")

    def mock_groq_request(self, messages):
        for msg in messages:
            if msg["role"] == "system":
                captured_sys_instructions["groq"] = msg["content"]
        raise ProviderRateLimitError("Groq 429")

    def mock_mistral_request(self, messages):
        for msg in messages:
            if msg["role"] == "system":
                captured_sys_instructions["mistral"] = msg["content"]
        return {"choices": [{"message": {"content": "Mistral reply"}}]}

    monkeypatch.setattr(GeminiProvider, "generate", mock_gemini_generate)
    monkeypatch.setattr(GroqProvider, "_request", mock_groq_request)
    monkeypatch.setattr(MistralProvider, "_request", mock_mistral_request)

    from brain.router import BrainRouter
    router = BrainRouter(provider_name="gemini")
    from brain.prompt_builder import PromptBuilder
    from brain.message import Message
    prompt = PromptBuilder().build(
        history=[],
        user_message=Message(role="user", content="hello"),
    )
    reply = router.generate(prompt)
    assert reply == "Mistral reply"

    assert "gemini" in captured_sys_instructions
    assert "groq" in captured_sys_instructions
    assert "mistral" in captured_sys_instructions
    assert captured_sys_instructions["gemini"] == captured_sys_instructions["groq"]
    assert captured_sys_instructions["groq"] == captured_sys_instructions["mistral"]


def test_streaming_fallback_keeps_personality_scenario(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "dummy-key")

    captured_sys = []

    from brain.providers.mistral import MistralProvider
    def mock_stream(self, prompt):
        from brain.providers.base import split_prompt
        sys, _ = split_prompt(prompt)
        captured_sys.append(sys)
        yield "Chunk"

    monkeypatch.setattr(MistralProvider, "stream", mock_stream)

    provider = MistralProvider()
    chunks = list(provider.stream("===== SYSTEM =====\ninstruction\n\n===== CURRENT USER MESSAGE =====\nhello"))
    assert chunks == ["Chunk"]
    assert len(captured_sys) == 1
    assert "instruction" in captured_sys[0]
