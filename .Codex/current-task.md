# Current task

Phase 5A **LIVE-CLOSED** — 2026-09-05. Every item of the Phase 5A.8 brief is
VERIFIED on real hardware (`IBCQMB4PTGNZJVTO`), including the final mutating-
action link: one live `android.launch_app` on the stock calculator produced an
observed `postcondition {"verified": true}` that reached
`ClaimState.VERIFIED` with `VerifierDecision.PASS` and an unmodified reply.
Chain proof 16/16; evidence and the exact device report in
`.Codex/progress.md` (2026-09-05 final).

All Phase 1–5A work is COMMITTED and PUSHED on `origin/feature/aura-identity`.

## Status table

| Item | State |
| --- | --- |
| Repo integrity (nothing discarded) | VERIFIED |
| Phase 1–5A committed + pushed | VERIFIED |
| Installed APK == current source | VERIFIED (sha256 match) |
| Companion connection + token | VERIFIED (heartbeat 200; token never read) |
| 15 Android capabilities AVAILABLE | VERIFIED |
| `android.app_inventory` AVAILABLE live | VERIFIED |
| Live `PackageManager` enumeration | VERIFIED (277 pkgs, 3.80 s) |
| Inventory freshness / no cache | VERIFIED (`observed_at` 23.9 s old) |
| Diagnostics privacy | VERIFIED (0 leaks in 10,567 lines) |
| postcondition → POSTCONDITION Evidence | VERIFIED |
| Evidence → `ClaimState.VERIFIED` | VERIFIED (live, decision PASS) |
| **Verified postcondition on a mutating action** | **VERIFIED (live, 16/16)** |
| False postcondition → CONTRADICTED | VERIFIED (read-only + mutating tools) |
| Bare `{"ok": true}` is not verification | VERIFIED (INFERRED, not VERIFIED) |
| Observation ≠ postcondition | VERIFIED |
| Regression vs baseline | VERIFIED — 3489/2/1/5, zero regressions |

Nothing in Phase 5A remains UNKNOWN or BLOCKED.

## Open items

1. **Connection URL is `http://127.0.0.1:8000/`** — left deliberately for the
   owner to restore to `https://aura-xwm4.onrender.com/` through the
   Connection UI, which pre-fills the token from state
   (`ui/hub/ConnectionSection.kt`) and therefore preserves it. Verified
   earlier that the Render URL works: the companion long-polled it
   successfully once Render had woken.
2. **Production is 18 commits behind.** `origin/main` has neither
   `tools/outcome.py` nor `brain/verify/`, so the deployed server cannot emit
   POSTCONDITION Evidence or grade a ClaimState. Everything verified here
   lives on `feature/aura-identity` only. Merging is a separate decision.
3. **Not fixed on purpose** (verify-only): `ToolResult.capability` is the
   literal `"unknown"` for bridge reports, so claim binding rests on tool name
   and outcome text; and repair phrasing can pick an awkward object noun
   ("I can't verify that the android was actually confirmed").
4. `tests/conftest_caps.py` and `tests/conftest_capabilities.py` are dead,
   unreferenced scratch. Left on disk, deliberately NOT committed and NOT
   deleted — the owner's call.
5. The stock calculator is left in the foreground on the device; pressing home
   would have been a second unrequested mutation.

## NEXT

Phase 5 task tools (calendar / email / SMS / contacts). Still gated on the
security review for dangerous permissions, and on API level: AppFunctions is
Android 16+ and this device is API 33.

---

## Historical record below (superseded, kept deliberately)

The 2026-08-31 offline foundation and the 2026-09-01 stopped live attempt are
retained as written, because their statuses were honest at the time. One of
them was wrong and is corrected here: the 2026-09-01 entry recorded the
installed APK as the OLD pre-Phase-5A build, but the install did land
(`lastUpdateTime=2026-09-01 08:31:25`, sha256 match with the local build).

## Phase 5A offline foundation — 2026-08-31

