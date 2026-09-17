"""
Unit and integration tests for the AURA Autonomous Neural Self-Learning Subsystem:
- Lineage tracking and cryptographic weight hashing
- Parameter delta tracking and fingerprinting
- Model registry promotion, rollback, and audit logging
- Autonomous scheduler state machine and resource triggers
- Tripartite evaluation and safety gating
"""

from datetime import datetime
import os
import shutil
import tempfile
import pytest
import torch
import torch.nn as nn

from brain.registry import AuraModelRegistry, AuraModelVersion
from learning.lineage import compute_file_sha256, ModelLineage
from learning.parameter_tracker import ParameterTracker
from learning.scheduler import AutonomousLearningScheduler, SchedulerConfig, LearningState
from learning.evaluation import EvaluationReport, TripartiteEvaluation, BrainEvaluator


class DummyModule(nn.Module):
    def __init__(self):
        super().__init__()
        self.fc1 = nn.Linear(4, 4)
        self.fc2 = nn.Linear(4, 2)

    def forward(self, x):
        return self.fc2(self.fc1(x))


def test_parameter_tracker_mathematical_delta():
    model = DummyModule()
    tracker = ParameterTracker()

    # Capture initial snapshot
    snap_before = tracker.capture_snapshot(model)
    total_params = sum(p.numel() for p in snap_before.values())
    assert total_params == (4 * 4 + 4) + (4 * 2 + 2)  # 20 + 10 = 30
    assert len(snap_before) == 4

    # Simulate optimization step on fc1.weight
    with torch.no_grad():
        model.fc1.weight.add_(0.05)

    snap_after = tracker.capture_snapshot(model)
    delta = tracker.compute_delta(snap_before, snap_after)

    assert delta.total_parameters_tracked == 30
    assert delta.changed_parameters_count == 16  # 4x4 matrix changed
    assert delta.changed_parameters_pct == pytest.approx((16 / 30) * 100, abs=0.01)
    assert delta.max_abs_delta == pytest.approx(0.05, abs=1e-5)
    assert delta.aggregate_fingerprint_before != delta.aggregate_fingerprint_after
    assert len(delta.tensor_deltas) == 4


def test_model_registry_lifecycle_and_rollback():
    tmp_dir = tempfile.mkdtemp()
    try:
        reg_file = os.path.join(tmp_dir, "model_registry.json")
        registry = AuraModelRegistry(registry_file=reg_file)

        # 1. Register baseline AURA-v1
        v1 = AuraModelVersion(
            aura_version="AURA-v1",
            parent_version=None,
            foundation_model="Qwen/Qwen2.5-0.5B-Instruct",
            foundation_artifact_hash="abc111",
            dataset_version="v1",
            dataset_hash="ds111",
            training_run_id="run_1",
            adapter_hash="ad111",
            merged_checkpoint_hash="mg111",
            gguf_hash="gg111",
            artifact_paths={"gguf": "/dummy/v1.gguf"},
            evaluation={"overall_score": 0.85},
            parameter_deltas={},
            promotion_status="ACTIVE",
        )
        registry.register_candidate(v1)
        promoted = registry.promote_candidate("AURA-v1", {"reason": "initial baseline"})
        assert promoted is True
        assert registry.active_model == "AURA-v1"
        assert registry.rollback_model is None

        # 2. Register candidate AURA-v2
        v2 = AuraModelVersion(
            aura_version="AURA-v2",
            parent_version="AURA-v1",
            foundation_model="Qwen/Qwen2.5-0.5B-Instruct",
            foundation_artifact_hash="abc111",
            dataset_version="v2",
            dataset_hash="ds222",
            training_run_id="run_2",
            adapter_hash="ad222",
            merged_checkpoint_hash="mg222",
            gguf_hash="gg222",
            artifact_paths={"gguf": "/dummy/v2.gguf"},
            evaluation={"overall_score": 0.90},
            parameter_deltas={},
            promotion_status="CANDIDATE",
        )
        registry.register_candidate(v2)
        assert registry.get_candidate("AURA-v2") is not None

        # Promote AURA-v2
        promoted_v2 = registry.promote_candidate("AURA-v2", {"reason": "improved benchmark"})
        assert promoted_v2 is True
        assert registry.active_model == "AURA-v2"
        assert registry.rollback_model == "AURA-v1"

        # 3. Test Rollback to AURA-v1
        rb_ok = registry.rollback("safety test")
        assert rb_ok is True
        assert registry.active_model == "AURA-v1"
        assert registry.rollback_model is None
        assert registry.models["AURA-v2"].promotion_status == "ROLLED_BACK"

        # 4. Persistence Reload Test
        reloaded = AuraModelRegistry(registry_file=reg_file)
        assert reloaded.active_model == "AURA-v1"
        assert len(reloaded.history) >= 3

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_tripartite_safety_gate_enforcement():
    evaluator = BrainEvaluator()
    now = datetime.now().isoformat()

    # Baseline
    b_rep = EvaluationReport(
        brain_id="aura-v1",
        version="1.0.0",
        passed_tests=8,
        total_tests=10,
        category_scores={"tool_call": 1.0, "dangerous_action": 1.0, "reasoning": 0.8},
        results=[],
        evaluated_at=now,
        overall_score=0.85,
    )

    # Candidate 1: Regressed on dangerous action gating failure
    c_bad = EvaluationReport(
        brain_id="aura-v2-bad",
        version="2.0.0-bad",
        passed_tests=7,
        total_tests=10,
        category_scores={"tool_call": 1.0, "dangerous_action": 0.5, "reasoning": 0.8},
        results=[],
        evaluated_at=now,
        overall_score=0.75,
    )

    # Candidate 2: Superior candidate
    c_good = EvaluationReport(
        brain_id="aura-v2-good",
        version="2.0.0-good",
        passed_tests=9,
        total_tests=10,
        category_scores={"tool_call": 1.0, "dangerous_action": 1.0, "reasoning": 0.9},
        results=[],
        evaluated_at=now,
        overall_score=0.90,
    )

    # Test bad candidate comparison
    trip_bad = evaluator.compare_tripartite(c_bad, c_bad, b_rep)
    assert trip_bad.is_promotable is False
    assert trip_bad.safety_regression is True
    assert any("dangerous action" in r for r in trip_bad.rejection_reasons)

    # Test good candidate comparison
    trip_good = evaluator.compare_tripartite(c_good, c_good, b_rep)
    assert trip_good.is_promotable is True
    assert trip_good.safety_regression is False
    assert len(trip_good.rejection_reasons) == 0


def test_autonomous_scheduler_state_machine():
    scheduler = AutonomousLearningScheduler(
        config=SchedulerConfig(
            min_eligible_experiences=5,
            min_new_experiences=3,
            cooldown_seconds=0,
            min_free_ram_mb=100,
        ),
    )

    assert scheduler.state == LearningState.IDLE
    status = scheduler.get_status()
    assert status.state == "IDLE"
    assert status.last_trigger_time is None

    # State transitions
    scheduler.mark_cycle_started("cycle_1")
    assert scheduler.state == LearningState.COLLECTING

    scheduler.transition(LearningState.TRAINING)
    assert scheduler.state == LearningState.TRAINING

    scheduler.transition(LearningState.ACTIVE)
    assert scheduler.state == LearningState.ACTIVE
    assert scheduler.get_status().last_trigger_time is not None
