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

## open_app verification race (uncommitted)

Field evidence ("mở youtube"): step 2 launched YouTube at the
ActivityTaskManager level (`Displayed ...Shell$HomeActivity: +898ms`),
but Aura verified 250ms after startActivity returned, read the
still-previous package (`Post-action state: package=com.aura.companion`),
reported `open_app verification FAILED: expected=...youtube, got=...companion`
-> UNVERIFIED -> step 3 re-issued the exact same open_app -> duplicate
launch; the second attempt verified only because YouTube had finished
drawing by then.

Confirmed by inspection, not assumed: `executeActionWithRecovery` in
AuraAccessibilityService.kt executed the launch via the executor's
open_app branch (`getLaunchIntentForPackage` + `startActivity`), then a
fixed `delay(250)`, then a SINGLE `rootInActiveWindow` snapshot compared
by `verifyOpenApp`'s identity check. There was no polling and no bounded
wait for open_app; the generic settle was simply shorter than the
launch. This is the same class of premature-verification bug the
earlier click-verification work addressed, specific to the app-launch
window transition.

The fix (smallest robust): bounded eventual verification for open_app
only, in the existing coroutine model (no new main-thread blocking -
the loop already runs on Dispatchers.Main with suspend `delay`).

- `waitForForegroundPackage(target, timeoutMs=2500, intervalMs=150)`
  companion helper: polls `rootInActiveWindow?.packageName` until it IS
  the target or the budget runs out. Clock and sleep injected (defaults:
  `System.currentTimeMillis`, `delay`) so JVM tests drive timing
  deterministically. Logs per-sample + VERIFIED/TIMEOUT lines matching
  the requested format (attempt + elapsed), ~16 lines worst case.
- `verifyActionOutcome(action, pre)` replaces the inline generic
  verification: open_app with a target routes to the poll; every other
  action keeps the original delay(250) + single snapshot + verifyStateChange.
- Already-foreground short-circuit placed AFTER the SafetyGuard check
  and BEFORE `startActivity`: pre-action package == target -> Verified,
  no relaunch. (Before the guard it would have let a blocked package
  already in the foreground bypass safety.)
- Blank target falls through to the old any-package-change path, so the
  no-target semantics of verifyOpenApp are unchanged.

Deliberately NOT changed: maxSteps, step timeout, verifyOpenApp identity
rule (success still means the target package is actually foreground),
click/type/scroll verification, the executor, the backend, the manifest
`<queries>` fix from the previous session.

Tests: `OpenAppForegroundPollingTest` (5 new) with a fake clock/sleep:
already-foreground -> success in 1 sample with 0 elapsed; delayed
arrival at 500ms -> eventual success, no mid-launch failure; never
arrives -> bounded timeout (2000..2100ms fake clock); blank target ->
zero polling; slow launch at 2.2s within a 2.5s budget -> success.
Full Android unit suite: **308 tests, 0 failures, 0 errors, 0 skipped**.
`assembleDebug` builds. Not installed/reproduced on hardware - no phone
attached this session; the ADB reproduction from the bug report is the
device validation still owed.

## open_app race fix — log alignment (uncommitted, same work item)

The open_app bounded-polling fix from the previous entry was re-verified
against a re-issued spec and needed no functional change - placement
(after SafetyGuard, before startActivity), constants (2500/150),
injectable clock/sleep and all five polling tests already matched. Only
the three log lines inside `waitForForegroundPackage` were aligned to
the spec's wording: `open_app verification poll: ... current=...`,
`open_app foreground verification VERIFIED: ... current=... elapsed=...`,
and `open_app foreground verification TIMEOUT: ... current=... elapsed=...`
(the timeout line now reports the last sampled package under `current=`
rather than `lastPackage=`). Full Android unit suite still 308/0/0/0;
assembleDebug builds.

## Agent lifecycle audit: exactly-once completion guarantee (uncommitted)

Full audit of the Accessibility agent lifecycle (startAgentLoop ->
executeActionWithRecovery -> verification -> onComplete -> ChatViewModel
isSending). Verified NON-bugs: single active loop (agentJob?.cancel() +
Main dispatcher + isSending guard); no step after settled (every
settled=true is followed by break, timeout gated on !settled, onComplete
called once per invocation); repository.send cannot throw
(AuraRepository.call catches Timeout/Offline/Incompatible/Unknown ->
AuraResult.Failed); ScreenshotUploader.upload documented never-throws;
onAccessibilityEvent empty; ScreenObservationService unrelated; no UI
stop button exists (stopAgentTask is dead code - only onInterrupt and
onDestroy stop the loop).

PROVEN BUG (HIGH): the loop's onComplete(finalMessage) was the only
delivery of the completion callback, called at the end of the launch
block with NO try/finally. Any cancellation (onInterrupt ->
stopAgentLoop -> agentJob?.cancel(); onDestroy -> scope.cancel(); a
newer startAgentTask) or any crash outside AuraResult handling unwound
the coroutine and SKIPPED onComplete. ChatViewModel sets isSending=true
and only the agent callback clears it, so a cancelled/crashed loop left
the spinner spinning forever and lost the task's final message - the
exact "Task completed then spinner stays" / "completion never
propagated" symptom.

FIX: extracted the loop body into `private suspend fun runAgentSteps(
request): String` and routed every exit through a new companion
`runWithGuaranteedCompletion(block, onComplete, stoppedMessage,
crashedMessage)` - try/catch/finally where onComplete fires exactly once
from the finally (normal message, crashedMessage on a crash,
stoppedMessage on CancellationException which is then rethrown so the
Job stays cancelled). `startAgentLoop` is now a thin wrapper. The loop
body itself is byte-identical (verified via git diff -w).

Tests: new AgentLifecycleTest (4): normal completion delivers once with
the right message; crash delivers crashedMessage once without rethrow;
cancellation delivers stoppedMessage once and the CancellationException
propagates (job stays cancelled); exactly-once counter. OpenApp polling
(5) and identity (4) tests untouched. Full Android unit suite 312 tests,
0 failures, 0 errors, 0 skipped; assembleDebug builds.

Residual risks (documented, not code): (1) a loop cancelled before its
body starts (theoretical - no code path cancels a just-dispatched loop)
never runs the finally; (2) the completion callback still references the
ViewModel that started the task - if that ViewModel is destroyed the
reply is delivered to a dead state (no crash, and a fresh ViewModel
starts with isSending=false, so no stuck spinner); (3) one in-flight
action can execute when a newer task cancels the old loop (bounded to a
single action; the isSending guard prevents same-ViewModel overlap).
Hardware validation (install APK, reproduce on-device, check logcat for
onComplete on stop) still owed - no device attached this session.

## 2026-08-23 - Provider failure isolation (Phase F increment)

### The named production bug, reproduced and fixed

The modernization mandate names one concrete failure: entering an
Anthropic API key "through AgentRouter" made Aura stop functioning.
Reproduced from a minimal case and traced to `BrainRouter._create_provider`,
where the primary and the fallbacks were built by two different rules:

- every fallback was wrapped in try/except, carrying a comment that an
  optional provider must not be allowed to become a mandatory one
- the primary was built inline and a `None` result raised `ValueError`

So the provider the operator merely *selected* was fatal, while the one
they configured as a backup was survivable. With primary `anthropic`
unkeyed and `groq` + `mistral` both healthy, every single message failed -
the healthy chain was never reached, because the lazy `provider` property
raised before it was assembled.

Both paths now go through `BrainRouter._build(name, config)`, which never
raises and returns `(provider | None, reason)`. The chain is whatever
survives:

- primary dead, fallbacks healthy -> Aura answers, ERROR log naming the
  provider and the reason, `llm.provider` untouched
- primary dead, nothing else -> still fatal, but the message now carries
  the real reason from `_skip_reason` instead of "missing API key or config"
- the "no failover configured" warning now names `provider_names[0]`, so a
  dead primary stops pointing the reader at the wrong provider's key

`provider_name` still reports what the operator chose; `active_chain()`
reports what is actually answering. Nothing rewrites the setting.

### A second defect, found by the suite and fixed

`test_settings_contract.py::TestHealthSurvivesADeadProvider::
test_the_provider_error_message_is_not_in_the_response` asserted
`"401" not in response.text` across the entire health body. But
`runtime.uptime_seconds` is an unrounded `time.time()` delta, so the digits
"401" land in its decimals for roughly 1.2% of responses (measured 1230 in
100000 over realistic uptimes; e.g. 7.330945879406765). It failed once in
four runs here, for a reason unrelated to any secret leak.

Now scoped to the field that would actually carry a status code:
`runtime.llm_provider == "unavailable (ValueError)"`. Verified to be
*stronger* than the substring check it replaced by injecting a deliberate
leak into `server/runtime._provider_chain_label` and confirming the test
caught it, then restoring the file byte-for-byte.

### Verification actually performed

- backend: **1817 passed, 1 skipped, 1 deselected**, four consecutive full
  runs (baseline 1811/1/1; the 6 new tests account for the delta)
- the 6 new tests were each confirmed to FAIL against HEAD's router before
  passing - reverted `brain/router.py`, ran them, watched all 6 fail on the
  old `raise`, then restored the fix
- Android: 312 tests / 0 failures, forced to re-run rather than trusting an
  UP-TO-DATE gradle task (the first invocation re-ran nothing; its results
  on disk were nine days old)
- NOT done: no device attached, no live provider API called, no APK built

Diff: `brain/router.py` (+100/-25), `tests/test_provider_resolution.py`
(+160), `tests/test_settings_contract.py` (+15/-1).

### Audit findings recorded but deliberately NOT implemented

The mandate's own bug report names a component that does not exist:

1. **AgentRouter is absent from the repository** - zero matches, any case,
   any file type. So the bug cannot be "restored"; the concept was never
   built. The structural cause is that `PROVIDER_KEYS` maps a provider to
   one env var 1:1 while each provider hard-codes its own `default_url`.
   A proxy key pasted into the Anthropic slot is therefore shipped to
   `api.anthropic.com`, which rejects it - the key is fine, the
   destination is wrong. The fix is a provider whose base URL is a
   *required* setting so a key and its endpoint travel together. Not
   invented here: `agentrouter.org`'s endpoint shape is unknown to this
   deployment and CLAUDE.md forbids inventing APIs.
2. **No planner and no task graph** - zero matches (mandate 18/19).
3. **No persona output validator** - `brain/persona.py` only *instructs*.
   Mandate 11 requires rewriting "May thu..." -> "Cau thu..." before
   display; today a model that says "may" reaches the user unmodified.
4. **Android conversation history is in-memory only** - `ChatViewModel`
   holds a `MutableStateFlow`, there is no Room dependency, and
   `memory/models.py` has no Conversation entity or list/reopen API.
   Mandate 15 marks this CRITICAL.
5. **Error taxonomy is 5 classes against the 13 named** in mandate 9.

## 2026-08-24 - JARVIS modernization, phases 1-3

Working the master directive's section 42 order. Checklist and verification
log live in `.claude/modernization-checklist.md`; only the phases actually
built are recorded here.

### Phase 2 - Unified Model Contract (IMPLEMENTED)

`brain/capabilities.py` is new: `TaskClass` with all nine mandated classes,
a pure `classify_task()`, a `CapabilityLLM` protocol and a defensive
`generate_for()` free function.

The `@runtime_checkable` hazard applied again and was handled the same way
as `StreamingLLM`/`can_stream`: adding a method to `brain.ports.LLM` would
falsify `isinstance` for every provider that does not have it, so the new
capability surface is a *separate* protocol plus a free function that
degrades to plain `generate()`.

Wired at exactly one seam - `_generate` in `brain/conversation.py`. The task
class is decided once in `_prepare` and carried on `_Turn`, so nothing
re-classifies per retry.

### Phase 3 - Model Router 2.0 (IMPLEMENTED)

`brain/model_router.py` `CapabilityRouter`: one lane per task class, lazy
provider construction, a dead lane degrading to the chat provider, and the
chat lane as the floor that always answers.

`llm.task_models.*` added to `core/config.py` and `core/settings_store.py`
for five lanes (reasoning, coding, tool_planning, fast_response,
long_context). Only the five the server actually dispatches on - a control
for a lane that changes nothing is worse than no control.

Composition root `launcher/services.py` wraps `BrainRouter` **only when at
least one lane is non-empty**, so a default install's object graph is
byte-identical to before and `server/settings_service.py:_reapply_llm`
needed no change (it writes `provider_name` and clears `_provider`; both are
properties on `CapabilityRouter`).

Android `TaskModelsDto` in `ControlDto.kt` reads the lanes back.

### Phase 1 - Provider / credential foundation (IMPLEMENTED)

The directive's section 4 case: an owner-supplied endpoint whose key must
never reach a vendor. `brain/providers/custom.py` `CustomProvider` subclasses
the existing `OpenAICompatibleProvider` - no new HTTP client, no copied
dialect.

Structural properties, not conventions:

- `default_url = ""` and `default_model = ""`, with a new class flag
  `requires_base_url = True` on `HttpChatProvider`. A placeholder URL would
  resolve, answer 404, and read to the owner exactly like an outage at a
  provider they never configured.
- The key is read from `CUSTOM_API_KEY` and `self.url` is resolved in the
  same constructor from the custom endpoint settings. **Nothing in the
  request path consults the model name.** An owner may point this at a
  gateway proxying Anthropic and set `llm.custom_model = claude-sonnet-5`;
  the request still goes to their gateway with their key. Pinned by
  `test_a_claude_shaped_model_name_does_not_redirect_the_request`.
- New `base_url` constructor parameter carries `llm.custom_base_url` (the
  phone-writable surface). Precedence: non-blank setting > env var >
  `default_url`. Blank means "not configured here", not "configured to
  nothing", so saving an unrelated setting cannot un-configure an endpoint
  that came from the environment.

`OWNER_DEFINED_ENDPOINTS = {"custom": "CUSTOM_BASE_URL"}` in
`brain/router.py`. An absent endpoint or model returns `None` from
`_instantiate_provider` rather than raising, because `None` is the
established "a precondition is absent" signal and `_skip_reason` explains it
by name - `CUSTOM_BASE_URL is not set and llm.custom_base_url is empty`, or
`llm.custom_model is empty`. A constructor exception would have reached the
phone as `initialization raised ValueError`, which names nothing. The
constructor guard stays as defence in depth for direct construction.

`_endpoint_url` validator in `core/settings_store.py` requires an explicit
scheme and refuses a bare hostname: prepending one would choose between http
and https on the owner's behalf, and choosing wrong sends their key in
cleartext. Nothing else about the URL is judged - loopback is a first-class
case (vLLM, llama.cpp, LM Studio, LiteLLM all live there). A second new
validator `_clearable_text` exists because a custom endpoint has to be
*retirable*, unlike a vendor model name which always names something.

### Three derived-registry tests amended, not special-cased

Adding a row to `PROVIDER_KEYS` made three registry-loop tests fail, as
expected. All were fixed by making the invariant honest rather than adding
an exception for `custom`:

- `tests/test_cloud_providers.py` - `assert provider_class.default_model`
  became conditional on `requires_base_url`: a vendor must default *both*
  URL and model, an owner-defined provider must default *neither*. One
  without the other is a provider that guesses half of where it is going.
- `tests/test_provider_resolution.py` - new `preconditions_for(name)` helper
  derived from `OWNER_DEFINED_ENDPOINTS`, so "buildable" for such a provider
  means "buildable once the owner has supplied the endpoint and model". The
  opposite invariant is still pinned separately by
  `test_a_registered_provider_without_its_key_is_skipped_not_half_built`,
  which needed no change because the key check fires first.

### Verification actually performed

- Backend: `.venv/Scripts/python.exe -m pytest -q` gave
  `1879 passed, 1 skipped, 1 deselected in 29.82s`. Baseline was 1817, so
  +62 tests and zero regressions.
- Android: `./gradlew --offline cleanTestDebugUnitTest testDebugUnitTest`
  gave `classes=22 tests=312 skipped=0 failures=0 errors=0`, aggregated from
  `build/test-results/testDebugUnitTest/*.xml` after a clean, so freshly
  executed rather than UP-TO-DATE.
- `git diff --stat`: 19 tracked files, +1285/-210. Pre-existing uncommitted
  work verified intact.

### Fixture regeneration hazard, found and closed

Regenerating `android/app/src/test/resources/live/*.json` with
`AURA_WRITE_ANDROID_FIXTURES=1` captured **the development machine's live
provider state**, including a masked API key (`key_masked`), `key_source:
environment` and `active: gemini`. Restored the two affected files with a
scoped `git checkout HEAD -- <two paths>` (safe: `git diff --stat` on that
directory had printed nothing beforehand, proving both were clean; section
45 forbids a blanket `git checkout -- .`, not a scoped restore of files just
generated) and scrubbed the masked value from `settings.json`.

The correct invocation, used for every later regeneration, blanks the
provider keys in the environment first:

    GEMINI_API_KEY= DEEPSEEK_API_KEY= OPENROUTER_API_KEY= \
      AURA_WRITE_ANDROID_FIXTURES=1 .venv/Scripts/python.exe -m pytest ...

which makes the server produce exactly the committed unconfigured shape. The
resulting diff was pure additions with no key material.

### Deliberately deferred

The Hub UI has no graphical controls for the five `llm.task_models.*` lanes
or for `llm.custom_base_url` / `llm.custom_model`. All seven paths are in
`configurable` and settable through the settings PATCH API today. Section 32
is explicit that functionality precedes UI, so the controls belong to
phase 23.

### The one external input still required

Section 44's escape hatch, used once and named precisely: the repository
documents no AgentRouter endpoint or API dialect. Inventing a URL would
produce a provider that fails indistinguishably from an outage. If
AgentRouter speaks OpenAI chat-completions - as its compatibility claims
suggest - it needs no further code: `llm.provider = custom`, paste the base
URL and key, name the model. **The missing input is that base URL.**

## 2026-08-24 - JARVIS modernization, phase 4 (Cognitive State)

### What was wrong

Four things each knew part of "what is Aura in the middle of?" and none of
them could be asked:

    context: dict           the Android screen, untyped keys over HTTP
    _Turn                   one request's worth, gone at response time
    ProactiveContext        assembled for one decision
    runAgentSteps locals    on the stack of one suspend function

Four partial answers is no authority. The question that actually matters -
*have I already done this?* - had no answer anywhere, which is why the
agent opens YouTube, verifies it opened, and opens it again.

A survey subagent confirmed the scale before I wrote anything: six
independent copies of "now", four of "what app is in the foreground",
three of "when did we last see a screen", two of "when did the user last
speak" (in different units - one `time.monotonic`, one wall clock, not
comparable), and two vocabularies for "what kind of turn is this".
Section 8 forbids adding a fifth partial answer, so the object had to
become the home for these rather than a neighbour of them.

### core/cognitive.py

`CognitiveState` is mutable and authoritative; `snapshot()` hands out a
frozen `CognitiveSnapshot`. That is deliberately the bargain
`ProactiveContext` already makes and states in its own docstring - a
decision made from a frozen value is reproducible. `revision` increments
only on a real change, so "has anything happened since the last tick?" is
an integer comparison rather than a diff, and observing the same screen
twice does not move it. A verification loop has to be able to tell "still
loading" from "arrived".

Time is borrowed, never stored. The class holds a `TemporalClock` and asks
it. `launcher/services.py` already asserts one clock for the whole process
and four modules do not honour it; adding a fifth was not an option.

Action identity is `(kind, target)`. `open_app com.google.android.youtube`
is one fact whether attempted once or four times, which is exactly what
makes `has_succeeded` answerable without a model call. `attempts` counts
beginnings, not failures - a first attempt that failed and a second still
running is two attempts and one failure, and a retry bound built on the
failure count would be half the size it looks.

The invariant that kills the loop: `begin_action` on a succeeded record
returns that record and changes nothing. There is exactly one way to make
finished work run again - `enter_recovery(kind, target)` naming it - and
recovery is scoped to one action rather than being a mode, because a
global "recovering" flag would let *anything* repeat while it was set.
That is the open door this whole module closes.

Deliberately absent, and each for a reason: the time (above),
`events.AuraState` (that is what the *face* is doing - presentational,
and conflating the two because both are called "state" would be a real
bug), the task graph's shape (phase 6 - this tracks which node is current,
not what the graph looks like), and persistence (`memory/` is the durable
layer; a cache in front of it that could drift is worse than no cache).

### CognitiveStore - where one of these may live

Not a field on `ConversationManager`. Its own comment above `_Turn` says
why: one engine serves every session, so per-turn state kept on it "is a
race, not a cache". Here the consequence is worse than a race - two owners
sharing one record of completed actions means Aura skips a step for one of
them.

So: a dict keyed by session id behind a lock, which is exactly what
`SessionManager` already does for session metadata. One deliberate
divergence - `SessionManager` sweeps only when a session is created, this
sweeps on every access. Both bound the O(n) scan to once per interval, so
sweeping on access only changes *which* call pays. It matters because a
phone running an agent task holds one session and ticks it for hours; on
the create-only rule that session's stale neighbours would never be reaped
at all, and the entries here are action records and plans rather than four
floats. `server/session.py` records this exact leak being fixed once
already (AURA-P1-006: `cleanup_old` with no caller), so the lesson was
available to learn from rather than repeat.

Touch happens **before** sweep, and that ordering is load bearing. An
agent task ticks every few seconds and reads its state on every tick; if
the sweep ran first it could reap the very entry the caller came for - and
the agent would then re-open an app it had already opened, arriving at the
same failure by a different door. Touch first and a session in use can
never expire underneath its own reader. That also makes the wall-clock
idle math safe against a DST or NTP step: backwards reads as "no idle time
passed" and keeps the entry (the safe direction), and forwards cannot
touch a live entry at all.

### brain/agent_mode.absorb - feeding it from reality

The tick is where reality arrives and it was being dropped. Today
`PromptBuilder._build_agent_prompt` reads the device state once, renders
it, and forgets it, which leaves the phone as the only thing that
remembers progress - telling the model in prose. `AuraAccessibilityService`
literally sends *"This action was already successfully executed. Do not
repeat it."* as an error string. That works exactly as well as the model's
willingness to believe it.

`absorb` writes the same report into the session's state instead. It lives
in `agent_mode.py` because that module already exists to be the single
owner of how a tick is shaped - its docstring says three callers have to
agree on the rule and previously did not.

The parse follows only the format the device actually emits. I checked
`formatActionHistory` rather than guessing: `kind(args) [VERIFIED]`, pinned
by `AccessibilityAgentTest`. Target is the first argument, split on the
first comma so a quoted string containing one cannot shift the boundary.
`input_text`'s typed text is dropped on purpose - the same box typed into
twice is one step of the task, and a retry with corrected text is still
that step, so keeping the text would split one action into two records and
defeat the lookup. Unparseable lines are skipped, not represented as an
unknown action: the history is prose assembled for a prompt, and
half-reading it would put a fact in the state that no device reported.

`last_action_error` is **not** absorbed. The device builds it as free prose
in five different shapes (`grep -n "lastActionError = "` - lines 266, 283,
291, 324, 336) and none reliably names the action it refers to. Deriving a
`(kind, target)` from it would be inventing a format, which section 44
forbids.

Absorbing the same tick twice does not double the attempt count. Every
tick re-sends the whole history, so counting each replay would exhaust a
retry bound on step two through nothing but repetition of the report.

### Wiring

`ConversationManager(cognitive=...)`, called from `_prepare`'s machine
branch only. `_absorb` follows the shape of `_emit` and `_vision_context`
exactly - guard on None, try/except, log at debug - because this is
bookkeeping alongside the turn, not part of it. A store that raised would
take down the action the device is waiting for, and an agent that stops
mid-task because its notebook tore is worse than one working from a stale
note. There is a test for that: a store whose `for_session` raises still
returns a reply.

`ChatEngine(cognitive=...)` is pass-through with no default, and for a
different reason than `tools`: a store built inside the engine would be a
*second* one, private to it, and two records of completed actions is the
duplication the module exists to end. It arrives from the composition root
or not at all. `launcher/services.py` builds it after the clock (so the
store hands that same clock down) and before the engine that reads it,
and `_build_cognitive` is unconditional - there is no configuration under
which the agent should be allowed to forget that it already opened the app.

### Verification actually performed

    .venv/Scripts/python.exe -m pytest tests/test_cognitive_state.py -q
        67 passed

    .venv/Scripts/python.exe -m pytest -q
        1965 passed, 1 skipped, 1 deselected in 32.91s

Baseline was 1879, so +86 with zero regressions. Proven red first in both
places: `ModuleNotFoundError: No module named 'core.cognitive'` for the
state, then 12 failures for `CognitiveStore`, then `ImportError: cannot
import name 'absorb'`.

`git diff --numstat brain/conversation.py` is 68 insertions / 11
deletions in an 839-line file, and every deleted line belongs to the
uncommitted phases 2/3 work rather than to this phase - checked line by
line, nothing lost. The LF/CRLF warnings are cosmetic: git normalises on
the way in, and the numstat proves no whole-file rewrite.

### What this phase does not claim

The state is written to and can be read; nothing *acts* on it yet. The
planner (5), the task graph (6) and the prompt that would say "already
done, do not repeat" (17) are the consumers, and until they exist the
open_app loop is still prevented only by the phone's prose. Recorded as
IMPLEMENTED for what section 8 asks - one central state, tracking the
listed fields, not duplicated - and no further.

A fix I made to my own test helper while implementing: `moving_clock`'s
`advance` was doing `replace(second=...)` arithmetic with a carry branch
instead of `timedelta(seconds=...)`. It worked, which is the kind of thing
that survives review and then breaks the first time someone advances by
more than a minute.

---

## Android `submit` defect — CLOSED (2026-08-24)

Queued ahead of phase 5 because it refused an action the server prompt
instructs the model to use. Fixed on both halves, each proven red first.

### Half 1: the parser refused it

`"submit"` was absent from `AgentActionParser.KNOWN_ACTIONS` while
`brain/prompt_builder.py:444` offered it in the action enum and rules 2
and 3 of the SEARCH & RESULT SELECTION section instruct "Submit search".
A model doing exactly as told was answered `"submit" is not a supported
action`, which spends one of three parse failures. Section 23's mandated
flow (open → verify → locate search → focus → enter → **submit** →
verify results → stop) could not complete.

Added `"submit"` to the set. Four sides now agree: prompt offers,
executor implements (`AuraActionExecutor.kt:223`, via
`tryPerformImeSubmit`), completion heuristics test for it, parser
accepts.

### The guard, because the promise was already broken

`KNOWN_ACTIONS`' own docstring claimed it was "kept in step with the
supported set listed in the AGENT RULES prompt section" — nothing
checked that. Worse, `AgentActionParserTest.everyPromptedActionNameIsAccepted`
*claimed* to guard it and stayed green through the entire defect, because
it compared a hardcoded Kotlin list against the Kotlin constant that list
was copied from. Both halves of a same-language tautology.

The real guard has to cross the language boundary, so it lives in Python:
`tests/test_agent_protocol.py` renders the actual prompt, extracts the
action enum line, parses `KNOWN_ACTIONS` out of the `.kt` source, and
asserts they agree in both directions (`complete` excepted — it is
described in its own block, not the enum). Proven red: it failed with
`the AGENT RULES prompt offers ['submit'], which
AgentActionParser.KNOWN_ACTIONS rejects` before the fix.

Read out of the *rendered prompt*, not a Python constant, because the
prompt is what the model sees; a list agreeing with a constant while
disagreeing with the prompt would prove nothing. This is the first test
in the repo that reads a `.kt` file from Python — crude, and the only
option, since one runtime cannot import both a Kotlin `Set<String>` and a
Python f-string.

The Kotlin test was kept but relabelled
`everySupportedActionNameRoundTripsThroughTheParser`, with a comment
recording that it is a round-trip check (membership ≠ acceptance, since
normalisation runs first) and explicitly **not** a drift guard, naming
the Python test that is.

### Half 2: submit had no verification

