# Architecture Decisions

Record important technical decisions here.

Format:

## YYYY-MM-DD — Decision
Decision:
Reason:
Affected files:
Do not change unless:

## 2026-08-10 — Desktop actions reach the PC via a local agent (Option B)

Decision: Aura on Render does not execute desktop actions in its own
process. A minimal authenticated local Windows agent runs on the user's
machine and receives commands from the server. Until that agent is
connected, Aura states plainly that it cannot act on that machine.

The audit offered three options for AURA-P0-005. (a) desktop actions only
in desktop mode, server declines honestly; (b) a local agent plus a
Render->PC transport; (c) Android only. The user chose (b). Option (a)'s
honest refusal is still implemented first, in Phase 4, and remains the
behaviour whenever no agent is connected - otherwise (b) reintroduces the
original false success with a timeout in front of it.

Reason: `tools/builtins/apps.py` launches via in-process `subprocess`, so
on Render it would spawn inside the Linux container. There is no reverse
tunnel and no device-command route in the tracked source, and a container
cannot reach a home machine unsolicited. A long-poll endpoint the agent
calls outward is the smallest thing that works through NAT without new
infrastructure.

Security boundary: the agent holds its own allowlist locally and refuses
anything outside it. Server-side config alone is not sufficient -
compromising the server would otherwise equal arbitrary code execution on
the user's PC. The bearer token in `server/auth.py` authenticates the
agent; it does not decide what the agent may do.

Affected files: `server/routes/` (new device route), `server/main.py`,
a new local agent package, `tools/` (an `open_url` tool does not exist
yet), `.claude/task-queue.md` Phase 6.

Do not change unless: the deployment model changes - if Aura runs on the
user's own machine, the transport is unnecessary and actions execute
in-process as they already can.

## 2026-08-10 — Phase 3 was verified rather than reimplemented

Decision: the tool-calling implementation found already written in the
working tree was reviewed against the audit's requirements and kept. Only
tests were added.

Reason: a previous session wrote it and stopped before its tests - the
suite still reported the post-Phase-2 count, and no test referenced
`tool_calling`, `_resolve_tools` or `catalogue`. Review found the loop
correctly bounded, the port boundary intact (`brain/` imports nothing
from `tools/`), and both P0-003 and P0-004 genuinely fixed rather than
cosmetically changed. Rewriting working, well-documented code to
reproduce the same design would have risked a regression for no gain.
Three mutations confirmed the new tests fail against the old behaviour.

Affected files: `tests/test_tool_calling.py` (new, 95 tests). No
production file was modified in Phase 3.

Do not change unless: a live-provider run shows the documented tool-call
shape is not what models actually emit, which would move the problem to
`read_tool_call` rather than to the loop.

## 2026-08-11 — Memory 2.0 layers over the existing store, not beside it

Decision: `MemoryPipeline` shares the `MemoryManager`'s SQLAlchemy
session rather than opening its own. Episodic memories, temporary
context and the user model are new tables in the same database file and
the same transaction scope as the transcript.

Reason: the alternative is two memory systems with two lifetimes, and
the failure mode is silent - a transcript that commits while the
episodic write rolls back leaves a conversation that happened and an
event that did not. Sharing the session also means the existing
`db_lock` keeps covering everything, so no second concurrency story was
invented. SQLite was kept: nothing in the retrieval design needs vectors,
and a repository with no migration system is the wrong place to add a
second database engine.

Affected files: `memory/pipeline.py`, `memory/episodic.py`,
`memory/temporary.py`, `memory/user_model.py`, `launcher/services.py`.

Do not change unless: retrieval quality is measured as the bottleneck,
which would be an argument for better ranking before it is an argument
for a vector store.

## 2026-08-11 — Phase 8 table creation lives in the composition root

Decision: `init_database()` creates only `messages` and `user_facts`.
The Phase 8 tables are created by `_build_pipeline`, guarded by
`memory.pipeline` being enabled.

Reason: this repository has no migration system (docs/DEPLOYMENT.md is
explicit). `create_all` is additive and idempotent, so running it at
startup is safe either way - but keeping it next to the "is the pipeline
on?" decision means a deployment that disables pipeline memory leaves
the database exactly as it found it, and the guard cannot drift away
from the thing it guards. It also stopped a test from growing tables in
the user's real database.

Affected files: `memory/sqlite.py`, `launcher/services.py`.

Do not change unless: a real migration tool lands, at which point this
belongs in a migration.

## 2026-08-11 — The test suite can never reach the real database

Decision: a session-scoped autouse fixture in `tests/conftest.py`
rebinds `memory.sqlite.engine` and reconfigures `SessionLocal` onto an
in-memory StaticPool database for the whole run.

Reason: four composition-root tests wrote 46 seeded user-model rows into
`data/memory.db` while passing. Rows written there are read back on the
user's next real conversation and treated as confirmed facts about them.
Most suites already inject their own session; the ones that don't are
the ones that build a whole runtime, and an opt-in convention leaves the
next such test to rediscover this. `configure()` mutates the object the
stores captured at import time - rebinding the name would not have
reached them. StaticPool because a fresh connection to `:memory:` is a
fresh empty database, and in-memory rather than a temp file because
Windows cannot delete a SQLite file a pooled connection still holds.

Affected files: `tests/conftest.py`,
`tests/test_memory_integration.py::test_the_shared_engine_is_not_the_users_database`.

Do not change unless: a test genuinely needs the real database, which
should be an integration test with the existing opt-in marker instead.

## 2026-08-11 — Proactive delivery is pull-driven, and says so

Decision: `ProactiveEngine.tick()` runs when a client polls
`GET /api/notifications`, publishing to the existing
`CompanionNotificationEvent` + `NotificationOutbox` path. No scheduler,
no background thread, no second transport.

Reason: the spec asked for no second networking stack, and a background
scheduler needs deployment infrastructure this repo does not have - no
worker process, no task queue, and a free-tier service that spins down
when idle. Building a thread that ticks inside a web process that may
not be running would look like proactive messaging while delivering it
unreliably, which is worse than the honest limitation. The consequence
is documented in `config.yaml` and `docs/API.md`: a message that would
have gone to a sleeping phone is not sent late - it is not sent.

Affected files: `proactive/engine.py`, `server/runtime.py`,
`server/routes/notifications.py`, `config.yaml`, `docs/API.md`.

Do not change unless: a worker process exists, at which point the engine
already has a `tick()` to call and nothing else needs to move.

## 2026-08-11 — One vision key became two, with the old one still honoured

Decision: `vision.cloud_model` and `vision.ollama_model`, resolved by
`vision/settings.py`. Both fall back to the legacy `vision.model` before
their own defaults.

Reason: one key was read by two processors that want different kinds of
name, so only one of them could be right at a time. It was latent rather
than broken because `capture_screen` is false and the local processor is
never built - turning capture on handed a Gemini name to a local daemon.
The legacy fallback stays because config files have no migration path
either: an existing `vision.model` keeps resolving exactly as it did.

Affected files: `vision/settings.py`, `vision/cloud_processor.py`,
`vision/debug.py`, `launcher/services.py`, `core/config.py`,
`config.yaml`, `tests/test_vision_settings.py`.

Do not change unless: the legacy key is confirmed absent from every
deployment, which would allow dropping that fallback step.
