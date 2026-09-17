"""
AURA Parameter Delta Tracker.

Computes exact tensor-by-tensor mathematical and cryptographic parameter deltas
before and after neural training / LoRA adaptation, and between base and merged models.
Produces machine-readable evidence proving that parameters genuinely changed on GPU.
"""

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
from typing import Any, Dict, List, Optional
import torch


def tensor_sha256(tensor: torch.Tensor) -> str:
    """Computes SHA-256 hash of a tensor's raw binary buffer in CPU memory."""
    t_cpu = tensor.detach().cpu().contiguous()
    return hashlib.sha256(t_cpu.numpy().tobytes()).hexdigest()


@dataclass
class TensorDelta:
    name: str
    shape: List[int]
    numel: int
    has_changed: bool
    max_abs_delta: float
    mean_abs_delta: float
    l2_norm_delta: float
    sha256_before: str
    sha256_after: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ParameterDeltaReport:
    total_tensors_tracked: int
    changed_tensors_count: int
    total_parameters_tracked: int
    changed_parameters_count: int
    changed_parameters_pct: float
    max_abs_delta: float
    mean_abs_delta: float
    l2_norm_delta: float
    aggregate_fingerprint_before: str
    aggregate_fingerprint_after: str
    tensor_deltas: List[TensorDelta] = field(default_factory=list)
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["tensor_deltas"] = [td if isinstance(td, dict) else td.to_dict() for td in self.tensor_deltas]
        return d

    def save(self, filepath: str) -> None:
        import os
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)


class ParameterTracker:
    """Captures parameter snapshots and calculates mathematical and cryptographic deltas."""

    @staticmethod
    def capture_snapshot(model: torch.nn.Module, only_trainable: bool = False) -> Dict[str, torch.Tensor]:
        """Captures a detached CPU clone snapshot of model parameters."""
        snapshot = {}
        for name, param in model.named_parameters():
            if only_trainable and not param.requires_grad:
                continue
            snapshot[name] = param.detach().cpu().clone()
        return snapshot

    @staticmethod
    def compute_fingerprint(snapshot: Dict[str, torch.Tensor]) -> str:
        """Computes aggregate SHA-256 fingerprint across all tensors in snapshot."""
        h = hashlib.sha256()
        for name in sorted(snapshot.keys()):
            t = snapshot[name]
            h.update(f"{name}:{list(t.shape)}:{tensor_sha256(t)}\n".encode("utf-8"))
        return h.hexdigest()

    @classmethod
    def compute_delta(
        cls,
        snapshot_before: Dict[str, torch.Tensor],
        snapshot_after: Dict[str, torch.Tensor],
    ) -> ParameterDeltaReport:
        """Compares before and after snapshots and computes exact mathematical deltas."""
        from datetime import datetime

        tensor_deltas: List[TensorDelta] = []
        total_tensors = len(snapshot_before)
        changed_tensors = 0
        total_params = 0
        changed_params = 0
        max_abs = 0.0
        sum_abs = 0.0
        sum_sq = 0.0

        fp_before = cls.compute_fingerprint(snapshot_before)
        fp_after = cls.compute_fingerprint(snapshot_after)

        for name, t_before in snapshot_before.items():
            if name not in snapshot_after:
                continue
            t_after = snapshot_after[name]
            numel = t_before.numel()
            total_params += numel

            sha_b = tensor_sha256(t_before)
            sha_a = tensor_sha256(t_after)

            diff = (t_after.float() - t_before.float()).abs()
            t_max = float(diff.max().item())
            t_mean = float(diff.mean().item())
            t_l2 = float(torch.sqrt((diff ** 2).sum()).item())

            has_changed = (sha_b != sha_a) or (t_max > 1e-7)
            if has_changed:
                changed_tensors += 1
                changed_params += numel
                if t_max > max_abs:
                    max_abs = t_max
                sum_abs += float(diff.sum().item())
                sum_sq += float((diff ** 2).sum().item())

            tensor_deltas.append(
                TensorDelta(
                    name=name,
                    shape=list(t_before.shape),
                    numel=numel,
                    has_changed=has_changed,
                    max_abs_delta=round(t_max, 8),
                    mean_abs_delta=round(t_mean, 8),
                    l2_norm_delta=round(t_l2, 8),
                    sha256_before=sha_b,
                    sha256_after=sha_a,
                )
            )

        mean_abs = (sum_abs / total_params) if total_params > 0 else 0.0
        overall_l2 = math.sqrt(sum_sq)
        changed_pct = (100.0 * changed_params / total_params) if total_params > 0 else 0.0

        return ParameterDeltaReport(
            total_tensors_tracked=total_tensors,
            changed_tensors_count=changed_tensors,
            total_parameters_tracked=total_params,
            changed_parameters_count=changed_params,
            changed_parameters_pct=round(changed_pct, 4),
            max_abs_delta=round(max_abs, 8),
            mean_abs_delta=round(mean_abs, 8),
            l2_norm_delta=round(overall_l2, 8),
            aggregate_fingerprint_before=fp_before,
            aggregate_fingerprint_after=fp_after,
            tensor_deltas=tensor_deltas,
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
