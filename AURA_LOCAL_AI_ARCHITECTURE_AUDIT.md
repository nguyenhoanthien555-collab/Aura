# AURA 2.0 — Local AI Architecture & Forensic Audit

**Document**: `AURA_LOCAL_AI_ARCHITECTURE_AUDIT.md`  
**Repository**: `D:\AURA`  
**Branch**: `feature/aura-identity`  
**Baseline Verification**: 221 / 221 Phase 5B & 5C tests passing green (100% baseline integrity).  
**Working Tree Posture**: Intentionally dirty from previous phases (preserved). Zero commits, zero pushes.

---

## Executive Summary

This forensic audit evaluates the entire AURA 2.0 repository to establish the baseline for the **Grand Roadmap Implementation: Local Personal AI + 24/7 Runtime + Self-Learning**. 

AURA's core insight across Phases 0–5C is that **model output is untrusted and is never world state**. Real state transitions only occur through `ToolExecutor`, are observed via concrete device/system hooks, yield structured `Evidence`, and are authoritatively judged by `ResponseVerifier` and `TaskRuntime`. 

To transition AURA from a cloud-augmented agent to a genuine **local-first personal AI**, the local Brain must become the primary native intelligence, while cloud APIs remain strictly optional external capabilities. This audit maps every relevant architectural seam across the repository and defines the exact integration points required.

---

## 1. Current Model / Provider Seam

- **Primary Seam**: `brain/router.py` (`BrainRouter`).
  - Implements provider selection, fallback chains (`FallbackProvider`), lazy provider instantiation, and key validation.
  - Implements `generate(prompt: str) -> str`.
- **Capability Registry**: `brain/providers/capabilities.py`.
  - Defines `_FUNCTION_CAPABLE = frozenset({"gemini", "openai", "cerebras", "custom", "deepseek", "qwen", "xai"})`.
  - Providers not in this set are classified as `CapabilityStatus.UNSUPPORTED` for tool calling and skipped before sending requests.
  - Providers in this set start as `CapabilityStatus.UNKNOWN` until promoted to `CapabilityStatus.VERIFIED` by an actual successful `generate_with_tools` call (`mark_function_calling_verified`).
- **Native Function Calling Port**:
  - `brain/native_fc.py`: Defines `ModelTurn` (carrying `text`, `tool_calls: list[ToolCallRequest]`) and `ToolCallRequest`.
  - `generate_with_tools(system: str, messages: list, tools: list) -> ModelTurn` is implemented by `OpenAICompatibleProvider` (`brain/providers/openai_compatible.py`) and `GeminiProvider` (`brain/providers/gemini.py`).
- **Current Local Provider State**:
  - `brain/providers/local.py` exists as a stub subclass of `OpenAICompatibleProvider` pointing to `http://127.0.0.1:11434/v1` (`LOCAL_BASE_URL`).
  - It lacks a standalone local inference engine abstraction, hardware profiling, and offline packaging.
  - `BrainRouter` does not expose `generate_with_tools` directly on its public interface; callers in `server/routes/agent.py` currently use an adapter `_AgentLLMAdapter` that walks `router.provider`.

---

## 2. Current Chat Seam

- **Primary Seam**: `brain/chat_engine.py` (`ChatEngine`) and `brain/conversation.py` (`ConversationManager`).
  - Coordinates multi-turn user conversation, context building via `PromptBuilder`, temporary memory, episodic recall, and response verification.
- **Tool Interception**:
  - In `ConversationManager._resolve_tools`, tool requests from the model are forwarded to the injected `ToolRunner` (`ToolExecutor`). The model is never allowed to directly execute code.
- **Verification Ledger**:
  - `brain/verify/ledger.py`: Attaches a request-scoped `EvidenceLedger` to every chat turn. Tracks claimed actions vs. recorded `Evidence` and enforces anti-hallucination repairs.
