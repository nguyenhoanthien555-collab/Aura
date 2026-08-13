# Aura Project State

## Project
Aura AI assistant.

## Current Goal
Build and stabilize Aura as a local/cloud-capable AI assistant.

## Status
The **persona contract wiring** is the newest uncommitted work: the fully
autonomous personality-overhaul brief's core engine (`brain/persona.py` -
pronoun registers, context modes, dials, addressing preferences) was dead
code and is now wired through `PromptBuilder` -> `ConversationManager` ->
`ChatEngine` -> config, emitted as a per-turn PERSONA section in the
system slot of every provider. Provider fallback preserves it by
construction (same transcript, same resolution; no model branches). Backend
**1811 passed, 1 skipped, 1 deselected**; Android `SettingsContractTest`
42/42 against the regenerated settings fixture (no Kotlin change needed -
the DTOs drop `personality`). See current-task.md for details.

The **Vision production-wiring fix** is complete and **uncommitted** (the
user said do not commit, do not push). It sits on top of two other
uncommitted work items in the same tree - the Gemini thinking-budget
change (`brain/providers/gemini.py`, `config.yaml`, `core/config.py`,
`server/routes/chat.py`, `tests/test_server.py`,
`tests/test_gemini_thinking_budget.py`, `live/settings.json`) and these
`.claude/*.md` files. Its own files are: 4 new
(`screen/ScreenshotCapture.kt`, `screen/ScreenshotUploader.kt`,
`screen/ScreenshotUploaderTest.kt`, `screen/ScreenshotWiringTest.kt`) and
6 modified (`AuraAccessibilityService.kt`,
`ScreenObservationService.kt`, both accessibility service XMLs,
`server/routes/screen.py`, `tests/test_cloud_failover.py`). Both suites
green: backend **1766 passed, 1 skipped, 1 deselected**; Android **292
passed across 19 classes, 0 failures, 0 errors, 0 skipped**. No device was
attached, so nothing was captured or uploaded from real hardware.

Phases 0-7 of the repair mandate are complete, PHASE 8 (Memory 2.0 +
Temporal Context + User Model + Proactive System) is complete, PHASE 9
(Android Control Hub, modern UI, provider/API-key management, feature
controls) is complete, and PHASE 10 (Android <-> server settings
contract) is complete. The "local Windows device agent" that earlier
notes deferred is still deferred - it was not part of the Phase 8, 9, 10,
11 or 12 spec.

PHASE 12 (Android settings integration audit) is complete but
**uncommitted** - requirement 22 of the mandate says report the diff and
wait for approval. 19 code/test files modified, +868/-120 (plus these
four `.claude/*.md` state files), and six untracked paths (`ui/hub/SettingsAccess.kt`, `data/settings/DeviceSettings.kt`,
`ui/hub/HubViewModelTest.kt`, `ui/hub/SettingsAccessTest.kt`,
`android/app/src/test/resources/`, `tests/test_settings_fixture.py`).
Both suites green: backend **1756 passed, 1 skipped, 1 deselected**;
Android **273 passed across 17 classes, 0 failures, 0 errors**. Debug APK
rebuilt from clean: 19,323,605 bytes, 2026-08-12 13:27:08 +0700. No
backend behaviour changed, no dependency added, no test weakened (three
assertions were strengthened). Still NOT verified: no device attached, so
the APK was never installed or run; no live provider API called; the
Render host was not re-probed from the phone; `:app:lintDebug` not re-run.

PHASE 11 is complete and committed as `95ab4f1 Harden settings,
providers, Render startup, and Android UI` (44 files, 5798 insertions,
470 deletions), pushed to `origin/feature/aura-identity` with a clean
tree. Both suites are green: backend **1752 passed, 1 skipped, 1
deselected**; Android **225 passed across 15 classes, 0 failures, 0
errors** (the Phase 10 note here read "175 across 11 classes", which was
true when written and is now four test classes behind). Delivered: the
Render startup crash fixed and verified on a real 3.14.6 interpreter,
provider coverage reaching all ten providers the spec names, the Android
Hub redesign, and the debug APK
(`android/app/build/outputs/apk/debug/app-debug.apk`, 19,548,367 bytes).
The build-artifact untracking that earlier notes listed as remaining was
already done by `35589a0`. NOT verified: no device was attached, so the
APK was never installed or run; no live provider API was called; Render
was not redeployed; `:app:lintDebug` was not re-run after the redesign.

