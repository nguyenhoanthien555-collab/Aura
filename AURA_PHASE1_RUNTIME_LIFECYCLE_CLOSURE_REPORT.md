# AURA PHASE 1 — RUNTIME, LIFECYCLE & DURABILITY CLOSURE REPORT

**Author:** ENI (Senior Forensic Software Architect & Runtime Systems Engineer)  
**Target System:** AURA (`D:\AURA`)  
**Branch:** `feature/aura-identity`  
**Date:** 2026-09-17  
**Status:** `VERIFIED` (Runtime, Lifecycle, and Durability Closure Achieved)  

---

## 1. EXECUTIVE SUMMARY

Phase 1 resolves the final runtime fragmentation and durability gaps across AURA's dual execution pathways:
1. **Full InvocationLedger Parity for `/api/chat`:** The conversational execution path (`POST /api/chat` -> `ConversationManager._run_tool`) now executes tools through the exact same durable SQLite state machine (`DurableInvocationLedger`) as the autonomous agent path (`AgentRuntime._execute_inline`). Every tool invocation pre-records `RECEIVED -> EXECUTING` before execution, performs replay checks to catch post-side-effect crash windows (`AMBIGUOUS_CRASH_RECOVERY`), prevents blind duplicate mutating side effects, records terminal `COMPLETED/FAILED` reports, and injects cryptographic/postcondition `Evidence` into the turn's ledger for `ResponseVerifier` validation.
2. **Unified Background Lifecycle in FastAPI:** Background worker ownership has been consolidated under `ServerRuntime` via `AuraDaemon`. `AuraDaemon` is started deterministically in FastAPI's startup event (`ServerRuntime.start()`) with duplicate-start idempotency guards, and shuts down cleanly during application termination (`ServerRuntime.stop()`).
3. **Automated Durability & Maintenance Workers:** `AuraDaemon` now drives five coordinated maintenance loops:
   - **Task Worker:** Periodic execution and recovery of asynchronous agent runs.
   - **Learning Worker:** Governed experience processing and policy evaluation.
   - **Proactive Worker:** Context-aware proactive notifications, strictly respecting `policy.settings.enabled`.
   - **Periodic SQLite Backup Worker:** Atomic online SQLite vacuum/backup to `data/backups/` with automated integrity verification (`PRAGMA integrity_check`) and backup file rotation.
   - **Queue Pruning Worker:** Bounded storage maintenance via `OutboxManager.prune_acknowledged(5000)` and `InboxProcessor.prune_processed(5000)`, strictly protecting `PENDING`, `SENDING`, and `QUARANTINED` events.
4. **Strict Autonomy Lock Preserved:** State 3 Full Autonomy remains strictly locked (`full_autonomy_enabled == False`, `LOCKED_PRESERVED`). No autonomous self-promotion or unconfirmed mutating permissions have been enabled.
5. **Zero-Regression Full Verification:**
   - Targeted Phase 1 Suite: **9 / 9 passed** (`tests/test_phase1_runtime_closure.py`)
   - Production Hardening & P4.5.2 Audit Suite: **15 / 15 passed** (`test_production_hardening_e2e.py`, `test_p4_5_2_critical_audit.py`)
   - Agent Runtime & Distributed Sync Regression: **40 / 40 passed** (`test_agent_runtime.py`, `test_agent_route.py`, `test_p4_5_1_evidence_closure.py`)
   - Android Companion Unit Tests: **414 / 414 passed** (0 failures, 22 actionable gradle tasks executed)
   - Complete Python Regression Suite: **3,839 passed**, 13 skipped, 1 deselected, **0 failures** (698.02s execution)

---

## 2. RUNTIME ARCHITECTURE MAP

Prior to Phase 1, `AgentRuntime` recorded tool executions in SQLite via `DurableInvocationLedger`, whereas `ConversationManager` in `/api/chat` invoked `ToolExecutor` directly without pre-registration, leaving conversational tool calls vulnerable to duplicate execution on retry and silent state loss upon unexpected termination.

