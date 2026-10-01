"""
Embedding providers.

The seam the semantic half of memory sits behind. One protocol, three
implementations, zero new dependencies:

    HashingEmbeddingProvider   LOCAL   deterministic n-gram feature
                                       hashing, stdlib only. Generalizes
                                       word order and morphology a
                                       little; it is NOT deep semantics,
                                       and this docstring is where that
                                       is said rather than hidden.
    GeminiEmbeddingProvider    REMOTE  Google Gemini embeddings
                                       (text-embedding-004).
    RemoteEmbeddingProvider    REMOTE  OpenAI-compatible /v1/embeddings.

Two rules shape everything here.

LOCAL vs REMOTE is a privacy boundary, not a detail. Sending memory
content to a remote endpoint is the one act in this package that moves
the owner's words off their machine, so a REMOTE provider refuses to
embed unless the configuration explicitly allows remote embedding. It
fails closed: the default is lexical-only, and a misconfiguration costs
the semantic half nothing but never exfiltrates anything.

Every vector carries its metadata (provider, model, dimensions,
version). Vectors from incompatible metadata are never compared - the
semantic index treats them as stale rather than silently mixing spaces,
because two floats-only arrays from different models share no meaning.
"""

import hashlib
import json
import math
import os
import struct
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from core.logger import logger


LOCAL = "local"
REMOTE = "remote"


class EmbeddingUnavailableError(RuntimeError):
    """
    The provider could not produce an embedding, for a named reason.

    Typed so the semantic retriever can fail closed on it (lexical
    retrieval continues) while diagnostics can say WHY: unreachable,
    timeout, malformed response, refused by policy, dimension mismatch.
    """


@dataclass(frozen=True)
class EmbeddingMetadata:
    """What identifies a vector space. Incompatible means not equal."""

    provider: str
    model: str
    dimensions: int
    version: str
    locality: str          # LOCAL or REMOTE

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "dimensions": self.dimensions,
            "version": self.version,
            "locality": self.locality,
        }

    def compatible_with(self, other: "EmbeddingMetadata") -> bool:
        return (
            self.provider == other.provider
            and self.model == other.model
            and self.dimensions == other.dimensions
            and self.version == other.version
        )


