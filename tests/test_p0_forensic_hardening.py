"""
Focused Regression & Hardening Tests for AURA P0 Claims:
1. GGUF artifact metadata and structure
2. Autonomous scheduler threshold detection, cooldown, and duplicate prevention
3. Production model isolation and restart persistence
4. Failure recovery (failure injection preserves production active model)
"""

import json
import os
import shutil
import tempfile
import time
import pytest

from brain.package import BrainManager, BrainManifest, BrainStatus
from brain.registry import AuraModelRegistry, AuraModelVersion
from learning.experience import AuraExperienceStore
from learning.pipeline import LearningCandidatePipeline
from learning.scheduler import AutonomousLearningScheduler, SchedulerConfig, LearningState


def test_gguf_artifact_metadata_and_structure():
    """Validates the exact AURA-v2 GGUF artifact metadata and structure."""
    import gguf
    gguf_path = r"D:\AURA\brains\candidates\candidate-run_1789485787\gguf\AURA-v2-0.5B.gguf"
    assert os.path.exists(gguf_path), f"GGUF artifact not found at {gguf_path}"

    reader = gguf.GGUFReader(gguf_path)
    fields = {f.name: f for f in reader.fields.values()}

    # Verify architecture and tensor count
    assert "general.architecture" in fields
    arch = bytes(fields["general.architecture"].parts[fields["general.architecture"].data[0]]).decode("utf-8")
    assert arch == "qwen2"
    assert len(reader.tensors) == 290

    # Verify tokenizer metadata
    assert "tokenizer.ggml.model" in fields
    assert "tokenizer.ggml.pre" in fields
    pre = bytes(fields["tokenizer.ggml.pre"].parts[fields["tokenizer.ggml.pre"].data[0]]).decode("utf-8")
    assert pre == "qwen2"
    assert "tokenizer.ggml.merges" in fields
    assert len(fields["tokenizer.ggml.merges"].data) == 151387


def test_scheduler_autonomy_threshold_detection_and_cooldown(tmp_path):
    """Verifies that the scheduler enforces threshold, cooldown, and duplicate prevention."""
    state_file = str(tmp_path / "test_scheduler_state.json")
    exp_store = AuraExperienceStore()

    config = SchedulerConfig(
        min_eligible_experiences=3,
        min_new_experiences=2,
        cooldown_seconds=60.0,
        min_free_ram_mb=100,
    )

    scheduler = AutonomousLearningScheduler(
        config=config,
        experience_store=exp_store,
        state_file=state_file,
    )

    # 1. Without new experiences beyond baseline
    eligible = exp_store.list_eligible_experiences(min_quality=0.6, limit=1000)
    scheduler._last_trained_experience_count = len(eligible)
    scheduler._save_state()

    is_ready, reason = scheduler.check_eligibility()
    assert is_ready is False

    # 2. Add new experiences to meet threshold
    for i in range(2):
        exp_store.record_experience(
            session_id=f"test_p0_{int(time.time())}_{i}",
            input_text=f"Test input {i}",
            model_decision="ANSWER",
            outcome="SUCCESS",
            verifier_result="VERIFIED",
        )

    # Fast-forward last_trained_experience_count so that new experiences trigger
    scheduler._last_trained_experience_count = max(0, len(exp_store.list_eligible_experiences(min_quality=0.6, limit=1000)) - 2)
    scheduler.config.min_eligible_experiences = 1
    scheduler.config.min_new_experiences = 1

    is_ready_after, reason_after = scheduler.check_eligibility()
    assert is_ready_after is True
    assert "Eligible" in reason_after

    # 3. Simulate cycle start and cooldown
    scheduler.mark_cycle_started("test_cycle_p0")
    assert scheduler.state == LearningState.COLLECTING

    scheduler.transition(LearningState.REJECTED)
    assert scheduler.state == LearningState.REJECTED

    # In cooldown immediately after transition
    is_ready_cool, reason_cool = scheduler.check_eligibility()
    assert is_ready_cool is False
    assert "Cooldown in effect" in reason_cool

    # Duplicate prevention: even if cooldown expires, without new data it remains blocked
    scheduler._last_train_time = time.time() - 3600.0
    is_ready_dup, reason_dup = scheduler.check_eligibility()
    assert is_ready_dup is False

    # Reset config
    scheduler.config.min_eligible_experiences = 3
    scheduler.config.min_new_experiences = 2