`verifyStateChange` had no `submit` rule, so it fell to `else` and got one
snapshot after `delay(250)` — the same race class as the just-fixed
`open_app` bug, one action later in the same flow. `submit` fires the IME
action and returns; the results arrive over the *network*. At 250ms the
screen is still the unchanged query page, so a working submit was reported
UNVERIFIED and the agent spent a step re-submitting a search already in
flight. Section 11 forbids exactly this ("verification must not rely only
on: the command executed without throwing") — and a false negative here
costs a full LLM round trip, so the bounded wait is the *cheaper*
correctness, not a latency sacrifice (§36).

Added `waitForContentChange` to the companion, mirroring
`waitForForegroundPackage` deliberately: injected clock and sleep so JVM
tests drive timing without touching real time, bounded budget, per-attempt
logging, sits in the companion because the enclosing class is an
`AccessibilityService` and cannot be constructed in a unit test.

Design decisions worth keeping:
- **3000ms budget** vs open_app's 2500ms: open_app waits for a local
  activity to draw, submit waits for a network round trip *plus* a draw.
  Still leaves nine of ten steps, and the poll exits the moment the screen
  moves — a fast search pays one 150ms interval, not the budget.
- **Any of the three fingerprint fields moving counts.** A low bar for
  evidence, and still evidence — far above "did not throw". It cannot
  confirm the results are *relevant*; that is the caller's job and the
  result-selection heuristics already do it.
- **A null sample is waited through, not failed.** `rootInActiveWindow` is
  null precisely during the window transition a working submit causes;
  failing on it would reintroduce the bug through another door.
- **An unchanged screen still fails honestly.** The poll exists to stop
  reporting working submits as failures, not to start reporting failures
  as successes — a genuinely dead submit must fail so recovery can try
  another route.
- **`pre == null` falls through** to the generic path rather than
  pretending to compare against nothing.

7 tests in `SubmitVerificationTest.kt`, all red first (`Unresolved
reference 'waitForContentChange'`).

### Verified

- Android: `./gradlew --offline cleanTestDebugUnitTest testDebugUnitTest`
  → **23 classes, 319 tests, 0 failures, 0 errors, 0 skipped**, freshly
  executed after clean (312 → 319, +7). One genuine intermediate failure
  caught and fixed: the stale Kotlin pin above.
- `./gradlew --offline assembleDebug` → BUILD SUCCESSFUL,
  `android/app/build/outputs/apk/debug/app-debug.apk` (19,427,232 bytes)
- Backend: `.venv/Scripts/python.exe -m pytest -q` →
  **1967 passed, 1 skipped, 1 deselected in 32.58s** (1965 → 1967, +2)

Not yet done: the §35 live device scenarios. The APK is built but not
installed or driven on hardware, so submit's real-world timing is
untested — 3000ms is reasoned from the observed ~900ms YouTube draw plus a
network round trip, not measured.

### Finding while scoping phase 5: search-query normalisation exists once, in Kotlin only

`AuraActionExecutor.sanitizeSearchQuery` (`AuraActionExecutor.kt:474`) is
the *only* implementation of §23's "the query is `Minecraft`, not `search
for Minecraft`" rule. It strips one leading prefix from
`["search for ", "search ", "tìm kiếm ", "tìm "]`, case-insensitively,
preserving the remainder's case.

On the Python side that rule exists only as **prose in the prompt**
(`brain/prompt_builder.py:426`, rule 1). Grep found no request parsing in
`brain/`, `core/`, or `server/` at all — no query extraction, no app-name
extraction. So today the rule is enforced by asking the model nicely plus
a post-hoc Kotlin cleanup of whatever text it produced.

Consequence for phase 5: if the planner names the query in a plan step it
must strip the same prefixes, and that would be a second implementation
of the same list in a second language — precisely the drift the `submit`
defect was. If it goes that way it needs the same cross-language guard, in
the same test file, by the same technique. Noted before writing code
rather than discovered afterwards.

---

## Phase 5 — Planner — IMPLEMENTED (2026-08-24)

`brain/planner.py` (new, 380 lines). A pure module, no class, following
`brain/capabilities.classify_task`'s precedent. Confirmed by grep before
starting that no planner, `Plan`, or plan-like abstraction existed
anywhere in Python — the only `plan` surface was `core/cognitive.py`'s
`_plan: tuple[str, ...]`, which phase 4 created *for this phase to fill*.

### What it fixes

The agent loop sent the screen, the request, and a flat list of completed
actions, then asked for one action. Every step was a fresh derivation of
the same task, so ten steps meant ten rediscoveries with ten chances to
answer differently. Phase 4 gave the server durable memory of what had
succeeded; nothing read it back — `prompt_builder.py` did not import
`core.cognitive`, so the state was **write-mostly**. Phase 5 is the
return path.

The prompt now carries, mid-task:

```
===== PLAN =====
1. Open YouTube  [DONE]
2. Focus the search box  [DONE]
3. Type only "lofi music" into the search box  [DONE]
4. Submit the search  <- NOW
5. Search results are on screen
6. Then: pick the first non-ad result
```

### Public surface

`StepKind` (OPEN_APP, FOCUS_SEARCH, ENTER_QUERY, SUBMIT_SEARCH,
AWAIT_RESULTS, SELECT_RESULT), frozen `PlanStep(kind, detail)`, frozen
`Plan(goal, steps)` with `__bool__`, plus `plan_for(request)`,
`search_query(request)`, `current_step(plan, state)`,
`render_plan(plan, state)`.

### Decisions, and the evidence behind each

- **Structure recomputed every tick, not serialised.** `plan_for` is pure,
  and the request does not change mid-task, so two call sites cannot
  disagree and there is no second copy to drift (§8). What *is* written to
  the state is `set_plan` + `enter_node` — dead surface since phase 4,
  now called by `ConversationManager._plan`.
- **Position from `CognitiveState.succeeded`, never a counter.** A counter
  would be a second record of progress, and the two would part company on
  the first retry — the class of bug that produced open_app open_app
  open_app.
- **`AWAIT_RESULTS` shares its evidence with `SUBMIT_SEARCH`.** My first
  draft invented an `await_results` action to satisfy it separately.
  Nothing in the Android vocabulary emits that, so the step would have
  been permanently unmet in production. The honest satisfier is the
  verified `submit`: the device verifies a submit with the
  `waitForContentChange` poll built during the submit fix, so a verified
  submit *is* results having rendered. Caught by checking the action
  vocabulary rather than by a failing test.
- **`click` satisfies SELECT_RESULT and deliberately not FOCUS_SEARCH.**
  If a tap also counted as focusing, tapping the search box would satisfy
  the last step of a selection plan and the task would report itself
  finished having only opened the keyboard. `ActionRecord.at` cannot
  disambiguate — every action absorbed in one tick shares the clock
  reading — so ordering was not available as a fix. The prompt offers a
  dedicated `focus` action and typing proves focus anyway, so nothing is
  lost by refusing the ambiguous reading.
- **`_same_app` is narrow on purpose.** The plan holds a display name, the
  device reports a package. `com.google.android.youtube` contains
  "google", so a loose match would let a YouTube launch satisfy "open
  Google Chrome". Squashed-name containment, falling back to the final
  dot-segment against the name's words.
- **No LLM, no provider argument.** A plan produced by the thing being
  planned for cannot be the fixed point the model is steered against, and
  §7 forbids a model switch changing behaviour. Pinned by a signature
  test.
- **Unplannable request → empty plan → no section → byte-identical
  prompt.** The `read_intent` bargain: when unsure, the option that costs
  least when wrong. An invented plan would name an app nobody mentioned.

### Boundary held against the device (deliberate, not an oversight)

`shouldAutoComplete`, `isSearchTaskComplete` and `isSelectionTaskComplete`
in `AuraAccessibilityService` already encode an implicit version of this
decomposition as keyword heuristics, and they decide when the loop
*stops*. The planner does not touch that. Reconciling the two is phase 17;
doing it here would mean changing when tasks end in the same change that
introduces the thing describing them, and the two failures would be
indistinguishable. Recorded in the module docstring so the next reader
does not "fix" it by accident.

### Files changed

- `brain/planner.py` — new
- `core/cognitive.py` — `+has_succeeded_kind(kind)`, sited next to
  `has_succeeded` so "identity is `(kind, target)`" stays in one file.
  Needed because the planner cannot know a node id in advance.
- `brain/prompt_sections.py` — `+PLAN`
- `brain/prompt_builder.py` — `plan=` param on `build()`, forwarded to
  `_build_agent_prompt`, rendered as section 2.7 between COMPLETED ACTIONS
  and AGENT RULES
- `brain/conversation.py` — `+_plan(context, session_id)`, called in the
  machine branch immediately after `_absorb` (order matters: absorbing
  second would render a plan one step behind the device). Kept separate
  from `_absorb` so a fault in the derivation does not read as a fault in
  the ingest. Same swallow-and-degrade bargain: a device is waiting for an
  action.
- `tests/test_planner.py` — new, 41 functions / 50 cases
- `tests/test_machine_turns.py` — `+TestThePlanReachesThePrompt`, 11 tests
- `tests/test_agent_protocol.py` — +2 cross-language guard tests

### The duplication risk recorded before writing code is now closed

`SEARCH_VERBS` in `brain/planner.py` and the `prefixes` list in
`AuraActionExecutor.sanitizeSearchQuery` are the same vocabulary in two
languages — exactly the shape of the `submit` defect. Two guards in
`tests/test_agent_protocol.py` read the Kotlin source and compare:
`test_both_sides_strip_the_same_search_verbs` (list equality, so order
counts) and `test_the_longest_verb_is_tried_first_on_both_sides` (a prefix
listed before its extension would make "search for X" yield "for X").

Both were **verified to fail** before being trusted: dropping `tìm kiếm`
from `SEARCH_VERBS` failed the first; reversing the order failed both.
Restored, both pass. This matters because the last same-language pin in
this area was a tautology that stayed green through the whole defect.

### Verification (actually executed)

- Red proven first: `tests/test_planner.py` → `ModuleNotFoundError: No
  module named 'brain.planner'`; `TestThePlanReachesThePrompt` → 7 failed,
  4 passed (the 4 being the negative tests, which pass trivially with no
  plan at all — the right shape for a red).
- Backend: `.venv/Scripts/python.exe -m pytest -q` →
  **2030 passed, 1 skipped, 1 deselected in 28.75s** (1967 → 2030, +63,
  0 regressions).
- Android: **not run, because no Kotlin was modified in phase 5.** The
  suite last ran green at `classes=23 tests=319` after the submit fix.
  That result is not reused as fresh evidence (§34).

### Still owed

- §35 live device scenarios: unchanged from the submit fix — APK built,
  not installed or driven on hardware.
- Phase 17 will have to reconcile the device's completion heuristics with
  the plan. Until then the device remains the completion authority and the
  plan is advisory context.

---

## Phase 6 — Task Graph (§10) — IMPLEMENTED

2026-08-24. Backend **2093 passed, 1 skipped, 1 deselected in 19.14s**. No
Kotlin touched, so no Android run is claimed.

### The open question, settled from the repo

Phase 6's recorded plan asked whether the graph should be a new module or an
extension of `core/cognitive.py`, noting that `set_plan` stores a flat
`tuple[str, ...]` and `enter_node` a single string, so "something has to
give".

Nothing had to give. Reading `CognitiveState`'s public surface showed that all
seven of §10's states are answerable from facts it already records:

| State | Derived from |
|-------|--------------|
| SUCCESS | `succeeded` / `has_succeeded_kind` — the planner's `is_done` |
| SKIPPED | `focus.application`, written by `absorb` on every tick |
| RUNNING | an `ActionRecord` exists and is `PENDING` |
| FAILED | the record is `FAILED` and `should_retry` says no |
| RECOVERING | `recovering_from` names an action that satisfies this step |
| BLOCKED | an earlier node is FAILED or BLOCKED |
| PENDING | none of the above |

So node state is a **projection** — the same frozen-projection-over-mutable-owner
idiom `CognitiveSnapshot`, `ProactiveContext` and `TemporalContext` already
follow — not a second store. That answers §8 directly: had the graph persisted
per-node states, they would have disagreed with the action records the first
time anything was retried, with no way to tell which was lying. `set_plan` and
`enter_node` keep their existing shapes and are untouched.

The module therefore lives in `brain/` (derivation) rather than `core/`
(storage), beside `planner.py` and `capabilities.py`.

### What was built

`brain/task_graph.py` — `NodeState` (7 states, `str`-valued lowercase per the
`ActionState`/`TaskClass`/`AuraState` convention), frozen `TaskNode`
(step/state/attempts/detail) and `TaskGraph` (goal/nodes, with `current`,
`is_finished`, `is_stuck`), plus `build`, `current_step`, `render`,
`render_plan`.

`NodeState.SUCCESS`, not SUCCEEDED, deliberately: §10's word, and the
divergence from `ActionState.SUCCEEDED` is the point — an action is one attempt
at a device operation keyed by what it targeted; a node is a unit of plan and
can be BLOCKED or SKIPPED, neither of which any single attempt could be.

### Decisions worth keeping

**SKIPPED is the state that changes behaviour on hardware today.** It means
"the postcondition already holds", and `absorb` has been recording the
foreground package all along. Before this, asking to open an app that was
already open still produced an `open_app`; now node 1 renders `[SKIPPED]` and
the plan advances. Only OPEN_APP may claim it — a focused field or rendered
results would have to be read off `focus.screen`, which arrives permanently
empty because the device never fills in `AppInfo.activity`. Claiming a step was
skipped on evidence that does not exist would advance a plan past work nobody
did, which is the exact failure this area exists to stop.

**Precedence: RECOVERING > SUCCESS > SKIPPED > RUNNING > FAILED > BLOCKED >
PENDING.** RECOVERING beats SUCCESS because that is the entire content of §10's
exception — if SUCCESS won, `enter_recovery` could never reopen a completed node
and the exception would be decorative. SUCCESS beats SKIPPED because after a
real launch both facts hold at once and reporting SKIPPED would deny we did it.
A node's own FAILED beats an inherited BLOCKED because "this exact thing broke,
and here is what the device said" is more use than "something upstream broke".

**BLOCKED is about successors, never a node itself.** An exhausted node is
FAILED; the ones after it are BLOCKED, because telling the model to type into
the search box of an app that never opened is worse than telling it nothing.
`current` returns None for a blocked plan, and `is_stuck` / `is_finished` exist
so the caller can tell "nothing to do" from "done" — a distinction a null
current node cannot carry.

**One retry accounting, not two.** FAILED asks `state.should_retry(...)` rather
than comparing a bound defined here, and `TaskNode.attempts` is copied off the
record rather than counted. Python's `(kind, target)` key and Android's
`"${action.action}:${action.nodeId}"` (which collides for `open_app` — a null
nodeId gives `"open_app:null"`) already disagree; adding a third would be worse.

**No `depends_on` edge list.** `plan_for` produces a chain, so "an earlier node"
means exactly the nodes before this one. A field always equal to `(index - 1,)`
would be a structure pretending to carry information it does not. When plans
branch, that field is where the dependency goes and `_blocking` is the only
thing that changes — recorded in the module docstring so the absence reads as a
decision rather than an oversight.

### One implementation, not two

`current_step` and `render_plan` **moved out of `brain/planner.py` into
`brain/task_graph.py`**, because both answer "what state is this step in" and
phase 6 computes seven states where phase 5 computed two. Leaving them behind
would have left two renderers free to disagree — the same shape as the `submit`
verb drift.

- Promoted to public in planner, for the graph to use: `_is_done` → `is_done`,
  `_same_app` → `same_app`, `_describe` → `describe`, `_SATISFIED_BY` →
  `SATISFIED_BY`. Planner keeps request parsing and step description; the graph
  owns state and marking.
- The dependency runs one way only (`task_graph` imports `planner`), so no
  cycle.
- The 22 tests covering position and rendering moved with them, from
  `tests/test_planner.py` (42 defs → 20) to `tests/test_task_graph.py`. Every
  one still passes unchanged: the three-state view is a strict special case of
  the seven-state one.
- `brain/conversation.py` now imports `plan_for` from planner and
  `current_step`/`render_plan` from task_graph. `_plan` itself is unchanged.

### Verified, not assumed

Red first: `ModuleNotFoundError: No module named 'brain.task_graph'` at
collection.

Two defects found in my own tests before implementing:

- A `REACHED` set computed at module import — a collection-time landmine if any
  arrangement raised. Replaced with a function called from the test.
- A test that grepped `brain/task_graph.py` for the string `should_retry`, which
  is an implementation assertion and §33 forbids it. Replaced with a behavioural
  pair: the node's `attempts` must equal `state.attempts_for(...)`, and a
  failure still short of the bound must leave the node current.

One test was vacuous and was strengthened:
`test_recovering_a_different_action_leaves_this_node_alone` called
`enter_recovery("open_app", "com.android.chrome")` on a state with no Chrome
record, so `recovering_from` returned None and the test passed without
exercising the comparison. Chrome is now actually launched first.

`test_every_state_is_reachable_by_some_arrangement` is parametrized over all
seven and asserts each is produced by some arrangement of device-shaped facts.
A state nothing can produce is vocabulary, not behaviour.

End-to-end through a real `ConversationManager`, printed rather than asserted:

```
--- YouTube ALREADY in the foreground (the new behaviour) ---
1. Open YouTube  [SKIPPED]
2. Focus the search box  <- NOW
3. Type only "lofi music" into the search box
4. Submit the search
5. Search results are on screen
6. Then: pick the first non-ad result

--- launch verified, query typed ---
1. Open YouTube  [DONE]
2. Focus the search box  [DONE]
3. Type only "lofi music" into the search box  [DONE]
4. Submit the search  <- NOW
...

--- launch exhausted (phase 7 will produce this) ---
1. Open YouTube  [FAILED: app not installed]
2. Focus the search box  [BLOCKED]
...
current=None  is_stuck=True  is_finished=False
```

Note that the second case renders `[DONE]` and not `[SKIPPED]` even though
YouTube is in the foreground — SUCCESS beating SKIPPED, working as designed.

### Honest about reach (§44)

Of the seven states, **PENDING, SUCCESS and SKIPPED occur in production today**.
RUNNING requires `begin_action`; FAILED, BLOCKED and RECOVERING require
`fail_action` / `enter_recovery`. The tick calls none of those: `absorb` records
only the verified branch, because the device's error strings are free prose in
five shapes and deriving a `(kind, target)` from them would be inventing a
format rather than reading one. **Phase 7 owns those producers.** The projection
is complete and tested for all seven; three of its inputs arrive later. This is
not "implemented" standing in for "designed" — the code runs and is tested — but
it is not seven live states either, and the final report must not say it is.

### Still deliberately not crossed

The device keeps completion authority. `shouldAutoComplete`,
`isSearchTaskComplete` and `isSelectionTaskComplete` still decide when the agent
loop stops, and the graph's `is_finished` / `is_stuck` do not feed them.
Reconciling the two is phase 17; `is_finished` is now the server-side fact that
reconciliation will be built on.


## Phase 7 — Verification + Recovery (§11/§12) — IMPLEMENTED (2026-08-24)

Phase 6 closed with "**Phase 7 owns those producers**". It does now: FAILED,
BLOCKED and RECOVERING all occur in production, and the reason they could not
before was a single missing wire field.

### The gap, located rather than assumed

Verification was **not** missing on the device. `AuraActionExecutor` already
does the real per-kind check with bounded polls — `waitForForegroundPackage`
for a launch, `waitForContentChange` for a submit, a fingerprint comparison
otherwise — and rebuilding that server-side would have been a second
verification system whose disagreements with the first would be invisible.

What was missing is that **the result of the check never crossed the wire in a
form the server could read**. `completedActions` is appended only inside
`ExecutionResult.Verified`; failure travelled only as `last_action_error`, free
prose in five shapes, none carrying a `(kind, target)`. So the server could not
mark a node failed, could not know an attempt had been spent, and could not
decide anything about retrying. `fail_action`, `should_retry` and
`enter_recovery` had existed since phase 4 with **no production caller**.

### What was built

- **`failed_actions` on the wire.** `AccessibilitySnapshot` gained
  `@SerialName("failed_actions") val failedActions: List<String> = emptyList()`
  — the sibling of `completed_actions`, same format, defaulted so an installed
  APK predating the field still deserialises. Lines read
  `open_app(com.android.chrome) [FAILED x2]`. The verdicts are the device's own
  `ExecutionResult` names, not a taxonomy invented for the wire: FAILED means
  the gesture could not be performed, UNVERIFIED means it was performed and its
  postcondition was not observed — which is §11's distinction, and the half a
  single "it did not work" would throw away. The count is on the line because
  the service already keeps it in `failedActionsCount`; one line per attempt
  would make the server's arithmetic depend on how many ticks happened to pass,
  and every tick re-sends the whole list.
- **`brain/recovery.py`** — the phase's policy module. `DEFAULT_RETRY_LIMIT`,
  `limit_for`, `may_retry`, `invalidated`, `reconcile`, `absorbed_failure`.
- **`brain/agent_mode.py`** — `_FAILED_ACTION`, `_target_of`,
  `read_action_failures`, and the failure ingest at the tail of `absorb`.
- **`brain/task_graph.py`** — `_state_of` now asks `may_retry`, so a node is
  FAILED when its bound is spent rather than when it is merely unsuccessful.
- **`brain/conversation.py::_plan`** — builds the graph once, reconciles
  against measured `graph.is_stuck`, rebuilds if recovery moved.

### Two real defects fixed, not described

1. **`"open_app:null"`.** The service's retry key was
   `"${action.action}:${action.nodeId}"`, and a launch carries no node id — so
   *every* launch of *every* app keyed to the literal string `open_app:null`.
   Two failed attempts at Chrome and the next launch, of any app at all, was
   refused before it was tried. Nothing in the message said so; it surfaced as
   Aura simply declining to open things. Fixed by `actionTarget` /`actionKey`,
   which also makes the device's key agree with the server's `(kind, target)`.
2. **Three dead producers.** `fail_action`, `should_retry` and `enter_recovery`
   now all have callers, tested.

### Decisions, and the evidence behind each

**The bound is 2, and it is not a guess.** `should_retry` takes `limit` as an
argument on purpose — its docstring says the right number depends on the action
— and every caller so far took the default, which makes the bound a default
parameter rather than a policy. The device refuses to execute an action whose
failure count has reached `MAX_ACTION_ATTEMPTS`, so that number *acts*: a server
limit above it would be permission the phone declines to honour, while one at or
below it is enforceable because the server simply stops asking.
`DEFAULT_RETRY_LIMIT = 2` matches the floor exactly.

**`RETRY_LIMITS` is deliberately empty.** The seam exists because phases 18/22
bring actions that are not cheap (a shell command, a file write, a tool with an
off-phone side effect). None exist yet. Pre-filling it with plausible per-kind
numbers would be inventing behaviour — numbers that look like policy, derived
from nothing, that a later reader would reasonably assume someone had measured.
Evidence gathered before deciding: `AccessibilityNodeSerializer.kt` mints
`node_$nodeCounter` as a per-snapshot walk index, and `AuraActionExecutor` uses
`ACTION_SET_TEXT` for `input_text`, so retrying is idempotent.

**`limit_for` is total and never zero.** Total because an unknown kind must
still be bounded — "unknown means unlimited" is precisely the forever-repeat §12
forbids. Never zero because a zero limit refuses the *first* attempt, which is
not a retry policy but a block, and blocking an action the prompt openly offers
would be the arbitrary restriction §2 rules out. Safety refusals belong in
`SafetyGuard`, visibly, not smuggled in as a retry count of nothing.

**Recovery entry is gated on `is_stuck`. This is the most important line in the
phase, and a regression caught the flaw.** `invalidated` re-asks §11's launch
postcondition every tick, which is sound in principle — but the only evidence
available is one foreground package, and an app that was killed is
indistinguishable from one behind a permission dialog, a share sheet, or a
sub-activity in another package. Acting on that directly relaunches a healthy
app, throws away a half-typed search, and does it again next tick — which is
`open_app open_app open_app`, the exact behaviour §10 names as the thing to
prevent. A recovery engine that manufactures the loop it exists to stop is worse
than none. Waiting until the plan is stuck costs a few actions that were going
to fail anyway and buys an unambiguous reading: every remaining step has spent
its bound against an app that is not there, and a transient overlay does not
produce that. Missed recovery is bounded and honest (FAILED/BLOCKED, `is_stuck`
true); spurious recovery generates the forbidden loop. **Closing** stays
ungated, because it must fire on the tick the app returns, and a plan in
recovery is never stuck by construction.

Alternatives considered and rejected: requiring no later step to have succeeded
(fixed neither the test nor the hazard); two-consecutive-tick evidence (adds
state, still speculative); deleting `invalidated` (leaves RECOVERING with no
producer again).

**Recovery is itself bounded.** `_state_of` checks RECOVERING before FAILED, so
an unbounded recovery would keep a node workable forever while an app was
repeatedly killed. `reconcile` refuses to open recovery on an action already at
its bound — using `attempts_for` directly rather than `may_retry`, because
`may_retry` correctly refuses succeeded actions and the action being recovered
is succeeded by definition. Asking `may_retry` would mean recovery could never
start at all.

**Absorption is idempotent, and bounded by arithmetic.** Every tick re-sends the
whole list, so `absorbed_failure` brings the count *up to* what was reported
rather than adding to it. The loop is `for _ in range(wanted - already)` rather
than `while attempts < count` because `begin_action` returns a finished record
without incrementing when the action already succeeded — the condition form
would never advance and never end. A changed verdict with an unchanged count
still updates the reason, because the reason is what reaches the model.

**Successes are read before failures in `absorb`, and the order is
load-bearing.** The device clears an action's failure count when it finally
verifies, but a tick already in flight can still carry the old line; reading the
failure last would let a stale count reopen work that has since succeeded.

**`Blocked` is deliberately not reported as a failure.** A safety refusal is not
a thing to retry, which is exactly what putting it on `failed_actions` would
invite.

### Cross-language pins added (`tests/test_agent_protocol.py`)

Two, in the established `SEARCH_VERBS` reader style — read out of the Kotlin
source, with a message telling the next editor to re-point the reader rather
than delete the check. A same-language pin would be a tautology.

- `test_the_server_never_asks_for_more_attempts_than_the_device_performs` —
  reads `MAX_ACTION_ATTEMPTS` (currently 2) and fails if `DEFAULT_RETRY_LIMIT`
  or any `RETRY_LIMITS` entry exceeds it.
- `test_both_sides_agree_on_what_a_failure_verdict_says` — reads the verdict
  literals off the `formatActionFailure` **call sites** (not a constant, because
  a constant listing a verdict nobody emits would pass a check against itself)
  and fails if `_FAILED_ACTION` cannot match one, or `FAILURE_DETAIL` has no
  wording for it. A verdict the regex misses is not a parse error — the line is
  skipped — so the failure would vanish in silence and the step would look
  untried forever.

### Files changed

| File | Change |
| --- | --- |
| `brain/recovery.py` | **new** — retry bound, postcondition re-check, reconciler, failure absorption |
| `tests/test_recovery.py` | **new** — 57 tests, red first |
| `brain/agent_mode.py` | `_FAILED_ACTION`, `_target_of`, `read_action_failures`, failure ingest in `absorb` |
| `brain/task_graph.py` | `_state_of` asks `may_retry` for FAILED |
| `brain/conversation.py` | `_plan` builds once, reconciles on measured `is_stuck`, rebuilds if recovery moved |
| `tests/test_agent_protocol.py` | 2 cross-language pins |
| `AccessibilitySnapshot.kt` | `failed_actions` wire field, defaulted |
| `AuraAccessibilityService.kt` | `actionTarget`/`actionKey`/`actionSignature`/`formatActionFailure`, `MAX_ACTION_ATTEMPTS`, `failedActionLines`, guard rewritten off the new key |
| `ActionIdentityTest.kt` | **new** — 11 tests, including the `open_app:null` regression |

### Verification (actually executed, §34)

```
.venv/Scripts/python.exe -m pytest -q
2152 passed, 1 skipped, 1 deselected in 19.56s      (baseline 2093, +59)

tests/test_recovery.py                    57 passed
tests/test_agent_protocol.py              45 passed

./gradlew --offline cleanTestDebugUnitTest testDebugUnitTest
BUILD SUCCESSFUL — 24 classes, 330 tests, 0 failures, 0 errors, 0 skipped
  (compileDebugKotlin and testDebugUnitTest both EXECUTED, not UP-TO-DATE)

./gradlew --offline assembleDebug
BUILD SUCCESSFUL — app/build/outputs/apk/debug/app-debug.apk  19,427,232 bytes
```

Red first, for the right reason: `ImportError: cannot import name
'read_action_failures'` at collection before `brain/recovery.py` existed.

### Honest about reach (§44)

- **All seven node states now occur in production.** PENDING/SUCCESS/SKIPPED
  from phase 6; RUNNING from `begin_action`; FAILED, BLOCKED and RECOVERING from
  this phase's producers.
- **Not verified on hardware.** The APK is built and the Kotlin unit-tested, but
  §35's live scenarios have not been driven on a device, so `failed_actions` has
  never been observed crossing a real wire. The Kotlin test asserts the field
  serialises and defaults; it does not prove a phone fills it in.
- **`invalidated` only re-checks launches.** A focused search field or rendered
  results would have to be read off `focus.screen`, which arrives permanently
  empty because the device never sets `AppInfo.activity` (phase 17 debt). A
  postcondition asserted on absent evidence is worse than one not asserted.
- **Node ids are per-snapshot walk indices.** `(kind, target)` bounds catch
  repetition of the *same* target, not flailing across many ids on one screen.
  Recorded as a known issue; the fix belongs where the ids are minted, not in
  the retry policy.

## Phase 8 — Persona Engine + Validator (§13/§14) — IMPLEMENTED (2026-08-24)

### The gap, located rather than assumed

`brain/persona.py` already existed from the pre-mandate identity work, and
it is not thin: `PronounStyle`, `read_style`, `resolve`, `PersonaDials`,
`AddressPreference`, `ContextMode`, and a rendered PERSONA section wired
through `PromptBuilder` → `ConversationManager` → `ChatEngine` → config. So
"build the Persona Engine" was already done, and phase 8's real content is
the half §13 and §14 both name in the same words: §13 calls prompt-only
"insufficient", §14 says "prompt instructions alone are insufficient".

What was missing was any *consequence*. The prompt stated the register as
one unambiguous line and then hoped. A model that agreed to cậu/tớ and then
wrote "mày" in the third paragraph had broken the contract, and nothing
downstream noticed — not the reply, not the transcript, so the drift got fed
back to the model next turn as an example of how Aura talks.

So this phase **extended rather than replaced**, per the note left in
current-task.md: `persona.py` is untouched, and the new module reads its
vocabulary and its resolved state rather than restating either.

### What was built

**`brain/persona_validator.py`** — one public function, `validate(text,
state)`, and a scope deliberately not one word wider than the prompt's own
promises. Every correction maps to a sentence a model was actually given:

| correction | the promise it enforces |
|---|---|
| four coarse words → the register's terms | `_pronoun_line`: "Never use tao or mày back at him, whatever he uses himself." |
| a forbidden word is dropped | `_pronoun_line`: "He has asked you not to call him X; never do." |
| other self-words → the register's self word | `_pronoun_line`: "do not mix in any other first-person pronoun" |
| an emoji run keeps its first | `RESTRAINT`: "Slang, emoji and brainrot are seasoning" |
| an address term in every sentence keeps its first | `RESTRAINT`: "never put the address term in every sentence" |

Enforcing a rule the prompt never stated would be a different thing: the
model would be corrected for obeying its instructions, and the fix would be
invisible to whoever wrote them.

**`brain/style.py`** — `_hide_code`/`_restore_code` promoted to `hide_code`/
`restore_code` (two internal call sites updated). A second implementation
would have been a second answer to "what counts as code", and the two rules
that must never touch code are the filler filter there and the pronoun pass
here — so a disagreement between them would surface as one of them editing
a snippet.

**`brain/conversation.py`** — `_Turn` gained `persona`, resolved once in
`_prepare`; `_compose` now reads it instead of calling `persona_of` itself
(it is called more than once per tool-calling turn); `_voiced` runs at both
exits, after `_styled`.

**`tests/test_persona_validator.py`** — 49 tests, written red first, in ten
classes that follow the hazards rather than the functions.

### Decisions, with the reasoning that forced them

**The one substitution exception is justified, not waived.** `style.py`
declares "never substitutes, never reorders, never paraphrases" as absolute,
and that rule is right for what it does — deciding whether a clause is
filler is a judgement about meaning, and a regex that reached into a
sentence would eventually reach into a stack trace. Rather than violate it
or duplicate its machinery, substitution lives in a *separate* module, and
the difference is one of kind: a pronoun is a closed vocabulary of eight
words, replaced by another word from the same closed set, chosen by
`resolve` rather than by this module. That is a lookup. Facts remain
untouchable in both layers.

**The target words come from the resolver, never from a constant.** A
hard-coded "cậu" would override the owner's own register the moment he wrote
in another one — which is §2's complaint from the inside. The test named
`test_the_target_words_come_from_resolve_not_from_a_constant` asserts against
`resolve` rather than against literals, so the pin cannot rot into a
tautology.

**§2 got a mechanism, not a comment.** `state.address.preferred` is checked
before any correction, so an owner who asked to be called "mày" is called
"mày". A *forbidden* word is **dropped rather than swapped** — the same
bargain `PersonaState.address_word` already documents, because inventing a
replacement for a word he banned is guessing at a preference he did not
state.

**Three linguistic guards, all biased towards under-correcting.**

- "ông"/"bà" have ordinary meanings — "ông ấy" is *he*, "ông nội" is
  *grandfather* — so unlike "mày"/"tao" they are corrected only in vocative
  position, approximated by `NOT_VOCATIVE`. Guessing wrong the other way
  costs the meaning of the sentence, so the guard errs towards leaving them.
- "mình" has a second life in "của mình", "tự mình", "chúng mình", guarded
  by `NOT_SELF_BEFORE`.
- Diacritics do the rest for free: "tạo" (create) and "may" (lucky) are
  simply different strings from "tao"/"mày", so a Unicode word-boundary
  pattern never matches them — and "taobao" fails the trailing boundary.
  Tested, because "it happens to work" and "it is guaranteed" read the same
  in a diff.

**The machine-turn exemption is structural rather than a flag.** A machine
turn builds no `_Turn`, so its persona is None and `validate` returns the
text untouched. The alternative — a boolean threaded through from
`is_machine_turn` — would be one mistake away from rewriting the field names
inside an action the service then fails to parse.

**Validation runs after styling.** Style is subtractive over a whole reply
and can delete a clause, which changes what "the address term in every
sentence" means. Validating last is what makes the text the user reads the
text that was checked.

**Two of `RESTRAINT`'s own sentences are deliberately unenforced,** and the
reason is recorded in the module docstring rather than left as an omission to
be discovered later:

- *"Never open with the same phrase twice in a row."* The previous reply is
  available at the call site, but there is no safe subtractive fix. Deleting
  a repeated opener is harmless only when the opener carries no stance, and
  telling "Ok," from "Không —" apart is exactly the filler judgement
  `style.py` already encodes as a closed list and already applies
  unconditionally. For the openers where deletion is safe this would add
  nothing; for the rest it would invert an answer's meaning, which is the one
  thing both layers forbid.
- *"Never reach for a trend word because it is available."* Whether a word
  landed is a fact about the sentence around it. A banned-word list would
  delete the times it landed along with the times it did not.

**One §7 over-reach was declined.** A model introducing itself by its own
name is a real §7 violation, but "tớ đang chạy trên Qwen3" is the owner
asking about his own configuration and getting an honest answer. Telling
those apart needs to understand the sentence, and deleting the second to
catch the first would make Aura lie to her owner about what she is running
on — which §2 and §28 both weigh against more heavily than §7 weighs for it.
The identity anchor and the `CONTRACT` paragraph carry that promise instead.

**Restraint of the validator itself is tested, not asserted.** Five tests
exist only to check it does nothing: a clean reply is returned byte-for-byte,
`validate(x, None)` is the identity, empty/blank/None survive, correcting
twice changes nothing more, and a reply full of numbers and package names
comes back with every fact intact.

### Files changed

| file | change |
|---|---|
| `brain/persona_validator.py` | **new**, 558 lines — the enforcement layer |
| `tests/test_persona_validator.py` | **new**, 474 lines, 49 tests in 10 classes |
| `brain/style.py` | `_hide_code`/`_restore_code` → public, +2 docstrings |
| `brain/conversation.py` | `_Turn.persona`, resolved once, `_voiced` at both exits, streaming caveat extended |
| `brain/persona.py` | **untouched** — extended, not replaced |

### Verification

- `.venv\Scripts\python.exe -m pytest tests/test_persona_validator.py -q`
  → **49 passed**. Confirmed red first: `ModuleNotFoundError: No module named
  'brain.persona_validator'` at collection, then 44/49 with the module
  complete and the five wiring tests still failing, then 49/49 after
  `conversation.py`.
- `.venv\Scripts\python.exe -m pytest -q` → **2201 passed, 1 skipped, 1
  deselected in 19.86s**. Baseline before this phase was 2152/1/1, so +49 and
  **zero regressions**.
- `grep` for `_hide_code`/`_restore_code` across the repository → no remaining
  references, so the rename is complete rather than shadowed.
- `git status --porcelain` → every pre-existing uncommitted item still
  present, including the odd `"\304\221a"` filename, the three
  `android/aura-agent*.log` files, `local_agent/` and `android/.kotlin/`
  (§45).
- Android was **not** rebuilt: this phase changed no Kotlin. The APK from
  phase 7 is still the current one.

### Honest about reach

The validator sees only what `ConversationManager` returns, and that is now
the whole of it — `chat` and `chat_stream` are the only two reply paths in
the project, and both go through `_voiced`. What it does *not* do is make
Aura warm; §13 wants warmth and Gen-Z texture and no regex produces those.
What is checkable is whether the register held, and that is what is checked.

---

## Phase 9 — Conversation Persistence (§15) — IMPLEMENTED (2026-08-24)

§15's requirement is one sentence: "AURA's chat UI must NOT lose visible
history when the application closes." So the phase started by finding out
what the phone actually did, because the fix depended entirely on the answer.

### What was actually broken

The server side already persisted — `ConversationStore` is what `_remember`
writes to and `history()` reads from, keyed by session. The phone was the
half that did not:

- `ChatViewModel._state` was seeded with `ChatUiState()`, an empty message
  list. Every launch drew an empty screen.
- There was no history route to fetch from, and no local store to read.
- Worse than the bubbles: `AuraRepository._sessionId` was an
  `AtomicReference` that died with the process too. So even if the bubbles
  had been restored from somewhere, the next message would have opened a
  brand-new server session — a visible transcript beside a conversation the
  server had never heard of, which is a worse failure than a blank screen
  (§38: "The user should never need to explain the situation again").

Both halves are now restored, and by one object, so they cannot drift apart.

### Why local persistence and not a server round trip

There is no history endpoint on the client (`AuraApi` has chat, stream,
health, settings — no conversation read), so a server restore would have
meant a new route on both sides. Even built, a launch-time fetch shows an
empty transcript when the phone is offline or the server is down, which
fails §15 precisely where losing history hurts most. Local first is also
what the requirement literally asks for: the history must survive *the
application closing*, not the server restarting.

### The shape, and why it is this shape

Four new files under `data/chat` plus one mapper in `ui/chat`:

| File | What it is |
| --- | --- |
| `data/chat/Transcript.kt` | the contract: `Transcript` (read/write/clear) + `SessionStore` (id), each with a `None` no-op object |
| `data/chat/TranscriptCodec.kt` | pure `object` — encode/decode/bound. No Android types, so it is JVM-testable |
| `data/chat/TranscriptStore.kt` | the one implementation, `EncryptedSharedPreferences`, `Context`-bound |
| `ui/chat/ChatTranscript.kt` | `StoredMessage ↔ ChatMessage`, in `ui` because `data` may not import `ui` |

Two narrow interfaces over one store, copied deliberately from the shape
already in the repository — `SettingsProvider` (read) / `DeviceSettings`
(read+write) / `SettingsStore` (impl). The reason is the same one: the real
store needs a `Context` and a Keystore key, so a JVM test dies in its
constructor. `Transcript.None` and `SessionStore.None` are defaults on both
constructors, which is why every existing call site and every existing test
compiled untouched.

`EncryptedSharedPreferences` + `kotlinx.serialization` because both are
already declared — the build runs `--offline`, so a new Gradle dependency
may not resolve. Its own preferences file (`aura_transcript`), not the
settings one: per-turn transcript churn would otherwise rewrite the URL and
the API token on every message.

### Guarantees that are structural, not remembered

The three that matter are enforced by construction, so a later edit cannot
quietly lose them:

- **`streaming` has nowhere to be stored.** `StoredMessage` has no such
  column, so a half-arrived reply cannot come back looking like it is still
  arriving. A test asserts this against the *encoded text*, not against the
  round trip, so adding the field later fails the test.
- **Emptiness is only ever written on purpose.** `keep()` returns early on an
  empty projection. Every other route to an empty screen is a failure — most
  sharply a read that threw — and writing that back would *destroy* the
  transcript rather than fail to load it (§41: "Do not destroy existing
  data"). Only `newConversation()` clears, and it calls `clear()` explicitly.
- **One writer for the session id.** `send()` used to do
  `_sessionId.set(dto.sessionId)` while `stream()` went through `adopt()`.
  Both now go through `adopt()`, so there is exactly one place a session id
  is written and persisted. Behaviour change worth recording: a *blank*
  session id from the server is no longer adopted; previously `send()` would
  store `""`.

### Four defensive catches, each with a stated cost

Every one of them is a place where the transcript's failure must not become
the app's failure:

| Site | Why it swallows |
| --- | --- |
| `TranscriptStore.prefs` is nullable | an invalidated Keystore key must not stop the app from starting |
| `ChatViewModel.restored()` | this runs in `init`, before the screen draws — an exception is not a lost transcript, it is an app that cannot open |
| `ChatViewModel.keep()` | a failed write leaves the screen alone and leaves `kept` alone, so the next change retries |
| `AuraRepository.remember()` | failing to store the id costs continuity at the next launch; letting the failure out would cost the message being sent now, which the user is watching |

`TranscriptCodec.decode` never throws at all: corrupt JSON, a non-object
root, a non-array `messages`, an unparseable row and an unknown author all
degrade rather than raise. An unknown author is *dropped*, not guessed —
rendering it as AURA would show the user something Aura never said.

All logging is `Log.w(TAG, "...: ${error.javaClass.simpleName}")`. No
message contents and no key material reach the log (§30).

### Two decisions to not delete data

- `SettingsViewModel.save()` and `disconnect()` both call `resetSession()`,
  and both now clear the session id while **keeping** the transcript. The
  transcript is what the user said; deleting it to tidy an inconsistency
  they can see for themselves would destroy their data (§41). "New
  conversation" is one tap away if they want it gone.
- `Transcript.clear()` clears messages only. Forgetting the session is
  `remember(null)`, owned by the repository — the two halves are cleared by
  whoever owns them.

### Recorded loss

`TranscriptCodec.MAX_MESSAGES` is 200. `EncryptedSharedPreferences` rewrites
and re-encrypts the whole value on every commit, so an unbounded transcript
would make each message slower than the last. Past 200 messages the oldest
stop surviving a restart. That is a real loss, written down rather than left
to be discovered.

### A defect found in this phase's own code

The `keep()` collector's first emission is the conversation `restored()` just
loaded. Without a guard, every single launch would serialise and re-encrypt
up to 200 messages to write back exactly what was already on disk. Fixed with
a `kept` field and a `messages == kept` guard.

That fix introduced a second, subtler bug: `private var kept` was first
declared *after* `keep()`, which is after the `init` block that starts the
collector. On `Dispatchers.Unconfined` a `launch` runs its body
synchronously, so the collector read `kept` before its initialiser had run —
and the initialiser then clobbered whatever the collector assigned. Fixed by
moving the declaration above `init`, with a comment saying why the position
is load-bearing, and pinned by the test `a launch does not write back what
it just read`.

### Verification

Tests were written alongside the implementation, so a red state was never
observed naturally. It was produced deliberately instead, twice:

- Removed the `if (messages.isEmpty()) return@collect` guard and made
  `restored()` return `emptyList()` → **6 of 10 failed** (the four load tests
  and both never-write-emptiness tests). Reverted, re-confirmed green.
- Removed `|| messages == kept` → **1 of 11 failed**, exactly the ordering
  test. Reverted, re-confirmed green. `grep -c "MUTATED"` → 0.

Fresh runs, all figures read off the artefacts rather than off "BUILD
SUCCESSFUL" (§34):

- `./gradlew --offline cleanTestDebugUnitTest` then `testDebugUnitTest` →
  **359 tests, 0 failures, 0 errors, 0 skipped, across 26 classes.** Baseline
  was 330, so +29: 14 codec, 11 persistence, 4 repository.
- Freshness proved, not assumed: JUnit XML `timestamp` attributes span
  `2026-08-24T04:40:44`–`04:40:46` against `date -u` of `04:42:32`.
- `./gradlew --offline assembleDebug` →
  `android/app/build/outputs/apk/debug/app-debug.apk`, 19,427,232 bytes.
  The byte size matched the previous two builds exactly, which was
  suspicious enough to check rather than gloss over: opened the APK as a zip
  and confirmed 12 dex files with `TranscriptStore` present in
  `classes4.dex` and `classes6.dex`. The build is genuine; the size match is
  coincidence.
- `.venv\Scripts\python.exe -m pytest -q` → **2201 passed, 1 skipped, 1
  deselected**. Unchanged, as expected — this phase touched no Python.
- `git status --porcelain` → 64 entries, every pre-existing uncommitted item
  still present (§45).

### Files

| File | Change |
| --- | --- |
| `data/chat/Transcript.kt` | new — the contract |
| `data/chat/TranscriptCodec.kt` | new — pure, JVM-testable |
| `data/chat/TranscriptStore.kt` | new — the one implementation |
| `ui/chat/ChatTranscript.kt` | new — the ui↔data mapper |
| `data/AuraRepository.kt` | `SessionStore` param (defaulted), `adopt` as sole writer, `remember` |
| `ui/chat/ChatViewModel.kt` | `Transcript` param (defaulted), `restored`, `keep`, `kept`, `newConversation` clears the store |
| `AuraApplication.kt` | `AppContainer.transcript`, wired into `repository` |
| `MainActivity.kt` | passes `container.transcript` to the factory |
| `test/.../data/chat/TranscriptCodecTest.kt` | new — 14 tests |
| `test/.../ui/chat/ChatPersistenceTest.kt` | new — 11 tests |
| `test/.../data/AuraRepositoryTest.kt` | 21 → 25 tests, new "session id across a restart" section |

### Still owed

The APK has never been installed on hardware. §35 now has one more scenario
worth driving: send a message, force-stop the app, reopen, and confirm both
the bubbles *and* the session id came back — the second half is invisible on
screen and is the half that was most broken.

## Phase 10 — Time Awareness (§16) — IMPLEMENTED (2026-08-24)

§16 sets two prohibitions that pull in opposite directions: "Never rely on
the model guessing the current time" and "Do not hard-code dates". The
inspection came first, because `core/temporal.py` — `TemporalClock`,
`TemporalContext`, `resolve_timezone`, `describe`, the `TIME` prompt
section — **already existed** and predates this mandate. Writing a second
clock would have been the seventh source of "now" that §8 forbids by name.

The gap was **reach, not absence**. Both halves of it:

### What was actually broken

**Gap 1 — the CLI root had no clock.** `core/app.py::Aura.__init__` loaded
the config and then built `ChatEngine()` with no arguments. `ChatEngine`
leaves `clock=None` on purpose — a bare engine is byte-for-byte the Sprint 4
prompt pipeline that a good number of tests depend on — so the consequence
is that every faculty gated on the clock arrives from a composition root or
not at all. `launcher/services.py` always builds one ("there is no
configuration under which Aura should not know what time it is"), so the
*server* was fine; `python main.py` held entire conversations whose prompt
had no `===== CURRENT TIME =====` section. A model with no date in its
prompt does not decline to answer "what day is it" — it invents one, which
is exactly §16's first prohibition arriving through the back door.

**Gap 2 — `temporal.timezone` was readable and unsettable.** The key
existed in `core/config.py`, `TemporalClock.from_config` read it, and
`GET /api/settings`'s `effective` had always carried its value to the
phone. It was **not** in `core/settings_store.py`'s `ALLOWED`, so the phone
could display the zone and never change it. That is the dead control §2
rules out — and it bites hardest in the one deployment the key was written
for: a container running in UTC whose owner is not, and which has no
`config.yaml` to edit. Being confidently an hour out was the only reachable
state.

### Decisions, with the reasoning that forced them

**In-place mutation, not a rebuilt clock.** `TemporalClock.use_timezone`
re-resolves onto `self.timezone`/`self.timezone_name` rather than returning
a new clock, and that is the whole design rather than a shortcut.
`launcher/services.py` builds one clock and hands *the same object* to the
prompt builder, the memory pipeline, the ranked retriever, the quiet-hours
check and the proactive engine — so that "the time in the prompt and the
time on a stored memory cannot disagree". A replacement would move whichever
subsystem received it and leave the rest on the old zone: the exact
disagreement a single shared clock exists to prevent. The default `_now`
closure reads `self.timezone` when it is *called*, so even `RankedRetriever`
— which captured the bound `clock.now` method at construction — follows.
Pinned by `test_use_timezone_reaches_a_captured_bound_method`.

**The constructor and the setter disagree on purpose.** A bad zone name at
construction degrades to system local and logs; the same name through
`use_timezone` is refused and *nothing changes*. Stated in the docstring
because it reads like an inconsistency until the asymmetry is named: the
constructor has nothing better to fall back on, and refusing there would
mean no Aura at all over a typo. A running clock has something strictly
better — the zone already in effect — and silently dropping it would punish
a typo by moving Aura's clock. The caller reports the refusal instead.

**An injected `now` outranks the zone.** `use_timezone` moves the label but
leaves an injected `now` alone: it belongs to whoever injected it, a test
pinning the present or a harness, and the label still moves because the
label is what the prompt prints and it is what was asked for.

**The validator refuses an unresolvable zone rather than storing it.** The
opposite of what `resolve_timezone` does at startup, and deliberately.
Storing it would leave the settings screen showing a zone the clock never
uses — precisely the dead setting `validate_path` refuses by name — while
refusing costs the owner one error message and leaves the value they already
had standing. Nothing is silently mutated either way, which is what §2 asks
of this file. The message names `tzdata` because on Windows an unresolvable
name is usually **not** a typo — the zone is real and the database is
missing — and an error that only said "unknown timezone" would send the
owner hunting for a spelling mistake that is not there. This is not a §2
restriction: the owner is told what to do and their configuration is not
touched.

**One vocabulary for the UTC aliases.** `UTC_ALIASES = ("UTC", "GMT", "Z")`
was extracted from an inline tuple inside `resolve_timezone` because
`core/settings_store.py` now has to agree about them, and a second copy
there would refuse `"Z"` the day this one grew a fourth entry.
`canonical_timezone_name` folds whitespace and the aliases only — IANA keys
are case-sensitive to `ZoneInfo`, so the lowercasing every other name-ish
setting in this codebase applies would break every real zone
(`asia/ho_chi_minh` resolves nowhere), while leaving `"utc"` unfolded would
make the prompt print `(utc, UTC+00:00)`, which reads as some zone other
than the one the owner picked.

**Recorded caveat, not a blocker.** Timestamps are stored naive local, so
changing zone re-dates existing memories by the offset delta: a row written
at 14:00 in one zone reads as 14:00 in the next. That is a property of naive
storage and is identical whether the change lands live or at the next
restart, so it is **not** an argument for demoting this to
`restart_required`. Written into `_reapply_temporal`'s docstring so it is
stated rather than discovered.

### A §30 violation I introduced and then repaired

Worth recording in full, because the mechanism will catch the next person.

The new `temporal.timezone` path had to appear in the Android live fixtures
(`android/app/src/test/resources/live/`), so I regenerated them with
`AURA_WRITE_ANDROID_FIXTURES=1`. Two things I had not accounted for:

1. That flag makes `tests/test_settings_fixture.py` rewrite **all three**
   fixtures — `settings.json`, `providers.json`, `provider_health.json` —
   not just the stale one.
2. A 182-byte `.env` at the repo root holds a **real Gemini API key**, and
   dotenv loads it into `os.environ` at import time under pytest.

So the regeneration baked this machine's live provider state into checked-in
files: `provider_health.json` gained `active: "gemini"`, `chain: ["gemini"]`,
`ready: true`, `problems: []`, and gemini `state: "active"` — which broke
`SettingsContractTest`'s pin on the keyless deployment
(`expected:<[]> but was:<[gemini]>`, 359 tests, 1 failed). Worse, and the
part that is a §30 violation rather than a test failure: **the masked tail
of the owner's real key was written into `providers.json`**
(`"key_masked"`, eight bullets plus the key's last four characters)
and into `settings.json` (the same value under `"gemini"`, where HEAD
had `""`). **The tail itself is deliberately not reproduced here** -
section 30 covers diagnostics, and these state files are committed and
read into context every session.

The fixture file's own docstring is the warning I failed to heed: "It
compares *shape*, not values: `key_masked`, `configured` and the provider
chain all depend on which keys a host happens to have."

**The repair, and why it was not `git checkout`.** §45 forbids
`git checkout -- .` unless the owner instructs it, and here it would also
have reverted phase 1's legitimate `custom` provider additions. The owner's
`.env` was likewise not touched. Instead: two throwaway pytest modules that
`monkeypatch.delenv` every `brain.router.PROVIDER_KEYS` variable plus
`CUSTOM_BASE_URL` **before** `init_runtime()` — before, necessarily, because
the provider chain's `active`/`ready` are decided at build time while
`source_of`/`masked` read `os.environ` at call time — then GET
`/api/settings`, `/api/providers` and `/api/providers/health`, and rewrite
each fixture in the same JSON shape (`indent=2, sort_keys=True,
ensure_ascii=False`, trailing newline). Both throwaways deleted.

Verified: grepping the key's tail across
`android/app/src/test/resources/live/` returns nothing, and all three
fixtures now differ from HEAD by **additions only** (18 / 21 / 6 lines).

**Standing rule for the next phase that touches a settings path:** never run
`AURA_WRITE_ANDROID_FIXTURES=1` on a host with keys in `.env`. Regenerate
through a keyless runtime, then diff every fixture against HEAD and confirm
additions only.

### The host has no timezone database

This Windows box ships no tzdata. `ZoneInfo("Asia/Ho_Chi_Minh")`,
`Etc/GMT-7`, `America/New_York` and `Australia/Eucla` all raise
`ZoneInfoNotFoundError`; only `UTC`/`GMT`/`Z` resolve, and those only because
`resolve_timezone` special-cases them to `datetime.timezone.utc`.

Consequences accepted rather than worked around:

- Tests must never hard-code a specific non-UTC zone's accept/refuse
  outcome. `UTC` is used for the accept path and `Mars/Olympus_Mons` — a name
  no database contains — for the refuse path, so both assertions hold with or
  without tzdata installed.
- `test_a_zone_is_not_lowercased` needs a real IANA key to be meaningful, so
  it tries `Asia/Ho_Chi_Minh` and calls `pytest.skip("no system timezone
  database")` when the validator refuses. That is the 1 skip in this phase's
  count; it skips rather than pretending to pass.
- **Owner-facing limit:** on this host the owner can only set `UTC`.
  `pip install tzdata` lifts that, which is why the validator's error message
  names the package.

### Verification (actually executed, §34)

- `.venv/Scripts/python.exe -m pytest -q` → **`2225 passed, 2 skipped, 1
  deselected in 24.49s`**, from a 2201 baseline: +24 (22 written red first,
  plus 2 end-to-end HTTP tests added during self-review). 0 regressions.
- After the third reach gap was closed: **`2233 passed, 2 skipped, 1
  deselected in 20.21s`** (+8). That run had one intermediate failure worth
  recording rather than hiding — `1 failed, 2232 passed`, the failure being
  `test_a_machine_turn_prompt_has_no_time_or_memory_section`, i.e. the
  pre-existing pin on the invariant the change reverses. It was read before
  it was touched, narrowed to its memory half with the reasoning in its
  docstring, and only then did the suite go green.
- Red was real, not narrated: the 22 tests were written and run before any
  implementation existed → `AttributeError: 'TemporalClock' object has no
  attribute 'use_timezone'`, **5 failed, 81 passed** in
  `tests/test_temporal.py` alone, and `ModuleNotFoundError`-equivalent
  failures in the contract file. Then all green.
- `./gradlew --offline cleanTestDebugUnitTest` BUILD SUCCESSFUL at
  `2026-08-24T05:33:52` UTC, then `./gradlew --offline testDebugUnitTest`
  BUILD SUCCESSFUL, `date -u` `05:34:03`. Counts read from
  `app/build/test-results/testDebugUnitTest/TEST-*.xml`:
  **`classes=26 tests=359 failures=0 errors=0 skipped=0`**, `timestamp`
  attributes `2026-08-24T05:34:00` → `05:34:02` — inside the clean/now
  window, so a genuinely fresh run and not an UP-TO-DATE task (§34).
- `ALLOWED` is **50** entries (was 49); the fixture's `configurable` is 50
  and `== sorted(ALLOWED)`; `effective["temporal"] == {"timezone": ""}`.
- The HTTP path needed **no change**: `server/routes/settings.py:49`'s
  `SettingsPatch` carries `settings: Dict[str, Any]` because "the allow-list
  in `core/settings_store.py` is the real schema", and `patch_settings` calls
  `runtime.settings_service.apply(body.settings)` directly. Confirmed by
  test, not by reading: `{"temporal": {"timezone": "UTC"}}` over the wire
  returns `applied == ["temporal.timezone"]`, `restart_required == []`, and
  the next GET reports it in `effective`; `Mars/Olympus_Mons` returns 422
  with `tzdata` in the body and leaves `effective["temporal"]` untouched.

### Files

| File | Change |
| --- | --- |
| `core/temporal.py` | `UTC_ALIASES`, `canonical_timezone_name`, `TemporalClock.use_timezone`; `resolve_timezone` reads the shared tuple |
| `core/app.py` | the composition root now builds `TemporalClock.from_config(self.config)` and hands it to `ChatEngine` |
| `core/settings_store.py` | `_timezone_name` validator; `"temporal.timezone"` in `ALLOWED` (49 → 50) |
| `server/settings_service.py` | `LIVE_PATHS` entry, `_reapply_temporal`, dispatch-loop entry, docstring table row |
| `tests/test_temporal.py` | +5 tests (81 → 86) |
| `tests/test_settings_contract.py` | new `TestTimezoneSetting` (6), 3 live-apply tests, 2 end-to-end HTTP tests, `build_service` fixture gained `clock`, parametrize list gained the path |
| `tests/test_app_wiring.py` | **new** — 3 tests; `core/app.py` had no test file at all |
| `android/.../data/SettingsContractTest.kt` | 49 → 50, `"temporal.timezone"` named in the enumerated list |
| `android/app/src/test/resources/live/*.json` | the new path in `configurable`/`effective`; all three now additions-only vs HEAD |
| `brain/prompt_builder.py` | `_build_agent_prompt` gained `temporal`; `# 2.8` block extends `self._build_time` above AGENT RULES; `build()`'s tick branch passes it through |
| `brain/conversation.py` | `_prepare`'s machine branch passes `temporal=self._temporal_lines()` |
| `tests/test_machine_turns.py` | new `TestATickKnowsWhenItIs` (8); `manager()` gained `clock` |
| `tests/test_memory_integration.py` | machine-turn pin narrowed to its memory half and renamed; docstring records the reversal |

### A third §16 reach gap, found during self-review and closed

Phase 10 fixed two places where a clock existed but never reached a
prompt. There was a third, and it was the one that mattered most for
§23: **the Android agent-tick prompt carried no time at all.**

The chain, each link verified in the file rather than assumed:
`brain/conversation.py`'s machine branch called `builder.build()` with no
`temporal` argument; `build()`'s tick branch dropped the parameter before
dispatching; and `_build_agent_prompt` had never had one to drop.

Why this is a live exposure and not a theoretical one. The tick's action
vocabulary includes `input_text`, whose `text` field is free-form, and
the owner's request reaches the model **in the owner's own words** —
"hôm nay", "today", "tomorrow morning" are ordinary things to type into
an assistant. A model told to type a date, with no date anywhere in its
prompt, does not decline. It invents one. That is §16's first sentence.

Why it was right to reverse an existing pin rather than respect it.
`tests/test_memory_integration.py` asserted that a machine turn's prompt
contains neither TIME nor MEMORY, and its docstring gave the rule: *every
section that exists to make Aura sound like herself is absent*. TIME does
not satisfy that rule. It is not a personality section — it is a fact
about the present, the same category as DEVICE STATE, which the tick
prompt has always carried. The two sections were pinned together because
they landed in the same sprint, not because one rule covered both. The
MEMORY half is well-founded and stayed: a device step that quotes the
owner's private facts at a JSON parser has spent them for nothing.

So the test was narrowed to its real subject — it lives in a *memory*
integration file — renamed to
`test_a_machine_turn_prompt_has_no_memory_section`, and its docstring now
records that the time half was reversed on purpose, with this reasoning
and a pointer to the class that owns it. `CONVERSATIONAL_SECTIONS` in
`tests/test_machine_turns.py` was checked first and never listed CURRENT
TIME, so no other pin was contradicted.

Three properties made the change cheap. `_build_time` was reused rather
than re-rendered, so the section header and the omit-when-empty rule keep
one definition. `_temporal_lines()` already returns `[]` when no clock was
injected, so a deployment without one gets a **byte-identical** tick
prompt — pinned across `(None, [], [""], ["   "])`. And it already
swallows a raising clock at debug level, so a broken clock costs the time
and not the tick.

Placement: below the accessibility tree and immediately above AGENT
RULES, not beside DEVICE STATE where it belongs by category. The tree is
the largest block in this prompt and the request sentence lives in AGENT
RULES; a date placed above the tree is a date read a long way from the
sentence that needs it.

Eight tests, red first (`4 failed, 4 passed`, the shown failure an
`IndexError` on splitting a prompt with no TIME section). Two of them are
the ones §16 actually asks for: the year and day are read off a real
`datetime.now()` rather than compared to a literal, which is what
"do not hard-code dates" means for a test.

### Still owed

Phase 23 UI debt grows by one: the Hub has no graphical control for
`temporal.timezone`, joining the five `llm.task_models.*` lanes and
`llm.custom_base_url`/`llm.custom_model` — eight paths that are in
`configurable` and settable over the PATCH API today but have no widget.
Deferred per §32 (functionality first), recorded so it is not lost.

## Phase 11 — Memory 3.0 (§17) — IN PROGRESS (2026-08-24)

### Part 1: `memory.recall` was a dead owner control with four breaks

Found by verifying a subagent's map against the source rather than
trusting it — two of the four are things the map got wrong or missed.

**The chain, every link confirmed in the file.** `core/settings_store.py`
makes `memory.recall` owner-settable. `server/settings_service.py` lists
it in `LIVE_PATHS` and documents it in the handler table. The Hub ships a
toggle for it. And:

1. **`memory_lines` never read the flag.** `build_memory_pipeline` wrote
   `pipeline.recall_enabled` from `memory.recall`; the gate in
   `memory_lines` tested its own *parameter* `recall_episodic: bool =
   True`, and the sole production caller (`brain/conversation.py:886`)
   passes no arguments. So the literal `True` in the signature decided
   and the owner's value was stored and read by nobody.
2. **`_reapply_memory` reached for the wrong object.** It did
   `getattr(getattr(services, "memory"), "pipeline", None)`, but
   `Services` keeps `pipeline` as a **sibling** of `memory`
   (`launcher/services.py` line 36 vs line 31) and `services.memory` is
   the `MemoryManager`, which has no `pipeline` attribute. A guaranteed
   `None`; the assignment never ran once in production.
3. **The handler reported success unconditionally.** `apply` has two
   handler protocols: an unconditional group returning `None` whose paths
   stay in `applied`, and a conditional group returning `bool` where a
   `False` demotes the path to `restart_required` — *"`applied` is a
   promise"*, in the code's own words. `_reapply_memory` sat in the first
   group with an optional target, so a deployment with no pipeline at all
   was told `applied: ["memory.recall"]`.
4. **The legacy half needed a restart.** `memory.recall` also picks
   `KeywordRetriever` vs `NullRetriever` in `launcher/services.py`, which
   happens once at build time. Even with 1–3 fixed, `applied` would have
   been half true.

Two docstrings asserted the behaviour that did not happen —
`memory/pipeline.py` claimed *"switching recall on or off does one thing
everywhere"* and the handler table claimed *"read per turn"*. Both were
corrected rather than left as aspirational.

**Why it survived:** the unit tests asserted `build_memory_pipeline`
*stores* the flag, and the settings tests asserted PATCH *reports* the
path as applied. Both passed. Nothing asked whether a turn changed. The
`build_service` fixture also modelled the wrong shape — `present` had a
`memory` key and no `pipeline` key — so a test of the real bug could not
have been written against it. Fixed.

### The semantics conflict, and why the phone won

Fixing break 1 alone would have been a **new** §2 violation in the
opposite direction, and catching that needed the config rather than the
code. Two places in the repository disagree about what `memory.recall`
means:

- `config.yaml` calls it *"keyword search over the older transcript. OFF
  ON PURPOSE"* — the Sprint 5 mechanism, which would scope the key to the
  legacy retriever alone and make wiring it to the pipeline an over-reach.
- The Hub calls it *"Use memory in replies / Look things up from past
  conversations while answering"*, and `PrivacySection.kt:239` lists it
  under **privacy** as *"turn recall and the profile off"*.

**The phone governs.** §2 makes the application's settings screen the
owner's contract surface, and this is a *privacy* promise: being wrong in
that direction means past conversation content reaching a prompt after
the owner said not to, which is strictly worse than a capability the
owner can restore with one tap. So the phone was displaying "off" while
Aura recalled — the display was not the thing that was wrong.

**Behaviour change, stated rather than buried.** The shipped default is
`recall: false`, so honouring it *reduces* what a current deployment
injects into prompts. The owner has been getting ranked episodic recall
against their own configuration. One toggle restores it, and the toggle
now does what its label says. `config.yaml`'s comment was corrected to
describe both mechanisms and to say this outright; **the value was not
touched** (§2 forbids silently mutating owner configuration — verified
that `recall: false` appears in the diff only as context).

### Verification (actually executed, §34)

- Red first, three levels: pipeline gate (`1 failed, 3 passed`), live
  apply (`3 failed`), end to end (`1 failed`), then the legacy half
  (`3 failed, 3 passed`).
- **Mutation-tested in both directions**, because "the tests pass" is not
  evidence that they discriminate. Gate forced closed → **6 failed**
  (including the pre-existing `test_pipeline_recalls_what_it_stored`).
  Gate forced to ignore the owner, i.e. the original bug restored → **2
  failed**, exactly the new negative tests. Restored → 124 passed.
- `.venv/Scripts/python.exe -m pytest -q` → **`2247 passed, 2 skipped, 1
  deselected in 20.27s`**, from 2233: **+14**, 0 regressions.
- **A test discriminator of mine was wrong and the suite caught it.** The
  first end-to-end test asserted `"migration" not in prompt` while the
  query was *"how did the migration go"* — the word reaches the prompt as
  the user's own message whatever recall does. It failed for that reason,
  not for the code's. Worse, it made the *paired positive* test vacuous:
  it would have passed with recall entirely broken. Both now discriminate
  on `"sqlite"`, which exists only in the stored episode.

### Files

| File | Change |
| --- | --- |
| `memory/pipeline.py` | `recall_episodic: bool \| None = None` defers to `self.recall_enabled`; docstring records the conflict resolution |
| `server/settings_service.py` | `_reapply_memory` → no-arg `-> bool`, right object, moved to the conditional group; new `_swap_legacy_retriever`; table entry corrected |
| `core/config.py` | comment corrected — the key gates both mechanisms |
| `config.yaml` | same, plus the behaviour-change note; **value untouched** |
| `tests/test_memory_2.py` | +4 gate tests |
| `tests/test_settings_contract.py` | +7 live-apply tests; `build_service` gained `pipeline` and `knowledge` |
| `tests/test_memory_integration.py` | +3 end-to-end tests |

### Still owed on phase 11

The tier work itself (§17: working / episodic / semantic / procedural).
Confirmed against the repository: **episodic** exists and is good;
**working** exists unnamed as `TemporaryContext` (3h TTL, 12 entries,
never persisted); **semantic** is fragmented across three unrelated
things called "fact" (`user_facts`, CLI-write-only; `user_model`, rich but
only written by the seeder; `companion.Fact`, never written in
production); **procedural** does not exist — `procedural`, `working`,
`consolidat*`, `decay`, `salience` return zero matches repository-wide.
§17's *"do not blindly save every conversation line as permanent memory"*
is **already satisfied** by `MemorySelector` (six hard rejections, a
first-person requirement, then a scored threshold); the unconditional
transcript write is §15's visible history, not permanent memory, so it is
correct as it stands.


---

## Phase 11 part 2 — the semantic tier gets a runtime caller (§17) — 2026-08-24

`MemoryPipeline.remember_user_stated` and `remember_user_correction` were
written, tested, and called by **nothing outside the test suite**. So no
matter what the owner told Aura, she could not keep a durable keyed fact
about them: the episodic tier saved the *sentence*, and the fact the
sentence carried went nowhere. `UserModel` — ten categories, confirmed vs
inferred provenance, validity windows, relevance ranking — was reachable
only by the seeder.

This is the same defect shape as part 1 (`memory.recall`) and as the third
§16 reach gap: **the machinery exists, the tests pass, and nothing crosses
the last link.**

### Why a tool, and not extraction over every message

Two reasons, both from the repository rather than preference:

1. The keys are namespaced — `identity.name`, not `name`. Turning
   "I'm Thien btw" into that key is a judgement, not a pattern. Regex
   extraction invents keys or misses most facts; a second LLM call per
   message pays a call per message for facts that appear in maybe one.
2. `memory/user_model.py` is explicit that CONFIRMED means *the user
   actually said it*, and that `confirm()` is the only door in. A tool
   call is that — the model read the message, chose the key, and asked. A
   background scraper inferring intent is not, and would quietly fill the
   confirmed tier with guesses, which that module's own docstring forbids.

Tools were verified reachable first: `brain/conversation.py::_resolve_tools`
runs a requested tool mid-turn and feeds the real result back, so a tool is
a live path and not another dead one.

### Four things had to be true, not one

- **`CATEGORIES`** (`memory/user_model.py`). The ten category constants
  carry a comment promising "a typo is an ImportError rather than a
  silently unqueryable row". That holds only for callers that *import*
  them; a category chosen by a language model imports nothing and cannot
  get an ImportError, so `category="notes"` would write a row that
  `all(category=)` can never return. The set re-establishes the guarantee
  at the boundary where untrusted text becomes a row.
- **`RememberTool`** (`tools/builtins/memory.py`). SAFE, with the
  reasoning written down because it is arguable — see below. Every
  argument is validated because every argument was chosen by a model, and
  refusals carry a reason the model can retry from, including the
  vocabulary it should have used.
- **`timeout = 0`**, and this is required rather than tuning.
  `call_with_timeout` runs a tool on a daemon thread; a SQLAlchemy SQLite
  session belongs to the thread that opened it. A threaded `remember`
  raises `ProgrammingError` **every time, in production exactly as in
  tests** — and it did, the first time the suite ran it through an
  executor instead of calling `execute` directly. `tools/timeout.py` names
  this case in its own docstring: a timeout of 0 "runs the call inline on
  this thread", for a tool that otherwise "loses the ability to touch
  thread-affine state". The deadline is what is given up; affordable for a
  handful of rows in a local file Aura is the only writer of, and the
  thread could not have killed the write anyway.
- **The wiring.** `build_registry(config, memory)` /
  `build_tools(..., memory=)` gate registration on the pipeline the same
  way the filesystem tools gate on `allowed_paths` — absent dependency
  means absent tool, not a tool that accepts a fact and drops it.
  `launcher/services.py` passes the pipeline (which it already builds
  before tools, so no reordering was needed), and `config.yaml` names
  `remember` in `tools.allowed`.

### Why SAFE

The taxonomy in `tools/base.py` grades damage *outside* Aura: SAFE reads a
clock, SENSITIVE moves the owner's data somewhere it was not, DANGEROUS
changes the machine. `remember` sends nothing outward, touches nothing on
disk but Aura's own database, and files something the owner said moments
ago in the process that already had it.

The counter-argument is real — it writes, and the write persists. What
settles it is the cost of being wrong each way. Too loose: a bad call puts
a wrong fact in the profile, which `forget` undoes. Too strict:
`auto_approve: [safe]` plus no human to ask in server mode refuses every
call, and the tier is unreachable again with a permission error standing in
for a design decision.

### The owner's side (§2)

`config.yaml`'s `tools.allowed` gained a line, and its comment says so in
the owner's own file: what the tool does, that it is new, that it changes
what Aura does rather than only what she could do, and that deleting the
line reverts it — the tool then stays registered and inert, a refusal
rather than an error. A visible default change with its reasoning attached,
not a silent runtime mutation.

`DEFAULT_CONFIG` was deliberately **left alone**: a fresh install has
`tools.enabled: False` and `allowed: []` ("two locks"), and adding to it
would contradict that stated design.

### Two mutations survived, and only one was a bug

Mutation testing rather than trusting green, and it paid twice:

- **Dropping the pipeline argument** from the `_build_tools` call in
  `launcher/services.py` left the **full suite green** — every test above
  builds its own executor, so none of them touched the one executor the
  process actually uses. `remember` would have been absent from every real
  catalogue with 2263 tests agreeing. Closed by
  `test_the_composition_root_hands_the_pipeline_to_the_tools`, which drives
  the real `build_services`; the mutation now fails it (and
  `_warn_about_policy` independently logs "tools.allowed names remember,
  which is not registered", so the owner would have been told at startup).
- **Deleting the line from `config.yaml`** also left the suite green, and
  that one is **correct**: it is the documented owner control working. A
  test pinning the owner's editable file would fail when they exercise a
  documented option, so none was written.

Four mutations against the tool's own guards were all caught: category
validation removed (2 failures), empty-value guard removed (1), CONFIRMED
downgraded to `infer()` (2), `timeout = 0` removed (the thread error).

### A discriminator that would have passed with the feature broken

`assert "remember" in prompt` was wrong for the same reason
`assert "migration" not in prompt` was wrong in part 1. The persona and
memory sections say "remember what matters across conversations" and "do
not pretend to remember something that is not in front of you" in *every*
prompt Aura builds — so the naive positive passes with the catalogue
entirely broken, and its negative fails with the catalogue correctly empty.
Both now go through `catalogue_of()`, which slices out the `TOOLS` section,
and the helper's docstring records the trap.

### Files

| File | Change |
| --- | --- |
| `memory/user_model.py` | `CATEGORIES` frozenset + why the ImportError promise does not cover model-chosen text |
| `tools/builtins/memory.py` | **new** — `RememberTool`, SAFE, `timeout = 0`, all arguments validated |
| `tools/factory.py` | `build_registry(config, memory)` / `build_tools(..., memory=)`; registration gated on the pipeline |
| `launcher/services.py` | `_build_tools(config, bus, pipeline)` — the composition root hands the pipeline over |
| `config.yaml` | `remember` in `tools.allowed`, with the owner-facing note |
| `tests/test_tools.py` | +11 — storage, provenance, category refusals, blank refusals, update-not-duplicate, risk, thread affinity |
| `tests/test_tool_calling.py` | +6 — real loop to real executor to real database, catalogue presence/absence, unlisted-by-owner, absent-without-pipeline, composition root |

### Verification (actually executed, §34)

- `.venv\Scripts\python.exe -m pytest -q` gives **2264 passed, 2 skipped, 1
  deselected** (from 2247; +17, 0 regressions).
- Mutation runs as described above: 4 caught on the tool, 1 real gap found
  and closed in the wiring, 1 survivor correctly left alone.
- Android: **not run.** No Kotlin production code changed in this part.

### Still owed on §17

`MemoryTier` vocabulary naming the four tiers, and the **procedural** tier,
which still does not exist. `working` remains unnamed as `TemporaryContext`
and `semantic` remains fragmented across `user_facts` (CLI-write-only) and
`companion.Fact` (never written) alongside the `user_model` this part
wired — the fragmentation is documented, not yet resolved.

## Phase 11 part 3 — the procedural tier, and a §10 defect found on the way in — 2026-08-24

Part 3 set out to give the procedural tier — "how to do things" — a runtime
caller the way part 2 gave one to the semantic tier. It ended somewhere
better: two designs for that tier were worked out and both discarded on the
repository's own evidence, and the search for a real procedural fact to
learn turned up a live §10 defect in `same_app` that has now been fixed.

The defect is worth stating first, because it is the part that changes what
Aura does on a device.

### The repeat loop, reached from the other side

§10 names the behaviour to prevent: `open_app open_app open_app ...` after a
launch that already succeeded. Phases 6 and 7 attacked that from the
verification side — a node must not re-run once SUCCESS. But the same loop
is reachable from the recognition side, and that path was still open.

`brain/planner.py::is_done` answers "is this OPEN_APP step already
satisfied?" by asking `same_app(step.detail, record.target)` over the
succeeded actions. `same_app` matches narrowly on purpose: it squashes and
lowercases the display name and asks whether that appears in the package.
Narrow is the right instinct — a loose match would advance a plan past a
step that never happened — but the cost is the opposite error. An app whose
name appears nowhere in its package can never be recognised. A launch that
genuinely succeeded is never marked done, the next tick re-issues OPEN_APP,
and the device opens an app that is already in the foreground, forever.

Not a hypothetical. `same_app` was run against ten real packages before any
code was written, and seven missed:

| Owner says | Real package | Before |
| --- | --- | --- |
| Messenger | `com.facebook.orca` | miss |
| X | `com.twitter.android` | miss |
| Gmail | `com.google.android.gm` | miss |
| Play Store | `com.android.vending` | miss |
| Phone | `com.google.android.dialer` | miss |
| TikTok | `com.zhiliaoapp.musically` | miss |
| Messages | `com.google.android.apps.messaging` | miss |
| Google Maps | `com.google.android.apps.maps` | ok |
| Photos | `com.google.android.apps.photos` | ok |
| WhatsApp | `com.whatsapp` | ok |

Every miss has a reason no heuristic could have guessed. Facebook's
messenger has been "orca" internally since before it was split into its own
app; the Twitter rename left the package untouched; TikTok ships under the
name of the app it was built from; Gmail predates the convention its
siblings follow. These are among the most common apps a Vietnamese owner has
installed, which is why a gap in a string comparison mattered enough to fix.

### Exact packages, never substrings

`APP_ALIASES` maps a whole normalised name to a frozenset of exact packages,
and both halves of that are load bearing.

Keyed on the *whole* name, because a substring reading of `"x"` would satisfy
very nearly every package on the device. Holding *exact packages*, because a
loose `messenger` entry would let `com.facebook.katana` — Facebook itself —
through. That is the dangerous direction: a false positive advances a plan
past a step that never happened, and Aura then types into an app she is not
in. A missing entry merely falls back to the substring heuristic, so a name
absent from the table is no worse off than it was before this change.

The lookup goes first, before the heuristic, because it is the cheapest check
and the least ambiguous. It **adds** readings without removing any — an owner
who still says "Twitter" is served by the substring rule exactly as before.

Two tests hold that shape rather than the table's contents:
`test_an_alias_does_not_make_matching_loose` and
`test_an_alias_is_not_a_substring_rule`, plus five parametrized negatives
naming the specific wrong app each alias must refuse (Messenger→katana,
Gmail→messaging, Messages→gm, Phone→vending, TikTok→orca).

### One fix, six call sites

`same_app` is called from `brain/planner.py:317`, `brain/recovery.py:148,178,189`
and `brain/task_graph.py:204,234`. Widening it in one place fixes recognition
in the planner, the recovery engine and the task graph at once — which is why
the end-to-end test goes through `is_done` and not through `same_app`:
`is_done` is what a tick actually asks, and the other five sites inherit the
fix without a line of their own.

### Two designs for the procedural tier, both discarded

Neither was discarded for being hard. Each was discarded because the
repository already answers the question it was trying to solve.

**Caching plans as procedural memory.** The obvious shape: remember that
"open YouTube and search X" decomposes into six steps, and skip the
decomposition next time. Worthless here. `plan_for` is a pure deterministic
function — `test_the_planner_never_calls_a_model` asserts its signature is
exactly `["request"]` — so there is no model round trip to save and no
variation to learn from. Caching a deterministic function of one argument
buys nothing but a cache invalidation bug.

**A module-level alias resolver.** The learned version wants aliases to grow
from experience, which suggested a module global that `same_app` consults and
a runtime path that writes to it. Rejected after checking how this repository
actually injects things: `set_tool_confirmation` is a *method* on
`launcher/runtime.py:249`, called from `launcher/cli.py:48`. The idiom is a
composition root handing dependencies to objects, not modules reaching for
globals. A global would have been a new idiom introduced for one function,
and `same_app` is a free function with six call sites across three modules —
none of which owns state.

### What the learned tier actually needs, and why it is not here

The honest version of a learned procedural tier is: the device reports which
package it actually launched for the name it was given, and Aura files that
pairing. That is a device-contract change — a new field flowing back from the
Accessibility agent — and §35 requires it be driven on real hardware before
being called done. This host has no device attached. So the fully learned
procedural tier is **Not Implemented**, stated as such rather than sketched,
and the fixed table is what ships.

### Files

| File | Change |
| --- | --- |
| `brain/planner.py` | `APP_ALIASES` table (7 entries) plus a 4-line exact-package lookup at the top of `same_app` |
| `tests/test_planner.py` | `same_app` added to the imports; `TestAppsWhoseNameIsNotInTheirPackage` with 15 cases; 2 end-to-end `is_done` tests |

### Verification (actually executed, §34)

- `tests/test_planner.py` — **50 passed** (32 test functions, parametrized to 50 cases; was 35 before this part)
- Full backend suite — **2286 passed, 2 skipped, 1 deselected** in 34.72s (was 2264 at the end of part 2; +22, no regressions)
- Honest red first: the 7 alias tests failed with `NameError` on `same_app`
  before the import was fixed, then 3 failed / 32 passed against the
  unmodified planner — a real red, not an assumed one.

Mutation testing, both mutations caught:

| Mutation | Result |
| --- | --- |
| Alias lookup deleted from `same_app` | **10 failed** |
| Lookup made a substring match instead of exact | **3 failed**, including Messenger→katana and Gmail→messaging |

Restored cleanly to 50 passed after each.

No Kotlin production code changed in this part, so no Android build was run
and the phase 9 APK remains current. §35 live device tests are still owed.

### Still owed on §17

Unchanged from part 2, minus nothing: the `MemoryTier` vocabulary naming the
four tiers, and a procedural tier that learns rather than one that is
tabulated. Semantic fragmentation across `user_facts` (CLI-write-only) and
`companion.Fact` (never written in production) alongside the now-wired
`user_model` is still documented rather than resolved — §41 forbids deleting
owner data, so consolidation needs a migration path, not a delete.

---

## Phase 12 — the Event Bus grew an observer, and the agent learned to speak — 2026-08-24

### A bus nobody was listening to

`events/bus.py` had been correct since before this program started: an
`RLock`, a snapshot taken inside the lock and dispatched outside it, handler
exceptions swallowed into `logger.exception` so one bad subscriber cannot
take down a publish, and base-class subscription that reaches subclasses.
None of that was the problem.

The problem was that `subscribe_all` had **zero production callers**, while
`events/__init__.py` and `subscribe_all`'s own docstring both described
logging as its user. Two docstrings claimed a subscriber that did not exist.

The concrete cost is worth stating, because "add logging" reads like
housekeeping until you name the bug it blocks. A proactive notification that
never arrives on the phone has three possible causes:

| cause | what the log would show |
|---|---|
| never published | no `event CompanionNotificationEvent` line at all |
| aged out of the outbox | published, then no delivery |
| drained by another device | published and delivered, to someone else |

One symptom, three bugs, and no way to tell them apart. That is what an
unobserved bus costs.

### Default-deny, because section 30 decides this file

`events/log.py` is not a formatter with a redaction feature bolted on. The
redaction rule *is* the design:

- **Strings are denied by default.** A `str` field is written as
  `<N chars>` unless its own event opts it in via a `log_fields` tuple.
  §30 requires that API keys never appear in normal logs, never appear in
  chat history, never be exposed by diagnostics. A default-allow logger
  with a blocklist is one new field away from violating that; a
  default-deny logger with an allowlist is one new field away from being
  slightly less informative.
- **`SAFE_FIELDS`** — `name`, `source`, `reason`, `priority`, `detail`,
  `message` — are the fields that exist to be read by a human.
- **Enum is checked before `str`, and this is the ordering that matters.**
  `AuraState`, `Mood` and `Expression` are all declared `(str, Enum)`, so a
  plain `isinstance(value, str)` catches them, and `state=thinking` — the
  single most useful thing on the bus — would be redacted to a character
  count. `bool` before `int` for the same reason: `bool` is an `int`
  subclass.
- **Containers are named, never walked.** A dict, a list, a nested
  dataclass logs as `arguments=<dict>`. This is what keeps a tool call's
  arguments out of the log without anyone maintaining a list of which
  tools take secrets.
- **`QUIET_EVENTS`** excludes `BlinkEvent` (an idle timer) and
  `StreamChunkEvent` (one per fragment) by `isinstance`, so a subclass
  inherits the exclusion along with the behaviour that earned it.
- **`ErrorEvent` writes at WARNING**, everything else at DEBUG.

Wired in `launcher/services.py` unconditionally, immediately after the bus
is constructed and before anything that could publish. Not behind a flag:
it writes at DEBUG, `logging.level` is already the owner's control over
whether any of it is visible, and a second switch would let the two
disagree — §31, one canonical model.

### The layer that had the most to say and no way to say it

Phases 4–11 built a cognitive state, a planner, a task graph, a
verification pass and a recovery engine. Between them they published
**nothing**. The one thing on this system worth watching — an agent
working a real task on a real phone — was the one thing the bus could not
see.

Three events, and the shape of them is the whole argument:

| event | when |
|---|---|
| `TaskStepChangedEvent` | the current step changed |
| `TaskFinishedEvent` | every node settled |
| `TaskStuckEvent` | nothing left to try, and not because it's done |

**Edge triggered, not per tick.** `_plan` rebuilds the whole graph on every
tick, because `plan_for` is pure and deliberately so —
`test_the_planner_never_calls_a_model` pins its signature to exactly
`["request"]`. A naive publish would therefore emit the same step forever
and put §10's repeat loop onto the bus as noise, which is the opposite of
what an observer is for. An event here means something *moved*.

### "No current node" has two causes, and the first version conflated them

The first `_announce` opened with `if was == node: return`, which is right
for the repeating case and wrong in a way that took a failing test to see:

    a task not yet started   ->  task_node == ""
    a task already finished  ->  task_node == ""

A device can report a task already complete on its **first** tick — every
action verified, nothing left to do. `was` is `""`, `node` is `""`, the
guard fires, and the finish is never announced. Fixed by reading
`had_plan = bool(state.plan)` *before* `set_plan` writes it, so arrival at
"no current node" is distinguishable from sitting there:

    if not (was or not had_plan): return

Both reads now happen before both writes, which is the only reason the
ordering is load-bearing enough to be worth the comment it carries.

### Finished and stuck are not the same absence

`graph.current is None` cannot be the signal on its own. `is_finished`
means every node settled; `is_stuck` means there is no current node and the
work is *not* done. A subscriber that confused them would congratulate the
owner on a search that never happened.

### Files

| file | change |
|---|---|
| `events/log.py` | new — the bus's first observer, default-deny |
| `events/types.py` | +3 task events |
| `brain/conversation.py` | `_announce`, and the read-before-write in `_plan` |
| `launcher/services.py` | `event_log` on `Services`, attached at the root |
| `tests/test_events.py` | 17 → 34 |
| `tests/test_machine_turns.py` | +10 |

### Verification (actually executed, §34)

Backend: `2313 passed, 2 skipped, 1 deselected`, up from 2303 at the end of
phase 11.

Mutation testing, part 1 (`events/log.py`) — 5 of 6 caught. The survivor
was `SKIP_FIELDS = frozenset()`, and it was right to survive: a dict falls
through to the generic `arguments=<dict>` branch whether or not a skip list
names it. `SKIP_FIELDS` was a second mechanism for a guarantee the generic
branch already made, and it is now deleted rather than tested.

Mutation testing, part 2 (`_announce`) — every mutation caught, but only
after two of them were run twice:

| mutation | result |
|---|---|
| repeat guard removed | 1 failed |
| arrival guard removed | 1 failed |
| `had_plan` term dropped | 1 failed |
| `_announce` unwired | 5 failed |
| stuck never announced | 1 failed |
| stuck published as finished | 2 failed |
| stuck loses its step | 1 failed |
| `index` replaced by `0` | **survived**, then 1 failed |
| `elif graph.is_stuck:` → `or True` | **survived** — equivalent |

The `or True` survivor is a genuinely equivalent mutation: `is_stuck` is
defined as "current is None and not finished", and `_plan` returns before
`_announce` on an empty plan, so the branch cannot be reached in a state
where the disjunct changes the answer. But checking *why* it survived found
that the stuck branch had **no test whatsoever** — the equivalence had been
hiding a coverage hole, and two tests now cover it.

The `index=0` survivor was a plain missing assertion: the only test looked
at a first tick, where the correct index *is* zero, so a hard-coded
constant passed. A second tick after a verified launch now pins
`index == 1`.

## Phase 13 - the limits stopped resetting themselves - 2026-08-24

Section 19 is titled "Background 24x7", and the obvious reading of it -
build a scheduler that never stops - is the wrong one for this
deployment. Most of what section 19 asks for is already here and already
correct: the phone drives the clock through `NotificationWorker`
(15-minute floor, network constraint, `KEEP` policy, exponential
backoff, every gate re-checked per run), and `brain/agent_mode.absorb`
re-sends the whole action history every tick, so a server that dies
mid-plan loses nothing. The Hub UI already says out loud that there is
no background scheduler on the server.

So the honest target was not a process that never dies. It was a system
that **survives being killed** - and two things did not.

### A daily limit that reset on every reboot

`ProactivePolicy` held its send history in `deque(maxlen=64)` in RAM,
and every limit the owner configures was derived from it:

| setting | question it asks of the history |
|---|---|
| `max_per_day` | how many were sent today |
| `cooldown_seconds` | when was the last one, any category |
| `category_cooldown_seconds` | when was the last one of this kind |
| `duplicate_window_seconds` | was this text said recently |
| `similarity_threshold` | was something close to it said recently |

All five, from one list that died with the process. The owner sets "no
more than four a day", Aura sends four, the laptop closes, and four more
are allowed the same afternoon. Nothing overrode the setting; it simply
forgot. Section 20 says do not spam notifications and section 2 says
AURA must not silently override owner configuration - a cap that resets
itself honours neither.

`proactive/ledger.py` is the fix, and it copies `core/settings_store.py`
rather than inventing a format: version 1 documents, per-row validation
that drops the offender and keeps the rest, an unreadable file logged by
exception class only and then left exactly where it is, and an atomic
`.tmp` + `os.replace` write.

Three decisions inside it worth keeping:

- **The bound lives in one place.** `save` writes exactly what it is
  handed and has no size limit of its own, so the caller's
  `deque(maxlen=64)` is the only definition of how much history exists.
  A second limit here would be a second mechanism for a guarantee the
  caller already makes.
- **Whole-file writes, not append.** An append-only file grows past a
  `maxlen` that a deque enforces in memory. Sixty short rows a few times
  a day costs nothing.
- **The write cannot raise.** A failed write means a limit forgotten at
  the next start; an exception means a message the owner allowed is not
  delivered now. The first is the failure they would rather have, and it
  is the only place this diverges from the settings-store precedent.

### The greeting dict was the same fact, kept twice

`ProactiveEngine._greeted` was a dict of date to set of parts of day, so
Aura would not say good morning twice in one morning. But
`core/temporal.part_of_day` is a pure function of any datetime, and the
policy's history already records every greeting with the time it went
out - so the dict was a **second independent copy** of something already
recorded, which is what section 8 forbids.

Two problems, one cause. Deriving the answer from the history removed
the duplicate and fixed the re-greeting in the same move, and deleted
`_prune_greetings` along with it - there is nothing left to prune.

### Then the fix exposed its other half

With the send history durable, the engine's remaining volatile field
became the visible one. `_last_user_message_at` is set by `note_chat()`,
`seconds_since_user()` reads a missing value as infinity, and the
greeting rule reads infinity as "they have been away". A probe, not a
guess:

    still talking ->  (silent)
    after restart ->  greeting
    seconds_since_user: inf

The owner is mid-conversation, the process restarts, and Aura opens with
a welcome-back to somebody who spoke a minute ago. Section 21 says AURA
must not silently perform high-impact actions merely because it detected
an event, and an unprompted message triggered by *forgetting* is the
weakest justification there is.

Same shape as the greeting fix: every real chat turn already writes a row
to the `messages` table, so the answer is already on disk.
`MemoryManager.last_said_at(role, session_id)` was added there rather
than in the proactive package, because that class owns the table and the
proactive package has no business reaching into another module's storage.

The engine keeps `note_chat()` as the live signal and asks the source
only when it has no live answer. That is not a section 8 duplicate but a
cache with a narrower lifetime: the durable record is the one that
outlives the process, and the live value is discarded and re-derived
rather than maintained in parallel.

### The defect that was wired and dead

`last_said_at` was written with a session_id defaulting to "default",
matching `get_recent` above it. A probe against a phone-style session id:

    last_said_at()                            -> None
    last_said_at(session_id="android-9f2c41") -> 2026-08-24 18:54:43

`server/session.py` says it plainly - "an Android install keeps one
across launches" - so the primary client supplies its own session id and
every message the owner had ever sent from their phone was invisible to
the query. The wiring was present, the tests were green, and the feature
did not exist. That is section 44 exactly: wired is not implemented.

Not filtering is also the *right* question rather than a patch around a
wrong one. `server/runtime.py` states the deployment - one person, one
Aura, the auth token as the only identity boundary - and there is one
proactive engine per process, not one per session. "Has the owner spoken
lately" has no session in it. Naming a session still narrows it, because
the table really is stored that way.

### A volatile counter that turned out to be harmless

`ProactiveEngine._rotation` picks message variants and still resets to
zero on restart, so the composer re-offers the phrasing that already
went out. Verified rather than assumed:

    rotation at construction: 0
    sent    : Morning. Anything you want to get moving today?
    rotation after tick     : 1
    rotation after restart  : 0
    same text again allowed  : False | already said this recently

The durable duplicate window refuses it. So the volatile counter needs
no fix *because* the ledger is durable - the guarantee moved to the layer
that can actually keep it.

### Files

| file | change |
|---|---|
| `proactive/ledger.py` | **new.** Load/save the send history as JSON, on the `core/settings_store.py` pattern. |
| `proactive/policy.py` | `ledger=None` (default keeps today's in-memory behaviour byte for byte), load once at construction, write through on `note_sent` and `reset`, new `history()` accessor. |
| `proactive/engine.py` | `_greeted` dict and `_prune_greetings` deleted; `_greeted_this_part` derives from the history. New `last_user_message` source and `_presence()`. |
| `memory/manager.py` | new `last_said_at(role, session_id)`; spans sessions by default. |
| `launcher/services.py` | `_build_proactive` takes `memory` and supplies `SendLedger()` and `memory.last_said_at`. |
| `.gitignore` | `data/proactive.json` and its `.tmp` - not a secret, but a record of what Aura said to this owner. |
| `tests/test_proactive.py` | +41 tests in four classes. |

### Verification (actually executed, section 34)

Full suite: **2354 passed, 2 skipped, 1 deselected** (from 2313 at the
end of phase 12). `tests/test_proactive.py` alone: 106 passed.

No Kotlin changed this phase, so no Android build was required and none
is claimed. The phase 9 APK remains the current one.

Mutation testing, 31 mutations across `proactive/ledger.py`,
`proactive/policy.py`, `proactive/engine.py`, `memory/manager.py` and
`launcher/services.py`. **31 of 31 caught, no survivors.** The ones that
paid for themselves:

| mutation | what it exposed |
|---|---|
| `load`: keep the file's order instead of sorting | Nothing tested ordering. Aura's own writes are already ordered, so this was only equivalent for files *she* wrote - and this file sits where the owner can edit it. Closed with a hand-written out-of-order fixture. |
| `query`: oldest turn instead of newest (asc/desc) | Survived at first: every test had exactly one user row, and with one row the two are the same answer. A cooldown measured from the owner's first message reads an all-day conversation as an absence since breakfast. |
| `query`: pin it back to the default session | The dead-wiring defect above, now permanently pinned. |
| `wiring`: the server forgets the ledger / the presence source | `core/app.py` records what this failure looks like - code present, tests green, running application without the faculty. Both roots now have a test. |

Two anchors matched twice because the docstrings quote the code they
explain; re-run with unique anchors rather than counted as survivors.

### Known limits, recorded not hidden

- `NotificationOutbox` is still in memory, so a message counted against
  the daily limit can go undelivered if the server dies before the phone
  polls. Not new and not silently accepted: the engine's own docstring
  already says "the outbox is a queue, not a receipt", and the outbox
  expires messages at 30 minutes by design. Favouring section 20 (do not
  spam) over replay is the deliberate call.
- Presence reads only the `messages` table. A machine turn - a device
  agent step - calls `note_chat()` but writes no row, so after a restart
  mid-agent-task the owner reads as absent until they type again.
- One process per ledger. Two servers over one file would each enforce
  the owner's limit against a stale copy of the other's sends.

## Phase 14 - the notification gate stopped forgetting - 2026-08-24

Section 20 is one sentence long in the directive - "do not spam
notifications" - and that sentence is a claim about **rate**. A rate is
a fact about history, so the whole section rests on whether the thing
that decides "have I said too much lately" can still answer after the
process that asked it has been restarted.

`companion/policy.py` could not. It kept its entire history in
`deque(maxlen=32)` in RAM, and every one of the owner's limits was
derived from that one list:

| setting | question it asks of the history |
|---|---|
| `cooldown_seconds` | when did I last speak first |
| `max_per_hour` | how many in the last hour |
| `suppress_after_chat_seconds` | how long since the owner and I talked |
| the duplicate check | did I already say this |

So the sequence that mattered was: Aura speaks her hourly allowance,
the server bounces, and the allowance is back. "No more than N an hour"
was a number in the settings file that nothing enforced across a
restart. This is the same defect phase 13 fixed on the proactive gate,
in the other gate, which is exactly why it was worth looking for.

### One switch where the sibling gate has six

The second problem was not durability, it was section 2. The proactive
gate exposes six tunable paths. `server.companion.*` exposed one:

```
server.companion.enabled
```

An owner who finds Aura chatty had exactly one remedy - silence - and
nothing between that and the shipped default. Section 2 says do not
create arbitrary restrictions that prevent the owner from changing
their own configuration, and "you may turn this feature off" is the
most arbitrary restriction there is when four real numbers already
exist in the code and simply were not reachable.

All six are settable now, and `duplicate_window_seconds` makes seven:

```
server.companion.enabled
server.companion.relevance_threshold
server.companion.cooldown_seconds
server.companion.max_per_hour
server.companion.quiet_hours
server.companion.suppress_after_chat_seconds
server.companion.duplicate_window_seconds
```

### A constant sitting next to four settings

`DUPLICATE_WINDOW = 1800.0` was a module-level constant in the same
file as four settings the owner controls. Nothing marked it as
different in kind from them; it was hard-coded because nobody had
needed to change it yet. The owner could tune when Aura may repeat
herself only by editing source, which is not configuration.

It is now `DEFAULT_DUPLICATE_WINDOW = 1800.0` - the same number, kept
as the default, and read through `from_config` like its four
neighbours. Section 41 is the reason the value did not change: an owner
whose gate behaved a certain way yesterday should not find it behaving
differently today because the knob became reachable.

### The design, and what it borrowed

A repository search came first, per CLAUDE.md, and it found the answer
already written: `SendLedger`, built in phase 13 for the proactive gate.
So phase 14 **reuses it rather than cloning it** - the durable-JSON
pattern from `core/settings_store.py:620-670` did not need a second
implementation.

What it deliberately does *not* reuse is the file:

```python
LEDGER_PATH = DATA_DIR / "companion.json"
LEDGER_CATEGORY = "companion"
```

One shared ledger would have been less code and wrong. Each gate would
then count the other gate's sends against its own budget, and the owner
configured two budgets, not one. Two files, two categories, two
independent limits - which is what the settings actually describe.

Time follows the rule phase 10 set and phase 13 refined: **monotonic
for intervals, wall time only to cross the disk.** Monotonic clocks do
not survive a process, so a persisted row has to carry a wall
timestamp; but every comparison made in memory uses the monotonic
clock, because that is the one that cannot jump. And the seam between
them is where the interesting bug lives, so:

**Ages that come back negative are discarded rather than trusted.** A
laptop resumed from sleep, an NTP correction, a timezone change, or an
owner fixing their clock can all produce a stored timestamp that is
*ahead* of now. Treated naively, that is an enormous positive age (or a
negative one that sorts wrong) and the gate either wedges shut or opens
completely. A test drives `wall.advance(-3600.0)` and asserts the gate
still behaves.

### `_last_notified` was a duplicate, so it was deleted

Section 8 says do not duplicate independent versions of state across
modules, and `_last_notified` was a field holding a fact the history
already contained: the time of the most recent send. Two copies, and
the copy that the code actually read was the one that died with the
process.

It is gone. `allows()` derives it:

```python
last_notified = max((when for when, _ in recent), default=None)
```

One truth, and the one that survives a restart. This is the same move
phase 13 made when it deleted `ProactiveEngine._greeted` - a derived
fact stored as a field is a bug waiting for a restart to expose it.

`reset()` clears the file as well as the deque:

```python
self._recent.clear()
# The file too. `reset` is the owner dropping the limit, and a
# limit that comes back from disk after being dropped is not a
# limit the owner controls (section 2).
self._save()
```

### What was re-examined and left alone

The `NotificationOutbox` was inspected during this phase and
**deliberately not made the source of rate-limit history.** The outbox
is a queue, not a receipt: something in it may never be delivered, and
something delivered may have been dropped from it. Rate limiting has to
be decided by what was actually *sent*, which is what the ledger
records. Recording this because "I looked at it and left it" and "I did
not look at it" are different facts, and section 44 cares about the
difference.

### Mutation testing found a class of weak test, not just a weak test

28 targeted mutations across `companion/policy.py`, `companion/engine.py`,
`core/settings_store.py`, `core/config.py` and `server/settings_service.py`.

**Round one caught 19 of 28.** The survivors clustered in two places.

The first cluster was the age arithmetic across the disk boundary -
precisely the code that only runs on the restart path, which is
precisely the path the old tests never took. Five tests closed it:

- a send 3500 seconds old must age out **across a restart too**, not
  only within one process;
- a row from two days ago must not occupy a live slot in the 32-deep
  deque - dead rows crowding out live ones would silently shrink the
  window the owner configured;
- a clock that moved backwards must not wedge the gate;
- a hand-edited ledger row (`"  Your Build   FAILED. "`) must still
  match as a duplicate, because normalisation has to happen on the way
  *out* of the file, not only on the way in;
- the cooldown must run from the **newest** send, not the oldest.

The second cluster is the one worth writing down. Two mutants survived
in the live-reapply path, and they survived for a reason that had
nothing to do with the mutation being subtle:

> the tests asserted **membership in `LIVE_PATHS`** rather than driving
> a real PATCH and reading the running policy afterwards.

A path can be listed as live-reappliable and the handler that is
supposed to reapply it can be broken, and a membership assertion will
pass every time. This is the identical shape to the phase 11
`memory.recall` defect - wired, green, and dead. So:

**Ninth standing rule: a path in `LIVE_PATHS` and a handler that
reapplies it are two different facts.**

Three tests against a real runtime closed it, in a new class
`TestTuningTheGateReachesTheRunningGate`, using the real `api` fixture
from `tests/test_settings_api.py`:

- all six numbers land on the live policy object, every path reported
  in `applied` and none in `restart_required`;
- a duplicate is refused, `duplicate_window_seconds: 60` is PATCHed,
  and the same duplicate is then allowed - the new number changed an
  actual decision;
- with no gate running (`companion_engine is None`), the reply says
  `restart_required` rather than lying with `applied`.

**Round two: 28 of 28 caught, 0 survivors.**

Four anchors were mis-transcribed while building the harness and were
reported as `ANCHOR MISSED x0` rather than passing silently. The
harness was built to distinguish a missed anchor from a real kill, and
that is the only reason those four were not miscounted as clean kills -
a mutation harness that cannot tell "I failed to apply the mutation"
from "the tests caught it" reports its own bugs as good news.

### The fixture failures were the fixture being right

Two Android tests failed after the server change, and they were correct
to: `android/app/src/test/resources/live/settings.json` pins the
`configurable` list exactly, and the phone locks its controls on it. Six
new settable paths and one new `effective` field is a real contract
change, so the fixture noticing was the fixture working.

Regenerating it was attempted first, using the recipe this project's own
S30 standing rule described - delete every provider key from the
environment before `init_runtime()`, so no real credential can be
written into a checked-in file. **It did not work.** The regenerated
fixture still carried a real key tail, `"configured": true`, and
`"key_source": "environment"`.

The cause, found by grep rather than guessed: **every provider
constructor calls `load_dotenv()` itself** -

```
server/main.py:19
brain/providers/gemini.py:29
brain/providers/groq.py:42
brain/providers/http_chat.py:260
brain/providers/mistral.py:44
```

so deleting the variable is undone the moment the provider is built.
There is a real `.env` at the repository root on this host, which is why
this mattered and why the throwaway script and its output directory were
deleted immediately rather than inspected at leisure (section 30: keys
must never be exposed by diagnostics, and a checked-in test fixture is
about as exposed as a file gets).

The standing rule in `.claude/modernization-checklist.md` was then
rewritten to the recipe that was actually **probed**: patch
`load_dotenv` to a no-op in those four provider modules plus
`server.main`, `core.config`, and `dotenv` itself - verified to yield
`configured: False, key_source: ''`. A rule that has been run is worth
more than a rule that sounded right, and this one had sounded right for
several phases.

The fixture itself was then edited in place, which section 45 prefers
anyway ("do not overwrite large files with generated replacements when
a targeted edit is possible"). Additions-only, proven rather than
claimed - `git diff --stat` across the three live fixtures: **52
insertions, 0 deletions.**

`SettingsContractTest.kt` moved from `assertEquals(50, ...)` to `56` and
names all six new paths individually, with the reasoning in a comment
beside them. No DTO change was needed: `CompanionConfigDto`
(`ControlDto.kt:240`) carries only `enabled`, and `ApiFactory.kt:34`
sets `ignoreUnknownKeys = true`, so the phone tolerates the new
`effective` field.

### Verification

| what | result |
|---|---|
| backend suite | `2387 passed, 2 skipped, 1 deselected` (2379 -> 2387, 0 regressions) |
| `tests/test_companion.py` | `110 passed` (102 -> 110) |
| mutation | round one 19/28, round two after +8 tests **28/28, 0 survivors** |
| Android | `classes=26 tests=359 skipped=0 failures=0 errors=0` |

The Android run followed section 34 properly: `cleanTestDebugUnitTest`
and `testDebugUnitTest` as **separate invocations**, and freshness
proven from the JUnit XML `timestamp` attributes rather than from
BUILD SUCCESSFUL - newest suite stamped `13:08:37` against a wall clock
of `13:08:57`.

### Known issue, recorded rather than fixed

The two speaking gates keep **independent budgets** - proactive 4/day,
companion up to 12/hour - so section 20's "do not spam" is enforced
per-gate and not across both. In the worst case Aura's total unprompted
output is the sum.

Merging them into one budget is the obvious fix and it is the wrong one
to make silently: the owner configured two numbers, and collapsing them
changes what both of those numbers mean. Section 2 says Aura may warn
the owner but must not silently mutate the owner's configuration. So it
is recorded here, and it needs an owner decision (probably a third
setting, an overall ceiling, defaulting to unlimited so nothing changes
for anyone who does not set it).

`data/companion.json` and its `.tmp` sibling are gitignored, for the
same reason as the proactive ledger: not a secret, but a record of what
Aura said to this owner unprompted, written by the running server.

## Phase 15 - the category that could not fire - 2026-08-24

Section 21 is short and its binding sentence is a prohibition: "Aura
must not silently perform arbitrary high-impact actions merely because
it detected an event." Phases 12-14 built the detecting half - an event
bus, a proactive engine with a durable ledger, a notification gate with
another. So this phase had two jobs: make the proactive engine actually
do the thing it claims to do, and establish that nothing on the bus
crosses from noticing into acting.

Both jobs found a defect. The third finding is not a defect and is the
one that needed a decision.

### An entire category was unreachable in production

`Category.APPRECIATION` has a branch in `proactive/decision.py`, a
24-hour entry in `DEFAULT_CATEGORY_COOLDOWN`, two templates in
`proactive/messages.py`, and **three passing tests**. It could not fire
in a real process, and had never fired.

`ProactiveEngine.__init__` has taken a `memories` callable since it was
written. `launcher/services.py::_build_proactive` never passed one. The
probe, before any edit:

    '_build_proactive' passes memories=: False
    engine.memories is: None
    context.relevant_memories: ()

and the decision branch is guarded on `if context.relevant_memories:`.
So the feature was complete, correct, tested, and unreachable.

What made it survive is the shape of its tests. All three built
`ProactiveContext` by hand with `relevant_memories=("...",)` - the
single field production leaves empty. They exercised the behaviour
properly. They just constructed an input the composition root has no
path to producing.

This is a third distinct variety of the section 44 defect:

| phase | shape |
|---|---|
| 11 | wired, green and dead - the test read a **copied default**, not the wired object |
| 14 | the test asserted a **declaration** (`LIVE_PATHS` membership), not the handler |
| 15 | the test supplied **state production cannot reach** |

**Tenth standing rule: when a test hand-builds the input object, ask
which field production fills and which it leaves empty.** A field that
only ever has a value inside a test is not exercised code; it is a
fixture describing a system that does not exist.

### `proactive/memories.py` - 131 lines, and every constant argued for

    EpisodicMemorySource(store, clock=local_now,
                         max_age_days=14, min_age_hours=24, limit=3)

- **Reads the `project` category, not `plan`.** `proactive/tasks.py`
  already owns `plan`, for reminders. Mining one category for two kinds
  of unprompted message means saying the same thing twice in two voices.
- **Older than 24 hours.** Repeating what somebody told you an hour ago
  is not warmth.
- **Newer than 14 days.** Past that it is bringing up something they
  have moved on from.
- **At least 12 characters.** The composer interpolates the subject into
  "Been thinking about {subject}.", and "ok" is not a subject.
- **Newest first, three at most**, and the text handed out is the
  owner's own stored sentence. Nothing in the module writes, summarises
  or paraphrases a memory.

One store, not two. The source is handed `pipeline.episodic` - the same
object `EpisodicTaskSource` gets - so an appreciation cannot be about a
project recorded in a database the reminders never read. There is a test
asserting the identity (`memories.store is pending_tasks.store is
pipeline.episodic`), not just that both are non-None.

**Red-verified**: setting `memories = None` back in `_build_proactive`
gives `2 failed, 2 passed` - exactly the two wiring tests fail, and the
two that supply their own source still pass, which is the discrimination
the check is for.

### An unlisted category had no throttle at all

`proactive/decision.py` says, in prose, of `Category`: "a category that
is not listed here cannot be sent at all." True of the decision engine.
False of `ProactivePolicy`, which took a plain `str` and looked its
cooldown up with `.get()` - so an unlisted string skipped the whole
per-category branch. Probed against the real policy, five distinct
messages one second apart:

| category | sent |
|---|---|
| `"task"` | 1 of 5 |
| `"insight"` | **5 of 5** |

That is the spam route the closed set exists to prevent, and it is
reachable from any caller that passes a string.

The fix refuses rather than defaults:

    KNOWN_CATEGORIES = frozenset(category.value for category in Category)

- **Derived from the enum, not retyped.** A hand-maintained copy of a
  closed set is the thing that drifted in the first place. There is also
  now a test asserting every `Category` member has a cooldown entry.
- **Refuse, not a fallback number.** Inventing a plausible cooldown for
  a category nobody declared would hide the caller bug and ship the
  spam. Refusing makes the sentence `decision.py` already claims true.
  Section 44: do not invent behaviour.
- **This does not restrict the owner** (section 2). An unknown category
  cannot come from owner configuration - `category_cooldown_seconds`
  keys are merged over the defaults, and the owner may still set any
  value for any real category.
- **Guard placed after `enabled` and the empty-message check, before
  quiet hours**, so the reason an operator reads names the actual
  problem rather than the time of day. The string carries the offending
  category and the set it is not in.

**Red-verified**: removing the guard gives `2 failed, 2 passed`.

### Seven tests were leaning on the defect

`TestTheProactiveLimitsSurviveARestart` used the category `"check_in"`,
which is not in `Category` and never has been. That was not carelessness
with no consequence: because an unlisted category had no per-category
cooldown, using one left whichever rule each test was about as the only
rule standing. The tests were quietly depending on the bug, which is a
large part of why it lasted.

Closing the set broke all seven. The repair is the general one - name a
real category (`Category.WELLBEING.value`) and stand the other rules
down **explicitly**, `category_cooldown_seconds={CATEGORY: 0}`, so the
rule under test is still the only one that can answer. Several of them
discard the reason string with `allowed, _reason`, and a test that
asserts "not allowed" while a different rule than the named one does the
refusing is a test that passes for the wrong reason.

### Nothing on the bus acts on an event - now a test, not a coincidence

Enumerated at a fully built composition root, with avatar, proactive and
voice all on and the notification outbox attached:

| owner module | handlers | what it does |
|---|---|---|
| `avatar.animation` | 6 | changes which animation is playing |
| `events.log` | 1 | writes a DEBUG line |
| `avatar.state` | 1 | moves the avatar's state machine |
| `avatar.controller` | 1 | blinks |
| `voice.tts.engine` | 2 | speaks a reply that was already produced |
| `server.notifications` | 1 | queues for a device to come and drain |

All render, speak, log or queue. None acts. Section 21 is satisfied -
**by accident of what happens to subscribe**, not by construction:
`subscribe` takes any callable, and `publish` calls every match and
swallows handler exceptions with `logger.exception`. The day something
that acts is attached, an event becomes an action, and nothing in that
diff says so.

The honest question for the phase was whether a property that is
currently true by accident should be a test that fails when someone
attaches an acting handler. It should, so
`TestNothingOnTheBusActsOnAnEvent` now walks **both** `_wildcard` (via
`subscribe_all`) and `_handlers`, resolves each handler's owning module,
and fails on any module outside a declared presentation set. Its failure
message says this needs a section 21 decision, not a list addition.

Two details it cost:

- `handler_count()` returned 9 while the first walk printed 8.
  `subscribe_all` stores into a separate `self._wildcard` list that the
  count includes and a walk of `_handlers` alone misses. A boundary test
  that reads one structure is a boundary test with a hole in it.
- A second test asserts the bus is **not vacuously clean** - that
  `events.log`, `avatar.animation` and `server.notifications` are
  actually present. An empty bus would pass the boundary test and is the
  phase 12 defect, not safety.

**Verified to fire**: a synthesised `tools.autopilot` module with an
acting handler was attached, and both tests caught it.

### A docstring that was wrong about which direction a value pushes

`proactive/context.py::seconds_since_user` claimed "the rules that
consume this all use it to *hold back*, never to fire." The
active-conversation guard does. The greeting, task and appreciation
rules all fire on a **large** value, and infinity is the largest.
Probed rather than reasoned about:

    last_user_message_at=None -> seconds_since_user: inf
      -> send: True, category: greeting
      -> reason: user away, no record of a previous message
                 + no afternoon greeting yet

So a brand-new owner with no message on record clears
`GREETING_AWAY_SECONDS` on the first tick, and only `greeted_this_part`
and the engine's presence source stand between an empty history and an
unprompted "welcome back". The docstring now says that, naming the
firing path. Recorded as a known issue rather than changed: the
behaviour may well be what an owner wants, and guessing is section 2's
business, not mine.

### Mutation testing found the branch with no test

15 targeted mutations across `proactive/memories.py`,
`proactive/policy.py` and `launcher/services.py`.

**Round one: 14 of 15 killed.** One survivor:

    if when is None:
        continue        # -> pass

That branch had no test, and its consequence is out of proportion to its
size. `parse_timestamp` is deliberately tolerant because it reads
columns written by several versions of Aura. Without the `continue`, the
next comparison is `oldest <= None`, which raises `TypeError`, which
`_gather_memories()` swallows as "nothing to say". The cost of one bad
row is therefore not one lost memory - it is **every** memory, for as
long as that row sits inside the fortnight the query covers.

The test that closes it puts a bad row in front of a good one on
purpose, and the bad row sorts first without any arranging, because
`by_category` sorts a string column and `"sometime last tuesday"` sorts
above any ISO date. **Round two: 15/15, 0 survivors.**

### Files

| file | change |
|---|---|
| `proactive/memories.py` | **new**, 131 lines - `EpisodicMemorySource` |
| `proactive/policy.py` | `KNOWN_CATEGORIES` + refusal in `allows()`; docstring `Six` -> `Seven independent rules` |
| `launcher/services.py` | `_build_proactive` constructs and passes `memories=` |
| `proactive/context.py` | `seconds_since_user` docstring corrected to name the firing path |
| `tests/test_proactive.py` | +20 tests in 4 new classes; 7 `check_in` tests repaired |

### Verification (actually executed, section 34)

| what | result |
|---|---|
| `tests/test_proactive.py` | `126 passed in 1.60s` (106 -> 126) |
| backend suite | `2407 passed, 2 skipped, 1 deselected in 22.46s` (2387 -> 2407, 0 regressions) |
| red check, appreciation wiring | `2 failed, 2 passed` with `memories = None` |
| red check, unknown category | `2 failed, 2 passed` with the guard removed |
| boundary test fires | caught a synthesised `tools.autopilot` acting handler |
| mutation | round one 14/15, round two after +1 test **15/15, 0 survivors** |

No Android change in this phase, so no Gradle run is claimed.

### Known issues, recorded rather than fixed

- **Section 21's open question is now answerable but unanswered**: what
  "high-impact" means for the tool system, given that today no event can
  reach a tool at all. Phase 16 (Universal Tools) is where that boundary
  acquires teeth, and the test above is what will notice if it is
  crossed by accident.
- **The two speaking gates still keep independent budgets** - proactive
  4/day, companion up to 12/hour - so section 20's "do not spam" holds
  per-gate, not across both. Unchanged from phase 14, and still needs an
  owner decision rather than a silent merge (section 2).
- **A brand-new owner with no message history gets `inf` for
  `seconds_since_user`**, which clears the greeting's away threshold on
  the first tick. Documented in the docstring; not changed, because
  which behaviour the owner wants is theirs to say.

## Phase 17 - two authorities on "done", and the field that was never filled - 2026-08-25

Section 42 puts accessibility integration here. Two defects were owed, and
both were about the device and the server disagreeing about what happened.

### Defect 2: `AppInfo.activity` was declared, serialized, and never assigned

`focus.screen` arrived permanently empty on the server. Not "sometimes" -
never, because nothing on the device ever wrote the field. Both
`brain/task_graph.py` and `brain/recovery.py` say so in their own comments
and *restrict section 11 verification because of it*: "a focused search
field or rendered results would have to be read off `focus.screen`, which
arrives permanently empty because the device never fills in the activity
name. A postcondition asserted on absent evidence is worse than one not
asserted at all."

So a whole class of postconditions was unavailable, and the reason was one
unassigned field.

Filled it from `TYPE_WINDOW_STATE_CHANGED`, which this service's config
(`res/xml/aura_accessibility_service.xml`) already subscribes to. The
package and class arrive together in one event and are stored as one
`AtomicReference<Window>` rather than two fields: a torn read would pair
one app's package with another app's activity, which is the single answer
worse than no answer.

`activityFor` returns null when the remembered window's package is not the
foreground package. That was the deliberate choice, and it is cheap
because of what `CognitiveState.observe()` already promises - `None` means
"no news" and `""` means "nothing there", so a transient null leaves the
last known screen alone. The cost of the race is one tick of a slightly
stale screen; the cost of guessing would be a sentence about the device
that was never true at any instant.

Established before writing anything, from the code rather than by
assumption:

- no verification logic branches on `screen` being empty - the launch-only
  restriction is enforced by *step kind*, so filling the field is purely
  additive and cannot change an existing verdict;
- no test locks `activity` empty, while several backend tests hand-build
  it - `tests/test_machine_turns.py` already asserts
  `state.focus.screen == ".Main"` from a hand-built payload, which is the
  standing rule's textbook case: a passing test built on a field
  production cannot fill is the defect, not the coverage.

### Defect 1: two authorities decided when a task ends, and neither deferred

The server owns "is the goal met": `plan_for` decomposes the request,
`task_graph.build` reads it against what happened, `is_finished`/`is_stuck`
separate a completed task from an abandoned one, and the model ends a task
by emitting `complete`.

The device also decided, in three keyword heuristics, and it decided
*first* - `shouldAutoComplete`, `isSearchTaskComplete` and
`isSelectionTaskComplete` run on a verified action and `break` the loop, so
the server is never asked. `brain/planner.py` and `tests/test_planner.py`
both recorded that reconciling them was phase 17's job.

Phase 17 named the boundary instead of merging the two:

- **The server owns "is the goal met."** Unchanged.
- **The device owns "may I stop without asking."** That is a latency
  optimisation over an obviously single-step request, and it is allowed to
  be wrong in exactly one direction - the one that costs a round trip,
  never the one that ends a task early.

The device was wrong the other way, twice, and both were proved against
the real planner before any code changed.

**`shouldAutoComplete` tested for a conjunction and nothing else.**
`plan_for` decomposes on a conjunction *or* on a launch verb and a search
verb appearing together. `planner.CONJUNCTIONS` carried a comment claiming
the device's list was "deliberately the same set" - it was not, and the
gap was not the differences in the list but the missing second test. Six
phrasings the planner read as multiple steps returned true on the launch,
so the loop reported "App launched successfully!" for a request that asked
for a search:

    mở YouTube tìm nhạc              planner: 5 steps, device: finished
    open YouTube search Minecraft    planner: 5 steps, device: finished
    search Minecraft on YouTube      planner: 4 steps, device: finished
    tìm Minecraft trên YouTube       planner: 4 steps, device: finished
    open Chrome search for weather   planner: 5 steps, device: finished
    mở Chrome tìm kiếm thời tiết     planner: 5 steps, device: finished

**`isSearchTaskComplete` declined only for a *selection*.** A trailing
clause of any other kind was invisible, so it stopped at the submit while
the planner still had a step to go. Six more, in both languages, three of
them ending in a verb ("tap", "click", "bấm") the selection list does not
carry at all - which is why the fix is positional rather than another word
added to a list:

    open YouTube and search Minecraft then open settings
    open Chrome and search weather, then open YouTube
    mở YouTube và tìm nhạc rồi mở cài đặt
    open YouTube and search Minecraft and tap the result
    open YouTube and search Minecraft and click it
    mở YouTube và tìm nhạc rồi bấm vào đó

`hasClauseAfterSearch` asks whether a separator sits to the *right* of the
last search verb. Positional, because the ordinary two-clause search
request has a conjunction too, before the search, and had to keep ending
at the submit - a containment test would have taken that away and cost
every search an extra round trip. The *last* verb, because with the first
one "search for X then search for Y" would never end: the separator sits
between the two verbs.

### One vocabulary, not four

The search verbs existed on the device already, as a local `val prefixes`
inside `sanitizeSearchQuery`, and the early exit did not look at them.
They are now `AuraActionExecutor.SEARCH_VERBS`, read by the sanitiser, by
`shouldAutoComplete` and by `hasClauseAfterSearch`, and pinned to
`brain.planner.SEARCH_VERBS` by a guard that reads the Kotlin source. The
selection list was declared twice, identically - the shape every drift in
this file has taken - and is now one `selectionCues`.

The device's conjunctions stay a *superset* of the planner's rather than
equal to them. Every difference (" tiếp ", " to ", bare "," and ";") makes
the device call a request multi-step where the planner calls it single,
which is the safe direction. `CONJUNCTIONS` claimed equality; the
containment that actually has to hold is now asserted instead of described.

### What was deliberately not done

The device still does not tell the server that a task ended. Nothing calls
`CognitiveState.clear_task()` on completion - it fires only on a session
or conversation change - so `state.plan` and `state.task_node` describe a
finished task until the next request overwrites them, and
`TaskFinishedEvent` fires only if the last tick the device happened to
send already saw every node settled. The only channel from device to
server is `repository.send(AGENT_TICK, ...)`, which is a full model turn;
spending one to say "I stopped" would also produce a reply the device
would then have to ignore. Recorded rather than guessed at - it needs a
non-LLM endpoint, which is a wire change and not this phase's.

### Files

    android/.../accessibility/AuraAccessibilityService.kt
        lastWindow, onAccessibilityEvent, activity = activityFor(...),
        Window/windowFrom/activityFor, multiStepKeywords and selectionCues
        promoted to shared constants, hasClauseAfterSearch,
        shouldAutoComplete and isSearchTaskComplete now defer
    android/.../accessibility/AuraActionExecutor.kt
        SEARCH_VERBS promoted from a local val to a companion constant
    android/.../test/.../ForegroundActivityTest.kt        new, 11 tests
    android/.../test/.../AccessibilityAgentTest.kt        +9 tests
    tests/test_agent_protocol.py                          +5 tests, guard repointed
    brain/planner.py                                      two false comments corrected
    tests/test_planner.py                                 stale "phase 17 will" note updated

### Verification (actually executed, section 34)

Android was run with `--no-build-cache` after `cleanTestDebugUnitTest` came
back FROM-CACHE on identical inputs and left the JUnit XML timestamp
unchanged - section 34 forbids trusting that, and the stale timestamp is
how it was caught.

    baseline                    classes=26 tests=359  (stale ts 13:08:37)
    defect 2 + 9 tests          classes=27 tests=368  fresh 2026-08-24T17:10:59
    wire contract + 2           classes=27 tests=370  fresh 2026-08-25T00:25:22
    defect 1 + 4 tests          classes=27 tests=374  fresh 2026-08-25T00:38:18
    trailing clause + 4         classes=27 tests=378  fresh 2026-08-25T00:44:52
    failures=0 errors=0 skipped=0 throughout

    backend before               2423 passed, 2 skipped, 1 deselected
    backend after                2428 passed, 2 skipped, 1 deselected in 40.16s

Nine mutations, nine caught, zero survivors. Each was applied to the real
source, run, and the file restored and confirmed byte-identical with
`diff -q`:

    M1  drop the package-match guard in activityFor    1 failed - and only
                                                      the cross-app test
    M2  drop the blank guard in windowFrom             3 failed
    M3  drop .trim() in windowFrom                     3 failed
    M4  rename the wire key to "screen"               2 failed - and only
                                                      the two contract
                                                      tests, which is the
                                                      point: the nine
                                                      decision tests were
                                                      genuinely blind to it
    M5  signals reduced to conjunctions-only,          1 failed, naming
        i.e. the pre-fix device                       'open YouTube search
                                                      for Minecraft'
    M6  remove the search-verb deferral               3 Kotlin + 1 Python
    M7  drop "tìm " from the shared vocabulary        2 Python - the pin
                                                      and the invariant
    M8  remove the trailing-clause deferral           1 Kotlin + 1 Python
    M9  measure from the first search verb, not last  1 Kotlin - exactly
                                                      the test written for it

The two generated corpora were checked for vacuity rather than assumed:
80 of 80 early-exit phrasings and 192 of 192 trailing-clause phrasings
reach their assertion, none skipped. Both are built from
`LAUNCH_VERBS`/`SEARCH_VERBS`/`CONJUNCTIONS` rather than hand-picked, so
they do not only contain the cases I already believed.

The Python invariants assert over vocabularies, which would be vacuous if
the device declared a list and never read it - which is exactly the state
it was in. Two structural guards read the bodies of `shouldAutoComplete`
and `isSearchTaskComplete` and fail if the references disappear; M6 and M8
are what proves they work.

### Known issues

- No device-to-server "task ended" signal (above). `clear_task()` still has
  no production caller.
- `isSelectionTaskComplete` does not carry "click"/"tap"/"bấm", which
  `planner.SELECTION_CUES` does. The gap only makes it return false, so the
  loop asks the server - the safe direction - and it is left alone rather
  than widened without a case that needs it.
- The planner reads a trailing "open settings" as `select_result`, because
  "open" is one of its selection cues. That is a planner imprecision, not a
  device one; the device now declines on those requests for the correct
  reason (a clause follows the search) regardless.
- Not run on hardware. Section 35 device tests remain owed.

## Phase 16 - the tool that said "remembered" and stored nothing - 2026-08-24

Section 22 lists seven things every tool should expose: name,
description, parameters, capabilities, permissions, `execute()`,
`verify()`. Six of the seven were already here under names the section
does not use. `name`, `risk` and `execute` are `ToolProtocol`;
`description` and `parameters` are what `describe_tool` reads to build
the catalogue the model sees; the section's *permissions* is the
five-gate executor - enabled, registered, allow-listed, risk-approved,
plain arguments - where gate 3 is `tools.allowed` and gate 4 is `risk`
against `auto_approve` with refusal as the default when no confirm
callback is installed.

So the phase's real work was the two that were missing, and they turned
out to have opposite answers. One should not be added. The other was
already covering a live defect.

### `capabilities`: deliberately not added

Nothing would read it. `risk` already grades how much damage a tool can
do and drives approval; `tools.allowed` already decides what may run at
all; `parameters` already declares the interface. A `capabilities` list
would either restate one of those three or sit in the tree as metadata
with no consumer.

That is the *wired, green and dead* defect this project has now hit
three separate ways - phase 11 read a copied default, phase 14 asserted
a declaration, phase 15 supplied state production cannot produce - and
the version here would be the worst of the three to inherit, because a
field named `capabilities` reads as though somebody measured something.
The next person to touch the executor would reasonably assume a gate
consults it.

Recorded as a decision with its reasoning rather than as an omission.
It goes in when a phase brings a real reader for it. This is the same
discipline `recovery.py::RETRY_LIMITS = {}` documents in place: an empty
structure with a comment saying why it is empty beats a populated one
nothing consults.

### `verify()`: added, because a live defect was already sitting in the gap

Section 11 is explicit that verification "must not rely only on: 'the
command executed without throwing'", and `ToolExecutor._run` produces
exactly that sentence and nothing more. A tool that returns `ok(...)`
is believed.

Three properties, each of which was a decision:

**It is getattr-optional and never joins the Protocol.**
`ToolProtocol` is three members, and
`test_the_protocol_stayed_narrow` fails the moment a fourth appears -
that guard exists so a framework capability cannot quietly become a
requirement every tool author must satisfy. `verify()` joins `timeout`,
`describe` and `required_parameters` as optional-by-absence: the
executor asks with `getattr`, and a tool that does not answer is not
penalised.

**The executor consults it, not the tool.** A tool that verified itself
inside `execute()` could still return `ok` while lying, and every tool
would have to remember to do it. Asking from `_verified()`, between
`_run` and `_finish`, means the downgrade happens on one code path -
and because `_finish` is what emits `ToolCompletedEvent`, a downgraded
result reaches the event bus as `ok=False` with no extra wiring.

**Three limits, each pinned by a mutation.** A failure is never
re-verified: there is nothing to second-guess, and the side effect was
never meant to hold. A `verify()` that raises **fails closed** - the
alternative yields precisely "it ran without throwing" for the
verification itself, the one sentence section 11 rules out. `None`
asserts nothing rather than failing, so a tool can decline to verify a
particular call without that reading as a verification failure.

### It is not a second verifier

Worth stating plainly, because the repository already has something
called verification and adding another would be the duplicate-system
defect.

`brain/task_graph.py`, `brain/planner.py::is_done` and
`brain/recovery.py` verify **device** actions: things the phone did,
reported back over the wire as `ActionRecord`s, reconciled against
`CognitiveState`. A local Python tool never creates an `ActionRecord`
and never enters that graph. The two systems cannot see each other's
work, and the local equivalent of recovery already exists and is
bounded: a failed `ToolResult` becomes text through
`conversation._render_result` - *"This did not happen. Tell the user it
failed, and why."* - and `TOOL_CALL_LIMIT = 3` stops the loop.

`recovery.py`'s distinction between `FAILED` ("could not execute") and
`UNVERIFIED` ("executed but not verified") is exactly the one this
phase needed, so it was borrowed **in wording** and not by import - a
new brain -> tools dependency to reuse two strings would be the wrong
trade.

While looking for a taxonomy to reuse, one open question from earlier
phases got an answer: section 29's error taxonomy exists **only** as
the provider-scoped exception hierarchy in `brain/providers/errors.py`
(`ProviderUnavailableError`, `ProviderAuthError`,
`ProviderParameterError` with `.parameter`, `ProviderRateLimitError`
with `retry_after`/`is_account_limit`). There is no global `ErrorKind`
enum and no `VERIFICATION_ERROR` constant anywhere in the tree - a
repo-wide grep returns nothing. So a verification failure reuses
`ToolResult(ok=False, error=...)`, which is what every other tool
failure already is.

### The defect, proved before any code was written

`RememberTool.execute()` ended like this:

```python
self.pipeline.remember_user_stated(key, value, category=category)
return ok(f"remembered {key} = {value}", tool=self.name)
```

The return value was discarded. That return value is the written
`Belief` - or `None`, when nothing was written.

`memory/user_model.py::_write` opens with
`slug = self._normalise(key)` and `if not slug: return None`, and
`memory/profile.py::normalise_key` is
`re.sub(r"[^a-z0-9]+", "_", slug).strip("_")`. So any key with no ASCII
alphanumerics slugs to the empty string, writes no row, raises nothing,
and returns `None` into a variable nobody looked at.

Probed on the live tree before touching a line:

- `key='???'` -> `ok=True`, `output='remembered ??? = Thien'`, **rows=0**
- `key='---'` -> `ok=True`, **rows=0**

Then the case that makes it more than a curiosity:

- `key='名前'` -> `ok=True`, **rows=0**
- `key='tên'` -> stored fine
- `key='identity.tên'` -> stored fine

A CJK key is silently lost and reported as saved. This is the machine
of an owner whose own filenames are Vietnamese, and the string
`remembered 名前 = Thien` goes straight into the model's context as the
result of the call - so the model then writes a reply telling the user
a fact about them was kept, when the database has nothing. Section 11
exists for exactly this: the write did not throw, and the write did not
happen.

### Two layers, because they catch different things

`execute()` now checks the return and fails on `None`, with a reason
that names the key and suggests one that would survive:

```python
stored = self.pipeline.remember_user_stated(key, value, category=category)
if stored is None:
    return fail(
        f"could not remember {key!r}: it has no letters or digits to "
        f"key on, so nothing was stored. Try a key like identity.name.",
        tool=self.name,
    )
return ok(f"remembered {key} = {value}", tool=self.name)
```

That reason is written for its actual reader - the model, which gets it
back through `_render_result` and can choose a storable key on the next
attempt instead of repeating the same lost write.

`verify()` reads the fact back through `value_of`, the same door the
prompt recalls memories with:

```python
def verify(self, key: str = "", value: str = "", category: str = IDENTITY):
    key = (key or "").strip()
    if not key:
        return None
    stored = self.pipeline.user_model.value_of(key)
    if not stored:
        return fail(f"{key} was not stored - it could not be read back "
                    f"after remembering.", tool=self.name)
    return ok(f"{key} reads back as {stored!r}", tool=self.name)
```

The two layers catch different failures. `execute`'s guard catches the
empty-slug write at the moment it happens, with the most actionable
message. `verify`'s read-back catches anything that makes a fact
unreadable *afterwards* - a write that landed and was immediately
invalidated, a normalisation that stored under a key the recall path
would not look up - by proving the fact is reachable the way it will
actually be reached, not merely that a row exists somewhere.

### One tool deliberately gets no `verify()`

`open_application` has none, and its absence is documented in the
Protocol docstring so the next author does not read it as an oversight.
It already resolves the executable before spawning and watches the
process through a grace period - the verification is *inside* execute,
where the evidence still exists. Afterwards the postcondition cannot be
honestly re-asked: a launched app may have opened and closed, or opened
a window this process cannot see. Absence of `verify()` there means
"execute already told the whole truth", never "unverified". That
sentence is now in `tools/base.py` next to the capability list.

### Files

- `tools/executor.py` - `_verified()` added between `_run` and
  `_finish`; `execute()` rewired to `self._finish(name,
  self._verified(tool, arguments, result))`.
- `tools/base.py` - `verify()` added to the optional-capability list in
  the `ToolProtocol` docstring, with the paragraph on read-back and the
  `open_application` exception. Protocol itself unchanged - still three
  members.
- `tools/builtins/memory.py` - `execute()` gains the `if stored is
  None` guard; new `verify()` reads back through `value_of`.
- `tests/test_tool_framework.py` - new section, 8 tests, with doubles
  `VerifyingTool`, `FailingThenVerifyingTool`, `RaisingVerifyTool`.
- `tests/test_tools.py` - new section, 8 tests, both live triggers
  (`???`, `名前`) parametrized.

### Verification (actually executed, section 34)

- Baseline before any edit: the three tool suites `115 passed`.
- Red-first: framework `4 failed, 4 passed` - and the four passes are
  the point, a failing `verify()` passed because nothing consulted it
  yet; remember `7 failed, 1 passed` on `AttributeError: no attribute
  'verify'`.
- After: the three suites `232 passed`.
- End-to-end probe at the executor boundary: before, `名前` -> `ok=True`
  / rows=0; after, `名前` and `???` -> `ok=False` with the key named,
  `identity.name` and `identity.tên` -> `ok=True`, rows=2.
- Mutation testing: **5 of 5 caught, 0 survivors.** Executor ignores
  the verdict (4 failures); `execute` discards the `None` again;
  `verify` passes by default; executor fails **open** on a raising
  verify; executor re-verifies failures. Every guard is load-bearing.
- Full backend: **2423 passed, 2 skipped, 1 deselected in 22.42s**
  (2407 -> 2423, +16, 0 regressions).
- Android: not run. No Kotlin changed. The phase 9 APK
  (19,427,232 bytes) is current; section 35 live device scenarios still
  owed.

### Known issues, recorded rather than fixed

- **`capabilities` is not implemented**, by decision above. When a
  phase needs a reader for per-tool capability metadata, it is added
  then, with the reader.
- **Only `remember` has a `verify()` today.** It is the tool whose
  silent-failure mode was live and provable. Others get one when a real
  postcondition gap is shown, not pre-emptively - a `verify()` that
  cannot fail is the wired-green-and-dead defect wearing a safety label.
- **The `tools/registry.py::describe()` docstring was stale and is now
  fixed** - it claimed the catalogue "will be dropped into the TOOLS
  prompt section once tool calling is enabled", but tool calling is live
  and the TOOLS section comes from the policy-filtered
  `ToolExecutor.catalogue()`, not from the unfiltered
  `ToolRegistry.describe()`. The docstring now says which is which and
  why the unfiltered one is wrong for a prompt. Docstring-only; the 58
  framework tests stay green.

## Phase 18.1 - PC Agent: observation and window management (uncommitted)

Section 42 order, phase 18 (section 24 PC/Windows agent). Split per section
43 (`Phase N -> N.1 -> N.2`) because the section 24 list spans nine distinct
capabilities: 18.1 is the observation half plus window management, 18.2 is
controlled command execution, 18.3 is the remaining actuation.

Status: **IMPLEMENTED** for what is listed below; the section 24 items that
are *not* here are named explicitly at the end rather than left ambiguous.

### What was built

Four tools, all `ToolProtocol` implementations with no new abstraction:

| Tool | Risk | `verify()` | Module |
|---|---|---|---|
| `system_information` | SENSITIVE | none, on purpose | `tools/builtins/system.py` |
| `list_processes` | SENSITIVE | none, on purpose | `tools/builtins/system.py` |
| `list_windows` | SENSITIVE | none, on purpose | `tools/builtins/desktop.py` |
| `focus_window` | DANGEROUS | reads the foreground window back | `tools/builtins/desktop.py` |

Registered through a new `tools/factory.py::_pc_tools()`, called from
`_builtin_tools`. Documented in `config.yaml` in a ~45-line comment block.
**No new config keys were added and nothing was enabled**: `tools.allowed`
is still `['current_time', 'remember']` and `auto_approve` is still
`['safe']`. Section 2 cuts both ways here - the owner must be able to enable
these freely, and must not find them already enabled without having said so
- and the shipped-config guard test reads `config.yaml` directly rather than
trusting a loader, so it survives a defaulting bug in the loader itself.

Each module follows the Protocol + Mock + real triple already used by
`vision/capture.py`: `ProcessSource`/`MockProcessSource`/`PsutilProcessSource`
+ `TasklistProcessSource`, and `WindowSource`/`MockWindowSource`/
`WindowsWindowSource`. psutil is not installed on this machine, so
`TasklistProcessSource` is the one that actually runs; `tasklist` reads ~231
processes in 0.25s.

### The section 11 work, which is the point of the phase

`SetForegroundWindow` returns zero and changes nothing whenever Windows'
foreground lock applies. Measured: it accepts-and-ignores. So "the command
executed without throwing" - the exact sentence section 11 forbids resting
on - is not merely a weak signal here, it is the *normal* signal for a call
that did nothing.

The postcondition is therefore asked separately. `execute` performs the
action and its message says **"asked for"**, not "brought". `verify` reads
the foreground window back and fails the call when the window that was asked
for is not the one in front, polling to a bounded 1.5s deadline because focus
changes are asynchronous.

A live probe confirmed the whole loop rather than asserting it: `execute`
returned ok=True and `verify` failed 1.50s later on a window the lock refused
to surface. And the executor-level test drives it end to end through
`ToolExecutor`, because a postcondition the tool checks and the framework
ignores protects nobody.

Two smaller decisions in the same area:

- **A reading tool needs no `verify()`.** A read's postcondition *is* its
  return value; re-reading would assert nothing the caller cannot see. The
  three readers have none, deliberately.
- **`_verified` calls `check(**arguments)`**, so `verify`'s signature must
  accept exactly what `execute` accepts or it raises TypeError and fails
  closed. There is a structural guard test on that, added after `pid` was
  threaded through.

### Two defects the live machine exposed that no mock would have

This desktop had **two windows titled exactly `Settings`** (pids 100/200 in
the test fixture, which copies the real case). Against that:

1. The ambiguity message said "name one of them more precisely" - impossible
   advice when the titles are byte-identical.
2. The verification failure message read as nonsense: `'Settings' did not
   come to the front - 'Settings' is still the active window`.

Both fixed with an optional `pid` parameter - already visible in
`list_windows` output, so the model has it - threaded through `_match`,
`execute` and `verify`, with an `_as_pid` coercion helper. The failure
message now prints the pid in both halves or neither.

### A false claim of my own, deleted (section 44)

The `WindowsWindowSource` docstring asserted that an undeclared
`GetForegroundWindow` returns a truncated handle, since ctypes defaults
`restype` to `c_int` and an HWND is pointer-sized. **Measured on this
machine, it does not.** 203 windows enumerated, largest handle `0x1202A0`,
nothing anywhere near `2**31`, and the undeclared call returned the identical
value - Windows keeps USER handles inside 32 bits deliberately.

Section 44 forbids inventing API behaviour, so the docstring now records the
measured truth and gives the two honest reasons the declarations stay
(documenting the real signature, and the habit being load-bearing elsewhere).
The real version of the bug lives one module over: `_uptime_hours` calls
`GetTickCount64`, whose result genuinely exceeds `c_int` - undeclared it
reports negative uptime after **596.5 hours**, and this machine measured
**300.7**, roughly twelve days of headroom. Both claims are now structural
guard tests, since neither is behaviourally observable today.

### Smaller findings, recorded because each is a decision

- **`default_process_source()` and `default_window_source()` return `None`,
  not a mock.** Changed so the factory's existing absent-dependency rule
  applies: a tool whose dependency is absent is not registered, so it is
  *missing* rather than *present and broken*. `ListProcessesTool` still
  accepts an injected source and falls back to a mock when constructed
  directly, which is what the tests use.
- **`list_windows` and `focus_window` register together and share one source
  object.** Two would be two enumerations of the same desktop, and
  `focus_window` matching against a listing the owner never saw is how the
  wrong window gets brought forward.
- **An empty window listing is a success; an empty process listing is a
  failure.** Deliberate asymmetry, documented in both places: every operating
  system has processes, so an empty process listing can only mean the reading
  broke, whereas a session genuinely can have no visible titled window.
- **Window handles are never printed.** They are matching keys, not owner
  information, and a stale handle in a transcript is a way to act on the
  wrong window later.
- **`system_information` omits username, hostname and home directory**, and a
  test asserts their absence from the real reading - a section 30 habit
  applied one level out from credentials.
- **A missing fact is omitted, not zeroed**, so the owner cannot mistake
  "unreadable" for "zero".
- **System Idle Process really has pid 0** on Windows, which broke a
  `pid > 0` assertion in my own test. Fixed to `>= 0` with a parser test for
  pid 0, and `_as_pid`'s docstring now justifies why 0-means-absent stays safe
  there (pid 0 owns no windows).
- The `EnumWindows` callback **skips one unreadable window rather than
  abandoning the enumeration**.

### Not exercised, recorded rather than glossed

The real cross-window focus switch under Windows' foreground lock was **not
exercised on live hardware.** The owner's foreground window was an active
fullscreen game and a real switch would have yanked focus out of it. The
ctypes path was exercised on the already-front window (zero visible effect),
and the switch-fails case used `honour_focus=False` on the mock, which
reproduces the lock exactly.

### Section 24 items NOT in 18.1

Named rather than left ambiguous: keyboard input, mouse interaction,
filesystem write operations, terminal commands, screenshots. Terminal commands
are 18.2; the rest are 18.3. `config.yaml`'s comment block says the same thing
to the owner: *"The list is short because it is honest about this machine
rather than aspirational."*

### Verification (actually executed, section 34)

- `tests/test_pc_tools.py`: **85 passed** in 6.56s.
- Full backend: **`2513 passed, 2 skipped, 1 deselected in 32.44s`**
  (baseline before this phase was 2428; delta is exactly the 85 new tests).
- Mutation testing, two batteries: **`12 of 12 caught, 0 survivors`** and
  **`21 of 21 caught, 0 survivors`**, spanning `system.py`, `desktop.py`,
  `factory.py` and `config.yaml` - including "a PC tool is silently enabled in
  the shipped config", which the section 2 guard catches.
- Three survivors were found and fixed rather than explained away:
  `default_process_source` falling back to a mock (unreachable on this machine
  because `tasklist` always exists - fixed with two tests that monkeypatch
  both `is_available` methods), and the two ctypes restype deletions above.
- Harness removed; `_mut/config.yaml.orig` and `_mut/factory.py.orig`
  confirmed byte-identical with `diff -q`, and the two module diffs showed only
  the intentional docstring corrections.

## Phase 18.2 - the brace check that broke `find -exec` (uncommitted)

Phase 18.1 gave the PC agent eyes and hands for windows. 18.2 gives it the
one capability section 24 puts a fence around in the same sentence it grants
it: *"Do not give arbitrary LLM text direct unrestricted shell execution
without a controlled tool boundary."*

`tools/builtins/commands.py`, ~1090 lines, exactly one tool: `run_command`,
`ToolRisk.DANGEROUS`. The shape follows the sentence. The model never
composes a command line. The **owner** declares named argv lists in a new
`tools.commands` config section:

```yaml
tools:
  commands:
    unit_tests: [".venv/Scripts/python.exe", "-m", "pytest", "-q", "{path}"]
    git_log:
      argv: ["git", "log", "--oneline", "-n", "{count}"]
      description: "Recent commits in the Aura repo."
      cwd: "D:/AURA"
      timeout: 20
```

The model supplies the key and values for whatever slots the owner wrote -
one slot per argv element, `shell=False`, no interpreter in between. Two
declaration shapes are accepted because the short one is what an owner
actually types and the long one is what they need once they want a
description, a working directory or a different bound.

### The property the phase exists for, and how it is actually shown

Fifteen shell-meaningful payloads (`a && b`, `; rm -rf /`, `$(echo hi)`,
`` `echo hi` ``, `%PATH%`, `x | y`, `> out.txt`, `quote " inside`, a literal
newline, `--looks-like-a-flag`, `..` traversal) are each filled into a slot
and asserted to arrive at a real program as **exactly one** `sys.argv` entry
with the punctuation intact. A value also cannot lengthen the argument list:
`["--flag", "{value}", "--after"]` filled with `x --injected y` arrives as
three elements, not five, and the same slot used twice fills twice without
merging.

These run a real subprocess (`sys.executable -c` printing `json.dumps(
sys.argv[1:])`) rather than a mock. A mock asserts what I believed about the
platform; only the platform can demonstrate that the belief is true. The
module docstring says which three tests spawn real programs and why.

### Re-measuring the batch-file claim, and finding it too broad

The 18.2 brief recorded, from an earlier probe: *"a resolved `.bat`/`.cmd`
still parses its arguments through cmd.exe even under `shell=False`; a
canary file was created this way."* True, but not for the reason implied,
and the difference decides how wide the refusal has to be.

A nine-payload battery against a real `.bat`, on Python 3.11.15, this
machine:

- `& && | ^ >` in a value are **neutralised**. The CVE-2024-1874 fix is
  present, so `subprocess` quotes batch arguments defensively and none of
  those payloads created a canary.
- A literal `"` in the value **breaks out**, and the remainder of the string
  runs as commands. Both quote-bearing payloads created their canaries.
- `%CD%` and `%PATH:~0,12%` **expand** inside the batch file - not command
  execution, but the value the program receives is not the value the model
  supplied.
- The identical payloads against a real `.exe` arrive in `sys.argv`
  byte-for-byte, with nothing created and nothing expanded.
- `.cmd` behaves exactly like `.bat`.

So the refusal stands and is scoped precisely: a command whose executable
resolves to `.bat`/`.cmd` **and which has a fillable slot** is refused
before anything is spawned. With no slot it runs, because there is no
model-supplied text for cmd.exe to re-parse - the argv is entirely the
owner's, and refusing it would be inventing a restriction section 2
forbids. This is not an exotic path: `shutil.which` resolves `npm`, `npx`
and `code` to `.CMD` shims here, so an owner declaring `npm test` with a
`{script}` slot hits it on the first try. The test uses the payload that
genuinely created a file, and asserts the canary does not exist.

### A defect in my own code, found by my own test

`ECHO_ENV` - a test helper containing a Python dict comprehension - was
refused by the tool. The cause was a leftover-brace check: after
substitution, any remaining `{...}` in the argv meant refusal, on the theory
that a misspelled slot would otherwise be passed through literally and
"run a command nobody wrote".

Both halves of that were wrong.

It breaks ordinary commands. `find . -exec rm {} \;`, `grep -E "a{2,3}"`,
`jq "{name: .n}"` - all refused, none of them a misspelled slot, all of them
things an owner would reasonably declare.

And the justification does not survive contact with section 24. The braces
in those argvs are the **owner's** text, typed into their own config file.
Section 24 fences *model-supplied* text. Passing `{pattern}` through
literally does not execute anything unwritten; it performs the wrong search
and the owner sees the wrong search. Refusing to run what the owner wrote,
to protect them from a typo they did not make, is the arbitrary restriction
section 2 names: *"Do not create arbitrary restrictions that prevent the
owner from changing their own configuration."*

Replaced with `NEAR_SLOT = re.compile(r"\{\s*[A-Za-z_][A-Za-z0-9_.\-]*\s*\}")`,
which matches only brace content that reads like a slot someone meant to
write (`{ pattern }` with spaces, `{my-pattern}` with a hyphen) and says
nothing about `{}`, `{2,3}` or `{name: .n}`. The execute-time refusal is
gone; a load-time warning remains, naming the fix (*"a value name is written
{name}, with no spaces or punctuation inside the braces"*). Warn, do not
override. The reasoning is in the source above the pattern, because the next
person to see `find . -exec rm {}` pass through deserves to know it was
considered rather than missed.

### A section 30 path nobody asked about

Section 30: an API key must *"never appear in normal logs; never appear in
chat history."* `core/credentials.py` deliberately puts stored keys into
`os.environ` so the provider clients can find them. Child processes inherit
`os.environ`. This tool's stdout goes into the transcript. So
`run_command` with `["cmd", "/c", "set"]` - or any program that dumps its
environment, or crashes with an environment dump in the traceback - would
put the owner's Gemini key in chat history, and no line of section 30 had to
be violated deliberately for that to happen.

`_child_environment` strips, before spawning:

- every name in `brain/router.py::PROVIDER_KEYS` and
  `core/credentials.py::SECRET_ENV_VARS`, imported **at call time** so a
  provider added in phase 19 is covered with no edit here;
- anything matching `KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH`, for
  the owner's own secrets that Aura has never heard of;
- with exactly one documented exception, `SSH_AUTH_SOCK`, which is the
  *path* of a socket rather than the contents of one, and without which
  `git` over SSH stops working for a command the owner declared.

The end-to-end test asks the child process itself - it prints how many
credential-shaped names it can see, whether a known key *value* arrived
under any name at all, that `PATH` survived, and that an ordinary variable
survived. That question can only be answered from inside the child, and the
report is bounded so it survives `MAX_OUTPUT` truncation.

### Three mutation survivors, three real gaps

30 mutations across `commands.py`, `factory.py`, `config.yaml` and
`settings_store.py`. Final: **30 of 30 caught, 0 survivors, 0 bad anchors.**
Three survived the first run. None was an equivalent mutant; each was a
place where the suite was checking a weaker thing than I thought.

**1. "a command line string is split instead of refused."** Survived
because a bare string is *also* caught by the generic "declaration is not a
list" branch. The command was still refused - with the unhelpful message
instead of the actionable one. The suite was asserting refusal where the
value is in the wording: `applications` (18.1) accepts exactly this shape,
so an owner writing `unit_tests: "pytest -q"` has made a specific,
predictable mistake, and the message has to name the fix. Now asserted on
the message.

**2. "credential scrub keeps Aura's own names."** Survived because every
single name currently in `PROVIDER_KEYS` and `SECRET_ENV_VARS` happens to
contain `KEY` or `TOKEN`, so the pattern sweep catches it regardless. The
imports' entire value is *future* names - and that mechanism was verified by
nothing at all. Fixed by monkeypatching `router.PROVIDER_KEYS` with
`ACME_SESAME`, a name no pattern would match, so only the live import can
withhold it. The mutation now dies.

**3. "run_command registered with no declarations."** Survived because
`RunCommandTool({}).available` is empty anyway and the inner check refuses
registration either way - the outer `if commands:` gate in `tools/factory.py`
is not what keeps the tool unregistered. What it actually decides is whether
a stock server logs *"none of the 0 declared command(s) could be used"* on
every single startup: a warning about a problem the owner does not have, in
a log where a real warning has to stand out. That is a legitimate thing to
protect, so it is now protected explicitly - the test asserts the log line's
absence, and a comment in `factory.py` says the gate governs the honesty of
a log rather than the registration, so nobody deletes it as redundant.

### Two bounds, and getting the nesting right

`tools/timeout.py` bounds `execute` on a daemon thread it cannot kill. So
the tool's own bound must sit **outside** every command's bound:
`self.timeout = longest_declared + KILL_GRACE + 5.0`. Inside it, the
executor would abandon the thread mid-kill and the process being killed
would survive with nobody watching it.

`timeout: 0` in a declaration leaves both bounds unbounded rather than being
clamped to something sensible. `tools/timeout.py` documents `0` as the "no
bound" hatch, and clamping the owner's explicit 0 is precisely the silent
mutation of owner configuration section 2 forbids: *"AURA may warn the
owner. AURA must not silently mutate the owner's configuration."* It warns.

### Temp files, not pipes - measured again on this machine

A batch file that leaves a `ping -n 40` grandchild, under
`subprocess.run(timeout=1.0)`: returned after **29.25 s**. The grandchild
inherited the stdout pipe and held it open long after `cmd.exe` died, and
`taskkill /F /T` did not unblock the read. Temp files for stdout/stderr,
`Popen` + `wait(timeout)` + `kill()` + `taskkill /F /T /PID`: **1.08 s**,
with the output the command had already produced captured and returned. A
command that overruns must not hold the reply open, so the reply carries
partial output plus the fact that it was stopped.

Two tests, because they answer different questions. The Windows one starts a
detached `ping`, asserts the call returned in under 10 s (against the
measured 29.25 s), then reads `tasklist` to confirm no `PING.EXE` survived -
the process table is the only honest answer to "did the tree die". The
portable one runs three overruns in a row, so leftovers would accumulate
into the timing if the kill were not working. An earlier version of that
second test claimed to verify the tree was gone and only re-timed; it was
replaced, because a test that overstates what it checked is worse than no
test.

### No `verify()`, deliberately

Section 11: *"Verification must not rely only on: 'the command executed
without throwing'."* The exit status satisfies that on its own - it is the
program's own verdict, read back after the fact, and a non-zero exit is an
honest `fail()` carrying stderr rather than a success with sad text
attached. Re-asking would mean running the command a second time: double
the side effects, no new information, and this tool has no idea what any
given owner-declared command was supposed to change. A test asserts the
absence is deliberate, so `verify()` is not added later by someone filling
in a perceived gap.

### Nothing is enabled

`config.yaml` ships `commands: {}`. `tools.allowed` is still
`['current_time', 'remember']`; `tools.auto_approve` is still `['safe']`;
with the shipped config `run_command` is not registered at all. Four tests
read `config.yaml` **from disk** rather than through the loader, so a
defaulting bug in the loader cannot hide a silent enable.

`tools.commands` is deliberately **absent** from
`core/settings_store.py::ALLOWED` (56 entries; its only `tools.*` keys are
`enabled`, `auto_approve`, `timeout`). A settable `commands` would let
anything holding the bearer token declare `["cmd", "/c", "{x}"]` and then
fill in `{x}` - arbitrary shell execution reached through the settings API,
around the tool boundary rather than through it. Guarded from both sides: a
Python test on `ALLOWED`, and a Kotlin test that the live settings
document's `configurable` list contains no `tools.commands` prefix.

### The device side, and a fixture that could not be regenerated

`core/config.py` gaining `commands: {}` surfaced `effective.tools.commands`
in the settings payload, which failed `tests/test_settings_fixture.py`.
The documented way to fix that is to regenerate the Android fixtures with
`AURA_WRITE_ANDROID_FIXTURES=1` - which must never run on this host,
because a real Gemini key sits in `.env` at the repo root and would be
written into a checked-in test resource. So one line was added to
`android/app/src/test/resources/live/settings.json` by targeted edit
instead, which is also what section 45 asks for: *"Do not overwrite large
files with generated replacements when a targeted edit is possible."*

`ToolsConfigDto` gained `commands: Map<String, JsonElement>` - raw JSON,
because the server accepts two declaration shapes and this screen only ever
displays them. Parsing it into a sealed Kotlin type would be a second
implementation of a schema the PC already owns, and it would fail closed on
a shape a newer server added. Its KDoc now enumerates all four read-only
capability grants, `commands` most of all: a settable one would be the
settings-API hole above, and it is reported precisely because the owner can
see on their phone what their PC has been authorised to run without being
able to change it from there.

Three device-side section 2 guards in `SettingsContractTest.kt`: the
hand-written body (which predates `commands` and does not carry it) must
still parse, because an older server that never heard of `run_command` must
not break the settings screen; the live fixture must declare no commands and
must not allow `run_command`; and `tools.commands` must not appear in
`configurable`. While adding these I attached a "reads the live fixture"
comment to an assertion that read a hand-written constant - caught on
re-reading, comment corrected, and the real guard moved to the test that
does read the fixture. A comment that misstates what a test proves is a
false verification claim with a longer fuse than a bad assert.

### Verification (actually executed, section 34)

- `tests/test_command_tool.py`: **154 passed**.
- Full backend: **`2667 passed, 2 skipped, 1 deselected in 64.68s`**
  (baseline before this phase was 2513; delta is exactly the 154 new tests).
- Android: `gradlew.bat cleanTestDebugUnitTest testDebugUnitTest
  --no-build-cache` - **378 tests, 0 failures, 0 errors, 0 skipped**,
  BUILD SUCCESSFUL, with `:app:testDebugUnitTest` confirmed **executed**
  rather than UP-TO-DATE, per section 34.
- Mutation testing: **30 of 30 caught, 0 survivors, 0 bad anchors**, across
  `tools/builtins/commands.py`, `tools/factory.py`, `config.yaml` and
  `core/settings_store.py`.
- Residue: no `.mutbak` files, no canary files, all four mutated files parse,
  harness deleted.

### Owed, recorded rather than glossed

- No Hub widget renders `tools.commands`. The DTO carries it; nothing shows
  it. Phase 23, and a different kind of debt from the fourteen
  settable-but-unrendered paths already on the list - this one is read-only
  display.
- The quote-breakout measurement is specific to Python 3.11.15 on this
  machine. The refusal does not depend on it (it refuses the whole class),
  but the *reason recorded in the source* is a measurement, and a future
  CPython could widen or narrow which payloads escape.

## Phase 18.3 - the drive letter the tests could not see (uncommitted)

Filesystem writes, section 24's *"filesystem operations"*. Four tools
added to the module that already held the readers rather than a new one:
`tools/builtins/filesystem.py`, 137 -> 688 lines. `write_file`,
`append_to_file`, `create_directory`, `delete_file`, all DANGEROUS, all
with a real `verify()`.

### The grant that was not inherited

The whole phase turns on one line in `tools/factory.py`:

```python
writable = _list_setting(config, "writable_paths")
```

A second list, read separately, and deliberately **not** defaulted from
`allowed_paths`. The tempting version - write roots fall back to read
roots - fails section 2 in the specific way section 2 warns about. An
owner adds their notes folder to `allowed_paths` so Aura can look
something up. Under the inheriting version, that single act also grants
permission to overwrite every file in it, and the owner is never asked.
Reading a directory and being allowed to destroy its contents are two
different permissions, and the owner must not find the second one
already made. An owner who wants both lists the folder in both places;
that is one extra line of config in exchange for the grant being
something they *did* rather than something that happened to them.

Tested from both directions, because the property is symmetric and a
one-directional test would miss half of it:

- `allowed_paths` alone registers `read_file` and `list_directory` and
  none of the four writers.
- `writable_paths` alone registers the four writers and neither reader.
  (`verify()` reads the file back, but that is a tool checking its own
  postcondition, not the owner having permitted `read_file` there.)
- With two *different* folders, the writer refuses the reader's folder
  and the reader refuses the writer's - and the assertion checks the
  file's bytes afterwards, not just that an exception was raised.

### The measurement that changed the code

`_atomic_write` follows the pattern already in `core/settings_store.py`,
`core/credentials.py` and `proactive/ledger.py` - temporary file, then
`os.replace` - with one difference that turned out to matter:

```python
handle, temporary = tempfile.mkstemp(
    dir=str(target.parent), prefix=".aura-", suffix=".tmp")
```

The `dir=` is load-bearing, not tidy. `os.replace` across volumes fails
on Windows with `ERROR_NOT_SAME_DEVICE` (WinError 17), because CPython
calls `MoveFileExW` without `MOVEFILE_COPY_ALLOWED`. Measured on this
host:

```
TEMP volume: C:
repo volume: D:
cross-volume replace FAILED: OSError 17
    The system cannot move the file to a different disk drive
```

So a temporary in the system temp directory would have made **every**
write to a folder on any drive other than `C:` fail - which for a typical
owner is most of the folders they would ever grant. And no test could
have seen it, because pytest puts `tmp_path` under `%TEMP%`: inside the
suite the target and the temporary are always on the same volume, so the
same-volume case always holds. The mutation battery is what surfaced it;
a suite of 117 passing tests did not.

Now covered twice: a behavioural test that goes looking for a second
volume, writes there, and skips cleanly when there is not one; and a
structural assertion that `dir=str(target.parent)` is in `_atomic_write`,
which always runs. The structural half is the weaker test and it is the
one that will still be there on a single-volume CI box.

`mkstemp` rather than `<path>.tmp` for a second, independent reason.
`settings_store` names its temporary after a fixed path Aura owns; here
the path came from a model, so `notes.md.tmp` may be a file the owner
already has. A unique name in the same directory keeps both properties:
no collision, and `os.replace` stays atomic.

### Two defects in my own tests, found by mutating my own code

22 mutations, 20 caught on the first pass. Both survivors were test
defects, not equivalent mutants - which is the only outcome that makes
the exercise worth the time.

**(1) A decoy at the wrong filename.** The test that proves the
temporary cannot clobber the owner's own scratch file planted a decoy at
`notes.tmp`. But the repository's own idiom -
`path.with_suffix(suffix + ".tmp")` - produces `notes.md.tmp` for
`notes.md`. So the test named a file no plausible alternative
implementation would touch, and could not fail no matter what
`_atomic_write` did. It now plants both spellings and asserts each is
still a file with its original bytes, since the mutant renames the
temporary away rather than merely overwriting it.

**(2) Kotlin assertions that were empty on both sides.** I added three
device-side guards for `writable_paths`. All three would have passed with
a misspelled `@SerialName`, because `writablePaths` defaults to an empty
list and every body I asserted against carried no value - empty because
absent and empty because defaulted are indistinguishable. Fixed by giving
the inline body a real writable root and reading the value back. That
also closed the identical pre-existing gap for `allowed_paths`, whose
value that same body has always carried with nothing asserting it.
Confirmed load-bearing the only honest way: misspell the `@SerialName`,
run the suite, watch `settings parse into the sections the hub renders`
fail, restore.

The full mutation list, all now caught: containment stops resolving
links and `..`; containment lets everything through; the overwrite gate
is dropped; the write is no longer atomic; `fsync` is dropped; oversize
content is truncated instead of refused; an unrecognised flag is guessed
as true; append bounds only the addition, not the total; append creates a
file that was not there; delete stops refusing directories; `write_file`
stops refusing directories; write `verify` stops comparing the bytes;
append `verify` stops comparing the tail; delete `verify` stops looking;
mkdir `verify` stops looking; mkdir claims it created what was already
there; paths are shown in full; the relative-path message is dropped; the
temporary is named after the target (two spellings); the temporary is
written in the system temp directory; content is written as text,
translating newlines; writable roots default from the read roots.

### The escape that could not be tested, and the one that could

The most important property in the module is that nothing lands outside a
writable root. A symlink at the target is the sharpest version: resolving
before checking is what catches it, and it matters more for writing than
for reading, because a followed link would replace the owner's real file
elsewhere and leave the link looking untouched.

The symlink test skips on this host - `WinError 1314, A required
privilege is not held by the client`. Creating a symlink on Windows needs
`SeCreateSymbolicLinkPrivilege`, which means Developer Mode or an
elevated runner. Leaving it at that would have meant the single most
important property in the phase was permanently unexercised here and
only ever checked on CI.

A **directory junction** needs no privilege, and `Path.resolve()` sees
through one exactly as it sees through a symlink. Verified before relying
on it: `mklink /J` returned 0 unelevated, and `resolve()` reported the
target outside the root. So the same escape is now exercised, by both
`write_file` and `delete_file`, with the outside file's bytes checked
afterwards.

The junction is removed inside the test's own `finally`, not left to
`tmp_path` cleanup, because `shutil.rmtree` follows a junction into its
target - and pytest reaps old `tmp_path` directories with `rmtree`. In
this test the junction points inside `tmp_path`, so following it would be
harmless, but the habit is what matters: a test that leaves a junction
lying around is one edit away from pointing at something real.

**A limit worth recording rather than papering over:** a *hard* link is
not caught, and cannot be. Both names are equally real, so `resolve()`
has nothing to see through. It is not reachable through Aura - no tool
creates links - but if one ever does, this is where the reasoning
changes.

### What each refusal is for

- **Replacing is asked for, never assumed.** An existing file needs
  `overwrite=true`. A model that means `notes-2026.md` and types
  `notes.md` would otherwise destroy a year of the owner's writing
  silently, which is exactly the high-impact-without-confirmation
  section 21 rules out. The message names *both* ways forward - pass the
  flag, or write to a different name - because a refusal that names
  neither gets retried verbatim.
- **`append_to_file` refuses a missing file.** Shell `>>` would create
  it. That is the wrong default for a path a model chose: a mistyped
  name becomes a new file, the append reports success, and the owner's
  real log stays empty while Aura reports writing to it every day. The
  message names `write_file` as the way to start a file deliberately.
- **`create_directory` creates parents; `write_file` does not.** Not an
  inconsistency. An extra empty directory costs nothing and is obvious;
  an extra *file* down a mistyped path is a stray copy of real content
  that looks like the real thing.
- **`delete_file` never takes a directory.** Section 24 asks for
  "filesystem operations", and recursive deletion is a different blast
  radius from everything else here with nothing to read back afterwards
  to learn what was lost. It is absent, and the refusal says so rather
  than failing obscurely on a directory handle.
- **Oversize content is refused, not truncated.** Truncating is silent
  data loss, and it would pass `verify` only if `verify` truncated
  identically - two wrongs agreeing. `MAX_WRITE_BYTES = 100_000`; the
  message carries the size and the limit so the caller can split.
  `append_to_file` bounds `before + len(data)`, not just the addition,
  or a bounded append repeated is an unbounded file.
- **`create_directory` is idempotent and says which.** "already exists"
  and "created" are different facts about the machine, and a caller told
  "created" about a directory full of the owner's files has been told
  something untrue.

### Two smaller decisions

**Newlines are not translated.** `Path.write_text` turns every `\n` into
`\r\n` on Windows, so a two-line file would not contain what was asked
for and the byte comparison in `verify` could never pass. `_atomic_write`
writes encoded bytes untranslated, in both directions - a caller who
sends `\r\n` gets `\r\n`.

**Paths are shown relative to their root, with forward slashes.** Every
message here ends up in a prompt and therefore leaves the machine, and an
absolute path names the owner's home directory and username. This is the
same reason `system_information` reports the disk and not the user. Two
tests assert it structurally: no success or refusal message contains
`str(Path.home())`. `_shown` falls back to `target.name` for a path it
cannot place, because it is called on the way out of error paths too and
must not raise.

This also fixed a real defect during smoke testing: `create_directory`
reported `created c` for `a/b/c`, which loses the structure the tool
exists to create and does not say whether `a/b` was made too. The
readers were left on `target.name` - changing a message with no defect
behind it is how a working system acquires churn.

**A relative path gets its own message.** `_contained` resolves against
Aura's working directory, so `notes.md` becomes `D:\AURA\notes.md` and is
outside every root - true, and useless to a caller who will just try
another bare filename. Containment semantics were *not* changed: with
multiple roots there is no non-arbitrary root to resolve against, and
altering shipped, tested read behaviour with no instruction to do so is
not in scope. Confirmed by grep first that no test pinned either message.
The message deliberately does not echo the resolved absolute path, for
the same reason as above.

### Verification, actually executed

- `2786 passed, 3 skipped, 1 deselected` (baseline `2667 passed, 2
  skipped, 1 deselected`). 119 new tests in
  `tests/test_filesystem_writes.py`, 0 regressions.
- The one new skip is the symlink-privilege skip described above. It
  names the `WinError 1314` in its skip reason rather than passing
  quietly.
- Android: `cleanTestDebugUnitTest` + `testDebugUnitTest` executed with
  `--no-build-cache`; `27 suites, 378 tests, 0 failures, 0 errors, 0
  skipped`. Counts read from the JUnit XML rather than trusting
  BUILD SUCCESSFUL.
- 22 mutations over `tools/builtins/filesystem.py` and
  `tools/factory.py`, all caught after the two test defects above were
  fixed. Both source files confirmed byte-restored afterwards.

### The device side

`core/config.py`'s new default surfaced `effective.tools.writable_paths`
and failed `tests/test_settings_fixture.py` with
`settings.json is missing fields the server now sends` - exactly as
`commands` did in 18.2, and predicted in the 18.3 brief before it
happened. Fixed with a one-line targeted edit to
`android/app/src/test/resources/live/settings.json`, not a regeneration:
`AURA_WRITE_ANDROID_FIXTURES=1` rewrites the fixtures from this host's
live state, and there is a real Gemini key in `.env`, so regenerating
here is the section 30 violation that happened once already in phase 10.
Section 45 asks for targeted edits anyway.

`ToolsConfigDto` gained `@SerialName("writable_paths") val writablePaths:
List<String>`. Not forced - `ApiFactory` sets `ignoreUnknownKeys = true`,
so the device tolerated the new key without any change. Added because a
writable root is arguably the single most important line in the tools
config for an owner to be able to *see* from their phone, and because
18.2 set the precedent of parsing now and building the widget in phase
23. Three guards: an older server that sends no `writable_paths` reads as
"nothing is writable" rather than as a parse failure; the shipped server
declares none and allows none of the four names; and `tools.writable_paths`
is absent from `configurable`, prefix-checked, so a future server that
starts sending them one at a time is caught too.

### Nothing is enabled

`config.yaml` ships `writable_paths: []` with an owner-facing comment
explaining the separation from `allowed_paths` in the concrete terms the
decision was made in. Read from disk in the guards, not through the
loader, so a defaulting bug in the loader cannot hide a silent enable -
the same technique as 18.2. `allowed` is still
`['current_time', 'remember']`; `auto_approve` is still `['safe']`, so
with no confirmation handler - server mode - a DANGEROUS call is refused
before it reaches the tool, and there is a test for that state
specifically because it is the state the shipped configuration is in.

`tools.writable_paths` is deliberately **absent** from
`core/settings_store.py::ALLOWED`. A settable one would let anything
holding the bearer token add `C:/` and then have `write_file` replace
whatever it liked: filesystem access reached *around* the tool boundary
through the settings API instead of through it. Section 2's own limit
covers this - owner configuration freedom does not extend to letting an
LLM bypass application-level permission boundaries.

### Owed, and recorded

- No Hub widget renders `tools.writable_paths`. The DTO parses it and
  nothing displays it. Phase 23, joining `tools.commands` as read-only
  display debt - distinct from the fourteen settable-but-unrendered
  paths.
- `append_to_file`'s `verify` proves the added text arrived and **not**
  that nothing else changed. The prior length is not among the arguments
  `verify` receives, and stashing it on the instance would be a lie the
  moment two appends overlap. Said plainly in the docstring rather than
  left for a reader to discover.
- The cross-volume test skips on a single-volume host. The structural
  assertion is what runs there, and it is the weaker of the two.
- Hard links are not caught by containment and cannot be. Not reachable
  through Aura today; recorded in case a tool ever creates one.


## Phase 18.4 - screenshots (section 24). IMPLEMENTED.

`take_screenshot`, one DANGEROUS tool, plus the two things it turned out
to need: a capture backend that works on a stock Windows machine and a
PNG encoder that needs nothing installed.

**Result:** `tests/test_screenshot.py`, 112 tests, 48/48 mutations caught.
Full suite `2898 passed, 3 skipped, 1 deselected in 43.22s`.

### The brief was wrong about the dependency, and that mattered

The 18.4 brief assumed `vision.capture.ScreenshotCapture` could be reused
directly. It wraps `mss`; `mss` is optional and is **commented out** in
`requirements.txt`; it is **not installed on the owner's machine**. So
`capture_screen: true` in `config.yaml` had been quietly doing nothing
here - `_build_vision` constructed `ScreenshotCapture`, got
`is_available() -> False`, and the pixel half of vision was unreachable
code on the machine Aura actually runs on. Not a phase 18 regression; a
latent defect that phase 18 walked into.

Adding `mss` to `requirements.txt` was the obvious move and the wrong one
(section 41: do not add dependencies where the platform already answers).
`GdiScreenCapture` was written beside `ScreenshotCapture` instead, using
the same ctypes route 18.1's `list_windows` already uses for `user32`:

    GetDC(None) -> CreateCompatibleDC -> CreateCompatibleBitmap
                -> SelectObject -> BitBlt(SRCCOPY) -> GetDIBits

`default_screen_capture(monitor)` prefers `mss` when installed, falls back
to GDI on Windows, and returns **None** everywhere else - never a
`MockScreenCapture`, following the factory rule the tool layer already
uses. A mock wired in by default would let a screenshot tool report
success having written a blank 1920x1080 image. `launcher/services.py`
now builds vision through it, which is why `capture_screen: true` works on
this host for the first time.

### The encoder is four chunks and a zlib stream

`encode_png(frame)` in `vision/capture.py`: `zlib` and `struct`, both
standard library. Deliberately not PIL. Pillow *is* present here (11.1.0)
but is listed optional, and a screenshot that required it would be the one
capability in the PC layer an owner could not use without installing
something - while 18.1's window enumeration, 18.2's command execution and
18.3's filesystem writes all need nothing.

Refuses what it cannot honestly encode: a non-`rgb` frame (an
already-encoded JPEG is **not** passed through silently - that would write
JPEG bytes under a `.png` name and tell the owner something untrue about
their own file), a zero area, and data whose length disagrees with
`width * height * 3`. `Frame`'s default `image_format` is `"raw"`, which
is also what `MockScreenCapture` returns when it runs out of frames, so
the format check is a live guard rather than a formality.

`OllamaVisionProcessor._to_png` still uses PIL and was **left alone**
(section 41). It round-trips already-encoded frames through `Image.open`
to guarantee the model gets one known format, which is more than
`encode_png` does, and rewriting a tested path that works is churn. What
it should eventually do is fall back to `encode_png` when PIL is missing.
Recorded, not done.

### Section 11: a byte count cannot see the two defects that matter

`GetDIBits` filling `width * height * 3` bytes is exactly the sentence
section 11 forbids resting on. Two specific wrong pictures have the
identical length:

- **Mirrored.** GDI writes bottom-up rows for a positive `biHeight`. The
  fix is `info.bmiHeader.biHeight = -height`, which is one character away
  from a valid, correctly-sized, upside-down screenshot.
- **Channels swapped.** GDI hands back BGRX, not RGB. `_bgrx_to_rgb`
  converts by slice assignment (`out[0::3] = pixels[2::4]` and so on),
  and a wrong offset there produces a valid image of the right size in
  false colour.

Neither is catchable by inspecting my own output with my own assumptions.
So the pixels are compared against PIL's `ImageGrab` - an independent
implementation of the same capture - and against that same reference
deliberately flipped and channel-swapped, which must match *worse*:

    sampled 2400 pixels: 2400 identical, worst channel delta 0
    vertically flipped reference:   90.3% close
    red/blue swapped reference:      4.9% close

That is a test, not a one-off measurement. `test_the_picture_matches_an_
independent_capture` is what catches the `biHeight` mutation, and it is
the only test that does.

The encoder's own structural claims are checked the same way - by decoding
with PIL rather than re-reading my bytes back: exact pixels of a
four-colour 2x2, a non-square frame not transposed, top row distinct from
bottom, every chunk CRC recomputed, and one filter byte per scanline
counted out of the inflated IDAT.

### `verify()` says what it can, and refuses to say more

Reads the file back, checks the signature, checks `head[12:16] == b"IHDR"`,
parses width and height and refuses zero. Because "the write did not
throw" leaves a file behind when a disk fills, a quota bites, or an
antivirus rewrites the file as it lands.

It does **not** claim the picture shows the screen, and the docstring says
so in those words. Nothing can re-ask that: the screen has already changed
by the time anyone looks, and a second capture to compare against is a
different image of a different moment. That the pixels are faithful was
established once, against an independent implementation, and lives in this
record rather than being pretended at on every call.

### Nothing is captured on a path that will be refused

`execute` proves the destination before it reads a single pixel:
containment, not-a-directory, `.png` suffix, overwrite stated, parent
exists. A screenshot held in memory on a failure path is a privacy leak
that leaves nothing behind to find.

Five tests assert a counting fake capture's `captures == 0` - one per
refusal - and the mutation battery proves they are load-bearing: moving
`target = self._target(...)` to after `frame = capture.capture()` is
caught by all five. The first attempt at that mutation only moved it past
the *factory* call, which builds a backend and reads nothing; the mutation
was re-specified rather than the result accepted.

### No new grant, no new setting, and nothing enabled by default

The destination goes through `tools.writable_paths` - the grant 18.3
already defined - and through 18.3's own `_contained`, `_atomic_write` and
`_shown`, **imported** from `tools/builtins/filesystem.py` rather than
reimplemented. The reason is written at the import: two resolve-and-compare
implementations can drift, a drift in a message is cosmetic, a drift in
`_contained` is a path escape. Same reasoning `run_command` uses when it
imports `PROVIDER_KEYS` from `brain/router.py` instead of keeping its own
list of names to strip. A test asserts `screen.py` has no `def _contained`
of its own.

Registration sits inside the existing `if writable:` block in
`tools/factory.py`, gated a **second** time on
`default_screen_capture() is not None`. So:

- shipped `config.yaml` -> `writable_paths: []` -> no screenshot tool, and
  `take_screenshot` is not in `allowed` either;
- a granted writable root -> the tool appears, scoped to that root;
- a headless machine or a container -> the writers still register, the
  screenshot does not, and the reason is logged. The model is never
  offered a capability it cannot perform.

`core/settings_store.py::ALLOWED` gained **nothing**. A settable
screenshot path would be a second setting answering the same question as
`writable_paths` and able to disagree with it.

### Which display, and why a factory rather than a capture

Configured index, overridable per call. `monitor: "2"` from YAML is read;
`monitor: yes` is refused, because bool is an `int` subclass and
"display 1 by accident" is not a choice the owner made. An index no
display answers to falls back to the **primary** and warns once - never to
index 0, which is every monitor stitched into one wide image and reads
like a hallucination from outside.

`ScreenshotTool` takes `capture_factory: monitor -> ScreenCapture | None`
rather than a capture object. The alternative is mutating a shared
backend's `monitor` between calls, and that backend carries a warn-once
flag whose entire purpose is to fire - so reusing one object across
displays would suppress the warning for a second bad index, capturing the
wrong screen silently. That is the exact failure `_resolve` exists to
prevent.

`_monitors()` mirrors mss's index semantics: 0 is the union of every
display, 1 is the primary, 2+ are the others. The primary is **sorted**
first because `EnumDisplayMonitors` order is undocumented, and on a
machine where it does not come first `monitor: 1` would silently mean a
different screen. On this single-display host that sort is unobservable,
and the test for it says so.

### DPI awareness: thread-scoped, reversible, and unproven here

An unaware process is lied to about screen size - measured on this
machine, a 1920x1080 panel at 150% scaling reports 1280x720.
`SetThreadDpiAwarenessContext(-4)` fixes it for the calling thread only,
and was probed for reversibility before being relied on (previous context
`2147508240`, restored). The process-wide variant was **rejected**: it is
permanent and changes how every other thing in the process reads the
screen, which is not a choice a screenshot should make for the whole
application.

Its benefit is **unverifiable on this host** - single display at 100% - and
that is recorded rather than claimed. What *is* tested is the discipline:
the undo runs after a successful capture, runs on the failure path too
(the `finally`), and is a real restore rather than a no-op lambda.

### Handles

Every capture acquires a screen DC, a memory DC and a bitmap, and releases
all three in a `finally` in reverse order. A leak there is invisible per
call and fatal over a session that captures every two seconds. Tested by
asking Windows: `GetGuiResources(process, GR_GDIOBJECTS)` before and after
eight captures, tolerance 2. Each of the three releases has its own
mutation, and each is caught by that one test.

### Section 30: nothing sensitive in a message or a log

The return value is one short line - `saved shots/today.png (1920x1080,
154802 bytes)` - root-relative via `_shown`, so it never names the owner's
home directory or username, and it never contains image bytes. The log
line carries the monitor index and the geometry only. Tested on the
success path and on four separate refusal paths, and both the absolute-path
mutation and a log-the-pixels mutation are caught.

### What the mutation battery found

48 mutations across `vision/capture.py`, `tools/builtins/screen.py` and
`tools/factory.py`. First pass: 43 caught, 5 survivors. Two survivors were
my own mis-specified mutations. **Three were real test defects**, and the
battery is the only reason they are known:

1. **A test that could never fail.** `test_true_is_not_display_one`
   asserted `_resolve(monitor=True) == PRIMARY_DISPLAY`. `PRIMARY_DISPLAY`
   is 1 and `True == 1` in Python, so an implementation that used the bool
   directly as an index satisfied the assertion. Exactly the class of
   defect 18.3's battery found. Replaced with a `type(resolved) is int`
   check plus the two cases where the guard is genuinely observable:
   `monitor: no` is `int(False)` is 0, a *different* screen from the
   fallback; and a bool must produce the configuration warning rather than
   quietly work.

2. **A test that measured its baseline too late.** The DPI restore test
   read the thread context immediately before a capture - but the first
   capture of the session had already left the thread aware, so with the
   restore removed the test compared an already-changed value against
   itself and agreed. Order-dependent and silently useless. Replaced with
   a recording undo handed to `capture` directly, plus a failure-path
   test, plus an isolated round-trip proving the undo is real.

3. **Two tests that could not report their own failure.**
   `monkeypatch.setattr(os, "name", "posix")` patches the real module, and
   `pathlib` consults `os.name` to choose a Path flavour. A *failing*
   assertion inside that window makes pytest raise
   `NotImplementedError: cannot instantiate 'PosixPath' on your system`
   while formatting the failure - an `INTERNALERROR` that aborts the whole
   session, names no test, and skips everything not yet run. A green run
   never notices; the battery saw it as two mutations "caught by
   <collection error>". Replaced with a `not_windows()` helper that patches
   `vision.capture.os` with a namespace carrying only `name`.

After the fixes: **48/48 caught, every one attributed to a named test.**

### Owed, and recorded

- `OllamaVisionProcessor._to_png` should fall back to `encode_png` when
  PIL is absent. Left alone on purpose (section 41); this is the one place
  in the codebase where a working path duplicates new capability.
- The GDI DPI-awareness benefit is unverifiable on this host: one display
  at 100%. The mechanism is tested; the payoff is not.
- `_monitors()`'s primary-first sort is unobservable with one display.
  Only a fake exercises the ordering here.
- No Hub widget shows that `take_screenshot` is available or which root it
  writes to. Phase 23, joining `tools.commands` and `tools.writable_paths`
  as read-only display debt.

## Phase 18.5 - keyboard and mouse input synthesis (section 24). IMPLEMENTED.

The last of section 24's list: "keyboard input / mouse interaction". Four
tools in `tools/builtins/input.py` - `move_mouse`, `click_mouse`,
`type_text`, `press_keys` - behind `SendInput`, registered by
`tools/factory.py` beside the 18.1 window tools and sharing their one
window source.

This is the most dangerous thing in the PC layer. Every earlier phase
either read the machine (18.1 observation, 18.4 pixels) or wrote somewhere
the owner had explicitly named (18.2 declared commands, 18.3
`writable_paths`). This one presses real keys into whatever window happens
to be in front, with no list of permitted targets to check against,
because there is no such list to write: the target is "the desktop".

### Two measurements the design rests on

Both taken before any code was written, per section 44's rule against
inventing API behaviour.

**`SetCursorPos` reports success for a point it silently clamps.** Asked
for `(2420, 1580)` on this 1920x1080 desktop it returned **true** and left
the pointer at **(1919, 1079)**. `(-40, -40)` returned true and landed at
`(0, 0)`. In-bounds moves are exact. So section 11's "verification must not
rely only on: the command executed without throwing" is not hypothetical
here - it is the literal failure mode of the primary API. A click aimed off
screen would land on whatever sits in the bottom-right corner and the
caller would be told it worked. Hence `_target` refuses off-desktop
coordinates *before* moving, and `verify` re-reads the position afterwards.

**A wrong `cbSize` is completely silent.** `SendInput(1, array, 8)`
returned 0 and left `GetLastError()` at **0** - no exception, no error
code, nothing inserted. The accepted-event count is the only signal that
exists for that whole family of failure, so `_send` returns it and `_sent`
compares it. Measured `sizeof(INPUT)` on this host: **40** (KEYBDINPUT 24,
MOUSEINPUT 32, `dwExtraInfo` pointer-sized via `wintypes.WPARAM`).

And one honesty limit that cannot be closed: Microsoft documents that
`SendInput` blocked by UIPI is reported through **neither** the return
value **nor** `GetLastError`. A full accepted count therefore does not
prove arrival at an elevated window. `_sent`'s message names that
possibility rather than claiming delivery.

### The postconditions, and what each refuses to claim

`GetAsyncKeyState` was measured seeing a *synthesized* VK_SHIFT: the
down event made `& 0x8000` true and the up cleared it. That makes a
stuck-modifier check a genuine postcondition rather than the decorative
kind the brief warns about.

- `move_mouse` / `click_mouse` re-read the cursor and fail past
  `POINTER_TOLERANCE` (2 px - measured exact, slack for a coarser host,
  not enough to hide a clamp). `click_mouse` delegates to the same check
  and **claims nothing about the click having done anything**: only the
  application knows whether the button under the pointer was enabled.
- `press_keys` asserts **no modifier this call pressed is still held**.
  That is the only durable trace a key press leaves, and the failure it
  catches is genuinely nasty - a CTRL that never came back up turns
  everything the owner types next into shortcuts with no message anywhere
  saying why. It does not claim `ctrl+s` saved anything.
- `type_text` re-checks only that the named window is still in front, and
  its docstring says explicitly that it does not claim the characters
  arrived. Nothing can re-ask that.

`verify` returning `None` where the desktop is unreadable is deliberate:
the executor treats None as no postcondition offered, which is honest -
nothing was learned - and is not the same as the action having failed.

### The `window` guard is the real safeguard, not a fourth risk level

A plan two steps long - focus the editor, then type - has a gap the tool
cannot see: between the steps the owner alt-tabs to their bank, and step
two types into whatever is there now. `window` turns that from silent
misdelivery into a refusal.

**A guard that cannot be checked is refused, not skipped.** No window
source, an unreadable desktop, or nothing reporting itself active all
raise. Ignoring a safeguard because the desktop is unreadable would give
the caller the words without the protection.

All four tools are DANGEROUS, which is the **top of the existing ladder
rather than a new rung** - and `ToolRisk.DANGEROUS`'s own docstring
already names "click" as its example. A fourth level would have to be
learned by the settings contract, the Android DTO, `config.yaml` and the
Hub before it protected anything, and it would protect nothing the owner
cannot already get by leaving `dangerous` out of `tools.auto_approve`. The
effort went into the guard, which the ladder genuinely cannot express.

`click_mouse` requires x and y rather than clicking wherever the pointer
is. Not a convenience: a click at an implicit position would be approved
without the confirmation prompt naming where it lands, because an earlier
call decided that.

### The guard proved itself before it was ever tested

The end-to-end probe was going to drive Notepad. Its pre-check found
**`cf-backup-codes.txt - Notepad` already open** - the owner's own file,
on the machine the suite runs on. The probe refused and Notepad was
abandoned entirely. That is the exact scenario the `window` argument
exists for, arriving unprompted on the first real run.

(Two smaller findings from the same probe: Windows 11 Notepad is a
packaged app, so the pid `subprocess` returns does not own the window and
matching by pid never succeeds. And `GetClipboardData` **must** be
declared `restype = wintypes.HANDLE` - undeclared it returns `c_int`,
truncating a pointer-sized handle, and `GlobalLock` on the garbage
**segfaults the process**, exit 139 with buffered stdout lost. Run probes
with `-u`.)

### Verified against an independent oracle

A tkinter window we own, driven by the production tools, reading back what
the application actually received - the widget's contents rather than a
clipboard copy that could fail on its own. Six oracles, **FAILURES:
none**: text byte-identical including a newline and a tab; backspace
deleting exactly one character; `shift+q` producing `Q`, which proves the
release ordering; the click landing on the exact aimed pixel with the
window correctly named; both refusals firing; and the clamp reproduced.

### Two defects the writing found

1. **`MockInputSynthesizer.write` cancelled its own count.**
   `2 * len(text.encode("utf-16-le")) // 2` is `len(bytes)` by precedence,
   not `2 * units`. Every accepted-count test would have agreed with itself
   for the wrong reason. Fixed to `2 * (len(...) // 2)`.
2. **`split_typing` collapsed a blank line.** It compared the last emitted
   piece against `Key("enter", 0x0D)`, so the second newline of `a\n\nb`
   was swallowed and two paragraphs became one. Rewritten around a
   `previous` character so only a `\n` directly following `\r` is dropped.
   Now in the suite as `test_a_blank_line_keeps_both_of_its_newlines`, and
   the battery reintroduces the original bug to confirm it is caught.

### What the mutation battery found

**45 mutations, 0 survived, 0 misapplied** - every one attributed to a
named test. It found three real gaps rather than none:

1. **A test that could not see a second enumeration.**
   `test_they_share_the_window_source_with_the_desktop_tools` patched
   `default_window_source` with `lambda: source` - one shared object - so
   it passed whether the factory called it once or five times. The
   mutation that gave the guard its own source **survived**. Replaced with
   a stub handing out a *different* `FakeWindows` per call, asserting the
   call count is 1 and that all four input tools hold the same object
   `list_windows` does (which stores it as `self.source`, not `windows`).
2. **`press_keys`'s description did not say where the keys land.**
   `click_mouse` says "a real mouse button", `type_text` says "whichever
   window is currently in front"; `press_keys` said neither, and it is the
   one that can send `alt+f4`. The description is what a confirmation
   prompt shows, so the fix went in the description, not the test.
3. **The backend's event stream had no unit coverage at all.** The chord
   release order is Windows-only code the mock cannot speak for, and it
   holds the worst failure in the file: releasing CTRL before the letter
   turns `ctrl+s` into a stray `s` typed into the document. The e2e probe
   caught it on hardware; nothing caught it in the suite. Added
   `TestTheEventsSentToWindows`, which stubs `_bind` truthy and replaces
   `_key_event` with a recorder, so the order, the KEYUP flags, the
   extended flag on *both* halves, and the UTF-16 surrogate pair are all
   asserted without a keyboard. Seven mutations now land there.

The two "misapplied" patterns were also informative: `risk =
ToolRisk.DANGEROUS` appears **once**, on the shared `_InputTool` base, so
one edit downgrades all four tools - and `MoveMouseTool` declares x and y
identically to `ClickMouseTool`, so the click block needed its `button`
parameter as an anchor.

### The suite only reads the real machine

`TestTheWindowsBackend` runs on hardware but **never presses a key or a
mouse button**. The owner may be using this machine while the suite runs,
and a synthesized click would land on whatever they have open. It checks
`sizeof(INPUT)` is 40, that the binding is cached, that the desktop has a
size, that the cursor reads, that `window_at` answers, and that no
modifier is reported held. The one exception moves the pointer and puts it
back in a `finally` - once to prove in-bounds moves are exact, once to
reproduce the clamp.

**Full suite: 3059 passed, 3 skipped, 1 deselected in 44.84s** (18.4's
baseline was 2898 passed; +161 is exactly `tests/test_input.py`). No new
config keys, so no Android fixture change.

### Owed, and recorded

- Windows' foreground lock is still unexercised on hardware. The Tk window
  already held the foreground, so `WindowSource.focus()` under the lock
  remains untested - carried unchanged from 18.1.
- UIPI blocking of synthesized input into an elevated window was not
  exercised. The limit is documented in `_sent`'s message and above; it
  cannot be closed by a test that does not run something as administrator.
- No Hub widget shows that the four input tools are available. Phase 23,
  joining `take_screenshot`, `tools.commands` and `tools.writable_paths`
  as read-only display debt.

## Phase 19 - vision (section 19). IMPLEMENTED.

Vision existed before this phase and was worse than it looked. Three
sub-phases: 19.1 fixed an inversion, 19.2 added the half that was
actually missing, 19.3 proved both.

### 19.1 - the feature that made things worse

Not a missing feature. A regression hiding behind a switch. Measured on
this machine, both halves:

    capture_screen=False -> [screen] User is browsing the web in Chrome
    capture_screen=True  -> None

Turning pixel vision *on* made vision report *nothing*, because
`_build_vision_processor` **replaced** `WindowTitleProcessor` with the
pixel processor rather than layering over it, and `VisionManager.refresh`
reads an empty description as "no observation" and drops the context to
None. So an owner whose Ollama daemon was not running traded a working
sentence for silence by switching on the feature meant to improve it.

`vision/processor.py` `ProcessorChain` is the fix: ask each processor in
turn, first non-empty answer wins, window titles last as the floor. The
subtle part is that the two bundled processors decline **differently** -
`OllamaVisionProcessor` returns `""` for every failure it has (dead
daemon, HTTP error, model not pulled, unencodable frame) while
`CloudVisionProcessor` *raises* `ProviderUnavailableError`. A chain
catching only one of those would fall through for one backend and go
silent for the other, which is this phase's bug reintroduced one layer
up. Both are exercised against the real classes rather than stand-ins.

Deliberately not `FallbackProvider`: that is the same shape for text
providers, and reusing it would mean `vision/` importing
`brain/providers/` - the one dependency edge this package's docstring
says does not exist - to gain a five-line loop that advances on the wrong
condition. Deliberately not a concatenation either: the pixel model can
read the window title out of the pixels, so both descriptions in the
prompt would pay tokens to say the same thing twice.

### 19.2 - the half that was actually missing

Ambient vision has shipped since phase 4: a line in every prompt saying
what is on screen, throttled, silent on failure, and never asked for.
That is context, not an action. `tools/builtins/vision.py`
`DescribeScreenTool` is the other shape - the model decides it needs to
look, says so, and gets an answer through the same five gates as every
other tool.

Three decisions carry the design, and each one was a bug avoided rather
than a preference:

**`execute` calls `refresh()`, not `get_context()`.** The throttle exists
so fifty turns in a minute do not become fifty screenshots, and it is
exactly wrong here. The model asked to look. Handing it a two-second-old
cache answers a different question than the one asked.

**`execute` refuses when `vision.enabled` is off.** Found by reading
`VisionManager`: `refresh()` does **not** consult the flag - only
`get_context()` does. A tool calling refresh blindly would look at a
screen the owner had switched off, silently, with nothing downstream able
to tell. That is section 2 with pixels attached. Guarded twice: the
factory does not register while vision is off, and `execute` refuses if
it was switched off since.

**Risk is read off the processor chain per instance.** Reading the screen
is SENSITIVE. Sending a picture of it to a hosted model is a different
act, and section 30 does not let the second ride on the first's
permission. The signal is a new `sends_pixels_offsite` flag on the
processors - False on titles, mock and Ollama (loopback or an
owner-typed host is the owner's own daemon), True on
`CloudVisionProcessor`, and `any()` across a chain, because the shipped
order puts the cloud link in the *middle* rather than at either end. Read
through `getattr` with a False default everywhere, so it is a fact a
processor may offer rather than a member the protocol demands.
Over-reporting is the safe direction: a chain whose cloud link has no
usable key still reports DANGEROUS, which costs one confirmation prompt,
where the mistake in the other direction costs an upload nobody
approved.

### The verify was nearly self-defeating

First draft read `get_context()`. That re-observes once its throttle
expires - and with `min_interval: 0`, on **every** call - so verification
would have paid for a second capture per tool call and, with a cloud link
in the chain, a second upload of the owner's screen. Two new read-only
properties on the manager instead: `last_observation` and
`seconds_since_observation`.

Freshness is the load-bearing half. "I looked and saw X" and "I did not
look and X is what I remember" are the same string, and section 11
forbids trusting the second. `STALE_AFTER = 60.0` is generous on purpose:
`refresh` stamps its clock *before* asking the processor, so a vision
model taking twenty seconds leaves a twenty-second-old observation and
has done nothing wrong. The bound is not a latency policy - it catches a
description served out of a cache by something that never looked.

Deliberately **not** compared against what `execute` returned. That would
need the tool to remember its answer across two calls, and the manager is
shared - an ambient observation landing in between would change the held
description for a perfectly good reason and fail a call that did nothing
wrong.

### A defect the full suite found that the file could not

`test_vision_switched_off_means_no_tool` passed alone and failed in the
full run. Under pytest the root logger already has a handler by the time
`core.logger` is imported, so `setup_logger` returns early on
`hasHandlers()` and never calls `setLevel` - measured, `Aura.level == 0`
(NOTSET), effective 30 by inheritance. Any earlier test that runs
`apply_config_level` leaves it at INFO, and at INFO the factory's
`logger.debug` is dropped **at the logger**, before caplog's root handler
ever sees it. A bare `caplog.at_level("DEBUG")` only raises *root's*
level, which is why the assertion held in isolation.

Fixed in the test with `logger="Aura"` - the idiom
`tests/test_error_visibility.py` already uses. The source was not
touched, because the source is right; the test was making an assumption
about global logger state that only held when it ran first.

### What the mutation battery found

15 mutations over the five files, each named against the test that must
notice: **15 of 15 caught, 0 survivors.** Every anchor asserted to match
exactly once before writing, all five files restored from an in-memory
snapshot in a `finally`.

The four that matter for security: dropping the `vision.enabled` guard
(section 2); making `CloudVisionProcessor.sends_pixels_offsite` False
(section 30); fixing the risk at SENSITIVE regardless of chain; and
making the chain report only its **first** link's exposure - caught by
the mid-chain test specifically, since the shipped order would have let a
first-link reading understate a live upload. Section 11's three -
ignoring staleness, returning ok unconditionally, reading
`get_context()` instead of `last_observation` - are each caught by a
different verify test.

One mutation reported ANCHOR x2 rather than surviving: `return
self._context` appears twice in `manager.py`. Re-run with a
docstring-tail anchor and caught by
`test_an_observation_just_inside_the_bound_still_verifies`.

### Registered is not enabled, twice over

`tools/factory.py` registers `describe_screen` only while
`vision.is_available()` - the gate is vision being *switched on*, not the
manager merely existing, precisely because `refresh()` ignores the flag.
And the shipped `config.yaml` `tools.allowed` still names `current_time`
and `remember` and nothing else, verified by reading the file off disk
rather than through the loader. Both gate messages are pinned with the
mock processor's `calls == 0`, so a refused call is proven to have
captured nothing.

`vision.enabled` was already contractually restart-only
(`server/settings_service.py`, "Persisted, needs restart"), so static
registration introduces no new inconsistency; that docstring was extended
to record the new dependency rather than the behaviour being changed.

**Full suite: 3113 passed, 3 skipped, 1 deselected in 42.61s** (18.5's
baseline was 3059; +54 is exactly `test_vision_chain.py`'s 25 plus
`test_vision_tool.py`'s 29). Android: BUILD SUCCESSFUL in 59s with
`compileDebugKotlin`, `compileDebugUnitTestKotlin` and
`testDebugUnitTest` all listed without UP-TO-DATE or FROM-CACHE.

### Also corrected while here

`requirements.txt` claimed vision needed both `mss` and Pillow and fell
back to window titles without them. Neither half was true after 18.4
added the GDI backend, and the fallback is a chain now rather than a
substitution. `tools/builtins/__init__.py` said "Six tools" and listed
six modules; measured, there are ten modules and 18 tool classes -
rewritten from the measurement, with the risk levels per tool and the
note that everything above SAFE is absent from the shipped allow list.

### Owed, and recorded

- No Hub widget for the three new settable vision paths
  (`vision.capture_screen`, `vision.send_screen_to_cloud`,
  `vision.min_interval`), and none showing `describe_screen`'s
  availability. Phase 23, joining `take_screenshot`, the four input
  tools, `tools.commands` and `tools.writable_paths` as display debt.
- The `server.screen.min_interval` vs `vision.min_interval` conflation
  over one live attribute is recorded, not resolved.
- Nothing here was exercised against a live vision model. Both decline
  paths were, which is the part that used to break.
