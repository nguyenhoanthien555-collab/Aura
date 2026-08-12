# Current Task

## Vision production wiring (DONE, uncommitted)

The one confirmed bug from the pre-test sweep, fixed: the server-side
Vision pipeline was complete and nothing on the phone ever called it.

**Root cause was an absent caller, not a wrong result.**
`AuraRepository.uploadScreenshot()` and the whole
`/api/screen/upload` → `RemoteScreenSource` → `VisionManager` →
`CloudVisionProcessor` path worked; no Android production class invoked
it, and `AccessibilitySnapshot.screenshotAvailable` was the literal
`false`. Every existing test passed the entire time, which is why the
regression worth pinning is structural.

**Added:** `screen/ScreenshotCapture.kt` - a `ScreenshotCapture`
interface over `AccessibilityService.takeScreenshot(displayId, Executor,
callback)` (API 30, the only screenshot API this app can reach; both
services already hold the accessibility grant, so MediaProjection would
have been a second mechanism). `HardwareBuffer` → software bitmap →
downscale to `vision.max_pixels` → JPEG q80, off the main thread,
cancellation-aware via `suspendCancellableCoroutine`.

**Added:** `screen/ScreenshotUploader.kt` - the single gate both services
call, pure Kotlin so a JVM test reaches it. Gates in order:
`screenObservationEnabled` → `uploadScreenshots` → `isConfigured` →
`isSupported` → an 8 s interval stamped on every *attempt* (matched to
`server.screen.min_interval`, so a down server does not cost a
full-screen encode per event). Returns `Sent` / `Skipped(reason)` /
`Failed(reason, error?)`; both callers log `Failed`.

**Wired:** `ScreenObservationService` uploads pixels *after* its existing
`sendScreen` POST - mandatory, because `RemoteScreenSource` is one
last-write-wins slot and the frame-only observation must land last or
`describe()` returns `""`. `AuraAccessibilityService` sets
`screenshotAvailable = outcome is ScreenshotOutcome.Sent`. Both service
XMLs now declare `android:canTakeScreenshot="true"` (the framework throws
without it) - a visible change in what the accessibility grant covers.

**One server change, justified:** `upload_screenshot` awaited synchronous
`runtime.observe_screen` inline, which with a frame attached reaches a
real VLM request on the single event loop - the same defect `/api/chat`
had, unreachable until a phone actually uploaded pixels. Now
`await run_in_threadpool(...)`, pinned by
`tests/test_cloud_failover.py::test_upload_screenshot_does_not_run_on_the_event_loop`.
`/api/screen` (no frame) deliberately left on the loop.

Tests: 16 in `ScreenshotUploaderTest` (MockWebServer, injected clock,
fake capture), 4 in `ScreenshotWiringTest` (both services declare a
`ScreenshotUploader`; availability derives from the outcome; the wire
field is `screenshot_available`, and absent means false because
`Json.Default` omits defaults), 1 backend. Backend **1766 passed, 1
skipped, 1 deselected**; Android **292 across 19 classes**.

NOT verified: no device attached, so nothing was captured or uploaded on
hardware. API 26-29 cannot capture. A JVM test cannot prove Android
delivers an event that runs the uploader.

## Pre-test repository bug sweep (DONE, uncommitted)

One audit pass over the runtime-critical paths before the next real-device
test. One confirmed bug fixed, three findings reported as risks rather than
changed.

**Fixed:** `POST /api/chat` was an `async def` route calling the fully
synchronous `runtime.chat()` inline, so every turn held the single ASGI
event loop for the whole model call (up to `llm.timeout: 120`). While any
turn was in flight nothing else was served - `/api/health`,
`/api/notifications` and the phone's next agent tick all queued behind it.
Now `await run_in_threadpool(runtime.chat, ...)`, matching what
`ws_chat.py` already does with `iterate_in_threadpool`. Regression test
`tests/test_server.py::test_chat_does_not_run_on_the_event_loop` asserts
from inside the call that `asyncio.get_running_loop()` raises, and it was
confirmed to fail with the offload removed.

Consequence accepted on purpose: two concurrent `/api/chat` calls can now
genuinely overlap where the loop used to serialise them. Aura is
single-tenant by design and `memory/sqlite.py` serialises through
`db_lock`, so the trade is a real one - freezing every other route for the
length of every reply is worse.

**Reported, not changed** (see project-state "Pre-test sweep findings"):
`llm.timeout` is ignored by `GeminiProvider`; a mid-chain
`is_account_limit` aborts the rest of the fallback chain; streaming
silently degrades to one chunk whenever a fallback chain initialises.
(The fourth finding, unwired Android screenshot upload, is now fixed -
see the section above.)

## Runtime-quality regression after the model change (DONE, uncommitted)

**Symptom:** Aura got noticeably worse - replies stopping mid-sentence,
and the Android agent reaching "Task timed out: maximum number of steps
reached." **Root cause was orchestration, not the model.**