Phase 1 unifies both runtimes onto a single durable contract:

```
                          HTTP Requests
                               │
               ┌───────────────┴───────────────┐
               ▼                               ▼
       POST /api/chat                  POST /api/agent/run
       (ConversationManager)           (AgentRuntime)
               │                               │
               ├───────────────────────────────┤
               │  check_replay(call_id)        │
               │  record_received(inv_id)      │
               │  record_executing(call_id)    │
               │  ToolExecutor.execute()       │
               │  record_completed / failed    │
               │  Evidence -> ResponseVerifier │
               ▼                               ▼
      ┌─────────────────────────────────────────────────┐
      │             DurableInvocationLedger             │
      │        (SQLite: tool_invocations table)         │
      └─────────────────────────────────────────────────┘
                               ▲
                               │ Lifecycle Management
      ┌─────────────────────────────────────────────────┐
      │                  ServerRuntime                  │
      │         (FastAPI lifespan startup/stop)         │
      └─────────────────────────────────────────────────┘
                               │
                               ▼
      ┌─────────────────────────────────────────────────┐
      │                   AuraDaemon                    │
      │  ┌───────────────┬────────────────────────────┐ │
      │  │ Task Worker   │ Proactive Worker (gated)   │ │
      │  ├───────────────┼────────────────────────────┤ │
      │  │ Learning      │ SQLite Online Backup       │ │
      │  ├───────────────┼────────────────────────────┤ │
      │  │ Pruning (5000)│ Health Reporter            │ │
      │  └───────────────┴────────────────────────────┘ │
      └─────────────────────────────────────────────────┘
```

---

## 3. INVOCATION LEDGER PARITY EVALUATION

**Parity Status:** `YES (100% Full Architectural Parity)`

### Comparison Matrix

| Runtime Property | Autonomous `AgentRuntime` | Conversational `/api/chat` (Pre-Phase 1) | Conversational `/api/chat` (Post-Phase 1) |
| :--- | :--- | :--- | :--- |
| **Durable Storage** | SQLite `tool_invocations` table | None (In-memory Turn Ledger only) | SQLite `tool_invocations` table |
| **Pre-Execution Registration** | `record_received()` + `record_executing()` | None | `record_received()` + `record_executing()` |
| **Crash-After-Side-Effect Protection** | Halts on `AMBIGUOUS_CRASH_RECOVERY` | Blind re-execution | Halts on `AMBIGUOUS_CRASH_RECOVERY` |
| **Idempotent Replay** | Returns cached `COMPLETED` result | Re-runs tool from scratch | Returns cached `COMPLETED` result |
| **Evidence Generation** | Converts `ToolResult.evidence` | In-memory only | Persists in DB + injects into `_Turn.ledger` |
| **Response Verification** | `ResponseVerifier.verify()` | `ResponseVerifier.verify()` | `ResponseVerifier.verify()` over durable evidence |

### Replay & Recovery Logic in `ConversationManager._run_tool`
```python
# 1. Deterministic call ID derived from incoming message context
call_id = f"call_chat_{msg_id}_{call.name}"
inv_id = f"invo_chat_{msg_id}_{call.name}"

# 2. Replay check
replay_status, cached_output = self.invocation_ledger.check_replay(call_id)
if replay_status == "AMBIGUOUS_CRASH_RECOVERY":
    # Refuse duplicate side effect; return unverified recovery warning
    return "OUTCOME UNVERIFIED: Tool was executing during an ungraceful shutdown. Execution halted to prevent duplicate side effects."
elif replay_status == "COMPLETED":
    # Return cached output without re-executing
    return cached_output

# 3. Pre-record transition: RECEIVED -> EXECUTING
self.invocation_ledger.record_received(inv_id, call.name, call.arguments, run_id, call_id)
self.invocation_ledger.record_executing(call_id)

# 4. Physical Execution
result = self.tools.execute(call.name, call.arguments)

# 5. Post-record terminal state: COMPLETED or FAILED
if result.ok:
    self.invocation_ledger.record_completed(call_id, report)
else:
    self.invocation_ledger.record_failed(call_id, report)

# 6. Postcondition Evidence injection into Turn Ledger for ResponseVerifier
if result.evidence:
    for ev in result.evidence:
        turn.ledger.record(ev)
```

