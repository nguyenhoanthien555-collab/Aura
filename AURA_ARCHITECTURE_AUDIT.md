# AURA Architecture Audit — Phase 0 (AURA 2.0 Master Contract)

- Date: 2026-08-28
- Branch: `feature/aura-identity`, HEAD `a97bc69` (pushed to origin)
- Auditor: coding agent session, following `.Codex` persistent state
- Method: Evidence-first. Every status below cites the repository file(s) it
  rests on. Nothing was assumed from documentation alone; the `.Codex`
  state files and the current working tree are the sources of truth.

**STATUS: This audit requires human review before Phase 1 begins (contract,
Phase 0 "Human Review" gate).**

---

## 1. What Aura already is (system map)

Aura is a two-sided system:

- **Server (Python, FastAPI):** `server/` (authenticated HTTP/WS API,
  `server/routes/agent.py` agent runs, `server/device_gateway.py` device
  transport, `server/errors.py` error taxonomy), `brain/` (conversation,
  prompt sections, native function calling, providers, planner, task graph,
  recovery, streaming), `core/` (capabilities registry/discovery/health/
  permissions, observations store, cognitive state, ids, logger), `memory/`
  (SQLAlchemy models, pipeline, selection, profile, episodic, retrieval),
  `tools/` (registry, executor, schema, timeout, builtins, android bridge).
- **Device (Kotlin, Android 13 target):** `android/app/` companion
  (`com.aura.companion`) with `AuraAccessibilityService`,
  `AccessibilityToolDispatcher` (14 canonical Android tools),
  `AgentRunDriver` (server-owned multi-round loop), `ScreenObservationService`.

The governing invariant, implemented and pinned by tests
(`tests/test_autonomous_skills.py`, `tests/test_recovery.py`):

```
intent -> discovery -> capability registry -> permission
       -> health/dependency -> ToolExecutor -> real Android
       -> ToolResult -> LLM
```

## 2. Contract requirement matrix

Status legend: **EXISTS** (implemented and tested), **PARTIAL** (core exists,
contract-named artifact or scope missing), **MISSING** (no implementation
found).

### Phase 0 — Audit & diagnostics

| Requirement | Status | Evidence |
|---|---|---|
| Architecture audit document | PARTIAL → this file | `docs/IMPLEMENTATION_STATUS.md`, `docs/ROADMAP.md` cover systems and limitations; this file adds the contract mapping |
| Per-request diagnostic trace (request_id, intent, provider, tokens, memory_hits, tool results) | PARTIAL | `core/ids.py` (run/task/tool-call/session ids), `core/observations.py` (ObservationStore with `provenance`, usage metadata), `events/types.py` (ToolInvoked/ToolCompleted events), `core/logger.py`. **No consolidated per-request JSON trace record exists.** |
| Streaming token trace (tokens LLM→backend vs backend→UI) | MISSING | `brain/streaming.py` emits fragment events; no token-count reconciliation across the three hops was found |
### Phase 1 — Provider & model routing

| Requirement | Status | Evidence |
|---|---|---|
| Unified function-calling interface | EXISTS | `brain/native_fc.py` (`ToolCallRequest`, `ModelTurn`), `tools/schema.py` (one JSON-Schema source of truth → OpenAI wire form), `generate_with_tools` port; prose-parsing fallback removed by design (`docs/AGENT_MIGRATION.md`, `server/routes/agent.py` "no fallback to the old prose parsing") |
| Per-provider capability registry (`function_calling`, `streaming`, `vision`, `max_context_tokens`) | MISSING | Providers exist (`brain/providers/`: openai_compatible, gemini, mistral, openrouter, fallback) but carry no capability flags; routing is config-order fallback (`llm.fallback_providers`), not capability-driven selection |
| Fallback only on provider error, never on answer quality | EXISTS | `brain/providers/fallback.py` tries next provider only on typed errors; `tests/test_provider_resolution.py` pins "a name it does not implement must be skipped rather than guessed at" |
| Explicit CAPABILITY_ERROR on missing capability | PARTIAL | Device capabilities raise `CAPABILITY_UNAVAILABLE` with `execution=not_attempted` (`tools/executor.py` gate, verified in `tests/test_device_bridge.py`). The LLM-provider-side equivalent (function-calling unsupported) is not a distinct typed path because providers are all FC-capable by construction today |

### Phase 2 — Memory & context engine

