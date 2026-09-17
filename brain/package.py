"""
Brain packaging, versioning, manifests, and promotion/rollback lifecycle for AURA.

Enforces durable states:
    CANDIDATE -> VALIDATING -> ACTIVE -> DISABLED / REJECTED / ROLLED_BACK

Every Brain package carries a cryptographically verifiable manifest with training lineage,
parameter metadata, hardware bounds, and runtime backend specifications.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
import hashlib
import json
import os
import shutil
import tempfile
import threading
from typing import Any, Dict, List, Optional

from core.logger import logger


class BrainStatus(str, Enum):
    CANDIDATE = "CANDIDATE"
    VALIDATING = "VALIDATING"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    REJECTED = "REJECTED"
    ROLLED_BACK = "ROLLED_BACK"


def timestamp_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class BrainManifest:
    """Authoritative manifest describing one versioned Brain package."""

    brain_id: str
    version: str
    model_format: str  # "gguf", "safetensors", "deterministic", "openai_compatible"
    context_length: int = 4096
    parameter_count: str = "7B"
    quantization: str = "Q4_K_M"
    runtime_backend: str = "cpu"
    checksum: str = ""
    status: str = BrainStatus.CANDIDATE.value
    parent_brain: Optional[str] = None
    training_lineage: Dict[str, Any] = field(default_factory=dict)
    dataset_lineage: List[str] = field(default_factory=list)
    capabilities: List[str] = field(
        default_factory=lambda: ["function_calling", "streaming", "reasoning", "clarification"]
    )
    compatibility_requirements: Dict[str, Any] = field(
        default_factory=lambda: {"min_ram_mb": 4096, "min_vram_mb": 0, "platform": "any"}
    )
    created_at: str = field(default_factory=timestamp_now)
    updated_at: str = field(default_factory=timestamp_now)
    evaluation_result: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "BrainManifest":
        valid_fields = set(cls.__dataclass_fields__.keys())
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)

    def calculate_checksum(self, content_bytes: bytes) -> str:
        """Computes SHA-256 checksum."""
        digest = hashlib.sha256(content_bytes).hexdigest()
        self.checksum = digest
        return digest


class BrainPackage:
    """Encapsulates a Brain artifact directory containing model weights and manifest."""

    def __init__(self, package_dir: str, manifest: BrainManifest):
        self.package_dir = package_dir
        self.manifest = manifest

    @property
    def brain_id(self) -> str:
        return self.manifest.brain_id

    @property
    def version(self) -> str:
        return self.manifest.version

    @property
    def status(self) -> str:
        return self.manifest.status

    def save_manifest(self) -> None:
        os.makedirs(self.package_dir, exist_ok=True)
        manifest_path = os.path.join(self.package_dir, "manifest.json")
        self.manifest.updated_at = timestamp_now()
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(self.manifest.to_dict(), f, indent=2)

    def verify_checksum(self, weight_file: str = "model.bin") -> bool:
        """Verifies weight file SHA-256 against manifest checksum if checksum is set."""
        if not self.manifest.checksum:
            return True
        candidates = [
            weight_file,
            self.manifest.metadata.get("weight_file", ""),
            "adapter/adapter_model.safetensors",
            "model.gguf",
            "model.bin",
        ]
        target_path = None
        for c in candidates:
            if c:
                p = os.path.join(self.package_dir, c)
                if os.path.exists(p):
                    target_path = p
                    break

        if not target_path:
            # For deterministic / test formats, allow dummy or direct verification
            if self.manifest.model_format in ("deterministic", "openai_compatible"):
                return True
            return False

        h = hashlib.sha256()
        with open(target_path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest() == self.manifest.checksum



class BrainManager:
    """
    Manages discovery, registration, lifecycle transitions, promotion,
    and rollback of Brain packages across disk storage.
    """

    def __init__(self, brains_dir: str = "brains"):
        self.brains_dir = os.path.abspath(brains_dir)
        self._lock = threading.RLock()
        os.makedirs(self.brains_dir, exist_ok=True)
        self.state_file = os.path.join(self.brains_dir, "brain_state.json")
        self._load_state()

    def _load_state(self) -> None:
        with self._lock:
            if os.path.exists(self.state_file):
                try:
                    with open(self.state_file, "r", encoding="utf-8") as f:
                        self.state = json.load(f)
                except Exception as e:
                    logger.warning("Could not read brain state, resetting: %s", e)
                    self.state = {"active_brain_id": None, "previous_brain_id": None, "history": []}
            else:
                self.state = {"active_brain_id": None, "previous_brain_id": None, "history": []}

    def _save_state(self) -> None:
        dir_name = os.path.dirname(self.state_file)
        os.makedirs(dir_name, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix="brain_state_", suffix=".tmp")
        try:
            with open(fd, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            shutil.move(tmp_path, self.state_file)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

    def discover_packages(self) -> Dict[str, BrainPackage]:
        """Discovers all valid Brain packages in brains_dir."""
        packages = {}
        if not os.path.exists(self.brains_dir):
            return packages

        for entry in os.listdir(self.brains_dir):
            subdir = os.path.join(self.brains_dir, entry)
            if not os.path.isdir(subdir):
                continue
            manifest_file = os.path.join(subdir, "manifest.json")
            if os.path.exists(manifest_file):
                try:
                    with open(manifest_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    manifest = BrainManifest.from_dict(data)
                    packages[manifest.brain_id] = BrainPackage(subdir, manifest)
                except Exception as e:
                    logger.warning("Failed to load brain manifest from %s: %s", manifest_file, e)

        return packages

    def get_package(self, brain_id: str) -> Optional[BrainPackage]:
        packages = self.discover_packages()
        return packages.get(brain_id)

    def get_active_package(self) -> Optional[BrainPackage]:
        active_id = self.state.get("active_brain_id")
        if not active_id:
            # Fall back to any package marked ACTIVE
            for pkg in self.discover_packages().values():
                if pkg.manifest.status == BrainStatus.ACTIVE.value:
                    return pkg
            return None
        return self.get_package(active_id)

    def register_package(self, manifest: BrainManifest, weight_content: Optional[bytes] = None) -> BrainPackage:
        """Registers a new Brain package as CANDIDATE."""
        with self._lock:
            pkg_dir = os.path.join(self.brains_dir, manifest.brain_id)
            os.makedirs(pkg_dir, exist_ok=True)
            if weight_content is not None:
                manifest.calculate_checksum(weight_content)
                weight_path = os.path.join(pkg_dir, "model.bin")
                with open(weight_path, "wb") as f:
                    f.write(weight_content)

            manifest.status = BrainStatus.CANDIDATE.value
            pkg = BrainPackage(pkg_dir, manifest)
            pkg.save_manifest()
            logger.info("Registered brain package %s (v%s) as CANDIDATE", manifest.brain_id, manifest.version)
            return pkg

    def validate_package(self, brain_id: str) -> bool:
        """Validates package checksum, files, and format."""
        with self._lock:
            pkg = self.get_package(brain_id)
            if not pkg:
                logger.error("Brain package %s not found for validation", brain_id)
                return False

            pkg.manifest.status = BrainStatus.VALIDATING.value
            pkg.save_manifest()

            # Check checksum
            if not pkg.verify_checksum():
                logger.error("Brain package %s checksum verification failed", brain_id)
                pkg.manifest.status = BrainStatus.REJECTED.value
                pkg.manifest.metadata["rejection_reason"] = "checksum_mismatch"
                pkg.save_manifest()
                return False

            # Check required manifest fields
            if not pkg.manifest.context_length or pkg.manifest.context_length <= 0:
                pkg.manifest.status = BrainStatus.REJECTED.value
                pkg.manifest.metadata["rejection_reason"] = "invalid_context_length"
                pkg.save_manifest()
                return False

            pkg.save_manifest()
            return True

    def promote_candidate(self, candidate_id: str, evaluation_summary: Optional[dict] = None) -> bool:
        """
        Atomically promotes CANDIDATE to ACTIVE.
        Demotes previously active brain to ROLLBACK target.
        """
        with self._lock:
            packages = self.discover_packages()
            candidate = packages.get(candidate_id)
            if not candidate:
                logger.error("Candidate %s not found for promotion", candidate_id)
                return False

            if not self.validate_package(candidate_id):
                logger.error("Candidate %s failed validation before promotion", candidate_id)
                return False

            current_active_id = self.state.get("active_brain_id")
            if current_active_id and current_active_id in packages:
                prev_pkg = packages[current_active_id]
                prev_pkg.manifest.status = BrainStatus.DISABLED.value
                prev_pkg.save_manifest()
                self.state["previous_brain_id"] = current_active_id

            candidate.manifest.status = BrainStatus.ACTIVE.value
            if evaluation_summary:
                candidate.manifest.evaluation_result = evaluation_summary
            candidate.save_manifest()

            self.state["active_brain_id"] = candidate_id
            self.state["history"].append(
                {
                    "action": "PROMOTE",
                    "brain_id": candidate_id,
                    "previous_brain_id": current_active_id,
                    "timestamp": timestamp_now(),
                    "eval": evaluation_summary,
                }
            )
            self._save_state()
            logger.info("Successfully promoted Brain %s to ACTIVE (previous: %s)", candidate_id, current_active_id)
            return True

    def rollback(self, reason: str = "manual_rollback") -> bool:
        """
        Rolls back ACTIVE brain to previous known-good Brain.
        Marks rolled back brain as ROLLED_BACK.
        """
        with self._lock:
            packages = self.discover_packages()
            current_active_id = self.state.get("active_brain_id")
            previous_id = self.state.get("previous_brain_id")

            if not previous_id or previous_id not in packages:
                logger.error("No valid previous brain found for rollback (target was: %s)", previous_id)
                return False

            if current_active_id and current_active_id in packages:
                curr_pkg = packages[current_active_id]
                curr_pkg.manifest.status = BrainStatus.ROLLED_BACK.value
                curr_pkg.manifest.metadata["rollback_reason"] = reason
                curr_pkg.save_manifest()

            prev_pkg = packages[previous_id]
            prev_pkg.manifest.status = BrainStatus.ACTIVE.value
            prev_pkg.save_manifest()

            self.state["active_brain_id"] = previous_id
            self.state["previous_brain_id"] = None
            self.state["history"].append(
                {
                    "action": "ROLLBACK",
                    "from_brain_id": current_active_id,
                    "to_brain_id": previous_id,
                    "reason": reason,
                    "timestamp": timestamp_now(),
                }
            )
            self._save_state()
            logger.info("Successfully rolled back Brain to %s (demoted: %s)", previous_id, current_active_id)
            return True

    def reject_candidate(self, candidate_id: str, reason: str) -> bool:
        """Rejects a candidate Brain, preventing promotion."""
        with self._lock:
            pkg = self.get_package(candidate_id)
            if not pkg:
                return False

            pkg.manifest.status = BrainStatus.REJECTED.value
            pkg.manifest.metadata["rejection_reason"] = reason
            pkg.save_manifest()

            self.state["history"].append(
                {
                    "action": "REJECT",
                    "brain_id": candidate_id,
                    "reason": reason,
                    "timestamp": timestamp_now(),
                }
            )
            self._save_state()
            logger.info("Rejected Brain candidate %s: %s", candidate_id, reason)
            return True
