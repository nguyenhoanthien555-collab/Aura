"""Cloud-only screenshot understanding with a vision-capable fallback."""

import base64
import io
import os

from brain.providers.errors import ProviderRateLimitError, ProviderUnavailableError
from brain.providers.openrouter import OpenRouterProvider
from core.logger import logger
from vision.capture import Frame
from vision.processor import MAX_DESCRIPTION
from vision.settings import cloud_model


class CloudVisionProcessor:
    """Send one reduced screenshot to cloud vision providers, never Ollama."""

    def __init__(self, providers: list, max_pixels: int = 1_500_000, jpeg_quality: int = 75):
        self.providers = providers
        self.max_pixels = max_pixels
        self.jpeg_quality = jpeg_quality
        self.last_error: Exception | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self.providers)

    def describe(self, frame: Frame | None, window_title: str = "") -> str:
        if frame is None or frame.is_empty():
            return ""
        image, mime = self._compact(frame)
        prompt = "Describe this Android screen accurately in one concise sentence. Do not invent text."
        last_error = None
        for index, provider in enumerate(self.providers):
            try:
                result = provider.describe_image(prompt, image, mime)
                self.last_error = None
                return (result or "").strip()[:MAX_DESCRIPTION]
            except ProviderUnavailableError as error:
                last_error = error
                if index + 1 < len(self.providers):
                    logger.warning("Cloud vision provider unavailable; trying configured vision fallback")
        self.last_error = last_error or ProviderUnavailableError("No cloud vision provider is configured")
        raise self.last_error

    def _compact(self, frame: Frame) -> tuple[bytes, str]:
        """Decode/validate and downscale before a provider receives pixels."""
        try:
            from PIL import Image
            with Image.open(io.BytesIO(frame.data)) as image:
                image.verify()
            with Image.open(io.BytesIO(frame.data)) as image:
                image = image.convert("RGB")
                if image.width * image.height > self.max_pixels:
                    scale = (self.max_pixels / (image.width * image.height)) ** 0.5
                    image.thumbnail((max(1, int(image.width * scale)), max(1, int(image.height * scale))))
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=self.jpeg_quality, optimize=True)
                return output.getvalue(), "image/jpeg"
        except Exception as error:
            raise ProviderUnavailableError("Screenshot could not be decoded") from error


class GeminiVisionProvider:
    provider_name = "gemini-vision"
    supports_text = False
    supports_vision = True

    def __init__(self, model: str):
        from google import genai
        self.client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        self.model = model

    def describe_image(self, prompt: str, image: bytes, mime: str) -> str:
        from google.genai import types
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=[prompt, types.Part.from_bytes(data=image, mime_type=mime)],
            )
            return response.text or ""
        except Exception as error:
            status = getattr(error, "code", None) or getattr(error, "status_code", None)
            text = str(error).lower()
            if status == 429 or "resource_exhausted" in text or "quota" in text:
                raise ProviderRateLimitError("Gemini vision quota/rate limit reached") from error
            if (isinstance(status, int) and status >= 500) or any(word in text for word in ("unavailable", "timeout", "connection")):
                raise ProviderUnavailableError("Gemini vision is unavailable") from error
            raise


class OpenRouterVisionProvider(OpenRouterProvider):
    provider_name = "openrouter-vision"
    supports_vision = True

    def describe_image(self, prompt: str, image: bytes, mime: str) -> str:
        encoded = base64.b64encode(image).decode("ascii")
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
            ],
        }]

        # Detect openrouter/free and use specific free vision models to guarantee vision capability
        models_to_try = [self.model]
        if self.model == "openrouter/free":
            models_to_try = [
                "google/gemma-4-31b-it:free",
                "nvidia/nemotron-nano-12b-v2-vl:free",
                "google/gemma-4-26b-a4b-it:free",
                "openrouter/free",  # last resort fallback
            ]
        elif self.model in ("google/gemma-4-31b-it:free", "nvidia/nemotron-nano-12b-v2-vl:free", "google/gemma-4-26b-a4b-it:free"):
            models_to_try = [self.model] + [m for m in [
                "google/gemma-4-31b-it:free",
                "nvidia/nemotron-nano-12b-v2-vl:free",
                "google/gemma-4-26b-a4b-it:free",
            ] if m != self.model]

        last_error = None
        for model in models_to_try:
            try:
                # Temporarily switch self.model to the model being tried
                original_model = self.model
                self.model = model
                data = self._request(messages)
                self.model = original_model

                if "choices" in data and len(data["choices"]) > 0:
                    choice = data["choices"][0]
                    if "message" in choice and "content" in choice["message"]:
                        content = choice["message"]["content"]
                        if content:
                            return content.strip()
                raise ProviderUnavailableError(f"Model {model} returned an empty or invalid response")
            except ProviderRateLimitError as error:
                if getattr(error, "is_account_limit", False):
                    raise
                last_error = error
                logger.warning("OpenRouter vision try with model %s failed (rate limit): %s", model, error)
                continue
            except Exception as error:
                last_error = error
                logger.warning("OpenRouter vision try with model %s failed: %s", model, error)
                continue

        # If all fail, raise the last encountered error
        raise ProviderUnavailableError("All configured/fallback vision models on OpenRouter failed") from last_error


def build_cloud_vision_processor(config: dict) -> CloudVisionProcessor | None:
    vision = config.get("vision") or {}
    llm = config.get("llm") or {}
    providers = []
    if os.getenv("GEMINI_API_KEY"):
        prov = GeminiVisionProvider(cloud_model(config))
        if getattr(prov, "supports_vision", False):
            providers.append(prov)
    if os.getenv("OPENROUTER_API_KEY"):
        prov = OpenRouterVisionProvider(
            model=vision.get("fallback_model") or llm.get("fallback_model") or "openrouter/free",
            timeout=float(vision.get("timeout") or llm.get("timeout") or 45.0),
            max_tokens=220,
        )
        if getattr(prov, "supports_vision", False):
            providers.append(prov)
    return CloudVisionProcessor(
        providers,
        max_pixels=int(vision.get("max_pixels", 1_500_000)),
        jpeg_quality=int(vision.get("jpeg_quality", 75)),
    ) if providers else None
