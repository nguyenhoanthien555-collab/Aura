"""
Tests for Phase P2.1 Autonomous Canary Policy & Machine Enforcement.

Proves:
1. Below threshold -> no cycle
2. Eligible threshold met but new threshold missing -> no cycle
3. New threshold met but eligible threshold missing -> no cycle
4. Both thresholds met -> cycle eligible
5. Duplicate trigger -> blocked
6. Cooldown -> enforced
7. Candidate -> isolated (production untouched)
8. Failed candidate -> cannot serve
9. Dual-suite gate (Held-Out V1 + V2) -> hard gate regression blocks promotion
10. Longitudinal drift tracker -> >5% hard gate drop or >8% overall drop blocks promotion
11. Rollback -> restores known-good production
"""

import json
import os
import shutil
import tempfile
import time
import pytest

from brain.registry import AuraModelRegistry, AuraModelVersion
from learning.experience import AuraExperienceStore
from learning.pipeline import LearningCandidatePipeline
from learning.scheduler import (
    AutonomousLearningScheduler,
    SchedulerConfig,
    LearningState,
)
from learning.quality_eval import (
    HeldOutReport,
    CaseResult,
    compare_dual_suite_for_promotion,
)
from learning.replay_buffer import LongitudinalDriftTracker


class MockExperienceStore:
    def __init__(self, count: int = 0):
        self.count = count

    def list_eligible_experiences(self, min_quality=0.6, limit=1000):
        return [{"id": f"exp_{i}"} for i in range(self.count)]


def test_canary_threshold_rules():
    """Proves rules 1-4: Canary experience eligibility thresholds."""
    tmp_dir = tempfile.mkdtemp()
    try:
        mock_store = MockExperienceStore(count=30)
        config = SchedulerConfig(
            canary_mode=True,
            canary_min_eligible=50,
            canary_min_new=20,
            cooldown_seconds=0,
            min_free_ram_mb=100,
        )
        scheduler = AutonomousLearningScheduler(
            config=config,
            experience_store=mock_store,
            state_file=os.path.join(tmp_dir, "sched.json"),
        )

        # Rule 1: Below eligible threshold (30 < 50) -> no cycle
        eligible, reason = scheduler.check_eligibility()
        assert not eligible
        assert "Eligible experiences below threshold (30 < 50)" in reason

        # Increase store count to 55 (both met: 55 >= 50, 55 new >= 20)
        mock_store.count = 55
        # Rule 4: Both thresholds met -> eligible
        eligible, reason = scheduler.check_eligibility()
        assert eligible
        assert "55 total, 55 new" in reason

        # Simulate cycle completion with 55 trained experiences
        scheduler._last_trained_experience_count = 55
        scheduler._save_state()

        # Rule 2: Eligible threshold met (55 >= 50) but new threshold missing (0 < 20) -> no cycle
        eligible, reason = scheduler.check_eligibility()
        assert not eligible
        assert "Insufficient new experiences (0 < 20)" in reason

        # Increase to 65 (new = 10 < 20) -> still no cycle
        mock_store.count = 65
        eligible, reason = scheduler.check_eligibility()
        assert not eligible
        assert "Insufficient new experiences (10 < 20)" in reason

        # Increase to 75 (new = 20 >= 20) -> eligible!
        mock_store.count = 75
        eligible, reason = scheduler.check_eligibility()
        assert eligible
        assert "75 total, 20 new" in reason

        # Rule 3: High new experiences but eligible below threshold
        # Total is 25 (< 50), new is 25 (>= 20)
        mock_small = MockExperienceStore(count=25)
        scheduler_small = AutonomousLearningScheduler(
            config=config,
            experience_store=mock_small,
        )
        eligible_s, reason_s = scheduler_small.check_eligibility()
        assert not eligible_s
        assert "Eligible experiences below threshold (25 < 50)" in reason_s

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)



def test_cooldown_and_duplicate_trigger():
    """Proves rules 5 & 6: Cooldown and duplicate trigger blocking."""
    scheduler = AutonomousLearningScheduler(
        config=SchedulerConfig(
            min_eligible_experiences=1,
            min_new_experiences=1,
            cooldown_seconds=60.0,
            min_free_ram_mb=100,
        ),
    )

    # 1. Duplicate trigger when in active state
    scheduler.transition(LearningState.TRAINING)
    eligible, reason = scheduler.check_eligibility()
    assert not eligible
    assert "Job already active in state: TRAINING" in reason

    # Transition to ACTIVE (triggers cooldown)
    scheduler.transition(LearningState.ACTIVE)
    assert scheduler._last_train_time > 0

    # 2. Immediate check after training -> Cooldown in effect
    eligible, reason = scheduler.check_eligibility()
    assert not eligible
    assert "Cooldown in effect" in reason


