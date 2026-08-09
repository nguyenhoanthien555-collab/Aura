"""
Ollama Local LLM Provider.
"""

import json
from typing import Iterator
import urllib.error
import urllib.request

from brain.providers.base import BaseProvider
from core.config import load_config
from core.logger import logger


class OllamaProvider(BaseProvider):
    """
    Local LLM provider using Ollama's HTTP API.

    Default endpoint: http://127.0.0.1:11434
    Default model: qwen3:8b
    """

    def __init__(
        self,
        host: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ):
        config = load_config()
        llm_cfg = config.get("llm") or {}

        configured_model = llm_cfg.get("model")
        if not model:
            if configured_model and not configured_model.startswith("gemini"):
                model = configured_model
            else:
                model = "qwen3:8b"

        self.host = (host or llm_cfg.get("host") or "http://127.0.0.1:11434").rstrip("/")
        self.model = model
        self.timeout = timeout if timeout is not None else float(llm_cfg.get("timeout", 60.0))

    def generate(self, prompt: str) -> str:
        """
        Generate a response from Ollama via POST /api/generate.
        """
        url = f"{self.host}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
        }

        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
                res_json = json.loads(body)
                return res_json.get("response", "") or ""

        except urllib.error.URLError as error:
            logger.warning("Ollama API request failed: %s", error)
            raise RuntimeError(f"Ollama provider failed: {error}") from error
        except Exception as error:
            logger.warning("Ollama generation failed: %s", error)
            raise RuntimeError(f"Ollama provider failed: {error}") from error

    def stream(self, prompt: str) -> Iterator[str]:
        """
        Stream a response from Ollama line-by-line via POST /api/generate.
        """
        url = f"{self.host}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": True,
        }

        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                for line in response:
                    line_str = line.decode("utf-8").strip()
                    if not line_str:
                        continue
                    try:
                        chunk = json.loads(line_str)
                    except json.JSONDecodeError:
                        continue

                    piece = chunk.get("response", "")
                    if piece:
                        yield piece

                    if chunk.get("done", False):
                        break

        except urllib.error.URLError as error:
            logger.warning("Ollama stream request failed: %s", error)
            raise RuntimeError(f"Ollama streaming failed: {error}") from error
        except Exception as error:
            logger.warning("Ollama streaming failed: %s", error)
            raise RuntimeError(f"Ollama streaming failed: {error}") from error