`llm.max_output_tokens: 768` was sized for a non-thinking model.
`gemini-3.6-flash` reasons before it answers and bills those thoughts
against the same budget, and `brain/providers/gemini.py` sent no
`thinking_config` and discarded `finish_reason`. Measured live against
the real API on the real production prompt:

| question | finish | thought tokens | answer tokens |
|---|---|---|---|
| "sqlite or postgres for a small app" | MAX_TOKENS | 686 | 78 |
| "debug a memory leak in a python service" | MAX_TOKENS | 705 | 59 |
| 15-node agent tick | STOP | 738 | 22 (760/768) |

So a truncated reply was returned as a successful one - `response.text
or ""` cannot tell "finished in four words" from "cut off after four
words" - and an empty one was saved as an assistant turn and published
to the UI with no error, no log and no failover. The agent path sat one
token from the cliff on a 15-node tree; a real accessibility tree
crossed it, the truncated JSON failed to parse, and the retry budget ran
out as "maximum number of steps reached".

**Fixed:** new `llm.thinking_level` setting (default `low`), sent as
`thinking_config`; `finish_reason == MAX_TOKENS` now logs a warning with
the budget and the two settings that fix it, and raises
`ProviderUnavailableError` when the reply is empty so the fallback chain
is offered the outage. Streaming gets the thinking level but still sends
no budget, deliberately.

Files: `brain/providers/gemini.py`, `core/config.py`, `config.yaml`,
`tests/test_gemini_thinking_budget.py` (new, 8 tests), and one
regenerated line in `android/app/src/test/resources/live/settings.json`.

Verified live after the fix: the same two questions return 1617 and 2503
characters ending in complete sentences; a 60-node agent tick returns
clean JSON; the intent probe still answers one word. Backend **1764
passed, 1 skipped, 1 deselected**; Android **273 passed, 0 failures**.

Not done: not committed. `llm.thinking_level` is not on the settings
allow-list in `core/settings_store.py`, so it cannot be changed from the
phone - out of scope for this fix, worth one line later.

## Repair mandate

Fix every defect in the full-project audit, P0 -> P3, working phase by
phase. Run the relevant tests after each phase, run the full hermetic
suite before declaring a phase complete, and review `git diff` after
each phase. Do not commit unless asked.

Hermetic suite: `.venv/Scripts/python.exe -m pytest -q`
Baseline: 885 passed, 1 deselected. After Phase 2: 958. After Phase 3: 1053.
After Phase 4: 1067. After Phase 5: 1101. After Phase 6: 1146.
After Phase 7: 1160. After Phase 8: 1441 (stale - the tree measured 1480
when Phase 9 began). Phase 9 backend: 1535. Phase 10 backend: 1628.

## Phase status

- [x] Phase 0 - Baseline (885 passed)
- [x] Phase 1 - Error visibility (916 passed)
- [x] Phase 2 - Android accessibility (958 passed + 132 Android)
- [x] Phase 3 - Tool/capability pipeline (1053 passed): AURA-P0-001,
      -002, -003, -004, AURA-P1-001, -002. Implementation was found
      already written and was reviewed rather than rewritten; the missing
      95 tests were added and mutation-checked.
- [x] Phase 4 - Device-action boundary (1067 passed): AURA-P0-005.
      Diagnosed as Case B in speech only - nothing could execute, but
      the honesty rule lived in the TOOLS section, which renders nothing
      when the catalogue is empty. Moved to `prompts/system.md`. No
      `/device` route, no `open_url`, no new abstraction.
- [x] Phase 5 - Provider cleanup / reliability (1101 passed): AURA-P1-009,
      -010, P2-003, -004, -009, -010, plus an extension of P1-012 (which
      Phase 1 had already closed). Ollama now builds a real chain and can
      be a fallback member; OLLAMA_HOST is read; the model comes from
      `llm.ollama_model` instead of a `startswith("gemini")` test;
      `fallback_providers` is authoritative and the legacy singular key
      is honoured with a warning instead of silently ignored. Groq and
      Mistral KEPT, Cerebras kept but explicitly unwired, no DeepSeek
      provider invented.
- [x] Phase 6 - Security & deployment hardening (1146 passed):
      AURA-P1-007, -008, -014 and the deployment half of AURA-P0-005.
      NOTE THE RENUMBERING: this plan previously called Phase 6 "local
      Windows device agent". The device agent was explicitly deferred and
      prohibited for this phase; hardening took the slot. The device
      agent is now Phase 7+ and its notes below still stand unchanged.
- [x] Phase 7 - Repository cleanup, final P2/P3, cross-phase verification
      (1160 passed): AURA-P2-002, -005, -006, -007, P3-001, -002, -003,
      -004, -005, plus the P2-001 wiring test and a second-pass audit.
      THE RENUMBERING HAPPENED AGAIN: this plan called Phase 7 "local
      Windows device agent". It was not built and was not asked for. The
      device agent is now Phase 8+ and its notes below stand unchanged.
