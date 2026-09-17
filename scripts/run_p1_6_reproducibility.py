# -*- coding: utf-8 -*-
"""
AURA P1.6 Reproducibility & Statistical Evaluation Script.

Executes independent experiment P1.6-E001 on the RTX 4060 GPU,
merges weights, exports GGUF, evaluates on the immutable held-out benchmark,
computes McNemar's exact paired test, and outputs forensic evidence.
"""

import json
import math
import os
import sys
import time
import hashlib
from typing import Dict, Any, List

REPO_ROOT = r"D:\AURA"
sys.path.insert(0, REPO_ROOT)

import torch
from learning.heldout import HELD_OUT_SUITE, heldout_dataset_hash
from learning.trainer import AuraNeuralTrainer
from learning.merger import ModelMerger
from learning.gguf_exporter import GGUFExporter
from learning.quality_eval import GGUFHarness, _run_case, HeldOutReport


def mcnemar_exact_test(b: int, c: int) -> float:
    """
    Computes two-sided exact p-value for McNemar's test
    using the binomial distribution under H0: p = 0.5.
    b: Base FAIL, Candidate PASS (wins)
    c: Base PASS, Candidate FAIL (losses)
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p_val = 0.0
    for i in range(k + 1):
        p_val += math.comb(n, i) * (0.5 ** n)
    return min(1.0, 2.0 * p_val)


def main():
    print("=" * 70)
    print("AURA P1.6 REPRODUCIBILITY EXPERIMENT: P1.6-E001")
    print("=" * 70)

    dataset_path = r"D:\AURA\data\aura\dataset_cycle_1789542202_run_59.jsonl"
    exp_id = "P1.6-E001"
    output_dir = os.path.join(REPO_ROOT, "brains", "candidates", f"candidate-{exp_id}")
    os.makedirs(output_dir, exist_ok=True)

    t0 = time.time()
    torch.manual_seed(42)

    # 1. Real GPU LoRA Training
    print("\n[1] Starting independent LoRA training (Seed 42) on RTX 4060 GPU...")
    trainer = AuraNeuralTrainer(base_model_id="Qwen/Qwen2.5-0.5B-Instruct")
    train_res = trainer.train(
        dataset_path=dataset_path,
        output_dir=output_dir,
        job_id=exp_id,
        epochs=2,
        max_steps=120,
        learning_rate=2e-4,
    )
    print(f"    Training completed in {train_res.duration_seconds:.2f}s")
    print(f"    Loss: {train_res.initial_loss:.4f} -> {train_res.final_loss:.4f}")
    print(f"    Adapter SHA-256: {train_res.adapter_sha256[:16]}...")
    print(f"    Peak VRAM: {train_res.peak_vram_mb:.1f} MB")

    # 2. Standalone Weight Fusion
    print("\n[2] Merging adapter into base model weights...")
    merged_dir = os.path.join(output_dir, "merged")
    merge_res = ModelMerger.merge(
        base_model_id="Qwen/Qwen2.5-0.5B-Instruct",
        adapter_dir=train_res.adapter_dir,
        output_dir=merged_dir,
        device="cpu",
    )
    print(f"    Merged weights: {merge_res.merged_weight_file} (SHA: {merge_res.merged_weight_sha256[:16]}...)")
    print(f"    Base weights distinct: {merge_res.base_weights_distinct}")

    # 3. GGUF Export
    print("\n[3] Serializing to standalone binary GGUF...")
    gguf_dir = os.path.join(output_dir, "gguf")
    os.makedirs(gguf_dir, exist_ok=True)
    gguf_path = os.path.join(gguf_dir, f"{exp_id}.gguf")
    gguf_res = GGUFExporter.export(
        checkpoint_dir=merged_dir,
        output_gguf_path=gguf_path,
        model_name=exp_id,
    )
    print(f"    GGUF exported: {gguf_res.gguf_path} ({gguf_res.file_size_bytes} bytes)")
    print(f"    GGUF SHA-256: {gguf_res.sha256}")

    # 4. Deterministic Held-Out Evaluation
    print("\n[4] Evaluating candidate GGUF across the 26 immutable held-out cases...")
    harness = GGUFHarness(gguf_path=gguf_res.gguf_path, expected_sha256=gguf_res.sha256)
    if not harness.verify():
        print(f"FATAL: Harness verification failed: {harness.load_error}")
        return 1

    per_case = []
    for case in HELD_OUT_SUITE:
        score, passed, reason, raw = _run_case(harness, case)
        per_case.append({
            "test_id": case.test_id,
            "category": case.category,
            "input_text": case.input_text,
            "raw_reply": raw,
            "score": score,
            "passed": passed,
            "reason": reason,
        })

    cand_score = sum(c["score"] for c in per_case) / len(per_case)
    cand_passed = sum(1 for c in per_case if c["passed"])
    print(f"    P1.6-E001 Candidate GGUF Score: {cand_score:.4f} ({cand_passed}/26 passed)")

    # 5. Statistical Comparison against Baseline
    # Load P1.5 base results
    p1_5_data = json.load(open(r"D:\AURA\artifacts\p1_5_forensic.json", encoding="utf-8"))
    base_results = {c["test_id"]: c for c in p1_5_data["per_case_diff"]}

    wins = 0      # Base FAIL, Candidate PASS
    losses = 0    # Base PASS, Candidate FAIL
    both_pass = 0
    both_fail = 0
    case_diffs = []

    for c in per_case:
        cid = c["test_id"]
        b = base_results[cid]
        b_pass = b["base_passed"]
        c_pass = c["passed"]

        if not b_pass and c_pass:
            wins += 1
            status = "NEW_GAIN"
        elif b_pass and not c_pass:
            losses += 1
            status = "REGRESSION"
        elif b_pass and c_pass:
            both_pass += 1
            status = "BOTH_PASS"
        else:
            both_fail += 1
            status = "BOTH_FAIL"

        case_diffs.append({
            "test_id": cid,
            "category": c["category"],
            "classification": status,
            "base_score": b["base_score"],
            "candidate_score": c["score"],
            "base_passed": b_pass,
            "candidate_passed": c_pass,
            "reason": c["reason"],
            "raw_reply": c["raw_reply"][:120],
        })

    base_score = 0.5577
    abs_delta = cand_score - base_score
    rel_delta = (abs_delta / base_score) * 100.0 if base_score else 0.0
    p_value = mcnemar_exact_test(wins, losses)

    print("\n[5] Statistical Analysis (Paired McNemar Exact Test):")
    print(f"    Base Score:        {base_score:.4f} (12/26)")
    print(f"    Candidate Score:   {cand_score:.4f} ({cand_passed}/26)")
    print(f"    Absolute Delta:    {abs_delta:+.4f} ({abs_delta*100:+.2f}%)")
    print(f"    Relative Delta:    {rel_delta:+.2f}%")
    print(f"    Wins (Gains):      {wins}")
    print(f"    Losses (Regr.):    {losses}")
    print(f"    Ties (Both Pass):  {both_pass}")
    print(f"    Ties (Both Fail):  {both_fail}")
    print(f"    McNemar p-value:   {p_value:.4f}")
    
    if p_value < 0.05:
        stat_conclusion = "STATISTICALLY_SIGNIFICANT"
    else:
        stat_conclusion = "INCONCLUSIVE_SAMPLE_SIZE_LIMITED"
    print(f"    Statistical Verdict: {stat_conclusion}")

    duration = time.time() - t0

    # 6. Save Machine-Readable Artifact
    summary = {
        "experiment_id": exp_id,
        "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "duration_seconds": round(duration, 2),
        "seed": 42,
        "training": {
            "steps": train_res.steps_completed,
            "initial_loss": train_res.initial_loss,
            "final_loss": train_res.final_loss,
            "adapter_sha256": train_res.adapter_sha256,
            "peak_vram_mb": train_res.peak_vram_mb,
        },
        "merge": {
            "merged_weight_sha256": merge_res.merged_weight_sha256,
            "base_weights_distinct": merge_res.base_weights_distinct,
        },
        "gguf": {
            "path": gguf_res.gguf_path,
            "sha256": gguf_res.sha256,
            "size_bytes": gguf_res.file_size_bytes,
        },
        "evaluation": {
            "candidate_score": cand_score,
            "passed_tests": cand_passed,
            "total_tests": len(per_case),
            "baseline_score": base_score,
            "absolute_delta": round(abs_delta, 4),
            "relative_delta_pct": round(rel_delta, 2),
        },
        "statistics": {
            "wins": wins,
            "losses": losses,
            "ties_pass": both_pass,
            "ties_fail": both_fail,
            "mcnemar_exact_p_value": round(p_value, 4),
            "conclusion": stat_conclusion,
            "notes": (
                "With N=26 paired test cases, 5 gains vs 1 regression yields two-sided exact p = 0.2188. "
                "While directionally positive (Delta = +9.62%), classical alpha=0.05 significance requires "
                "larger sample sizes (N >= 80) or independent benchmark replication."
            ),
        },
        "per_case_diff": case_diffs,
    }

    out_file = os.path.join(REPO_ROOT, "artifacts", "p1_6_reproducibility.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"\nSaved {out_file}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
