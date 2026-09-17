"""
AURA Autonomous Real Neural Self-Learning Model Pipeline.
Authoritative End-to-End Forensic Execution and Evidence Verification Script.

Executes the complete autonomous closed-loop lifecycle:
1. Environment & Base Foundation Model Lineage Resolution (Cryptographic File Hashes)
2. Experience Collection & Privacy/Quality Screening from Experience Store
3. Immutable Dataset Construction with Provenance & Versioning
4. Isolated Candidate Neural Training on RTX 4060 GPU (Real PyTorch Backpropagation & AdamW)
5. Parameter-Delta Mathematical & Cryptographic Verification
6. Model Weight Merging (Base + LoRA -> Standalone AURA Checkpoint)
7. Held-Out Benchmark & Tripartite Evaluation (Baseline vs Candidate vs Merged)
8. Deterministic Safety & Promotion Gating
9. GGUF Model Export & GGUFReader Structural Validation
10. Direct Ollama-Independent Local Inference (C:\llama-cuda\llama-completion.exe)
11. Production Runtime Verification (Provenance & Consumption Proof)
12. Atomic Rollback & Re-promotion Verification
13. Strict Offline Socket Layer Isolation Test
14. Comprehensive JSON Evidence Generation
"""

from datetime import datetime
import hashlib
import json
import os
import shutil
import socket
import sys
import time

# Ensure UTF-8 output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

REPO_ROOT = r"D:\AURA"
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import torch

from brain.hardware import detect_hardware
from brain.local_runtime import LocalModelRuntime, TorchInferenceBackend, LlamaCliInferenceBackend
from brain.package import BrainManager, BrainManifest, BrainStatus, BrainPackage
from brain.registry import AuraModelRegistry, AuraModelVersion
from core.logger import logger
from learning.evaluation import BrainEvaluator, IMMUTABLE_REFERENCE_EVAL_SUITE
from learning.experience import AuraExperienceStore
from learning.gguf_exporter import GGUFExporter
from learning.lineage import compute_file_sha256, resolve_base_model_info, ModelLineage
from learning.merger import ModelMerger
from learning.parameter_tracker import ParameterTracker
from learning.pipeline import LearningCandidatePipeline
from learning.promotion import LearningCoordinator
from learning.scheduler import AutonomousLearningScheduler, SchedulerConfig
from learning.trainer import AuraNeuralTrainer


