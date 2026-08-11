# Progress Log

## Baseline (Phase 0)

- Repository: `D:\AURA`, branch `feature/aura-identity`.
- Hermetic suite command: `.venv/Scripts/python.exe -m pytest -q`
  (the bare `python` on PATH resolves to an unrelated venv without pytest).
- **Baseline: 885 passed, 1 deselected, 0 failed.**
- Working tree already dirty before this repair began: `.env.example`,
  `launcher.py`, `requirements.txt`, `tests/test_remote_vision.py`, plus
  ~300 tracked Android build artifacts. Untracked: `.claude/`, `CLAUDE.md`,
  `Modelfile.qwen35`, `aura-tree.txt`, `awesome-claude-skills/`,
  `brain/providers/cerebras.py`, `docker/`, `memory/project-*.md`.

## Completed

### Phase 1 - Error visibility (916 passed, 1 deselected)

- **AURA-P1-015** `brain/conversation.py`: the four optional-collaborator
  swallows (`_emit`, `_vision_context`, `_knowledge_for`, `_styled`) now
  log at debug level. Graceful degradation is unchanged - the turn still
  completes and is still saved.
- **AURA-P1-011** `brain/providers/fallback.py`: extracted `_category_of`.
  An unrecognised exception is now `"unclassified provider error"` and the
  real exception type is logged, instead of every unknown failure being
  reported as an authentication/configuration error. The account-limit
  short-circuit is preserved via the `ACCOUNT_LIMIT` constant.
- **AURA-P2-001** `core/logger.py`: level is configurable via
  `AURA_LOG_LEVEL` (read at import, governs startup) and via
  `logging.level` in config.yaml (applied by `apply_config_level`, called
  from `build_services`). `logging.level` previously did nothing at all.
- **AURA-P1-012** `brain/router.py`: logs the requested chain, the
  initialized chain, and every skipped fallback with a reason naming the
  missing environment variable. Key *values* are never logged.
- Also: `launcher/services.py` `_CompositeKnowledge` now logs both of its
  swallowed lookups at debug level.
- New tests: `tests/test_error_visibility.py` (31 tests).

### Phase 2 - Android accessibility (958 passed, 1 deselected; 132 Android)

- **AURA-P0-006/007** `brain/agent_mode.py` (new) is now the single
  definition of "this turn is read by a parser, not a person" -
  `is_agent_tick`, `is_intent_probe`, `is_machine_turn`, `read_intent`.
  Detection is by context key, never by message text. `PromptBuilder`,
  `ConversationManager` and the tests all import it, replacing the inline
  predicate that used to sit in `prompt_builder.build`.
- `brain/prompt_builder.py`: an agent tick now gets a purpose-built
  prompt (DEVICE STATE / ACCESSIBILITY TREE / LAST ACTION ERROR / AGENT
  RULES / USER) with none of the conversational sections. Asking for warm
  Gen-Z prose and raw JSON in one prompt is what AURA-P0-007 was.
  `_build_intent_prompt` added for the router probe.
- `brain/conversation.py`: a machine turn is not styled, not saved, and
  not published (`ResponseEvent`, `UserInputEvent`, `ThinkingEvent`, all
  stream events). `ErrorEvent` still fires - a provider outage is a fact
  about Aura whoever asked.
- `brain/providers/base.py`: INTENT RULES joins the system-slot headers.
- **AURA-P0-008** `AgentActionParser.kt` (new): fences, unknown fields,
  prose wrappers, single-element arrays and braces inside strings all
  parse. Extraction is brace-matched and string/escape aware. An
  unreadable reply is no longer fatal - the reason travels back as
  `last_action_error` and the model gets up to
  `MAX_PARSE_FAILURES = 3` consecutive corrections inside the existing
  `maxSteps` budget.
- **AURA-P1-003** `verifyOpenApp`: when the target package is known,
  verification requires the foreground package to BE that package. The
  old check passed on *any* package change, so opening a crash dialog
  counted as opening YouTube. Generic change is the fallback only when
  the target is unknown.
- **AURA-P1-004** `IntentRouter.kt` (new) + `ChatViewModel.send`: the
  agent loop is entered only when the server's one-word probe says
  `action`. Previously every message went into the loop whenever the
  service was enabled, so the phone could not hold a conversation. The
  probe failing open to conversation is deliberate; the two mistakes do
  not cost the same.