**Phases 9, 10, 11 ARE committed.** HEAD is `07e3cda Record Phase 11
completion in project state` on `feature/aura-identity`, pushed. Phase 12
sits on top of it, unstaged. The *redeploy* that earlier notes listed as
outstanding **has happened**: the user verified against the live Render
host that authenticated `GET /api/health`, `GET /api/settings`,
`GET /api/providers` and `GET /api/providers/health` all return 200, and
that `/api/settings` returns a valid
`effective`/`overrides`/`providers`/`configurable` payload. The "404
there" note above was true for the older revision and is no longer the
live contract - which is precisely what made Phase 12 necessary, because
the phone still said "This Aura server does not expose settings".

## Phase 12 architecture (standing)
- **The settings verdict is a typed error, not a boolean.**
  `ServerState.settingsError: AuraError?` carries *why* the settings read
  failed, and `settingsAccess(loaded, connected, error)`
  (`ui/hub/SettingsAccess.kt`) is the only place that turns it into words.
  A boolean plus free text is what produced the bug: six sites
  independently re-derived "this server does not expose settings" from
  `loaded == false`, so an auth failure, a cold start, a 500 and a
  malformed body all rendered as 404. Every screen now reads
  `SettingsAccess` (13 members, each with `label`/`reason`/`headline`/
  `tone`/`retryable`/`usable`) instead of re-deciding.
- **A body that will not parse is an incompatibility, never a 404.**
  `AuraRepository.call()` maps 401/403/422/404-405/429/503/502-504 each to
  its own `AuraError`, an empty 2xx body to `Incompatible("empty body")`,
  and catches `SerializationException` *before* the generic clause -
  dropping its message, which quotes the offending JSON.
- **The contract is tested against the server's own bytes.**
  `android/app/src/test/resources/` holds the current server build's route
  output captured through `tests/test_settings_api.py`'s FastAPI
  `TestClient` (not a network capture), and `SettingsContractTest` parses
  it. `tests/test_settings_fixture.py` keeps the capture honest on the
  backend side.
- **Provider -> model setting is server-authoritative.**
  `PROVIDER_CAPABILITIES[name]["model_setting"]` ->
  `ProviderDto.modelSetting` -> `modelSettingOr("llm.model")`. The phone
  never guesses which key a provider's model lives under, so choosing a
  model for Anthropic cannot write Gemini's `llm.model`.
- **`DeviceSettings` is the phone-local seam.** `HubViewModel` depends on
  that interface rather than the concrete encrypted store, which is what
  makes the hub testable on the plain JVM (no Robolectric in this project)
  and what keeps device toggles provably off the wire.
- **Anything asserted must be pure Kotlin.** Unit tests are plain JVM;
  `androidx.compose.ui.test.junit4` is `androidTestImplementation` only.
  That is why the verdict, the overview mapping, the provider summary and
  the motion tokens live in Compose-free files.

## Phase 10 architecture (standing)
- **Connectivity is a ladder, not a boolean.** `ServerReach`
  (`ui/hub/HubViewModel.kt`): `Unknown < Unreachable < Connected <
  Authenticated < SettingsAvailable < ProviderHealthy`, compared by
  ordinal through `atLeast`. Each rung is one observed request.
  `connected` is anchored to `Authenticated` = a 200 from `/api/health`,
  which is itself behind `verify_token`, so one request proves
  reachability and the token together. An optional route returning 404
  must never make a working server read as dead.
- **A report is not a snapshot.** `ServerRuntime.config` is still built
  once - `build_services` hands that dict to every subsystem - but
  `SettingsService.refresh_config()` re-merges it after every overlay
  write, because the same dict is what `GET /api/settings`,
  `GET /api/providers` and `/api/health` report. Live application is
  unaffected: every `_reapply_*` handler reads `load_config()` fresh.
- **`/api/health` must not build anything.** `_provider_chain_label()`
  guards the lazy provider construction that `active_chain()` triggers,
  and reports the exception *type* only. The route that means "Aura is
  alive" cannot be allowed to fail because one subsystem is unwell -
  least of all the provider, whose repair is what the user came for.
- **Subsystem-conditional settings demote themselves.** Three handlers
  (`_reapply_screen`, `_reapply_tools`, `_reapply_voice`) return whether
  the change reached a live object; a `False` moves the path from
  `applied` to `restart_required`. Derived from the assignment, never
  from a table.
