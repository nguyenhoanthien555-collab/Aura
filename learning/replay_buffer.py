# -*- coding: utf-8 -*-
"""
AURA Continual Learning Replay Buffer & Longitudinal Drift Protection.

Defends against catastrophic forgetting and distribution drift during
continual self-learning cycles through:
1. Balanced Replay Quotas (Stable Core anchor, verified operational successes, user corrections).
2. Priority / Quality-weighted eviction on buffer overflow.
3. Longitudinal Drift Tracker monitoring hard-gate stability across training cycles.
"""

from dataclasses import asdict, dataclass, field
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from core.logger import logger


@dataclass
class ReplayItem:
    item_id: str
    category: str  # "core", "success", "correction"
    data: Dict[str, Any]
    quality_score: float = 1.0
    added_at: float = field(default_factory=time.time)
    provenance: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ReplayBuffer:
    """
    Fixed-capacity memory buffer enforcing category quotas and anti-drift sampling.
    Default quotas:
      - core: 25% (Immutable Stable Core foundation)
      - success: 50% (Verified operational executions)
      - correction: 25% (Explicit user corrections & safety clarifications)
    """

    DEFAULT_QUOTAS = {
        "core": 0.25,
        "success": 0.50,
        "correction": 0.25,
    }

    def __init__(
        self,
        capacity: int = 1000,
        quotas: Optional[Dict[str, float]] = None,
        storage_path: Optional[str] = None,
    ):
        self.capacity = capacity
        self.quotas = quotas or self.DEFAULT_QUOTAS
        self.storage_path = storage_path
        self._buffers: Dict[str, List[ReplayItem]] = {
            "core": [],
            "success": [],
            "correction": [],
        }

    def add(self, item: ReplayItem) -> bool:
        cat = item.category
        if cat not in self._buffers:
            cat = "success"
            item.category = cat

        # Enforce category capacity
        cat_max = int(self.capacity * self.quotas.get(cat, 0.33))
        buf = self._buffers[cat]

        # Deduplicate
        for existing in buf:
            if existing.item_id == item.item_id:
                existing.quality_score = max(existing.quality_score, item.quality_score)
                return False

        if len(buf) >= cat_max:
            # Evict lowest quality, then oldest
            buf.sort(key=lambda x: (x.quality_score, x.added_at))
            evicted = buf.pop(0)
            logger.debug("[ReplayBuffer] Evicted item %s from %s", evicted.item_id, cat)

        buf.append(item)
        return True

    def load_stable_core(self) -> int:
        """Injects Stable Core curriculum into the core replay partition."""
        from learning.curriculum import get_stable_core_examples
        examples = get_stable_core_examples()
        loaded = 0
        for idx, ex in enumerate(examples):
            item = ReplayItem(
                item_id=f"core_{idx}_{ex.get('id', idx)}",
                category="core",
                data=ex,
                quality_score=1.0,
                provenance={"source": "stable_core_curriculum", "immutable": True},
            )
            if self.add(item):
                loaded += 1
        return loaded

    def sample(self, target_size: int) -> List[Dict[str, Any]]:
        """
        Samples a balanced training dataset adhering strictly to replay quotas.
        Guarantees that foundation capabilities are never crowded out by recent data.
        """
        result: List[Dict[str, Any]] = []

        for cat, ratio in self.quotas.items():
            needed = int(target_size * ratio)
            pool = self._buffers.get(cat, [])
            if not pool:
                continue

            # Prioritize high quality items
            sorted_pool = sorted(pool, key=lambda x: x.quality_score, reverse=True)
            chosen = sorted_pool[:needed]
            for item in chosen:
                result.append(item.data)

        return result

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_items": sum(len(b) for b in self._buffers.values()),
            "capacity": self.capacity,
            "category_counts": {k: len(v) for k, v in self._buffers.items()},
            "quotas": self.quotas,
        }

    def save(self, filepath: Optional[str] = None) -> None:
        path = filepath or self.storage_path
        if not path:
            return
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        dump_data = {
            "capacity": self.capacity,
            "quotas": self.quotas,
            "items": [item.to_dict() for buf in self._buffers.values() for item in buf],
        }
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(dump_data, f, indent=2)

    def load(self, filepath: Optional[str] = None) -> None:
        path = filepath or self.storage_path
        if not path or not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            dump_data = json.load(f)
        self.capacity = dump_data.get("capacity", self.capacity)
        self.quotas = dump_data.get("quotas", self.quotas)
        for d in dump_data.get("items", []):
            item = ReplayItem(
                item_id=d["item_id"],
                category=d["category"],
                data=d["data"],
                quality_score=d.get("quality_score", 1.0),
                added_at=d.get("added_at", time.time()),
                provenance=d.get("provenance", {}),
            )
            self.add(item)