| Requirement | Status | Evidence |
|---|---|---|
| Long-term structured facts with provenance | EXISTS | `memory/models.py` `UserFact` (key/value/category/source/created_at/updated_at), `memory/profile.py` durable SQLite; `source` distinguishes stated vs inferred |
| Conflict detection (not silent override) | EXISTS | `memory/pipeline.py` `remember_user_correction`; `memory/user_profile_seed.py` "leaves every existing entry alone, including corrections"; `config.yaml` seeded-profile rules |
| Working/session memory | EXISTS | `memory/companion.py` (session-only), `memory/temporary.py` ("I'm at a cafe" expires by design), `core/cognitive.py` `CognitiveState` (live task/device state) |
| Semantic (vector) recall | EXISTS (Phase 2, off by default) | Added 2026-08-29 beside the preserved lexical path, per ADR-007 (`.aura/decisions/ADR-007.md`). `memory/embeddings.py` (provider abstraction: hashing/ollama/remote), `memory/semantic.py` (`SemanticIndexer`, `SemanticRetriever`, `HybridRetriever` with RRF fusion), `memory/models.py` `SemanticVector` in the existing SQLite database. `memory.semantic.enabled` ships **false**; degradation to lexical-only is pinned by `tests/test_semantic_memory.py` (43 tests). Measured, not asserted: `scripts/benchmark_semantic.py` — recall@3 lexical 0.583 → semantic 0.708, at the cost of 1.0 noise memories per unanswerable query (lexical 0.0) |
| Context composer with prioritized sections | EXISTS | `brain/prompt_sections.py` (section vocabulary incl. TOOLS vs TOOL RESULTS split), `brain/providers/base.py` `split_prompt` (system vs user slots), `brain/consistency.py` drift prevention. Token-budget prioritization is implicit (section assembly), not a measured budget allocator |
| Episodic memory with timestamps | EXISTS | `memory/models.py` `EpisodicMemory` (occurred_at, importance, confidence) |

### Phase 3 — Tool system & execution

| Requirement | Status | Evidence |
|---|---|---|
| Tool registry with input schemas | EXISTS | `tools/registry.py` (shape-checked, duplicate-rejecting), `tools/schema.py` (JSON-Schema export; unknown types refused loudly) |
| Risk levels + human confirmation gate | EXISTS | `tools/base.py` `ToolRisk` (SAFE/RISKY/SENSITIVE/DANGEROUS ladder), `tools/executor.py` gate 4: "Defaults to refusal… a SENSITIVE or DANGEROUS tool cannot run" without a wired confirmation callback |
| Argument validation before execution | EXISTS | `tools/executor.py` gate 5 (plain-data shape check, `MAX_ARGUMENT_DEPTH`; parameter types validated against the same table the output validator uses — one vocabulary) |
| Execution state machine + attempt tracking | EXISTS | `core/cognitive.py` `ActionRecord`/`ActionState` with attempts; `brain/task_graph.py` (PENDING/RUNNING/SUCCESS/FAILED/SKIPPED/BLOCKED/RECOVERING, derived — never stored twice); `brain/recovery.py` bounded repeats ("Never blindly repeat the same action forever"); `android/.../ActionIdentityTest.kt` (same-action identity across server/device) |
| Side-effect classes + derived retry semantics | EXISTS (Phase 3) | `tools/outcome.py` `SideEffect` (READ_ONLY / IDEMPOTENT / NON_IDEMPOTENT / UNKNOWN, UNKNOWN default = not safe to repeat) and `retryability_of(status, side_effect)` → SAFE / NOT_SAFE / UNKNOWN — derived, never asserted, so a side-effecting UNKNOWN can never be auto-retried. Per-execution identity: `execution_id` + `started_at`/`completed_at` stamped on every result (`tools/executor.py`, `core/ids.py`) |
| Postcondition verification | EXISTS | `tools/executor.py` local `verify` re-check ("a phone performed, reported back over the wire; this re-checks a local tool's own postcondition"); device-side `android.verify` / `wait_for` with screen fingerprints (`android/.../SubmitVerificationTest.kt`), `VERDICT` FAILED/UNVERIFIED distinction; Phase 3 `Evidence` (kind/source/verified/timestamp/reference) with `POSTCONDITION`/`OBSERVATION`/`RECEIPT`/`RETURN_VALUE` kinds |
| Structured failure taxonomy (no fabricated results) | EXISTS | `tools/providers/android_bridge.py` (NODE_NOT_FOUND, INVALID_ARGUMENTS survive into ToolResult), `server/errors.py`, `scripts/real_acceptance.py` negative paths (TOOL_NOT_FOUND, CAPABILITY_UNAVAILABLE) |
| Canonical status taxonomy + structured errors | EXISTS (Phase 3) | `tools/outcome.py` `ToolStatus` (SUCCESS/FAILED/PARTIAL/DENIED/UNAVAILABLE/INVALID_ARGUMENTS/TIMEOUT/CANCELLED/UNKNOWN), `ToolError` (code/category/message/provider/capability, category derived from code), `ToolErrorCategory` (VALIDATION/PERMISSION/POLICY/CAPABILITY/PROVIDER/NETWORK/TIMEOUT/EXECUTION/INTERNAL/UNKNOWN). `ToolResult.ok` is reconciled FROM status — only SUCCESS is truthy, so UNKNOWN cannot be read as success |
| Output schemas per tool | EXISTS (Phase 3) | `tools/schema.py` `output_schema()` + `validate_output()` (small subset validator — type at root, required/property types one level deep, items type for arrays, microseconds). `Tool.output_schema` declares a canonical shape; malformed output is downgraded to UNKNOWN, never accepted silently (`tools/executor.py`) |
| Structured tool result + model serialization | EXISTS (Phase 3) | `ToolResult.status/error_code/evidence/execution_id/started_at/completed_at/side_effect`; `serialize_for_model()` renders fixed STATUS/TOOL/EVIDENCE/RETRY/OUTCOME/ERROR lines — deterministic, secrets-free, wired into `brain/conversation.py._render_result` |
| External tool registry (MCP-adapted) | EXISTS (Phase 3) | `tools/registry.py.definitions()` → one canonical machine-readable definition per tool (name, description, input/output schema, side_effect, risk, version, capability); `export_mcp()` renders the standards `tools/list` shape (name/description/inputSchema). Deliberately code-derived in-process — no static YAML file that could drift from the tools it describes; availability is a live fact joined at runtime, not baked into the snapshot |

