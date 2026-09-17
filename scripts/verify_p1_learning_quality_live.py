"""
AURA P1 - REAL LEARNING QUALITY LIVE EXPERIMENT (P1-E001).

Runs a real scheduler-driven self-learning cycle end-to-end on this
machine and captures the forensic evidence:

    1. Scheduler autonomy: the cycle is triggered by the scheduler's own
       eligibility logic (check_eligibility -> poll_and_execute), not a
       manual pipeline invocation.
    2. Real GPU LoRA training: loss.backward/optimizer.step on the RTX
       4060, parameter-delta proof.
    3. Contamination gate: dataset vs immutable held-out set.
    4. Deterministic held-out evaluation of FOUR models under identical
       conditions: parent base (learning baseline), trained adapter,
       exported candidate GGUF, production 3B GGUF.
    5. Conservative promotion decision with hard safety gates.
    6. Real promotion/rejection: registry + package state verified on
       disk afterwards; if promoted, the runtime-visible GGUF SHA must
       match the promoted artifact.
    7. Everything written to artifacts/p1_learning_quality_summary.json.

Usage:
    .venv\\Scripts\\python.exe -X utf8 scripts\\verify_p1_learning_quality_live.py [--promote]

--promote enables auto_promote (gated promotion; it still only promotes
if the evaluation says so). Without it the cycle runs in
recommendation-only mode (rejection recorded, production untouched).
"""