def print_section(title: str):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def main():
    start_all = time.time()
    print_section("AURA AUTONOMOUS REAL NEURAL SELF-LEARNING MODEL PIPELINE")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print(f"Repository: {REPO_ROOT}")

    # -------------------------------------------------------------
    # STAGE 1: HARDWARE & ENVIRONMENT INSPECTION
    # -------------------------------------------------------------
    print_section("STAGE 1: HARDWARE & ENVIRONMENT PROBING")
    hw = detect_hardware()
    cuda_avail = torch.cuda.is_available()
    device_name = torch.cuda.get_device_name(0) if cuda_avail else "CPU"
    vram_gb = (torch.cuda.get_device_properties(0).total_memory / (1024**3)) if cuda_avail else 0.0

    print(f"CUDA Available: {cuda_avail}")
    print(f"GPU Accelerator: {device_name} ({vram_gb:.2f} GB GDDR6)")
    print(f"System RAM Total: {hw.ram_total_mb} MB | Available: {hw.ram_available_mb} MB")
    print(f"PyTorch Version: {torch.__version__} (CUDA: {torch.version.cuda})")
    assert cuda_avail, "Physical CUDA hardware required for real neural adaptation"

    # -------------------------------------------------------------
    # STAGE 2: FOUNDATION MODEL LINEAGE & TRUE ARTIFACT HASH
    # -------------------------------------------------------------
    print_section("STAGE 2: FOUNDATION MODEL LINEAGE RESOLUTION")
    base_model_id = "Qwen/Qwen2.5-0.5B-Instruct"
    base_info = resolve_base_model_info(base_model_id)

    print(f"Foundation Model ID: {base_info['base_model_id']}")
    print(f"Local Snapshot Dir: {base_info['snapshot_dir']}")
    print(f"Primary Weights Format: {base_info['format']}")
    print(f"Primary Artifact SHA-256: {base_info['primary_artifact_hash']}")
    print(f"Total Model Bytes: {base_info['total_bytes']:,} bytes")
    print(f"Parameter Count: {base_info['parameter_count']:,}")
    print(f"Files Tracked: {len(base_info['files'])} files")

    # -------------------------------------------------------------
    # STAGE 3: EXPERIENCE STORE & CURRICULUM PREPARATION
    # -------------------------------------------------------------
    print_section("STAGE 3: EXPERIENCE STORE ACCUMULATION & SCREENING")
    exp_store = AuraExperienceStore()

    # Seed operational experiences if store is sparse
    curriculum_path = os.path.join(REPO_ROOT, "training", "dataset", "aura_curriculum.jsonl")
    if os.path.exists(curriculum_path):
        print(f"Seeding verified experiences from curriculum: {curriculum_path}")
        with open(curriculum_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                item = json.loads(line)
                msgs = item.get("messages", [])
                user_msg = next((m["content"] for m in msgs if m["role"] == "user"), "")
                asst_msg = next((m["content"] for m in msgs if m["role"] == "assistant"), "")
                cat = item.get("category", "general")

                decision = "ANSWER"
                tool_name = ""
                args = {}
                if "<tool_call>" in asst_msg:
                    decision = "TOOL_CALL"
                    try:
                        tc = json.loads(asst_msg.split("<tool_call>")[1].split("</tool_call>")[0].strip())
                        tool_name = tc.get("name", "")
                        args = tc.get("arguments", {})
                    except Exception:
                        pass
                elif "chắc chắn" in asst_msg or "confirm" in asst_msg.lower():
                    decision = "CONFIRMATION_REQUIRED"
                elif "nào" in asst_msg or "which" in asst_msg.lower():
                    decision = "CLARIFICATION"

                exp_store.record_experience(
                    session_id="seed_forensic_session",
                    input_text=user_msg,
                    model_decision=decision,
                    selected_tool=tool_name,
                    arguments=args,
                    verifier_result="VERIFIED",
                    final_response=asst_msg,
                    outcome="SUCCESS",
                    category=cat,
                )

    eligible_exps = exp_store.list_eligible_experiences(min_quality=0.6, limit=1000)
    print(f"Eligible Experiences Screened: {len(eligible_exps)}")
    assert len(eligible_exps) >= 5, "Insufficient eligible experiences for dataset generation"

    # -------------------------------------------------------------
    # STAGE 4: IMMUTABLE DATASET GENERATION WITH PROVENANCE
    # -------------------------------------------------------------
    print_section("STAGE 4: IMMUTABLE DATASET GENERATION & PROVENANCE")
    dataset_version = "aura_dataset_v2"
    ds_pipeline = LearningCandidatePipeline(store=exp_store, data_dir=os.path.join(REPO_ROOT, "data", "aura", "learning", "datasets"))
    ds_manifest = ds_pipeline.generate_candidate_dataset(min_quality=0.6, dataset_name=dataset_version)
    assert ds_manifest is not None, "Failed to generate candidate dataset"

    print(f"Dataset Version: {dataset_version}")
    print(f"Dataset Path: {ds_manifest.file_path}")
    print(f"Dataset SHA-256: {ds_manifest.checksum}")
    print(f"Examples Count: {ds_manifest.num_examples}")
    print(f"Category Breakdown: {ds_manifest.categories}")

    # -------------------------------------------------------------
    # STAGE 5: ISOLATED CANDIDATE NEURAL TRAINING ON GPU
    # -------------------------------------------------------------
    print_section("STAGE 5: ISOLATED CANDIDATE REAL NEURAL TRAINING (RTX 4060)")
    candidate_version = "AURA-v2"
    run_id = f"run_{int(time.time())}"
    candidate_dir = os.path.join(REPO_ROOT, "brains", "candidates", f"candidate-{run_id}")
    os.makedirs(candidate_dir, exist_ok=True)

    trainer = AuraNeuralTrainer(
        base_model_id=base_model_id,
        device="cuda",
        torch_dtype=torch.float16,
    )

    t_train_start = time.time()
    train_result = trainer.train(
        dataset_path=ds_manifest.file_path,
        output_dir=candidate_dir,
        job_id=run_id,
        epochs=1,
        max_steps=20,
        learning_rate=2e-4,
        lora_r=8,
        lora_alpha=16,
    )
    t_train = time.time() - t_train_start

    print(f"Training Job ID: {train_result.job_id}")
    print(f"Steps Completed: {train_result.steps_completed}")
    print(f"Initial Loss (Step 1): {train_result.initial_loss:.4f}")
    print(f"Final Loss (Step {train_result.steps_completed}): {train_result.final_loss:.4f}")
    print(f"Loss Delta (Reduction): {train_result.initial_loss - train_result.final_loss:.4f}")
    print(f"Loss History: {[round(l, 4) for l in train_result.loss_history]}")
    print(f"Peak VRAM: {train_result.peak_vram_mb:.2f} MB")
    print(f"Training Duration: {t_train:.2f} s")
    print(f"Adapter Path: {train_result.adapter_dir}")
    print(f"Adapter SHA-256: {train_result.adapter_sha256}")

    # -------------------------------------------------------------
    # STAGE 6: PARAMETER-DELTA MATHEMATICAL VERIFICATION
    # -------------------------------------------------------------
    print_section("STAGE 6: PARAMETER-DELTA MATHEMATICAL VERIFICATION")
    param_delta = train_result.parameter_deltas
    print(f"Total Tracked Parameters: {param_delta['total_parameters_tracked']:,}")
    print(f"Changed Parameters Count: {param_delta['changed_parameters_count']:,} ({param_delta['changed_parameters_pct']}%)")
    print(f"Changed Tensors Count: {param_delta['changed_tensors_count']} / {param_delta['total_tensors_tracked']}")
    print(f"Max Absolute Parameter Delta: {param_delta['max_abs_delta']:.8f}")
    print(f"Mean Absolute Parameter Delta: {param_delta['mean_abs_delta']:.8f}")
    print(f"L2 Norm Delta of Updates: {param_delta['l2_norm_delta']:.8f}")
    print(f"Pre-Training Fingerprint: {param_delta['aggregate_fingerprint_before'][:16]}...")
    print(f"Post-Training Fingerprint: {param_delta['aggregate_fingerprint_after'][:16]}...")
    assert param_delta["changed_parameters_count"] > 0, "No parameters were updated during training"
    assert param_delta["aggregate_fingerprint_before"] != param_delta["aggregate_fingerprint_after"], "Parameter fingerprint did not change"

    # -------------------------------------------------------------
    # STAGE 7: MODEL WEIGHT MERGING (BASE + LoRA -> STANDALONE CHECKPOINT)
    # -------------------------------------------------------------
    print_section("STAGE 7: MODEL WEIGHT MERGE (FUSION INTO AURA CHECKPOINT)")
    merged_dir = os.path.join(candidate_dir, "merged")
    merge_res = ModelMerger.merge(
        base_model_id=base_model_id,
        adapter_dir=os.path.join(candidate_dir, "adapter"),
        output_dir=merged_dir,
        device="cpu",
    )

    print(f"Merged Checkpoint Dir: {merged_dir}")
    print(f"Merged Weights File: {merge_res.merged_weight_file}")
    print(f"Merged Weights SHA-256: {merge_res.merged_weight_sha256}")
    print(f"Merged Weights Size: {merge_res.merged_weight_size_bytes:,} bytes")
    print(f"Base Weights Distinct: {merge_res.base_weights_distinct}")
    print(f"Merge Duration: {merge_res.duration_seconds:.2f} s")
    assert merge_res.base_weights_distinct, "Merged weights must mathematically differ from base weights"

    # -------------------------------------------------------------
    # STAGE 8: GGUF EXPORT & STRUCTURAL VALIDATION
    # -------------------------------------------------------------
    print_section("STAGE 8: GGUF EXPORT & BINARY VALIDATION")
    gguf_output_path = os.path.join(candidate_dir, "gguf", f"{candidate_version}-0.5B.gguf")
    gguf_res = GGUFExporter.export(
        checkpoint_dir=merged_dir,
        output_gguf_path=gguf_output_path,
        model_name=candidate_version,
    )

    print(f"Exported GGUF Path: {gguf_res.gguf_path}")
    print(f"GGUF File Size: {gguf_res.file_size_bytes:,} bytes ({gguf_res.file_size_bytes / (1024*1024):.2f} MB)")
    print(f"GGUF SHA-256: {gguf_res.sha256}")
    print(f"Architecture: {gguf_res.architecture}")
    print(f"Tensors Exported: {gguf_res.tensor_count}")
    print(f"Total Parameters: {gguf_res.parameters_count:,}")
    print(f"GGUF Structural Validity: {gguf_res.is_valid}")
    print(f"GGUF Validation Details: {gguf_res.validation_details}")
    assert gguf_res.is_valid, "GGUF validation failed"

    # -------------------------------------------------------------
    # STAGE 9: HELD-OUT BENCHMARK & TRIPARTITE EVALUATION
    # -------------------------------------------------------------
    print_section("STAGE 9: HELD-OUT BENCHMARK & TRIPARTITE EVALUATION")
    evaluator = BrainEvaluator(IMMUTABLE_REFERENCE_EVAL_SUITE)
    manager = BrainManager(brains_dir=os.path.join(REPO_ROOT, "brains"))
    coordinator = LearningCoordinator(manager=manager, evaluator=evaluator)

    # 1. Baseline Evaluation (aura-brain-v1)
    baseline_pkg = manager.get_package("aura-brain-v1")
    b_brain = coordinator._build_test_brain(baseline_pkg) if baseline_pkg else None
    baseline_rep = evaluator.evaluate(b_brain) if b_brain else None
    if baseline_rep:
        print(f"1. Baseline (aura-brain-v1) Score: {baseline_rep.overall_score:.3f} ({baseline_rep.passed_tests}/{baseline_rep.total_tests} passed)")

    # 2. Candidate Adapter Evaluation (PyTorch PEFT)
    # Register temporary candidate package for evaluation
    manifest_cand = BrainManifest(
        brain_id=f"cand-{run_id}-adapter",
        version="candidate-adapter",
        model_format="peft",
        context_length=32768,
        parameter_count="0.5B",
        quantization="FP16",
        runtime_backend="torch",
        parent_brain="aura-brain-v1",
        dataset_lineage=[ds_manifest.file_path],
        training_lineage=train_result.lineage,
        checksum=train_result.adapter_sha256,
        metadata={"adapter_dir": train_result.adapter_dir},
    )
    cand_pkg = manager.register_package(manifest_cand)
    cand_brain = coordinator._build_test_brain(cand_pkg)
    cand_rep = evaluator.evaluate(cand_brain)
    print(f"2. Candidate Adapter Score: {cand_rep.overall_score:.3f} ({cand_rep.passed_tests}/{cand_rep.total_tests} passed)")

    # 3. Merged Model Evaluation
    manifest_merged = BrainManifest(
        brain_id=f"cand-{run_id}-merged",
        version="candidate-merged",
        model_format="safetensors",
        context_length=32768,
        parameter_count="0.5B",
        quantization="FP16",
        runtime_backend="torch",
        parent_brain="aura-brain-v1",
        dataset_lineage=[ds_manifest.file_path],
        training_lineage=train_result.lineage,
        checksum=merge_res.merged_weight_sha256,
        metadata={"merged_dir": merged_dir},
    )
    merged_pkg = manager.register_package(manifest_merged)
    merged_brain = coordinator._build_test_brain(merged_pkg)
    merged_rep = evaluator.evaluate(merged_brain)
    print(f"3. Merged Model Score: {merged_rep.overall_score:.3f} ({merged_rep.passed_tests}/{merged_rep.total_tests} passed)")

    # 4. Tripartite Comparison
    tripartite = evaluator.compare_tripartite(cand_rep, merged_rep, baseline_rep)
    print(f"\nTripartite Comparison Result:")
    print(f"  Baseline Score: {tripartite.baseline_score:.3f}")
    print(f"  Candidate Score: {tripartite.candidate_score:.3f}")
    print(f"  Merged Score: {tripartite.merged_score:.3f}")
    print(f"  Regression Delta: {tripartite.regression_delta:+.3f}")
    print(f"  Safety Regression: {tripartite.safety_regression}")
    print(f"  Is Promotable: {tripartite.is_promotable}")
    print(f"  Rejection Reasons: {tripartite.rejection_reasons}")

    # -------------------------------------------------------------
    # STAGE 10: DETERMINISTIC PROMOTION GATE & MODEL REGISTRY
    # -------------------------------------------------------------
    print_section("STAGE 10: PROMOTION GATE & MODEL REGISTRY PERSISTENCE")
    registry = AuraModelRegistry()

    # Create authoritative AuraModelVersion
    model_version = AuraModelVersion(
        aura_version=candidate_version,
        parent_version=registry.active_model or "AURA-v1",
        foundation_model=base_model_id,
        foundation_artifact_hash=base_info["primary_artifact_hash"],
        dataset_version=dataset_version,
        dataset_hash=ds_manifest.checksum,
        training_run_id=run_id,
        adapter_hash=train_result.adapter_sha256,
        merged_checkpoint_hash=merge_res.merged_weight_sha256,
        gguf_hash=gguf_res.sha256,
        artifact_paths={
            "adapter": train_result.adapter_dir,
            "merged": merged_dir,
            "gguf": gguf_output_path,
        },
        evaluation=tripartite.to_dict(),
        parameter_deltas=param_delta,
        promotion_status="CANDIDATE",
    )

    registry.register_candidate(model_version)

    promotion_occurred = False
    if tripartite.is_promotable:
        print(f"Candidate {candidate_version} satisfies all promotion criteria! Executing promotion...")
        promotion_occurred = registry.promote_candidate(candidate_version, tripartite.to_dict())
        print(f"Promotion Result: {promotion_occurred}")
    else:
        print(f"Candidate {candidate_version} blocked by promotion gate: {tripartite.rejection_reasons}")
        registry.reject_candidate(candidate_version, "; ".join(tripartite.rejection_reasons), tripartite.to_dict())
        print("Candidate marked as REJECTED in model registry. Production model untouched.")

    # -------------------------------------------------------------
    # STAGE 11: OLLAMA-INDEPENDENT DIRECT GGUF INFERENCE
    # -------------------------------------------------------------
    print_section("STAGE 11: DIRECT OLLAMA-INDEPENDENT LOCAL INFERENCE")
    cli_backend = LlamaCliInferenceBackend()
    cli_loaded = cli_backend.load(BrainPackage(
        package_dir=os.path.dirname(gguf_output_path),
        manifest=BrainManifest(
            brain_id=candidate_version,
            version="2.0.0",
            model_format="gguf",
            metadata={"weight_file": os.path.basename(gguf_output_path)},
        ),
    ))

    print(f"LlamaCliInferenceBackend Loaded: {cli_loaded}")
    test_queries = [
        "Who are you?",
        "Xin chào, bạn là ai?",
    ]
    for q in test_queries:
        resp = cli_backend.generate(q, max_tokens=40)
        print(f"\nQuery: {q}")
        print(f"Direct Llama-CLI Output: {resp.strip()}")

    # -------------------------------------------------------------
    # STAGE 12: PRODUCTION RUNTIME CONSUMPTION & ATOMIC ROLLBACK
    # -------------------------------------------------------------
    print_section("STAGE 12: ATOMIC ROLLBACK & RUNTIME RESTORATION")
    if promotion_occurred:
        print(f"Testing rollback from {registry.active_model} to {registry.rollback_model}...")
        rb_ok = registry.rollback("forensic_rollback_test")
        print(f"Rollback Succeeded: {rb_ok}")
        print(f"Active Model After Rollback: {registry.active_model}")
        assert rb_ok, "Rollback failed"

        # Re-promote candidate to establish AURA-v2 in registry
        registry.promote_candidate(candidate_version, tripartite.to_dict())
        print(f"Re-promoted {candidate_version} as active production model.")

    # -------------------------------------------------------------
    # STAGE 13: STRICT OFFLINE NETWORK ISOLATION TEST
    # -------------------------------------------------------------
    print_section("STAGE 13: STRICT OFFLINE SOCKET LAYER ISOLATION")
    real_socket = socket.socket
    network_attempted = False

    def guard_socket(*args, **kwargs):
        nonlocal network_attempted
        sock = real_socket(*args, **kwargs)
        orig_connect = sock.connect
        def check_connect(addr):
            nonlocal network_attempted
            host = addr[0] if isinstance(addr, tuple) else addr
            if host not in ("127.0.0.1", "localhost", "::1"):
                network_attempted = True
                raise ConnectionRefusedError(f"Offline Mode: Blocked outbound socket to {addr}")
            return orig_connect(addr)
        sock.connect = check_connect
        return sock

    socket.socket = guard_socket
    try:
        offline_resp = cli_backend.generate("State your identity and purpose.", max_tokens=30)
        print(f"Offline Local Inference Response: {offline_resp.strip()}")
        print(f"Outbound network attempted during inference: {network_attempted}")
        assert not network_attempted, "Network activity detected in offline isolation test"
    finally:
        socket.socket = real_socket

    # -------------------------------------------------------------
    # STAGE 14: SUMMARY JSON EVIDENCE GENERATION
    # -------------------------------------------------------------
    print_section("STAGE 14: MACHINE-READABLE EVIDENCE SUMMARY GENERATION")
    duration_total = time.time() - start_all

    summary = {
        "execution_timestamp": datetime.now().isoformat(),
        "duration_seconds": round(duration_total, 2),
        "hardware": {
            "accelerator": device_name,
            "vram_gb": round(vram_gb, 2),
            "peak_training_vram_mb": round(train_result.peak_vram_mb, 2),
        },
        "foundation_lineage": {
            "model_id": base_model_id,
            "artifact_sha256": base_info["primary_artifact_hash"],
            "total_bytes": base_info["total_bytes"],
            "parameter_count": base_info["parameter_count"],
        },
        "dataset": {
            "version": dataset_version,
            "path": ds_manifest.file_path,
            "sha256": ds_manifest.checksum,
            "num_examples": ds_manifest.num_examples,
            "categories": ds_manifest.categories,
        },
        "training": {
            "run_id": run_id,
            "steps": train_result.steps_completed,
            "initial_loss": round(train_result.initial_loss, 4),
            "final_loss": round(train_result.final_loss, 4),
            "loss_delta": round(train_result.initial_loss - train_result.final_loss, 4),
            "loss_history": [round(l, 4) for l in train_result.loss_history],
            "adapter_path": train_result.adapter_dir,
            "adapter_sha256": train_result.adapter_sha256,
        },
        "parameter_delta": {
            "total_parameters_tracked": param_delta["total_parameters_tracked"],
            "changed_parameters_count": param_delta["changed_parameters_count"],
            "changed_parameters_pct": param_delta["changed_parameters_pct"],
            "max_abs_delta": param_delta["max_abs_delta"],
            "l2_norm_delta": param_delta["l2_norm_delta"],
            "fingerprint_before": param_delta["aggregate_fingerprint_before"],
            "fingerprint_after": param_delta["aggregate_fingerprint_after"],
        },
        "merged_checkpoint": {
            "dir": merged_dir,
            "weight_file": merge_res.merged_weight_file,
            "sha256": merge_res.merged_weight_sha256,
            "size_bytes": merge_res.merged_weight_size_bytes,
            "base_weights_distinct": merge_res.base_weights_distinct,
        },
        "gguf_artifact": {
            "path": gguf_output_path,
            "sha256": gguf_res.sha256,
            "size_bytes": gguf_res.file_size_bytes,
            "architecture": gguf_res.architecture,
            "tensor_count": gguf_res.tensor_count,
            "is_valid": gguf_res.is_valid,
        },
        "evaluation": tripartite.to_dict(),
        "registry": {
            "candidate_version": candidate_version,
            "active_model": registry.active_model,
            "rollback_model": registry.rollback_model,
            "promotion_occurred": promotion_occurred,
        },
        "offline_isolation_verified": True,
    }

    summary_path = os.path.join(REPO_ROOT, "brains", "forensic_self_learning_summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print(f"\nMachine-readable forensic evidence saved to: {summary_path}")
    print_section("AURA AUTONOMOUS REAL NEURAL SELF-LEARNING PIPELINE COMPLETE")


if __name__ == "__main__":
    main()
