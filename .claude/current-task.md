# CURRENT TASK — Cloud-Only Migration (remove the local LLM subsystem)

## Mission
Remove the ENTIRE local / on-device LLM subsystem and make Aura cloud-only.
Gemini is the default provider. ALL 11 cloud providers stay selectable so the
owner can add any key themselves (gemini, openai, anthropic, groq, cerebras,
openrouter, mistral, xai, deepseek, qwen, custom); `mock` retained for tests.

Superseded: the previous "P1 REAL LEARNING QUALITY" mission — that entire
local-training / self-learning effort is being deleted, not continued.

## Owner constraints (absolute)
- REAL EXECUTION ONLY. Never fabricate metrics or test results.
- Do NOT weaken tests, destroy the repo, or rewrite git history.
- Do NOT git commit / push unless explicitly asked.
- Deleting tracked source files is reversible (git) and authorized.
- HOLD for explicit confirmation: the single IRREVERSIBLE action — deleting the
  untracked ~55 GB `brains/` directory from disk (+ any doc/artifact `git rm`).
- No manual normalization of brains/model_registry.json or brain_state.json.

## Phase status
1. Spine (reversible, keep boot working) — DONE
2. Minimum rewiring (decouple from learning before deleting it) — DONE
3. Deletion phase (local brain & learning package purged) — DONE
4. Android Companion Cloud-Only refactor (remove on-device LLM & native llama) — DONE
5. Settings migration & degradation UX — DONE
6. Disk Cleanup (~54.2 GB brains/ & dead training sets purged) — DONE
7. Final verification across backend (496 tests) & Android (Gradle) — 100% PASS — DONE

## Current State
All phases of the Cloud-Only Architecture Migration (Phases 0–6) are complete and fully verified:
- Backend: 496/496 tests passed.
- Android Companion: `./gradlew :app:testDebugUnitTest` 22/22 tasks up-to-date, BUILD SUCCESSFUL.
- Reclaimed ~54.2 GB of disk space.
- Git commit requested by owner.