- [x] Phase 8 - Memory 2.0 + Temporal Context + User Model + Proactive
      System (1441 passed, 0 failed, 0 errors). NOT the device agent:
      the user's Phase 8 spec was the memory/temporal/proactive work, so
      the device agent moves to Phase 9+ and its notes below stand
      unchanged. Delivered: `core/temporal.py`, `memory/pipeline.py`,
      `memory/episodic.py`, `memory/temporary.py`, `memory/selection.py`,
      `memory/user_model.py`, `memory/user_profile_seed.py`,
      `proactive/` (7 modules), `vision/settings.py`, `tests/conftest.py`
      and 5 new test files (281 new tests). Wired through
      `launcher/services.py`, `brain/conversation.py` and
      `server/runtime.py`.
- [x] Phase 9 - Android Control Hub, provider/key management (COMPLETE:
      backend 1550 passed / 1 deselected, Android 132 unit tests passed,
      lint 0 errors / 44 warnings)
- [x] Phase 10 - Android <-> server settings contract (COMPLETE: backend
      1628 passed / 1 deselected, Android 175 unit tests passed, debug
      APK built and verified, four routes checked against a real uvicorn).
      The reported 404s had two causes: a deployment older than the
      commit that added the routes, and one boolean carrying two facts on
      the client. Three real bugs fixed - the empty `auto_approve`
      permission widening, `/api/health` 500ing when the provider key is
      missing, and reports served from a config snapshot taken at process
      start. Details in `progress.md`; the standing architecture is in
      `project-state.md`.
- [x] Phase 11 - Render startup recovery, provider coverage, Hub
      redesign. COMPLETE, committed as `95ab4f1` and recorded in
      `07e3cda`.
- [x] Phase 12 - Android Settings integration audit. COMPLETE and
      **UNCOMMITTED** (held for approval). Backend 1756 passed / 1
      skipped / 1 deselected; Android 273 passed across 17 classes; debug
      APK rebuilt from clean. Root cause was the client, not the server:
      the settings verdict was a boolean plus free text, so every
      settings failure after a 200 from `/api/health` rendered as "this
      Aura server does not expose settings". Now a typed
      `settingsError: AuraError?` and one `SettingsAccess` enum. Detail
      in `progress.md`; the decision is in `decisions.md`.

**Correction to the three entries above:** they said "nothing from
Phase 9 or Phase 10 is committed" and named `35589a0` as HEAD. Both are
stale. HEAD is `b5ec777 Fix settings connectivity and provider
management` with a clean tree, so Phase 9 *and* Phase 10 are committed.

## Phase 11 (IN PROGRESS)

Backend baseline entering the phase: **1628 passed, 1 deselected**.
After the Render fix: **1642 passed, 1 skipped, 1 deselected**.

- [x] **11.1 Render startup crash - FIXED and verified on 3.14.6.**
      Not the annotation. `requirements-server.txt` pinned
      `sqlalchemy==2.0.36` while *nothing pinned the interpreter*, so
      Render's native runtime default moved to 3.14, where PEP 604 makes
      `typing.Union` an alias of `types.UnionType` and 2.0.36's
      `cast(Any, Union).__getitem__(types)` becomes an unbound
      descriptor call. It fires for every optional column, so the first
      `Mapped[str | None]` in the metadata killed the import -
      `UserModelEntry.last_confirmed_at`. Fix: pin `sqlalchemy==2.0.51`
      (server), floor `>=2.0.51` (dev), new `.python-version` = 3.12
      matching `Dockerfile`, `docs/DEPLOYMENT.md` §1a, and
      `tests/test_deploy_startup.py` (15) asserting the *pairing* on any
      interpreter. Booted under Python 3.14.6 and confirmed
      `/api/health`, `/api/settings`, `/api/providers`,
      `/api/providers/health` all 200 authenticated / 401 not - with no
      provider key present, which also proved a dead provider does not
      make the server look dead. Detail in `progress.md`.
- [x] **11.2 Survey the existing surface against the Phase 11 spec.**
      Most of §3-§9, §12, §15, §16, §18 was already delivered by
      Phases 9/10 and needs verifying, not rebuilding. Real gaps: two.