- **Transport Endpoints**:
  - `server/routes/chat.py`: Exposes HTTP POST `/api/chat`, POST `/api/chat/stream`, and WebSocket `/ws/chat`.

---

## 3. Current Agent Seam

- **Primary Seam**: `agent/runtime.py` (`AgentRuntime`).
  - Implements multi-round autonomous execution: `start_run(goal, session_id)`, `step()`, `run_to_completion()`.
  - Driven by `generate_with_tools` calls producing `ModelTurn` directives.
- **Capability Gap Engine**:
  - `core/capabilities/gap.py` (`CapabilityGapEngine`) detects missing capabilities before round 0.
  - If a gap is identified and policy permits, invokes `tools/builder/` to autonomously synthesize, sandbox-test, and register a new tool before executing the goal.
- **Device-Driven Deferred Mode**:
  - Supports both inline execution (local executor) and deferred execution (Android device poll via `/api/agent/step`).

---

## 4. Current Memory Seam

- **Primary Seams**: `memory/manager.py` (`MemoryManager`), `memory/pipeline.py` (`MemoryPipeline`), and `memory/models.py`.
- **Knowledge Sources**:
  - `UserFact`: Explicit user-stated attributes (`user_facts` table).
  - `UserModelEntry`: Long-term inferred/confirmed user traits with temporal validity (`user_model` table).
  - `EpisodicMemory`: Dated event records with importance and confidence (`episodic_memories` table).
  - `SemanticVector`: Cosine similarity search over episodic memory embeddings (`semantic_vectors` table).
  - `CognitiveStore` (`core/cognitive.py`): In-process active state tracking what Aura is currently doing.
- **Separation of Concerns**:
  - User preferences ("user likes concise answers") belong in memory.
  - Model behavioral deficiencies ("model fails tool argument schemas") belong in learning.

---

## 5. Current Tool Seam

- **Authoritative Boundary**: `tools/executor.py` (`ToolExecutor`).
  - Enforces `ToolPolicy` (`allowed` list, `auto_approve` risk gates: `SAFE`, `SENSITIVE`, `DANGEROUS`).
  - Dispatches execution to `Tool.run(arguments)` within strict timeout bounds (`tools/timeout.py`).
  - Gated tools require explicit human approval via confirmation handlers.
- **Tool Outcomes & Evidence**:
  - `tools/outcome.py`: Returns typed `ToolResult` containing `status: ToolStatus`, `evidence: list[Evidence]`, `side_effect: SideEffect`, `retryability: Retryability`.
  - Authoritative rule: A tool output is NOT world state unless backed by valid `Evidence`.
- **Tool Registry**:
  - `tools/registry.py` (`ToolRegistry`): Thread-safe tool catalog with dynamic registration and revocation (`register`, `unregister`).

---

## 6. Current Durable Task Seam

- **Primary Seam**: `agent/task_runtime.py` (`TaskRuntime`).
  - Full support for compound tasks spanning multiple steps, client disconnects, and process restarts.
  - Tables: `durable_tasks`, `durable_task_steps`, `durable_confirmations`, `durable_clarifications`.
- **Step Dependency & Propagation**:
  - DAG execution with `depends_on`.
  - Result propagation via `${step_X.result.key}` interpolation.
- **Safety Semantics**:
  - Timeouts on mutating steps resolve to `ToolStatus.TIMEOUT` / `TaskStatus.UNKNOWN` rather than blind failure.
  - Uncertain mutations are NEVER replayed blindly.
- **Human-in-the-Loop**:
  - Durable confirmation requests with SHA-256 fingerprinting, replay protection, and cross-process atomic resolution.
  - Durable clarification requests for ambiguous goals.

---

## 7. Current Android Seam

- **Primary Seams**: `tools/providers/android_provider.py` and `tools/providers/android_bridge.py`.
  - Exposes 14 canonical Android actions (`android.launch_app`, `android.tap`, `android.type_text`, `android.get_foreground_app`, etc.).