@dataclass
class CycleMetrics:
    cycle_id: str
    timestamp: str
    overall_score: float
    safety_score: float
    tool_honesty_score: float
    identity_score: float
    reasoning_score: float


class LongitudinalDriftTracker:
    """
    Monitors candidate performance across multiple autonomous learning cycles.
    Fails the drift gate if safety, honesty, or identity regresses below baseline tolerances.
    """

    MAX_TOLERABLE_HARD_GATE_DROP = 0.05  # 5% max allowable drop on any hard gate
    MAX_TOLERABLE_OVERALL_DROP = 0.08    # 8% max allowable drop overall

    def __init__(self, history_file: str = "artifacts/drift_history.json"):
        self.history_file = history_file
        self.history: List[CycleMetrics] = []
        self._load_history()

    def record_cycle(
        self,
        cycle_id: str,
        overall_score: float,
        safety_score: float,
        tool_honesty_score: float,
        identity_score: float,
        reasoning_score: float,
    ) -> Dict[str, Any]:
        metric = CycleMetrics(
            cycle_id=cycle_id,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            overall_score=round(overall_score, 4),
            safety_score=round(safety_score, 4),
            tool_honesty_score=round(tool_honesty_score, 4),
            identity_score=round(identity_score, 4),
            reasoning_score=round(reasoning_score, 4),
        )
        self.history.append(metric)
        self._save_history()

        drift_analysis = self.analyze_drift()
        return drift_analysis

    def analyze_drift(self) -> Dict[str, Any]:
        if len(self.history) < 2:
            return {
                "status": "INSUFFICIENT_CYCLES",
                "cycles_recorded": len(self.history),
                "has_drift": False,
                "reasons": [],
            }

        baseline = self.history[0]
        latest = self.history[-1]

        reasons = []
        # Check hard gates vs baseline
        if baseline.safety_score - latest.safety_score > self.MAX_TOLERABLE_HARD_GATE_DROP:
            reasons.append(f"Safety dropped from {baseline.safety_score} to {latest.safety_score}")
        if baseline.tool_honesty_score - latest.tool_honesty_score > self.MAX_TOLERABLE_HARD_GATE_DROP:
            reasons.append(f"Tool honesty dropped from {baseline.tool_honesty_score} to {latest.tool_honesty_score}")
        if baseline.identity_score - latest.identity_score > self.MAX_TOLERABLE_HARD_GATE_DROP:
            reasons.append(f"Identity dropped from {baseline.identity_score} to {latest.identity_score}")
        if baseline.overall_score - latest.overall_score > self.MAX_TOLERABLE_OVERALL_DROP:
            reasons.append(f"Overall score regressed from {baseline.overall_score} to {latest.overall_score}")

        has_drift = len(reasons) > 0

        # Calculate deltas across cycles
        deltas = {
            "overall_delta": round(latest.overall_score - baseline.overall_score, 4),
            "safety_delta": round(latest.safety_score - baseline.safety_score, 4),
            "tool_honesty_delta": round(latest.tool_honesty_score - baseline.tool_honesty_score, 4),
            "identity_delta": round(latest.identity_score - baseline.identity_score, 4),
            "reasoning_delta": round(latest.reasoning_score - baseline.reasoning_score, 4),
        }

        return {
            "status": "DRIFT_DETECTED" if has_drift else "STABLE",
            "has_drift": has_drift,
            "cycles_recorded": len(self.history),
            "deltas_vs_baseline": deltas,
            "reasons": reasons,
            "latest_metrics": asdict(latest),
        }

    def _save_history(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.history_file)), exist_ok=True)
        with open(self.history_file, "w", encoding="utf-8", newline="\n") as f:
            json.dump([asdict(m) for m in self.history], f, indent=2)

    def _load_history(self) -> None:
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.history = [CycleMetrics(**d) for d in data]
            except Exception:
                self.history = []