def test_dual_suite_hard_gate_enforcement():
    """Proves rule 9: Zero tolerance on safety, tool_honesty, identity across V1 and V2."""
    b_v1 = HeldOutReport(
        model_label="baseline-v1",
        overall_score=0.60,
        category_scores={"safety": 0.80, "tool_honesty": 0.90, "identity": 0.85, "reasoning": 0.50},
        passed_tests=15,
        total_tests=26,
    )
    b_v2 = HeldOutReport(
        model_label="baseline-v2",
        overall_score=0.65,
        category_scores={"safety": 0.70, "tool_honesty": 0.80, "identity": 0.75},
        passed_tests=12,
        total_tests=20,
    )

    # Candidate 1: Better overall, but V1 safety regressed (0.80 -> 0.75)
    c1_v1 = HeldOutReport(
        model_label="cand1-v1",
        overall_score=0.70,
        category_scores={"safety": 0.75, "tool_honesty": 0.90, "identity": 0.85, "reasoning": 0.70},
        passed_tests=18,
        total_tests=26,
    )
    c1_v2 = HeldOutReport(
        model_label="cand1-v2",
        overall_score=0.70,
        category_scores={"safety": 0.70, "tool_honesty": 0.80, "identity": 0.75},
        passed_tests=14,
        total_tests=20,
    )
    dec1 = compare_dual_suite_for_promotion(c1_v1, c1_v2, b_v1, b_v2)
    assert not dec1.is_promotable
    assert any("V1 safety regression" in r for r in dec1.rejection_reasons)

    # Candidate 2: Better overall, V1 perfect, but V2 identity regressed (0.75 -> 0.60)
    c2_v1 = HeldOutReport(
        model_label="cand2-v1",
        overall_score=0.70,
        category_scores={"safety": 0.85, "tool_honesty": 0.90, "identity": 0.85, "reasoning": 0.70},
        passed_tests=18,
        total_tests=26,
    )
    c2_v2 = HeldOutReport(
        model_label="cand2-v2",
        overall_score=0.70,
        category_scores={"safety": 0.75, "tool_honesty": 0.80, "identity": 0.60},
        passed_tests=14,
        total_tests=20,
    )
    dec2 = compare_dual_suite_for_promotion(c2_v1, c2_v2, b_v1, b_v2)
    assert not dec2.is_promotable
    assert any("V2 identity regression" in r for r in dec2.rejection_reasons)

    # Candidate 3: Superior on V1 and V2, no regressions
    c3_v1 = HeldOutReport(
        model_label="cand3-v1",
        overall_score=0.72,
        category_scores={"safety": 0.85, "tool_honesty": 0.95, "identity": 0.90, "reasoning": 0.70},
        passed_tests=19,
        total_tests=26,
    )
    c3_v2 = HeldOutReport(
        model_label="cand3-v2",
        overall_score=0.75,
        category_scores={"safety": 0.75, "tool_honesty": 0.85, "identity": 0.80},
        passed_tests=15,
        total_tests=20,
    )
    dec3 = compare_dual_suite_for_promotion(c3_v1, c3_v2, b_v1, b_v2)
    assert dec3.is_promotable
    assert len(dec3.rejection_reasons) == 0


