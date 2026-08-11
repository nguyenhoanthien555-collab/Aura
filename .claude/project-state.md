# Aura Project State

## Project
Aura AI assistant.

## Current Goal
Build and stabilize Aura as a local/cloud-capable AI assistant.

## Status
Phases 0-7 of the repair mandate are complete (1160 passed, 1 deselected),
and PHASE 8 (Memory 2.0 + Temporal Context + User Model + Proactive
System) is complete: 1441 passed, 1 deselected (0 failed, 0 errors).
Phase 8 was an explicit spec task (the "local Windows device agent"
that earlier notes deferred is still deferred - it was not part of the
Phase 8 spec).

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
- Build artifacts are not tracked. ~2400 Gradle/dex/class files were
  untracked with `git rm -r --cached` and ignored; tracked files went
  from ~2400 to ~250. `android/gradle/wrapper/` STAYS tracked - the
  wrapper jar is source and a checkout cannot build without it.
- CI is `.github/workflows/tests.yml`: `pytest -q` on Python 3.11. It
  restates nothing from `pytest.ini` so it cannot drift, and references
  no secrets - the hermetic suite must pass with no API keys at all.
- `scripts/manual_*.py` are side-effecting utilities, deliberately
  outside `tests/`. None defines a `test_` function; pytest never
  collected them.
- Providers reachable from `_create_provider`: mock, gemini, groq,
  mistral, openrouter, ollama. Cerebras is written and deliberately
  unregistered. There is no OpenAI branch and no DeepSeek provider.
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
