# Progress

## 2026-09-05 — Phase 5A.8 live verification: COMPLETE on real hardware

Device `IBCQMB4PTGNZJVTO` reconnected and every step of the Phase 5A.8 brief
was executed live. Verify-only: no architecture changed, no gate weakened, no
install, no device mutation beyond one already-captured `android.launch_app`
attempt, screen never unlocked.

- Step 1 (repo): nothing reset, checked out, cleaned or stashed. All
  previously uncommitted Phase 1-5A work is now committed as `9466f89`
  (167 files, +17472 / -3147) and pushed to `origin/feature/aura-identity`
  (`a97bc69..9466f89`). Not pushed to main. `.gitignore` gained six rules for
  things that were never source: `.codegraph/` (438 MB index), `test_tmp/`,
  `/server_out.log`, `/server_err.log`, `_dbg_*.py`, `android/screen*.png`.
- Step 2 (device, read-only): API 33, `com.aura.companion` installed,
  accessibility enabled and both services bound.
- Step 3 (APK freshness) — **CORRECTS THE 2026-09-01 RECORD**. The installed
  APK IS the Phase 5A build; the earlier entry saying it "remains the OLD
  build (pre-Phase 5A)" is wrong. Evidence: `dumpsys` reports
  `lastUpdateTime=2026-09-01 08:31:25`, which is AFTER the 08:25 state-file
  write that made the claim; the local `app-debug.apk` (19,901,173 bytes,
  built 2026-09-01 07:41) and the pulled installed `base.apk` share sha256
  `927891325cecd1a9367c182d3ee548d58d5f18faa6765d926ec49c9446218952`; and
  `find app/src -newer app-debug.apk` is empty, so no source postdates the
  build. The 2026-09-01 session did reach Step 4 even though its own notes
  say it did not.
- Step 4 (install): correctly SKIPPED. Nothing was installed, and
  `lastUpdateTime` is unchanged after the session.
- Step 5 (companion connection): verified WITHOUT reading or printing the
  token. Repeated `POST /api/device/poll -> 200` proves both that the stored
  URL resolves to the running server and that the companion's stored bearer
  token matches the server's. The stored URL is still
  `http://127.0.0.1:8000/` (reached through `adb reverse`), NOT
  `https://aura-xwm4.onrender.com/`. Connection prefs are
  EncryptedSharedPreferences (keys AND values), so the URL cannot be read or
  written from adb - restoring it requires the in-app Connection UI, which
  pre-fills the token from state. STILL OPEN.
- Step 6 (server): started via the documented entrypoint on `config.yaml`;
  live heartbeat received. `/api/capabilities` reported all **15** Android
  capabilities `AVAILABLE`, including `android.app_inventory` (bound tool
  `android.list_apps`), `authorization=granted`, `health=healthy`, nothing
  degraded or stale.
- Step 7 (live inventory): `android.list_apps` SUCCESS in **3.80 s**.
  Aggregates only, per the privacy rule: **277 packages**, `count=277`,
  `observed_at=1788605555.79` (fresh - 23.9 s old at read),
  `device_id="android-c9874ac1-fb2"`, `source="android.package_manager"`.
  Record shape `(enabled, label, launchable, package, version_name)`;
  launchable 88, enabled 269, 0 duplicates; `postcondition=null`;
  `evidence=VERIFIED`, `side_effect=READ_ONLY`. Live `PackageManager`
  enumeration moves from IMPLEMENTED BUT NOT VERIFIED to **VERIFIED**, and
  real-enumeration performance from UNKNOWN to **3.80 s / 277 packages**.
- Step 8 (privacy): NO regression. The observation payload carries only
  `{count, launchable_count, source}` plus a SHA-256 content hash; the
  `tool_execution` diagnostics line carries ids/status/duration/evidence/
  capability only. A sweep of all **10,567** lines of
  `logs/diagnostics.jsonl` found 0 inventory package names. One naive
  substring hit was a false positive - the package literally named `android`
  matching inside the string `"android.package_manager"` - cleared by a
  word-boundary re-check (`real leaks: []`). The single "YouTube" label hit
  traces to pre-existing 2026-08-28/29 test goal strings, not to inventory.
- Steps 9-10 (postcondition -> Evidence -> ClaimState, both directions):
  **5/5 PASS** live, driven through the production seam
  (`tool_result_from_report`) and the real
  `ConversationManager._record_tool_evidence` / `_verify_final`, never a
  mirror of them:
  1. live `{"verified": true}` -> `EvidenceKind.POSTCONDITION` verified=True
     -> `evidence_state VERIFIED` -> **`ClaimState.VERIFIED`**, decision
     PASS, reply returned unchanged. This is the link that had never been
     live-proven.
  2. live `{"verified": false}` -> **CONTRADICTED**, decision REPAIR, the
     false claim removed.
  3. live MUTATING action: `android.launch_app` returned
     `{"verified": false, "note": "executed; state change not observed"}`
     because the screen was off and locked (`mWakefulness=Dozing`,
     `mScreenLocked=true`) -> **CONTRADICTED**, repaired to "I can't verify
     that the app actually launched." A device declining to observe a state
     change correctly cannot produce a claim.
  4. live inventory observation -> `OBSERVATION` only, never
     `POSTCONDITION`.
  5. bare `{"ok": true}` from the same tool -> no Evidence at all ->
     `INFERRED`, never VERIFIED.