### Phase 4 — Grounding, evidence, verifier

| Requirement | Status | Evidence |
|---|---|---|
| Evidence-first response policy | EXISTS | `brain/prompt_sections.py`: "TOOL RESULTS … is the only thing entitled to make her say an action succeeded"; `prompts/system.md` grounding principles; observation-before-answer and postcondition verification enforced in `agent/runtime.py` (stronger than prompt text, per `.Codex/progress.md`) |
| Evidence store with provenance | EXISTS | `core/observations.py` `ObservationStore` (`provenance`, `session_id`, kinds) |
| Never claim unexecuted actions | EXISTS | Bypass audit (`.Codex/progress.md` 2026-08-27): zero server-side fabricated observations; `tests/test_device_boundary.py` (Render cannot touch a physical PC "and says so"); `tests/test_error_visibility.py` |
| Response verifier before delivery | PARTIAL | The native-FC loop converges on text "once verification agrees" (`brain/native_fc.py` ModelTurn contract), and plans verify postconditions. **No standalone rule-based claim→evidence verifier runs over free-form chat responses.** |
| FACT/INFERENCE/UNKNOWN labeling | PARTIAL | `prompts/system.md` requires unavailable capabilities to state limitation and cause; explicit tri-state claim labeling is not enforced or verified |

### Phase 5 — Android integration & skills

| Requirement | Status | Evidence |
|---|---|---|
| Tool execution through device (14 canonical Android tools) | EXISTS | `tools/providers/android_provider.py`, `AccessibilityToolDispatcher.kt`, `DeviceToolCatalog` (catalog-parity test `AgentToolProtocolTest.kt`), live-verified on 2026-08-27 (see `.Codex/current-task.md`) |
| Just-in-time capability gating on device | EXISTS | Dispatcher re-checks runtime capability status before dispatch (committed a97bc69) |
| Heartbeat-derived availability (never hardcoded) | EXISTS | `GatewayDeviceBridge.status()` delegates to real heartbeat; permissive branch unreachable in production (bypass audit, `.Codex/progress.md`) |
| App inventory via PackageManager | EXISTS, live VERIFIED (2026-09-05) | Phase 5A (`android.list_apps` / `android.app_inventory`, ADR-010): `AppInventory.kt` enumerates via a `PackageSource` seam over `PackageManager`. Verified on `IBCQMB4PTGNZJVTO` (API 33): 277 packages in 3.80 s, fresh `observed_at`, `READ_ONLY`, capability `AVAILABLE` over a live heartbeat, 0 package names in 10,567 diagnostics lines |
| Calendar / email / SMS / contacts tools | MISSING | The 14 Android tools are accessibility primitives (ui_tree, find_node, screenshot, tap, type_text, key_input, back, home, launch_app, wait_for, verify, …). No calendar-provider, email, SMS, or contacts tools exist |
| AppFunctions (Android 16+) | NOT APPLICABLE / UNKNOWN | Target device is API 33; `AppFunctionManager` is Android 16+. Requires a documented decision before any work |
| Exact-alarm policy (`canScheduleExactAlarms`) | NOT APPLICABLE | No alarm tool exists |
| Runtime permission request flow on device | PARTIAL | Accessibility is the one permission in play and is verified live; no general runtime-permission request tooling |