def test_longitudinal_drift_gate():
    """Proves rule 10: Drift exceeding 5% hard gate or 8% overall blocks promotion."""
    tmp_dir = tempfile.mkdtemp()
    try:
        drift_file = os.path.join(tmp_dir, "drift.json")
        tracker = LongitudinalDriftTracker(history_file=drift_file)

        # Baseline cycle
        tracker.record_cycle("cycle_base", 0.70, 0.90, 0.90, 0.90, 0.70)

        # Cycle 1: Stable
        res1 = tracker.record_cycle("cycle_1", 0.71, 0.90, 0.90, 0.90, 0.72)
        assert res1["status"] == "STABLE"
        assert not res1["has_drift"]

        # Cycle 2: Severe safety drop (0.90 -> 0.82, drop of 0.08 > 0.05 limit)
        res2 = tracker.record_cycle("cycle_2", 0.71, 0.82, 0.90, 0.90, 0.75)
        assert res2["status"] == "DRIFT_DETECTED"
        assert res2["has_drift"]
        assert any("Safety dropped" in r for r in res2["reasons"])

        # Check compare_dual_suite_for_promotion with this drift result
        dummy_rep = HeldOutReport("cand", 0.75, {"safety": 0.82, "tool_honesty": 0.90, "identity": 0.90}, 18, 26)
        base_rep = HeldOutReport("base", 0.70, {"safety": 0.90, "tool_honesty": 0.90, "identity": 0.90}, 15, 26)
        dec = compare_dual_suite_for_promotion(dummy_rep, dummy_rep, base_rep, base_rep, drift_result=res2)
        assert not dec.is_promotable
        assert any("Longitudinal drift gate failed" in r for r in dec.rejection_reasons)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_candidate_isolation_and_rollback():
    """Proves rules 7, 8, 11: Candidate isolation, rejection isolation, and rollback."""
    tmp_dir = tempfile.mkdtemp()
    try:
        reg_file = os.path.join(tmp_dir, "registry.json")
        registry = AuraModelRegistry(registry_file=reg_file)

        # 1. Establish production aura-brain-v1
        prod = AuraModelVersion(
            aura_version="aura-brain-v1",
            parent_version=None,
            foundation_model="Qwen/Qwen2.5-0.5B-Instruct",
            foundation_artifact_hash="hash_base",
            dataset_version="v1",
            dataset_hash="ds_hash",
            training_run_id="run_init",
            adapter_hash="ad_hash",
            merged_checkpoint_hash="mg_hash",
            gguf_hash="gg_hash",
            artifact_paths={"gguf": "/dummy/prod.gguf"},
            evaluation={"overall_score": 0.75},
            parameter_deltas={},
            promotion_status="ACTIVE",
        )
        registry.register_candidate(prod)
        registry.promote_candidate("aura-brain-v1", {"reason": "initial production"})
        assert registry.active_model == "aura-brain-v1"

        # 2. Register candidate in candidate directory
        cand_dir = os.path.join(tmp_dir, "candidates", "cand-01")
        os.makedirs(cand_dir, exist_ok=True)
        cand_gguf = os.path.join(cand_dir, "model.gguf")
        with open(cand_gguf, "w") as f:
            f.write("candidate data")

        cand = AuraModelVersion(
            aura_version="cand-01",
            parent_version="aura-brain-v1",
            foundation_model="Qwen/Qwen2.5-0.5B-Instruct",
            foundation_artifact_hash="hash_base",
            dataset_version="v2",
            dataset_hash="ds2_hash",
            training_run_id="run_cand_01",
            adapter_hash="ad2_hash",
            merged_checkpoint_hash="mg2_hash",
            gguf_hash="gg2_hash",
            artifact_paths={"gguf": cand_gguf},
            evaluation={"overall_score": 0.60},
            parameter_deltas={},
            promotion_status="CANDIDATE",
        )
        registry.register_candidate(cand)

        # Production model is STILL aura-brain-v1 (Rule 7: Candidate isolated)
        assert registry.active_model == "aura-brain-v1"

        # Reject candidate (Rule 8: Failed candidate cannot serve)
        registry.reject_candidate("cand-01", "Regressed on held-out benchmark", {"overall_score": 0.60})
        assert registry.active_model == "aura-brain-v1"
        assert registry.candidates["cand-01"].promotion_status == "REJECTED"

        # Rule 11: Rollback functionality
        # Suppose a promoted model was promoted to active:
        cand_good = AuraModelVersion(
            aura_version="cand-good",
            parent_version="aura-brain-v1",
            foundation_model="Qwen/Qwen2.5-0.5B-Instruct",
            foundation_artifact_hash="hash_base",
            dataset_version="v3",
            dataset_hash="ds3_hash",
            training_run_id="run_cand_good",
            adapter_hash="ad3_hash",
            merged_checkpoint_hash="mg3_hash",
            gguf_hash="gg3_hash",
            artifact_paths={"gguf": "/dummy/good.gguf"},
            evaluation={"overall_score": 0.85},
            parameter_deltas={},
            promotion_status="CANDIDATE",
        )
        registry.register_candidate(cand_good)
        registry.promote_candidate("cand-good", {"overall_score": 0.85})
        assert registry.active_model == "cand-good"
        assert registry.rollback_model == "aura-brain-v1"

        # Trigger rollback -> must restore aura-brain-v1
        rolled = registry.rollback("Detected operational anomaly")
        assert rolled is True
        assert registry.active_model == "aura-brain-v1"

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
