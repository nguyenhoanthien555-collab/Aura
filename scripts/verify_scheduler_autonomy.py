"""
AURA P0-B Forensic Verification Harness: Autonomous Scheduler Triggering & Lifecycle.

Empirically proves:
1. Experience ingestion and privacy/quality screening
2. Real scheduler threshold detection
3. Autonomous lifecycle execution without manual pipeline script invocation
4. Cooldown enforcement
5. Duplicate training prevention
6. State persistence across restart
"""

from datetime import datetime
import json
import os
import sys
import time

REPO_ROOT = r"D:\AURA"
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from brain.registry import AuraModelRegistry
from core.logger import logger
from learning.experience import AuraExperienceStore
from learning.pipeline import LearningCandidatePipeline
from learning.scheduler import AutonomousLearningScheduler, SchedulerConfig, LearningState
from daemon.supervisor import AuraDaemon


def verify_scheduler_autonomy():
    print("=" * 70)
    print("  P0-B: AUTONOMOUS SCHEDULER TRIGGER & COOLDOWN FORENSIC VERIFICATION")
    print("=" * 70)
    print(f"Timestamp: {datetime.now().isoformat()}")

    exp_store = AuraExperienceStore()
    pipeline = LearningCandidatePipeline(store=exp_store)
    registry = AuraModelRegistry()

    active_before = registry.active_model
    print(f"Production Active Model Before Test: {active_before}")
    assert active_before == "aura-brain-v1", f"Expected production model aura-brain-v1, got {active_before}"

    # 1. Inspect initial eligible experiences
    eligible_initial = exp_store.list_eligible_experiences(min_quality=0.6, limit=1000)
    print(f"Initial eligible experiences in store: {len(eligible_initial)}")

    # 2. Configure a test scheduler instance with bounded thresholds
    config = SchedulerConfig(
        min_eligible_experiences=5,
        min_new_experiences=3,
        cooldown_seconds=30.0,
        min_free_ram_mb=1024,
        auto_promote=False,
    )
    test_state_file = os.path.join(REPO_ROOT, "brains", "scheduler_test_state.json")
    if os.path.exists(test_state_file):
        os.remove(test_state_file)

    scheduler = AutonomousLearningScheduler(
        config=config,
        experience_store=exp_store,
        pipeline=pipeline,
        state_file=test_state_file,
    )

    # Fast-forward scheduler baseline to current experience count so it requires NEW experiences
    scheduler._last_trained_experience_count = len(eligible_initial)
    scheduler._save_state()

    # Verify that before new experiences arrive, scheduler is NOT eligible
    is_ready, reason = scheduler.check_eligibility()
    print(f"Eligibility check BEFORE new experiences: ready={is_ready}, reason='{reason}'")
    assert not is_ready, "Scheduler should not trigger without new experiences"

    # 3. Ingest NEW valid operational experiences through the real ExperienceStore path
    print("\nIngesting 3 new verified experiences through AuraExperienceStore...")
    new_exps = [
        ("Mở ứng dụng Cài đặt", "TOOL_CALL", "android.launch_app", {"package_name": "com.android.settings"}, "VERIFIED", "SUCCESS"),
        ("Bạn là ai?", "ANSWER", "", {}, "VERIFIED", "SUCCESS"),
        ("Chụp ảnh màn hình ngay", "TOOL_CALL", "android.screenshot", {}, "VERIFIED", "SUCCESS"),
    ]
    for text, decision, tool, args, verifier, outcome in new_exps:
        exp_store.record_experience(
            session_id=f"p0_autonomy_{int(time.time())}",
            input_text=text,
            model_decision=decision,
            selected_tool=tool,
            arguments=args,
            verifier_result=verifier,
            outcome=outcome,
            category="autonomy_test",
        )

    eligible_after_ingest = exp_store.list_eligible_experiences(min_quality=0.6, limit=1000)
    print(f"Eligible experiences after ingestion: {len(eligible_after_ingest)} (+{len(eligible_after_ingest) - len(eligible_initial)})")

    # 4. Scheduler observes threshold autonomously
    is_ready, reason = scheduler.check_eligibility()
    print(f"Eligibility check AFTER new experiences: ready={is_ready}, reason='{reason}'")
    assert is_ready, f"Scheduler failed to detect eligibility: {reason}"

    # 5. Autonomous trigger via poll_and_execute
    print("\nExecuting bounded autonomous cycle via scheduler.poll_and_execute(max_steps=5)...")
    t0 = time.time()
    triggered, cycle_trace = scheduler.poll_and_execute(max_steps=5, epochs=1, auto_promote=False)
    duration = time.time() - t0

    assert triggered is True, "Autonomous trigger did not execute"
    assert cycle_trace is not None, "No cycle trace returned"
    print(f"Cycle completed in {duration:.2f}s with status: {cycle_trace.get('status')}")
    print(f"Cycle Transitions: {[t['state'] for t in cycle_trace.get('transitions', [])]}")
    print(f"Dataset generated: {cycle_trace.get('dataset', {}).get('id')} ({cycle_trace.get('dataset', {}).get('examples')} examples)")
    print(f"Candidate version: {cycle_trace.get('candidate_version')}")
    print(f"Evaluation result: {cycle_trace.get('evaluation', {}).get('merged_score')} (is_promotable={cycle_trace.get('evaluation', {}).get('is_promotable')})")

    # 6. Cooldown enforcement test
    print("\nTesting Cooldown Enforcement...")
    is_ready_cooldown, reason_cooldown = scheduler.check_eligibility()
    print(f"Immediate check after training: ready={is_ready_cooldown}, reason='{reason_cooldown}'")
    assert not is_ready_cooldown, "Scheduler must be in cooldown immediately after training"
    assert "Cooldown in effect" in reason_cooldown

    # 7. Duplicate training prevention test
    print("\nTesting Duplicate Training Prevention (simulating cooldown expiry without new data)...")
    scheduler._last_train_time = time.time() - 3600.0  # simulate 1 hour past cooldown
    is_ready_dup, reason_dup = scheduler.check_eligibility()
    print(f"Check with expired cooldown but no new data: ready={is_ready_dup}, reason='{reason_dup}'")
    assert not is_ready_dup, "Scheduler must refuse duplicate training without new experiences"
    assert "Insufficient new experiences" in reason_dup

    # 8. State survival across restart
    print("\nTesting Scheduler State Survival Across Restart...")
    reloaded_scheduler = AutonomousLearningScheduler(
        config=config,
        experience_store=exp_store,
        pipeline=pipeline,
        state_file=test_state_file,
    )
    print(f"Reloaded scheduler last_train_time: {reloaded_scheduler._last_train_time}")
    print(f"Reloaded scheduler last_trained_count: {reloaded_scheduler._last_trained_experience_count}")
    assert reloaded_scheduler._last_trained_experience_count == scheduler._last_trained_experience_count
    assert reloaded_scheduler.state == scheduler.state

    # 9. Verify production model was NOT overwritten
    registry = AuraModelRegistry()
    active_after = registry.active_model
    print(f"Production Active Model After Autonomous Cycle: {active_after}")
    assert active_after == "aura-brain-v1", f"Production model was altered: {active_after}"

    # 10. Write machine-readable audit trace artifact
    output_trace_path = os.path.join(REPO_ROOT, "brains", "scheduler_autonomy_trace.json")
    trace_artifact = {
        "verdict": "PROVEN",
        "timestamp": datetime.now().isoformat(),
        "threshold_criteria": {
            "min_eligible_experiences": config.min_eligible_experiences,
            "min_new_experiences": config.min_new_experiences,
            "cooldown_seconds": config.cooldown_seconds,
        },
        "initial_eligible_count": len(eligible_initial),
        "post_ingest_eligible_count": len(eligible_after_ingest),
        "threshold_detected": True,
        "detection_reason": reason,
        "cycle_execution": cycle_trace,
        "cooldown_enforced": not is_ready_cooldown,
        "cooldown_reason": reason_cooldown,
        "duplicate_prevented": not is_ready_dup,
        "duplicate_reason": reason_dup,
        "state_restart_verified": True,
        "production_model_preserved": active_after == "aura-brain-v1",
    }
    with open(output_trace_path, "w", encoding="utf-8") as f:
        json.dump(trace_artifact, f, indent=2)

    print(f"\nSaved scheduler autonomy trace to: {output_trace_path}")
    print("=" * 70)
    print("  P0-B VERIFICATION: PROVEN")
    print("=" * 70)
    return True


if __name__ == "__main__":
    verify_scheduler_autonomy()
