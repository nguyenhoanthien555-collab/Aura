"""
Ollama vision processor.

Sends the captured frame to a local vision model - qwen2.5-VL by default
- and returns what is actually visible on the screen.

Three deliberate choices:

- urllib, not requests. brain/providers/ollama.py already talks to the
  same daemon with the standard library, and vision is not a reason to
  add an HTTP dependency to a project that has none.

- Pillow imported lazily, inside the encoder. Frames arrive as raw RGB
  and Ollama wants a base64 image, so an encoder is genuinely required -
  but only when a frame is actually being described. Importing
  vision.capture must never require it.

- Silent on failure, loud in the log. A vision model that is missing,
  busy or slow yields "" so the turn continues without a vision
  section, but the reason is logged at warning level rather than
  swallowed.
"""

import base64
import io
import json
import urllib.error
import urllib.request
from pathlib import Path

from core.logger import logger

from vision.capture import Frame


DEFAULT_MODEL = "qwen2.5vl:7b"
DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_TIMEOUT = 120.0

MAX_DESCRIPTION = 600


PROMPT = (
    "Analyze this screenshot carefully. Describe only what is visibly "
    "present in the image. Do not guess, do not infer from anything "
    "outside the image, and do not invent applications, windows, files "
    "or text that you cannot actually see. Name the main visible "
    "application, the important UI elements or readable text, and the "
    "apparent activity only where the image supports it. If something "
    "is unclear or unreadable, say so instead of filling it in. Answer "
    "in two or three sentences."
)


class OllamaVisionProcessor:
    """
    Describes a frame with a local Ollama vision model.

    Satisfies vision.processor.VisionProcessor, so VisionManager accepts
    it wherever WindowTitleProcessor fits.

    `window_title` is accepted and deliberately not sent to the model.
    The whole point of this processor is that the description comes from
    pixels; handing the model the window title invites it to describe
    the title instead of the screen.
    """

    # Where the pixels can end up, advertised so a caller can price the
    # act rather than guess at it. Read with getattr and a False default
    # at every use site, so this is a fact a processor may offer and not
    # a member the VisionProcessor protocol demands.
    # Loopback by default and a host the owner typed otherwise. Either
    # way this is the owner's own daemon, not a third party, so looking
    # through it stays the same act as looking.
    sends_pixels_offsite = False

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        host: str = DEFAULT_HOST,
        timeout: float = DEFAULT_TIMEOUT,
        debug_path: str | Path | None = None,
    ):
        self.model = model
        self.host = (host or DEFAULT_HOST).rstrip("/")
        self.timeout = float(timeout)

        # When set, every frame sent to the model is also written here as
        # a PNG - the exact bytes that were base64 encoded, not a second
        # screenshot taken afterwards.
        self.debug_path = Path(debug_path) if debug_path else None

    # ------------------------------------------------------------------
    # Port: VisionProcessor
    # ------------------------------------------------------------------

    def describe(self, frame: Frame | None, window_title: str = "") -> str:

        if frame is None or frame.is_empty():
            logger.debug("Vision: no frame to describe")
            return ""

        try:
            png = self._to_png(frame)

        except Exception as error:
            logger.warning("Vision: could not encode frame (%s)", error)
            return ""

        logger.debug(
            "Vision: %dx%d %s frame -> %d byte PNG, model=%s",
            frame.width,
            frame.height,
            frame.image_format,
            len(png),
            self.model,
        )

        self._save_debug(png)

        image = base64.b64encode(png).decode()

        description = self._ask(image)

        if not description:
            return ""

        return description.strip()[:MAX_DESCRIPTION]

    # ------------------------------------------------------------------
    # Encoding
    # ------------------------------------------------------------------

    def _to_png(self, frame: Frame) -> bytes:
        """
        The frame as PNG bytes.

        Raw RGB is what ScreenshotCapture produces; anything else is
        assumed to already be an encoded image and is round tripped so
        the model always receives one known format.
        """

        from PIL import Image           # noqa: PLC0415  (optional dependency)

        if frame.image_format == "rgb":
            image = Image.frombytes(
                "RGB",
                (frame.width, frame.height),
                frame.data,
            )

        else:
            image = Image.open(io.BytesIO(frame.data)).convert("RGB")

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")

        return buffer.getvalue()

    def _save_debug(self, png: bytes) -> None:

        if self.debug_path is None:
            return

        try:
            self.debug_path.parent.mkdir(parents=True, exist_ok=True)
            self.debug_path.write_bytes(png)

            logger.info("Vision: frame sent to model saved to %s", self.debug_path)

        except OSError as error:
            logger.debug("Vision: could not write debug frame (%s)", error)

    # ------------------------------------------------------------------
    # Ollama
    # ------------------------------------------------------------------

    def _ask(self, image: str) -> str:
        """
        POST /api/chat with one image, non streaming.

        Returns "" for every failure mode: unreachable daemon, HTTP
        error, malformed body, missing model, empty answer.
        """

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": PROMPT,
                    "images": [image],
                }
            ],
            "stream": False,
        }

        request = urllib.request.Request(
            f"{self.host}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", errors="replace")

        except urllib.error.HTTPError as error:
            logger.warning(
                "Vision: Ollama returned HTTP %s (%s)",
                error.code,
                self._error_detail(error),
            )
            return ""

        except urllib.error.URLError as error:
            logger.warning(
                "Vision: Ollama unreachable at %s (%s)",
                self.host,
                error.reason,
            )
            return ""

        except Exception as error:
            logger.warning("Vision: Ollama request failed (%s)", error)
            return ""

        return self._content(body)

    @staticmethod
    def _content(body: str) -> str:
        """Pull message.content out of a response, defensively."""

        try:
            data = json.loads(body)

        except json.JSONDecodeError:
            logger.warning("Vision: Ollama returned a non JSON body")
            return ""

        if not isinstance(data, dict):
            logger.warning("Vision: unexpected Ollama response shape")
            return ""

        if data.get("error"):
            logger.warning("Vision: Ollama error: %s", data["error"])
            return ""

        message = data.get("message")

        if not isinstance(message, dict):
            logger.warning("Vision: Ollama response had no message")
            return ""

        content = message.get("content") or ""

        if not isinstance(content, str) or not content.strip():
            logger.debug("Vision: model returned an empty description")
            return ""

        return content

    @staticmethod
    def _error_detail(error: urllib.error.HTTPError) -> str:

        try:
            return error.read().decode("utf-8", errors="replace")[:200]

        except Exception:
            return error.reason or ""