- **Precedence, in two directions.** Settings:
  `DEFAULT_CONFIG < config.yaml < runtime overlay`, and `load_config()`
  reads no environment variable. Secrets: `.env < credential store`,
  since `CredentialStore.apply()` overwrites the environment at startup
  and after each write. Documented in `docs/API.md` (Precedence) and
  `docs/SECURITY.md`.
- **A settable path is not a new capability.** `tools.allowed`,
  `tools.allowed_paths` and `tools.applications` stay off the allow-list
  on purpose: a bearer token may change a setting, not grant a remote
  client a new verb on the host.

## Phase 9 architecture (standing)
- **Android is a control surface, not a source of truth.** Server state
  lives on the server and is read through `GET /api/settings`; the phone
  stores only device-local values (server URL, token, theme, dynamic
  colour, notifications, device id) in the one existing
  EncryptedSharedPreferences store.
- **One settable surface**: the dotted-path allow-list in
  `core/settings_store.py`. Anything absent from it 422s, and the Android
  UI renders such values read-only with the reason rather than offering a
  control that cannot work.
- **API keys enter through exactly one route** -
  `PUT /api/providers/{provider}/key`, bearer-authenticated - are stored
  Fernet-encrypted, applied to `os.environ` so no provider needed
  editing, and are only ever read back masked. No allow-list path is
  credential material, and a test enumerates the routes to keep it that
  way.
- **`PROVIDER_CAPABILITIES` is per-implementation, not per-vendor.** Groq
  is `streaming: false` because `GroqProvider` has no `stream()`. The UI
  renders these flags rather than vendor documentation.
- **Restart honesty**: `restart_required` names paths that were persisted
  but are not live, because they are built once in `build_services`.

## Phase 8 architecture (standing)
- **Temporal context** (`core/temporal.py`, `brain/prompt_builder.py`
  TIME section): one `TemporalClock` per process, injected everywhere,
  no hardcoded dates, no stray `datetime.now()` outside
  `core/temporal.py` and `memory/models.py`'s column default.
- **Memory 2.0** (`memory/pipeline.py`): one `MemoryPipeline` over the
  same SQLite session as the transcript. Episodic memories, temporary
  context and the user model are separate stores; temporary context
  never auto-promotes to episodic. Machine-turn isolation (Phase 7)
  holds: agent ticks and intent probes reach neither store.
- **Relevance recall** (`memory/retrieval.py` `RankedRetriever`): lexical
  scoring, bounded by `memory.retrieval_scope` (500) and by
  `memory_lines` caps (6 user-model + 3 episodic + 3 temporary). The
  prompt never sees the whole database.
- **User model** (`memory/user_model.py` + `memory/user_profile_seed.py`):
  confirmed/inferred/unknown with confidence, never auto-promoted;
  explicit corrections are persisted; the initial profile is seeded once
  and is idempotent.
- **Proactive system** (`proactive/`): scheduler tick + decision engine +
  anti-spam gates (global + category cooldowns, quiet hours, daily max,
  duplicate/similarity suppression), all off by default. Pending-task
  reminders read the pipeline's episodic store only - tasks are never
  invented. Delivery is pull-driven via the existing NotificationOutbox
  + `GET /api/notifications`; there is no background worker, and that
  limitation is documented rather than hidden.
- **Test isolation** (`tests/conftest.py`): session-wide autouse fixture
  redirects `memory.sqlite.engine` + `SessionLocal` to a StaticPool
  in-memory database, so no test can write the user's `data/memory.db`.
  Real DB verified: `['messages','user_facts']`, 76 messages, 0 rows.

## Deployment invariants (Phase 6)
- The server REFUSES TO START without `AURA_SERVER_AUTH_TOKEN`, unless
  `AURA_ALLOW_INSECURE` is explicitly `1`/`true`/`yes`. Enforced in the
  ASGI lifespan outside the `is_initialized` guard, and in
  `launcher.py --server`.
- Wildcard CORS origins never carry credentials (`server/config.py`
  `cors_policy`). The exposure was preflight origin reflection.
- Failures are classified by `server/errors.py` over the existing typed
  provider errors: 429 / 503 / 500. No second error hierarchy, and no
  exception text reaches a client.
- Liveness (`/`, `/api/health`) and readiness (`/api/ready`, public,
  503 when not ready) are separate questions. Readiness never calls the
  provider and reports nothing about a physical device.

