# AURA project state

Server-side grounding, the Android companion transport, and the device-side
dispatcher are implemented and committed (a97bc69 on `feature/aura-identity`).
The device-side evidence in this file comes from the 2026-08-27 session; as of
2026-08-28 the physical device is DISCONNECTED and none of it can currently be
reproduced. Production still runs older code than this branch: its
`/api/capabilities` reports Android capabilities as
`authorization=granted, health=unavailable`, and it does not serve
`/api/agent/intent`.

- Android companion package: `com.aura.companion`
- Target ADB device: `IBCQMB4PTGNZJVTO` (OPPO CPH2251, API 33) - DISCONNECTED as of 2026-08-28; `adb devices` is empty.
- As of 2026-08-27 (not reproducible today): `accessibility_enabled=1` and
  both `AuraAccessibilityService` and `ScreenObservationService` were enabled
  and bound.
- As of 2026-08-27 (not reproducible today): an authenticated local server
  received the companion poll heartbeat and reported 14 Android capabilities as
  `AVAILABLE`, with granted accessibility permission and healthy companion
  dependency. With no heartbeat today all 14 correctly report `UNAVAILABLE`
  with the missing-heartbeat reason.
- The working tree contains extensive pre-existing/uncommitted capability-grounding changes; preserve unrelated user changes.
- Persistent state files are established under `.Codex/`.
- Test status 2026-08-28: targeted capability/device/agent suites `563 passed,
  1 failed`; full suite `3246 passed, 2 skipped, 1 deselected, 6 failed`.
  Baseline was 3228/5, so +18 passing and zero regressions. Five failures are
  the pre-existing `server/routes/settings.py` restart path; the sixth is the
  environmental held-modifier input test, which passes in isolation. Android
  unit tests: 388 tests, 0 failures.
- Gradle builds and unit tests succeed with the bundled JDK 21 and normalized
  `TEMP`/`TMP` variables. The default desktop shell/JDK 17 environment
  reproduces the loopback `Invalid argument` failure.
- CORRECTION (2026-08-28): the earlier claim that the phone URL was restored to
  `https://aura-xwm4.onrender.com/` before handoff is not supported by
  evidence. On 2026-08-28 `adb reverse --list` showed `UsbFfs tcp:8000 tcp:8000`
  still mapped, companion logcat reported `poll unavailable: Offline`, and a
  locally started server received a real `POST /api/device/poll` - so the phone
  was still configured for `http://127.0.0.1:8000/`. The reverse mapping is now
  gone (it died with the USB disconnect) and the temporary local server is
  stopped, but the stored URL can only be corrected through the app's
  Connection UI once the device reconnects. Treat this as an open item.
- The companion dispatcher includes a just-in-time runtime capability gate;
  Android primitives are reached only after that gate and catalog validation.
- Phase 1 of the AURA 2.0 contract is complete on this branch (2026-08-28,
  uncommitted): provider capability registry (`brain/providers/
  capabilities.py`), capability-first FC routing with
  `CapabilityUnavailableError` (HTTP 501), per-request JSONL diagnostics
  (`core/trace.py` -> `logs/diagnostics.jsonl`), provider attempt records
  on `FallbackProvider`, and stream reconciliation in the WebSocket
  complete frame. Full suite 3267 passed / 2 skipped / 1 deselected /
  5 pre-existing settings-restart failures - +21 passing over baseline,
  zero regressions. Per decision 7, every provider's function-calling
  status is UNKNOWN until a real request demonstrates it; none is VERIFIED
  yet.

## Memory subsystem — 2026-08-29

Hybrid semantic recall exists beside the lexical path and is OFF by default
(`memory.semantic.enabled: false`), which is a complete configuration: with
no embedding provider, memory behaves exactly as it did before Phase 2.

- `memory/embeddings.py` - `EmbeddingProvider` protocol; `hashing` (LOCAL,
  stdlib only, the default), `ollama` (LOCAL), `remote` (REMOTE, inert
  until `allow_remote` is explicitly true).
- `memory/semantic.py` - `SemanticIndexer`, `SemanticRetriever`,
  `HybridRetriever` (Reciprocal Rank Fusion, `memory.semantic.weight`).
- `memory/models.py` `SemanticVector` - vectors in the SAME SQLite
  database. No vector store, no new dependency.