Scope at the time: offline/CI-verifiable only; NO live device work, NO
APK/install, NO companion, NO URL/token restore. See
`.aura/decisions/ADR-010.md`.

What was implemented (all reusing existing primitives, no new model, no new
dependency, no cache):
- Capability `android.app_inventory` (registered canonically in
  `core/capabilities/factory.py`, `required_dependencies=["android.companion"]`,
  same accessibility health/permission gates as the other 14 Android caps).
  Registration alone never claims availability.
- Tool `android.list_apps` (`AndroidListApps`, `tools/providers/android_provider.py`):
  `ToolRisk.SAFE`, `SideEffect.READ_ONLY`, structured output, `observed_at`
  required, `device_id` when known.
- Bridge validation `normalise_device_report` + `_valid_inventory`
  (`android_bridge.py`): malformed inventory → `EXECUTION_FAILED`, never
  success, never coerced; UNKNOWN stays UNKNOWN.
- Android half `android/.../accessibility/AppInventory.kt` (`PackageSource` /
  `PlatformPackageSource` / `AppInventory`): pure, deterministic, JVM-testable;
  launchability from the MAIN/LAUNCHER query only; `QUERY_ALL_PACKAGES` not added.
- Evidence seam: `_evidence_from_report` / `tool_result_from_report`
  (`android_provider.py`) convert a device postcondition with an explicit
  boolean into canonical Phase 3 `EvidenceKind.POSTCONDITION`; an
  `app_inventory` observation becomes `EvidenceKind.OBSERVATION` (never
  memory). Closes the chat-path gap (verified Android action could not reach
  VERIFIED). `verified=true` → VERIFIED; `verified=false`/missing/malformed →
  never VERIFIED; bare `{ok:true}` is never verification.
- Dispatcher wiring `DeviceToolDispatcher.kt`: `android.list_apps` dispatch +
  `ACCESSIBILITY_CAPABILITIES` (includes `android.app_inventory`).

Offline verification:
- Python: `tests/test_android_inventory.py` 36 passed; full suite
  `3489 passed / 2 skipped / 1 deselected / 5 failed` — the 5 are exactly the
  pre-existing settings-restart set; baseline 3453 → +36, zero regressions.
- Android JVM: `AppInventoryTest.kt` 26 passed (0 failures) after fixing a
  compile error (`app()` helper must accept a nullable package name) and a
  false manifest assertion (the manifest only *mentions* QUERY_ALL_PACKAGES in
  a comment; it is not declared). Full JVM run: 414 tests / 2 failures — the 2
  are pre-existing `SettingsContractTest` fixture-drift failures (working-tree
  `providers.json` / `provider_health.json` were updated to a live configured
  Gemini by earlier uncommitted baseline work; unrelated to Phase 5A).
- `git diff --check` clean for Phase 5A files.

Capability states:
- Offline verifier/pipeline inventory: VERIFIED (via tests).
- Live `PackageManager` enumeration: IMPLEMENTED BUT NOT VERIFIED (device
  disconnected).
- Performance of real enumeration: UNKNOWN (no handset measurement).

NEXT (deferred, live-device): re-connect `IBCQMB4PTGNZJVTO`, install the APK,
run real `android.list_apps`, and verify the postcondition→VERIFIED path over a
real heartbeat. Restore stored URL to `https://aura-xwm4.onrender.com/` via the
Connection UI (keeps the token). Then Phase 5 task tools (calendar/email/SMS/
contacts) — still blocked on device + security review.

## Phase 5A.8 live verification attempt (2026-09-01) — STOPPED: DEVICE DISCONNECTED

Steps completed before the stop (all read-only, no device mutation):
- Repo state re-checked: branch feature/aura-identity, Phase 1–5A uncommitted
  work present, nothing reset/checked-out/cleaned.
- Device WAS connected (API 33, com.aura.companion v0.1.0 installed,
  accessibility services enabled + active, app process alive).