- Step 11 (regression): full suite `3489 passed, 2 skipped, 1 deselected,
  5 failed` in 211.61 s - **identical to the baseline**, and the 5 are
  exactly the pre-existing settings-restart set (`test_companion`
  restart-reply, `test_settings_api` x2, `test_settings_contract` x2, all
  from `coroutine 'update_settings.<locals>._do_restart' was never
  awaited`). Zero regressions, zero new failures, and none of them touched.
  Targeted: `tests/test_android_inventory.py` 36 passed;
  `test_phase45_integration` + `test_android_provider` +
  `test_tool_output_contract` + `test_response_verifier` 156 passed.

A harness fault was found and fixed in the HARNESS, not the product: the
first revision asserted "I checked the foreground app on your phone.",
whose tokens do not intersect the ledger entry's tool name
(`android verify`), outcome text, or capability id, so
`EvidenceLedger.matching_tool` bound nothing and the claim was correctly
graded UNKNOWN. Token overlap is deliberate and documented in
`brain/verify/ledger.py`. Nothing under `brain/verify/` was changed.

Two observations recorded and deliberately NOT fixed, because this was a
verify-only task: `ToolResult.capability` is the literal string `"unknown"`
for bridge reports, so claim binding currently rests on tool name and
outcome text alone; and the repair phrasing for case 2 reads "I can't verify
that the android was actually confirmed", because the object noun is chosen
mechanically. Neither affects correctness - both are candidates for a later
phase.

Teardown: server stopped, `adb reverse tcp:8000 tcp:8000` removed. Device
left as found - no install, no data cleared, no permission granted, no
setting changed, screen never unlocked; `versionName=0.1.0` and
`lastUpdateTime=2026-09-01 08:31:25` unchanged, `accessibility_enabled=1`.
Live capture artifacts kept at `test_tmp/live5a/` (gitignored) as the audit
trail behind every number above.

## 2026-09-01 — Phase 5A.8 live verification attempt: STOPPED (device disconnected)

- Read-only pre-flight completed: repo state intact (feature/aura-identity,
  Phase 1-5A work present, nothing discarded); device was connected (API 33,
  companion v0.1.0, accessibility active); Phase 5A debug APK built fresh
  (documented Gradle wrapper + JDK 17) but NOT installed (Step 4 unreached);
  documented server entrypoint started; /api/device/poll auth verified
  host-side (401 anon / 200 auth); reverse tunnel transport device->host
  PROVEN at TCP level.
- STOP CONDITION HIT: device disconnected mid-Step-5; adb wait-for-device
  blocks indefinitely. Physical reconnect required. NO live inventory call,
  NO install, NO privacy-diagnostics check, NO postcondition/chat live proof
  was performed. Nothing live may be claimed VERIFIED.
- Installed v0.1.0 APK remains the OLD build (pre-Phase 5A) - recorded, not
  assumed.
  **WRONG - corrected 2026-09-05.** The installed APK is the Phase 5A build:
  `lastUpdateTime=2026-09-01 08:31:25` postdates this note, and the installed
  `base.apk` hashes identically to the locally built `app-debug.apk`. This
  session did reach Step 4. See the 2026-09-05 entry above.
- Honest statuses recorded in .Codex/current-task.md; offline baseline
  unchanged (3489 passed / 2 skipped / 1 deselected / 5 pre-existing
  settings-restart failures).
## 2026-08-31 — Phase 5A: offline app-inventory foundation + Evidence seam

- Pre-flight re-audit (no code before it): traced the canonical registration
  location (core/capabilities/factory.py), Android capability/tool naming,
  DeviceToolDispatcher dispatch table, DeviceToolCatalog validation,
  device-report format, normalise_device_report, ToolResult construction,
  Evidence construction (POSTCONDITION/OBSERVATION), postcondition
  representation, diagnostics/privacy boundary, and existing test conventions.
  Confirmed the repo already carried the Phase 1-4.5 uncommitted baseline
  (3453/2/1/5) - preserved unchanged.
- Implemented (reusing existing primitives only, no new model/dependency/cache):
  - core/capabilities/factory.py: capability android.app_inventory
    (android.list_apps), canonical registration with
    required_dependencies=["android.companion"] and the same accessibility
    health/permission gates as the other 14 Android capabilities.
  - tools/providers/android_provider.py: AndroidListApps (ToolRisk.SAFE,
    SideEffect.READ_ONLY); _evidence_from_report / tool_result_from_report -
    the chat-path Evidence seam (device postcondition to canonical
    POSTCONDITION Evidence; app_inventory observation to OBSERVATION Evidence,
    never memory).
  - tools/providers/android_bridge.py: LoopbackDeviceBridge._do_list_apps +
    DEFAULT_APPS; normalise_device_report inventory validation via
    _valid_inventory (malformed inventory -> EXECUTION_FAILED, never success,
    never coerced).
  - android/.../accessibility/AppInventory.kt (new): PackageSource /
    PlatformPackageSource / pure AppInventory.collect; launchability from the
    MAIN/LAUNCHER query; QUERY_ALL_PACKAGES not added; observation payload +
    SHA-256 content hash carry counts only (no package names/labels).
  - android/.../accessibility/DeviceToolDispatcher.kt: android.list_apps
    dispatch + ACCESSIBILITY_CAPABILITIES (adds android.app_inventory).
- Tests added: `tests/test_android_inventory.py` (36: schema/registration,
  loopback behaviour, bridge validation, evidence/privacy/freshness, timeout/
  denied/unavailable capability gates, postcondition Evidence matrix, chat-path
  recorder seam) and
  android/app/src/test/java/com/aura/companion/accessibility/AppInventoryTest.kt (26).
