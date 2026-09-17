"""
AURA Model Registry & Version Management.

Maintains the authoritative, immutable catalog of all independently versioned AURA models:
AURA v1, AURA v2, AURA v3...
Tracks active production model, rollback targets, candidates, rejected models,
artifact locations, cryptographic hashes, training lineage, and evaluation metrics.
Uses atomic file writes and re-entrant locking to guarantee corruption-free persistence.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
import os
import shutil
import tempfile
import threading
from typing import Any, Dict, List, Optional

from core.logger import logger


@dataclass
class AuraModelVersion:
    aura_version: str  # "v1", "v2", etc.
    parent_version: Optional[str]  # "v1" or None
    foundation_model: str  # "Qwen/Qwen2.5-0.5B-Instruct"
    foundation_artifact_hash: str
    dataset_version: str
    dataset_hash: str
    training_run_id: str
    adapter_hash: str
    merged_checkpoint_hash: str
    gguf_hash: str
    artifact_paths: Dict[str, str] = field(default_factory=dict)
    evaluation: Dict[str, Any] = field(default_factory=dict)
    parameter_deltas: Dict[str, Any] = field(default_factory=dict)
    promotion_status: str = "ACTIVE"  # ACTIVE, CANDIDATE, REJECTED, ROLLED_BACK
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    promoted_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AuraModelVersion":
        return cls(**d)


class AuraModelRegistry:
    """Persistent, thread-safe model version registry."""

    def __init__(self, registry_file: str = "brains/model_registry.json"):
        self.registry_file = os.path.abspath(registry_file)
        self._lock = threading.RLock()
        self.active_model: Optional[str] = None
        self.rollback_model: Optional[str] = None
        self.models: Dict[str, AuraModelVersion] = {}
        self.candidates: Dict[str, AuraModelVersion] = {}
        self.history: List[Dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        with self._lock:
            if os.path.exists(self.registry_file):
                try:
                    with open(self.registry_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self.active_model = data.get("active_model")
                    self.rollback_model = data.get("rollback_model")
                    self.history = data.get("history", [])

                    self.models = {}
                    for ver_id, m_dict in data.get("models", {}).items():
                        self.models[ver_id] = AuraModelVersion.from_dict(m_dict)

                    self.candidates = {}
                    for cid, c_dict in data.get("candidates", {}).items():
                        self.candidates[cid] = AuraModelVersion.from_dict(c_dict)

                except Exception as e:
                    logger.error("Failed to parse registry file %s: %s", self.registry_file, e)
            else:
                os.makedirs(os.path.dirname(self.registry_file), exist_ok=True)
                self._save()

    def _save(self) -> None:
        """Atomic write using temporary file replacement."""
        with self._lock:
            data = {
                "active_model": self.active_model,
                "rollback_model": self.rollback_model,
                "models": {k: v.to_dict() for k, v in self.models.items()},
                "candidates": {k: v.to_dict() for k, v in self.candidates.items()},
                "history": self.history,
                "last_updated": datetime.now().isoformat(timespec="seconds"),
            }
            dir_name = os.path.dirname(self.registry_file)
            os.makedirs(dir_name, exist_ok=True)

            fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix="reg_", suffix=".tmp")
            try:
                with open(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                shutil.move(tmp_path, self.registry_file)
            except Exception:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                raise

    def register_candidate(self, candidate: AuraModelVersion) -> None:
        """Registers a new training candidate in isolated state."""
        with self._lock:
            candidate.promotion_status = "CANDIDATE"
            self.candidates[candidate.aura_version] = candidate
            self.history.append({
                "action": "REGISTER_CANDIDATE",
                "version": candidate.aura_version,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            })
            self._save()
            logger.info("Registered candidate %s in model registry", candidate.aura_version)

    def promote_candidate(self, candidate_version: str, evaluation: Dict[str, Any]) -> bool:
        """Promotes a candidate model to ACTIVE production, setting prior model as rollback target."""
        with self._lock:
            cand = self.candidates.get(candidate_version) or self.models.get(candidate_version)
            if not cand:
                logger.error("Candidate %s not found for promotion", candidate_version)
                return False

            now = datetime.now().isoformat(timespec="seconds")
            prev_active = self.active_model

            cand.promotion_status = "ACTIVE"
            cand.promoted_at = now
            cand.evaluation = evaluation

            # Move from candidates to models
            if candidate_version in self.candidates:
                del self.candidates[candidate_version]
            self.models[candidate_version] = cand

            # Update pointers
            if prev_active and prev_active in self.models:
                self.models[prev_active].promotion_status = "ROLLED_BACK_TARGET"
                self.rollback_model = prev_active

            self.active_model = candidate_version
            self.history.append({
                "action": "PROMOTE",
                "version": candidate_version,
                "previous_version": prev_active,
                "timestamp": now,
                "eval_score": evaluation.get("score") or evaluation.get("overall_score"),
            })
            self._save()
            logger.info("Successfully promoted %s to ACTIVE (previous: %s)", candidate_version, prev_active)
            return True

    def reject_candidate(self, candidate_version: str, reason: str, evaluation: Dict[str, Any]) -> None:
        """Marks candidate as REJECTED without modifying active production model."""
        with self._lock:
            cand = self.candidates.get(candidate_version)
            if cand:
                cand.promotion_status = "REJECTED"
                cand.evaluation = evaluation
                cand.metadata["rejection_reason"] = reason
                self.history.append({
                    "action": "REJECT",
                    "version": candidate_version,
                    "reason": reason,
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                })
                self._save()
                logger.info("Rejected candidate %s: %s", candidate_version, reason)

    def rollback(self, reason: str = "manual_rollback") -> bool:
        """Rolls back production model pointer to previous known-good version."""
        with self._lock:
            if not self.rollback_model or self.rollback_model not in self.models:
                logger.error("No valid rollback model available (target: %s)", self.rollback_model)
                return False

            curr_active = self.active_model
            target = self.rollback_model

            if curr_active and curr_active in self.models:
                self.models[curr_active].promotion_status = "ROLLED_BACK"
                self.models[curr_active].metadata["rollback_reason"] = reason

            self.models[target].promotion_status = "ACTIVE"
            self.active_model = target
            self.rollback_model = None

            self.history.append({
                "action": "ROLLBACK",
                "from_version": curr_active,
                "to_version": target,
                "reason": reason,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            })
            self._save()
            logger.info("Successfully rolled back production model to %s", target)
            return True

    def get_active_model(self) -> Optional[AuraModelVersion]:
        with self._lock:
            if self.active_model and self.active_model in self.models:
                return self.models[self.active_model]
            return None

    def get_candidate(self, version: str) -> Optional[AuraModelVersion]:
        with self._lock:
            return self.candidates.get(version)

    def get_model(self, version: str) -> Optional[AuraModelVersion]:
        with self._lock:
            return self.models.get(version)