- New tests: `tests/test_machine_turns.py` (42),
  `AgentActionParserTest.kt` (26), plus one `ChatViewModelTest` case.
- **Not verified:** `"mở youtube"` end to end on hardware. Needs a device.

### Phase 3 - Tool/capability pipeline (1053 passed, 1 deselected)

The implementation was found already written in the working tree by a
previous session that stopped before its tests - `git status` showed it
untracked/modified, `.claude/progress.md` still listed Phase 3 as current,
and the suite still reported the post-Phase-2 count of 958. This phase was
therefore a review-and-verify pass, not a reimplementation.

**Reviewed and found correct** (no code changes needed):

- **AURA-P0-001** `brain/conversation.py:408` `_resolve_tools` - bounded
  three ways: `TOOL_CALL_LIMIT = 3` rounds counting malformed requests, an
  identical-repeat check, and a final round that offers no tools so the
  turn always ends on a sentence. `_run_tool` treats an exception from the
  injected runner as a failed call, never a successful one.
- **AURA-P0-002** `brain/prompt_builder.py:211/251` - `_build_tools`
  renders TOOLS, `_build_tool_results` renders TOOL RESULTS. Both return
  `[]` when empty, so a no-tools prompt is byte-identical to the
  pre-tools one. `split_prompt` puts the catalogue in the system slot
  (an instruction) and results in the user slot (evidence).
- **AURA-P0-003** `tools/builtins/apps.py` - `_resolve` refuses rather
  than falling back to the raw name; a `GRACE_SECONDS = 0.5` wait
  distinguishes "still running" from "exited non-zero"; stderr goes to a
  temp file and is reported. A success does not claim a window appeared.
- **AURA-P0-004** `tools/factory.py:74` `_mapping_setting` - a non-mapping
  `applications` is ignored *with a warning*. `config.yaml:82` is `{}`.
- **AURA-P1-001/002** `config.yaml:56` enables tools with exactly one SAFE
  tool (`current_time`); `auto_approve: [safe]` and no server-side confirm
  handler keep DANGEROUS unreachable in server mode.
- Architecture boundary verified empirically: `brain/` has no import of
  `tools/` (both grep hits are prose in docstrings), and
  `isinstance(ToolExecutor(), ToolRunner)` is True - the port is satisfied
  structurally.

**Added:** `tests/test_tool_calling.py` (95 tests), the missing proof.
Covers the byte-identity regression contract, request parsing (fenced,
prose-wrapped, brace-in-string, escaped quotes, malformed, non-string
name/arguments), prompt placement and slot routing, the loop (execute,
reinject, multi-round, limit, repeat refusal, malformed-never-executes,
raising runner, null result, denial), the real `ToolExecutor` as the
runner, `OpenApplicationTool` (missing binary, non-zero exit, stderr,
clean exit, still-running, `shell=False`, OS refusal), and the four
independent config gates.

**Verified the tests can fail.** Three mutations were applied to the
implementation and reverted byte-identically afterwards:

- `offer = True` (final round always offers tools) -> 4 failures
- `_resolve` falling back to the raw name (the original P0-003 bug)
  -> `test_a_missing_executable_fails_honestly...` fails
- skipping `_mapping_setting` -> `test_a_wrongly_shaped_applications...`
  fails

Determinism: every application test uses `sys.executable` or a name that
cannot exist, so nothing depends on what is installed on the machine.

**Not verified:** no end-to-end run against a live provider. The loop is
proven against stubs and against the real `ToolExecutor`, not against
Gemini.

### Phase 4 - Device-action boundary (1067 passed, 1 deselected)

Scope was re-framed before implementation: the question is not "what
transport reaches the PC" but "where can the server claim a physical
action succeeded". The end-to-end trace of "Open YouTube on my PC" -
`/api/chat` -> `ServerRuntime.chat` -> `ChatEngine` ->
`ConversationManager.chat` -> `_generate` -> `_resolve_tools` ->
`ToolExecutor` -> `ChatResponse.reply` - found the answer is **Case B,
but only in speech**.

**Root cause.** Two halves of the boundary, one of which was missing.

