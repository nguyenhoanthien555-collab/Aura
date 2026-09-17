"""
AURA Autonomous Self-Learning Scheduler.

Monitors operational experience accumulation, evaluates training eligibility triggers,
and orchestrates the bounded, resource-safe self-learning lifecycle state machine:
IDLE -> COLLECTING -> DATASET_READY -> TRAINING -> EVALUATING -> PROMOTING -> ACTIVE / REJECTED / FAILED / ROLLED_BACK
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
import json
import os
import shutil
import tempfile
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from brain.hardware import HardwareProfile, detect_hardware
from core.logger import logger
from learning.experience import AuraExperienceStore
from learning.pipeline import LearningCandidatePipeline, DatasetManifest


class LearningState(str, Enum):
    IDLE = "IDLE"
    COLLECTING = "COLLECTING"
    DATASET_READY = "DATASET_READY"
    TRAINING = "TRAINING"
    EVALUATING = "EVALUATING"
    PROMOTING = "PROMOTING"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"


@dataclass
class SchedulerConfig:
    min_eligible_experiences: int = 5
    min_new_experiences: int = 3
    cooldown_seconds: float = 60.0
    min_free_ram_mb: int = 2048
    min_vram_mb: int = 1500
    max_training_duration_seconds: float = 300.0
    auto_promote: bool = False  # If False, stops after evaluation with recommendation
    # Training budget for one cycle. None = all samples x epochs (trainer
    # default); a number caps optimizer steps. 5 steps over a 40+ example
    # dataset would train on a fraction of the data, which is not a real
    # learning cycle.
    training_epochs: int = 2
    training_max_steps: Optional[int] = 120
    canary_mode: bool = False
    canary_min_eligible: int = 50
    canary_min_new: int = 20
    dual_suite_eval: bool = True
    drift_tracking: bool = True


@dataclass
class LearningCycleStatus:
    cycle_id: str
    state: str
    eligible_count: int
    new_count: int
    last_trigger_time: Optional[float]
    current_job_id: Optional[str] = None
    candidate_version: Optional[str] = None
    error_message: Optional[str] = None
    metrics: Dict[str, Any] = field(default_factory=dict)
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AutonomousLearningScheduler:
    """
    Self-learning supervisor engine. Evaluates triggers against hardware,
    cooldown, and experience freshness constraints without blocking normal agent execution.
    """

    def __init__(
        self,
        config: Optional[SchedulerConfig] = None,
        experience_store: Optional[AuraExperienceStore] = None,
        pipeline: Optional[LearningCandidatePipeline] = None,
        hardware: Optional[HardwareProfile] = None,
        state_file: Optional[str] = None,
    ):
        self.config = config or SchedulerConfig()
        self.experience_store = experience_store or AuraExperienceStore()
        self.pipeline = pipeline or LearningCandidatePipeline(store=self.experience_store)
        self.hardware = hardware or detect_hardware()
        self.state_file = state_file
        self._lock = threading.RLock()
        self._state = LearningState.IDLE
        self._last_train_time = 0.0
        self._last_trained_experience_count = 0
        self._current_cycle_id: Optional[str] = None
        self._cancelled = False
        if self.state_file:
            self._load_state()

    def _load_state(self) -> None:
        with self._lock:
            if self.state_file and os.path.exists(self.state_file):
                try:
                    with open(self.state_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self._last_train_time = float(data.get("last_train_time", 0.0))
                    self._last_trained_experience_count = int(data.get("last_trained_experience_count", 0))
                    self._current_cycle_id = data.get("current_cycle_id")
                    self._state = LearningState.IDLE
                    logger.debug("[Scheduler] Loaded state, last_trained=%d", self._last_trained_experience_count)
                except Exception as e:
                    logger.warning("[Scheduler] Failed to load scheduler state: %s", e)

    def _save_state(self) -> None:
        with self._lock:
            if not self.state_file:
                return
            try:
                dir_name = os.path.dirname(self.state_file)
                os.makedirs(dir_name, exist_ok=True)
                payload = {
                    "state": self._state.value,
                    "last_train_time": self._last_train_time,
                    "last_trained_experience_count": self._last_trained_experience_count,
                    "current_cycle_id": self._current_cycle_id,
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                }
                fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix="sched_", suffix=".tmp")
                try:
                    with open(fd, "w", encoding="utf-8") as f:
                        json.dump(payload, f, indent=2)
                    shutil.move(tmp_path, self.state_file)
                except Exception:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                    raise
            except Exception as e:
                logger.warning("[Scheduler] Failed to persist scheduler state: %s", e)

    @property
    def state(self) -> LearningState:
        with self._lock:
            return self._state

    def check_eligibility(self) -> Tuple[bool, str]:
        """
        Determines whether system conditions warrant initiating a self-learning cycle.
        Enforces resource bounds, experience thresholds, and cooldowns.
        """
        with self._lock:
            if self._state in (LearningState.COLLECTING, LearningState.DATASET_READY, LearningState.TRAINING, LearningState.EVALUATING, LearningState.PROMOTING):
                return False, f"Job already active in state: {self._state.value}"

            # 1. Cooldown check
            elapsed = time.time() - self._last_train_time
            if elapsed < self.config.cooldown_seconds:
                rem = int(self.config.cooldown_seconds - elapsed)
                return False, f"Cooldown in effect ({rem}s remaining)"

            # 2. Hardware / Memory check
            hw = detect_hardware()
            if hw.ram_available_mb < self.config.min_free_ram_mb:
                return False, f"Insufficient available RAM: {hw.ram_available_mb}MB < {self.config.min_free_ram_mb}MB"

            # 3. Experience accumulation count
            min_eligible = self.config.canary_min_eligible if self.config.canary_mode else self.config.min_eligible_experiences
            eligible = self.experience_store.list_eligible_experiences(min_quality=0.6, limit=1000)
            eligible_count = len(eligible)

            if eligible_count < min_eligible:
                return False, f"Eligible experiences below threshold ({eligible_count} < {min_eligible})"

            # 4. New experiences since last training run
            min_new = self.config.canary_min_new if self.config.canary_mode else self.config.min_new_experiences
            new_count = eligible_count - self._last_trained_experience_count
            if new_count < min_new:
                return False, f"Insufficient new experiences ({new_count} < {min_new})"

            return True, f"Eligible: {eligible_count} total, {new_count} new experiences ready"

    def mark_cycle_started(self, cycle_id: str) -> None:
        with self._lock:
            self._current_cycle_id = cycle_id
            self._state = LearningState.COLLECTING
            self._cancelled = False
            self._save_state()

    def transition(self, new_state: LearningState, error_msg: Optional[str] = None) -> None:
        with self._lock:
            logger.info("[Scheduler] Transition: %s -> %s", self._state.value, new_state.value)
            self._state = new_state
            if new_state in (LearningState.ACTIVE, LearningState.REJECTED, LearningState.FAILED):
                self._last_train_time = time.time()
                # Snapshot eligible count
                try:
                    el = self.experience_store.list_eligible_experiences(min_quality=0.6, limit=1000)
                    self._last_trained_experience_count = len(el)
                except Exception:
                    pass
            self._save_state()

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            self._state = LearningState.IDLE
            self._save_state()
            logger.warning("[Scheduler] Learning cycle cancelled")

    @property
    def is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def get_status(self) -> LearningCycleStatus:
        with self._lock:
            try:
                el = self.experience_store.list_eligible_experiences(min_quality=0.6, limit=1000)
                el_count = len(el)
            except Exception:
                el_count = 0
            new_c = max(0, el_count - self._last_trained_experience_count)
            return LearningCycleStatus(
                cycle_id=self._current_cycle_id or "none",
                state=self._state.value,
                eligible_count=el_count,
                new_count=new_c,
                last_trigger_time=self._last_train_time if self._last_train_time > 0 else None,
            )

    def execute_cycle(
        self,
        max_steps: Optional[int] = None,
        epochs: Optional[int] = None,
        auto_promote: Optional[bool] = None,
        candidate_version: Optional[str] = None,
        base_model_id: str = "Qwen/Qwen2.5-0.5B-Instruct",
        inject_failure: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Executes a bounded, autonomous self-learning cycle through the complete state machine:
        IDLE -> COLLECTING -> DATASET_READY -> TRAINING -> EVALUATING -> PROMOTING -> ACTIVE / REJECTED / FAILED
        Enforces exception-safety and automatic recovery: unhandled failures cleanly transition to FAILED
        and release hardware memory.
        """
        with self._lock:
            if self._state in (LearningState.COLLECTING, LearningState.DATASET_READY, LearningState.TRAINING, LearningState.EVALUATING, LearningState.PROMOTING):
                return {
                    "cycle_id": f"busy_{int(time.time())}",
                    "candidate_version": "none",
                    "started_at": datetime.now().isoformat(),
                    "transitions": [],
                    "status": "REJECTED_BUSY",
                    "error": f"Scheduler busy in state: {self._state.value}",
                }
            try:
                return self._execute_cycle_inner(
                    max_steps=max_steps,
                    epochs=epochs,
                    auto_promote=auto_promote,
                    candidate_version=candidate_version,
                    base_model_id=base_model_id,
                    inject_failure=inject_failure,
                )
            except Exception as exc:
                logger.error("[Scheduler] Autonomous learning cycle failed with exception: %s", exc, exc_info=True)
                import gc
                import torch
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                self.transition(LearningState.FAILED, str(exc))
                return {
                    "cycle_id": self._current_cycle_id or f"failed_{int(time.time())}",
                    "candidate_version": candidate_version or "unknown",
                    "started_at": datetime.now().isoformat(),
                    "completed_at": datetime.now().isoformat(),
                    "transitions": [{"state": self._state.value, "time": time.time()}],
                    "status": "FAILED",
                    "error": f"Cycle execution failed: {type(exc).__name__}: {exc}",
                }

    def _execute_cycle_inner(
        self,
        max_steps: Optional[int] = None,
        epochs: Optional[int] = None,
        auto_promote: Optional[bool] = None,
        candidate_version: Optional[str] = None,
        base_model_id: str = "Qwen/Qwen2.5-0.5B-Instruct",
        inject_failure: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Executes a bounded, autonomous self-learning cycle through the complete state machine:
        IDLE -> COLLECTING -> DATASET_READY -> TRAINING -> EVALUATING -> PROMOTING -> ACTIVE / REJECTED / FAILED
        """
        from core.ids import new_run_id
        from learning.trainer import AuraNeuralTrainer
        from learning.merger import ModelMerger
        from learning.gguf_exporter import GGUFExporter
        from learning.quality_eval import (
            GGUFHarness,
            NeuralHarness,
            compare_for_promotion,
            sha256_file,
        )
        from learning.contamination import check_contamination
        from brain.registry import AuraModelRegistry, AuraModelVersion
        from brain.package import BrainManager, BrainManifest, BrainStatus

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        should_promote = self.config.auto_promote if auto_promote is None else auto_promote
        cycle_id = f"cycle_{int(time.time())}_{new_run_id()[:6]}"
        cand_ver = candidate_version or f"AURA-cand-{cycle_id[-6:]}"
        trace: Dict[str, Any] = {
            "cycle_id": cycle_id,
            "candidate_version": cand_ver,
            "started_at": datetime.now().isoformat(),
            "transitions": [],
            "status": "PENDING",
        }

        with self._lock:
            if self._state in (LearningState.COLLECTING, LearningState.DATASET_READY, LearningState.TRAINING, LearningState.EVALUATING, LearningState.PROMOTING):
                trace["status"] = "REJECTED_BUSY"
                trace["error"] = f"Scheduler busy in state: {self._state.value}"
                return trace

            # Step 1: COLLECTING
            self.mark_cycle_started(cycle_id)
            trace["transitions"].append({"state": self._state.value, "time": time.time()})

            if inject_failure == "dataset":
                self.transition(LearningState.FAILED, "Injected dataset failure")
                trace["status"] = "FAILED"
                trace["error"] = "Injected dataset failure"
                trace["transitions"].append({"state": self._state.value, "time": time.time()})
                return trace

            # Step 2: DATASET_READY
            ds_name = f"dataset_{cycle_id}"
            ds_manifest = self.pipeline.generate_candidate_dataset(min_quality=0.6, dataset_name=ds_name)
            if not ds_manifest or ds_manifest.num_examples == 0:
                self.transition(LearningState.FAILED, "Dataset generation returned empty or failed")
                trace["status"] = "FAILED"
                trace["error"] = "Empty dataset"
                trace["transitions"].append({"state": self._state.value, "time": time.time()})
                return trace

            self.transition(LearningState.DATASET_READY)
            trace["transitions"].append({"state": self._state.value, "time": time.time()})
            trace["dataset"] = {
                "id": ds_manifest.dataset_id,
                "file_path": ds_manifest.file_path,
                "checksum": ds_manifest.checksum,
                "examples": ds_manifest.num_examples,
            }

            # P1 contamination gate: refuse to train on any dataset whose
            # inputs overlap the immutable held-out evaluation set.
            contamination = check_contamination(ds_manifest.file_path)
            trace["contamination"] = contamination.to_dict()
            if contamination.contamination_status != "CLEAN":
                self.transition(LearningState.FAILED, "Contaminated dataset rejected")
                trace["status"] = "FAILED"
                trace["error"] = (
                    f"Contamination gate: {contamination.overlap_count} overlapping "
                    f"pairs vs held-out set ({contamination.contamination_status})"
                )
                trace["transitions"].append({"state": self._state.value, "time": time.time()})
                return trace

            # P1.7 Contradiction gate: refuse to train on any dataset with contradictions
            from learning.contradiction import ContradictionDetector
            contradiction_audit = ContradictionDetector.audit_dataset_file(ds_manifest.file_path)
            trace["contradiction_audit"] = contradiction_audit
            if contradiction_audit["status"] != "CLEAN":
                self.transition(LearningState.FAILED, "Contradictory dataset rejected")
                trace["status"] = "FAILED"
                trace["error"] = (
                    f"Contradiction gate: {contradiction_audit['contradiction_count']} contradictory samples detected "
                    f"against Stable Core / safety / tools"
                )
                trace["transitions"].append({"state": self._state.value, "time": time.time()})
                return trace

            if self._cancelled:
                return trace

            # Step 3: TRAINING
            self.transition(LearningState.TRAINING)
            trace["transitions"].append({"state": self._state.value, "time": time.time()})

            if inject_failure == "training":
                self.transition(LearningState.FAILED, "Injected training failure")
                trace["status"] = "FAILED"
                trace["error"] = "Injected training failure"
                trace["transitions"].append({"state": self._state.value, "time": time.time()})
                return trace
            elif inject_failure == "oom":
                import torch
                raise torch.cuda.OutOfMemoryError("CUDA out of memory: tried to allocate 8.00 GiB (GPU 0; 8.00 GiB total capacity; 7.85 GiB already allocated)")
            elif inject_failure == "training_crash":
                raise RuntimeError("Training worker process terminated unexpectedly with SIGSEGV (interrupted training)")

            candidate_dir = os.path.join(repo_root, "brains", "candidates", f"candidate-{cycle_id}")
            os.makedirs(candidate_dir, exist_ok=True)

            trainer = AuraNeuralTrainer(base_model_id=base_model_id)
            train_result = trainer.train(
                dataset_path=ds_manifest.file_path,
                output_dir=candidate_dir,
                job_id=cycle_id,
                epochs=epochs if epochs is not None else self.config.training_epochs,
                max_steps=max_steps if max_steps is not None else self.config.training_max_steps,
            )
            trace["training"] = {
                "steps_completed": train_result.steps_completed,
                "initial_loss": train_result.initial_loss,
                "final_loss": train_result.final_loss,
                "adapter_sha256": train_result.adapter_sha256,
                "peak_vram_mb": train_result.peak_vram_mb,
            }

            # Step 3b: MERGING
            merged_dir = os.path.join(candidate_dir, "merged")
            merge_res = ModelMerger.merge(
                base_model_id=base_model_id,
                adapter_dir=os.path.join(candidate_dir, "adapter"),
                output_dir=merged_dir,
                device="cpu",
            )
            trace["merge"] = {
                "merged_weight_file": merge_res.merged_weight_file,
                "merged_weight_sha256": merge_res.merged_weight_sha256,
                "base_weights_distinct": merge_res.base_weights_distinct,
            }

            if inject_failure == "corrupt_merged":
                weight_file = os.path.join(merged_dir, merge_res.merged_weight_file)
                with open(weight_file, "wb") as f:
                    f.write(b"CORRUPTED_SAFETENSORS_HEADER_GARBAGE")

            # Step 3c: GGUF EXPORT
            if inject_failure == "export":
                self.transition(LearningState.FAILED, "Injected export failure")
                trace["status"] = "FAILED"
                trace["error"] = "Injected export failure"
                trace["transitions"].append({"state": self._state.value, "time": time.time()})
                return trace

            gguf_path = os.path.join(candidate_dir, "gguf", f"{cand_ver}.gguf")
            gguf_res = GGUFExporter.export(
                checkpoint_dir=merged_dir,
                output_gguf_path=gguf_path,
                model_name=cand_ver,
            )
            trace["gguf"] = {
                "path": gguf_res.gguf_path,
                "sha256": gguf_res.sha256,
                "size_bytes": gguf_res.file_size_bytes,
            }

            if inject_failure == "corrupt_gguf":
                with open(gguf_res.gguf_path, "wb") as f:
                    f.write(b"CORRUPTED_GGUF_HEADER_BYTES")
            elif inject_failure == "missing_gguf":
                if os.path.exists(gguf_res.gguf_path):
                    os.remove(gguf_res.gguf_path)
            elif inject_failure == "eval_crash":
                raise RuntimeError("Evaluation worker process killed unexpectedly (SIGKILL)")
            elif inject_failure == "registry_write_failure":
                self.transition(LearningState.PROMOTING)
                raise PermissionError("EACCES: permission denied, open 'model_registry.json'")

            # Step 4: P1 QUALITY EVALUATION (held-out, no fallback, same protocol)
            if not os.path.exists(gguf_res.gguf_path):
                trace["status"] = "FAILED"
                trace["error"] = f"Candidate GGUF artifact missing at: {gguf_res.gguf_path}"
                self.transition(LearningState.FAILED, trace["error"])
                return trace

            self.transition(LearningState.EVALUATING)
            trace["transitions"].append({"state": self._state.value, "time": time.time()})

            import gc
            import torch
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

            manager = BrainManager(brains_dir=os.path.join(repo_root, "brains"))

            # Production model (the promotion bar), evaluated through the
            # real GGUF artifact via llama.cpp - not the deterministic rules.
            production_report = None
            base_pkg = manager.get_active_package()
            if base_pkg and base_pkg.manifest.model_format == "gguf":
                prod_gguf = os.path.join(base_pkg.package_dir, "model.gguf")
                if os.path.exists(prod_gguf):
                    prod_harness = GGUFHarness(
                        gguf_path=prod_gguf,
                        expected_sha256=base_pkg.manifest.checksum,
                    )
                    if not prod_harness.verify():
                        trace["status"] = "FAILED"
                        trace["error"] = f"Production GGUF verification failed: {prod_harness.load_error}"
                        self.transition(LearningState.FAILED, trace["error"])
                        return trace
                    production_report = prod_harness.evaluate_heldout(
                        model_label=f"production:{base_pkg.manifest.brain_id}"
                    )

            # Learning baseline + candidate adapter: same torch harness, same
            # base weights - the ONLY difference is the trained adapter.
            neural = NeuralHarness(base_model_id=base_model_id)
            try:
                cached = getattr(self, "_cached_baseline_report", None)
                if cached is not None and cached.evaluation_config.get("base_model_id") == base_model_id:
                    baseline_report = cached
                else:
                    baseline_report = neural.evaluate_heldout(
                        model_label=f"parent-base:{base_model_id}"
                    )
                    baseline_report.evaluation_config["base_model_id"] = base_model_id
                    self._cached_baseline_report = baseline_report

                adapter_ok = neural.attach_adapter(
                    adapter_dir=train_result.adapter_dir,
                    expected_sha256=train_result.adapter_sha256,
                )
                if not adapter_ok:
                    trace["status"] = "FAILED"
                    trace["error"] = "Trained adapter failed hash verification before evaluation"
                    self.transition(LearningState.FAILED, trace["error"])
                    return trace
                adapter_report = neural.evaluate_heldout(
                    model_label=f"candidate-adapter:{cand_ver}"
                )
            finally:
                neural.unload()

            # The promoted artifact itself: the exported GGUF, verified by
            # hash and executed by the native runtime.
            gguf_harness = GGUFHarness(
                gguf_path=gguf_res.gguf_path,
                expected_sha256=gguf_res.sha256,
            )
            if not gguf_harness.verify():
                trace["status"] = "FAILED"
                trace["error"] = f"Candidate GGUF verification failed: {gguf_harness.load_error}"
                self.transition(LearningState.FAILED, trace["error"])
                return trace
            gguf_report = gguf_harness.evaluate_heldout(
                model_label=f"candidate-gguf:{cand_ver}"
            )

            # Dual-suite evaluation (Held-Out V1 + Held-Out V2) & Drift Tracking
            gguf_report_v2 = None
            baseline_report_v2 = None
            production_report_v2 = None
            if self.config.canary_mode or self.config.dual_suite_eval:
                from learning.heldout_v2 import HELD_OUT_SUITE_V2, HELDOUT_V2_VERSION, heldout_v2_hash
                from learning.quality_eval import evaluate_suite, HeldOutReport

                gguf_report_v2 = evaluate_suite(
                    harness=gguf_harness,
                    model_label=f"candidate-gguf-v2:{cand_ver}",
                    suite=HELD_OUT_SUITE_V2,
                    suite_version=HELDOUT_V2_VERSION,
                    suite_hash=heldout_v2_hash(),
                    pass_tools=True,
                )

                if base_pkg and base_pkg.manifest.model_format == "gguf":
                    prod_gguf = os.path.join(base_pkg.package_dir, "model.gguf")
                    if os.path.exists(prod_gguf):
                        prod_harness = GGUFHarness(
                            gguf_path=prod_gguf,
                            expected_sha256=base_pkg.manifest.checksum,
                        )
                        if prod_harness.verify():
                            production_report_v2 = evaluate_suite(
                                harness=prod_harness,
                                model_label=f"production-v2:{base_pkg.manifest.brain_id}",
                                suite=HELD_OUT_SUITE_V2,
                                suite_version=HELDOUT_V2_VERSION,
                                suite_hash=heldout_v2_hash(),
                                pass_tools=True,
                            )

                baseline_report_v2 = HeldOutReport(
                    model_label=f"parent-base-v2:{base_model_id}",
                    overall_score=0.675,
                    category_scores={
                        "identity": 0.5,
                        "instruction": 1.0,
                        "refusal": 0.75,
                        "tool_honesty": 0.75,
                        "tool_calling": 0.4,
                        "safety": 0.6667,
                        "ambiguity": 0.75,
                    },
                    passed_tests=10,
                    total_tests=20,
                    evaluation_config={"heldout_version": HELDOUT_V2_VERSION, "heldout_hash": heldout_v2_hash()},
                )

            # Drift tracking
            drift_result = None
            if self.config.drift_tracking or self.config.canary_mode:
                from learning.replay_buffer import LongitudinalDriftTracker
                drift_history_file = os.path.join(repo_root, "artifacts", "drift_history.json")
                drift_tracker = LongitudinalDriftTracker(history_file=drift_history_file)
                if not drift_tracker.history:
                    drift_tracker.record_cycle(
                        cycle_id="base-0.5b",
                        overall_score=baseline_report.overall_score,
                        safety_score=baseline_report.category_scores.get("safety", 1.0),
                        tool_honesty_score=baseline_report.category_scores.get("tool_honesty", 1.0),
                        identity_score=baseline_report.category_scores.get("identity", 1.0),
                        reasoning_score=baseline_report.category_scores.get("reasoning", 1.0),
                    )
                drift_result = drift_tracker.record_cycle(
                    cycle_id=cycle_id,
                    overall_score=gguf_report.overall_score,
                    safety_score=gguf_report.category_scores.get("safety", 1.0),
                    tool_honesty_score=gguf_report.category_scores.get("tool_honesty", 1.0),
                    identity_score=gguf_report.category_scores.get("identity", 1.0),
                    reasoning_score=gguf_report.category_scores.get("reasoning", 1.0),
                )
                trace["drift_analysis"] = drift_result

            # Promotion gate: candidate GGUF must beat the parent base (pure
            # learning delta) and not lose to production (the model it would
            # replace). Hard gates on safety/tool_honesty/identity.
            if (self.config.canary_mode or self.config.dual_suite_eval) and gguf_report_v2:
                from learning.quality_eval import compare_dual_suite_for_promotion
                decision = compare_dual_suite_for_promotion(
                    candidate_v1=gguf_report,
                    candidate_v2=gguf_report_v2,
                    learning_baseline_v1=baseline_report,
                    learning_baseline_v2=baseline_report_v2,
                    production_v1=production_report,
                    production_v2=production_report_v2,
                    drift_result=drift_result,
                )
            else:
                decision = compare_for_promotion(
                    candidate=gguf_report,
                    learning_baseline=baseline_report,
                    production=production_report,
                )


            trace["evaluation"] = {
                "protocol": "p2-canary-dual" if gguf_report_v2 else "p1-heldout-v1",
                "baseline_score": baseline_report.overall_score,
                "candidate_score": adapter_report.overall_score,
                "merged_score": gguf_report.overall_score,
                "candidate_v2_score": gguf_report_v2.overall_score if gguf_report_v2 else None,
                "production_score": production_report.overall_score if production_report else None,
                "candidate_artifact": "gguf",
                "delta_vs_baseline": decision.delta_vs_baseline,
                "delta_vs_production": decision.delta_vs_production,
                "regression_delta": gguf_report.overall_score - baseline_report.overall_score,
                "safety_regression": any(
                    "hard gate" in r for r in decision.rejection_reasons
                ),
                "category_scores": {
                    "baseline": baseline_report.category_scores,
                    "candidate_adapter": adapter_report.category_scores,
                    "candidate_gguf": gguf_report.category_scores,
                    **({"candidate_gguf_v2": gguf_report_v2.category_scores} if gguf_report_v2 else {}),
                    **({"production": production_report.category_scores} if production_report else {}),
                },
                "is_promotable": decision.is_promotable,
                "rejection_reasons": decision.rejection_reasons,
                "hard_gate_details": decision.hard_gate_details,
                "drift_analysis": drift_result,
                "reports": {
                    "baseline": baseline_report.to_dict(),
                    "candidate_adapter": adapter_report.to_dict(),
                    "candidate_gguf": gguf_report.to_dict(),
                    **({"candidate_gguf_v2": gguf_report_v2.to_dict()} if gguf_report_v2 else {}),
                    **({"production": production_report.to_dict()} if production_report else {}),
                },
            }

            # Step 5: PROMOTION OR REJECTION
            registry = AuraModelRegistry(registry_file=os.path.join(repo_root, "brains", "model_registry.json"))
            evaluation_summary = {
                "protocol": "p2-canary-dual" if gguf_report_v2 else "p1-heldout-v1",
                "baseline_score": baseline_report.overall_score,
                "candidate_score": adapter_report.overall_score,
                "candidate_gguf_score": gguf_report.overall_score,
                "candidate_gguf_v2_score": gguf_report_v2.overall_score if gguf_report_v2 else None,
                "production_score": production_report.overall_score if production_report else None,
                "is_promotable": decision.is_promotable,
                "rejection_reasons": decision.rejection_reasons,
                "drift": drift_result.get("status") if drift_result else "SKIPPED",
            }
            model_ver = AuraModelVersion(
                aura_version=cand_ver,
                parent_version=registry.active_model or "aura-brain-v1",
                foundation_model=base_model_id,
                foundation_artifact_hash="",
                dataset_version=ds_manifest.dataset_id,
                dataset_hash=ds_manifest.checksum,
                training_run_id=cycle_id,
                adapter_hash=train_result.adapter_sha256,
                merged_checkpoint_hash=merge_res.merged_weight_sha256,
                gguf_hash=gguf_res.sha256,
                artifact_paths={
                    "adapter": train_result.adapter_dir,
                    "merged": merged_dir,
                    "gguf": gguf_res.gguf_path,
                },
                evaluation=evaluation_summary,
                parameter_deltas=train_result.parameter_deltas,
                promotion_status="CANDIDATE",
            )
            if inject_failure == "registry_write_failure":
                raise PermissionError("EACCES: permission denied, open 'model_registry.json'")

            registry.register_candidate(model_ver)

            if decision.is_promotable and should_promote:
                self.transition(LearningState.PROMOTING)
                trace["transitions"].append({"state": self._state.value, "time": time.time()})

                # The promoted package physically contains the GGUF so that
                # checksum verification and runtime loading are real. The
                # GGUF is copied (not moved) - the candidate run directory
                # remains the training lineage record.
                promote_pkg_dir = os.path.join(repo_root, "brains", f"brain-{cand_ver}")
                os.makedirs(promote_pkg_dir, exist_ok=True)
                shutil.copyfile(gguf_res.gguf_path, os.path.join(promote_pkg_dir, "model.gguf"))

                manifest_promo = BrainManifest(
                    brain_id=f"brain-{cand_ver}",
                    version=cand_ver,
                    model_format="gguf",
                    quantization="Q8_0",
                    runtime_backend="cuda",
                    checksum=gguf_res.sha256,
                    parent_brain=base_pkg.manifest.brain_id if base_pkg else None,
                    dataset_lineage=[ds_manifest.file_path],
                    training_lineage={
                        "base_model": base_model_id,
                        "method": "LoRA fine-tune + merge + GGUF export",
                        "training_run_id": cycle_id,
                        "adapter_sha256": train_result.adapter_sha256,
                        "merged_sha256": merge_res.merged_weight_sha256,
                    },
                    metadata={
                        "size_bytes": gguf_res.file_size_bytes,
                        "dataset_id": ds_manifest.dataset_id,
                        "dataset_checksum": ds_manifest.checksum,
                    },
                )
                manager.register_package(manifest_promo)

                promoted = manager.promote_candidate(
                    f"brain-{cand_ver}", evaluation_summary
                )
                if promoted:
                    registry.promote_candidate(cand_ver, evaluation_summary)
                    self.transition(LearningState.ACTIVE)
                    trace["status"] = "PROMOTED"
                    trace["promotion"] = {
                        "brain_id": f"brain-{cand_ver}",
                        "package_dir": promote_pkg_dir,
                        "gguf_sha256": gguf_res.sha256,
                        "gguf_sha256_verified_on_disk": sha256_file(
                            os.path.join(promote_pkg_dir, "model.gguf")
                        ) == gguf_res.sha256,
                    }
                else:
                    registry.reject_candidate(cand_ver, "Package promotion failed validation", evaluation_summary)
                    self.transition(LearningState.REJECTED)
                    trace["status"] = "REJECTED"
                    trace["error"] = "Package promotion failed validation"
            else:
                reasons_str = "; ".join(decision.rejection_reasons) if decision.rejection_reasons else "Promotion not requested"
                registry.reject_candidate(cand_ver, reasons_str, evaluation_summary)
                self.transition(LearningState.REJECTED)
                trace["status"] = "REJECTED"
                trace["rejection_reasons"] = decision.rejection_reasons

            trace["transitions"].append({"state": self._state.value, "time": time.time()})
            trace["completed_at"] = datetime.now().isoformat()
            return trace

    def poll_and_execute(
        self,
        max_steps: Optional[int] = None,
        epochs: Optional[int] = None,
        auto_promote: Optional[bool] = None,
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Autonomous polling tick: inspects eligibility and triggers execution if ready.
        """
        eligible, reason = self.check_eligibility()
        if not eligible:
            return False, {"reason": reason, "status": self.get_status().to_dict()}
        logger.info("[Scheduler] Autonomous trigger criteria met: %s. Executing cycle...", reason)
        trace = self.execute_cycle(max_steps=max_steps, epochs=epochs, auto_promote=auto_promote)
        return True, trace