## Device boundary (standing)
Render CANNOT execute physical PC actions. There is no device route, no
device transport and no executor that reaches a machine - the only
runnable tool reads a clock. This is structural, not a policy toggle.
Pinned by `tests/test_device_boundary.py` and re-pinned by
`tests/test_security_hardening.py`.

## Repository invariants (Phase 7)
- Build artifacts are NOT tracked. Phase 7 untracked ~2400 Gradle/dex/
  class files via `git rm -r --cached`; commits 4ba906e and 1fe3368 then
  re-added ~2100 of them, because `.gitignore` does not apply to paths
  already in the index; `35589a0` removed them again (2139 files under
  those two directories, 61183 deletions, no insertions). At HEAD both
  `git ls-files android/app/build` and `git ls-files android/.gradle`
  return nothing, and `.gitignore:37-38` covers both. Earlier notes in
  this file and in `docs/IMPLEMENTATION_STATUS.md` prescribed a `git rm
  -r --cached` commit; that work is done and must not be repeated. The
  hazard is structural rather than fixed - ignoring a path does not
  untrack it, so a future `git add -A` over an uncleaned build can re-add
  them. `android/gradle/wrapper/` STAYS tracked - the wrapper jar is
  source and a checkout cannot build without it.
- CI is `.github/workflows/tests.yml`: `pytest -q` on Python 3.11. It
  restates nothing from `pytest.ini` so it cannot drift, and references
  no secrets - the hermetic suite must pass with no API keys at all.
- `scripts/manual_*.py` are side-effecting utilities, deliberately
  outside `tests/`. None defines a `test_` function; pytest never
  collected them.
- Providers reachable from `_create_provider`: mock, ollama, gemini,
  groq, mistral, openrouter, and - added in Phase 11 on the shared
  `brain/providers/http_chat.py` client - openai, anthropic, cerebras,
  xai, deepseek, qwen. `PROVIDER_KEYS` is the registry; the six new ones
  are also rows in `HTTP_CHAT_PROVIDERS`, which is the only place naming
  their modules. None of the six has been called against its live API
  from this deployment - there is no key for any of them here.
- `split_prompt` lives in `HttpChatProvider.generate`, not in each new
  provider. That is what made Cerebras registrable (AURA-P2-003 was a
  copy whose `generate` skipped the split), and a test asserts the method
  is never overridden.
- Memory is SQLite plus lexical keyword recall. There is NO vector
  store, NO embedding model and NO semantic search anywhere in this
  codebase; any claim otherwise is wrong.
- Vision config split (Phase 8): `vision.cloud_model` (server, Gemini)
  and `vision.ollama_model` (desktop, Ollama tag) are separate keys
  resolved by `vision/settings.py`; legacy `vision.model` still works.
  `pytest.ini` pins `asyncio_default_fixture_loop_scope = function`.

## Architecture Rule
Preserve the existing architecture unless a change is clearly necessary.

## Pre-test sweep findings (standing, post-Phase-12)
**A blocking call in an `async def` route freezes the whole server.**
`POST /api/chat` awaited `runtime.chat()` inline, and every step of that
pipeline is synchronous - including a model call bounded only by
`llm.timeout: 120`. FastAPI runs `async def` handlers on the one event
loop, so a turn in flight served nothing else: not `/api/health`, not
`/api/notifications`, and not the phone's next agent tick. Now
`await run_in_threadpool(runtime.chat, ...)`; `ws_chat.py` had always done
the equivalent through `iterate_in_threadpool`, which is what made this an
oversight rather than a decision. Pinned by
`tests/test_server.py::test_chat_does_not_run_on_the_event_loop`, which
asserts from inside the call that no loop is running on that thread.
Accepted consequence: concurrent `/api/chat` calls now genuinely overlap.
Single-tenant by design, and `memory/sqlite.py`'s `db_lock` serialises the
database.

**Vision is wired end-to-end as of the Vision production-wiring fix
(uncommitted).** `server.screen.enabled: true` builds the remote source,
the cloud processor and both routes; on the phone
`screen/ScreenshotCapture.kt` wraps `AccessibilityService.takeScreenshot`
(API 30+) behind a `ScreenshotCapture` interface, and
`screen/ScreenshotUploader.kt` is the single gate both accessibility
services call. `ScreenObservationService` uploads pixels *after* its text
POST, and `AuraAccessibilityService` derives
`screenshotAvailable = outcome is ScreenshotOutcome.Sent` instead of the
old hardcoded `false`. Both service XMLs now declare
`android:canTakeScreenshot="true"`, which the framework requires.