- *Structural half (was already correct).* No `/device` route exists;
  the shipped policy grants exactly one SAFE tool. `_resolve_tools`
  returns early when the catalogue is empty, so an unknown tool is
  either never requested or refused by `check()`. A device action
  therefore cannot actually execute, and a *verified* false success is
  impossible.
- *Spoken half (was missing).* Nothing executes, but nothing stopped the
  model from saying it had. The only instance of "never claim you did
  something without evidence" lived in
  `PromptBuilder._build_tools`, which renders **nothing** when the
  catalogue is empty. So the rule was absent in exactly the
  configurations where Aura can do least:
  `/api/chat/stream` and the WebSocket (`offer_tools=False` always),
  tools disabled, and an empty allow list. `/api/chat` returns
  `response.text` verbatim as `reply`, so a fabricated "Opening YouTube
  now" reaches the user unchallenged.

**Fix - one file, no Python.** The rule moved to `prompts/system.md`
(new `# Actions` section), which every conversational turn loads
unconditionally, so it no longer depends on a tool happening to be
allowed. `_build_tools` keeps its own copy: with a catalogue present the
rule is worth repeating next to what it governs. No `/device` route, no
`open_url`, no capability abstraction, no change to the tool loop.

Machine turns are deliberately excluded - verified empirically that an
agent-tick prompt contains no SYSTEM section. The Android agent really
does act and really does verify (`verifyOpenApp`), so the rule would be
false there.

**Added:** `tests/test_device_boundary.py` (14 tests) covering both
halves - the rule present with an empty/absent/whitespace catalogue, on
the streamed path, routed to the system slot by `split_prompt`, absent
on machine turns, plus the structural invariants (no device route, the
shipped policy grants only `current_time`, a hallucinated `open_url` is
refused). Vietnamese request included: a boundary stated only in English
leaks on the first `"mở youtube"` turn.

**Verified the tests can fail.** Three mutations, all reverted and
confirmed byte-identical by md5: reverting the `system.md` section -> 9
failures (the 5 structural tests correctly still pass, they were already
true); adding `POST /api/device/commands` -> the route test fails;
allowing `open_application` with an `applications` entry -> the policy
test fails.

**Not verified:** that a live provider obeys the instruction. This is a
prompt-level control, and its limit is honest - it makes the false claim
contrary to instruction rather than impossible. The impossibility is the
structural half, and Phase 6 must keep it by giving the device agent its
own local allowlist and returning real execution evidence.

### Phase 5 - Provider cleanup / reliability (1101 passed, 1 deselected)

One question this phase had to make answerable: *which provider does Aura
use, in what order, and what happens when one fails?* Five defects, all
reproduced empirically before being fixed.

- **AURA-P1-009** `brain/router.py` - `_create_provider` returned
  `OllamaProvider()` before the chain builder was reached, and
  `_instantiate_provider` had no `ollama` branch. So the local provider,
  the one most likely to be unreachable, was the only one with no
  failover, *and* could not be a fallback member either. Both halves
  fixed; `provider: ollama` now yields a real `FallbackProvider`. The
  existing wrapper was reused - no parallel mechanism.
- **AURA-P1-010** `brain/providers/ollama.py` - `OLLAMA_HOST` is read.
  `docs/DEPLOYMENT.md` had documented it in four places and nothing read
  it. Precedence: argument, config, environment, loopback default.
- **AURA-P2-009** `brain/providers/ollama.py` - the model was inferred
  from `llm.model` by `startswith("gemini")`, so *any* non-Gemini primary
  model name was passed to Ollama as a local tag (measured:
  `claude-opus-5` -> Ollama model `claude-opus-5`). Replaced with
  `llm.ollama_model`, mirroring the existing `groq_model`/`mistral_model`
  pattern.
- **AURA-P2-010** `brain/router.py` `_fallback_names` (new) - the legacy
  `fallback_provider` was read only when `fallback_providers` was `None`,
  but that key is in `DEFAULT_CONFIG`, so after the deep merge it is
  never `None`. **This was a live bug, not dead config:** an operator who
  wrote only the singular form got no failover and no warning. Emptiness
  is now the test, the legacy key is honoured with a warning, and setting
  both names the loser.