- Current Phase 5A debug APK built successfully (JDK 17, documented Gradle
  wrapper); provenance = fresh build of this working tree. NOT installed —
  Step 4 was never reached, so the INSTALLED v0.1.0 APK is still the OLD build
  and provably does not contain Phase 5A.
- Server started via documented entrypoint (config.yaml); authenticated
  /api/device/poll responded 401 without auth / 200 with auth (host-side).
- Reverse tunnel adb reverse tcp:8000 tcp:8000 set; transport device→host
  PROVEN at TCP level (device-originated connection reached a host listener).

BLOCKER (stop condition hit): the device disconnected mid-Step-5 and
`adb wait-for-device` blocks indefinitely — physical reconnect required.

What that leaves UNVERIFIED (do not trust docs over this list):
- Installed APK freshness: the installed v0.1.0 is the OLD build.
- Companion connection settings could not be read (prefs read died with the
  device); whether the stored URL/token are usable is UNKNOWN.
- Live heartbeat, android.app_inventory AVAILABLE state, live
  PackageManager enumeration, diagnostics privacy on a live request,
  postcondition→Evidence on a live round trip, chat-path grounding live:
  all UNKNOWN / BLOCKED.
- Offline suites remain exactly as the baseline: 3489/2/1/5 — nothing about
  this session changed them, and nothing live was claimed.


# Current task

Phase 4 (claim→evidence response verifier) COMPLETE — 2026-08-30.
`brain/verify/` implements the deterministic boundary (claim states,
typed claims, request-scoped evidence ledger reusing Phase 3 Evidence,
hard action-claim rules, memory attribution, live-registry capability
checks, minimal repair, hallucination taxonomy, privacy-safe verifier
trace). Wired into `ConversationManager.chat` and `chat_stream` via the
launcher's `response.verify` config; `enabled:false` restores the
pre-Phase-4 pipeline. ADR: `.aura/decisions/ADR-009.md`.

Resumption fixes (2026-08-30): the sentence splitter's look-behind
collapsed whole replies into one claim (5 verifier test failures) —
replaced with a boundary regex plus an explicit false-end check
(abbreviations, initials); the first-person action pattern now tolerates
intervening adverbs ("I definitely already sent..."); the Android
settings fixture was regenerated for the new `response.verify` keys.
Full suite 3420 passed / 2 skipped / 1 deselected / 5 failed (the exact
pre-existing settings-restart set). Baseline 3357 / 5: +63, zero
regressions.

NEXT (unchanged):
AURA 2.0 contract. Phase 0 (audit) COMPLETE and human-approved. Phase 1
(provider capability registry + capability-first routing, per-request
diagnostic trace, stream reconciliation) COMPLETE and verified - see
`.Codex/progress.md` 2026-08-28 and the new
`tests/test_capability_routing.py` / `tests/test_diagnostics_trace.py`.

Phase 2 (hybrid semantic memory) COMPLETE and verified - 2026-08-29. See
`.Codex/progress.md` and `.aura/decisions/ADR-007.md`.

Phase 3 (structured tool output contract) COMPLETE - 2026-08-29. Device-
independent, per `.aura/decisions/ADR-008.md`. All requirements of the
Phase 3 STOP CONDITIONS were held: nothing was rebuilt that did not need to
be, and output schemas are only validated when a tool declares one (existing
string-returning tools are untouched). Full Python suite 3357 passed / 2
skipped / 1 deselected / 5 failed, where the 5 are exactly the pre-existing
settings-restart `no running event loop` set; baseline was 3311 / 5, so +46
passing, zero regressions. See `.Codex/progress.md` 2026-08-29 (Phase 3).

NEXT (in order, per the approved decisions):

1. Android task tools (Phase 5): interfaces, schemas, permission/policy
   scaffolding, mocks and tests ONLY - no real-device claims until the
   phone reconnects and end-to-end tests produce evidence. Still blocked:
   physical device `IBCQMB4PTGNZJVTO` disconnected; companion URL restore
   via Connection UI still outstanding.
