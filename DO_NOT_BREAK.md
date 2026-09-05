# AURA ARCHITECTURAL INVARIANTS & DO NOT BREAK CONTRACTS

## 1. Zero Cloud Leakage
- All LLM inference MUST strictly route to `http://127.0.0.1:8080/v1` (gpt-oss-20b-MXFP4).
- Never send user prompts, code, memory, or tokens to external cloud endpoints.

## 2. Checkpoint & Memory Integrity
- Checkpoints in `.aura/checkpoints/` must remain lossless and recoverable.
- Failed tasks must never corrupt active session memory or SQLite state.

## 3. AsyncIO Concurrency Safety
- Never block the main FastAPI / AsyncIO event loop with synchronous I/O.
- Tool timeouts must never leave orphan child processes running.

## 4. Android Companion Safety
- ADB commands must handle connection drops gracefully.
- Downscaled image capture must respect memory budgets (< 2MB).
