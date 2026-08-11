# Current Task

## Repair mandate

Fix every defect in the full-project audit, P0 -> P3, working phase by
phase. Run the relevant tests after each phase, run the full hermetic
suite before declaring a phase complete, and review `git diff` after
each phase. Do not commit unless asked.

Hermetic suite: `.venv/Scripts/python.exe -m pytest -q`
Baseline: 885 passed, 1 deselected. After Phase 2: 958. After Phase 3: 1053.
After Phase 4: 1067. After Phase 5: 1101. After Phase 6: 1146.
After Phase 7: 1160. After Phase 8: 1441.

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
- [ ] Phase 9 - Local Windows device agent (Option B) - NOT STARTED
- [ ] Phase 10 - Final integration, regression, release-readiness audit

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
conversational text.

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

## Next: Phase 9 - local Windows device agent (Option B)

Not started. Phases 6, 7 and 8 each took a slot this plan had once
labelled "device agent"; it has never been built and was never asked
for.

## When Phase 9 starts (Option B - local Windows agent)

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