- Two real defects found by the JVM compile/test run and fixed:
  1. AppInventoryTest.kt compile error - the app() helper declared name: String
     non-null but a test passed null; changed to String? (matches
     RawPackage.packageName: String?).
  2. the_inventory_needs_no_new_permission_beyond_the_launcher_query failed
     because it searched text.substringBefore("<queries>") for
     QUERY_ALL_PACKAGES, which the manifest legitimately mentions in a COMMENT
     (it documents why the MAIN/LAUNCHER query is used instead). It is NOT
     declared as a permission. Changed the assertion to require no
     <uses-permission ... QUERY_ALL_PACKAGES> element.
- Verification:
  - Python: tests/test_android_inventory.py 36 passed; full suite
    3489 passed / 2 skipped / 1 deselected / 5 failed - the 5 are exactly the
    pre-existing settings-restart set; baseline 3453 +36, zero regressions.
  - Android JVM: full run 414 tests / 2 failures - only the pre-existing
    SettingsContractTest fixture-drift pair (working-tree providers.json /
    provider_health.json updated to a live configured Gemini by earlier
    uncommitted baseline work; NOT Phase 5A). AppInventoryTest 26/26.
  - git diff --check clean for Phase 5A files.
- Docs: ADR-010 created; AURA_ARCHITECTURE_AUDIT.md Phase 5 inventory row
  updated + Phase 5A note added; state files updated.
- Capability states: offline verifier/pipeline inventory VERIFIED; live
  PackageManager enumeration IMPLEMENTED BUT NOT VERIFIED (device
  disconnected); real-enumeration performance UNKNOWN (no handset).
## 2026-08-30 — Phase 4.5: verification integration & hardening

- Pre-flight audit (no code written before it): traced every
  final-response path from user input to delivered bytes.
  - `ConversationManager.chat` and `chat_stream` — verified (Phase 4):
    ledger built per turn (`_prepare`), tool evidence captured
    (`_record_tool_evidence`), memory evidence captured (knowledge lines
    with honest unknown provenance), `_verify_final` on both paths.
  - `ws_chat.py` complete frame — carries the authoritative repaired
    `text` plus `verifier` metadata (lines ~299-311). Fragments go out
    raw first — pinned as the documented boundary, NOT verified
    streaming.
  - **GAP FOUND:** `POST /api/agent/intent` (`server/routes/agent.py`)
    returned the final assistant message as `reply` with no
    verification. The run transcript carries structured envelopes
    (ok/error.code/postcondition) — unused by the verifier.
  - `/api/agent/step` returns directives/snapshots, not final prose —
    no server-side text to verify; deferred final surface BLOCKED on
    the disconnected device.
- Implementation (smallest delta, existing architecture preserved):
  - `brain/verify/ledger.py`: `ledger_from_transcript` + private
    `_status_from_error` (error category → ToolStatus; unparseable
    envelopes skipped, never guessed).
  - `brain/verify/verify.py`: `verify_run_reply` helper.
  - `server/routes/agent.py`: `_verify_run_reply` in `agent_intent`,
    config-gated, failure-tolerant, additive `verifier` field.
- Two rule gaps caught by the integration matrix and fixed at root:
  - `rules.py::_grade_success`: SUCCESS + evidence_state CONTRADICTED
    (postcondition verified=False) is now CONTRADICTED, was INFERRED.
  - `repair.py::repair_claims`: user-world FACTUAL + INFERRED is now
    qualified ("As far as I can tell, ..."), was delivered as bare fact.
- Tests: `tests/test_phase45_integration.py` — 33 passed. Full suite
  3453 passed / 2 skipped / 1 deselected / 5 failed (exact pre-existing
  settings-restart set). Baseline 3420 / 5: +33, zero regressions.
- Docs: `AURA_ARCHITECTURE_AUDIT.md` gap 6 Phase 4.5 note;
  ADR-009 addendum; state files updated.
- FC regression (contract §17): pinned by existing
  `tests/test_tool_output_contract.py::test_capability_unavailable_is_not_a_generic_provider_failure`
  and `test_capability_unavailable_does_not_mark_side_effects_retryable`
  — re-run green this session; no new code needed.

## 2026-08-30 — Phase 4 complete (resumption session)

# Progress

## 2026-08-30 — Phase 4 complete (resumption session)

- Audited the working tree first: `brain/verify/` (status, claims,
  ledger, rules, repair, verify) and `tests/test_response_verifier.py`
  already existed from the prior session, integrated into
  `brain/conversation.py` (per-turn EvidenceLedger, tool/memory evidence
  capture, `_verify_final` on chat and chat_stream) and
  `launcher/services.py` (`response.verify` config). Documentation was
  NOT done and 5 verifier tests were failing.
- Resumption delta (all in `brain/verify/claims.py`):
  1. `_SENTENCE_END` look-behind `(?<![A-Za-z0-9])` prevented a period
     from ever matching after a normal word ("out." == "Dr."), so whole
     replies collapsed into one claim — replaced with a plain boundary
     regex plus `_is_false_end` (abbreviations, single-letter initials).
  2. `_ACTION_FIRST_PERSON` required the verb immediately after the
     pronoun; now tolerates up to three intervening words so "I
     definitely already sent the email" classifies as ACTION.
  3. Android fixture `settings.json` regenerated via
     `AURA_WRITE_ANDROID_FIXTURES=1` for the new
     `effective.response.verify` keys (test_settings_fixture now passes).
