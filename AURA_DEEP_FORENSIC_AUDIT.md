# AURA 2.0 — DEEP FORENSIC ARCHITECTURE & REALITY AUDIT
## Independent Technical Investigation of Local AI, Brain, Learning, and 24/7 Runtime

**Audit Date:** 2026-09-15  
**Auditor:** Independent Forensic AI Architect  
**Repository:** `D:\AURA`  
**Branch:** `feature/aura-identity`  
**Audit Mode:** Extreme Forensic Zero-Trust Audit  
**Operating System:** Windows 11 (Host: x86_64)  
**Python Environment:** Python 3.11.9 Virtual Environment (`.venv`)  

---

## Table of Contents
1. [Executive Verdict](#1-executive-verdict)
2. [Repository State](#2-repository-state)
3. [Actual Architecture](#3-actual-architecture)
4. [Chat Execution Graph](#4-chat-execution-graph)
5. [Agent Execution Graph](#5-agent-execution-graph)
6. [Brain Execution Graph](#6-brain-execution-graph)
7. [Model Artifact Inventory](#7-model-artifact-inventory)
8. [Inference Backend Analysis](#8-inference-backend-analysis)
9. [Local vs External Inference](#9-local-vs-external-inference)
10. [Tool Execution Authority](#10-tool-execution-authority)
11. [Evidence / Verifier Authority](#11-evidence--verifier-authority)
12. [Memory](#12-memory)
13. [Experience Store](#13-experience-store)
14. [Learning](#14-learning)
15. [Training](#15-training)
16. [Candidate Brain](#16-candidate-brain)
17. [Evaluation](#17-evaluation)
18. [Promotion & Rollback](#18-promotion--rollback)
19. [24/7 Daemon](#19-247-daemon)
20. [Crash Recovery](#20-crash-recovery)
21. [Android Integration](#21-android-integration)
22. [Cloud Integration](#22-cloud-integration)
23. [Offline Operation](#23-offline-operation)
24. [Security & Permission Boundaries](#24-security--permission-boundaries)
25. [Database Architecture](#25-database-architecture)
26. [Test Authenticity Analysis](#26-test-authenticity-analysis)
27. [Live Test Authenticity Analysis](#27-live-test-authenticity-analysis)
28. [Documentation Discrepancies](#28-documentation-discrepancies)
29. [Dead Code Inventory](#29-dead-code-inventory)
30. [Unwired Code Inventory](#30-unwired-code-inventory)
31. [Real vs Simulated Matrix Reference](#31-real-vs-simulated-matrix-reference)
32. [Severity-Ranked Findings](#32-severity-ranked-findings)
33. [Current AURA Level](#33-current-aura-level)
34. [Required Work to Reach Next Level](#34-required-work-to-reach-next-level)
35. [Final Recommendation & Required Verdict Block](#35-final-recommendation--required-verdict-block)

---

## 1. Executive Verdict

The central question governing this audit is:
> **"Does AURA actually have its own AI Brain, or does AURA currently provide an abstraction/wrapper around external or deterministic models?"**

### The Definitive Technical Answer:
**AURA DOES NOT HAVE ITS OWN AI BRAIN.**

AURA currently operates as a **dual system**:
1. **In Cloud/Connected Mode:** A sophisticated prompt wrapper and tool orchestration engine around external cloud models (principally Google Gemini Flash Lite via `brain/providers/gemini.py`).
2. **In Offline Mode:** An in-process rule-based deterministic emulator (`DeterministicBackend` in `brain/local_runtime.py`) that matches hardcoded strings in Python (such as `"who are you"`, `"calculator"`, `"chụp màn hình"`).

### Core Forensic Findings:
- **Zero Neural Weights:** A recursive scan across the repository and storage revealed **zero neural weight files** (`.gguf`, `.safetensors`, `.pt`, `.onnx`). The files residing in `brains/` are 53-byte plain text files containing ASCII strings (e.g., `b"AURA_BRAIN_CANDIDATE_aura-local-v1-candidate-5ee064_1"`).
- **Zero Active Local Inference Servers:** Local host ports `11434` (Ollama), `8000` (vLLM), `8080` (llama.cpp), and `1234` (LM Studio) are all closed.
- **Unwired HTTP Backend:** `HttpInferenceBackend` exists in `brain/local_runtime.py`, but is **never instantiated** by `LocalAuraBrain` or any other production class.
- **Unwired Experience Collection:** The `AuraExperienceStore.record_experience()` method is never called in any production chat or agent execution route (`server/routes/chat.py`, `agent/runtime.py`). The production database contains zero user experience records.
- **Simulated Training:** `TrainingJobRunner.run_job()` executes `time.sleep(0.05)`, writes a 53-byte string as `model.bin`, and hardcodes `final_loss: 0.042`. No machine learning framework (`torch`, `transformers`, `unsloth`) is imported or executed.
- **Dormant 24/7 Daemon:** `AuraDaemon` is gated behind `if daemon_cfg.get("enabled", False):` in `server/runtime.py`. Because `config.yaml` contains no `daemon:` section, the daemon is never started during normal server execution.
- **High-Quality Software Core:** Despite the absence of a real local AI model, AURA's **software and orchestration architecture is genuinely exceptional**. The transactional SQLite WAL durable task engine (`agent/task_runtime.py`), AST-verified dynamic tool synthesis (`tools/builder/`), cryptographic evidence verification (`brain/verify/ledger.py`), and atomic package promotion state machine (`brain/package.py`) represent real, robust, and verified production-grade code.

---

## 2. Repository State

- **Repository Root:** `D:\AURA`
- **Git Branch:** `feature/aura-identity`
- **Git Commit Status:** Clean preservation; working tree changes from Phase 5B retained without commit, reset, or checkout.
- **Python Environment:** Python 3.11.9 running in `.venv`.
- **Installed Packages:** `fastapi`, `uvicorn`, `pydantic`, `sqlalchemy`, `google-genai`, `httpx`, `cryptography`, `pytest`, `psutil`. Noticeably absent: `torch`, `torchvision`, `torchaudio`, `transformers`, `accelerate`, `peft`, `bitsandbytes`, `llama-cpp-python`.
- **Active Model Storage:** `D:\AURArains`
  - `brains/brain_state.json`: Active brain points to `aura-local-v1`.
  - `brains/aura-local-v1/manifest.json`: Declares `model_format: "deterministic"`, `parameter_count: "7B"`, `quantization: "Q4_K_M"`. Contains no model binary.
  - Three candidate directories (`aura-local-v1-candidate-5ee064`, `aura-local-v1-candidate-fdf48c`, `aura-local-v1-candidate-fdf48c-candidate-e9d460`) each contain a `manifest.json` and a 53-byte `model.bin`.

---

## 3. Actual Architecture

The operational architecture of AURA 2.0 differs fundamentally between its software control plane and its intelligence plane:

```
+─────────────────────────────────────────────────────────────────────────────────+
|                           AURA 2.0 CONTROL PLANE                                |
+─────────────────────────────────────────────────────────────────────────────────+
|  HTTP REST (/api/chat, /api/agent) | WebSockets (/ws)                           |
+────────────────────────────────────────┬────────────────────────────────────────+
                                         │
                                         ▼
+─────────────────────────────────────────────────────────────────────────────────+
|  ServerRuntime (server/runtime.py)                                              |
|  - Config Loader (config.yaml: provider=gemini)                                 |
|  - Event Bus (events/bus.py)                                                    |
|  - Durable Task Recovery (agent/task_runtime.py) [REAL]                         |
|  - 24/7 Daemon Supervisor (daemon/supervisor.py) [DISABLED BY DEFAULT]          |
+────────────────────────────────────────┬────────────────────────────────────────+
                                         │
                                         ▼
+─────────────────────────────────────────────────────────────────────────────────+
|  BrainRouter (brain/router.py)                                                  |
|  ├─ If AURA_OFFLINE=1 -> LocalAuraBrain (brain/providers/local_aura.py)         |
|  └─ Default -> Primary Cloud Provider (Gemini Flash Lite)                        |
|        └─ Fallbacks: Groq -> Mistral -> OpenRouter                              |
+────────────────────────────────────────┬────────────────────────────────────────+
                                         │
                    ┌────────────────────┴────────────────────┐
                    ▼                                         ▼
+───────────────────────────────────────+ +───────────────────────────────────────+
|     CLOUD INTELLIGENCE (DEFAULT)      | |      LOCAL BRAIN (OFFLINE ONLY)       |
| Provider: Gemini / Groq / Mistral     | | Class: LocalAuraBrain                 |
| Model: gemini-3.5-flash-lite          | | Backend: DeterministicBackend         |
| Transport: HTTPS External APIs        | | Engine: Pure Python if/elif matching  |
| Status: REAL EXTERNAL CLOUD           | | Weights: NONE (53-byte dummy files)   |
+───────────────────┬───────────────────+ +───────────────────┬───────────────────+
                    │                                         │
                    └────────────────────┬────────────────────┘
                                         │
                                         ▼
+─────────────────────────────────────────────────────────────────────────────────+
|  Execution & Task Subsystems                                                    |
|  - AgentRuntime (agent/runtime.py): Permission gating & confirmation token      |
|  - DurableTaskRuntime (agent/task_runtime.py): SQLite WAL transactional engine  |
|  - ToolExecutor (tools/executor.py): Evidence collector & ledger hashing        |
|  - Dynamic Tool Builder (tools/builder/): AST validation & sandboxed execution  |
+─────────────────────────────────────────────────────────────────────────────────+
```

---

## 4. Chat Execution Graph

**Tracing the path of an incoming chat message:**

1. **HTTP Ingestion:** `POST /api/chat` received by FastAPI route handler in `server/routes/chat.py` (`chat_endpoint()`).
2. **Runtime Resolution:** Accesses `request.app.state.runtime` (`ServerRuntime` in `server/runtime.py`).
3. **Session & History:** Queries SQLite `conversations` and `messages` tables via `ConversationManager` in `memory/manager.py`.
4. **Provider Invocation:** Calls `runtime.llm.generate()` or `runtime.llm.stream()` via `BrainRouter` in `brain/router.py`.
5. **Execution Branch:**
   - **Default Execution Branch:** Calls `GeminiProvider.generate()` in `brain/providers/gemini.py`. Dispatches HTTPS call to `generativelanguage.googleapis.com`. Returns generated text.
   - **Offline Execution Branch (when `AURA_OFFLINE=1`):** Calls `LocalAuraBrain.generate()` in `brain/providers/local_aura.py` -> calls `LocalModelRuntime.generate()` -> calls `DeterministicBackend.generate()` in `brain/local_runtime.py`. Runs Python string checks.
6. **Tool Selection & Execution:** If tool calls are generated:
   - Tool arguments are passed to `ToolExecutor.execute()` in `tools/executor.py`.
   - Tool execution captures output into an `Evidence` object.
   - Evidence is recorded in the cryptographically hashed ledger in `brain/verify/ledger.py`.
7. **Response Formatting:** Formatted response is returned to the HTTP client.
8. **Experience Persistence:** **MISSING.** No call to `AuraExperienceStore.record_experience()` is made. The turn vanishes from self-learning memory.

---

## 5. Agent Execution Graph

**Tracing the path of an autonomous goal ("Open settings and check battery"):**

1. **API Ingestion:** `POST /api/agent/intent` or `POST /api/agent/tasks` in `server/routes/agent.py`.
2. **Intent Classification:** Handled by `AgentRuntime.parse_intent()` in `agent/runtime.py`.
3. **Planning:** `CompoundTaskPlanner.plan_from_goal()` in `agent/planner.py`:
   - Queries LLM for JSON execution plan.
   - **Cloud Model Behavior:** Gemini parses goal and outputs structured multi-step JSON plan.
   - **Local Brain Behavior:** `DeterministicBackend` outputs `"Aura local brain response to: ..."`. Planner fails JSON parsing, attempts 2 repairs, and crashes with `ValueError`.
4. **Security & Confirmation Check:** `AgentRuntime` checks required permissions for planned tools. If a tool is marked dangerous (`requires_confirmation=True`), execution halts and returns `CONFIRMATION_REQUIRED`.
5. **Durable Task Registration:** `DurableTaskRuntime.create_task()` in `agent/task_runtime.py`:
   - Persists task and individual steps to `durable_tasks` and `durable_steps` tables in SQLite.
6. **Step Execution Loop:**
   - Step state set to `RUNNING`.
   - Tool executed via `ToolExecutor.execute()`.
   - Result captured in `Evidence` object.
   - Step state updated to `COMPLETED` in SQLite within `with db_lock:` block.
7. **Post-Task Completion:** Task marked `COMPLETED`. Experience recording is **NOT invoked**.

---

## 6. Brain Execution Graph

**Tracing `LocalAuraBrain` internal control flow:**

```
LocalAuraBrain.__init__(runtime=None, manager=None)
  │
  ├─ manager = BrainManager()
  │    └─ Reads brains/brain_state.json
  │
  └─ runtime = LocalModelRuntime(backend=None)
       └─ backend = DeterministicBackend(hardware)  <--- HARDCODED DEFAULT
```

When `LocalAuraBrain.generate_with_tools()` is called:
1. `messages` inspected for last user query.
2. Regex scan runs against configured tool rules.
3. Built-in hardcoded rules evaluated:
   - `"calculator"` in string -> returns `android.launch_app` (`com.android.calculator2`).
   - `"screenshot"` in string -> returns `android.screenshot`.
4. If no rule matches -> returns generic string `"Aura local brain response to: <prompt>"`.
5. **Zero neural weights are consulted. Zero logits are sampled.**

---

## 7. Model Artifact Inventory

A physical audit of the filesystem in `D:\AURA` produced the following inventory:

| Path | File Type | Actual Byte Size | Cryptographic SHA-256 | Analysis |
| :--- | :---: | :---: | :---: | :--- |
| `brains/brain_state.json` | JSON | 842 B | `4480...` | State pointer file tracking active brain and history. |
| `brains/aura-local-v1/manifest.json` | JSON | 412 B | `3f19...` | Package manifest declaring 7B Q4_K_M model. |
| `brains/aura-local-v1/` | Directory | N/A | N/A | **NO weights file present.** |
| `brains/aura-local-v1-candidate-5ee064/model.bin` | ASCII Text | **53 B** | `90b1...` | Plain text: `AURA_BRAIN_CANDIDATE_aura-local-v1-candidate-5ee064_1` |
| `brains/aura-local-v1-candidate-fdf48c/model.bin` | ASCII Text | **53 B** | `40be...` | Plain text: `AURA_BRAIN_CANDIDATE_aura-local-v1-candidate-fdf48c_1` |
| `brains/aura-local-v1-candidate-fdf48c-candidate-e9d460/model.bin` | ASCII Text | **70 B** | `785e...` | Plain text: `AURA_BRAIN_CANDIDATE_..._1` |

**Conclusion:** No genuine neural model artifact exists anywhere in the repository.

---

## 8. Inference Backend Analysis

`brain/local_runtime.py` defines three backend classes:

1. **`InferenceBackend` (Abstract Base Class, L30–56):** Declares `load()`, `unload()`, `is_loaded()`, `generate()`, `generate_with_tools()`, and `stream()`.
2. **`DeterministicBackend` (Active Local Backend, L58–175):**
   - In-process rule engine.
   - Generates responses via `if/elif` string checks.
   - Simulates streaming by splitting strings on spaces and sleeping 10ms per word.
   - **Verdict:** Simulated mock backend.
3. **`HttpInferenceBackend` (Unwired Backend, L187–315):**
   - Connects to `http://127.0.0.1:11434/v1/chat/completions`.
   - Formats standard OpenAI-compatible JSON payloads.
   - **Verdict:** Valid HTTP wrapper, but dead code (never instantiated in production).

---

## 9. Local vs. External Inference

- **Current Default Mode:** **100% External Cloud Inference.** All intelligence is supplied by Google Gemini API endpoints.
- **Current Offline Mode:** **100% In-Process Deterministic Emulation.** Responses are generated by Python string rules.
- **Local Neural Inference:** **0%.** There is no native local inference, and no communication with external local inference daemons (Ollama/llama.cpp).

---

## 10. Tool Execution Authority

- **Implementation:** `tools/executor.py` (`ToolExecutor`).
- **Authority Model:** **Strict & Real.** The LLM cannot hallucinate tool execution.
- **Mechanism:** The LLM can only emit a structured `ToolCallRequest`. The Python runtime intercepts the request, validates arguments against schemas defined in `tools/schema.py`, executes the actual tool code, captures stdout/stderr, and returns a verified `ToolResult`.
- **Verdict: REAL & PRODUCTION GRADE (Class A).**

---

## 11. Evidence / Verifier Authority

- **Implementation:** `brain/verify/ledger.py` and `tools/executor.py`.
- **Authority Model:** **Cryptographic Evidence Ledger.**
- **Mechanism:** Every executed tool generates an `Evidence` record containing:
  - `evidence_id`, `tool_name`, `args_hash`, `output_hash`, `timestamp`.
  - Signatures are chained into an audit trail.
  - The model's claims are compared against the evidence ledger before returning final status to the user.
- **Verdict: REAL & PRODUCTION GRADE (Class A).**

---

## 12. Memory

- **Implementation:** `memory/sqlite.py`, `memory/manager.py`, `memory/selection.py`.
- **Lexical Recall:** Fully operational. Full-text token search over SQLite message tables.
- **Semantic / Vector Recall:** Declared in `config.yaml` lines 85–100 (`provider: hashing` or `ollama`), but disabled by default (`recall: false`). Hashing provides deterministic n-gram overlap, not true dense neural vector embeddings.
- **Verdict: PARTIALLY WIRED (Class B).**

---

## 13. Experience Store

- **Implementation:** `learning/experience.py` (`AuraExperienceStore`).
- **Schema:** `AuraExperienceRecord` table in SQLite (`data/memory.db`).
- **Status:** **Completely unwired from live execution.**
- **Evidence:** `record_experience()` is never called in `server/routes/chat.py` or `agent/`. The SQLite database contains only 7 synthetic records generated during test runs.
- **Verdict: IMPLEMENTED BUT UNWIRED (Class C).**

---

## 14. Learning

- **Implementation:** `learning/pipeline.py` (`LearningCandidatePipeline`).
- **Functionality:** Queries `AuraExperienceStore` for high-quality, privacy-screened experiences and formats them into JSONL training datasets.
- **Status:** Functional in unit tests, but dormant in production due to the empty experience store.
- **Verdict: IMPLEMENTED BUT UNWIRED (Class C).**

---

## 15. Training

- **Implementation:** `learning/training.py` (`TrainingJobRunner`).
- **Functionality:** Claims to fine-tune candidate Brain packages.
- **Source Code Reality:**
  - Executes `time.sleep(0.05)`.
  - Generates 53-byte string `b"AURA_BRAIN_CANDIDATE_..."`.
  - Writes string to `model.bin`.
  - Hardcodes `final_loss: 0.042`.
  - No ML training frameworks are imported or executed.
- **Verdict: SIMULATED / DETERMINISTIC MOCK (Class D).**

---

## 16. Candidate Brain

- **Implementation:** `brain/package.py` (`BrainPackage`).
- **Status:** Package manifests, version numbers (`v1.1.0`), training lineage dictionaries, and SHA-256 hashes are calculated correctly. However, the candidate contains no neural model weights.
- **Verdict: REAL METADATA / MOCK WEIGHTS (Class B / D).**

---

## 17. Evaluation

- **Implementation:** `learning/evaluation.py` (`BrainEvaluator`).
- **Status:** Evaluates models against `IMMUTABLE_REFERENCE_EVAL_SUITE` (8 test cases).
- **Circular Coupling:** The test cases assert against the exact strings hardcoded in `DeterministicBackend`. Both baseline and candidate brains score 1.0 because both execute identical Python rules.
- **Verdict: SIMULATED / CIRCULAR BENCHMARK (Class D).**

---

## 18. Promotion & Rollback

- **Implementation:** `brain/package.py` (`BrainManager.promote_candidate()`, `rollback()`) and `learning/promotion.py` (`LearningCoordinator`).
- **Status:** **Fully operational metadata and pointer state machine.**
- **Mechanism:** Updates `active_brain_id` in `brains/brain_state.json` atomically, updates package manifests, and maintains rollback pointers.
- **Verdict: REAL METADATA STATE MACHINE (Class A) OPERATING OVER MOCK ARTIFACTS.**

---

## 19. 24/7 Daemon

- **Implementation:** `daemon/supervisor.py` (`AuraDaemon`).
- **Status:** Background thread with task worker and learning worker loops exists.
- **Defect:** Gated behind `if daemon_cfg.get("enabled", False):` in `server/runtime.py`. `config.yaml` contains no `daemon:` section.
- **Operational Reality:** In standard production launches, `self.daemon` is `None` and the daemon never executes.
- **Verdict: IMPLEMENTED BUT DISABLED BY DEFAULT (Class B / C).**

---

## 20. Crash Recovery

- **Implementation:** `agent/task_runtime.py` (`DurableTaskRuntime.recover_interrupted_tasks()`).
- **Startup Trigger:** `server/runtime.py` lines 295–306.
- **Status:** Interrupted tasks in `RUNNING` status are recovered on server reboot. Idempotency tokens prevent duplicate execution of mutating actions.
- **Verdict: REAL & PRODUCTION GRADE (Class A).**

---

## 21. Android Integration

- **Implementation:** `tools/providers/android_provider.py`.
- **Status:** Executes genuine ADB shell commands (`adb shell input tap`, `adb shell screencap`) via Python `subprocess`. If no physical/emulated Android device is connected, gracefully reports device unavailability.
- **Verdict: PRODUCTION WIRED & LIVE VERIFIED (Class A).**

---

## 22. Cloud Integration

- **Implementation:** `brain/providers/gemini.py`, `brain/router.py`.
- **Status:** Fully functional cloud provider using Google Gemini API (`gemini-3.5-flash-lite`). Fallback chain configured for Groq, Mistral, and OpenRouter.
- **Verdict: PRODUCTION WIRED & LIVE VERIFIED (Class A / E).**

---

## 23. Offline Operation

- **Implementation:** `brain/router.py`, `brain/local_runtime.py`.
- **Status:** Operates offline **only when `AURA_OFFLINE=1` is explicitly set**.
- **Defect:** If `AURA_OFFLINE=1` is not set and the machine has no cloud API keys, `BrainRouter` throws an unhandled exception and crashes the server on startup.
- **Offline Intelligence:** Purely deterministic Python rules.
- **Verdict: PARTIALLY WIRED / BROKEN CLEAN BOOT (Class B / G).**

---

## 24. Security & Permission Boundaries

- **Implementation:** `agent/runtime.py`, `tools/builder/`, `tools/sandbox/`.
- **Status:**
  - Dangerous tool execution requires explicit user confirmation tokens.
  - Dynamically synthesized tools undergo strict Python AST parsing; forbidden calls (`eval`, `exec`, `subprocess`, socket imports) are rejected.
  - Sandbox dry-runs tools before registration.
- **Verdict: REAL & PRODUCTION GRADE (Class A).**

---

## 25. Database Architecture

- **Implementation:** `memory/sqlite.py`.
- **Status:** SQLite database (`data/memory.db`) configured with Write-Ahead Logging (`WAL`), busy timeouts, foreign keys enabled, re-entrant thread locks (`db_lock`), and SQLAlchemy session scoping.
- **Verdict: REAL & PRODUCTION GRADE (Class A).**

---

## 26. Test Authenticity Analysis

- **The Myth:** "356/356 unit tests passing proves full local AI implementation."
- **The Reality:** The tests pass because they assert against `DeterministicBackend` mocks that were deliberately written to return the exact strings expected by the assertions.
- **Example:** `test_local_aura_brain_identity` asserts that `generate("who are you")` contains `"Tôi là AURA"`. This passes because line 102 of `brain/local_runtime.py` literally returns `"Tôi là AURA, trợ lý cá nhân thông minh và an toàn của bạn."` The tests validate string matching, not neural intelligence.

---

## 27. Live Test Authenticity Analysis

- **The Myth:** `scripts/verify_aura_local_ai_live.py` achieves "LIVE VERIFIED (25/25)".
- **The Reality:** The verification script is a self-contained synthetic harness. It instantiates isolated test stores, manually injects dummy experience records, triggers `TrainingJobRunner` (which sleeps 0.05s and writes a 53-byte string), and asserts that `brain_state.json` was updated. The script verifies metadata plumbing, not live system integration.

---

## 28. Documentation Discrepancies

| Claim in Previous Reports | Forensic Reality | Severity |
| :--- | :--- | :---: |
| "AURA has its own Local Brain" | Zero neural weights exist; runs Python string rules | **CRITICAL** |
| "7B Q4_K_M model loaded in CUDA" | Pure metadata declaration; weights file is absent | **CRITICAL** |
| "AuraExperienceStore captures every turn" | Never called by chat or agent routes | **HIGH** |
| "Continuous local fine-tuning pipeline" | `time.sleep(0.05)` writing 53-byte text files | **CRITICAL** |
| "24/7 Daemon provides continuous vigilance" | Gated off by default; missing from `config.yaml` | **HIGH** |
| "Sub-millisecond local inference" | Instantaneous Python string lookup, not inference | **MEDIUM** |

---

## 29. Dead Code Inventory

1. **`HttpInferenceBackend` (`brain/local_runtime.py`, L187–315):** Complete HTTP client for Ollama/vLLM, never instantiated in production.
2. **`LocalProvider` (`brain/providers/local.py`):** Completely shadowed and replaced by `LocalAuraBrain` in `brain/router.py`.
3. **`inject_regression` parameter in `TrainingJobRunner`:** Exists solely to trigger an artificial failure branch in unit tests.

---

## 30. Unwired Code Inventory

1. **`AuraExperienceStore.record_experience()`:** Fully written persistence and PII screening engine, called 0 times in production workflows.
2. **`LearningCandidatePipeline`:** Fully written dataset generator, produces 0 production datasets because no experiences accumulate.
3. **`AuraDaemon` in `ServerRuntime`:** Fully written background supervisor, disabled by default due to missing config block.

---

## 31. Real vs. Simulated Matrix Reference

For the complete, itemized reality breakdown across all 25 subsystems with classifications A through H, refer to the accompanying document:
👉 [`AURA_REALITY_MATRIX.md`](file:///D:/AURA/AURA_REALITY_MATRIX.md)

---

## 32. Severity-Ranked Findings

### 🔴 Severity P0: Critical Architectural Gaps
1. **Absence of Local Neural Model:** No actual local intelligence exists. Offline mode is an `if/elif` string matcher.
2. **Startup Crash on Clean Machine:** Server crashes immediately without internet or API keys unless `AURA_OFFLINE=1` is manually exported.
3. **Compound Planner Failure on Local Brain:** Multi-step autonomous planning crashes with `DeterministicBackend` because it cannot generate valid JSON plans.

### 🟠 Severity P1: Major Deficiencies & False Claims
1. **Unwired Experience Collection:** Self-learning loop is severed at step 1; conversations are never recorded for learning.
2. **Simulated Training Runner:** Training is a 0.05s sleep writing a 53-byte string; zero neural parameters are learned.
3. **24/7 Daemon Disabled by Default:** Missing from `config.yaml`, leaving the server running as an ephemeral request handler.

### 🟡 Severity P2: Technical Debt & Dead Code
1. Dead `HttpInferenceBackend` sitting uninstantiated in runtime code.
2. Circular evaluation suite that benchmarks deterministic string rules against themselves.

---

## 33. Current AURA Level

Under the standardized Autonomous Agent Intelligence Hierarchy:

- **Orchestration, Durability & Tool Safety:** **LEVEL 4 (Autonomous, Resilient, Production-Ready)**
- **Brain & Intelligence Reality:** **LEVEL 0 (Prompt-Wrapped Cloud) / LEVEL 1 (Deterministic Emulation)**

AURA has built the complete chassis and control telemetry of a high-performance vehicle, but currently has an electric starter motor (deterministic string rules) in place of the engine.

---

## 34. Required Work to Reach Next Level

To transition AURA from Level 0/1 to **Level 2 (True Native Local Model Inference)** and **Level 3 (Real Continuous Adaptation)**:

1. **Wire a Real Local Inference Engine:**
   - Integrate an in-process GGUF engine (via `llama-cpp-python`) or wire `HttpInferenceBackend` to a bundled local server.
   - Package a genuine open-weights base model (e.g., Qwen2.5-Coder-7B or Llama-3.2-3B-Instruct in Q4_K_M GGUF format).
2. **Fix Default Boot Behavior:**
   - Update `BrainRouter` to gracefully fall back to local inference when cloud keys are absent, eliminating startup crashes.
3. **Wire the Experience Store:**
   - Add calls to `AuraExperienceStore.record_experience()` in `server/routes/chat.py` and `agent/task_runtime.py`.
4. **Implement Real Parameter Adaptation:**
   - Replace `time.sleep(0.05)` in `TrainingJobRunner` with a real LoRA fine-tuning script using PEFT/Unsloth or quantized GGUF LoRA training.
5. **Enable the 24/7 Daemon:**
   - Add a default `daemon: enabled: true` configuration block to `config.yaml`.

---

## 35. Final Recommendation & Required Verdict Block

### Final Recommendation:
**Cease declaring AURA as a "fully implemented local-first self-learning AI" until real neural weights are packaged and real parameter fine-tuning is implemented.** 
Acknowledge the immense strength of the Phase 5B durable task and tool architecture, keep that codebase intact, and focus engineering effort squarely on connecting a real GGUF local model runtime and wiring the live experience collection pipeline.

---

### Required Section 48 Verdict Block

```
AURA CURRENT STATE:
    LEVEL 1 (Level 4 Tool/Task Architecture with Level 0 Cloud / Level 1 Deterministic Brain)

LOCAL BRAIN:
    MOCK (Deterministic string matcher; no neural weights)

AURA-SPECIFIC MODEL:
    NO (Manifests declare 7B Q4_K_M, but physical files are 53-byte ASCII strings)

REAL SELF-LEARNING:
    NO (Experience store is unwired to chat/agent; no live data collected)

REAL TRAINING:
    NO (TrainingJobRunner runs time.sleep(0.05) and writes dummy 53-byte strings)

24/7 RUNTIME:
    PARTIAL (Durable task recovery is real; 24/7 daemon is disabled by default in config.yaml)

OFFLINE:
    PARTIAL (Operates offline only if AURA_OFFLINE=1 is set; otherwise crashes without keys)

SELF-EXTENSION:
    REAL (Dynamic tool synthesis with AST validation and sandbox testing is fully functional)

CURRENT BIGGEST GAP:
    Complete absence of a real neural model artifact and real local inference engine.

NEXT MOST IMPORTANT ENGINEERING STEP:
    Package a real GGUF model (e.g., Qwen2.5-Coder-7B-Instruct-Q4_K_M) and wire LocalAuraBrain to execute real token generation via in-process llama-cpp or active HTTP inference.
```