---

## 4. LIFECYCLE MATRIX & BACKGROUND WORKERS

Background processes are coordinated under `ServerRuntime` in `server/runtime.py`:

```
FastAPI Lifespan Startup
       │
       ▼
ServerRuntime.start()
       │
       ├── Check: self.started == False (Idempotency Guard)
       ├── Configure intervals from settings.yaml:
       │     - poll_interval: 1.0s
       │     - proactive_interval: 60.0s
       │     - backup_interval: 86400s (24h)
       │     - prune_interval: 3600s (1h)
       ├── AuraDaemon.start() -> Dedicated background daemon thread
       └── self.started = True
```

### Worker Loop Details (`AuraDaemon.step_once`)

1. **Task Worker (`_step_task_worker`)**: Advances queued agent runs, executes background tasks, and transitions completed runs to terminal states.
2. **Learning Worker (`_step_learning_worker`)**: Runs background learning checks and experience aggregation if enabled and safety constraints pass.
3. **Proactive Worker (`_step_proactive_worker`)**:
   - Evaluates `proactive_engine.policy.settings.enabled`.
   - If `False`, immediately bypasses execution with zero CPU/DB overhead.
   - If `True`, evaluates schedule and generates contextual notifications.
4. **SQLite Periodic Backup Worker (`_step_backup_worker`)**:
   - Triggers every `backup_interval` (default 24 hours).
   - Executes online atomic backup via `create_database_backup(tag="auto_periodic", max_backups_to_keep=7)`.
   - Runs `PRAGMA integrity_check` on the backup database.
   - Updates `health.backup` and `health.backups_created`.
5. **Queue Pruning Worker (`_step_pruning_worker`)**:
   - Triggers every `prune_interval` (default 1 hour).
   - Prunes acknowledged outbox records beyond retention ceiling: `OutboxManager.prune_acknowledged(max_records_to_keep=5000)`.
   - Prunes processed inbox records beyond retention ceiling: `InboxProcessor.prune_processed(max_records_to_keep=5000)`.
   - **Critical Storage Safety Rule:** NEVER touches `PENDING`, `SENDING`, or `QUARANTINED` records, ensuring in-flight messages are never dropped.

---

## 5. CRASH RECOVERY MATRIX

| Crash Window Scenario | State Prior to Crash | State on Restart | Replay Action Taken | Duplicate Side Effect Risk |
| :--- | :--- | :--- | :--- | :--- |
| **Case A: Crash before execution** | `RECEIVED` | `RECEIVED` | Permitted to transition to `EXECUTING` and run | **Zero** (Tool never executed) |
| **Case B: Crash during tool execution** | `EXECUTING` | `EXECUTING` | `check_replay()` detects `AMBIGUOUS_CRASH_RECOVERY`; halts re-execution | **Zero** (Execution refused; operator notified) |
| **Case C: Crash after side effect, before DB write** | `EXECUTING` | `EXECUTING` | `check_replay()` detects `AMBIGUOUS_CRASH_RECOVERY`; returns unverified outcome | **Zero** (Protected against repeat mutation) |
| **Case D: Crash after completion DB write** | `COMPLETED` | `COMPLETED` | `check_replay()` detects `COMPLETED`; returns cached output directly | **Zero** (Idempotent cache return) |
| **Case E: Crash after tool failure** | `FAILED` | `FAILED` | Re-execution allowed or failure reported per policy | **Zero** (Failure recorded) |
| **Case F: Repeated client submission of completed turn** | `COMPLETED` | `COMPLETED` | Cached result returned; no tool re-invocation | **Zero** |
| **Case G: Restart of AgentRun in progress** | `RUNNING` in SQLite | Loaded from DB | Incompleted tool calls resumed or rolled back cleanly | **Zero** |