- [x] **11.3 Provider coverage - DONE (1752 passed, 1 skipped, 1
      deselected).** Six providers added on one shared urllib client:
      `brain/providers/http_chat.py` (keys, timeouts, error taxonomy, and
      the `split_prompt` call) -> `openai_compatible.py` (the OpenAI wire
      format) -> `openai.py`, `cerebras.py`, `xai.py`, `deepseek.py`,
      `qwen.py`. `anthropic.py` subclasses `http_chat` only: `x-api-key`,
      `anthropic-version`, top-level `system`, required `max_tokens`,
      content blocks. Registered via `PROVIDER_KEYS` +
      `HTTP_CHAT_PROVIDERS` and ONE generic `_instantiate_provider`
      branch; the five hand-written branches and the five working
      provider files are untouched. Cerebras is registered because the
      split now lives in the base class, so its AURA-P2-003 defect is
      structurally impossible - pinned by
      `assert CerebrasProvider.generate is HttpChatProvider.generate`.
      **Found and fixed a live bug on the way:**
      `_instantiate_provider` was an instance method that
      `server/settings_service.test_provider` had always called unbound,
      so `self` took the provider name, the TypeError was swallowed by
      `except Exception`, and EVERY `POST /api/providers/test` answered
      "not configured" regardless of provider or key. Now a
      `@staticmethod`, with typed error categories (invalid api key /
      quota exhausted / rate limited / unreachable / request failed)
      instead of "unreachable" for everything. Docs corrected:
      `.env.example`, `docs/DEPLOYMENT.md`, `docs/FOLDER_STRUCTURE.md`,
      `docs/IMPLEMENTATION_STATUS.md` all claimed OpenAI was unwired and
      `DEEPSEEK_API_KEY`/`CEREBRAS_API_KEY` inert.