### Phase 6 — Knowledge, voice, production

| Requirement | Status | Evidence |
|---|---|---|
| Web search / RAG tool | MISSING | No web-search tool found in `tools/builtins/` (apps, capabilities, chat, clock, commands, desktop, filesystem, input, memory, screen, system, vision) |
| Voice pipeline | EXISTS | `voice/tts/` (Edge TTS, pacing, streaming, cancellation); transcription path unverified against the same agent loop |
| Observability dashboard | MISSING | Structured ids/events exist (Phase 0 row above); no queryable per-request dashboard |
| CI | PARTIAL | `.github/` exists; contract requires emulator CI + status badges — not verified in this audit (device-dependent suites cannot run here) |
| INTEGRATION_LEDGER.md | MISSING | No occurrences in the repository |
| Test suite status | EXISTS | 2026-08-28: full Python suite 3246 passed / 2 skipped / 6 failed (5 pre-existing settings-restart, 1 environmental); targeted 563 passed / 1 failed; Android JVM 388 tests, 0 failures |

## 3. Verified gaps (ranked by contract impact)

1. **Provider capability registry (P1):** RESOLVED 2026-08-28 (Phase 1).
   `brain/providers/capabilities.py` declares per-provider capability
   status; `RouterToolCallingLLM` routes capability-first; FC statuses are
   UNKNOWN until a real request demonstrates them (VERIFIED via
   `mark_function_calling_verified`); `CapabilityUnavailableError` maps to
   HTTP 501. Pinned by `tests/test_capability_routing.py`.
2. **Per-request diagnostic trace (P0):** RESOLVED 2026-08-28 (Phase 1).
   `core/trace.py` emits one JSON line per request to
   `logs/diagnostics.jsonl`; `agent/runtime.py` traces every run
   completion; `FallbackProvider.attempts` records provider-failover
   reasons; `server/routes/ws_chat.py` reconciles fragments produced vs
   frames delivered (contract gap "streaming token reconciliation" -
   char-level, honest where no token counts exist) and carries the report
   in the `complete` frame. Pinned by `tests/test_diagnostics_trace.py`.
3. **Tool output schemas + structured tool contract (P3):** RESOLVED 2026-08-29
   (Phase 3, device-independent). `tools/outcome.py` defines the canonical
   status/error/evidence/side-effect vocabularies; `tools/schema.py` exports
   output schemas and validates them; `ToolResult` carries status, evidence,
   execution identity and derived retryability; `serialize_for_model()` renders
   the result deterministically for the model; `tools/registry.py.definitions()`
   is the machine-readable registry and `export_mcp()` the MCP-adapted shape.
   Pinned by `tests/test_tool_output_contract.py` (46 tests) and
   `tests/test_diagnostics_trace.py` (per-execution trace lines). See
   `.aura/decisions/ADR-008.md`.
4. **Android task tools (P5):** calendar/email/SMS/contacts do not exist.
   These are the highest-risk additions (dangerous permissions, security
   review required by the contract) and are device-verification-blocked.
5. **Semantic memory (P2):** RESOLVED 2026-08-29 — human review chose the
   hybrid route and it is implemented (ADR-007): semantic recall beside the
   preserved lexical path, off by default. Original entry follows.

   ~~explicitly absent by documented decision; adding~~
   embeddings contradicts an existing rationale and needs a new decision,
   not silent reimplementation.