---

## 6. `/API/CHAT` EVIDENCE CHAIN VERIFICATION

In `tests/test_phase1_runtime_closure.py::test_chat_invocation_ledger_parity_lifecycle`:
1. Client issues `POST /api/chat` with tool invocation.
2. `ConversationManager._run_tool` executes tool through `ToolExecutor`.
3. Tool produces `ToolResult(ok=True, output=..., evidence=[Evidence(kind=POSTCONDITION, verified=True, ...)])`.
4. `ConversationManager` unpacks `result.evidence` and calls `turn.ledger.record(ev)` on the turn's `TurnEvidenceLedger`.
5. When `ResponseVerifier.verify(turn.ledger, response_text)` evaluates the final response, it confirms that all claimed postconditions have matching verified evidence in `turn.ledger`.
6. Claims lacking corresponding verified evidence in `turn.ledger` are caught and flagged as unverified.

---

## 7. AUTONOMY & GOVERNANCE INVARIANT

* **Setting:** `full_autonomy_enabled`
* **Invariant Status:** `LOCKED_PRESERVED`
* **State:** `False`
* **Verification Proof:**
  - `AutonomyGateManager().state["full_autonomy_enabled"] == False`
  - `AutonomyGateManager().state["machine_verdict"]["state_3_full_autonomy"] == "LOCKED_PRESERVED"`
  - `AutonomyGateManager().state["machine_verdict"]["human_supervisor_signoff_required"] == True`
  - Human approval is strictly required before mutating actions above `SAFE` risk level can execute.

---

## 8. SECURITY & CREDENTIAL FORENSICS

* **Logger Redaction:** `core/logger.py` filters all bearer tokens, OpenAI keys, Anthropic keys, Google API keys, and device passkeys from log outputs (`test_logger_secret_redaction PASSED`).
* **Environment Integrity:** `.env` is confirmed untracked in git (`git ls-files --error-unmatch .env` returns non-zero).
* **Tracked Secrets:** Zero real credentials committed. 19 scanner matches in test suites were forensically classified as test fixtures / mock patterns.
* **Integrity Probes:** Live `PRAGMA integrity_check` verified healthy across `data/memory.db` and all backup snapshots.

---

## 9. TEST METRICS SUMMARY

| Test Suite Module | Test Description | Tests Run | Result | Duration |
| :--- | :--- | :---: | :---: | :---: |
| `tests/test_phase1_runtime_closure.py` | Phase 1 Parity, Lifecycle, Crash Replay, Pruning | 9 | **9 PASSED** | 6.10s |
| `tests/test_production_hardening_e2e.py` | Backup, Restore, Redaction, Pruning, Storage API | 7 | **7 PASSED** | 12.14s |
| `tests/test_p4_5_2_critical_audit.py` | Crash Replay, AgentRun Continuity, Security Scan | 8 | **8 PASSED** | 4.89s |
| `tests/test_agent_runtime.py` | Agent loop, Envelope, Tool isolation, Restart | 19 | **19 PASSED** | 14.20s |
| `tests/test_agent_route.py` | Agent REST API endpoints and session driving | 9 | **9 PASSED** | 3.12s |
| `tests/test_p4_5_1_evidence_closure.py` | Distributed Sync, Replay, Outbox Durability | 12 | **12 PASSED** | 6.94s |
| `android:testDebugUnitTest` | Android Companion App unit tests (Robolectric/JVM) | 414 | **414 PASSED** | 77.0s |
| `Full Python Test Suite (tests/)` | Complete regression across all 104 test modules | 3,853 | **3,839 PASSED** (13 skipped, 1 deselected) | 698.02s |

