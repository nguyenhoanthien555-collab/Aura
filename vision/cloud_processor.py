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


# What the picture is of, in the words the model is told. `Frame.source`
# is set by whoever captured it: "screen" by both desktop backends,
# "phone" by the upload route, "mock" by the test double.
#
# This used to be the fixed string "Android screen", which was true of
# the only caller at the time and became a lie the moment phase 19 let a
# desktop frame reach here - telling a vision model it is looking at a
# phone is an invitation to describe one, and the same prompt ends with
# "Do not invent text".
SUBJECTS = {
    "phone": "Android phone screen",
    "screen": "computer screen",
}


def _subject(frame: Frame) -> str:
    """The neutral word when the source is one nobody taught this map."""

    return SUBJECTS.get(frame.source, "screen")


class CloudVisionProcessor:
    """Send one reduced screenshot to cloud vision providers, never Ollama."""

    # Where the pixels can end up, advertised so a caller can price the
    # act rather than guess at it. Read with getattr and a False default
    # at every use site, so this is a fact a processor may offer and not
    # a member the VisionProcessor protocol demands.
    # The one processor for which this is True, and the reason the flag
    # exists: a picture of the owner's screen goes to a third party.
    sends_pixels_offsite = True

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
        prompt = (
            f"Describe this {_subject(frame)} accurately in one concise "
            f"sentence. Do not invent text."
        )
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
        """
        Decode/validate and downscale before a provider receives pixels.

        Two frame shapes arrive here, and until phase 19 only one of them
        worked. A device uploads an encoded image - PNG, JPEG, WebP -
        which `Image.open` reads from its header. `GdiScreenCapture` and
        `ScreenshotCapture` produce raw RGB, which has no header to find,
        so every desktop frame came back as
        ProviderUnavailableError("Screenshot could not be decoded").
        Measured before the fix on a real GDI frame; the encoded path
        was fine, which is why the phone half never showed it.

        The raw branch is `OllamaVisionProcessor._to_png`'s idiom rather
        than a second one: `Image.frombytes` with the frame's own
        geometry, which raises when the byte count and the geometry
        disagree and so needs no separate length check.

        `verify()` stays on the encoded branch alone. It is there to
        reject a malformed or hostile *upload* before the rest of the
        pipeline touches it - and it invalidates the object it checked,
        which is why that branch opens the bytes twice. Raw pixels have
        no structure to be hostile in; `frombytes` is their check.
        """
        try:
            from PIL import Image

            if frame.image_format == "rgb":
                decoded = Image.frombytes(
                    "RGB", (frame.width, frame.height), frame.data
                )
            else:
                with Image.open(io.BytesIO(frame.data)) as candidate:
                    candidate.verify()
                decoded = Image.open(io.BytesIO(frame.data))

            with decoded as image:
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
