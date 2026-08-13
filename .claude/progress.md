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

## Phase 9 (IN PROGRESS) - Android Control Hub, provider/API-key management

Backend half complete. `.venv/Scripts/python.exe -m pytest -q` ->
**1535 passed, 1 deselected** (from a verified 1480 baseline; the state
file's previous 1441 figure was stale). +55 in
`tests/test_settings_api.py`. Android work not started.

**Audit first (Phase A), and it changed the plan twice.**

- *The "Failed to parse action from server" regression in STEP 22 is not
  an open defect.* `AgentActionParser.kt` is brace-matched, strips fences,
  and sets `ignoreUnknownKeys`/`isLenient`/`coerceInputValues`;
  `tests/test_agent_protocol.py` (588 lines) pins the server->Android
  transport byte-for-byte on `response.content`. The symptom was an APK
  older than the server. Nothing was "fixed" that was not broken.
- *There is no Android TTS/STT at all.* Voice is server-side and ships
  disabled, so the Voice section must say so rather than offer phone-side
  controls that do nothing. This is a spec item that cannot be
  implemented as written, and the UI will state the constraint.

**API keys: `core/credentials.py`.** Fernet (AES-128-CBC + HMAC) under a
scrypt-derived key (n=2^14), salt stored beside the ciphertext, written
via `os.open(..., 0o600)` + `os.replace` so the file is never briefly
world-readable and a crash cannot leave a half-written blob.

The design decision worth recording: every provider already reads its own
key with `os.getenv` in `__init__`, and `BrainRouter._skip_reason` probes
the same variables to explain a provider it could not build. So a stored
key is applied to `os.environ` and **nothing downstream changes** - no
provider edits, no second resolution path, and `_skip_reason` stays
truthful. Threading a key parameter through five providers would have
been the larger change and would have left that diagnostic lying.

There is deliberately no plaintext fallback. With no secret configured
`persistent` is False, writes are refused, and the API returns the reason
verbatim - a store that silently degrades to plaintext is worse than one
that says it cannot help. `mask()` is the only way a value leaves the
module (`••••••••ABCD`, and a key of 8 characters or fewer loses
everything, since masking a 4-character key to its last 4 is the key).

**Settings: `core/settings_store.py` + one merge point.** An additive
overlay in `data/settings.json`, deep-merged over `config.yaml` rather
than rewriting it - rewriting would destroy operator comments, could not
distinguish "the deployment set this" from "the user set this", and a bad
write would corrupt the file the server needs to boot. Delete the overlay
and the deployment is exactly as it was.

`ALLOWED` is a closed allow-list of dotted paths, each with a validator;
the config tree carries things a remote client has no business setting
(`server.host`, logging, plugin paths) and an allow-list fails safe as
the tree grows. `update()` is all-or-nothing: a PATCH naming one bad
setting changes nothing, because a partially applied settings write is
state nobody can reason about afterwards.

The merge happens in `core/config.py:load_config` and nowhere else,
because `GeminiProvider`, `OllamaProvider` and `vision/settings.py` all
call `load_config()` directly - so a single choke point makes every
allow-listed setting real with no call-site changes.

**Live vs restart, reported honestly.** `ProactivePolicy.allows()` reads
`self.settings` at decision time and `MemoryPipeline.recall_enabled` is
read per turn, so both are genuinely live-mutable; `BrainRouter` caches
`_provider` lazily, so clearing it rebuilds the chain. Everything gated
in `build_services` needs a restart and is returned as `restart_required`
rather than claimed applied. When the chain cannot be rebuilt (a provider
whose key is still missing) the llm.* paths are *moved* from `applied` to
`restart_required` - claiming a live switch that did not happen is the
"fake toggle" the spec forbids.

**Two real bugs, both found by tests rather than review.**

- `set_provider_key` inferred "could not be made durable" from
  `not store.persistent`, but `CredentialError` was also raised for an
  *invalid* key - so on a server with no secret, posting a masked value
  back returned 200 "saved" instead of 422. Fixed at the source with a
  `CredentialNotPersisted` subclass, so the route distinguishes "valid
  but in-memory only" from "rejected" by type instead of by guessing.
  `TestNonPersistentKeys` is what caught it and now pins it.
- `SettingsService.apply` read `store.persistent` off the *settings*
  overlay, which has no such property - a `CredentialStore` concept
  borrowed onto the wrong object, returning 500 on every successful
  PATCH. `RuntimeSettings.update` raises rather than returning when it
  cannot write, so reaching that line already means it persisted.

**Test isolation caught a third.** The API fixture set
`AURA_SERVER_AUTH_TOKEN` with monkeypatch, but `server/config.py:114`
builds `settings = ServerSettings()` once at *import* time - so the
variable changed nothing and every request 401'd. It only appeared to
work when the file ran alone and happened to import the module first.
Now set on the singleton, which is what `tests/test_server.py` already
did. `tests/conftest.py` also snapshots and restores every
`PROVIDER_KEYS` environment variable per test, because `CredentialStore`
writes them by design and monkeypatch cannot undo a write it did not
make.

**A pre-existing flake fixed, unrelated to Phase 9.**
`test_expiry_drops_no_conversation_history` swept with
`max_age_seconds=0`, but `_expire` drops what is idle *longer than* the
age and `time.time()` on Windows advances in ~15.6ms steps - so a session
created and swept inside one tick had an age of exactly 0.0. It failed
about one run in four, and only when other sessions existed to satisfy
the `>= 1` on their own. The test now passes -1; production's strict `>`
is correct and unchanged. Six consecutive full-suite runs green
afterwards, from intermittent before.

**Added:** `tests/test_settings_api.py` (55 tests) - masking, encryption
at rest, reload, wrong-secret, corrupt-blob, environment precedence,
all-or-nothing validation, the merge, auth on every route, no key in any
response, no key in any log (including the failure paths, where a
careless `logger.warning("... %s", value)` usually hides), no key in
error text, and the non-persistent path end to end.

**Not verified:** no live provider was called; `POST /api/providers/test`
is proven against the real route, not against Gemini or Groq. No Android
code has been written or built this phase.

## Phase 9 Android - COMPLETE

**Added:** eleven files under `ui/hub/` and three under `ui/components/`
(4772 lines), plus `data/remote/ControlDto.kt`. `MainActivity` now
navigates chat -> hub -> ten sections against `HubRoutes`, sharing one
Activity-scoped `HubViewModel` so the server config is fetched once
rather than per screen.

**Toggles are real, or they are read-only and say why.** The
notifications switch re-syncs WorkManager rather than only writing a
flag; dynamic colour locks below Android 12 with the reason shown.
(*Superseded by Phase 10:* this phase also recorded
`server.screen.min_interval` as not settable and voice as two toggles,
because neither was in the allow-list then. Phase 10 added eight paths
and both statements are now false.)

**Four defects found and fixed during the build**, three of them mine:
`StatusRow` had no `subtitle` parameter (widened after checking every
call site uses named arguments); the two-stage quiet-hours dialog closed
itself after the first pick because `ChoiceDialog` calls `onDismiss()`
right after `onPick`; `@Suppress` sat on a `when` branch in
`AuraRepository.buildTree` and would not compile; and lint's
`MissingPermission` on `NotificationWorker.present` is a pre-existing
false positive - the guard is behind a helper and the post is inside
`runCatching`, neither of which lint's dataflow reads - now suppressed
with that reasoning recorded at the call site.

**Phase J/K:** `tests/test_settings_api.py` is now 70 tests. The new
ones enumerate every settings/provider route from the ASGI app and
assert each refuses an unauthenticated call, assert no allow-list path is
credential material (matched on the last dotted segment - a substring
test would call `llm.max_output_tokens` a token, and a check that cries
wolf gets deleted), assert `PATCH` with an `llm.api_key` is refused 422
and changes nothing, and assert no route logs the key including on the
rejection paths.

**Verified:** backend `1550 passed, 1 deselected`; Android
`132 tests, 0 failures`; `lintDebug` 0 errors, 44 warnings (39 are
"newer dependency available" noise, the rest pre-existing: 3
ObsoleteSdkInt, 1 InsecureBaseConfiguration in the debug-only network
config, 1 ConstantLocale).

**Found during the Phase L doc sweep, not fixed** (both recorded in
`docs/IMPLEMENTATION_STATUS.md` Known Limitations and
`.claude/project-state.md`):
1. `docs/SECURITY.md` and `docs/API.md` claimed `server.screen.enabled`
   defaults to false. The *code* default is false; the committed
   `config.yaml` sets it to true. Docs corrected to say both.
2. The Phase 7 build-artifact cleanup regressed. ~2100 files under
   `android/app/build/` and `android/.gradle/` are tracked again from
   commits 4ba906e/1fe3368 - `.gitignore` does not apply to paths already
   in the index. Needs `git rm -r --cached` in a commit of its own; left
   for Phase 10 rather than mixed into this one.
   *Resolved in `35589a0` itself* (2183 files, 61256 deletions), which is
   the commit this section describes - so the "left for Phase 10" note
   was already stale when written. Phase 10 verified `git ls-files` returns
   zero paths under those directories and removed three generated files
   the sweep missed.

**Not verified:** no live provider, no real device, no deploy. Every
claim is proven against `TestClient`, JVM unit tests and lint.

## Phase 10 - COMPLETE - Android <-> server settings contract

**The reported symptom** was Settings showing "Disconnected / unexpected
response (404)" while chat, `/api/health` and `/api/screen` all returned
200, and Render logged 404 for `/api/settings`, `/api/providers` and
`/api/providers/health`.

**Two causes, one of them ours.**

1. *Deployment skew.* Those three routes first exist in `35589a0`
   (Phase 9, current HEAD of `feature/aura-identity`). The deployed
   revision predates it. `main` has no `server/` directory at all, and
   `docs/DEPLOYMENT.md` told the operator to push there - corrected.
2. *One boolean carrying two facts.* `server.loaded` meant both "Aura
   answered" and "Aura gave me its settings", so a 404 from an optional
   route was rendered as a dead server. Replaced with `ServerReach`, a
   six-rung ladder (`Unknown < Unreachable < Connected < Authenticated <
   SettingsAvailable < ProviderHealthy`) where each rung is one observed
   request. `connected` is anchored to `Authenticated`, which is
   `/api/health` - itself behind `verify_token`, so one 200 proves
   reachability *and* the token. A missing settings API now reads
   "Connected / Settings unavailable".

**Eight settings promoted from read-only to settable**, each classified
from its read site rather than guessed: `server.screen.min_interval`,
`tools.enabled/auto_approve/timeout`, `voice.tts.provider/voice/volume/`
`playback`. Three are subsystem-conditional - the vision manager, the
tool executor, the TTS provider - so `SettingsService.apply` calls one
handler per group and a `False` demotes the path from `applied` to
`restart_required`. `applied` stays a promise.

**Deliberately still not settable:** `tools.allowed`,
`tools.allowed_paths`, `tools.applications`. A bearer token is enough to
change a setting; it is not enough to hand a remote client a new verb on
the host.

**Three real bugs found, two of them by doing the live check.**

1. `ToolPolicy.from_config` reads `config.get("auto_approve") or
   ["safe"]`, so an empty list collapses to auto-approving safe tools -
   a UI that let the user clear the list would have silently *widened*
   permission. `_risk_levels` now rejects `[]`, and `ToolsSection` locks
   the last enabled switch.
2. `/api/health` returned **500** with no provider key. `health_status()`
   called `active_chain()` bare, and that property builds the provider on
   first access, which raises when the key is missing. So the one route
   the phone treats as proof of life failed for the exact user the
   Control Hub exists for - and the phone sent them to the connection
   screen to retype a working token. Now guarded by
   `_provider_chain_label()`, mirroring what `readiness_status()` already
   did, reporting `"unavailable (ValueError)"` - the type name only,
   because a provider's exception can quote the key it was rejected for.
3. `ServerRuntime.config` is materialised once in the constructor, but is
   also what every *report* reads. A successful PATCH persisted the
   value and applied it live (every `_reapply_*` handler reads
   `load_config()` fresh) and then answered the next GET with the old
   one - so the switch the user just moved sprang back while the server
   used the new value. `SettingsService.refresh_config()` re-merges the
   snapshot after every overlay write; the reset route calls it too.
   Nothing in the old suite caught this, because those tests assert on
   `overrides` and on the PATCH report, both of which were right.

**Precedence, now documented** (`docs/API.md`, `docs/SECURITY.md`) - two
chains that resolve in opposite directions. Settings:
`DEFAULT_CONFIG < config.yaml < runtime overlay`, and `load_config()`
reads no environment variable at all. Secrets: `.env < credential store`,
because `CredentialStore.apply()` pushes stored keys into the environment
at startup and after each write - a phone must be able to replace a key
the dashboard got wrong.

**Tests:** `tests/test_settings_contract.py` (78) plus
`SettingsContractTest.kt` (29) and `ServerReachTest.kt` (14) - 121 new.
The 4 read-after-write tests were confirmed to fail with the fix
disabled, so they test the defect rather than the code.

**Verified:** backend `1628 passed, 1 deselected`; Android
`175 tests, 0 failures` across 11 classes; `:app:assembleDebug BUILD
SUCCESSFUL` with a 19.6 MB APK; and a real uvicorn on `127.0.0.1:8123`
answering all four routes 200 authenticated / 401 unauthenticated, with
a full PATCH -> GET -> reset round-trip and 422s for an out-of-range
value and an off-allow-list path.

**Git hygiene:** the ~2139 build artifacts were already untracked in
`35589a0`; three generated files that predate the rule were still tracked
and are now removed from the index (kept on disk) - `android/`
`local.properties`, which carries the machine username in `sdk.dir`,
plus `source.properties` and `NOTICE.txt`.

## Phase 11 - Render startup, provider coverage, Hub redesign

### Render startup crash - FIXED, verified on a real 3.14 interpreter

**The reported crash**, at Render startup on Python 3.14.3:

```text
TypeError: descriptor '__getitem__' requires a 'typing.Union' object
but received a 'tuple'
```

traceback ending in `memory/models.py`, `class UserModelEntry(Base)`.

**It was not the annotation.** `Mapped[str | None]` is correct and stayed
correct. The defect was a *pairing* of two pinned things, and the
annotation was merely the first place the pairing was exercised.

Python 3.14 implements PEP 604 by making `typing.Union` an **alias of**
`types.UnionType` rather than a separate special form. So `Union` became a
class and `Union.__getitem__` became an unbound slot wrapper.
`sqlalchemy/util/typing.py` in 2.0.36 built unions as

```python
return cast(Any, Union).__getitem__(types)      # 2.0.36, line 478
```

which is a bound call on 3.13 and an unbound descriptor handed a tuple on
3.14. `_init_column_for_annotation` -> `de_optionalize_union_types` ->
`make_union_type` runs for every optional column, so the first
`Mapped[str | None]` in the metadata took the process down at import:
`UserModelEntry.last_confirmed_at`. That is why the traceback named a
class nobody had touched in months. 2.0.51 is `return Union[types]`.

**Proven, not inferred.** Reproduced the message directly on 3.14.6, then
restored 2.0.36's exact expression under SQLAlchemy 2.0.51 by monkeypatch
and got the reported traceback back, frame for frame, ending at
`UserModelEntry`. Removing the monkeypatch made it import cleanly.

**Root cause, two halves.** `requirements-server.txt` pinned
`sqlalchemy==2.0.36` and `docs/DEPLOYMENT.md` tells Render to build from
that file; *nothing pinned the interpreter*, so Render's native Python
runtime followed its own default, which had moved to 3.14. A pinned
dependency set under a floating interpreter is not a reproducible deploy.

**Fix - four lines of configuration and no architecture.**
`requirements-server.txt` pins `sqlalchemy==2.0.51` (the crash cannot
recur even if the interpreter pin is removed); `requirements.txt` replaces
bare `sqlalchemy` with `>=2.0.51`, because a bare line is what let a 3.14
interpreter resolve a 3.13-era release; new `.python-version` pins 3.12,
matching `Dockerfile`, so the two production paths run one interpreter.
`docs/DEPLOYMENT.md` §1a records the whole chain. No annotation was
changed, no dependency downgraded, no `type: ignore` added, no
SQLAlchemy typing disabled.

**Tests: `tests/test_deploy_startup.py` (15).** Split by what they can
actually catch, which is the point. `TestOptionalColumnsMap` covers the
mechanism on whatever interpreter is running - the three optional columns
stay nullable *and the required ones stay required* (a fix that made
everything nullable would pass a weaker test), the schema emits DDL, a
model defined inside the test exercises the scan live, and
`de_optionalize_union_types` is asserted by name so a recurrence reads as
"this function broke" rather than an unexplained ImportError.
`TestDeclaredPins` covers the *combination*, reading
`requirements.txt`, `requirements-server.txt`, `.python-version` and
`Dockerfile` as data: the pin must support 3.14, the floor must admit no
broken version, the two must agree, the interpreter must be pinned at
all, and the pin must match both the Docker base image and the
site-packages path the Dockerfile copies between stages. Those are
interpreter-independent on purpose - CI runs 3.11, so an assertion that
only fires on 3.14 would never run, which is exactly how this reached
production.

**Live validation on 3.14.** Booted `python -m server.main` under Python
**3.14.6** with SQLAlchemy 2.0.51: startup completed and all four routes
answered `200` authenticated / `401` unauthenticated -
`/api/health`, `/api/settings`, `/api/providers`, `/api/providers/health`.
That run had **no provider key**, so it also demonstrates §3 of the
mandate: `llm_provider` reported `unavailable`, `/api/providers/health`
returned 200 carrying `provider chain unavailable (ValueError)`, and
`/api/ready` correctly returned 503 - a dead provider does not make the
server look dead.

Backend after this step: **1642 passed, 1 skipped, 1 deselected**
(baseline 1628), with SQLAlchemy upgraded to the pinned 2.0.51 in the
development venv too - testing against a version the project declares too
old is the same mistake in miniature.

### Provider coverage - six providers added, one live bug found

The mandate's §6 names ten providers. Six of them - OpenAI, Anthropic,
Cerebras, xAI, DeepSeek, Qwen - could not be selected at all:
`_instantiate_provider` had branches for gemini, groq, mistral,
openrouter, ollama and mock, so `provider: openai` raised "could not be
initialized" and `PUT /api/providers/openai/key` returned 422. §6 also
says "do not hardcode fake support", which rules out the shortcut of
listing them in the capabilities table and leaving the router alone.

**Why a shared client rather than six more files like the existing four.**
`groq.py`, `mistral.py`, `openrouter.py` and `cerebras.py` were already
four near-identical OpenAI-compatible urllib clients. Adding six more of
the same would have made ten copies of the same request builder, and the
history of this repository says what happens then: `cerebras.py` was one
of those copies whose `generate` forgot to call `split_prompt`
(AURA-P2-003), which is why it shipped deliberately unregistered for six
phases. A copy that forgets one line is invisible until the system prompt
- carrying the Phase 4 device-action boundary - arrives as chat text.

So the split is now in the base class:

```text
brain/providers/http_chat.py         keys, timeouts, error taxonomy,
                                    ONE generate() that calls split_prompt
  -> openai_compatible.py           the OpenAI wire format + SSE stream
       -> openai.py cerebras.py xai.py deepseek.py qwen.py
  -> anthropic.py                   its own wire format, deliberately NOT
                                    an OpenAICompatibleProvider subclass
```

Each leaf is ~45 lines: name, label, env vars, default URL, default
model. No new dependency - urllib throughout, no `openai` or `anthropic`
package. `anthropic.py` sits beside `openai_compatible.py` rather than
under it because four things differ, not one: `x-api-key` instead of
`Authorization: Bearer`, an `anthropic-version` header, a top-level
`system` field instead of a system message, `max_tokens` required, and
content blocks instead of a string. Faking that through a subclass would
have meant overriding every method it inherited.

**Cerebras is registered now, and not by fixing its `generate`.** Its
`generate` was deleted; it inherits the base one. The defect is
structurally unreachable rather than corrected, and
`test_cerebras_cannot_regain_the_defect_that_unwired_it` asserts
`CerebrasProvider.generate is HttpChatProvider.generate` so a future
override fails the build. Its default model is unchanged from the
unregistered version, so registering it did not quietly also change which
model it asks for.

**Registration is a table, not six branches.** `HTTP_CHAT_PROVIDERS` in
`brain/router.py` maps name -> (module, class, `llm.*` model key) and is
the only place that names a module. `_instantiate_provider` gained one
generic branch after the five hand-written ones, which are byte-identical;
so are `gemini.py`, `groq.py`, `mistral.py`, `openrouter.py`, `ollama.py`.
`tests/test_provider_resolution.py` now asserts the registry is closed in
both directions - every name in `PROVIDER_KEYS` builds a real provider,
and a key alone still conjures nothing (`MYSTERYAI_API_KEY` builds no
provider) - because a half-registered provider reaches the phone as
"unknown provider" and reads as a typo by the operator.

**The bug found on the way, which had nothing to do with the six.**
`server/settings_service.test_provider` calls
`BrainRouter._instantiate_provider(name, config)` unbound. It was an
instance method, so `self` took the provider name, `config` was missing,
and it raised `TypeError` - which the route's `except Exception` reported
as `"not configured"`. **Every `POST /api/providers/test` has always
failed, for every provider, whether or not its key was present.** Nothing
failed loudly, because "not configured" is a plausible answer on a
deployment with no keys. It is a `@staticmethod` now (it used no `self`),
which also leaves the internal `self._instantiate_provider(...)` calls
working. Six tests in `TestProviderTestRoute` pin it.

While there, the same function's error reporting was corrected: it
answered `"unreachable"` for every failure including a bad key. It now
classifies `ProviderAuthError` -> "invalid api key",
`ProviderRateLimitError` -> "quota exhausted" or "rate limited" by
`is_account_limit`, `ProviderUnavailableError` -> "unreachable", and
anything unclassified -> "request failed" rather than claiming a network
fault it did not observe. Still category-only, never the provider's raw
text, so a key cannot leak through an error message.

**One bounded repair retry.** OpenAI's reasoning models reject
`max_tokens` (wanting `max_completion_tokens`) and reject an explicit
`temperature`. §7 requires a real Temperature control, so dropping the
field globally was not acceptable and neither was 400-ing. `_send`
retries at most once, and only for the field the provider itself named in
`error.param` - not by pattern-matching model names, which is how this
kind of code rots. `test_the_repair_is_attempted_at_most_once` refuses two
*different* fields in turn to prove the bound in the case where a second
repair would otherwise have been possible.

**What `GET /api/providers` publishes about a base URL.**
`PROVIDER_CAPABILITIES` now has 12 entries carrying `api_base`,
`api_key_env` and `model_setting`. `api_base` is the *default*, never the
effective value, and the override is reported as a boolean
(`api_base_overridden`) because some gateways carry a token in the query
string and this response renders on a phone. A test asserts the secret
query value is absent from `response.text`. Capabilities stay
per-implementation, not per-vendor: the six report `streaming: true`
because the shared class really implements `stream()`, and `vision: false`
because none is wired into `vision/cloud_processor.py`.

`mock` is handled inside `test_provider`, not taught to the router: a
`mock` *fallback* would answer every outage with a canned reply and hide
it, which §3 forbids.

**Five existing tests had to be rewritten, and were not weakened.**
`tests/test_settings_api.py` used `"deepseek"` as its stand-in for an
unsupported provider name. DeepSeek is supported now, so five tests
asserting a 422 quietly became five tests asserting that a supported
provider accepts a key. Replaced with `UNKNOWN_PROVIDER = "notaprovider"`
plus `test_the_placeholder_provider_name_is_really_unsupported`, which
checks the constant against the registry so the same rot cannot recur.

New: `brain/providers/{http_chat,openai_compatible,openai,anthropic,xai,
deepseek,qwen}.py`, `tests/test_cloud_providers.py` (9 sections).
Rewritten: `brain/providers/cerebras.py`.
Modified: `brain/router.py`, `core/config.py`, `core/settings_store.py`,
`server/routes/settings.py`, `server/settings_service.py`,
`tests/test_provider_resolution.py`, `tests/test_settings_api.py`,
`tests/test_settings_contract.py`.
Docs corrected, all four of which asserted things that are now false -
`.env.example` ("DEEPSEEK_API_KEY has no effect"), `docs/DEPLOYMENT.md`
("OpenAI: not yet wired"), `docs/FOLDER_STRUCTURE.md` ("cerebras.py
written, deliberately not registered"; "there was never an `openai`
branch"), `docs/IMPLEMENTATION_STATUS.md` (file counts, plus a new Known
Limitation stating the six are unverified against live APIs).

Backend after this step: **1752 passed, 1 skipped, 1 deselected** (+110).

**Not live-validated.** There is no key for any of the six here, so not
one has exchanged a request with its vendor. What is pinned is the request
Aura sends, which is where every historical provider bug in this
repository actually lived.

## Current

Phase 11 is complete: 11.1 (Render startup), 11.3 (provider coverage),
11.4 (Android Hub redesign) and 11.5 (suites, APK, state, commit). Both
suites are green as of this entry - backend **1752 passed, 1 skipped, 1
deselected**, Android **225 passed across 15 classes, 0 failures, 0
errors**. Committed per the mandate's §21 as `95ab4f1 Harden settings,
providers, Render startup, and Android UI` - 44 files, 5798 insertions,
470 deletions - and pushed to `origin/feature/aura-identity`; tree clean,
branch in sync. The debug APK is
`android/app/build/outputs/apk/debug/app-debug.apk`, 19,548,367 bytes,
with `:app:packageDebug` and `:app:assembleDebug` both executed rather
than UP-TO-DATE.

11.5 also closed a planned commit that turned out to be unnecessary. The
`android/app/build` + `android/.gradle` untracking three documents still
prescribed had already been done by `35589a0` itself (2139 files removed,
61183 deletions, no insertions), so `git ls-files` returns nothing under
either path at HEAD; the prescriptions were corrected rather than
re-executed.

11.4's real result was not the visuals. The app's most visible sentence
lived inside a `@Composable`, and this module has no JVM Compose harness
and no Robolectric, so it was also its least testable one. The verdict
logic now sits in pure Kotlin - `HubOverview.kt`, `ProviderSummary.kt`,
`AuraMotion.kt` - and the four new test classes (`HubOverviewTest` 18,
`ProviderSummaryTest` 16, `ModelSettingTest` 10, `AuraMotionTest` 5) are
what took the suite from 175 to 225. §16's regression - `/api/health`
200 + `/api/settings` 404 must read **Connected** - is an assertion now
rather than a paragraph.

Android lint was **not** re-run after the redesign. The last measured
figure is Phase 9's `0 errors, 44 warnings`, and it should not be quoted
as current. Nor was anything validated live: no device was attached, so
the APK was built and measured but never installed or run; no live
provider API was called; Render was not redeployed.

**The user's next action is still a redeploy.** Render must track
`feature/aura-identity` at `b5ec777` or later for the settings routes to
exist at all, and at `95ab4f1` for the SQLAlchemy pin that lets the
service boot on Python 3.14. Until then Aura reads "Connected /
Settings unavailable", which is now the truth rather than a bug.

## Phase 12 - Android Settings integration audit (COMPLETE, UNCOMMITTED)

The premise this phase started from was that the live server is fine.
Authenticated `GET /api/health`, `/api/settings`, `/api/providers` and
`/api/providers/health` all answer 200 on the current deployment, so the
Phase 11 reading - "the deployment predates the routes" - was true then
and is not the current contract. The phone was still saying **"This Aura
server does not expose settings"**. It was the client.

**Root cause.** The settings verdict was a boolean (`ServerState.loaded`)
plus a free-text `settingsProblem: String?`, and six separate sites
re-derived their own sentence from the boolean alone. Once `/api/health`
had returned 200, *every* later settings failure - a refused token, a
403, a 422, a rate limit, a 500, a cold-start 502, a read timeout, a body
this build cannot parse - rendered as the one sentence about a missing
endpoint. On a free-tier host that is reachable with no server bug at
all: health succeeds, the very next request meets a cold-start gateway
error, and the app tells the user to update a server that is current.

**Fix.** The verdict is typed and decided once.
`ServerState.settingsError: AuraError?` replaces the string, and the new
`ui/hub/SettingsAccess.kt` maps it to a 13-member `SettingsAccess` enum
carrying `label` / `reason` / `headline` / `tone` / `retryable`.
`HubUiState.settingsAccess` is the single source; `lockedReason`,
`hubHeadline`, `hubBanner`, `AuraSection`, `ConnectionSection` and
`DiagnosticsSection` all read it instead of composing a sentence.
"Does not expose settings" is now reachable from `NotExposed` alone,
which is 404/405 and nothing else. Three supporting mismappings went
with it: a 2xx with an empty body became `ServerFailure(200)` -> "an
unexpected response (200)", now `Incompatible("empty body")`; a
`SerializationException` is a `RuntimeException` and fell into
`AuraError.Unknown`, now caught before the generic clause as
`Incompatible("unreadable body")` with its message dropped, because a
parse message quotes the JSON it choked on; and both provider routes
were `if (result is AuraResult.Ok)` with no else, so a 500 left the
section blank with no statement of why - now `providersError`.

Status mapping as shipped: 200+valid -> Available; 404/405 -> NotExposed;
401 -> AuthRequired; 403 -> Forbidden; 422 -> Refused (with the server's
own message, from `detail.message` only - FastAPI's pydantic list is
discarded); 429 -> RateLimited; 500/503-from-Aura -> ServerError;
502/503/504 bare -> Waking; timeout/no route -> Network; unparseable ->
Incompatible; anything unattributed -> Unexplained.

**Proof, from the server's own bytes.** The audit's first hypothesis - a
null in the live payload defeating a `Json` without `coerceInputValues` -
was disproved by dumping the real document: the only nulls are
`effective.avatar.position` and `effective.voice.microphone.device`, both
in sections the DTOs do not declare, so `ignoreUnknownKeys` drops them
and the live 200 parses cleanly (13 `effective` sections, 42
`configurable` paths, `overrides: {}`). Those bodies are checked in as
`android/app/src/test/resources/live/{settings,providers,provider_health}.json`
and `SettingsContractTest` parses them with the app's own DTOs - the
strongest contract test available, a payload nobody retyped.
**Provenance, stated plainly:** they are the current server build's route
output captured through `tests/test_settings_api.py`'s FastAPI
`TestClient`, not a network capture of the Render host, so every cloud
provider reads `configured: false` and the chain reports `provider chain
unavailable (ValueError)`. `tests/test_settings_fixture.py` (4 tests)
keeps them honest from the Python side, comparing *shape* both ways plus
the exact `configurable` list and the exact provider->`model_setting`
map, and asserting nothing key-shaped is in them. Regenerate with
`AURA_WRITE_ANDROID_FIXTURES=1`.

**`DeviceSettings`, and why it is not a widening.** `HubViewModel` took a
concrete `SettingsStore`, which needs a `Context` and a Keystore-backed
key, so it could not be constructed on the JVM and the hub had no
ViewModel-level test at all. `SettingsProvider`'s KDoc deliberately says
it is read-only, so the mutators were not added there; the new
`data/settings/DeviceSettings.kt` extends it with the five device
mutators the hub genuinely needs, `SettingsStore` implements it, and
`FakeSettings` supplies them through one backing `MutableStateFlow` so a
collector cannot be told a different story from a direct reader.

**Tests.** Android **273 passed across 17 classes** (from 225/15).
`HubViewModelTest` (18) is new and drives the whole path - `/api/health`,
`/api/settings`, `/api/providers`, `/api/providers/health`, `PATCH` -
against MockWebServer on loopback, asserting on `HubUiState`: the six
failure codes each keeping their own identity, a 404 being the only one
allowed to name a missing route, a truncated body reading as a version
mismatch and never leaking the parser's message, a save showing the
server's clamped 0.9 rather than the 1.4 that was sent, a 422 quoting
the server's own sentence and changing nothing, restart-required and
non-persistent saves, a reload proving the GET agrees, the model picker
writing `llm.anthropic_model` rather than Gemini's `llm.model`, a
provider 500 recorded while settings stay Available, a path missing from
`configurable` locking with its own reason, the capability paths
(`tools.allowed`, `tools.allowed_paths`, `tools.applications`) locked
because the server never lists them, and a device toggle sending no
PATCH. `SettingsAccessTest` (12) pins the wording. `SettingsContractTest`
grew from 33 to 42. Backend **1756 passed, 1 skipped, 1 deselected**.

**Tests strengthened, not weakened.** Three pre-existing assertions of
`403 -> Unauthorized` were rewritten to `Forbidden` - the mandate's own
401/403 split, and both messages still name the token. `ServerReachTest`'s
"that server explains itself" fixture passed `settingsProblem = null`
while asserting the 404 wording, which the typed field cannot express;
it now passes `settingsError = AuraError.NotSupported`.

**UI pass, on the existing tokens.** No new design system, no GSAP, no
WebView, no dependency. `SettingsCard`, `NoticeCard` and `ProviderCard`
now carry the hub's own `auraGlassEdge` hairline, so a settings card
reads as the same material as the front page's tiles instead of a flat
tonal block; and the three literal durations left in
`SettingsComponents.kt` (150/180/120) and two in `ProviderComponents.kt`
(160/120) are now `AuraMotion.scaled(Quick|Standard,
rememberReducedMotion())`, which also makes them honour "Remove
animations" - they did not before.

**Build.** `.\gradlew.bat clean assembleDebug` ->
`android/app/build/outputs/apk/debug/app-debug.apk`, **19,323,605 bytes,
2026-08-12 13:27:08 +0700**, with `:app:packageDebug` and
`:app:assembleDebug` executed after a real `clean`. `git diff --check`
clean.

**Not done.** No device was attached, so the APK was measured and never
installed. No live provider was called and Render was not redeployed, so
the fix is proven against loopback and the server's own captured bytes
rather than against the deployment. Android lint was not re-run. Nothing
is committed - held for approval per the mandate.

## Blockers

- The Bash permission classifier was intermittently unavailable during
  Phase 0 and again during Phase 3, which delayed test runs. Not a code
  problem.

## Post-Phase-12 - "Aura got dumber after the model change" (uncommitted)

Traced the production chat path only: `POST /api/chat` ->
`ServerRuntime.chat` -> `ChatEngine` -> `ConversationManager.chat` ->
`PromptBuilder.build` -> `split_prompt` -> `BrainRouter` ->
`GeminiProvider.generate`, plus `_resolve_tools` and the Android agent
loop in `AuraAccessibilityService`. Cleared by inspection, not assumed:
the section headers `PromptBuilder` emits all match `split_prompt`'s
regex and land in the right slot (11208 chars of system instruction,
265 of user content on a real turn); memory recall is bounded and enters
as `MEMORY` in the user slot where it cannot outrank the current
message; the tool catalogue and results are two sections on purpose;
`_resolve_tools` bounds itself three ways and always ends on a
tool-free round; machine turns are correctly isolated from style,
memory and the bus.

**Root cause: `llm.max_output_tokens: 768` was sized for a non-thinking
model, and nothing told `gemini-3.6-flash` not to think.** Gemini 3
bills hidden reasoning against the output budget. Measured against the
live API on prompts built by the real `PromptBuilder`:

- "compare sqlite and postgres..." -> MAX_TOKENS, 686 thought tokens,
  78 answer tokens, reply cut mid-sentence.
- "walk me through debugging a memory leak..." -> MAX_TOKENS, 705
  thought, 59 answer.
- a 15-node agent tick -> 738 thought + 22 answer = 760 of 768. One
  token from the cliff, which is why a real accessibility tree crossed
  it: the truncated JSON failed `AgentActionParser`, `last_action_error`
  fed it back, and the step budget ran out as "Task timed out: maximum
  number of steps reached". The agent loop itself is not at fault - it
  was retrying an unparseable reply, exactly as designed.

**And the pipeline could not see any of it.** `response.text or ""`
discards `finish_reason`, so a reply cut off after four words and one
that finished in four words were the same string, and a completion with
no visible text at all was returned as a success: saved to the
transcript, published as `ResponseEvent`, and never offered to
`FallbackProvider`.

Fixed, three files:

- `brain/providers/gemini.py`: `_request_config()` (shared by `generate`
  and `stream`) sends `thinking_config` from the new setting;
  `_check_truncation()` reads `finish_reason` and either warns (partial
  text kept - half an answer beats none) or raises
  `ProviderUnavailableError` (no text - a real outage, and the chain is
  entitled to try the next provider). `stream` gets the thinking level
  but still no `max_output_tokens`, deliberately: a streamed reply is
  already on screen when a budget would run out.
- `core/config.py` + `config.yaml`: `llm.thinking_level`, default
  `"low"`. `""` sends nothing, so a provider without the knob is
  unaffected.

`tests/test_gemini_thinking_budget.py` (8 tests, no network): the level
reaches the request, `""` sends no `thinking_config` at all, streaming
sends the level and no budget, a truncated reply is kept and logged with
the numbers, an empty truncated reply raises, a blocked `STOP` still
normalises to `""`, a response with no candidates is not an error, and
the *shipped* config bounds the thinking (or the fix could be absent
from the tree and the suite still pass).

Verified live after the fix: the same two questions return 1617 and 2503
characters ending in complete sentences; a 60-node agent tick (11731
chars) returns clean JSON; the intent probe still answers one word.
Backend **1764 passed, 1 skipped, 1 deselected**. Android **273 passed
across 17 classes, 0 failures**.
`android/app/src/test/resources/live/settings.json` regenerated through
`AURA_WRITE_ANDROID_FIXTURES=1` for the one new field.

Not done: not committed. `llm.thinking_level` is absent from
`core/settings_store.py`'s allow-list, so the phone cannot change it.

## Post-Phase-12 - pre-test repository bug sweep (uncommitted)

One audit pass over the runtime-critical paths, asked for before the next
real-device test so the test would not be spent on a software bug.

**One confirmed bug, fixed.** `POST /api/chat` - the route the Android app
uses for both normal chat and every agent tick - was `async def` and called
the synchronous `runtime.chat()` inline, holding the single ASGI event loop
for the whole model call. Now `await run_in_threadpool(runtime.chat, ...)`.
`server/routes/ws_chat.py` already offloaded its own path through
`iterate_in_threadpool`, which is what identifies this as an oversight.
`tests/test_server.py::test_chat_does_not_run_on_the_event_loop` asserts
from inside the call that `asyncio.get_running_loop()` raises; verified to
fail with the offload removed and pass with it.

**Four findings reported rather than changed**, recorded in
`.claude/project-state.md` under "Pre-test sweep findings": Android
screenshot upload has no caller (so Vision is server-complete and
device-unwired, degrading to an absent VISION section rather than a false
claim); `llm.timeout` is ignored by `GeminiProvider`; a mid-chain
`is_account_limit` aborts the remaining, unrelated providers and is pinned
by an existing test; and `stream_of` finds no `stream` on
`FallbackProvider`, so streaming silently becomes a single chunk whenever a
chain initialises.

**Cleared by inspection**, in case the same suspicion returns: the
`_resolve_tools` bound (three ways, always ending on a tool-free round);
`AgentActionParser` (brace-matched extraction, fence stripping, unknown
actions returned as correctable failures, `MAX_PARSE_FAILURES = 3` reset by
any readable reply); `executeActionWithRecovery` (bounded at 2 attempts,
nodes re-resolved from a fresh root so the recycled `nodeMap` is not a
use-after-free); `verifyOpenApp`'s identity check; `tools/timeout.py`;
`db_lock`; memory recall bounded at 12 lines with per-source failure
isolation; and the empty-`except Exception` sweep (7 sites, all
event-publish, socket-close or SSE-line-skip).

Backend after the fix: **1765 passed, 1 skipped, 1 deselected**. No Android
source changed, so no Gradle run. `git diff --check` clean. Two fixture
files that showed modified on line endings only were restored with
`git checkout --`.

Not done: not committed, not pushed.

### Vision production wiring (1766 passed, 1 skipped, 1 deselected; Android 292 across 19 classes)

The first of the four reported findings, now fixed: the server-side Vision
pipeline was complete and no Android production code ever called it.

**Root cause: an absent caller.** `AuraRepository.uploadScreenshot()`,
`POST /api/screen/upload`, `RemoteScreenSource`, `VisionManager` and
`CloudVisionProcessor` all worked. Nothing on the phone invoked them, and
`AccessibilitySnapshot.screenshotAvailable` was the literal `false`. The
whole suite passed throughout, because "no caller exists" is not a wrong
result - which is why the regression pinned here is structural.

**Mechanism.** `AccessibilityService.takeScreenshot(displayId, Executor,
TakeScreenshotCallback)`, API 30, verified against
`platforms/android-35/data/api-versions.xml` - and not MediaProjection,
which would have been the second screenshot mechanism the brief rules out.
Both Aura services already *are* accessibility services with the grant.
`CAPABILITY_CAN_TAKE_SCREENSHOT` is also API 30, and the framework refuses
the call unless the service XML declares it, so
`android:canTakeScreenshot="true"` was added to
`accessibility_service_config.xml` and `aura_accessibility_service.xml`.

**New `screen/ScreenshotCapture.kt`.** The `ScreenshotCapture` interface is
the seam a JVM test can reach; `AccessibilityScreenshotCapture` is the half
it cannot. `Bitmap.wrapHardwareBuffer` returns a HARDWARE bitmap a software
canvas refuses to draw, so it is copied to `ARGB_8888` and the
`HardwareBuffer` closed; downscale mirrors `vision.max_pixels =
1_500_000` so no pixels are sent that the server would discard; JPEG q80.
`suspendCancellableCoroutine` for the framework callback,
`Dispatchers.Default` for the encode. A `SecurityException` from a
service that is not currently bound is reported, not thrown.

**New `screen/ScreenshotUploader.kt`.** The single place both services ask
"should a screenshot be sent". `screenObservationEnabled` first, because
the Privacy screen promises "Screen text is off, so nothing is sent at
all"; then `uploadScreenshots`, `isConfigured`, `isSupported`, then an 8 s
interval. The interval is `server.screen.min_interval` - the server would
not look at a faster frame - and it is stamped on every *attempt*, so a
down server does not cost a full-screen encode per accessibility event.
Outcomes are `Sent` / `Skipped(reason)` / `Failed(reason, error?)`; the
temp file is deleted in a `finally`.

**Ordering is a server constraint.** `RemoteScreenSource` is one
last-write-wins slot and `/api/screen/upload` submits a *frame-only*
observation, so pixels must go after the text POST. Reversed, the frame is
replaced by a frameless observation and `describe()` returns `""` - the
exact silent-nothing this fix exists to end.

**`screenshotAvailable` is this tick's upload outcome**, `outcome is
Sent` and nothing weaker. A skip and a failure both read as no screenshot.
That was chosen over "this build can capture" because the field is a claim
about what the server is holding.

**One server change.** `upload_screenshot` awaited synchronous
`runtime.observe_screen` inline in an `async def`; with a frame attached
that reaches a real VLM request on the single event loop - the same defect
`/api/chat` had, and unreachable until a phone actually uploaded pixels.
Now `await run_in_threadpool(...)`. `/api/screen` carries no frame and
stays on the loop. No other Vision file touched.

**Tests.** `ScreenshotUploaderTest` (16, MockWebServer on loopback,
`TemporaryFolder` cache, injected clock): the multipart POST reaches
`/api/screen/upload` with all five form parts, the bearer token and
`image/jpeg`; the captured bytes are in the body; the cache is left empty;
each of the four gates skips *without capturing*; the interval holds and
then releases; a failed attempt still costs it; null/empty capture is
`Failed` with no `AuraError` and zero requests; a 503 `screen_disabled`
carries `AuraError.Unavailable`; 415 is reported not retried; an
unreachable server leaves nothing on disk. `ScreenshotWiringTest` (4) pins
the property that was false - both services *declare* a
`ScreenshotUploader` - plus the availability rule and the wire field.
Backend: one test asserting from inside `observe_screen` that no loop is
running on that thread.

**Two things learned the hard way.** `Json.Default` has
`encodeDefaults = false`, so `screenshotAvailable = false` is an *omitted*
key, not an explicit `false` - the test now pins absence-means-false as
the contract instead of asserting what I assumed. And inserting the new
backend test silently ate the four assertions of
`test_upload_screenshot_route_success`, leaving it passing with none;
caught in the final diff read and restored, so that file's diff is now
60 insertions, 0 deletions.

Not done: not committed, not pushed. No device attached, so nothing ran on
hardware. API 26-29 cannot capture. Two services each carry their own 8 s
clock, so a fast app switch across both can produce two uploads inside one
server interval - the server keeps the last. `screenshot_available` is
still read nowhere in Python. `VisionManager`'s own 8 s throttle means an
uploaded frame is usually described on the next turn, not the one that
sent it.

## open_app was structurally impossible (uncommitted)

`mở YouTube` → `Task timed out: maximum number of steps reached.`, with
all ten steps returning `POST /api/chat 200`. The 200s were real: every
tick reached the model and parsed a valid action. The failure was on the
phone, one line further on.

`getLaunchIntentForPackage` resolves the target's MAIN/LAUNCHER activity,
which makes it a *package query*, and since API 30 queries are filtered to
what the manifest declares. This manifest declared nothing, `targetSdk` is
35, so the call returned `null` for an app that was installed with the
package name spelled correctly. `open_app` returned `false` on both
recovery attempts; ten steps, and `startActivity` was never reached.

**The provider was a red herring.** Gemini 429, Groq 403, Mistral
selected - all concurrent, none causal. Gemini would have failed the same
way. Worth remembering next time the logs are loud: `/api/chat 200` says
the model call worked and nothing about whether the device did.

**Verified rather than assumed the load-bearing fact.** I suspected an
`AccessibilityService` might be exempt from visibility filtering, which
would have made the whole diagnosis wrong. The Android docs say it is not
exempt, and separately that starting another app's activity is allowed
*regardless* of visibility - which is precisely why `startActivity` was
never the problem and only the query was.

**Two changes.** A `<queries>` MAIN/LAUNCHER block, chosen over
`QUERY_ALL_PACKAGES` because it is exactly the question `open_app` asks
and needs no Play policy declaration. And `failureReason`, because the old
`last_action_error` read "Action open_app on null failed. Target not
clickable or not found." - false twice over for an action with no target
node, and naming neither the package tried nor why it failed. A model
given that can only guess again, which is the other way ten steps
disappear.

**The test caught a flaw in itself, twice.** First it matched
`QUERY_ALL_PACKAGES` inside my own explanatory manifest comment - fixed by
stripping XML comments, which also stops a commented-out `<queries>` block
from satisfying the positive assertion. Then, checking that it failed
before the fix, Gradle reported `BUILD SUCCESSFUL` with the `<queries>`
block deleted: the manifest is read at runtime, so it was not a declared
task input and the test had gone stale. `app/build.gradle.kts` now
declares it. Both were only found by trying to make the test fail rather
than trusting it to pass.

297 Android tests / 20 classes, 0 failures (was 292 / 19). 44 backend
agent tests pass; no Python changed.

Not done: not committed, not pushed. A launch has not been observed on
hardware - the structural cause is fixed and proven, the device
confirmation is still owed. `failureReason` improves the model's chance of
self-correcting a wrong package name but does not guarantee it: the agent
prompt still ships no installed-app list, so the first guess is always
world knowledge.

## Persona contract wired into the prompt pipeline (uncommitted)

The personality-overhaul brief's highest-leverage piece - `brain/persona.py`,
a fully written 978-line pronoun-register / context-mode / dials /
addressing-preference engine - was dead code. `PERSONA` was imported by
`prompt_builder.py` but never emitted, and no module referenced the persona
layer, so pronoun coherence, mode-appropriate intensity and explicit
addressing preferences were left to whichever model happened to be active.
Wired it in following the exact `style`/`identity` pattern:

- `PromptBuilder._build_persona` + `persona` param; the section sits
  directly under PERSONALITY, so it is in the system slot for every provider
  (`split_prompt` and `split_prompt_to_messages` both gained the header -
  required, or the Anthropic/OpenAI-compatible adapters would have received
  the contract as user content instead of instructions).
- `ConversationManager.persona` collaborator; `_compose` passes
  `render_of(self.persona, persona_of(self.persona, history, user_msg))` -
  the same defensive-reader shape as `anchor_of`/`hint_of`.
- `ChatEngine.persona`, defaulting to `build_persona(personality.persona)`,
  and a `personality.persona` section in `DEFAULT_CONFIG` (enabled,
  `pronoun_style` pin, optional humour/brainrot ceilings read as caps in
  every mode).
- The agent `complete` message now uses `AGENT_VOICE` - the one place
  personality is allowed inside the agent prompt.
- The `REVISION` marker's comment fixed: `brain/persona_guard.py` never
  existed, and validation is deliberately deferred (brief Section 22), so
  nothing emits it.

The design guarantee: the register is derived from the transcript, so a
fallback provider handed the same history resolves to the same pronoun
style and mode - nothing to copy over, nothing to forget to copy. One
instance serves every session because `resolve` is a pure function of the
turn, not stored state. No model/provider-specific branches anywhere - the
whole point of the brief.

33 new tests in `tests/test_persona.py` (reading register/mode, resolution
precedence, preferences, render, config, system-slot placement, and a
Gemini->Groq->Mistral fallback capturing the same contract verbatim). Full
Python suite: **1811 passed, 1 skipped, 1 deselected, 0 failed** (was 1809
before this work; 2 pre-existing settings-fixture failures fixed by
regenerating the fixture).

Fixture work: `live/settings.json` regenerated with the persona block to
keep `test_fixtures_match_the_routes` honest. The regeneration also
rewrote `providers.json`/`provider_health.json` with *this machine's* env
state (GEMINI key present, masked) - reverted, because the test compares
shape not values and committing a masked key fragment would be noise.
Android `SettingsContractTest` (42 tests) re-run against the new fixture: 0
failures. The DTOs drop `personality` via `ignoreUnknownKeys` (documented
in ControlDto.kt), so no Kotlin change was needed.

Not done, per the mandate: no persona guard / second generation pass, no
changes to providers, fallback chain, memory, tools, Android execution,
`prompts/personality.md`, `brain/style.py` or `brain/consistency.py`. The
unverifiable-by-unit-test question remains: whether models actually follow
the register line needs a real model and a real conversation.
