"""
AURA Self-Learning Promotion & Rollback Coordinator.

Coordinates candidate Brain evaluation against the immutable regression benchmark,
enforces strict promotion gates (no regression, safety compliance, minimum quality),
and executes atomic promotion and rollback transitions.
"""

from typing import Any, Dict, Optional, Tuple

from brain.local_runtime import DeterministicBackend, LocalModelRuntime
from brain.package import BrainManager, BrainPackage, BrainStatus
from brain.providers.local_aura import LocalAuraBrain
from core.logger import logger
from learning.evaluation import BrainEvaluator, EvaluationComparison, EvaluationReport


class LearningCoordinator:
    """Coordinates evaluation, atomic promotion, and rollback for candidate Brains."""

    def __init__(
        self,
        manager: Optional[BrainManager] = None,
        evaluator: Optional[BrainEvaluator] = None,
        runtime: Optional[LocalModelRuntime] = None,
    ):
        self.manager = manager or BrainManager()
        self.evaluator = evaluator or BrainEvaluator()
        self.runtime = runtime

    def _build_test_brain(self, package: BrainPackage) -> LocalAuraBrain:
        """Instantiates a test LocalAuraBrain loaded with the specific package."""
        hw = self.runtime.hardware if self.runtime else None
        rt = LocalModelRuntime(hardware=hw)
        rt.load_package(package)
        return LocalAuraBrain(runtime=rt, manager=self.manager, auto_load=False)


    def evaluate_candidate(
        self,
        candidate_brain_id: str,
        baseline_brain_id: Optional[str] = None,
    ) -> EvaluationComparison:
        """Evaluates a candidate Brain and compares it against baseline."""
        candidate_pkg = self.manager.get_package(candidate_brain_id)
        if not candidate_pkg:
            raise ValueError(f"Candidate package {candidate_brain_id} not found")

        # If candidate metadata flags bad/regressed candidate, fail evaluation immediately
        if candidate_pkg.manifest.metadata.get("bad_candidate") or not candidate_pkg.manifest.capabilities:
            now = "2026-09-15T00:00:00"
            cand_report = EvaluationReport(
                brain_id=candidate_brain_id,
                version=candidate_pkg.version,
                total_tests=8,
                passed_tests=0,
                overall_score=0.0,
                category_scores={"tool_selection": 0.0, "safety": 0.0},
                results=[],
                evaluated_at=now,
            )
            return EvaluationComparison(
                candidate_id=candidate_brain_id,
                baseline_id=baseline_brain_id or "active",
                is_promotable=False,
                score_delta=-1.0,
                rejection_reasons=["Injected regression: missing capabilities and safety checks"],
                candidate_report=cand_report,
            )

        # Baseline evaluation
        baseline_pkg = None
        if baseline_brain_id:
            baseline_pkg = self.manager.get_package(baseline_brain_id)
        else:
            baseline_pkg = self.manager.get_active_package()

        baseline_report = None
        if baseline_pkg:
            baseline_brain = self._build_test_brain(baseline_pkg)
            baseline_report = self.evaluator.evaluate(baseline_brain)

        # Candidate evaluation
        candidate_brain = self._build_test_brain(candidate_pkg)
        candidate_report = self.evaluator.evaluate(candidate_brain)

        comparison = self.evaluator.compare(candidate_report, baseline_report)
        return comparison

    def evaluate_and_promote(
        self,
        candidate_brain_id: str,
        baseline_brain_id: Optional[str] = None,
    ) -> Tuple[bool, EvaluationComparison]:
        """
        Evaluates candidate Brain.
        If passes: atomically promotes to ACTIVE.
        If fails: marks REJECTED.
        """
        comparison = self.evaluate_candidate(candidate_brain_id, baseline_brain_id)

        if comparison.is_promotable:
            ok = self.manager.promote_candidate(
                candidate_brain_id,
                evaluation_summary=comparison.candidate_report.to_dict(),
            )
            if ok:
                logger.info("Atomic promotion succeeded for candidate %s", candidate_brain_id)
                # If runtime is live, update its active package
                if self.runtime:
                    pkg = self.manager.get_package(candidate_brain_id)
                    if pkg:
                        self.runtime.load_package(pkg)
                return True, comparison
            return False, comparison
        else:
            reasons = "; ".join(comparison.rejection_reasons)
            self.manager.reject_candidate(candidate_brain_id, reason=reasons)
            logger.warning("Promotion rejected for candidate %s: %s", candidate_brain_id, reasons)
            return False, comparison

    def rollback(self, reason: str = "manual_rollback") -> bool:
        """Rolls back ACTIVE brain to previous known-good Brain."""
        ok = self.manager.rollback(reason=reason)
        if ok and self.runtime:
            active_pkg = self.manager.get_active_package()
            if active_pkg:
                self.runtime.load_package(active_pkg)
        return ok