- **AURA-P1-012 (already closed in Phase 1)** - the requested/initialized/
  skipped logging was in place. Extended rather than rebuilt: a fallback
  whose constructor *raises* is now reported and skipped instead of
  taking the process down (every cloud provider validates its key in
  `__init__`), a chain with no surviving fallback says so, and
  `BrainRouter.active_chain()` lets `/api/health` report what was built
  ("gemini->groq") instead of what was configured ("gemini").

Also: Ollama's `URLError` now raises `ProviderUnavailableError`, so
`_category_of` reads an unreachable local box as "transient/unavailable"
rather than "unclassified provider error".

**Decisions on orphaned providers.**

- *Groq and Mistral: KEEP.* Their keys are absent from this deployment,
  which is not the same as the providers being obsolete. Both are
  registered in `PROVIDER_KEYS`, both have `DEFAULT_CONFIG` model
  defaults, both are in the shipped chain, and both have end-to-end
  failover tests (`27c8dd4 Integrate Groq and Mistral into cloud provider
  failover`). Removal would have been destructive guessing.
- *Cerebras: KEEP, EXPLICITLY UNWIRED.* Registering it would ship code
  nobody has executed - and it has a real defect its siblings do not:
  `generate` sends the whole prompt as one user message instead of
  calling `split_prompt`, so the system slot (which now carries the
  Phase 4 device-action boundary) would arrive as conversational text.
  The file documents what must be true before it is registered.
- *DeepSeek: NOT IMPLEMENTED.* `DEEPSEEK_API_KEY` is present in the real
  `.env` and read by no code. No provider was invented for it; the key is
  documented in `.env.example` as having no effect, so its presence is
  not mistaken for configured failover.

**Model-value contradiction, reported not resolved.** `qwen3:8b`
(provider default), `qwen2.5vl:7b` (`core/config.py` vision),
`qwen3-coder:30b` (`Modelfile`), `qwen3.5-9b-local` (`Modelfile.qwen35`),
`aura-qwen3-coder` (`opencode.json`), `ollama_chat/qwen3-coder:30b`
(`.aider.model.settings.yml`). The last four configure the *external
coding agent*, not Aura's runtime. `ollama_model` was set to `qwen3:8b`
- the value the provider has always effectively used - rather than
picking a new one from the audit. Nothing was verified against what is
actually pulled on this machine.

**Added:** `tests/test_provider_resolution.py` (34 tests), grouped by
promise rather than defect number.

**Verified the tests can fail.** Four mutations, all reverted and
confirmed byte-identical by md5: restoring the Ollama early return -> 2
failures; removing the `OLLAMA_HOST` read -> 2; restoring the
`startswith("gemini")` hack -> 3; restoring the `is None` emptiness test
-> 1. The fourth mutation is worth recording: mutating
`config.get(...) or []` to `is None` changed *nothing*, because the
load-bearing line is the downstream `if legacy and not names`. Mutating
the line that actually carries the fix is what caught it.

**Not verified:** no live provider was called. Failover is proven against
patched `generate` methods and the real instantiation path, not against
Gemini, Groq, Mistral, OpenRouter or a running Ollama. Ollama is not in
the shipped chain, and nothing here installs Ollama into the Render image
or gives the cloud access to the user's PC.

# Phase 6 - Security & deployment hardening (1146 passed, 1 deselected)

AURA-P1-007, AURA-P1-008, AURA-P1-014 and the deployment half of
AURA-P0-005. Renumbering: this took the slot the plan had labelled "local
Windows device agent", which was explicitly deferred and prohibited here.

**AURA-P1-008 - an empty token silently disabled authentication.** The
server logged a warning and then served every request anyway, so a
forgotten environment variable on a public host published an LLM.
`enforce_auth_policy` (`server/config.py`) now raises
`InsecureConfigurationError` unless a token is set or
`AURA_ALLOW_INSECURE` is explicitly affirmative (`1`, `true`, `yes` -
anything else, including `0` and a half-edited line, means no). It is
called from the ASGI lifespan *outside* the `is_initialized` guard: that
guard exists so tests can pre-install a runtime, and a pre-installed
runtime says something about who built the engine, not about whether the
process is safe to expose. `launcher.py --server` calls the same policy so
the failure reads as a configuration error on stderr rather than a
traceback from inside uvicorn. Nothing here reads, logs or returns the
token; the only inputs are whether it is empty and whether the opt-in is
present.