- Degradation is the design, not a fallback: any embedding failure, stale
  index or model mismatch leaves lexical retrieval serving normally, and
  retrieval never raises into a turn.
- Provenance, scope isolation, conflict handling and deletion invariants
  are pinned by `tests/test_semantic_memory.py` (43 tests).
- `scripts/benchmark_semantic.py` is the measurement, repeatable and
  fixture-based. Its sweep is what set the hashing provider's 0.24
  similarity floor.

Decision record: `.aura/decisions/ADR-007.md`.

NOT VERIFIED: no model-backed embedding provider has been exercised
against a real server; semantic recall has never run in a live
conversation.
## Tool output contract — 2026-08-29 (Phase 3)

Phase 3 of the AURA 2.0 contract is complete on this branch (uncommitted),
device-independent. The tool system is machine-readable end to end.

- `tools/outcome.py` (NEW) - canonical vocabularies: `ToolStatus` (SUCCESS /
  FAILED / PARTIAL / DENIED / UNAVAILABLE / INVALID_ARGUMENTS / TIMEOUT /
  CANCELLED / UNKNOWN), `ToolError` + `ToolErrorCategory` (category derived
  from code; CAPABILITY distinct from PROVIDER), `Evidence` (kind/source/
  tri-state verified/timestamp/reference), `SideEffect` (READ_ONLY /
  IDEMPOTENT / NON_IDEMPOTENT / UNKNOWN), `Retryability`, derived
  `retryability_of(status, side_effect)`.
- `tools/base.py` - `ToolResult` gains status/error_code/evidence/
  execution_id/started_at/completed_at/side_effect; `ok` is reconciled FROM
  `status` so UNKNOWN can never be truthy; `serialize_for_model()` renders
  fixed STATUS/TOOL/EVIDENCE/RETRY/OUTCOME/ERROR lines.
- `tools/schema.py` - `output_schema()`, `validate_output()` (microsecond
  subset validator; malformed output -> UNKNOWN, never SUCCESS),
  `tool_definition()`, `mcp_export()` (MCP-adapted `tools/list` shape).
- `tools/executor.py` - execution identity stamping, output validation,
  one `tool_execution` diagnostics line per execution (no arguments, no
  content), trace failure never breaks execution.
- `tools/registry.py` - `definitions()` = machine-readable registry
  (discovery, schema inspection, side-effect/risk/version filtering);
  availability stays a live fact joined at runtime, never baked into a
  static file.
- `brain/conversation.py` - `_render_result` routes ToolResult through
  `serialize_for_model`.
- Regression pinned: the original "no cloud provider supports function
  calling" failure is a structured CAPABILITY result (never generic
  PROVIDER_FAILURE, never retried against an incapable provider, never
  converted into a claim of success).
- Tests: `tests/test_tool_output_contract.py` 46 passed. Full Python suite
  `3357 passed, 2 skipped, 1 deselected, 5 failed` - the 5 are the exact
  pre-existing settings-restart set; baseline 3311 / 5, +46, zero
  regressions. Android status unchanged (device DISCONNECTED).
- Decision record: `.aura/decisions/ADR-008.md` + `.json`; Phase 3 table of
  `AURA_ARCHITECTURE_AUDIT.md` rewritten, gap 3 RESOLVED.

Capability status (AURA 2.0 vocabulary): tool-contract runtime, status and
error taxonomies, evidence model, retry semantics, registry, MCP-adapted
export - VERIFIED by the test suite. Device-side Android execution and the
companion still BLOCKED (phone disconnected). Semantic recall and
model-backed embeddings remain IMPLEMENTED BUT NOT VERIFIED (no live server).

## Phase 4 � response verifier (2026-08-30)

Phase 4 of the AURA 2.0 contract is complete on this branch (uncommitted).
rain/verify/ is the deterministic claim->evidence boundary over
free-form chat: ClaimState (VERIFIED/SUPPORTED/INFERRED/UNKNOWN/
CONTRADICTED), VerifierDecision, world-object-scoped claim extraction,
a request-scoped EvidenceLedger reusing the Phase 3 Evidence model, hard
action-claim rules, memory attribution, live-registry capability checks
and minimal repair. ConversationManager verifies the final text on both
chat and chat_stream; the launcher injects it from response.verify
config. Pinned by tests/test_response_verifier.py (63 tests). Full suite
3420 passed / 2 skipped / 1 deselected / 5 pre-existing failures (+63
over baseline, zero regressions). Decision record:
.aura/decisions/ADR-009.md. Honest limits: streamed fragments reach the
UI before verification; live provider round trip NOT VERIFIED; device
BLOCKED.