- [x] **11.4 Android Hub redesign + visual identity - DONE (225 passed
      across 15 classes, 0 failures, 0 errors).** Compose only; no GSAP,
      no WebView. Two new theme files: `AuraMotion.kt` (three durations -
      Quick 140 / Standard 240 / Slow 420 - plus `scaled()`, which
      returns **0** rather than a halved duration under reduced motion,
      and `mayLoop()`, which lets a repeating animation run only while
      something is genuinely in flight, so the frame pipeline is not kept
      awake for a settled status) and `AuraSurfaces.kt` (gradient and
      glass tokens at 0.05-0.18 alpha, every one **derived from the
      active `colorScheme`** rather than a literal - the only way any of
      it survives dynamic colour on Android 12+). `HubScreen.kt` was
      rebuilt into `HeroCard` + `StatusRing` + `TileGrid` + `StatusTile`
      + `ChatCard` over the shared `SurfaceCard`, replacing the single
      status card and 13 flat rows.
      **The testability problem this phase actually solved:** the app's
      most visible sentence lived inside a `@Composable`, and this module
      has no JVM Compose harness and no Robolectric, so it was also its
      least testable one. The verdict logic is now pure Kotlin in
      `HubOverview.kt` (`hubHeadline`, `hubTiles`, `hubBanner`) and
      `ProviderSummary.kt` (`modelFact` / `endpointFact` /
      `keySourceFact` / `healthFact`), covered by `HubOverviewTest` (18),
      `ProviderSummaryTest` (16), `ModelSettingTest` (10) and
      `AuraMotionTest` (5). §16's regression - `/api/health` 200 +
      `/api/settings` 404 must read **Connected**, not Disconnected - is
      now an assertion, alongside a sweep over 8 reach states proving no
      headline ever says "unexpected response", a status code, or "null".
      An overridden endpoint is acknowledged (`Custom endpoint (via
      OPENAI_BASE_URL)`) and never printed, because some gateways carry a
      token in the base URL's query string.
      **Found and fixed a second fake control:** the `Model` row in
      `AuraSection` read `llm.model` directly, which is *Gemini's* field,
      so a phone whose primary was Claude displayed a Gemini model name.
      It now reads `state.activeModel` (the primary provider's own),
      matching the `model_setting` fix from 11.3. `ControlDto.kt`'s five
      new fields (`api_base`, `api_base_overridden`, `model`,
      `model_setting`, `api_key_env`) landed with it, and
      `ProviderComponents.kt` / `ModelsSection.kt` show the six new
      providers.
- [x] **11.5 Full suites, APK, state, commit.** Suites are green as of
      this entry - backend **1752 passed, 1 skipped, 1 deselected**,
      Android **225 passed**. The debug APK is built and fresh -
      `android/app/build/outputs/apk/debug/app-debug.apk`, 19,548,367
      bytes, with `:app:packageDebug` and `:app:assembleDebug` both
      executed rather than UP-TO-DATE - and the
      `docs/IMPLEMENTATION_STATUS.md` test counts are corrected. The
      `android/app/build` + `android/.gradle` untracking earlier notes
      listed here was NOT needed: `35589a0` already removed all 2139
      files and `git ls-files` returns zero under both paths. Committed
      as `95ab4f1 Harden settings, providers, Render startup, and Android
      UI` (44 files, 5798 insertions, 470 deletions) and pushed to
      `origin/feature/aura-identity`; tree clean, branch in sync.
      NOT done: no device was attached, so the APK was never installed or
      run; no live provider API was called; Render was not redeployed;
      `:app:lintDebug` was not re-run after the redesign.

## Phase 12 (COMPLETE, uncommitted) - Android Settings integration

Requirements 1-22 of the audit mandate. Every acceptance criterion A-N
met except that N (do not commit) is the state this stops in.

- [x] **12.1 Audit.** Traced Retrofit -> auth interceptor -> `AuraResult`
      -> repository -> `ControlDto` -> `HubViewModel` -> `HubOverview` /
      `ProviderSummary` -> the eleven sections -> error mapping. Six
      sites were re-deriving "does not expose settings" from
      `ServerState.loaded`. Cleared by evidence, each checked rather than
      assumed: the bearer token is attached, the base URL is normalised,
      `/api/settings` uses the *same* authenticated Retrofit client as
      `/api/health` (requirement 8), the DTOs parse the live payload, and
      no control is fake.
- [x] **12.2 Typed verdict.** `ui/hub/SettingsAccess.kt` (new) +
      `ServerState.settingsError` / `providersError`. Full status mapping
      in `progress.md`.
- [x] **12.3 Mapping repairs.** Empty 2xx body -> `Incompatible`, not
      `ServerFailure(200)`. `SerializationException` -> `Incompatible`,
      caught before the generic clause, message dropped. Provider-route
      failures recorded instead of swallowed. 403 split from 401.
- [x] **12.4 Contract, from the server's own bytes.**
      `android/app/src/test/resources/live/*.json` +
      `tests/test_settings_fixture.py`. Captured through the FastAPI
      `TestClient`, not from Render - see `progress.md` before quoting
      `configured` values from them.
- [x] **12.5 ViewModel-level regression tests.** `DeviceSettings.kt`
      (new) made `HubViewModel` constructible on the JVM without widening
      the read-only `SettingsProvider`; `HubViewModelTest` (18) drives
      four routes on loopback.
- [x] **12.6 Read-only audit (requirement 13).** All 32 literal settings
      paths used under `ui/` are among the server's 42 `configurable`
      paths. Every control that writes a server setting passes through
      `lockedReason`; `AuraSection` and `DiagnosticsSection` write nothing
      and state their reason from `settingsAccess`; `ConnectionSection` is
      device-local. `tools.allowed`, `tools.allowed_paths` and
      `tools.applications` are absent from the server's allow-list by
      design and render locked - a bearer token must not widen what the
      tools may reach.
- [x] **12.7 UI pass on the Phase 11 tokens.** `auraGlassEdge` on the
      three card types; `AuraMotion.scaled` + `rememberReducedMotion` in
      place of five literal durations. No new design system, no GSAP, no
      WebView, no dependency.
- [x] **12.8 Suites, APK, diff.** Backend 1756 / 1 skipped / 1
      deselected. Android 273 across 17 classes. APK 19,323,605 bytes,
      2026-08-12 13:27:08 +0700, built after a real `clean`.
      `git diff --check` clean; 19 modified files, 6 untracked paths.
- [ ] **12.9 Commit and push.** BLOCKED BY DESIGN: requirement 22 says
      report the diff and wait for approval.

## Standing constraints

- Do not redesign the architecture; do not rewrite working subsystems.
- Do not remove providers/files/features unless the audit calls them
  orphaned AND dependencies are verified first.
- Never claim an action succeeded unless the execution layer executed and
  verified it.
- Every code change has tests.
- Start with SAFE tools only; no arbitrary command execution.
- Stop and ask before any decision that materially changes architecture.

## Outstanding from Phase 2

Manual verification of `"mở youtube"` on a real device is NOT done - it
needs hardware this environment does not have. Everything reachable
without a device is tested (958 Python, 132 Android unit tests).

Android test command:
`cd android && ./gradlew :app:testDebugUnitTest --offline`

## Outstanding from Phase 3

The tool loop is proven against stubs and against the real `ToolExecutor`,
not against a live provider. Whether Gemini reliably emits the documented
`{"tool": ..., "arguments": {...}}` shape is unmeasured; `read_tool_call`
is deliberately lenient about fences and prose, but a provider that never
emits the shape at all would fail silently as "no tool call". Worth one
manual desktop run before Phase 6 depends on it.

`config.yaml` currently allows only `current_time`. Nothing user-visible
can be launched yet - `open_application` is registered only when
`applications` has entries AND the name is on the allow list, and neither
is true today. That is the intended Phase 3 end state, not an oversight.

## Outstanding from Phase 4

The boundary is now stated unconditionally in `prompts/system.md`, and
that is a prompt-level control: it makes a false device-success claim
contrary to instruction, not impossible. Whether a live provider obeys it
is unmeasured, like the rest of the tool loop. The *impossibility* comes
from the structural half - no `/device` route and a policy granting one
SAFE clock tool - which `tests/test_device_boundary.py` now pins.

## Outstanding from Phase 5

No live provider was called. Failover is proven against patched
`generate` methods and the real instantiation path, never against Gemini,
Groq, Mistral, OpenRouter or a running Ollama. Worth one manual run
alongside the Phase 3 tool-loop check.

Ollama is NOT in the shipped chain (`config.yaml` is
`gemini -> groq -> mistral -> openrouter`). It can now be added, but on
Render `OLLAMA_HOST` must point at a host the *server* can reach - which
is not the user's PC. Nothing in this phase installs Ollama into the
Docker image or gives the cloud access to the desktop.

`ollama_model: qwen3:8b` is the value the provider has always effectively
used. It has not been checked against what is actually pulled locally.
The other qwen values in the repo (`qwen3-coder:30b`, `qwen3.5-9b-local`,
`aura-qwen3-coder`, `ollama_chat/qwen3-coder:30b`) configure the external
coding agent, not Aura's runtime, and were deliberately left alone.

`brain/providers/cerebras.py` is kept and deliberately unregistered. Its
docstring lists what must be true before it is wired - chiefly that
`generate` must call `split_prompt` like its siblings, or the system slot
(which carries the Phase 4 device-action boundary) arrives as ordinary
conversational text. **SUPERSEDED IN PHASE 11.3:** it is registered now.
Not by correcting its `generate` - by deleting it. `split_prompt` moved
into `HttpChatProvider.generate`, which every new provider inherits, so
the defect cannot be reintroduced by a copy-paste; a test asserts the
method is not overridden.

## Outstanding from Phase 11

**Six of the ten cloud providers have never spoken to their vendor.**
OpenAI, Anthropic, Cerebras, xAI, DeepSeek and Qwen are registered and
buildable, and `tests/test_cloud_providers.py` pins the request bytes,
the reply parsing, the failure classification and the streaming for each.
No key for any of them exists in this deployment, so the request shapes
are the documented ones, not the confirmed ones, and the default model
names in `core/config.py` are current-as-of-writing rather than probed.
The escape hatch is deliberate: every model setting is free text, so a
renamed model is a settings change and not a code change. The settings
screen's Test button is the confirmation step, and it works now that
`_instantiate_provider` is callable unbound.

## Outstanding from Phase 6

No live deployment was exercised. The auth refusal, the CORS policy, the
error taxonomy and `/api/ready` are proven against the real ASGI app via
`TestClient`, never against Render. Two things worth one manual check on
the next deploy: that `AURA_SERVER_AUTH_TOKEN` is actually set in the
Render dashboard (without it the service now *fails to start* instead of
serving an open LLM - a louder failure, but a failure), and that the
Render health check path is `/api/ready` rather than `/`.

Render persistence was investigated rather than built (STEP 7). The
configuration-level fix already exists and is free: a 1 GB disk named
`aura-data` mounted at `/app/data`, documented in `docs/DEPLOYMENT.md`
§2, which is where `memory/sqlite.py` writes `memory.db`. Without it
every deploy, restart, crash and idle spin-down starts from an empty
database and nothing warns you. No persistence infrastructure was
invented and no paid feature was assumed.

`/api/ready` is public by design - a container healthcheck and Render's
probe cannot carry a bearer token. It returns a boolean plus failure
category strings: no configuration, no versions, no secrets. It does not
call the provider, so polling it costs nothing and one provider outage
cannot become a restart loop.

## Outstanding from Phase 8

**Proactive delivery is pull-driven, and that is a real limitation, not
a detail.** `ProactiveEngine.tick()` runs when a client polls
`GET /api/notifications`; a decision to speak is published to the
existing `NotificationOutbox` and leaves on that same poll. Nothing
schedules a tick on its own, so a phone that is not polling gets
nothing, and a message that would have gone to a sleeping device is not
sent late - it is not sent. A background scheduler would need
deployment infrastructure this repo does not have (no worker process, no
task queue, and Render free-tier services spin down when idle); it was
documented in `config.yaml` and `docs/API.md` rather than invented.

**No live provider, no real device, no deploy was exercised.** Every
Phase 8 claim is proven against in-memory SQLite, injected clocks and
the real ASGI app via `TestClient`. Whether a live model actually uses
the TIME section or the recalled MEMORY lines well is unmeasured. Push
notifications on Android were not built and not tested.

**The seeded user model is a starting point, not observed truth.** The
46 seeded rows come from the profile the user supplied; each carries
`source="seed"` and a confidence below 1.0 where the spec called it an
inference. Nothing in the pipeline promotes an inference to a confirmed
fact - only an explicit user statement does that.

**Temporary context is not swept on a timer.** It expires by
`valid_until` on read, so an unread expired row stays in the table until
something reads it. Bounded (the store caps its own size) but not
tidy; a periodic sweep needs the same missing scheduler as above.

## Phase 9 (COMPLETE) - Android Control Hub, provider/key management

The user's Phase 9 spec is NOT the device agent. It is: modernize the
Android UI, add a Settings/Control Hub, add API-key + provider/model
management callable from the phone, and add real feature toggles. The
device agent moves to Phase 10+ and its notes below stand unchanged.

Verified baseline before any change: **1480 passed, 1 deselected** in
7.71s. (The 1441 recorded for Phase 8 was stale.)

### Phase A audit - COMPLETE, findings that constrain the build

- **One Android preference store exists**: `SettingsStore` over
  EncryptedSharedPreferences (`aura_secure_settings`), exposed read-only
  as `SettingsProvider`. Extend `AuraSettings`; do NOT add a second store.
- **One config system exists**: `DEFAULT_CONFIG` + `deep_merge` +
  `load_config()`. A runtime override layer must merge INTO it, not
  replace it.
- **Provider keys are read via `os.getenv` inside each provider's
  `__init__`** (gemini, groq, mistral, openrouter, cerebras), and each
  raises `ValueError` when the key is absent. `BrainRouter._skip_reason`
  also probes `os.getenv`. So the smallest correct way to make an
  Android-set key effective is a credential store that applies keys to
  `os.environ` - zero provider edits, and `_skip_reason` stays honest.
- **`conversation.llm` IS a `BrainRouter`** (`launcher/services.py:200`,
  `brain/chat_engine.py:79`). It caches `_provider` lazily, so a live
  provider switch = clear `_provider` + set `provider_name`.
- **groq/mistral/openrouter/cerebras are four near-identical
  OpenAI-compatible urllib clients.** Only mistral has `stream()`. This
  is the reusable compatibility layer STEP 4 asks for.
- **`cryptography` 50.0.0 is already importable** in `.venv` (transitive
  via google-genai). Fernet is available; it must be declared explicitly
  if depended on.
- **`data/*.db` is gitignored but arbitrary `data/` files are not.** A
  credential file needs its own ignore rule.
- **STEP 22 "Failed to parse action from server" is NOT an open defect.**
  Verified against code, not just the docstring: `AgentActionParser`
  brace-matches, strips fences, tolerates unknown keys/lenient JSON, and
  returns a model-readable `Failure` otherwise; `tests/test_agent_protocol.py`
  (588 lines) pins the transport byte-for-byte on `response.content`.
  Root cause was an installed APK older than the server.
- **No Android voice code exists at all** - zero hits for
  `TextToSpeech|SpeechRecognizer`. Backend `voice/` runs on the SERVER
  and ships disabled. Voice settings must say so rather than offer
  phone-side controls that do nothing.
- **Proactive delivery is pull-driven** (`GET /api/notifications` is the
  only trigger). No background scheduler, no FCM.

### Phase B/C/D backend - COMPLETE (1535 passed, 1 deselected)

Delivered, all authenticated with the existing `verify_token` bearer
dependency - no new auth mechanism and no public write route:

| Route | Purpose |
|---|---|
| `GET /api/settings` | effective config + overrides + `configurable` allow-list + provider persistence note |
| `PATCH /api/settings` | validate + apply; 422 verbatim message on bad input; reports `applied` / `restart_required` |
| `POST /api/settings/reset` | drop all overrides or named `paths`; never touches keys |
| `GET /api/providers` | per-provider `configured` / `key_masked` / `source` / capabilities |
| `GET /api/providers/health` | active chain, `in_fallback`; calls no provider |
| `POST /api/providers/test` | real single probe; returns latency + error *category* only |
| `PUT /api/providers/{p}/key` | store a key; returns masked only |
| `DELETE /api/providers/{p}/key` | forget a key, and unset it for this process |

New files: `core/credentials.py`, `core/settings_store.py`,
`server/settings_service.py`, `server/routes/settings.py`,
`tests/test_settings_api.py`.
Modified: `core/config.py` (`apply_overlay` at the single merge point),
`server/runtime.py` (bootstrap stores, `settings_store` /
`settings_service` properties), `server/main.py` (router),
`.gitignore` (credential + overlay files), `tests/conftest.py`
(per-test settings/credential isolation + PROVIDER_KEYS env restore).

**Contract notes the Android client must respect:**

- A masked value (`••••••••ABCD`) is never accepted as a key - posting
  back what was displayed returns 422. Leave the field untouched to keep
  the current key.
- `key_masked` is `""` when nothing is stored. `source` is `"store"`,
  `"environment"` or `""`; a key from the deployment's environment cannot
  be deleted from the phone, and the UI must say so rather than offering
  a delete that appears to do nothing.
- `PATCH` is all-or-nothing. On 422 nothing changed.
- `needs_restart` in the response is the honest signal; a path listed in
  `restart_required` was persisted but is NOT live yet.
- `PROVIDER_CAPABILITIES` is per-implementation, not per-vendor: Groq is
  `streaming: false` here because `GroqProvider` has no `stream()`. The
  UI must render this rather than assume vendor docs.

### Live-vs-restart, decided from the code

Applies live: API keys + `llm.provider` + fallback chain + model (router
reset), all `proactive.*` (read from `policy.settings` at decision time),
`memory.recall` (`pipeline.recall_enabled`).
Needs restart: anything built once in `build_services` - `vision.enabled`
when vision was never built, `tools.enabled`, `voice.tts/stt.enabled`,
`server.screen.enabled`. The API reports `restart_required` for these
instead of pretending.

### Phase E-L Android - COMPLETE (132 Android tests, lint 0 errors)

Eleven hub files under `ui/hub/` and three component files under
`ui/components/` (4772 lines total). `MainActivity` navigates
chat -> hub -> ten sections; one Activity-scoped `HubViewModel` is shared
by every destination, so the server's config is fetched once on entry
rather than per screen.

New: `ControlDto.kt`, `SettingsComponents.kt`, `ProviderComponents.kt`,
`InputComponents.kt`, and `ui/hub/{AuraSection, ModelsSection,
AwarenessSection, MemorySection, ProactiveSection, VisionSection,
VoiceSection, NotificationsSection, GeneralSection, ConnectionSection,
HubScreen, HubViewModel, DevicePermissions}.kt`.
Modified: `MainActivity.kt`, `AuraRepository.kt`, `AuraResult.kt`,
`AuraApi.kt`, `SettingsStore.kt`, `Theme.kt`, `NotificationWorker.kt`.

**Every toggle is wired to something real.** The notifications switch
calls `NotificationScheduler.sync` as well as writing the flag, so "off"
means off now rather than at the next launch. Dynamic colour locks below
Android 12 with a stated reason instead of failing silently.

*Superseded by Phase 10:* this phase left `server.screen.min_interval`
read-only and voice at two toggles, because neither was in the
validator's allow-list. Phase 10 added eight paths to that allow-list -
`server.screen.min_interval`, `tools.enabled/auto_approve/timeout`,
`voice.tts.provider/voice/volume/playback` - so both statements are now
false. See the Phase 10 section of `progress.md`.

**`ui/settings/SettingsScreen.kt` is now unreachable** - the chat gear
opens the hub, and `ConnectionSection` reuses the same
`SettingsViewModel` rather than becoming a second connection store. The
file was left on disk (rule 16: do not delete working functionality to
simplify), and this is the record of that decision, not an oversight.

**Phase J/K additions to `tests/test_settings_api.py` (70 tests total):**
every `/api/settings*` and `/api/providers*` route enumerated from the
ASGI app and asserted to refuse an unauthenticated call (so a route added
without `Depends(verify_token)` fails on the day it is written); no
allow-list path is credential material, matched on the last dotted
segment rather than as a substring - `llm.max_output_tokens` is not a
token; `PATCH` with an `llm.api_key` is refused 422 and changes nothing;
and no route logs the key, covering the rejection paths where a
`logger.warning("bad key: %s", ...)` would sit.

## When the device agent starts (Option B - local Windows agent)

Phase 4 did not build any of this, deliberately. Still to settle:
transport (long-poll `/api/device/commands` recommended - mirrors the
existing inbound screen/notifications pattern, works through NAT), the
agent's own local allowlist (the agent must refuse anything outside it,
so a compromised server is not arbitrary code execution on the PC), and
the initial tool surface (`open_url` does not exist yet).