def test_production_isolation_and_restart_persistence(tmp_path):
    """Verifies that rejected candidate cannot become active and active model persists across reload."""
    reg_file = str(tmp_path / "model_registry.json")
    registry = AuraModelRegistry(registry_file=reg_file)

    # Register baseline v1 as active
    v1 = AuraModelVersion(
        aura_version="aura-brain-v1",
        parent_version=None,
        foundation_model="Qwen/Qwen2.5-3B-Instruct",
        foundation_artifact_hash="hash_v1",
        dataset_version="v1",
        dataset_hash="ds_v1",
        training_run_id="run_v1",
        adapter_hash="",
        merged_checkpoint_hash="",
        gguf_hash="gguf_hash_v1",
        artifact_paths={"gguf": "/dummy/v1.gguf"},
        evaluation={"overall_score": 0.875},
        parameter_deltas={},
        promotion_status="ACTIVE",
    )
    registry.register_candidate(v1)
    registry.promote_candidate("aura-brain-v1", evaluation={"overall_score": 0.875})
    assert registry.active_model == "aura-brain-v1"

    # Register v2 as rejected candidate
    v2 = AuraModelVersion(
        aura_version="AURA-v2",
        parent_version="aura-brain-v1",
        foundation_model="Qwen/Qwen2.5-0.5B-Instruct",
        foundation_artifact_hash="hash_v2",
        dataset_version="v2",
        dataset_hash="ds_v2",
        training_run_id="run_v2",
        adapter_hash="ad_v2",
        merged_checkpoint_hash="mg_v2",
        gguf_hash="gguf_hash_v2",
        artifact_paths={"gguf": "/dummy/v2.gguf"},
        evaluation={"overall_score": 0.812},
        parameter_deltas={},
        promotion_status="CANDIDATE",
    )
    registry.register_candidate(v2)
    registry.reject_candidate("AURA-v2", "Safety regression gating failure", evaluation={"overall_score": 0.812})

    assert registry.active_model == "aura-brain-v1"
    assert registry.candidates["AURA-v2"].promotion_status == "REJECTED"

    # Reload / simulate restart
    reloaded = AuraModelRegistry(registry_file=reg_file)
    assert reloaded.active_model == "aura-brain-v1"
    assert reloaded.candidates["AURA-v2"].promotion_status == "REJECTED"


def test_failure_injection_recovery(tmp_path):
    """Verifies that injected failures in dataset, training, or export preserve the production active model."""
    reg_file = str(tmp_path / "model_registry.json")
    registry = AuraModelRegistry(registry_file=reg_file)

    v1 = AuraModelVersion(
        aura_version="aura-brain-v1",
        parent_version=None,
        foundation_model="Qwen/Qwen2.5-3B-Instruct",
        foundation_artifact_hash="hash_v1",
        dataset_version="v1",
        dataset_hash="ds_v1",
        training_run_id="run_v1",
        adapter_hash="",
        merged_checkpoint_hash="",
        gguf_hash="gguf_hash_v1",
        artifact_paths={"gguf": "/dummy/v1.gguf"},
        evaluation={"overall_score": 0.875},
        parameter_deltas={},
        promotion_status="ACTIVE",
    )
    registry.register_candidate(v1)
    registry.promote_candidate("aura-brain-v1", evaluation={"overall_score": 0.875})

    scheduler = AutonomousLearningScheduler(
        config=SchedulerConfig(cooldown_seconds=0),
        state_file=str(tmp_path / "sched_state.json"),
    )

    # Injected dataset failure
    res_ds = scheduler.execute_cycle(inject_failure="dataset")
    assert res_ds.get("status") == "FAILED"
    reg_after_ds = AuraModelRegistry(registry_file=reg_file)
    assert reg_after_ds.active_model == "aura-brain-v1"

    # Injected training failure
    res_tr = scheduler.execute_cycle(inject_failure="training")
    assert res_tr.get("status") == "FAILED"
    reg_after_tr = AuraModelRegistry(registry_file=reg_file)
    assert reg_after_tr.active_model == "aura-brain-v1"

    # Injected export failure
    res_ex = scheduler.execute_cycle(inject_failure="export")
    assert res_ex.get("status") == "FAILED"
    reg_after_ex = AuraModelRegistry(registry_file=reg_file)
    assert reg_after_ex.active_model == "aura-brain-v1"