@runtime_checkable
class EmbeddingProvider(Protocol):

    def embed(self, text: str) -> list[float]:
        """One vector for one text. Raises EmbeddingUnavailableError."""
        ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Vectors in input order; batching is an efficiency hint."""
        ...

    def metadata(self) -> EmbeddingMetadata:
        ...

    def health_check(self) -> bool:
        """True when the provider can serve right now. Never raises."""
        ...

    @property
    def recommended_min_similarity(self) -> float:
        """
        The cosine floor below which this provider's scores are noise.

        It belongs to the PROVIDER, not to the retriever, because the
        distribution of cosine similarities is a property of the
        embedding space. Hashed n-grams collide, so arbitrary text
        scores well above zero against everything; a trained model
        separates related from unrelated far more cleanly. One shared
        constant would therefore be wrong for at least one provider.

        `memory.semantic.min_similarity` overrides it when set.
        """
        ...


# ----------------------------------------------------------------------
# Local, dependency-free: n-gram feature hashing
# ----------------------------------------------------------------------

HASH_DIMS = 256
NGRAM_SIZE = 3


class HashingEmbeddingProvider:
    """
    Deterministic feature hashing over character n-grams.

    Why it exists: the semantic machinery needs a provider that is
    always available, offline, dependency-free and reproducible - for
    tests, for the benchmark, and as the default local option on a
    machine with no model server. What it is honest about: hashed
    n-gram similarity generalizes token overlap (word order, shared
    substrings, morphology), it does not understand paraphrase the way
    a trained embedding model does. "I love Python" and "Python is my
    favourite language" share few trigrams and will NOT land close.
    That limitation is a property of this provider, not of the design;
    swapping in Ollama or a remote model changes the quality without
    touching anything else.
    """

    def __init__(self, dimensions: int = HASH_DIMS, ngram: int = NGRAM_SIZE):
        self._dims = max(16, int(dimensions))
        self._ngram = max(2, int(ngram))

    def metadata(self) -> EmbeddingMetadata:
        return EmbeddingMetadata(
            provider="hashing",
            model=f"ngram-{self._ngram}",
            dimensions=self._dims,
            version="1",
            locality=LOCAL,
        )

    def embed(self, text: str) -> list[float]:
        return self._hash_embed(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self._hash_embed(text) for text in texts]

    def health_check(self) -> bool:
        return True        # pure computation; it cannot be unhealthy

    @property
    def recommended_min_similarity(self) -> float:
        """
        0.24, MEASURED on the Phase 2 benchmark fixture, not chosen.

        `scripts/benchmark_semantic.py` sweeps this floor. On that
        fixture, moving it from 0.05 to 0.24 cut the memories returned
        for a query with no correct answer from 3.0 to 1.0 while recall
        stayed at 0.708 (lexical alone scores 0.583) and precision rose
        to 0.562 (lexical 0.521). At 0.26 recall collapses back to
        lexical's, so semantic stops earning its place.

        Honest limit: that fixture holds ten memories. This number is
        the knee of a small measured curve, not a universal constant -
        it is the reason the floor is configurable and the reason the
        sweep stayed in the benchmark rather than being deleted once a
        value was picked.
        """

        return 0.24

    def _hash_embed(self, text: str) -> list[float]:
        vector = [0.0] * self._dims

        normalized = " ".join(str(text or "").lower().split())

        if not normalized:
            return vector

        grams = {
            normalized[index:index + self._ngram]
            for index in range(max(1, len(normalized) - self._ngram + 1))
        }

        for gram in grams:
            digest = hashlib.blake2b(
                gram.encode("utf-8"), digest_size=8
            ).digest()
            (slot,) = struct.unpack("<Q", digest)
            vector[slot % self._dims] += 1.0

        norm = math.sqrt(sum(value * value for value in vector))

        if norm == 0.0:
            return vector

        return [value / norm for value in vector]


# ----------------------------------------------------------------------
# Remote: Google Gemini embeddings, behind the consent gate
# ----------------------------------------------------------------------

class GeminiEmbeddingProvider:
    """
    REMOTE embeddings via Google GenAI SDK.

    Every call checks the consent flag (`memory.semantic.allow_remote`).
    Uses Gemini's embedding models (default: text-embedding-004).
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "text-embedding-004",
        timeout: float = 10.0,
        allow_remote: bool = False,
    ):
        self._api_key = str(api_key or os.getenv("GEMINI_API_KEY") or "").strip()
        self._model = str(model or "text-embedding-004")
        self._timeout = float(timeout)
        self._allow_remote = bool(allow_remote)
        self._dimensions: int | None = None
        self._client = None

    def metadata(self) -> EmbeddingMetadata:
        return EmbeddingMetadata(
            provider="gemini",
            model=self._model,
            dimensions=self._dimensions or 0,
            version="1",
            locality=REMOTE,
        )

    def embed(self, text: str) -> list[float]:
        if not self._allow_remote:
            raise EmbeddingUnavailableError(
                "gemini embedding refused: memory.semantic.allow_remote "
                "is not enabled"
            )
        return self._request([str(text or "")])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not self._allow_remote:
            raise EmbeddingUnavailableError(
                "gemini embedding refused: memory.semantic.allow_remote "
                "is not enabled"
            )
        if not texts:
            return []
        return self._request([str(text or "") for text in texts])

    def health_check(self) -> bool:
        if not self._allow_remote or not self._api_key:
            return False
        try:
            self.embed("health check")
            return True
        except EmbeddingUnavailableError:
            return False

    @property
    def recommended_min_similarity(self) -> float:
        """
        0.05 - conservative default for trained embedding models.
        Overrides via memory.semantic.min_similarity in configuration.
        """
        return 0.05

    def _get_client(self):
        if self._client is None:
            if not self._api_key:
                raise EmbeddingUnavailableError("gemini embedding missing GEMINI_API_KEY")
            try:
                from google import genai
                self._client = genai.Client(api_key=self._api_key)
            except Exception as error:
                raise EmbeddingUnavailableError(
                    f"failed to initialize google genai client: {error}"
                ) from error
        return self._client

    def _request(self, texts: list[str]) -> list[list[float]]:
        client = self._get_client()
        started = time.monotonic()
        try:
            response = client.models.embed_content(
                model=self._model,
                contents=texts,
            )
        except Exception as error:
            status = getattr(error, "code", None) or getattr(error, "status_code", None)
            text_err = str(error).lower()
            if status == 429 or "resource_exhausted" in text_err or "quota" in text_err:
                raise EmbeddingUnavailableError(
                    f"gemini embedding quota/rate limit reached: {error}"
                ) from error
            raise EmbeddingUnavailableError(
                f"gemini embedding endpoint error: {error}"
            ) from error

        embeddings = getattr(response, "embeddings", None)
        if not embeddings or len(embeddings) != len(texts):
            raise EmbeddingUnavailableError(
                "gemini embedding response does not match the batch or is empty"
            )

        vectors: list[list[float]] = []
        for emb in embeddings:
            values = getattr(emb, "values", None)
            if not isinstance(values, list) or not values or not all(
                isinstance(v, (int, float)) for v in values
            ):
                raise EmbeddingUnavailableError(
                    "gemini embedding response contains a malformed vector"
                )
            vectors.append([float(v) for v in values])

        if self._dimensions is None and vectors:
            self._dimensions = len(vectors[0])

        logger.debug(
            "Gemini embedding ok (%d texts, %d dims, %.0f ms)",
            len(texts),
            self._dimensions or 0,
            (time.monotonic() - started) * 1000,
        )
        return vectors


