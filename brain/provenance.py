"""
Brain provenance and attribution for AURA.

Ensures every model turn, tool decision, and task execution carries explicit provenance:
    provider: "local_aura" | "cloud"
    brain_id: e.g. "aura-brain-v1"
    version: e.g. "1.0.0"
    model_digest: SHA-256 hash or model tag
    backend: "cpu" | "cuda" | "deterministic"
"""

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class BrainProvenance:
    provider: str
    brain_id: str
    version: str
    model_digest: str
    backend: str = "cpu"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BrainProvenance":
        return cls(
            provider=data.get("provider", "unknown"),
            brain_id=data.get("brain_id", "default"),
            version=data.get("version", "1.0.0"),
            model_digest=data.get("model_digest", ""),
            backend=data.get("backend", "cpu"),
        )


def extract_provenance(source: Any) -> BrainProvenance:
    """Extracts provenance metadata from an LLM provider or runtime object."""
    if hasattr(source, "get_provenance"):
        prov = source.get_provenance()
        if isinstance(prov, BrainProvenance):
            return prov
        if isinstance(prov, dict):
            return BrainProvenance.from_dict(prov)

    provider_name = getattr(source, "provider_name", type(source).__name__)
    brain_id = getattr(source, "brain_id", "aura-default")
    version = getattr(source, "version", "1.0.0")
    model_digest = getattr(source, "model_digest", "")
    backend = getattr(source, "backend", "cpu")

    return BrainProvenance(
        provider=provider_name,
        brain_id=brain_id,
        version=str(version),
        model_digest=model_digest,
        backend=backend,
    )