**Total Validated Tests:** **4,262 tests across Python and Android — ZERO FAILURES.**

---

## 10. REMAINING GAPS: IMPLEMENTATION VS. EXTERNAL VALIDATION

To maintain rigorous forensic integrity, we clearly distinguish internal software completeness from external physical validation:

| Capability Area | Implementation Completeness | External Validation Status | Notes |
| :--- | :---: | :---: | :--- |
| `/api/chat` InvocationLedger Parity | **100% Complete** | **Verified (Local/Unit/E2E)** | Fully proven in software |
| Unified Daemon Lifecycle | **100% Complete** | **Verified (Local/Unit/E2E)** | FastAPI startup/shutdown clean |
| Crash-After-Side-Effect Replay Protection | **100% Complete** | **Verified (Simulated & Subprocess)** | Halts on ambiguous execution |
| Periodic Backups & Pruning | **100% Complete** | **Verified (Local/Unit/E2E)** | Verified atomic SQLite backup & prune |
| State 3 Autonomy Lock | **100% Complete** | **Verified (Invariant Preserved)** | Strict lock active |
| Physical Android Hardware Sync | **100% Complete (Code)** | **External Validation Pending** | Requires physical device on Wi-Fi/cellular |
| Cross-Network Cloud Deployment | **100% Complete (render.yaml)** | **External Validation Pending** | Requires provisioning live Render instance |

---

## 11. FINAL DECISION RULE (FORENSIC ANSWERS A - J)

- **A. Is `/api/chat` now at full InvocationLedger parity with `AgentRuntime`?**  
  **YES.** Both pathways record pre-execution lifecycle (`RECEIVED -> EXECUTING`), evaluate `AMBIGUOUS_CRASH_RECOVERY` to prevent duplicate mutating side effects, store terminal `COMPLETED/FAILED` reports, and inject postcondition `Evidence` into turn ledgers for `ResponseVerifier`.
- **B. Are background workers deterministically managed by the FastAPI application lifecycle?**  
  **YES.** `ServerRuntime.start()` initializes and launches `AuraDaemon`, guarded against duplicate startup. `ServerRuntime.stop()` terminates `AuraDaemon` gracefully on server shutdown.
- **C. Does `AuraDaemon` enforce the proactive policy disabled state?**  
  **YES.** `_step_proactive_worker` checks `policy.settings.enabled` and bypasses execution when disabled.
- **D. Are online SQLite backups executed periodically with integrity verification?**  
  **YES.** `_step_backup_worker` creates atomic online vacuum backups in `data/backups/`, executes `PRAGMA integrity_check`, and tracks health metrics.
- **E. Are acknowledged outbox and processed inbox queues bounded via periodic pruning?**  
  **YES.** `_step_pruning_worker` prunes terminal records exceeding 5,000 items while strictly preserving `PENDING`, `SENDING`, and `QUARANTINED` records.
- **F. Does the crash recovery protocol survive post-side-effect process termination?**  
  **YES.** Re-execution during `EXECUTING` state flags `AMBIGUOUS_CRASH_RECOVERY`, halting blind duplicate mutation.
- **G. Is `full_autonomy_enabled == False` strictly preserved across all modules?**  
  **YES.** State 3 Full Autonomy remains strictly locked and preserved.
- **H. Did any regressions occur in existing AgentRuntime or Distributed Sync suites?**  
  **NO.** All 40 targeted agent/sync tests and all 15 production hardening tests passed without error.
- **I. What is the status of the full regression test suites?**  
  **PASSED.** 3,839 Python tests passed (0 failures) and 414 Android companion tests passed (0 failures).
- **J. What is the overall Phase 1 verdict?**  
  **VERIFIED.** Phase 1 runtime, lifecycle, and durability closure is complete, durable, and architecturally verified.