2. Claim→evidence verifier (Phase 4, audit gap 6): loop-level verification
   exists; a response-level verifier over free-form chat does not.

Optional Phase 2 follow-up, only if a real corpus justifies it: benchmark
a model-backed provider and set its floor from the sweep. Worth doing
before semantic recall is turned on for real, since the shipped hashing
provider does not understand paraphrase.

---

AURA 2.0 Master Implementation Contract received (2026-08-28). Phase 0
(codebase audit against the contract) is COMPLETE: see
`AURA_ARCHITECTURE_AUDIT.md` at the repo root. WAITING ON HUMAN REVIEW of
that audit before Phase 1 (provider capability registry + router refactor)
begins — this is the contract's own Phase 0 gate. Three decisions are needed:

1. Approve gap ranking / phase order (audit sections 3 and 5).
2. Semantic memory: contract wants vector recall; the codebase documents a
   deliberate lexical-only decision. Override or keep?
3. Android task tools (SMS/email/contacts/calendar): contract wants them;
   device is API 33, AppFunctions is Android 16+, and dangerous permissions
   require security review. Scope decision needed.

Meanwhile the pre-existing device-verification task below stays blocked on
hardware.

---

Make AURA's Android capabilities runtime-grounded and executable through the invariant:

`intent -> discovery -> capability registry -> permission -> health/dependency -> ToolExecutor -> real Android -> ToolResult -> LLM`.

Current focus: BLOCKED on hardware. The strict Android capability integration
is committed and pushed (a97bc69 on feature/aura-identity). Everything that can
be verified without the phone has been verified.

BLOCKER: physical device `IBCQMB4PTGNZJVTO` is disconnected (`adb devices`
empty). The following remain NOT VERIFIED and require the phone:

- install the freshly built `app-debug.apk` (sha256 11f48b5675fe8c5b0...)
- runtime service check on the real device
- the 11 real capability executions through the AURA pipeline
- the real `NODE_NOT_FOUND` failure path
- observation and action grounding on live screen state
- final device state confirmation

RECOVERY TASK, still outstanding: the device's stored server URL is
`http://127.0.0.1:8000/` and must be restored to
`https://aura-xwm4.onrender.com/` through the app's Connection UI, which
preserves the stored token because that field is pre-filled from state
(`ui/hub/ConnectionSection.kt`). The `adb reverse` mapping is already gone -
it died with the USB disconnect - and the temporary local server has been
stopped. Do this the moment the device reconnects.

Current verified device state: the physical package is installed and both AURA
accessibility services are enabled/bound. The companion sent a live heartbeat
to an authenticated local server and all 14 canonical Android capabilities
were `AVAILABLE`.

Completed milestones in this task:

- Per-tool Android capabilities are dynamically registered and resolved from
  companion status.
- ToolExecutor and `/api/device/invoke` are the server execution gates.
- The HTTP harness preserves live capability evidence and structured failure
  codes.
- The legacy direct agent-step body is disabled; AgentRunDriver is the active
  companion agent path.
- Discovery ranking was verified for six Android intents; every intended
  capability ranked first, and `select_best_executable` returned none while
  the real device was unavailable.
- Full Python suite completed with 3228 passed, 2 skipped, 1 deselected, and
  5 pre-existing settings-restart failures. The focused capability/device
  suite completed with 545 passed. Android Gradle unit tests and compilation
  succeed with the documented JDK 21/TEMP/TMP workaround.
- Final local live API check confirms 14 Android capabilities are `AVAILABLE`,
  with `authorization=granted`, `health=healthy`, and no stale reason.
- Safe physical execution succeeded for foreground app, UI tree, UI search,
  screenshot, tap/back/home, launch, wait, verify, text input, and the fixed
  node-scoped backspace path. A real missing-node failure and unknown-tool
  rejection also returned structured results.
- The companion dispatcher now re-checks its own runtime capability status
  immediately before dispatching a known Android tool.