## Phase 4.5 - verification integration hardening (2026-08-30)

Every final-response path audited. chat/chat_stream were already
verified; POST /api/agent/intent was not and now is: the run's own
structured tool envelopes become ledger evidence via
brain/verify/ledger.py::ledger_from_transcript, the reply is verified
by brain/verify/verify.py::verify_run_reply, and agent.py returns the
repaired reply plus a metadata-only verifier summary (config-gated by
response.verify.enabled). Two deterministic rule gaps fixed: SUCCESS
with a failed postcondition is CONTRADICTED (was INFERRED), and
user-world FACTUAL INFERRED claims are qualified, never bare fact.
33 integration tests in tests/test_phase45_integration.py. Full suite
3453 passed / 5 pre-existing failures (+33 over baseline, zero
regressions). Deferred-step final surface BLOCKED on the disconnected
device. Honest limits unchanged: streamed fragments reach the client
before verification; live provider round trip NOT VERIFIED.

## Phase 5A - offline app-inventory foundation + Evidence seam (2026-08-31)

Offline/CI-verifiable only; no live-device work, no APK/install, no
companion, no URL/token restore. ADR-010 records the contract.

- Capability `android.app_inventory` registered canonically
  (core/capabilities/factory.py) with required_dependencies=["android.companion"]
  and the same accessibility health/permission gates as the other 14 Android
  capabilities. Existence != availability; registration alone never claims
  availability.
- Tool `android.list_apps` (AndroidListApps): ToolRisk.SAFE,
  SideEffect.READ_ONLY, structured output, observed_at required, device_id when
  known. Bridge validation (normalise_device_report + _valid_inventory) maps
  malformed inventory to EXECUTION_FAILED; UNKNOWN stays UNKNOWN.
- Android half AppInventory.kt (PackageSource / PlatformPackageSource / pure
  AppInventory.collect): deterministic, JVM-testable; launchability from the
  MAIN/LAUNCHER query only; QUERY_ALL_PACKAGES not added. No cache; every call
  re-enumerates with a fresh observed_at.
- Evidence seam: device postcondition with an explicit boolean becomes canonical
  POSTCONDITION Evidence (verified=true -> VERIFIED action; false/missing/
  malformed -> never VERIFIED; bare {ok:true} is never verification);
  app_inventory observation -> OBSERVATION Evidence (current device state,
  never memory). Closes the chat-path gap (verified Android action could not
  reach VERIFIED before).
- Privacy: package names/labels never enter diagnostics; observation payload +
  content hash carry counts only; inventory never logged in full; not persisted
  as memory.
- Tests: tests/test_android_inventory.py 36; AppInventoryTest.kt 26. Two real
  JVM defects fixed (nullable app() helper; manifest-comment false positive on
  the QUERY_ALL_PACKAGES assertion).
- Verification: Python full suite 3489 passed / 2 skipped / 1 deselected /
  5 failed (the 5 are exactly the pre-existing settings-restart set; baseline
  3453 -> +36, zero regressions). Android JVM 414 tests / 2 failures - only the
  pre-existing SettingsContractTest fixture-drift pair (working-tree
  providers.json / provider_health.json updated to a live configured Gemini by
  earlier uncommitted baseline work; unrelated to Phase 5A).
- Capability states: offline pipeline/verifier inventory VERIFIED; live
  PackageManager enumeration IMPLEMENTED BUT NOT VERIFIED (device disconnected);
  real-enumeration performance UNKNOWN (no handset measurement).

### Live verification attempt 2026-09-01: STOPPED — device disconnected

Phase 5A.8 live verification reached Step 5 (companion connection) before the
device dropped off adb. Completed before the stop (all read-only): repo state
intact; device API 33, companion v0.1.0 installed, accessibility active; fresh
Phase 5A debug APK built (not installed — the installed v0.1.0 is provably the
OLD build); documented server started with auth verified; device→host transport
proven through adb reverse at TCP level. Blocked on: physical device reconnect.
Live heartbeat / live inventory / live privacy diagnostics / live
postcondition→Evidence / chat-path grounding all remain UNVERIFIED — no live
claim may be made from this attempt.