import argparse
import hashlib
import json
import os
import sys
import time
import traceback

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from core.logger import logger  # noqa: E402


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def file_state(path: str) -> dict:
    """Before/after snapshot for integrity-sensitive files."""
    if not os.path.exists(path):
        return {"exists": False}
    return {
        "exists": True,
        "sha256": sha256_file(path),
        "size_bytes": os.path.getsize(path),
        "modified": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(os.path.getmtime(path))),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--promote", action="store_true",
                        help="Enable auto-promotion (still gated by evaluation)")
    parser.add_argument("--experiment-id", default="P1-E001")
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--max-steps", type=int, default=None)
    args = parser.parse_args()

    print("=" * 78)
    print(f"AURA P1 REAL LEARNING QUALITY - LIVE EXPERIMENT {args.experiment_id}")
    print("=" * 78)

    summary = {
        "experiment_id": args.experiment_id,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "machine": os.environ.get("COMPUTERNAME", "unknown"),
        "auto_promote_requested": args.promote,
        "stages": {},
    }

    # ------------------------------------------------------------------
    # Stage 0: environment + pre-state snapshot (registry files are
    # integrity-sensitive; we snapshot, never hand-edit them)
    # ------------------------------------------------------------------
    registry_path = os.path.join(REPO_ROOT, "brains", "model_registry.json")
    brain_state_path = os.path.join(REPO_ROOT, "brains", "brain_state.json")
    sched_state_path = os.path.join(REPO_ROOT, "brains", "scheduler_state.json")
    prod_gguf = os.path.join(REPO_ROOT, "brains", "aura-brain-v1", "model.gguf")
    prod_manifest = os.path.join(REPO_ROOT, "brains", "aura-brain-v1", "manifest.json")

    pre_state = {
        "model_registry": file_state(registry_path),
        "brain_state": file_state(brain_state_path),
        "scheduler_state": file_state(sched_state_path),
        "production_gguf": file_state(prod_gguf),
        "production_manifest": file_state(prod_manifest),
    }
    summary["stages"]["pre_state"] = pre_state
    print(f"\n[0] Pre-state snapshot recorded (registry, brain_state, production GGUF).")
    print(f"    active production GGUF sha256: {pre_state['production_gguf'].get('sha256', '')[:16]}...")

    import torch
    summary["environment"] = {
        "torch": torch.__version__,
        "cuda_available": torch.cuda.is_available(),
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none",
        "vram_total_mb": round(torch.cuda.get_device_properties(0).total_memory / 1048576) if torch.cuda.is_available() else 0,
    }
    print(f"    torch {torch.__version__}, cuda={summary['environment']['cuda_available']}, "
          f"gpu={summary['environment']['gpu']}")
    if not summary["environment"]["cuda_available"]:
        print("FATAL: CUDA not available - P1 requires real GPU training.")
        return 2

    # ------------------------------------------------------------------
    # Stage 1: scheduler autonomy check
    # ------------------------------------------------------------------
    from learning.experience import AuraExperienceStore
    from learning.pipeline import LearningCandidatePipeline
    from learning.scheduler import AutonomousLearningScheduler, SchedulerConfig

    store = AuraExperienceStore()
    pipeline = LearningCandidatePipeline(store=store)
    config = SchedulerConfig(auto_promote=args.promote)
    scheduler = AutonomousLearningScheduler(
        experience_store=store,
        pipeline=pipeline,
        config=config,
        state_file=sched_state_path,
    )

    eligible, reason = scheduler.check_eligibility()
    print(f"\n[1] Scheduler eligibility: eligible={eligible} reason={reason!r}")
    summary["stages"]["scheduler_eligibility"] = {"eligible": eligible, "reason": reason}
    if not eligible:
        # The scheduler refuses on its own terms - that is a legitimate
        # autonomous outcome, but for the experiment we need a cycle.
        print("    Scheduler not eligible; cannot run the experiment now.")
        summary["status"] = "SCHEDULER_NOT_ELIGIBLE"
        dump_summary(summary)
        return 1

    # ------------------------------------------------------------------
    # Stage 2: the real cycle (scheduler-triggered, GPU training,
    # contamination gate, held-out evaluation, promotion decision)
    # ------------------------------------------------------------------
    print("\n[2] Executing scheduler cycle (this trains on GPU - takes minutes)...")
    t0 = time.time()
    try:
        triggered, trace = scheduler.poll_and_execute(
            epochs=args.epochs,
            max_steps=args.max_steps,
            auto_promote=args.promote,
        )
    except Exception as e:
        print(f"    CYCLE RAISED: {e}")
        traceback.print_exc()
        summary["status"] = "CYCLE_EXCEPTION"
        summary["stages"]["cycle_exception"] = traceback.format_exc()
        dump_summary(summary)
        return 2
    duration = time.time() - t0
    print(f"    Cycle finished in {duration:.1f}s, status={trace.get('status')}")

    # Strip the bulky per-case reports from the persisted trace copy
    trace_slim = dict(trace)
    if "evaluation" in trace_slim and isinstance(trace_slim["evaluation"], dict):
        trace_slim["evaluation"] = {
            k: v for k, v in trace_slim["evaluation"].items() if k != "reports"
        }
    summary["stages"]["cycle"] = trace_slim
    summary["cycle_duration_seconds"] = round(duration, 1)
    summary["cycle_status"] = trace.get("status")

    # ------------------------------------------------------------------
    # Stage 3: post-state verification (promotion/rejection reality)
    # ------------------------------------------------------------------
    print("\n[3] Post-state verification...")
    post_state = {
        "model_registry": file_state(registry_path),
        "brain_state": file_state(brain_state_path),
        "scheduler_state": file_state(sched_state_path),
        "production_gguf": file_state(prod_gguf),
        "production_manifest": file_state(prod_manifest),
    }

    from brain.registry import AuraModelRegistry
    from brain.package import BrainManager

    registry = AuraModelRegistry(registry_file=registry_path)
    manager = BrainManager(brains_dir=os.path.join(REPO_ROOT, "brains"))
    active_pkg = manager.get_active_package()

    verification: dict = {
        "registry_active_model": registry.active_model,
        "brain_state_active_brain": None,
        "active_package": None,
        "production_gguf_unchanged": (
            pre_state["production_gguf"].get("sha256") == post_state["production_gguf"].get("sha256")
        ),
        "production_manifest_unchanged": (
            pre_state["production_manifest"].get("sha256") == post_state["production_manifest"].get("sha256")
        ),
    }
    if os.path.exists(brain_state_path):
        with open(brain_state_path, "r", encoding="utf-8") as f:
            verification["brain_state_active_brain"] = json.load(f).get("active_brain_id")
    if active_pkg:
        verification["active_package"] = active_pkg.manifest.brain_id
        gguf_in_pkg = os.path.join(active_pkg.package_dir, "model.gguf")
        if os.path.exists(gguf_in_pkg):
            verification["active_package_gguf_sha256"] = sha256_file(gguf_in_pkg)
            verification["active_package_gguf_matches_manifest"] = (
                sha256_file(gguf_in_pkg) == active_pkg.manifest.checksum
            )

    summary["stages"]["post_state"] = post_state
    summary["stages"]["verification"] = verification
    print(f"    registry active_model: {verification['registry_active_model']}")
    print(f"    brain_state active: {verification['brain_state_active_brain']}")
    print(f"    active package: {verification['active_package']}")
    print(f"    production GGUF unchanged: {verification['production_gguf_unchanged']}")

    # ------------------------------------------------------------------
    # Stage 4: runtime verification - what the AURA runtime would load
    # ------------------------------------------------------------------
    print("\n[4] Runtime artifact verification (what LocalModelRuntime would load)...")
    runtime_check: dict = {}
    if active_pkg and active_pkg.manifest.model_format == "gguf":
        gguf_path = os.path.join(active_pkg.package_dir, "model.gguf")
        if os.path.exists(gguf_path):
            runtime_check["runtime_gguf_path"] = gguf_path
            runtime_check["runtime_gguf_sha256"] = sha256_file(gguf_path)
            # The GGUF the runtime would execute must be the artifact that
            # was evaluated and promoted.
            eval_block = trace.get("evaluation", {})
            promoted_sha = trace.get("promotion", {}).get("gguf_sha256")
            if promoted_sha:
                runtime_check["runtime_sha_matches_promoted_artifact"] = (
                    runtime_check["runtime_gguf_sha256"] == promoted_sha
                )
            # And it must actually run in the native runtime
            from learning.quality_eval import GGUFHarness
            harness = GGUFHarness(gguf_path=gguf_path, expected_sha256=runtime_check["runtime_gguf_sha256"])
            if harness.verify():
                reply = harness.generate("Người ta thường gọi bạn là gì?")
                runtime_check["smoke_test_reply"] = reply[:200]
                runtime_check["smoke_test_ok"] = bool(reply.strip())
            else:
                runtime_check["smoke_test_ok"] = False
                runtime_check["smoke_test_error"] = harness.load_error
    summary["stages"]["runtime_verification"] = runtime_check
    if runtime_check:
        print(f"    runtime GGUF: {runtime_check.get('runtime_gguf_path')}")
        print(f"    sha matches promoted artifact: {runtime_check.get('runtime_sha_matches_promoted_artifact')}")
        print(f"    smoke test ok: {runtime_check.get('smoke_test_ok')}")

    # ------------------------------------------------------------------
    # Stage 5: verdict (conservative, computed from trace evidence)
    # ------------------------------------------------------------------
    eval_block = trace.get("evaluation", {})
    baseline = eval_block.get("baseline_score")
    cand_adapter = eval_block.get("candidate_score")
    cand_gguf = eval_block.get("merged_score")
    production = eval_block.get("production_score")

    print("\n[5] Held-out evaluation summary (26 cases, identical protocol):")
    print(f"    parent base (learning baseline): {baseline}")
    print(f"    candidate adapter:               {cand_adapter}")
    print(f"    candidate GGUF (promoted artifact): {cand_gguf}")
    print(f"    production 3B GGUF:              {production}")
    if eval_block.get("rejection_reasons"):
        print(f"    rejection reasons: {eval_block['rejection_reasons']}")

    learning_improved = (
        isinstance(cand_gguf, (int, float))
        and isinstance(baseline, (int, float))
        and cand_gguf > baseline
    )
    summary["verdict_inputs"] = {
        "baseline_score": baseline,
        "candidate_adapter_score": cand_adapter,
        "candidate_gguf_score": cand_gguf,
        "production_score": production,
        "learning_improved_on_heldout": learning_improved,
        "cycle_status": trace.get("status"),
        "is_promotable": eval_block.get("is_promotable"),
        "rejection_reasons": eval_block.get("rejection_reasons"),
    }

    summary["status"] = "COMPLETED"
    summary["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    dump_summary(summary)
    print(f"\nSummary written to artifacts/p1_learning_quality_summary.json")
    return 0


def dump_summary(summary: dict) -> None:
    out_dir = os.path.join(REPO_ROOT, "artifacts")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "p1_learning_quality_summary.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    exp_id = summary.get("experiment_id", "")
    if "p1.5" in exp_id.lower() or "p1_5" in exp_id.lower():
        v_path = os.path.join(out_dir, "p1_5_learning_quality_summary.json")
        with open(v_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    sys.exit(main())