- **Transport**: `server/device_gateway.py` connects with the Kotlin companion app (`com.aura.companion`).
- **Verification Seam**:
  - Mutating Android tools run postcondition checks via `android.verify` or `android.wait_for`.
  - Produces `Evidence(kind=EvidenceKind.POSTCONDITION)` proving real UI state.

---

## 8. Current Startup / Shutdown Lifecycle

- **Startup**: `launcher/services.py` (`build_services`) acts as the composition root.
  - Sequence: `EventBus` -> `CredentialStore.apply()` -> `SettingsStore` -> `MemoryManager` -> `TemporalClock` -> `MemoryPipeline` -> `CognitiveStore` -> `VisionManager` -> `ToolExecutor` -> `ChatEngine` -> `ProactiveEngine`.
- **Shutdown**: Basic signal handlers in `launcher/main.py` and FastAPI lifespan in `server/main.py`.
- **Gap**: Lacks a persistent 24/7 daemon loop managing long-running background tasks, periodic sleep/consolidation, and autonomous self-learning.

---

## 9. Current Background Execution

- `TaskRuntime` contains an internal thread loop (`_worker_loop`) for executing ready steps.
- `ProactiveEngine` ticks on a background interval.
- **Gap**: Background execution is split across individual modules. There is no unified, bounded worker architecture for task processing, model learning, and memory consolidation.

---

## 10. Current Persistence

- **Engine**: SQLite via SQLAlchemy (`memory/sqlite.py`, default database file: `data/memory.db`).
- **Thread & Process Safety**: Managed via `db_lock` (threading mutex + SQLite WAL mode).
- **Entities**:
  - Messages, user facts, episodic memories, user model, semantic vectors.
  - Durable tasks, durable task steps, tool provenance, confirmations, clarifications.
- **Reuse Invariant**: All new learning and brain management metadata must reuse this SQLite substrate rather than introducing another database.

---

## 11. Current Test Infrastructure

- **Framework**: `pytest` 8.3.4, `pytest-asyncio`, `pytest-cov`.
- **Coverage**:
  - Full Phase 5B / 5C test suites: 221 tests passing green in ~39 seconds.
  - Hardware-isolated: Uses in-memory SQLite and mock/loopback device bridges so tests do not depend on external hardware.
- **Verification Scripts**:
  - `scripts/verify_phase5b*_live.py`, `scripts/verify_phase5c_live.py`.

---

## 12. Current Configuration

- **Configuration File**: `config.yaml`.
- **Runtime Overlay**: `core/settings_store.py` (`data/settings_overlay.json`).
- **Sections**: `llm`, `tools`, `memory`, `server`, `vision`, `voice`, `response`.
- **Gap**: Missing structured configuration sections for `brain.offline`, `brain.local`, `learning.*`, and `daemon.*`.

---

## 13. Current Cloud Dependency

- `config.yaml` defaults to `llm.provider: gemini`.
- If no API key is set and cloud providers are enabled, `BrainRouter` fails unless a fallback is configured.
- `brain/providers/capabilities.py` marks `gemini`, `openai`, `deepseek`, etc. as function capable, but leaves local providers as stubs or second-class citizens.
- **Requirement**: Offline mode must guarantee 0 remote HTTP calls, with cloud treated strictly as an optional external adapter.

---

## 14. Missing Local Brain Seams

1. **`LocalAuraBrain` Provider**: Must be a first-class provider in `brain/providers/` that natively handles `generate` and `generate_with_tools`, parses structured responses, and routes through `BrainRouter`.
2. **`LocalModelRuntime` Abstraction**: Pluggable local inference backend supporting local engines (GGUF/llama.cpp, OpenAI-compatible local servers like vLLM/LM Studio, PyTorch) plus a deterministic testing engine for CI.
3. **Hardware Detection Engine**: Real-time probing of CPU, RAM, GPU, VRAM, and OS to determine maximum safe context and recommend local brain packages.
4. **Structured Protocol Parser**: Parsing model outputs into `ANSWER`, `TOOL_CALL`, `CLARIFICATION`, `CONFIRMATION_REQUIRED`, `PLAN`, and `UNCERTAIN`.