**AURA-P1-007 - wildcard CORS paired with credentials.** Established by
measurement, not assumption: with `allow_origins=["*"]` *and*
`allow_credentials=True`, Starlette sets
`preflight_explicit_allow_origin`, so a **preflight** echoes whichever
origin asked - `Allow-Origin: https://evil.example` together with
`Allow-Credentials: true`, which browsers honour. A simple GET sends a
literal `*`, which they reject. The hole opened through preflight, not
through GET. `cors_policy` now refuses credentials whenever a wildcard
appears anywhere in the list. Wildcard is not banned outright, because
Aura authenticates with a bearer token that JavaScript sets explicitly and
such a request needs no credentials mode.

**AURA-P1-014 - every failure was the same opaque 500.** New
`server/errors.py` is a thin presentation layer over the *existing* typed
brain errors - no second hierarchy. `classify` maps
`ProviderRateLimitError` -> 429 `rate_limited` (carrying `Retry-After`
when the provider supplied one), `ProviderUnavailableError` -> 503
`provider_unavailable`, anything else -> 500 `chat_failed`. Order is
load-bearing: `ProviderRateLimitError` subclasses
`ProviderUnavailableError`, so an isinstance chain in the wrong order
turns every 429 into a 503 and clients stop honouring `Retry-After`.
Unknown stays 500 deliberately - guessing "provider problem" for an
unrecognised exception is the mistake `_category_of` was fixed for in
Phase 1. Every client-facing string is a module constant, so hosts, paths
and key fragments cannot reach the wire; the exception text is logged
only, now tagged `classified=<code>`.

**WebSocket, verified separately** rather than assumed identical to HTTP.
It differs in three ways that mattered: the token is a query parameter
(proxies log it), `CORSMiddleware` does not apply to websockets at all so
there is no Origin check, and rejection happens correctly before
`accept()`. Its error vocabulary was kept: `stream_failed` remains the
code for an unclassified in-generation failure and `internal_error` for
one outside generation (both are in `docs/API.md` and
`AuraStreamClient.kt`); only recognised provider failures get the new
codes, plus `message` and `retry_after`.

**Health semantics.** `/api/health` stays authenticated with its pinned
key set. Readiness became a separate public route, `/api/ready`, because a
container healthcheck and Render's probe cannot hold a bearer token.
`ServerRuntime.readiness()` reports only what a chat turn cannot proceed
without - a started runtime and a constructible provider chain - and 503s
otherwise. It excludes vision, voice, screen and companion on purpose: a
probe that fails when TTS is off would restart a healthy server forever.
It does **not** call the provider, so polling costs nothing and one
provider outage cannot become a restart loop. The docker-compose
healthcheck moved from `/` to `/api/ready`.

**STEP 7 persistence - investigated, not invented.** The
configuration-level fix already existed and is free: a 1 GB Render disk
named `aura-data` mounted at `/app/data`, which is where
`memory/sqlite.py:39` writes `memory.db` and what `docker-compose.yml`
already mounts. Documented the consequence of omitting it (every deploy,
restart, crash and idle spin-down starts from an empty database, silently)
and pointed at the existing plan. No infrastructure was built and no paid
feature assumed.

**Device boundary (AURA-P0-005) - unchanged and re-pinned.** No device
route, no fake executor, no `/device` endpoint, no weakening of Phase 4.
`tests/test_security_hardening.py` duplicates the structural assertions on
purpose, because that file is what a security review reads.

**Added:** `tests/test_security_hardening.py` (38 tests), grouped by the
promise each defect broke rather than by defect number, because that is
how they are read when one fails. Its autouse fixture deletes
`AURA_ALLOW_INSECURE` from the environment - without it a developer
machine exporting that flag would make the "startup fails" tests fail to
fail, which is worse than failing. `settings_with` passes
`_env_file=None` so a real `.env` token cannot hide the unauthenticated
case. `tests/test_server.py` gained 7 tests (429/503/500 through the real
app, `/api/ready` both ways, and the two stream codes); its module-scoped
`client` fixture now sets the insecure opt-in with save/restore, since the
development mode it depends on is now opt-in.

