# P1 REAL LEARNING QUALITY — In Progress

Mission: prove (or disprove) that AURA actually becomes better after
learning — genuine improvement on held-out data, not training loss.

## Completed so far

### Phase 0: Forensic audit (DONE)
P0 defects found and verified:
1. Train/eval contamination: 5/8 old eval inputs verbatim in curriculum.
2. Eval never loaded trained weights (TorchInferenceBackend adapter path
   defect + deterministic-fallback masking).
3. Baseline apples-to-oranges (3B GGUF prod vs 0.5B candidates, both
   scored through rule fallback).
4. Eval set too small; no hash pinned; no tool-honesty category.
5. auto_promote not threaded through daemon.

### P1 implementation (DONE, tests green)
- `learning/heldout.py` — immutable 26-case held-out suite, hash
  `bb253de2a836117c1ac34d6e26c881c33b3d35f015d315b42e63ec3f721453cd`,
  10 categories, all cases have positive assertions (no vacuous passes).
- `learning/contamination.py` — 5-overlap-kind checker (exact,
  normalized, near-duplicate trigram-Jaccard ≥ 0.5, prompt leakage,
  answer leakage). Curriculum verified CLEAN.
- `learning/quality_eval.py` — NeuralHarness (base + hash-verified
  adapter on same weights) + GGUFHarness (llama.cpp, hash-verified,
  seed/temp pinned) + promotion gate (strict improvement vs parent
  base, hard gates safety/tool_honesty/identity, must beat production).
  NO deterministic fallback anywhere in eval.
- `learning/scheduler.py` — execute_cycle rewritten: contamination gate
  after dataset generation (fails before training), 4-way held-out
  evaluation (parent base, adapter, candidate GGUF, production GGUF),
  promotion copies GGUF into a real brain package (checksum-verified)
  before promoting; auto_promote threaded from daemon.
- `tests/test_p1_learning_quality.py` — 30 tests pinning all the above.
  P0 forensic tests (4) still pass. Full suite regression check running.

## Next: live cycle (Task #4)
1. Write scripts/verify_p1_learning_quality_live.py: real
   daemon/scheduler-triggered cycle with GPU LoRA training, 4-way
   held-out evaluation, promotion-or-rejection, runtime SHA
   verification, artifact dump to artifacts/p1_learning_quality_summary.json.
2. Run it (RTX 4060, ~10-20 min).
3. Failure-recovery checks + second cycle if safe (Task #5).
4. Report AURA_P1_LEARNING_QUALITY_REALITY.md + reality matrix +
   conservative verdict (PROVEN / PARTIAL / NOT PROVEN / FAILED).

## Constraints (user, absolute)
- REAL EXECUTION ONLY. Never fabricate metrics.
- Do not destroy the repository / weaken tests / rewrite history.
- No manual normalization of brains/model_registry.json or
  brain_state.json; record before/after hashes if unavoidable.
- Training local only (no cloud).