- Tests: `tests/test_response_verifier.py` 63 passed (was 58/5 failed);
  full Python suite 3420 passed / 2 skipped / 1 deselected / 5 failed —
  the 5 are exactly the pre-existing settings-restart set. Baseline
  3357 / 5: +63 passing, zero regressions.
- Documentation: `AURA_ARCHITECTURE_AUDIT.md` gap 6 marked RESOLVED;
  `.aura/decisions/ADR-009.md` + `.json` created.
- Statuses (AURA 2.0 vocabulary): verifier modules, rules, repair,
  ledger, diagnostics line — VERIFIED by tests. Streaming: fragments
  reach the client raw before verification (documented boundary;
  StreamFinishedEvent carries the repaired text) — this is NOT claimed
  as verified streaming. Live end-to-end over a real FC provider — NOT
  VERIFIED (no provider verified per decision 7). Device — BLOCKED.

## 2026-08-27

# Progress

## 2026-08-27

- Read repository instructions and audited the working tree.
- Confirmed a physical ADB device: `IBCQMB4PTGNZJVTO`, `device`, model `CPH2251`, Android 13/API 33.
- Confirmed `com.aura.companion` is installed (`versionName 0.1.0`) and both AURA accessibility services are enabled/bound.
- Found Android provider tools inherit `capability = "dummy"`; only coarse Android capabilities are registered.
- Found `/api/device/invoke` and `scripts/aura_android.py` call Android tools directly instead of `ToolExecutor`.
- Baseline targeted Python tests passed: 81 passed.
- The companion currently points at `http://192.168.1.252:8000/`; this host is `192.168.1.35`, and no server is listening now.

## Live grounding milestone — 2026-08-27

- No companion heartbeat now resolves Android capabilities to `UNAVAILABLE`,
  not `BLOCKED_PERMISSION`.
- `ToolExecutor` preserves structured capability-gate failure payloads, and
  the HTTP Android harness queries `/api/capabilities` before its local gate.
- The dead legacy `runAgentSteps` implementation in the companion is
  disabled; `AgentRunDriver` and `AccessibilityToolDispatcher` are active.
- ADB still verifies the physical device and installed package, but current
  secure settings show neither AURA accessibility service enabled. Therefore
  no heartbeat or real Android execution is currently possible; no permission
  was toggled automatically.
- Live API inventory reports all Android capabilities `UNAVAILABLE` with
  reason `no Android companion poll heartbeat has been received`.
- Targeted Python regression suites pass: 107 passed.
- Extended capability/discovery/input/plugin regression suites pass: 313
  passed. Natural-language ranking selected the intended Android capability
  for screen inspection, UI search, button press, foreground app, home, and
  text input; all were correctly filtered out as non-executable while the
  heartbeat was absent.
- Full Python suite: 3228 passed, 2 skipped, 1 deselected, 5 unrelated
  settings-restart failures. The failures are in existing
  `server/routes/settings.py` background restart handling
  (`asyncio.create_task` called from a synchronous Starlette worker), not in
  the capability changes.
- Gradle Android build is blocked before compilation by the local JDK /
  Gradle loopback-daemon error (`java.net.SocketException: Invalid argument:
  connect`).
- Final current-code live API check: 14 Android capabilities, all
  `UNAVAILABLE`, same heartbeat reason; `android.get_foreground_app` through
  `/api/device/invoke` returned `CAPABILITY_UNAVAILABLE` with
  `execution=not_attempted`. The audit server was stopped afterward.
- Added a just-in-time capability gate inside the companion's
  `AccessibilityToolDispatcher`, so a permission/health change after the
  server advertised a directive still blocks before Android primitives run.

## Physical execution and build milestone — 2026-08-27

- Re-verified ADB device `IBCQMB4PTGNZJVTO` (OPPO CPH2251, Android 13/API
  33), package `com.aura.companion`, package process, and both enabled/bound
  AURA accessibility services.
- Built the debug APK with the repository Gradle wrapper using bundled JDK 21
  and normalized Windows TEMP/TMP; installed it with `adb install -r` and
  verified package version `0.1.0`, service declarations, and accessibility
  state.
- An authenticated local server received real companion polls. Live inventory:
  14 canonical Android capabilities, all `AVAILABLE`, `granted`, and
  `healthy`.
- Real `/api/device/invoke` results through the physical companion included
  foreground app `com.aura.companion`, UI tree, visible-node search, JPEG
  screenshot (`821x1825`, 105393 bytes), verified text entry into the harmless
  Aura draft field, verified node-scoped backspace clearing it, verified Home
  to `com.android.launcher`, verified launch back to Aura, `wait_for` met, and
  `verify package_is=com.aura.companion` met.
- Real failure grounding: a missing visible node returned `ok=false`,
  `NODE_NOT_FOUND`, and a fresh accessibility-tree observation; an unknown
  device tool returned `TOOL_NOT_FOUND` without device execution.
- Fixed result delivery for device failures without observations by allowing
  `observation_id=null` at the HTTP boundary; fixed `android.press_key` to
  accept a node-scoped backspace/clear action. The physical press-key test
  passed after reinstall.
- Fixed stale capability explanations: transitioning to `AVAILABLE` now
  clears an old blocking reason from registry metadata.
- Android unit tests pass (`:app:testDebugUnitTest`); targeted Python suites
  pass (`313 passed`). Test doubles were updated with explicit capability
  mappings required by the strict no-unmapped-tool rule; their focused suite
  now passes (`232 passed`).

- Re-ran the combined capability, Android provider, bridge, runtime, route,
  security, input, plugin, and tool-framework suites: `545 passed`.