**Verified the tests can fail.** Ten mutations, every one caught, every
one reverted and confirmed byte-identical by md5: remove the refusal;
break the env read; accept any opt-in value; move enforcement inside the
`is_initialized` guard; disable rate-limit classification; drop
`retry_after`; restore wildcard-plus-credentials; make readiness always
true; make `/api/ready` never 503. Two are worth recording. My first
attempt at the guard mutation left the original pre-guard call in place
and so proved nothing - redone, it failed as it should. And one mutation
was masked by another in a combined run; re-applied alone, it failed.
Line endings also bit: `read_text`/`write_text` silently converted LF to
CRLF and broke byte identity, so later mutations used `read_bytes`.

**Not verified:** no live deployment. The refusal, the CORS policy, the
taxonomy and `/api/ready` are proven against the real ASGI app via
`TestClient`, never against Render. On the next deploy, confirm
`AURA_SERVER_AUTH_TOKEN` is set in the dashboard (without it the service
now fails to start rather than serving an open LLM) and that the health
check path is `/api/ready`.

## Phase 7 - Repository cleanup + final P2/P3 + cross-phase verification

`.venv/Scripts/python.exe -m pytest -q` -> **1160 passed, 1 deselected**
(1161 collected). +14 over Phase 6: four in `test_error_visibility.py`
(STEP 6), seven in `test_server.py` (the P1-006 sweep and the
single-tenant pin), three in `test_pipeline.py` (the StreamingLLM
deduplication).

**Untracked ~2150 build artifacts (P2-007).** `git rm -r --cached` over
`android/.gradle/`, `android/build/`, `android/app/build/` plus `.class`,
`.dex`, `.apk`, `.aab`, `.ap_`, `local.properties`; `.gitignore` extended
to cover them and `.venv-py314-backup/`, `awesome-claude-skills/`,
`aura-tree.txt`. Tracked files ~2400 -> ~250. Every removal was confirmed
generated before it was removed. `android/gradle/wrapper/` is deliberately
still tracked: the wrapper jar is source, and without it a fresh checkout
cannot build.

**Deleted, each after proving zero importers, callers and references:**
the top-level `tts/` package (P2-002, superseded by `voice/`), the stray
`D:AURAserverroutes/` directory (P2-006, a literal-path mkdir accident),
`brain/providers/openai.py` and `core/events.py` (both 0 bytes; the
second was a trap, since `events/` is a live package and an empty
`core.events` invites a wrong import), and the two pre-audit
`.claude/*.bak` templates (P2-005).

**Kept, having proved the same thing.** `brain/prompt.py` (working
`Prompt` dataclass, superseded by `PromptBuilder`) and `brain/llm.py`
(documented back-compat shim) both have zero importers. Deleting working
code on a zero-caller proof alone is a judgement call, not evidence, and
nobody asked for it. Reported instead. Providers were held to the
opposite rule: Cerebras and every fallback member stayed, because a key
being absent today is not proof a provider is dead.

**P3 items.** `scripts/` created and six `manual_*.py` moved out of
`tests/` with `git mv` (P3-004) - none defined a `test_` function, so
pytest never collected them; no real test module moved. One Vietnamese
comment in `core/logger.py` translated (P3-001). One `@abstractmethod`
body changed from `pass` to `...` in `brain/providers/base.py` (P3-002);
`memory/models.py` left alone, where `pass` is the SQLAlchemy idiom. CI
added as `.github/workflows/tests.yml` (P3-003): checkout, setup-python
3.11 with pip cache, install, `pytest -q`. It restates nothing from
`pytest.ini` and references no secrets, since the hermetic suite must
pass with no keys at all.

**Closed the P2-001 wiring gap.** `apply_config_level` was correct and
nothing pinned that `build_services` calls it - the same shape of bug as
an auth guard placed after an early return, where the unit passes and the
deployment is still wrong. Added
`test_composition_root_applies_the_configured_level`, which drives the
real composition root with an injected in-memory database and reads the
level off the shared logger afterwards.