---

## 15. Missing Learning Seams

1. **`AuraExperienceStore`**: Persistence layer for capturing every chat and agent turn (`aura_experiences` table in SQLite) with outcome, evidence, verification state, privacy classification, and quality score.
2. **`LearningCandidatePipeline`**: Filtering pipeline enforcing privacy checks, user consent, quality thresholds, and dataset generation (`data/aura/`).
3. **`TrainingJob` Abstraction**: Process-isolated job runner with resource limits (CPU/RAM/GPU), execution timeouts, and cancellation.
4. **Continual Learning Evaluation Harness**: Immutable reference evaluation suites to prevent catastrophic forgetting:
   - Tool selection & argument validation regression set.
   - Response verification & safety regression set.
   - Identity & conversational quality regression set.

---

## 16. Missing Brain Versioning Seams

1. **Brain Package & Manifest**: Standard packaging format under `brains/` with `manifest.json` recording `brain_id`, `version`, `model_format`, `checksum`, `parent_brain`, and lifecycle status.
2. **Lifecycle State Machine**: States: `CANDIDATE`, `VALIDATING`, `ACTIVE`, `DISABLED`, `REJECTED`, `ROLLED_BACK`.
3. **Atomic Promotion & Rollback**: Safe pointer swap of active brain with immediate rollback capability.
4. **Provenance Attribution**: Explicit tracking on every response and task step recording `provider`, `brain_id`, `brain_version`, and `model_digest`.

---

## 17. Missing 24/7 Daemon Seams

1. **Persistent Daemon Supervisor**: Long-running service loop operating independently of HTTP requests and surviving client disconnects.
2. **Bounded Background Workers**:
   - `TaskWorker`: Drives pending durable task steps.
   - `LearningWorker`: Manages experience collection and candidate training.
   - `MemoryConsolidationWorker`: Background deduplication and episodic summarization.
   - `HealthMonitor`: Real-time health grading (`HEALTHY`, `DEGRADED`, `UNAVAILABLE`) across all subsystems.
3. **Strict Offline Mode**: Complete disconnection from external networks when `offline = True`.

---

## 18. Risks & Mitigations

| Risk | Consequence | Mitigation |
|---|---|---|
| **Training Data Poisoning** | Hallucinations or insecure tools trained into model weights | Only `VERIFIED` experiences with strict privacy and quality thresholds are eligible for learning candidate datasets. |
| **Catastrophic Forgetting** | New model candidate breaks existing tool calling or safety rules | Candidate evaluation against `immutable_reference_eval_set` and tool regression harness. Automatic rejection if score regresses. |
| **Resource Exhaustion** | Background training locks CPU/GPU or starves task runtime | Training jobs run in isolated subprocesses with configurable CPU/RAM bounds, process priority, and timeouts. |
| **Model Hallucinating Success** | Model claims an action succeeded without real effect | Architectural invariant: Model output is NEVER world state. Only `ToolExecutor` + `Evidence` establishes outcome. |
| **Regression in Phase 5B/5C** | Breaking existing durable task or synthesis capabilities | Continuous execution of the 221-test baseline suite throughout implementation. |

---

## 19. Compatibility Constraints

1. **Do not replace `ToolExecutor`**: Must remain the single authoritative tool runner.
2. **Do not replace `TaskRuntime`**: Durable tasks, steps, confirmations, and clarifications must be reused directly.
3. **Do not replace `ResponseVerifier`**: The claim-to-evidence verifier remains authoritative.
4. **Do not replace `AndroidBridge`**: Android tools must continue flowing through `ToolExecutor` -> `AndroidBridge` -> `DeviceGateway`.
5. **Preserve Git Worktree**: Zero git commits, zero git pushes, dirty working tree preserved.
