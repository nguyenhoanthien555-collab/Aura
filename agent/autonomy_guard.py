# -*- coding: utf-8 -*-
"""
AURA Pre-Full-Autonomy Gate and Hard Security Lock.

Enforces zero-bypass progression across phases (P1.5 to P2.2).
Full autonomous learning is LOCKED by default and strictly disabled
unless every mandatory gate (Gates A through T) evaluates to PASS.
"""

import json
import os
import time
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class PhaseState(str, Enum):
    LOCKED = "LOCKED"
    READY = "READY"
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"
    BLOCKED = "BLOCKED"


class AutonomyGateManager:
    GATE_KEYS = [
        "p1_5_reconstruction",
        "reproducibility",
        "benchmark_integrity",
        "statistical_validation",
        "runtime_parity",
        "generalization",
        "catastrophic_forgetting",
        "tool_honesty",
        "safety",
        "identity",
        "tool_calling",
        "provenance",
        "contradiction_protection",
        "crash_recovery",
        "rollback",
        "scheduler_autonomy",
        "multi_cycle_stability",
        "production_isolation",
        "resource_governance",
        "evaluator_integrity",
    ]

    def __init__(self, gate_file: Optional[str] = None):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.gate_file = gate_file or os.path.join(repo_root, "artifacts", "autonomy_gate.json")
        self.state = self._load()

    def _load(self) -> Dict[str, Any]:
        if os.path.exists(self.gate_file):
            try:
                with open(self.gate_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return self._default_state()

    def _default_state(self) -> Dict[str, Any]:
        return {
            "status": PhaseState.LOCKED.value,
            "current_phase": "P1.5_FORENSIC",
            "full_autonomy_enabled": False,
            "canary_enabled": False,
            "gates": {k: False for k in self.GATE_KEYS},
            "blocking_reasons": ["Initial state: All phases pending rigorous forensic verification"],
            "evidence": [],
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.gate_file), exist_ok=True)
        self.state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(self.gate_file, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)

    def set_gate(self, gate_name: str, passed: bool, evidence_ref: str, reason: Optional[str] = None) -> None:
        if gate_name not in self.GATE_KEYS:
            raise ValueError(f"Unknown gate: {gate_name}")
        self.state["gates"][gate_name] = bool(passed)
        self.state["evidence"].append({
            "gate": gate_name,
            "passed": bool(passed),
            "evidence": evidence_ref,
            "reason": reason or "",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        self._recompute_status()
        self.save()

    def set_phase(self, phase_name: str) -> None:
        self.state["current_phase"] = phase_name
        self.save()

    def _recompute_status(self) -> None:
        all_passed = all(self.state["gates"].values())
        failed_or_missing = [k for k, v in self.state["gates"].items() if not v]

        if all_passed:
            self.state["status"] = PhaseState.PASSED.value
            self.state["blocking_reasons"] = []
        else:
            self.state["status"] = PhaseState.LOCKED.value
            self.state["full_autonomy_enabled"] = False
            self.state["blocking_reasons"] = [
                f"Gate not passed: {g}" for g in failed_or_missing
            ]

    def can_enable_full_autonomy(self) -> Tuple[bool, List[str]]:
        all_passed = all(self.state["gates"].values())
        if not all_passed:
            unpassed = [k for k, v in self.state["gates"].items() if not v]
            return False, [f"Pre-full-autonomy gate incomplete: {u}" for u in unpassed]
        return True, []

    def assert_full_autonomy_allowed(self) -> None:
        allowed, reasons = self.can_enable_full_autonomy()
        if not allowed:
            raise RuntimeError(
                "[AutonomyGuard HARD LOCK] Full autonomous learning is LOCKED. "
                f"Blocking reasons: {reasons}"
            )