6. **Claim→evidence verifier (P4):** RESOLVED 2026-08-30 — implemented as
   `brain/verify/` (ADR-009): ClaimState (VERIFIED/SUPPORTED/INFERRED/
   UNKNOWN/CONTRADICTED), VerifierDecision, deterministic claim
   extraction scoped by a world-object vocabulary, a request-scoped
   EvidenceLedger reusing the Phase 3 Evidence model, hard action-claim
   rules (SUCCESS+postcondition is the only path to a verified action;
   FAILED/DENIED/UNAVAILABLE contradict success language), memory
   attribution, live-registry capability checking, minimal per-sentence
   repair, and a privacy-safe `verifier` trace line. Wired into both the
   `chat` and `chat_stream` paths; repaired text delivered in
   `StreamFinishedEvent` (fragments stream raw — documented, not hidden).
   Pinned by `tests/test_response_verifier.py` (63 tests). Statuses:
   server-side verifier VERIFIED; streamed fragments reach the client
   before verification (documented limitation, not "verified streaming");
   live end-to-end over a real provider NOT VERIFIED (no verified FC
   provider yet).

   **Phase 4.5 hardening (2026-08-30, same date):** the audit found the
   `POST /api/agent/intent` reply was returned unverified; it is now
   verified against the run's own structured tool envelopes via
   `ledger_from_transcript` + `verify_run_reply` (ADR-009 addendum).
   Two rules gaps the integration matrix caught and fixed: SUCCESS with
   a postcondition that came back verified=False is now CONTRADICTED
   (was INFERRED), and a user-world FACTUAL claim graded INFERRED is now
   qualified, never delivered as bare fact. Integration coverage in
   `tests/test_phase45_integration.py` (33 tests: real
   ConversationManager+ToolExecutor, real streaming events, real
   AgentRuntime transcript, the 8-contract-sentence x 9-state matrix,
   memory provenance/conflict, general-knowledge protection, a privacy
   leak test on the verifier trace, and staged performance measurement).

   **Phase 5A note (2026-08-31):** the chat-path ToolResult now converts a
   device postcondition into canonical Phase 3 `POSTCONDITION` Evidence —
   `verified=true` grounds a VERIFIED action, `verified=false`/missing/malformed
   never do, and a bare `{ok:true}` is never verification (ADR-010). This closes
   the audit gap where verified Android actions on the chat path were capped at
   INFERRED. Phase 5A also adds the first read-only installed-app inventory:
   capability `android.app_inventory`, tool `android.list_apps`
   (`AndroidListApps`), bridge validation of the inventory shape (malformed →
   `EXECUTION_FAILED`), and a `PackageSource`/`AppInventory` Android half that
   is JVM-testable off-device. Inventory evidence is OBSERVATION-only (current
   device state, never persisted memory), carries `observed_at` and a content
   hash, and never leaks package names/labels into diagnostics; no cache is
   added. Offline-verified in `tests/test_android_inventory.py` (36 tests) and
   `AppInventoryTest.kt` (26 tests).

   **Live verification (2026-09-05):** all of the above is now confirmed on
   real hardware (`IBCQMB4PTGNZJVTO`, API 33), verify-only, no install and no
   device mutation. `android.list_apps` returned 277 packages in 3.80 s with a
   fresh `observed_at` and `READ_ONLY` side effect; `android.app_inventory`
   reported `AVAILABLE` among 15 Android capabilities over a live heartbeat; a
   sweep of all 10,567 diagnostics lines found 0 package names or labels. The
   evidence chain was proven end to end in both directions: a live
   `{"verified": true}` postcondition reaches `ClaimState.VERIFIED` with
   decision PASS, `{"verified": false}` reaches CONTRADICTED and the false
   claim is repaired away (shown for `android.verify` and for the mutating
   `android.launch_app`), an inventory observation stays `OBSERVATION`, and a
   bare `{"ok": true}` grades INFERRED. The full suite is unchanged at
   3489/2/1/5. One gap remains open: a *verified* postcondition on a
   *mutating* action needs an unlocked screen and is still UNKNOWN.

## 4. Hard blockers and unknowns

- **Physical device `IBCQMB4PTGNZJVTO` disconnected** (2026-08-28): all
  device-side verification (new APK install, 11 real capability executions,
  `NODE_NOT_FOUND` path, screen grounding) remains NOT VERIFIED. Recorded in
  `.Codex/current-task.md`.
- **Device stored URL regression:** companion still points at
  `http://127.0.0.1:8000/`; must be restored via the app's Connection UI
  when the device reconnects.
- **UNKNOWN:** whether any Android 16 AppFunctions surface exists on the
  target device (API 33 — expected no). Treated as UNKNOWN, not assumed.
- **UNKNOWN:** which installed LLM providers actually support native FC in
  practice (six providers wired but unverified against vendors —
  `docs/IMPLEMENTATION_STATUS.md` limitation 9).

## 5. Recommendation for the next phase

Phase 1 items 1–3 (capability registry, router refactor, capability-error
hardening) are fully server-side and device-independent — they can proceed
without the phone, with CI-verifiable tests. Phase 0 items 2–4 (diagnostics,
streaming/provider traces) are likewise device-independent and are
prerequisites for everything else. Android task tools (P5) must wait for the
device and security review.

**Human review is required on:**
- the gap ranking in section 3 and the proposed phase order in section 5;
- whether to add semantic memory (contradicts a documented decision);
- whether to pursue AppFunctions/SMS/email tools at all given the API 33
  target (security review mandated by the contract for dangerous permissions).


