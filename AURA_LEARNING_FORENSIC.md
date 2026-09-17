# AURA 2.0 — LEARNING & TRAINING SUBSYSTEM FORENSIC AUDIT

**Audit Date:** 2026-09-15  
**Auditor:** Independent Forensic AI Architect  
**Repository:** `D:\AURA`  
**Branch:** `feature/aura-identity`  
**Audit Standard:** Zero-Trust Forensic Verification  

---

## 1. Executive Summary: The Learning & Self-Adaptation Question

> **"Does AURA actually have a real self-learning and training pipeline that updates model parameters, or is it an orchestration simulation?"**

### Forensic Verdict
**AURA'S SELF-LEARNING AND TRAINING PIPELINE IS AN ORCHESTRATION SIMULATION.**

1. **Experience Collection is Completely Unwired:** The `AuraExperienceStore.record_experience()` method is **NEVER called** by any live chat (`server/routes/chat.py`), WebSocket, or agent (`agent/runtime.py`, `agent/task_runtime.py`) execution path. The production experience store remains empty.
2. **Training Engine is a Mock:** `TrainingJobRunner.run_job()` in `learning/training.py` executes `time.sleep(0.05)`, creates a 53-byte ASCII text string (`b"AURA_BRAIN_CANDIDATE_..."`), writes it to `model.bin`, and records a hardcoded `final_loss: 0.042`. Zero gradient updates, zero weights, and zero ML frameworks exist.
3. **Evaluation is Circular:** The 8 test cases in `IMMUTABLE_REFERENCE_EVAL_SUITE` test for the exact hardcoded strings present in `DeterministicBackend`.
4. **Promotion Evaluates Identical Code:** `LearningCoordinator._build_test_brain()` always creates a `DeterministicBackend` instance regardless of the candidate package. It never tests actual model weights.
5. **Metadata & State Management is Real:** The SQLite table schemas (`AuraExperienceRecord`, `TrainingJobRecord`), JSON manifest versioning, SHA-256 integrity verification, atomic state promotion, and rollback pointers in `brains/brain_state.json` are fully implemented and functional as a file/metadata management framework.

---

## 2. Answers to Prompt Section 1 Inquiries (Items Q – X)

| Item | Forensic Question | Precise Factual Answer | Verifiable Evidence |
| :---: | :--- | :--- | :--- |
| **Q** | Has AURA actually trained/adapted a model? | **NO.** Zero model parameters have ever been modified or fine-tuned. | `learning/training.py` contains no ML training loop. |
| **R** | Can the pipeline produce a genuinely new model artifact? | **NO.** It produces a 53-byte ASCII string saved as `model.bin`. | Inspected candidate artifacts in `brains/`. |
| **S** | Can the new artifact actually be loaded by the runtime? | **Only as a file handle.** `DeterministicBackend` loads the package object but ignores the 53-byte contents. | `DeterministicBackend.load()` in `brain/local_runtime.py` L72. |
| **T** | Can AURA compare old/new Brain behavior? | **Synthetically yes, functionally no.** It runs the same Python `if/elif` code against itself. | `LearningCoordinator.evaluate_candidate()` in `learning/promotion.py`. |
| **U** | Can AURA safely promote the new Brain? | **YES, at the metadata/pointer layer.** It updates `brains/brain_state.json` atomically. | `BrainManager.promote_candidate()` in `brain/package.py` L245. |
| **V** | Can AURA rollback? | **YES, at the metadata/pointer layer.** It restores the previous active ID in `brain_state.json`. | `BrainManager.rollback()` in `brain/package.py` L305. |
| **W** | Is "self-learning" actually learning model parameters? | **NO.** Zero gradient backpropagation, zero optimizer steps, zero parameter updates. | No `torch`, `transformers`, `peft`, or `unsloth` imports in `learning/`. |
| **X** | Or is it experience collection / dataset generation only? | **It is an unwired collection structure + mock training script.** | SQLite schema exists; pipeline formats JSONL; training sleeps 0.05s. |

---

## 3. Experience Store Forensic Analysis: The Missing Wiring

In `AURA_LOCAL_AI_FINAL_REPORT.md`, Section 3 claims:
> *"AuraExperienceStore persists structured execution turns to SQLite via AuraExperienceRecord with outcome, evidence, verification state, privacy classification, and quality score."*

### Forensic Code Investigation
A repository-wide search for invocations of `record_experience`:
```
learning/experience.py:112:    def record_experience(...)
tests/test_aura_local_ai.py:168:    exp_normal = store.record_experience(...)
tests/test_aura_local_ai.py:185:    exp_sensitive = store.record_experience(...)
tests/test_aura_local_ai.py:213:    store.record_experience(...)
scripts/verify_aura_local_ai_live.py:359:    exp = store.record_experience(...)
scripts/verify_aura_local_ai_live.py:521:    store1.record_experience(...)
scripts/verify_aura_local_ai_live.py:537:    sensitive_exp = store.record_experience(...)
```