- Re-ran the full Python suite: `3228 passed, 2 skipped, 1 deselected,
  5 failed`. All five failures are the pre-existing settings restart path in
  `server/routes/settings.py`, where a Starlette sync background worker calls
  `asyncio.create_task` without a running event loop; no Android test failed.
- Re-ran Android `:app:testDebugUnitTest`: `BUILD SUCCESSFUL` with only the
  existing SDK XML compatibility warning and AccessibilityNodeInfo deprecation
  warnings.

## Session 2026-08-28 - grounding audit, strict selection, commit/push

- Physical device `IBCQMB4PTGNZJVTO` is DISCONNECTED for this whole session
  (`adb devices` empty; Windows PnP shows the serial with status Unknown).
  Every real-device verification is therefore NOT VERIFIED in this session and
  the prior session's device evidence is the only device evidence that exists.
- Android APK rebuilt fresh with `--rerun-tasks` (so no UP-TO-DATE masking):
  `BUILD SUCCESSFUL in 3m 5s`, 28 suites / 388 tests / 0 failures / 0 errors.
  `app-debug.apk` 19,504,065 bytes,
  sha256 11f48b5675fe8c5b03f3f812f264bd23e114207997b7b62d032a386054fad730.
  NOT installed on any device - installation requires the phone.
- Bypass audit across `core/capabilities/`, `tools/`, `server/`, `agent/` and
  the Kotlin accessibility package found no bypass of the capability path:
  - no raw adb/subprocess in the Android path (subprocess hits are the
    unrelated desktop builtins apps/commands/system);
  - `AndroidProvider` is constructed in exactly two places -
    `server/routes/agent.py` (production, `GatewayDeviceBridge`) and
    `scripts/aura_android.py` (CLI harness); nothing invokes a tool body
    outside `ToolExecutor`;
  - hardcoded `ok: True` / `state: AVAILABLE` exists only inside
    `LoopbackDeviceBridge` and `DeclaredOnlyBridge` (tests/CLI only);
  - `GatewayDeviceBridge.status()` delegates to `gateway.device_status()`,
    which derives state from the real heartbeat;
  - zero server-side fabricated observations or postconditions;
  - zero legacy/compat fallback shims in the capability path.
- `DeviceGateway.__init__` defaults `require_heartbeat=False`, but the process
  singleton `get_device_gateway()` always constructs `require_heartbeat=True`,
  and `configure_device_gateway` is called only from tests. The permissive
  no-heartbeat AVAILABLE branch is therefore unreachable in production.
- The Android side's `AVAILABLE` capability heartbeat is emitted only from
  `AuraAccessibilityService.onServiceConnected`, so it is conditioned on a
  really-bound service; `android.screen_capture` is computed from live
  `isSupported` / `screenshotToolAllowed()` rather than hardcoded.
- `prompts/system.md` satisfies the static grounding principles (no action
  claimed without a ToolResult, registry authoritative, unavailable
  capabilities must state limitation and cause). Android-specific
  observation-before-answer and postcondition verification are enforced in
  `agent/runtime.py` instead of prompt text, which is the stronger location; no
  prompt change was made, deliberately, to avoid a static list going stale.
- Fixed: `server/routes/agent.py` minted session ids with
  `uuid.uuid4().hex[:12]`, duplicating `core.ids` and violating the
  `^[a-z]+_[0-9a-f]{16}$` contract. Now uses `core.ids.new_session_id()`.
- Targeted suite: `563 passed, 1 failed`; full suite: `3246 passed, 2 skipped,
  1 deselected, 6 failed`. Baseline was 3228 passed / 5 failed, so this is
  +18 passing and zero regressions. The 5 settings-restart failures reproduce
  identically in isolation. The 6th,
  `test_input.py::...test_no_modifier_is_reported_held_when_none_is`, passes in
  isolation and its own comment documents that it fails when the owner is
  physically holding a key - environmental, not a regression.
- Committed and pushed a97bc69 to `feature/aura-identity` with exactly five
  files: `core/capabilities/discovery.py`, `agent/runtime.py`,
  `server/routes/agent.py`, `scripts/aura_intent.py`,
  `tests/test_autonomous_skills.py`. Remote verified. `main` untouched at
  8620574. All unrelated user changes (4 Android chat UI files,
  `data/memory.db-*`, `.claude/current-task.md`, the new root markdown docs,
  screenshots, logs) were left unstaged and unmodified.
- `server/auth_override.py` (a token-printing `verify_token` stub seen earlier
  in this investigation) no longer exists on disk and was never tracked by git
  and never imported by any module.
- Stopped the temporary local server (PID 8600) started for negative-path
  testing; port 8000 released.

## AURA 2.0 contract Phase 0 audit — 2026-08-28

- The user supplied the AURA 2.0 Master Implementation Contract (P0–P6).
- Produced `AURA_ARCHITECTURE_AUDIT.md` (175 lines) at the repo root: the
  contract requirement matrix mapped to repository evidence with
  EXISTS / PARTIAL / MISSING status per phase.
- Headline findings: native function calling, ToolExecutor five-gate risk
  model, execution state machine, capability registry, heartbeat-derived
  availability, memory conflict handling, and evidence-grounded prompts all
  EXIST and are test-pinned. Verified gaps: per-provider capability flags
  (P1), consolidated per-request diagnostic trace (P0), streaming token
  reconciliation (P0), tool output schemas / registry file (P3),
  claim-to-evidence response verifier (P4), calendar/email/SMS/contacts
  tools and app inventory (P5), web search RAG and observability dashboard
  (P6). Semantic recall remains deliberately absent (documented decision).