# ----------------------------------------------------------------------
# Remote: OpenAI-compatible embeddings, behind the consent gate
# ----------------------------------------------------------------------

class RemoteEmbeddingProvider:
    """
    REMOTE embeddings: the exfiltration boundary made executable.

    Every call checks the consent flag. The check lives here rather
    than in the caller because a boundary enforced by the caller is
    only as strong as the next caller's memory of it.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 10.0,
        allow_remote: bool = False,
        provider_name: str = "remote",
    ):
        self._base_url = str(base_url or "").rstrip("/")
        self._api_key = str(api_key or "")
        self._model = str(model or "text-embedding-3-small")
        self._timeout = float(timeout)
        self._allow_remote = bool(allow_remote)
        self._provider_name = str(provider_name or "remote")
        self._dimensions: int | None = None

    def metadata(self) -> EmbeddingMetadata:
        return EmbeddingMetadata(
            provider=self._provider_name,
            model=self._model,
            dimensions=self._dimensions or 0,
            version="1",
            locality=REMOTE,
        )

    def embed(self, text: str) -> list[float]:
        if not self._allow_remote:
            # Fails closed, by name. The configuration, not the call
            # site, decides whether memory content may leave.
            raise EmbeddingUnavailableError(
                "remote embedding refused: memory.semantic.allow_remote "
                "is not enabled"
            )

        return self._request([str(text or "")])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not self._allow_remote:
            raise EmbeddingUnavailableError(
                "remote embedding refused: memory.semantic.allow_remote "
                "is not enabled"
            )

        return self._request([str(text or "") for text in texts])

    def health_check(self) -> bool:
        if not self._allow_remote:
            return False

        try:
            self.embed("health check")
            return True
        except EmbeddingUnavailableError:
            return False

    @property
    def recommended_min_similarity(self) -> float:
        """
        0.05 - deliberately conservative, and UNMEASURED for this
        provider.

        No benchmark of a real embedding model has been run in this
        repository, so there is no evidence here for any particular
        floor, and inventing one would silently discard real recall.
        This value keeps the pre-existing permissive behaviour; tune it
        per model by pointing `scripts/benchmark_semantic.py` at this
        provider and reading the sweep, then set
        `memory.semantic.min_similarity` from what the sweep shows.
        """

        return 0.05

    def _request(self, texts: list[str]) -> list[list[float]]:
        payload = json.dumps({
            "model": self._model,
            "input": texts,
        }).encode("utf-8")

        request = urllib.request.Request(
            f"{self._base_url}/embeddings",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as (
                response
            ):
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise EmbeddingUnavailableError(
                f"embedding endpoint unreachable: {type(error).__name__}"
            ) from error
        except (ValueError, json.JSONDecodeError) as error:
            raise EmbeddingUnavailableError(
                "embedding endpoint returned a malformed response"
            ) from error

        rows = body.get("data")

        if not isinstance(rows, list) or len(rows) != len(texts):
            raise EmbeddingUnavailableError(
                "embedding response does not match the batch"
            )

        vectors: list[list[float]] = []

        for row in rows:
            vector = row.get("embedding") if isinstance(row, dict) else None

            if not isinstance(vector, list) or not vector or not all(
                isinstance(value, (int, float)) for value in vector
            ):
                raise EmbeddingUnavailableError(
                    "embedding response contains a malformed vector"
                )

            vectors.append([float(value) for value in vector])

        if self._dimensions is None and vectors:
            self._dimensions = len(vectors[0])

        return vectors


# ----------------------------------------------------------------------
# Composition
# ----------------------------------------------------------------------

def build_embedding_provider(memory_config: dict | None):
    """
    The provider `memory.semantic` asks for, or None.

    None is the honest answer to "not configured", "disabled", or
    "configured wrong" - and None means the memory system runs exactly
    as it did before this module existed. Startup never fails because
    embeddings are unavailable; that is the whole shape of the deal.
    """

    config = (memory_config or {}).get("semantic") or {}

    if not bool(config.get("enabled", False)):
        return None

    name = str(config.get("provider", "hashing")).strip().lower()

    try:
        if name == "hashing":
            return HashingEmbeddingProvider()

        if name == "gemini":
            api_key = ""
            key_variable = str(config.get("api_key_env") or "GEMINI_API_KEY").strip()
            if key_variable:
                api_key = os.getenv(key_variable, "")
            if not api_key:
                api_key = os.getenv("GEMINI_API_KEY", "")

            return GeminiEmbeddingProvider(
                api_key=api_key,
                model=str(config.get("model") or "text-embedding-004"),
                timeout=float(config.get("timeout", 10.0)),
                allow_remote=bool(config.get("allow_remote", False)),
            )

        if name == "ollama":
            logger.warning(
                "memory.semantic.provider 'ollama' is deprecated in cloud-only architecture; "
                "please switch to 'gemini', 'remote', or 'hashing'. Defaulting to lexical only."
            )
            return None

        if name == "remote":
            api_key = ""
            key_variable = str(config.get("api_key_env") or "").strip()

            if key_variable:
                api_key = os.getenv(key_variable, "")

            return RemoteEmbeddingProvider(
                base_url=str(config.get("base_url") or ""),
                api_key=api_key,
                model=str(config.get("model") or "text-embedding-3-small"),
                timeout=float(config.get("timeout", 10.0)),
                allow_remote=bool(config.get("allow_remote", False)),
            )

        logger.warning(
            "memory.semantic.provider %r is unknown - semantic recall "
            "stays off (known: hashing, gemini, remote)",
            name,
        )
    except (ValueError, TypeError) as error:
        logger.warning(
            "memory.semantic is misconfigured (%s) - semantic recall "
            "stays off rather than failing startup",
            error,
        )

    return None