Two things the device-agent phase must not break.
`tests/test_device_boundary.py` asserts no route matching
device/command/exec/shell exists - that test is *expected* to be updated
when the agent lands, and updating it should be a deliberate, reviewed act
rather than a reflex. And the `# Actions` section in `prompts/system.md`
says a tool has run only once its result is under TOOL RESULTS; a device
tool that returns before the PC confirms execution would make that
sentence false and reintroduce AURA-P0-005 behind a timeout.

## Outstanding from Phase 7

**Resolved during the phase.** The session-leak and single-tenant items
were the two open Phase 5 entries and are now closed, with tests:
`SessionManager.cleanup_old()` had zero callers and client-supplied ids
grew the dict for the process lifetime; a throttled sweep on both create
paths fixes it (unlocked shared `_expire` - the lock is not reentrant).
Single-tenant is now stated in `server/runtime.py` (the auth token is
the identity boundary, `session_id` scopes only the metadata endpoint)
and pinned by `test_sessions_share_one_memory_store`, so partitioning
cannot land silently. Both live in `tests/test_server.py` (49 passed)
and raised the suite to 1157. A duplicate-systems sweep then found the
last one: `StreamingLLM` was defined twice, in `brain/streaming.py` and
`brain/ports.py`, with *different* required members while the ports
docstring called itself a re-export. `ports.py` now imports it (no cycle
- `brain/streaming.py` imports nothing from `brain`), and three tests in
`test_pipeline.py` pin one-protocol-not-two. Suite 1160.

`vision.model` has two consumers that want different naming schemes, and
only one of them is right at a time. Documented at both ends in Phase 7,
NOT fixed then - splitting the key is a config change with a migration
question attached, and it was reported rather than done unilaterally.
**FIXED IN PHASE 8.** `vision/settings.py` resolves `cloud_model` and
`ollama_model` separately, both falling back to the legacy `vision.model`
so an old config file is unchanged in behaviour; `config.yaml` now names
both. 19 tests in `tests/test_vision_settings.py`.

`brain/prompt.py` is a working, superseded `Prompt` dataclass with zero
importers and zero references by name (`PromptBuilder` is the real
system). Left in place: the mandate was to prove absence of callers
before deleting, which is done, but deleting working code on that basis
alone is a judgement call rather than evidence, and nobody asked for it.

`brain/llm.py` is a documented back-compat shim re-exporting
`brain.ports.LLM`, also with zero importers. Same reasoning, and its
docstring already says what it is.