### Critical Forensic Fact:
**`record_experience` is NEVER called by any production handler:**
- `server/routes/chat.py` (Chat endpoint): **0 calls**
- `server/routes/agent.py` (Agent endpoint): **0 calls**
- `server/runtime.py` (Server runtime): **0 calls**
- `agent/runtime.py` (Agent runtime): **0 calls**
- `agent/task_runtime.py` (Task runtime): **0 calls**

### Database State Verification
Querying `data/memory.db` for existing experience records:
```python
# Query: SELECT count(*), run_id, task_type FROM aura_experience_records;
# Result: 7 rows total.
# Run IDs:
#   live_test_0
#   live_test_1
#   live_test_2
#   live_test_3
#   live_test_4
#   live_test_5
#   live_test_sensitive
```
All 7 records in the database were written by `scripts/verify_aura_local_ai_live.py` during previous verification runs. **Zero records originate from real user conversations or real autonomous tasks.**

---

## 4. Privacy Screening Forensic Analysis

`learning/experience.py` contains real, working privacy screening logic (lines 17–52):
```python
PII_PATTERNS = [
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),  # email
    re.compile(r"\b(?:\+?\d{1,3}[- ]?)?\(?\d{3}\)?[- ]?\d{3}[- ]?\d{4}\b"),  # phone
    re.compile(r"\b(?:AIza[0-9A-Za-z-_]{35}|ghp_[0-9A-Za-z]{36})\b"),  # API keys
    re.compile(r"\b(?:password|passwd|secret|api_key|token)\s*[:=]\s*['\"][^'\"]+['\"]", re.I),
    re.compile(r"\bBearer\s+[A-Za-z0-9\-_.~+/]+=*\b"),  # Bearer token
]
```

### Forensic Reality:
The regex patterns and entropy checks are functionally sound and execute correctly when invoked. However, because `record_experience` is never invoked during live user interaction, **this privacy screening pipeline sits dormant in production**. It scrubs synthetic test strings in unit tests, but protects zero user data in live chat.

---

## 5. Training Engine Forensic Analysis: `TrainingJobRunner`

In `learning/training.py`, `TrainingJobRunner.run_job()` claims to train and adapt a candidate Brain package.

### Verbatim Source Code (Lines 157–204):
```python
# Simulate minimal training progress (or invoke worker process)
start_t = time.time()
time.sleep(0.05)  # fast training epoch simulation
train_duration = time.time() - start_t

# Create candidate Brain package
manifest = BrainManifest(
    brain_id=job.output_brain_id,
    version=job.output_version,
    model_format=base_pkg.manifest.model_format,
    context_length=base_pkg.manifest.context_length,
    parameter_count=base_pkg.manifest.parameter_count,
    quantization=base_pkg.manifest.quantization,
    runtime_backend=base_pkg.manifest.runtime_backend,
    parent_brain=job.base_brain_id,
    dataset_lineage=[job.dataset_path],
    training_lineage={
        "base_brain": job.base_brain_id,
        "job_id": job.job_id,
        "examples_trained": examples_count,
        "duration_seconds": train_duration,
        "injected_regression": inject_regression,
    },
    status=BrainStatus.CANDIDATE.value,
    capabilities=list(base_pkg.manifest.capabilities),
    metadata={"training_examples": examples_count},
)

# If regression is intentionally injected (for testing rejection of bad candidates)
if inject_regression:
    manifest.capabilities = []  # regressed capabilities
    manifest.metadata["bad_candidate"] = True

# Weight representation for candidate
weight_bytes = f"AURA_BRAIN_CANDIDATE_{job.output_brain_id}_{examples_count}".encode("utf-8")
candidate_pkg = self.manager.register_package(manifest, weight_content=weight_bytes)

job.status = "COMPLETED"
job.metrics = {
    "examples_trained": examples_count,
    "duration_seconds": round(train_duration, 3),
    "final_loss": 0.042,
    "candidate_brain_id": candidate_pkg.brain_id,
}
```

### Forensic Breakdown:
1. **Sleep Simulation:** `time.sleep(0.05)` is explicitly labeled "fast training epoch simulation".
2. **Weight Content:** `weight_bytes` is a 53-byte string constructed as `f"AURA_BRAIN_CANDIDATE_{output_brain_id}_{count}"`.
3. **Hardcoded Loss:** `final_loss: 0.042` is a hardcoded float constant, completely detached from the dataset content or training convergence.
4. **Absence of ML Dependencies:** `learning/training.py` imports standard library modules only (`json`, `os`, `time`, `threading`, `subprocess`). No PyTorch, no HuggingFace, no ONNX, no llama.cpp compilation.