- No source code was modified in this milestone; the audit is a new
  untracked document only.
- BLOCKED on human review: the contract's Phase 0 gate requires the audit
  and three decisions (semantic memory, AppFunctions/SMS/email scope,
  phase ordering) to be reviewed before Phase 1 work starts. Device remains
  disconnected, so P5 work is hardware-blocked regardless.

## Phase 1 complete (provider capabilities, traces, reconciliation) — 2026-08-28

Human review approved Phase 0 with three decisions: capability registry +
traces first; semantic memory becomes a hybrid layer (lexical preserved);
Android task tools may be architected but are NOT verifiable until the
device reconnects. Provider FC capability must be recorded UNKNOWN until a
real request demonstrates it.

Implemented (device-independent, server-side only):

- `brain/providers/capabilities.py` (new): capability registry. FC
  structurally capable (gemini + openai/cerebras/custom/deepseek/qwen/xai)
  starts UNKNOWN; groq/mistral/openrouter/ollama/anthropic/mock are
  UNSUPPORTED (read from the code: no `generate_with_tools`). All FC
  statuses are UNKNOWN, not VERIFIED, at import - per decision 7.
- `brain/providers/errors.py`: `CapabilityUnavailableError`, deliberately
  NOT a `ProviderUnavailableError` subclass (failover cannot fix a
  capability gap); mapped to HTTP 501 `capability_unavailable` in
  `server/errors.py`.
- `server/routes/agent.py` `RouterToolCallingLLM`: capability-first
  selection - skips UNSUPPORTED candidates before any request is built,
  raises CapabilityUnavailableError when nothing capable remains, and
  promotes a provider to VERIFIED only after a real generate_with_tools
  round trip succeeds.
- `brain/providers/fallback.py`: `FallbackProvider.attempts` records
  (provider, outcome-category, error-type) per attempt in both generate
  and generate_with_tools - the provider trace.
- `core/trace.py` (new): consolidated per-request JSONL diagnostics to
  `logs/diagnostics.jsonl` (identifiers/counts/durations only; no user
  text; emission can never break the request). Includes
  `provider_label()` and `stream_reconciliation()`.
- `agent/runtime.py` `_stop`: emits one `agent_run` trace line per run
  (run/task/session ids, stop reason, rounds, tool calls, provider,
  duration).
- `server/routes/ws_chat.py`: stream reconciliation - fragments produced
  vs frames delivered, carried in the `complete` frame (`stream` field)
  and traced on both complete and error paths.
- New tests: `tests/test_capability_routing.py` (12),
  `tests/test_diagnostics_trace.py` (10), including an end-to-end agent
  run that lands exactly one trace line.

Test evidence: new/targeted suites 20 passed, then 345 passed across
eleven affected files. Full suite 3267 passed / 2 skipped / 1 deselected /
5 failed - the failures are exactly the documented pre-existing
settings-restart set; the 6th baseline failure (environmental held-key)
passed this run. Baseline was 3246 passed / 6 failed: +21 passing, zero
regressions.

Phase 1 items verified by CI tests but still UNKNOWN at the vendor level:
whether any specific provider actually accepts function-calling requests
in practice - the registry records that the moment it happens.

## Phase 2 complete (hybrid semantic memory) — 2026-08-29

Continued work an earlier session had started (`memory/embeddings.py`,
`memory/semantic.py`, `tests/test_semantic_memory.py`,
`scripts/benchmark_semantic.py` existed untracked). Audited what was there,
found and fixed four real gaps rather than re-implementing:

1. **`memory.semantic.weight` did not exist.** The contract's section 17
   lists it; nothing in the code read it and fusion was unweighted. Added
   to `HybridRetriever` (semantic's share of the fused score, lexical takes
   the rest), wired through `core/config.py`, `config.yaml` and
   `build_memory_pipeline`, clamped to [0, 1] so a bad config value cannot
   break recall. Default 0.5 scales both halves equally, so it orders
   results exactly as unweighted RRF did — the knob arrives without moving
   existing behaviour, which is pinned by a test that recomputes the
   unweighted formula independently.

2. **The benchmark had never been run, and was measuring the wrong thing.**
   `QUERIES` named CORPUS positions (0-based) while ground truth compared
   them against SQLite primary keys (1-based), so every expected id was off
   by one and the numbers were meaningless. Fixed by translating through an
   `ids_by_index` map built at insert time. Two methodology fixes on top:
   recall/precision now average over the queries that HAVE an answer (the
   two deliberately-unanswerable ones capped recall at 0.8), precision
   divides by results actually returned rather than by K, and the
   unanswerable queries are scored separately as `noise@K` instead of being
   averaged into the same column that hid them.

3. **The similarity floor was a guessed constant.** `min_similarity=0.05`
   was hardcoded in `SemanticRetriever`. The corrected benchmark showed why
   that matters: at 0.05 the hashing space returned a full 3 memories for
   every query with no correct answer, where lexical correctly returned
   none. Swept the floor and moved it to where the provider declares it —
   `recommended_min_similarity` on the provider, because the useful cutoff
   is a property of the embedding space, not of the retriever. Hashing
   declares 0.24 (the measured knee); ollama/remote declare a conservative
   0.05 labelled UNMEASURED, because no model-backed provider has been
   benchmarked in this repository and inventing a number would discard real
   recall. `memory.semantic.min_similarity` (default null) overrides.