Order is a server constraint, not a preference: `RemoteScreenSource` is a
single last-write-wins slot, and `POST /api/screen/upload` submits a
frame-only observation - text first then pixels, or the frame is replaced
by a frameless one and `CloudVisionProcessor.describe()` returns `""`.

Gates, in order, all inside `ScreenshotUploader`:
`screenObservationEnabled` → `uploadScreenshots` → `isConfigured` →
`capture.isSupported` (API < 30 cannot capture at all) → an 8 s interval
stamped on every *attempt*, matched to `server.screen.min_interval` so no
frame is sent faster than the server will look at one. Phone-side
downscale mirrors `vision.max_pixels = 1_500_000`. Failures are returned
as `ScreenshotOutcome.Failed` and logged by both callers, never swallowed.

Still not verified on hardware: no device was attached, so nothing was
captured or uploaded from a real phone. API 26-29 genuinely cannot capture
and report unavailable. `screenshot_available` is still consumed nowhere
in Python. `VisionManager`'s 8 s throttle means an uploaded frame is
usually described on the *next* turn, not the one that sent it.

**`llm.timeout` is not honoured by the primary provider.** groq, mistral,
openrouter and every `HttpChatProvider` receive it; `GeminiProvider`
constructs `genai.Client(api_key=...)` with no `http_options`, so a stalled
Gemini request has whatever bound the SDK defaults to and there is no
server-side deadline on `/api/chat`. Not changed - the SDK's own default
was not verified from here.

**`is_account_limit` is treated as global, not per-account.** A 429 whose
body says "daily", "rpd", "account" or "slow down" stops failover for the
whole chain (`FallbackProvider`, `ACCOUNT_LIMIT`), so Groq at position 2
exhausting its free-tier daily quota prevents mistral and openrouter - two
unrelated accounts - from ever being tried. Gemini's own quota does *not*
set the flag, so the primary still fails over. Deliberate and pinned
(`tests/test_cloud_failover.py:370` asserts "Please slow down." is an
account limit), so changing it is a product decision, not a bug fix.

**Streaming exists only when failover does not.** `stream_of` looks for a
`stream` attribute; `FallbackProvider` has none, so whenever two or more
providers initialise, every "stream" is one chunk from `generate()`. With
only Gemini's key present the primary is returned bare and true streaming
happens. So the same build streams or does not depending on which API keys
exist - and on the non-streaming path `max_output_tokens` applies, which is
why the thinking-budget fix covers both.

## Thinking budget (standing, post-Phase-12)
**`llm.max_output_tokens` is the length of the reply, and a thinking
model has to be told that.** Gemini 3 bills hidden reasoning against the
same budget, so with `thinking_level` unset the shipped 768 tokens went
~700 to thoughts and ~60 to the answer, and every reply arrived cut off.
`llm.thinking_level` (default `low`) is what keeps that number meaning
what the rest of the config assumes. Raising it to `high` requires
raising `max_output_tokens` with it.

**A truncated reply is a failure and must look like one.**
`GeminiProvider._check_truncation` reads `finish_reason` rather than
guessing from length: MAX_TOKENS with text logs a warning naming the
budget, MAX_TOKENS with no text raises `ProviderUnavailableError` so
`FallbackProvider` gets its turn. `response.text or ""` alone made an
empty completion indistinguishable from a successful one - it was saved
to the transcript, published to the UI, and never failed over. Only
MAX_TOKENS is treated this way; a blocked STOP still normalises to `""`,
or a safety block would be re-asked of the next provider.

**Phase 12 IS committed** as `5ca791b Complete Phase 12 Android settings
integration`. The "uncommitted, held for approval" note below was true
when written.

## Coding Rules
- Reuse existing systems.
- Do not invent APIs or files.
- Avoid unnecessary rewrites.
- Verify before modifying.
- Test after changes.
- Inspect git diff after implementation.

## External Coding Agent
Local Qwen3-Coder 30B via Ollama (development, coding, testing, and debugging ONLY; does NOT replace Aura's runtime LLM).

## Important
When context is compacted, use this file and the other `.claude/*.md` state files instead of reconstructing the conversation.