**STEP 5, false-success sweep: clean.** Every unconditional-success path
found was an event publish or a teardown - graceful degradation, not a
lie about an action. No device route exists. Server routes return
`accepted`/`ignored` reflecting the real outcome, and `last_error` raises
503. The only surviving mention of the old bug is a comment recording the
fix.

**STEP 6, error visibility.** Four `_emit` helpers swallowed bus failures
with no log at all, while `brain/conversation.py` logged its at debug -
so a dead bus was undiagnosable in exactly those four places. Added
logging, changed no control flow. Teardown sites left silent: during
shutdown there is nothing actionable. Importing `core.logger` into
`brain/mood.py` is allowed by the architecture rule (subsystems may
import `core/` and `events/`), so this is not a horizontal import.

**Documentation resynchronised against measurement, not memory.**
`docs/IMPLEMENTATION_STATUS.md` and `docs/ROADMAP.md` both claimed the
test suite had never been executed; `docs/FOLDER_STRUCTURE.md` listed
23 test files (32), omitted `server/` and `companion/` entirely, and
still listed both files deleted this phase. `docs/DEPLOYMENT.md` claimed
an OpenAI placeholder that no longer exists.
`docs/CLOUD_MIGRATION_AUDIT.md` kept its 659 figure but is now labelled a
historical snapshot rather than current status, per the instruction not to
rewrite it into marketing language. `.env.example` gained the five env
vars code reads but it never listed (GROQ_MODEL, MISTRAL_MODEL,
GROQ_BASE_URL, MISTRAL_BASE_URL, CEREBRAS_BASE_URL) and lost two it
listed that nothing reads (ELEVENLABS_API_KEY, EDGE_TTS_VOICE - the
former belonged to the deleted `tts/` tree).

**New finding, reported not fixed: `vision.model` has two consumers.**
`config.yaml:49` ships `gemini-3.6-flash`. That is right for
`vision/cloud_processor.py` (server, Gemini) and wrong for
`OllamaVisionProcessor` (desktop), which wants an Ollama tag like
`qwen2.5vl:7b` and POSTs to `127.0.0.1:11434` - so desktop pixel vision
would send a Gemini model name to a local Ollama daemon. Dormant
(`capture_screen: false`) and bounded (the processor logs and yields
`""`). Documented at both ends. Not fixed: splitting the key is a config
change with a migration question attached, and the mandate was to report
rather than act unilaterally.

**Secrets.** `.env` was never read, never printed and never modified;
it is untracked and ignored (`.gitignore:2`). No secret appears in any
diff from this phase; `.env.example` carries key names with empty
values only.

**STEP 4, duplicate systems.** A class-name collision sweep over every
package returned exactly two names. `Message` is deliberate - the ORM row
and the brain dataclass are two types precisely so the brain never
imports the ORM, and `test_brain_message_and_db_message_are_distinct_types`
already pinned that. `StreamingLLM` was not: it was defined in both
`brain/streaming.py` and `brain/ports.py`, and the two disagreed. The
ports copy required `generate` *and* `stream` while claiming in its own
docstring to be a re-export of the other, which requires only `stream` -
so a stream-only provider was a StreamingLLM through one import and not
through the other. Nothing imported either, which is why nothing failed.
`ports.py` now imports the real one; `brain/streaming.py` imports nothing
from `brain`, so there is no cycle, and `isinstance` semantics were
checked directly. Three tests added.

## Phase 8 - Memory 2.0 + Temporal Context + User Model + Proactive

Complete. 1441 passed, 1 deselected, 0 failed, 0 errors (from 1160).
281 new tests. Not the device agent - the user's Phase 8 spec was this
work, so the numbering slipped a third time.

**Investigation first.** The existing memory system was one
`MemoryManager` over SQLite with lexical keyword recall
(`memory/retrieval.py`), no vector store and no embeddings. It was kept.
Memory 2.0 is a second layer *over the same session*, not a replacement,
and `MemoryPipeline` is the only new entry point - there is no second
memory manager and no second database.