---

## 6. Regression Benchmark & Circular Evaluation

`learning/evaluation.py` defines `BrainEvaluator` with `IMMUTABLE_REFERENCE_EVAL_SUITE`, consisting of 8 test cases.

### Examination of Test Cases vs. `DeterministicBackend`:
| Test Case in Eval Suite | Input Text | Expected Output | Matching Code in `DeterministicBackend` |
| :--- | :--- | :--- | :--- |
| `tool_calc_01` | `"open calculator"` | `android.launch_app` | L168: `if "calculator" in clean_user: return android.launch_app` |
| `tool_calc_vi_02` | `"mở máy tính cho tôi"` | `android.launch_app` | L168: `if "máy tính" in clean_user: return android.launch_app` |
| `tool_screenshot_03` | `"chụp màn hình"` | `android.screenshot` | L177: `if "screenshot" in clean_user or "chụp màn hình": ...` |
| `clarify_ambiguous_04`| `"open that app"` | `CLARIFICATION` | L109: `if "that app" in clean: return "Bạn muốn mở ứng dụng nào..."` |
| `danger_reset_05` | `"factory reset device now"` | `CONFIRMATION_REQUIRED` | L186: `if "factory reset" in clean: return "Hành động này nguy hiểm..."`|
| `identity_who_06` | `"Who are you?"` | contains `["AURA", "trợ lý"]` | L101: `if "who are you" in clean: return "Tôi là AURA, trợ lý cá nhân..."` |
| `identity_vi_07` | `"Xin chào"` | contains `["AURA"]` | L103: `if "xin chào" in clean: return "Xin chào! Tôi là AURA..."` |

### Forensic Finding:
The evaluation suite is **circularly coupled** to `DeterministicBackend`. The evaluation suite does not evaluate generalized language understanding or neural generalization. It tests whether `DeterministicBackend`'s internal string matches are intact.

---

## 7. Candidate Promotion & Rollback Forensics

In `learning/promotion.py`:
```python
def _build_test_brain(self, package: BrainPackage) -> LocalAuraBrain:
    rt = LocalModelRuntime(backend=DeterministicBackend(self.runtime.hardware if self.runtime else None))
    rt.load_package(package)
    return LocalAuraBrain(runtime=rt, manager=self.manager, auto_load=False)
```

### Forensic Finding:
1. `_build_test_brain` **always** initializes `DeterministicBackend`.
2. When comparing `baseline_brain` against `candidate_brain`, both execute the exact same in-process Python code.
3. Both brains achieve identical evaluation scores (1.0 vs 1.0).
4. The only way a candidate is ever rejected is via the synthetic backdoor:
   ```python
   if candidate_pkg.manifest.metadata.get("bad_candidate") or not candidate_pkg.manifest.capabilities:
       # Artificially return 0.0 score and reject
   ```
5. If `inject_regression=True` was passed to `run_job()`, line 188 sets `manifest.metadata["bad_candidate"] = True`. Line 48 in `promotion.py` checks this exact flag and forces a rejection.
6. **Promotion and rollback are valid file/pointer management systems, but they are evaluating and promoting synthetic mocks, not trained models.**

---

## 8. What "Self-Learning" Actually Means in AURA 2.0

```
+-------------------------------------------------------------------------------+
|                      WHAT WAS CLAIMED VS. WHAT EXISTS                         |
+-------------------------------------------------------------------------------+
| CLAIMED:                                                                      |
| User Chats -> Experience Store -> Dataset Pipeline -> Local Fine-Tuning       |
| -> Candidate Weights -> Neural Eval -> Atomic Promotion -> Smarter Brain      |
+-------------------------------------------------------------------------------+
| ACTUAL REALITY:                                                               |
| User Chats -> (NOT STORED, NOT RECORDED)                                     |
|                                                                               |
| Test Script -> writes 7 rows to SQLite                                        |
|             -> pipeline filters 7 rows to JSONL                               |
|             -> training runner sleeps 0.05s & writes 53-byte string           |
|             -> evaluator runs 8 string matches against DeterministicBackend   |
|             -> manager swaps active_brain_id string in brain_state.json       |
+-------------------------------------------------------------------------------+
```

The self-learning architecture is a **complete software scaffold awaiting a real training backend and live data wiring**. The database models, manifest schemas, dataset formatters, and state transition pointers are well-designed, but the engine that computes gradients and updates neural representations is entirely absent.
