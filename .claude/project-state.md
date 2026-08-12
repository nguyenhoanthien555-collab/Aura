# Aura Project State

## Project
Aura AI assistant.

## Current Goal
Build and stabilize Aura as a local/cloud-capable AI assistant.

## Status
Phases 0-7 of the repair mandate are complete, PHASE 8 (Memory 2.0 +
Temporal Context + User Model + Proactive System) is complete, PHASE 9
(Android Control Hub, modern UI, provider/API-key management, feature
controls) is complete, and PHASE 10 (Android <-> server settings
contract) is complete. The "local Windows device agent" that earlier
notes deferred is still deferred - it was not part of the Phase 8, 9, 10
or 11 spec.

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

**Phases 9, 10 and 11 ARE committed.** HEAD is `95ab4f1 Harden settings,
providers, Render startup, and Android UI` on `feature/aura-identity`,
pushed, with a clean tree. What remains outstanding is the user's
*redeploy*: the deployed Render revision predates the settings routes,
which is why `/api/settings`, `/api/providers` and
`/api/providers/health` return 404 there, and it also predates the
SQLAlchemy pin the service needs to boot on Python 3.14 at all.

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