4. **A real regression the earlier session left behind.**
   `tests/test_settings_fixture.py::test_fixtures_match_the_routes` failed:
   the new `memory.semantic` config block made the Android fixture
   `android/app/src/test/resources/live/settings.json` stale. Regenerating
   with `AURA_WRITE_ANDROID_FIXTURES=1` also rewrote host-dependent values
   (an active gemini chain, a masked API key, this machine's model name and
   tool list) into two other fixtures, so those were reverted and the
   semantic block was added surgically instead — one 14-line insertion.
   Both the app and its tests parse with `ignoreUnknownKeys = true`, so the
   added block cannot break a DTO; verified by forcing the Kotlin contract
   test to actually re-run (`--rerun-tasks`, not an UP-TO-DATE pass).

Benchmark, measured on the shipped configuration (10 memories, 8 answerable
queries, 2 deliberately unanswerable, K=3, hashing provider, floor 0.24):

    mode         recall@K  precision@K  noise@K
    lexical         0.583        0.521      0.0
    semantic        0.708        0.562      1.0
    hybrid          0.708        0.562      1.0

The sweep behind the 0.24 default (semantic mode):

    floor   recall@K  precision@K  noise@K
     0.05      0.833          0.5      3.0
     0.20      0.708          0.5      1.5
     0.24      0.708        0.562      1.0   <- default
     0.26      0.583        0.458      0.0
     0.50      0.125         0.25      0.0

Read honestly: semantic buys +0.125 recall and +0.041 precision over
lexical, and pays 1.0 noise memories per unanswerable query where lexical
pays none. Above 0.26 semantic's recall collapses to lexical's and it stops
earning its place. That is the hashing provider's limit, documented in the
provider itself; it is not evidence about a model-backed provider.

Tests: `tests/test_semantic_memory.py` 31 → 43 passed (12 added, covering
the weight knob, the provider-declared floor, sub-floor exclusion, clamping,
and both config paths). Full Python suite `3310 passed, 2 skipped, 1
deselected, 5 failed` — the 5 are exactly the pre-existing
settings-restart `RuntimeError: no running event loop` set, confirmed
unrelated by running them in isolation. Baseline was 3267 passed / 5 failed:
+43 passing, zero regressions. Android `:app:testDebugUnitTest` 28 suites /
388 tests / 0 failures / 0 errors.

Documentation: `.aura/decisions/ADR-007.md` + `.json` (the repository's own
ADR convention, following ADR-006), `AURA_ARCHITECTURE_AUDIT.md` Phase 2 row
flipped from MISSING to EXISTS with evidence and the resolved gap-5 entry,
`docs/IMPLEMENTATION_STATUS.md` limitation 5 rewritten rather than deleted —
it now states the honest limit (opt-in, and the default provider does not
understand paraphrase) instead of the stale "no embedding model anywhere".

NOT verified: no model-backed embedding provider (ollama or remote) has been
exercised against a real server, so their code paths are IMPLEMENTED BUT NOT
VERIFIED and their similarity floors are unmeasured. Semantic recall has
never run inside a live conversation, only in tests and the benchmark.

### Phase 2 addendum - the two-switch gap (2026-08-29)

Self-review found a fifth gap the earlier session left, and it was the kind
that produces a support question rather than a stack trace. Semantic wiring
sits behind `provider is not None and pipeline.recall_enabled`, so a config
with `memory.semantic.enabled: true` and the shipped `memory.recall: false`
built no hybrid retriever and no indexer, said nothing, and looked exactly
like a broken feature. `build_memory_pipeline` now logs a warning naming
`memory.recall` as the switch actually holding it closed.

Not building the indexer in that case is correct and now documented as
deliberate: embedding a memory sends its text to the provider, which for a
REMOTE provider is the exfiltration boundary. Recall off therefore means no
embedding calls, not merely no results.

`tests/test_semantic_memory.py` 43 -> 44 passed. Full Python suite
`3311 passed, 2 skipped, 1 deselected, 5 failed` - the same pre-existing
settings-restart set, each reproduced in isolation with
`RuntimeError: no running event loop`. +1 passing, zero regressions.
### Phase 3 — structured tool output contract (2026-08-29)

Phase 3 of the AURA 2.0 contract is complete, device-independent. Everything
below is pinned by `tests/test_tool_output_contract.py` (46 tests) plus the
updated `tests/test_diagnostics_trace.py` per-execution trace assertions.

Pre-flight audit traced the full tool lifecycle (model decision -> tool
selection -> schema validation -> permission/policy gates -> execution ->
result -> observation -> model response) across `tools/`, `brain/`,
`core/`, `agent/`, `server/` and `tests/`. Findings: tool definitions are
generated from the tool class itself (`Parameter` tuples -> `schema.py`
JSON-Schema export), arguments are validated by the executor's gate 5,
errors were represented as a bool + two free-form strings, and results
reached the LLM as prose via `brain/conversation.py._render_result`. No
layer had a canonical status/error/evidence vocabulary - that was the gap
Phase 3 fills.

Changes:

- `tools/outcome.py` (NEW): `ToolStatus` (SUCCESS/FAILED/PARTIAL/DENIED/
  UNAVAILABLE/INVALID_ARGUMENTS/TIMEOUT/CANCELLED/UNKNOWN), `ToolError`
  (code/category/message/provider/capability; category derived from code),
  `ToolErrorCategory` (VALIDATION/PERMISSION/POLICY/CAPABILITY/PROVIDER/
  NETWORK/TIMEOUT/EXECUTION/INTERNAL/UNKNOWN), `Evidence` (kind/source/
  tri-state verified/timestamp/reference; POSTCONDITION/OBSERVATION/RECEIPT/
  RETURN_VALUE), `SideEffect` (READ_ONLY/IDEMPOTENT/NON_IDEMPOTENT/UNKNOWN),
  `Retryability`, `retryability_of(status, side_effect)` (derived, never
  asserted), `evidence_state()` -> NONE/UNVERIFIED/VERIFIED/CONTRADICTED,
  and the `CODE_*` constants each ToolErrorCategory maps to.
- `tools/base.py`: `ToolResult` gains status/error_code/evidence/
  execution_id/started_at/completed_at/side_effect. `ok` is reconciled FROM
  `status` in `__post_init__` - only SUCCESS may be truthy, so UNKNOWN can
  never read as success. `serialize_for_model()` renders fixed
  STATUS/TOOL/EVIDENCE/RETRY/OUTCOME/ERROR lines (deterministic, no
  arguments, no internals, no secrets).
- `tools/schema.py`: `output_schema()` (class-declared), `validate_output()`
  (microsecond subset validator - type at root, required/property types one
  level deep, items for arrays), `tool_definition()` (one canonical
  machine-readable definition per tool), `mcp_export()` (standards
  `tools/list` shape: name/description/inputSchema), shared `matches_type`
  table.
- `tools/executor.py`: stamps execution_id/started_at/completed_at, derives
  RETURN_VALUE evidence for unverified successes, validates declared output
  schemas (malformed output -> UNKNOWN, never SUCCESS), folds contract keys
  into `data` for envelope callers, and emits one `tool_execution`
  diagnostics line per execution (identity, status, duration, retryability,
  evidence state, capability - never arguments or content). Trace failure
  never breaks execution (tested).
- `tools/registry.py`: `definitions()` (discovery, schema inspection,
  capability/risk/version filtering - the answer to "what can AURA do right
  now" without reading source), `export_mcp()`, `by_side_effect()`.
- `tools/builtins/*`: declared honest `side_effect` classes (clock/filesystem
  READ_ONLY or IDEMPOTENT, apps/desktop/input NON_IDEMPOTENT where
  appropriate).
- `brain/conversation.py`: `_render_result` routes every `ToolResult` through
  `serialize_for_model`; foreign duck-typed results keep the legacy prose.
- `tests/test_tool_output_contract.py` (NEW, 46 tests): registry discovery,
  parity, schema validity, argument validation (valid/unknown/mistyped/
  missing/non-plain), every execution status (success/failure/timeout/
  unavailable/denied/unknown-tool/invalid), output validation (valid/
  malformed/missing-key/shape rules), retry rules across status x
  side-effect, evidence (verified/contradicted/unverified/strongest), the
  deterministic serializer (incl. "does not leak internal fields", "UNKNOWN
  never renders as success"), UNKNOWN-not-ok-by-construction, PARTIAL-not-
  success, gate statuses never report attempted, capability failure remains
  a structured CAPABILITY result (the original FC regression), diagnostics
  integration, trace-failure tolerance, and a measured microsecond overhead
  test.
- Original FC/capability regression (contract item 19): pinned in
  `test_capability_unavailable_is_not_a_generic_provider_failure` and
  `test_capability_unavailable_does_not_mark_side_effects_retryable` -
  `CapabilityUnavailableError` is NOT a `ProviderUnavailableError`,
  category is CAPABILITY not PROVIDER, and a NON_IDEMPOTENT UNAVAILABLE is
  not retryable. If a capable provider exists, Phase 1 routing already
  reaches it (`test_capability_routing.py`); if none does, the final state
  is `CAPABILITY_UNAVAILABLE`, never generic PROVIDER_FAILURE.

Test counts: `tests/test_tool_output_contract.py` 46 passed; targeted
tool/capability/diagnostics suites 197 passed. Full Python suite
`3357 passed, 2 skipped, 1 deselected, 5 failed` - the 5 are exactly the
pre-existing settings-restart `RuntimeError: no running event loop` set
(test_companion, test_settings_api x2, test_settings_contract x2). Baseline
was 3311 / 5: +46 passing, zero regressions.

Documentation: `AURA_ARCHITECTURE_AUDIT.md` Phase 3 table rewritten (output
schemas, status taxonomy, structured errors, side-effect/retry semantics,
registry, MCP-adapted export all EXISTS) and gap 3 marked RESOLVED;
`.aura/decisions/ADR-008.md` + `.json` created (status taxonomy, error
taxonomy, evidence model, retry/idempotency semantics, registry design,
MCP compatibility, side-effect handling, rejected alternatives).

MCP status (contract item 15): the registry's `export_mcp()` is
**MCP-adapted** - it renders the conceptual `tools/list` shape
(name/description/inputSchema) shared with the OpenAI function form, and
`output_schema`/`side_effect`/`risk`/`version` ride in the richer
`definitions()` payload. NOT a full MCP server and MCP is NOT a runtime
dependency; nothing in the architecture consumes an MCP client today.

Performance (contract item 20): measured in-test, ~microseconds per full
executor round including schema validation, stamping and serialization
(`test_the_contract_overhead_is_microseconds`, asserts < 5 ms/call, observed
well under). No model calls added for validation; the type table is shared,
not duplicated.

NOT verified / remaining: no static registry file (deliberate - availability
is a live fact joined at runtime, ADR-008); Android task tools remain Phase
5 and blocked on the disconnected device; the response-level claim verifier
remains Phase 4 work.