**Temporal.** `core/temporal.py` holds `TemporalClock` (one per process,
injected) and the relative-date vocabulary - today/yesterday/tomorrow,
earlier today, morning/afternoon/evening/night, last week, months ago -
computed against the clock rather than stored on the row, so a memory
written yesterday reads as "yesterday" today and "2 days ago" tomorrow.
A `TIME` prompt section was added between CONTEXT and MEMORY: a recalled
event dated "yesterday" is meaningless until the reader knows today's
date. `PromptBuilder._build_time` takes already-rendered lines and never
calls a clock, which is what keeps a prompt reproducible. No hardcoded
dates anywhere; a grep for `datetime.now` outside `core/temporal.py` and
the ORM column default returns nothing.

**Three stores, not one.** Stable profile (`memory/user_model.py`),
episodic events (`memory/episodic.py`) and temporary context
(`memory/temporary.py`) are separate, and temporary context never
auto-promotes - it expires by `valid_until` on read.
`memory/selection.py` is the gate: "ok thanks" reaches nothing, "I'm
learning Japanese because I want to read manga untranslated" reaches
episodic. Aura's own replies are never offered to long-term memory at
all - a system that remembers its own output starts citing itself as
evidence within a few turns.

**Machine-turn isolation held.** The Phase 7 rule was enforced at the
transcript; Memory 2.0 added a second store, which is a second place for
the rule to be forgotten. `tests/test_memory_integration.py` now asserts
on *both* stores, parametrized over an agent tick and an intent probe,
streamed and unstreamed, plus that a machine turn cannot be recalled on
a later turn.

**Recall is bounded and ranked.** `RankedRetriever` scores candidates
lexically and `memory_lines` caps what reaches the prompt (6 user-model,
3 episodic, 3 temporary) out of a `retrieval_scope` of 500 rows. 30
stored lessons and a query for one of them yields 3 lines; a query about
the capital of France drags in no manga.

**User model.** Confirmed / inferred / unknown, each with confidence,
timestamps and a source. Nothing promotes an inference to a confirmed
fact except an explicit user statement. "I drank tea today" is temporary
information; "I prefer tea over coffee now" is a preference change; "you
remembered that wrong" is a correction that rewrites the entry rather
than being acknowledged and dropped. The supplied profile is seeded once
by `memory/user_profile_seed.py` (46 rows, `source="seed"`, idempotent),
structured so only the relevant rows reach a prompt.

**Proactive.** `proactive/` is a scheduler tick, a pure decision engine,
and anti-spam gates: global cooldown (7200s), per-category cooldowns,
quiet hours (22-08), daily max (4), duplicate and Jaccard-similarity
suppression. Every decision carries a reason, including every SKIP.
Silence is the default and `enabled: false` ships. Pending-task
reminders read the episodic store and nothing else, so a task is never
invented; an appreciation with no referent is not sent. Delivery reuses
the existing `NotificationOutbox` + `GET /api/notifications` poll - no
second networking stack, and no background worker was invented.
`tests/test_proactive.py` runs 100 ticks against a disabled engine and
asserts nothing is published.

**Two real bugs found by the new tests.** `_away()` in
`proactive/decision.py` crashed with `int(nan)` when the last-seen time
was unknown (`inf // 3600`); it now says "away, no record of a previous
message", which is also the honest phrasing. And a completed plan was
still reported as pending because plan-scaffolding words diluted the
token overlap, so `INTENT_WORDS` is now stripped from both sides.

**One near-miss caught outside the tests.** Four composition-root tests
opened the real `data/memory.db` and wrote 46 seeded rows plus a table.
The tests were green while doing it. Fixed three ways: the rows were
dropped (76 real messages verified intact), `init_database()` was scoped
to the two pre-Phase-8 tables with Phase 8 table creation moved to the
composition root where the "is the pipeline on?" decision lives, and
`tests/conftest.py` now redirects the module-level engine to an
in-memory StaticPool database for the whole session, so no test can
reach the user's data even by accident.

**Vision config split**, carried over from Phase 7's report:
`vision/settings.py` resolves `cloud_model` (Gemini, server) and
`ollama_model` (Ollama tag, desktop) separately, both falling back to
the legacy `vision.model` so an existing config behaves identically.

## Current

Phase 8 complete; state files updated. Next: the local Windows device
agent, now Phase 9 - not started, not asked for. The plan's phase
numbering has slipped three times; `current-task.md` records each.

## Blockers

- The Bash permission classifier was intermittently unavailable during
  Phase 0 and again during Phase 3, which delayed test runs. Not a code
  problem.
