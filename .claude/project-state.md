# Aura Project State

## Project
Aura AI assistant.

## Current Goal
Build and stabilize Aura as a local/cloud-capable AI assistant.

## Status
**JARVIS modernization phases 1-19 are IMPLEMENTED** (section 42 order;
checklist and verification log in `.claude/modernization-checklist.md`).
**Phase 19 - Vision - is IMPLEMENTED**, split per section 43 into 19.1 the
processor chain, 19.2 the on-demand tool, 19.3 verification. 19.1 was a
regression rather than a missing feature: turning `vision.capture_screen` on
made vision report *less*, because the pixel processor **replaced**
`WindowTitleProcessor` instead of layering over it and `VisionManager.refresh`
reads an empty description as "no observation". `ProcessorChain` layers them -
first non-empty answer wins, titles last as the floor - and advances on both
ways a processor declines, since `OllamaVisionProcessor` returns `""` for
every failure while `CloudVisionProcessor` raises. 19.2 added
`describe_screen`, the on-demand counterpart to the ambient line that has
ridden along with every prompt since phase 4: it calls `refresh()` rather than
`get_context()` because the throttle answers a question nobody asked, it
refuses when `vision.enabled` is off because `refresh()` does not consult that
flag and its absence here would mean looking at a screen the owner closed, and
its `verify` reads two new read-only manager properties rather than
`get_context()`, which re-observes and would have made verification pay for a
second capture and a second upload. Its risk is **per instance**: SENSITIVE
while pixels stay on the machine, DANGEROUS once a hosted model is in the
chain, read off a new `sends_pixels_offsite` flag and `any()`-ed across the
chain because the cloud link sits mid-order. Phase 18 was split per section
43:
**18.1 - PC observation and window management - is IMPLEMENTED**, giving
Aura four registered-but-not-enabled Windows tools whose only actuator,
`focus_window`, asks its postcondition separately because
`SetForegroundWindow` accepts-and-ignores under the foreground lock.
**18.2 - controlled command execution - is IMPLEMENTED**: one tool,
`run_command`, where the owner declares named argv lists and the model
supplies only a key and values for declared slots, one slot per argv element
and `shell=False`, so a value can never become a second command or even a
second argument. **18.3 - filesystem writes - is IMPLEMENTED**: four tools
gated on `tools.writable_paths`, a second root list that is deliberately
never defaulted from `tools.allowed_paths`, because reading a folder and
being allowed to overwrite it are two different grants and the owner must
not find the second one already made. **18.4 - screenshots - is
IMPLEMENTED**: `take_screenshot` writes a PNG into the same
`writable_paths` grant through 18.3's own containment and atomic write, and
it needed a new `GdiScreenCapture` backend because `mss` is optional and
absent here - which also fixed a latent defect where `capture_screen: true`
had been silently doing nothing on this machine. **18.5 - keyboard and
mouse input synthesis - is IMPLEMENTED**, which closes section 24: four
tools behind `SendInput` and `SetCursorPos`, all DANGEROUS, each carrying an
optional `window` guard that refuses - rather than skips - when it cannot
read which window is in front. The open question the brief raised there,
whether synthesised input needs a stricter gate than DANGEROUS, was
answered no and the reasoning written into `_InputTool`'s docstring: a
fourth risk level would have to be learned by the settings contract, the
Android DTO, `config.yaml` and the Hub before it protected anything, and it
would protect nothing the owner cannot already get by leaving `dangerous`
out of `tools.auto_approve`. Section 11 turned out to have better answers
than expected, because `SetCursorPos` was measured **reporting success for
a point it silently clamped** - (2420, 1580) returned true and landed at
(1919, 1079) - and `GetAsyncKeyState` was measured seeing a *synthesised*
modifier, so "the pointer is where it was aimed" and "no modifier this call
pressed is still held" are both genuine postconditions rather than the fake
verify the brief warned about.
Phase 1 gave Aura an owner-defined endpoint whose key and URL are
structurally inseparable; phase 2 gave it a task-class contract that does
not touch the `@runtime_checkable` `LLM` protocol; phase 3 gave it a
capability router that wraps `BrainRouter` only when the owner has
configured a lane; phase 4 gave it one place that knows what it is in the
middle of, so *have I already done this?* is a lookup rather than
something a model re-derives from a screenshot every tick; phase 5 gave it
one decomposition of a request, computed on the server and pure, so a plan
cannot depend on which model answered; phase 6 gave it a task graph whose
node states are *derived* from that state rather than stored, so asking
where a task stands cannot look like doing something; phase 7 gave it the
one thing all of that was missing - a way to learn that an action did not
work; phase 8 gave it a consequence for ignoring the register the prompt
asked for, which is the difference sections 13 and 14 both name between
instructing a personality and having one; phase 9 gave it a conversation
that outlives the process, both the bubbles the user can see and the
session id they cannot; and phase 10 closed the reach of a clock that
already existed - the CLI root now has one, so `python main.py` no longer
holds conversations whose prompt has no date in it, and the zone is
settable by the owner rather than merely reported to them. Backend
**2407 passed, 2 skipped, 1 deselected** (baseline 1879, +528, zero
regressions); Android **classes=26 tests=359 failures=0 errors=0** after
`cleanTestDebugUnitTest`, freshness proved by reading the JUnit XML
timestamps rather than trusting BUILD SUCCESSFUL, plus an `assembleDebug`
APK at `android/app/build/outputs/apk/debug/app-debug.apk` (19,427,232
bytes) with `TranscriptStore` confirmed inside its dex - unchanged since
phase 9, because phase 10 touched no Kotlin production code. Phase 11
(Memory 3.0) is done in three parts - a real migration for the pipeline
tables, a `remember` tool giving the semantic tier its first runtime
caller, and an alias table closing a section 10 repeat loop that lived in
`same_app` rather than in the verification layer. Phase 12 (Event Bus,
section 18 - not to be confused with the older Android settings phase 12
further down this file) is done in two parts: `events/log.py` gave the bus
its first subscriber at all, and three task events gave the whole cognitive
layer a voice it had never had. Phase 13 (Background Service, section 19)
is done, and it turned out not to be about keeping a process alive: the
device side already satisfied section 19, while every proactive limit the
owner had configured was derived from a RAM-only deque, so a restart
handed back a clean slate and "no more than four a day" was a number
nothing enforced. `proactive/ledger.py` now keeps that history on disk,
and deriving the greeting fact from it deleted a section 8 duplicate at
the same time. Phase 14 (Notifications, section 20) is done, and it was the same defect
one floor down plus a section 2 problem the phase 13 work had made
visible: `companion/policy.py` derived `cooldown_seconds`, `max_per_hour`,
`suppress_after_chat_seconds` and its duplicate check from a RAM-only
`deque(maxlen=32)`, so an hourly allowance came back on every restart -
and of the six knobs the sibling gate exposes, `server.companion.*`
exposed exactly one, `enabled`, with the duplicate window hard-coded as a
constant beside four real settings. It now reuses phase 13's `SendLedger`
but deliberately not its file (`data/companion.json`, category
`companion`), because one shared ledger would make each gate count the
other's sends and the owner configured two budgets; all seven paths are
settable and live-reapplied; and `_last_notified` was deleted rather than
persisted, since the history already holds it. Phase 15 (Proactive
Agent, section 21) is done, and it was two wired-green-and-dead defects
of the same family as phases 11 and 13 plus one accident written down:
`Category.APPRECIATION` had a decision branch, a 24h cooldown, two
composer templates and three passing tests and could not fire in any
real process, because `launcher/services.py` never passed the `memories`
source, so `engine.memories` was `None` and every appreciation was gated
behind an empty tuple - `proactive/memories.py` is that source, reading
the same episodic store the reminders do; and the closed `Category` set
that `decision.py` says "cannot be sent at all" was closed at the
decision layer and open at the policy layer, where a `.get()` lookup gave
an unlisted category no per-category cooldown at all (a probe sent five
distinct messages in five seconds through one), now refused in `allows()`
against a `KNOWN_CATEGORIES` set derived from the enum rather than a
second hand-kept list. The accident is section 21's own boundary: every
bus subscriber today renders, speaks, logs or queues and none acts, so no
event can cause an action - true by what happens to subscribe rather than
by any rule, and now a test that fails the moment a handler from outside
the presentation layer is attached. Phase 16 (Universal Tools, section
22) is done, and it was the section 11 sentence made true one floor below
the device: section 22's seven members turned out to be six already here
under other names, and of the two missing one was refused and one was
covering a live defect. `capabilities` was **not** added, because nothing
would read it and a field named for a measurement nobody took is worse
than an absent one. `verify()` was added as an optional, getattr-looked-up
postcondition the executor asks after a successful `_run` - never on the
three-member `ToolProtocol` - because `remember` was discarding
`remember_user_stated`'s return value and so reported `ok=True` and
"remembered X = Y" while writing **zero rows** whenever the key had no
ASCII alphanumerics (`normalise_key` strips it to an empty slug and
`_write` returns `None` silently); probed live, `key='名前'` stored nothing
and claimed success, on the machine of an owner who writes Vietnamese.
Phase 17 (Accessibility integration) is done, and it was two defects that
had exactly one thing in common: both were invisible because the test that
would have caught them never crossed the boundary the defect lived on.
`AppInfo.activity` was declared, serialized, and read by two server
modules - and never assigned; nine passing device tests were blind to it
because none of them touched the serializer, so the wire key could be
renamed at will. And the device's two stop-early heuristics classified
twelve concrete requests as single-step that the server's own planner
decomposes into two nodes, because "more than one thing" was tested only
as *a conjunction* and never as *two verbs* - `mở YouTube tìm nhạc` has no
conjunction at all. Both are fixed, both are mutation-verified, and the
outcome that matters more than either is the ownership boundary that got
named instead of merged: the **server** owns *is the goal met*, the
**device** owns only *may I stop without asking* - a latency optimisation
that is allowed to be wrong in exactly one direction, the one that costs a
round trip. Phases 18 (Windows / PC agent, section 24) and 19 (Vision) are
done; **phases 21-23 are now done as well** (plugins audited + composition-root
tests, plugin/tool diagnostics added to `/api/health`, and the settable-without-
widget list closed - see the checklist), **and phases 24-25 are done as well**
(a measured `load_config` merge cache, ~136× on the per-turn config path,
with invalidation pinned by tests; then a fresh repo-wide RC audit). Phase 20
was audited against section 20 and the remainder was **deferred by the owner**:
push-to-talk STT and event-driven TTS are implemented and wired; streaming
speech, an interruption trigger, continuous listening and the hosted-TTS
offsite disclosure are recorded debt in checklist row 20. There
is existing voice code in the repository -
`tests/test_voice_cancel.py` implies a provider with
cancellation - so the first move is mapping what is there against what
section 20 asks for, not a greenfield build. Phase 19 is the precedent: the
work turned out to be one regression plus one missing half. ~~Still owed: Hub
UI controls for the settings paths that are settable over PATCH with no
widget~~ Closed in phase 23: `vision.capture_screen`,
`vision.send_screen_to_cloud`, `vision.min_interval`, `temporal.timezone`,
the five `llm.task_models.*` lanes and `llm.custom_base_url`/`llm.custom_model`
now have Hub controls, joining the six `server.companion.*` knobs. Still owed:
the live Accessibility
scenarios, which have never been driven on hardware.

Phase 7's substance is worth stating precisely, because the obvious reading
of section 11 is wrong here. Verification was **not** missing: the device
already does the real per-kind check with bounded polls, and rebuilding it
server-side would have been a second verification system whose
disagreements with the first would be invisible. What was missing is that
the *result* never crossed the wire in a readable form - success was
reported, failure was not - so `fail_action`, `should_retry` and
`enter_recovery` had existed since phase 4 with no production caller, and
three of section 10's seven node states were unreachable. A sibling
`failed_actions` field in the device's own `ExecutionResult` vocabulary
closed it, and `brain/recovery.py` holds the bound (2, matching the floor
the service already enforces, pinned across languages). Two real defects
fell out: every launch keyed to the literal string `open_app:null`, so two
failed Chrome attempts refused YouTube's first, and the three producers
above went from dead to tested.

**Provider failure isolation** is the newest uncommitted work, and it fixes
the one production failure the modernization mandate names by name: an API
key entered for a proxy made Aura stop functioning. The cause was not a
provider bug but an asymmetry in `BrainRouter._create_provider` - every
fallback was built defensively, the primary was not, so a provider the
operator merely *selected* could kill every message while a provider they
configured as a *backup* could not. Both paths now go through
`BrainRouter._build`, which never raises; a dead primary degrades to the
fallback chain with an ERROR log and no change to `llm.provider`. Backend
**1817 passed, 1 skipped, 1 deselected** (five consecutive runs); Android
**312 tests, 0 failures** (forced re-run - the first invocation was an
UP-TO-DATE gradle task that re-ran nothing).

Persona is now both *instructed* and *enforced*. `brain/persona_validator.py`
inspects every conversational reply on its way out and holds it to the
register `resolve` settled on - the mandate's own example, "May thu lai di"
-> "Cau thu lai di", is a test. Enforcement is deliberately narrower than
the prompt: two of `RESTRAINT`'s sentences are left to the model on purpose,
with the reason recorded in the module docstring rather than omitted
silently.

Of the five absences confirmed when that audit was written, four are now
closed: the **planner** (`brain/planner.py`, phase 5), the **task graph**
(`brain/task_graph.py`, phase 6), **bounded retry / recovery**
(`brain/recovery.py`, phase 7) and the **persona output validator**
(`brain/persona_validator.py`, phase 8) exist, are tested, and reach the
paths that matter, as do Android **conversation persistence**
(`data/chat/TranscriptStore`, phase 9) and an owner-settable **timezone**
(`temporal.timezone`, phase 10). Still absent: AgentRouter - zero
repository matches, and blocked on the owner's base URL. See progress.md.

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

## Modernization architecture (standing, phases 1-6)

- **A key and an endpoint are one fact.** `brain/providers/custom.py`
  `CustomProvider` is the owner's own endpoint: OpenAI chat-completions
  dialect, `default_url = ""`, `default_model = ""`, and a new
  `HttpChatProvider.requires_base_url` flag that makes an absent URL a named
  precondition rather than something to fall back on. The key comes from
  `CUSTOM_API_KEY` and `self.url` is resolved in the same constructor.
  **Nothing in the request path consults the model name**, so a `claude-*`
  model name on an owner's gateway cannot redirect the request to Anthropic.
  That is a property of the file, not a rule to remember.
- **Endpoint precedence is fixed and one-directional:** non-blank
  `llm.<name>_base_url` > `<NAME>_BASE_URL` env var > `default_url`. Blank
  means "not configured here", never "configured to nothing", so saving an
  unrelated setting cannot un-configure an endpoint that came from the
  environment.
- **`OWNER_DEFINED_ENDPOINTS` is the third provider registry**, beside
  `PROVIDER_KEYS` and `HTTP_CHAT_PROVIDERS` in `brain/router.py`. A row there
  means the endpoint and model are the owner's to supply, so
  `_instantiate_provider` returns `None` when either is empty and
  `_skip_reason` names which setting it was. Adding another such provider is
  a registry row, not a code path.
- **A missing precondition returns `None`; a broken provider raises.** The
  distinction is load-bearing: `None` reaches the owner as
  `llm.custom_model is empty`, an exception reaches them as
  `initialization raised ValueError`. New provider work must keep using the
  `None` path for anything the owner can fix in settings.
- **Task classes are a separate protocol, never a method on
  `brain.ports.LLM`.** `brain/capabilities.py` holds `TaskClass`,
  `classify_task` and `CapabilityLLM` + `generate_for`. `brain.ports.LLM` is
  `@runtime_checkable`, so adding a method to it would falsify `isinstance`
  for every provider lacking it - the same hazard `StreamingLLM`/`can_stream`
  and `IdentityAnchor`/`anchor_of` already work around, resolved the same
  way.
- **Model routing wraps; it does not replace.** `brain/model_router.py`
  `CapabilityRouter` holds a lane per task class and is built at the
  composition root (`launcher/services.py`) **only when `llm.task_models`
  names a non-empty lane**, so the default install's object graph is
  unchanged. It exposes `provider_name` (readable and writable),
  `active_chain()` and an assignable `_provider`, because five modules
  duck-type that surface - `server/settings_service.py:_reapply_llm` in
  particular writes `provider_name` and clears `_provider`.
- **A dead lane degrades to the chat lane; the chat lane is the floor.**
  Lane configuration can never make Aura unable to answer.
- **The owner's configuration is never rewritten.** `llm.provider` keeps
  saying what the owner chose even when a fallback is what answers;
  `active_chain()` is where the truth about the live chain lives.

### Cognitive state (phase 4)

- **One object owns "what is Aura in the middle of".** `core/cognitive.py`.
  Before it, four things each knew part of the answer - the untyped tick
  `context` dict, `_Turn`, `ProactiveContext`, and the Android service's
  stack locals - and none could be asked the question that matters: *have I
  already done this?*
- **Mutable owner, frozen projections.** `CognitiveState` mutates;
  `snapshot()` yields a frozen `CognitiveSnapshot`. Same bargain
  `ProactiveContext` makes: a decision from a frozen value is reproducible.
- **Time is borrowed, never stored.** The state holds a `TemporalClock` and
  asks it. `launcher/services.py` asserts one clock per process; a second
  copy of "now" would eventually disagree with the first.
- **An action is `(kind, target)`.** That identity is what makes
  `has_succeeded("open_app", pkg)` a lookup instead of a model call.
  `attempts` counts beginnings, not failures, so a retry bound built on it
  is the size it looks.
- **Finished work repeats only when recovery names it.** `begin_action` on
  a succeeded record is a no-op unless `enter_recovery(kind, target)`
  named that exact action. Recovery is scoped to one action, never a mode:
  a global flag would let anything repeat while it was set.
- **State lives in a store keyed by session, never as a field on a shared
  service.** `ConversationManager`'s own comment: one engine serves every
  session, so per-turn state on it "is a race, not a cache". `CognitiveStore`
  is the safe form of the same idea, following `SessionManager`.
- **Touch before sweep.** An entry is touched, then the sweep runs, so a
  session in use can never expire under its own reader - and wall-clock
  steps (DST, NTP) cannot reap a live task.
- **The ingest reads only the format the device emits.** `absorb` parses
  `kind(args) [VERIFIED]` because that is what `formatActionHistory` writes
  and `AccessibilityAgentTest` pins. `last_action_error` is free prose in
  five shapes with no recoverable target, so it is not parsed at all.
- **Bookkeeping never fails the turn.** `_absorb` guards, catches, logs at
  debug - the shape of `_emit` and `_vision_context`. An agent that stops
  mid-task because its notebook tore is worse than one working from a stale
  note.

### Plan and task graph (phases 5-6)

- **A request is decomposed once, on the server.** `brain/planner.py`
  `plan_for` turns a request into an ordered `Plan` of `PlanStep`s. Pure - no
  clock, no configuration, no model. §7 requires that swapping models leaves
  behaviour unchanged, so a plan must not depend on which model answered; that
  purity is also what makes it safe to recompute every tick instead of
  serialising it, since two calls cannot disagree.
- **The plan holds no progress.** Position comes from `CognitiveState`, the one
  place an action's outcome is recorded. A step counter in the plan would be a
  second record of the same fact and the two would part company the first time
  an action was retried.
- **Node state is derived, never stored.** `brain/task_graph.py`
  `build(plan, state)` assigns each step one of §10's seven states by reading
  the cognitive state; nothing is written back. `set_plan` still stores a flat
  `tuple[str, ...]` and `enter_node` a single string, and neither had to grow a
  per-node field. Reads must stay reads: asking where a task stands must not
  look like doing something, or asking twice would spend a retry.
- **Precedence between the seven: RECOVERING > SUCCESS > SKIPPED > RUNNING >
  FAILED > BLOCKED > PENDING.** RECOVERING beats SUCCESS because that *is*
  §10's exception - if SUCCESS won, `enter_recovery` could never reopen a
  completed node. SUCCESS beats SKIPPED because after a real launch both facts
  hold and reporting SKIPPED would deny we did it.
- **SKIPPED means "the postcondition already holds", and only a launch may
  claim it.** `absorb` records the foreground package every tick, so that one
  claim has evidence. A focused field or rendered results would have to be read
  off `focus.screen`, which is permanently `""` because the device never fills
  in `AppInfo.activity`. Claiming a step was skipped on evidence that does not
  exist would advance a plan past work nobody did.
- **BLOCKED is about a node's successors, never itself.** An exhausted node is
  FAILED; the ones after it are BLOCKED. `current` returns None for a blocked
  plan, and `is_finished` / `is_stuck` exist so "nothing to do" can be told
  apart from "done" - a distinction a null current node cannot carry.
- **One retry accounting.** FAILED asks `may_retry` (phase 7) rather than
  comparing a bound of its own, and `TaskNode.attempts` is copied off the
  action record rather than counted. Python's `(kind, target)` key and the
  device's key used to disagree - the device read `action.nodeId` for every
  kind, so every launch keyed to `open_app:null`. Phase 7 made both sides use
  the same target, and `test_agent_protocol.py` pins the bound across the
  language boundary.
- **No edge list.** `plan_for` produces a chain, so "an earlier node" means the
  nodes before this one. A `depends_on` field always equal to `(index - 1,)`
  would be structure pretending to carry information. When plans branch, that
  field is where the dependency goes and `_blocking` is the only function that
  changes.
- **One implementation of "which step are we on".** Planner owns request
  parsing and step description; task_graph owns state, position and marking.
  `current_step` and `render_plan` live only in task_graph, and the dependency
  runs one way (`task_graph` imports `planner`). Two renderers would be free to
  disagree, which is how the `submit` verb drift happened.
- **An unrecognised request gets an empty plan.** No PLAN section is rendered,
  so the tick is byte for byte what it was before any of this existed and an
  unparsed request cannot be a regression. An invented plan would be worse than
  none: it would name an app the owner never mentioned.
- **The device still owns completion.** `shouldAutoComplete`,
  `isSearchTaskComplete` and `isSelectionTaskComplete` decide when the agent
  loop stops. The graph's `is_finished` / `is_stuck` are the server-side facts
  that phase 17's reconciliation will be built on; they do not feed the device
  yet.

### Verification and recovery (phase 7)

- **The device verifies; the server learns the verdict.** There is one
  verification system, and it runs on the phone (`waitForForegroundPackage` for
  a launch, `waitForContentChange` for a submit, a fingerprint comparison
  otherwise). The server does not re-check what the device checked - a second
  implementation would disagree with the first invisibly. What the server owns
  is *policy*: how many attempts, and what to do when they are spent.
- **Failure crosses the wire in the same shape success does.**
  `AccessibilitySnapshot.failedActions` carries `kind(args) [VERDICT xN]`
  beside `completed_actions`, and both formatters share `actionSignature` so
  the two channels cannot drift into different notions of what identifies an
  action. Defaulted to empty, so a build predating the field still
  deserialises and silence reads as "nothing reported" rather than as an error.
  `last_action_error` is deliberately *not* parsed: five free-prose shapes,
  none naming its action, and deriving a target from it would be inventing a
  format rather than reading one.
- **The verdict vocabulary is the device's own.** FAILED (the gesture could not
  be performed) and UNVERIFIED (it was performed, the postcondition was not
  observed) are `ExecutionResult` names, not a taxonomy invented for the wire.
  §11 turns on exactly that distinction: a click that landed on nothing needs a
  different target, a submit whose results never rendered may only need another
  look.
- **One bound, and it is not above what the device will perform.** The service
  refuses to execute past `MAX_ACTION_ATTEMPTS`, so that number *acts*; a
  server limit above it would be permission the phone declines to honour.
  `DEFAULT_RETRY_LIMIT` matches it, `RETRY_LIMITS` is empty until an action
  exists whose per-kind number can be justified against something, and
  `limit_for` is total and never zero - unknown-means-unlimited is §12's
  forever-repeat, and zero would be a block wearing a retry count.
- **Absorption is idempotent.** Every tick re-sends the whole list, so counts
  are brought *up to* what was reported rather than added to. Successes are
  read before failures, because a tick in flight can still carry a failure line
  the device has since cleared.
- **Recovery opens only when the plan is stuck.** One foreground package cannot
  distinguish an app that was killed from one behind a permission dialog, a
  share sheet or a sub-activity, so an ungated reconciler relaunches healthy
  apps every tick - manufacturing the `open_app open_app open_app` loop §10
  exists to prevent. Stuckness makes the reading unambiguous: every remaining
  step has spent its bound against an app that is not there. Closing is
  ungated, because it must fire on the tick the app returns, and a plan in
  recovery is never stuck by construction.
- **Recovery is itself bounded.** RECOVERING outranks FAILED in the
  projection, so unbounded recovery would keep a node workable forever while
  an app was repeatedly killed. `reconcile` will not reopen an action already
  at its bound - checked with `attempts_for` rather than `may_retry`, because
  `may_retry` refuses succeeded actions and the action being recovered is
  succeeded by definition.

### Persona enforcement (phase 8)
- **`brain/persona_validator.py` is the only layer allowed to substitute a
  word.** `brain/style.py` states "never substitutes, never reorders, never
  paraphrases" as absolute, and that rule stands for what it does. A pronoun
  is a different kind of object: a closed vocabulary of eight words, swapped
  for another member of the same set, chosen by `resolve` rather than by the
  validator. Facts are untouchable in both layers.
- **Scope is exactly the prompt's own promises.** Four coarse words
  (`may`/`tao`/`ong`/`ba`) become the register's address or self term; a
  forbidden word is dropped; an emoji run keeps its first; an address term
  in *every* sentence keeps its first. Each maps to a sentence in
  `_pronoun_line` or `RESTRAINT`. Enforcing a rule the prompt never stated
  would correct a model for obeying its instructions.
- **The register comes from `resolve`, never from a constant.** The target
  words are `state.address_word` and `state.self_word`, so an owner writing
  in tui/bro gets "Bro thu lai di" and not "Cau thu lai di". A hard-coded
  pair here would be section 2's complaint from the inside.
- **The owner outranks the validator.** `AddressPreference.preferred` is
  checked before any correction, so an owner who asked to be called "may" is
  called "may". A *forbidden* word is dropped rather than swapped - the same
  bargain `PersonaState.address_word` already documents.
- **Code and quoted text are protected by construction, not by care.** Code
  spans are hidden with `brain.style.hide_code` - promoted from private for
  this, so there is one answer to "what counts as code" - and quotes with a
  local mirror of the same mechanism. `tao_count` and a quoted "tao xong
  roi" both survive intact.
- **Three linguistic guards, biased towards under-correcting.** "ong"/"ba"
  have ordinary meanings ("ong ay" = he, "ong noi" = grandfather) so they
  are corrected only in vocative position; "minh" is left alone after
  "cua"/"tu"/"chung"; diacritics do the rest for free, since "tao" (create)
  and "may" (lucky) are simply different strings. Guessing wrong the other
  way would cost the meaning of the sentence.
- **The machine-turn exemption is structural.** A machine turn builds no
  `_Turn`, so its persona is None and `validate` returns its JSON verbatim.
  No flag to thread, and no way for a later caller to accidentally rewrite
  the field names in an action the service then fails to parse.
- **Validation runs after styling, at both exits.** Style is subtractive
  over a whole reply and can delete a clause, which changes what "the
  address term in every sentence" means - so validating last makes the text
  the user reads the text that was checked. `_voiced` swallows its own
  exceptions for the same reason `_styled` does.
- **Two of `RESTRAINT`'s sentences are deliberately unenforced.** "Never
  open with the same phrase twice in a row" has no safe subtractive fix -
  deleting a stance-carrying opener ("Khong -") inverts an answer, and for
  the stance-free openers `style.py` already strips them unconditionally.
  "Never reach for a trend word" cannot be judged without the sentence
  around it. Both are recorded in the module docstring.

### Conversation persistence (phase 9)
- **The transcript lives on the device, not on the server.** There is no
  history route on the client, and even built, a launch-time fetch shows an
  empty screen when the phone is offline - which fails section 15 exactly
  where losing history hurts. The requirement is that history survive *the
  application closing*, not the server restarting.
- **One store, two narrow interfaces.** `Transcript` (read/write/clear) for
  the ViewModel, `SessionStore` (id) for the repository, both implemented by
  `data/chat/TranscriptStore`. The shape is copied from
  `SettingsProvider`/`DeviceSettings`/`SettingsStore` for the same reason:
  the real store needs a `Context` and a Keystore key, so a JVM test dies in
  its constructor. Each interface has a `None` no-op object, and both are
  constructor defaults - which is why every pre-existing call site and test
  compiled untouched.
- **Both halves of a conversation are restored by the same object.** The
  bubbles and `AuraRepository._sessionId` were both process-lifetime before
  this. Restoring only the bubbles would show a transcript beside a server
  session that had never heard of it, which is worse than a blank screen
  (section 38). One object cannot let them drift.
- **`TranscriptCodec` is pure so the fragile part is testable.** No Android
  type, so encode/decode/bound run on the JVM. Built as a JSON *tree* rather
  than `@Serializable`: one unreadable row is dropped without taking the
  rest, and the absent `streaming` column is visible in one place.
- **`decode` never throws.** It runs during `init`, before the chat screen
  draws, so an exception is not a lost transcript - it is an app that cannot
  open. Corrupt JSON, a non-object root, a non-array `messages`, a bad row
  and an unknown author all degrade. An unknown author is dropped rather
  than guessed: rendering it as AURA would show the user something Aura
  never said.
- **Three guarantees are structural.** `StoredMessage` has no `streaming`
  column, so a half-arrived reply cannot come back looking live (asserted
  against the encoded text, not the round trip). `keep()` returns early on an
  empty projection, so emptiness is only ever written by
  `newConversation()` - every other route to an empty screen is a failure,
  and writing it back would destroy the transcript rather than fail to load
  it (section 41). And `adopt()` is the sole writer of the session id, where
  `send()` used to set it directly; consequence worth knowing, a blank id
  from the server is no longer adopted.
- **Its own preferences file.** `aura_transcript`, not the settings file,
  because `EncryptedSharedPreferences` rewrites the whole value per commit
  and per-turn transcript churn must not keep re-encrypting the URL and the
  API token. Same `MasterKey` and schemes as `SettingsStore`; the transcript
  is the most personal thing the app stores and the key already existed.
- **Four catches, each with a stated cost.** A nullable `prefs` (an
  invalidated Keystore must not stop the app starting), `restored()` (see
  above), `keep()` (a failed write leaves `kept` alone so the next change
  retries), `AuraRepository.remember()` (losing continuity at the next
  launch beats losing the message being sent now). All logging is the
  exception's class name only - no contents, no key material (section 30).
- **Streamed replies are written once, not per token.** `keep()` collects a
  projection that filters out `streaming` messages, so the projection is
  constant for the whole of an arriving reply and `distinctUntilChanged`
  emits when it settles.
- **Clearing the session does not clear the transcript.**
  `SettingsViewModel.save()` and `disconnect()` both `resetSession()`; the
  transcript stays, because it is what the user said and tidying an
  inconsistency they can see for themselves is not worth destroying their
  data (section 41). `Transcript.clear()` clears messages only - forgetting
  the session is `remember(null)`, owned by the repository.
- **Recorded loss: `MAX_MESSAGES` = 200.** Past that the oldest messages
  stop surviving a restart. The bound is applied by the codec, not the
  caller, so no call site can forget it.
- **`kept` must be declared above `init`.** On `Dispatchers.Unconfined` a
  `launch` body runs synchronously, so a `kept` declared after `keep()` is
  read before its initialiser runs and then clobbered by it. Pinned by the
  test `a launch does not write back what it just read`.

### The semantic tier learns through a tool, not a scraper (phase 11)
- **`remember` is the only runtime door into `UserModel`.** SAFE, so it
  runs under `auto_approve: [safe]` with no human to ask - which is what
  keeps it usable in server mode. Registered only when a pipeline exists,
  the same dependency gating the filesystem tools use for `allowed_paths`.
- **`timeout = 0` is required, not tuning.** The executor bounds tools on
  a daemon thread, and a SQLAlchemy SQLite session belongs to the thread
  that opened it, so a threaded `remember` raises `ProgrammingError` every
  time - in production as in tests. `tools/timeout.py` documents inline
  execution as the escape hatch for thread-affine state. Any future tool
  that touches the database needs the same line.
- **Extraction over every message was rejected on the repository's own
  terms.** `memory/user_model.py` says CONFIRMED means the user actually
  said it and `confirm()` is the only door; a background scraper guessing
  intent would fill the confirmed tier with inferences. `infer()` remains
  the honest door for anything Aura works out.
- **Categories are validated at the tool boundary.** The constants'
  "a typo is an ImportError" promise covers importing callers only, and a
  model choosing an argument imports nothing. `CATEGORIES` re-establishes
  it where untrusted text becomes a row.
- **Test the composition root, not only the component.** Dropping the
  pipeline argument from the single `_build_tools` call left all 2263
  tests green while making the tool absent from every real catalogue.
  `test_the_composition_root_hands_the_pipeline_to_the_tools` drives the
  real `build_services` for exactly that reason.

### A test that supplies the state production cannot reach (phase 15)

- `Category.APPRECIATION` had a decision branch, a 24-hour cooldown, two
  composer templates and **three passing tests**, and could not fire in a
  real process. Every one of those tests built `ProactiveContext` by hand
  with `relevant_memories=("...",)` - the single field production left
  empty, because `launcher/services.py` never passed `memories=` and
  `_gather_memories()` therefore returned `()`.
- This is a distinct defect shape from the two above it. "Wired, green and
  dead" is a test reading a *copied default* instead of the wired object.
  "A path in `LIVE_PATHS`" is a test asserting a *declaration* instead of
  the behaviour. Here the tests exercised the behaviour correctly and
  fully - they just constructed an input the composition root has no path
  to producing. The feature worked. Nothing could call it.
- Standing rule: **when a test hand-builds the input object, ask which
  field production fills and which it leaves empty.** A field that only
  ever has a value inside a test is not exercised code, it is a fixture
  describing a system that does not exist. The cheap check is to build the
  thing the way the composition root builds it and print the field.
- The corollary is where the phase's second defect came from. Whatever
  populates that field in production is a *seam*, and a seam nobody has
  driven has no tests on its own contract either. `ProactivePolicy` took a
  plain `str` category and looked its cooldown up with `.get()`, so an
  unlisted string arrived with **no per-category throttle at all** - five
  distinct messages in five seconds, against `"task"`'s one - while
  `proactive/decision.py` stated in prose that "a category that is not
  listed here cannot be sent at all". True of the decision engine, false
  where sending is decided. `KNOWN_CATEGORIES`, derived from the enum
  rather than retyped, closes it.
- Seven tests had been using the category `"check_in"`, which is not in
  `Category` and never was. They were not merely sloppy: an unlisted
  category had no per-category cooldown, which left whichever rule each
  test was about as the only rule standing. **The tests were leaning on
  the defect, which is why it survived.** Closing the set broke them, and
  the repair is the general one - name a real category and stand the other
  rules down explicitly (`category_cooldown_seconds={CATEGORY: 0}`), so
  the rule under test is the only one that can answer. Several of them
  discard the reason string, and a test asserting "not allowed" while a
  different rule does the refusing passes for the wrong reason.
- Third finding, and the one that is not a bug: **nothing on the event bus
  acts on an event.** Every subscriber at a fully built root renders,
  speaks, logs or queues. That was true by accident of what happens to
  subscribe, not by construction - `subscribe` takes any callable and
  `publish` swallows handler exceptions - so section 21's boundary is now a
  test that walks both `_wildcard` and `_handlers` and fails on any owner
  module outside a declared presentation set. Verified to fire against a
  synthesised acting handler. A property worth relying on is worth a test
  even when it currently holds.

### A limit that dies with the process is not a limit (phase 13)

- Five owner-configured proactive limits - `max_per_day`,
  `cooldown_seconds`, `category_cooldown_seconds`, and
  `duplicate_window_seconds` with `similarity_threshold` - were every one
  of them derived from a single RAM-only `deque(maxlen=64)`.
- Each of them read correctly, was validated on write, appeared in the
  settings contract, and enforced nothing across the only event that
  matters to a daily ceiling. "No more than four a day" was a number in a
  file.
- The generalisation is not "persist more state". It is that **durability
  is a property the guarantee demands of whichever layer is asked to keep
  it**, so the question to ask of any limit is "over what interval, and
  does the thing holding the count live that long". A per-request limit may
  be RAM. A per-day limit may not.
- Two corollaries, both paid for in this phase:
  - A durable limit must not become a permanent ban. The ledger persists
    history, not verdicts; `sent_today` is recomputed from timestamps, so
    tomorrow is allowed. This is section 2 - Aura may warn, not lock the
    owner out.
  - Making one half durable can expose the other half. Once greetings
    survived a restart, `_last_user_message_at` did not, and
    `seconds_since_user()` reads a missing value as infinity, which the
    greeting rule reads as "away" - so a restart mid-conversation greeted a
    present owner. Deriving presence from the `messages` table fixed it.
    Expect the symmetric defect and go looking for it.
- Not everything volatile needs fixing. `ProactiveEngine._rotation` resets
  to zero and re-offers identical text, and the now-durable duplicate
  window refuses it. Checked by probe and left alone with the evidence
  written down, which is a different act from not noticing.

### A path in `LIVE_PATHS` and a handler that reapplies it (phase 14)

- Two mutants survived round one of the phase 14 mutation pass, both in the
  live-reapply path, and both survived for the same structural reason: the
  tests asserted **membership in `COMPANION_LIVE_PATHS`** rather than
  driving a real PATCH and reading the running policy afterwards.
- A path can be listed as live-reappliable while the handler meant to
  reapply it is broken, and a membership assertion passes every time. The
  list is a *declaration*; the handler is the *behaviour*. Asserting the
  declaration tests the constant, not the feature.
- Standing rule: **a path in `LIVE_PATHS` and a handler that reapplies it
  are two different facts.** Any settings path claimed as live needs one
  test that PATCHes it against a running object and then reads that
  object - not the disk, and not the list.
- This is the same defect shape as "wired, green, and dead" one section
  above, arriving through a different door. There the wiring was absent and
  the tests used the copied default; here the wiring is present and the
  tests never exercised it. Both are section 44: the artefact existing is
  not the behaviour working.
- The three tests that closed it are in
  `tests/test_companion.py::TestTuningTheGateReachesTheRunningGate`, and
  the third is the one worth copying - with no gate running it asserts the
  reply says `restart_required`, because a handler that reports `applied`
  when there was nothing to apply is the failure mode a membership
  assertion is least able to see.
- Cost: two mutation rounds and eight tests. Round one 19/28, round two
  28/28 with 0 survivors.

### Wired, green, and dead (phase 13)

- `MemoryManager.last_said_at` was written with `session_id: str = "default"`
  because `get_recent` directly above it does that. The Android client
  supplies its own session id (`server/session.py:66-83` says so in as many
  words), so the method was blind to every message the owner had ever sent
  from their phone - the primary client.
- Every test passed. The tests used the default session, because the
  default was the thing being copied.
- What found it was a probe against a realistic input, not a test:
  `last_said_at()` -> `None` beside
  `last_said_at(session_id="android-9f2c41")` -> a real datetime, on the
  same database. Two lines.
- Standing rule: **a default that matches the file above it can still
  exclude the primary client.** When a new query goes in, run it once
  against the input production will actually hand it before believing the
  suite. Section 44's "never classify audited as implemented" has this
  quieter form - wired is not implemented either.
- The consistency argument is what made it plausible. Matching the
  neighbouring method is usually right, and here the neighbour's scope was
  narrower than the new question's. Copy the idiom, re-derive the default.

### An empty value that means two things (phase 12)

- `task_node == ""` meant both "no task started" and "task finished". Both
  are real states, both are reached constantly, and the sentinel could not
  tell them apart.
- The consequence was invisible in the common case and wrong in the
  uncommon one: a device reporting a task **already complete on its first
  tick** had its completion silently dropped, because the edge detector saw
  `"" -> ""` and concluded nothing had moved.
- The fix is a second reading rather than a special case. `had_plan`, taken
  *before* `set_plan` writes it, distinguishes arriving at "no current
  node" from sitting there. Both reads now happen before both writes.
- Generalisation worth keeping: when a sentinel is doing two jobs, add the
  missing distinction, not a branch for the case you happened to notice.
- The same shape already exists elsewhere in this file and was handled
  correctly: `TaskGraph.current` returns `None` for two reasons and the
  docstring says so, which is exactly why `is_finished` and `is_stuck` are
  separate properties. Phase 12's bug was failing to apply a lesson the
  graph had already learned.

### An equivalent mutation still has something to say (phase 12)

- `elif graph.is_stuck:` mutated to `elif graph.is_stuck or True:` left the
  suite green, and that is the correct result: `is_stuck` is defined as "no
  current node and not finished", and `_plan` returns before `_announce` on
  an empty plan, so the disjunct cannot change an answer where it is
  evaluated.
- The mutation was equivalent. The **branch underneath it had no test at
  all**, and only asking why the mutation survived revealed that.
- A surviving mutation therefore has three possible readings, not two:
  a missing test, dead code, or an equivalence hiding a missing test.
  Phase 12 hit all three - an `index=0` survivor was a missing assertion,
  a `SKIP_FIELDS` survivor was dead code now deleted, and this one was the
  third kind.

### A narrow match has two failure directions (phase 11)
- **`same_app` could not recognise its own successes.** It matches a
  squashed display name against a package, and seven of ten common apps
  missed: Messenger (`com.facebook.orca`), X (`com.twitter.android`),
  Gmail, Play Store, Phone, TikTok, Messages. A miss makes `is_done`
  False for a launch that happened, the tick re-issues OPEN_APP, and the
  device opens a foregrounded app forever - the section 10 loop reached
  from the recognition side, not the verification side.
- **`APP_ALIASES` holds exact packages keyed on whole names.** A substring
  reading of `"x"` matches nearly every package on a device, and a loose
  `messenger` entry would admit `com.facebook.katana`. The asymmetry
  decides the design: a false positive advances a plan past a step that
  never happened, while a missing entry falls back to the heuristic and is
  no worse off than before. The lookup adds readings without removing any.
- **One free function, six call sites.** `same_app` is called from the
  planner, the recovery engine and the task graph, so recognition is fixed
  in all three at once. Tests go through `is_done`, which is what a tick
  actually asks.
- **Test both directions of any predicate that gates "done".** A too-loose
  match is the failure everyone tests for. The miss surfaces as an
  infinite loop in a different module while the function looks correct in
  isolation.
- **The learned procedural tier is Not Implemented, deliberately.** Plan
  caching buys nothing - `plan_for` is deterministic and its signature is
  pinned to `["request"]`. A module-global alias resolver contradicts the
  composition-root idiom (`set_tool_confirmation` is a method on the
  runtime). The honest version needs the device to report which package it
  launched, which is a contract change requiring section 35 hardware.

### Memory recall is one control over two mechanisms (phase 11)
- **`memory.recall` gates both retrievers, and did neither before phase
  11.** The Memory 2.0 ranked episodic search via
  `MemoryPipeline.recall_enabled`, read by `memory_lines`; and the Sprint 5
  keyword transcript search via `Services.knowledge.retriever`, swapped
  between `KeywordRetriever` and `NullRetriever`. Both are read per turn,
  so both are live; the handler returns False and reports
  `restart_required` only when neither subsystem is in this process.
- **`Services.pipeline` is a sibling of `Services.memory`, not a child.**
  `services.memory` is the `MemoryManager`. Anything reaching a pipeline
  through the memory manager gets `None` - that was the shape of one of
  the four breaks, and the `build_service` test fixture had copied it.
- **The Hub's wording is the contract for what `memory.recall` means.**
  "Use memory in replies / look things up from past conversations", listed
  under privacy. `config.yaml`'s older comment scoped it to keyword search
  only; where the two disagree the settings screen wins (§2), and a
  privacy reading beats a capability reading because the cost of being
  wrong is past conversation content in a prompt the owner refused.
- **Consequence still live for the owner:** the shipped default is
  `recall: false`, so episodic recall is now off where it had been
  running against configuration. One toggle restores it.
- **`apply` has two handler protocols and the choice is not cosmetic.**
  An unconditional handler returning `None` leaves its paths in `applied`
  whatever happened; a conditional handler returning `bool` gets its paths
  demoted to `restart_required` on a False. Any setting whose target is
  optional belongs in the second group, or the report lies.

### Time awareness (phase 10)
- **There is exactly one clock per process, and it is shared by object
  identity.** `launcher/services.py` builds one `TemporalClock` and hands
  *that object* to the prompt builder, the memory pipeline, the ranked
  retriever, the quiet-hours check and the proactive engine, so the time in
  the prompt and the time on a stored memory cannot disagree. Anything that
  changes the clock must therefore mutate it, not replace it - a replacement
  moves whichever subsystem receives it and leaves the rest behind.
- **`TemporalClock.use_timezone(name)` mutates in place and returns whether
  it moved.** The default `_now` closure reads `self.timezone` when it is
  *called*, which is why even `RankedRetriever` - which captured the bound
  `clock.now` method at construction - follows a zone change.
- **The constructor degrades, the setter refuses.** An unresolvable name at
  construction falls back to system local and logs; the same name through
  `use_timezone` changes nothing and returns False. Not an inconsistency:
  the constructor has nothing better to keep and refusing there would mean
  no Aura over a typo, while a running clock has the zone already in effect
  and dropping it would punish a typo by moving Aura's clock.
- **An injected `now` outranks the zone.** `use_timezone` leaves it alone -
  it belongs to whoever injected it - but still moves the label, because the
  label is what the prompt prints.
- **`UTC_ALIASES` and `canonical_timezone_name` are the single vocabulary**
  for the three spellings that resolve without a timezone database.
  `core/settings_store.py` imports them rather than keeping a copy, and the
  canonicaliser folds whitespace and those aliases *only* - IANA keys are
  case-sensitive to `ZoneInfo`, so the lowercasing every other name-ish
  setting applies would break every real zone.
- **`temporal.timezone` is owner-settable and live.** `_timezone_name` in
  `core/settings_store.py` (`ALLOWED` is now 50) refuses a name this host
  cannot resolve rather than storing it, because a stored-but-unusable zone
  is the dead setting `validate_path` refuses by name; the error names
  `tzdata`, since on Windows the cause is usually a missing database and not
  a spelling mistake. `server/settings_service._reapply_temporal` applies it
  to the shared clock and reports `restart_required` only when this process
  has no clock.
- **The machine-turn prompt carries the time; it carries nothing else
  conversational.** An agent tick and an intent probe strip history,
  recalled memory, vision, the identity anchor and the style hint. CURRENT
  TIME is the one exception, and the rule the others obey is what admits
  it: they are withheld for existing to make Aura sound like herself, and
  the time is a fact about the present - the same category as DEVICE STATE,
  which the tick has always carried. It matters because the owner's request
  reaches the model verbatim and `input_text` types free text, so "hom nay"
  with no date in the prompt is an invented date (§16). MEMORY stays out.
  `_temporal_lines()` returns `[]` with no clock, so a clockless deployment
  gets a byte-identical tick prompt.

- **The composition roots both build a clock, and `ChatEngine` still does
  not.** `clock=None` on the engine is deliberate - a bare `ChatEngine()` is
  byte-for-byte the Sprint 4 prompt pipeline that many tests depend on - so
  the clock arrives from `launcher/services.py` or from `core/app.py`. Any
  third entry point must remember to build one.
- **Timestamps are naive local.** Changing zone therefore re-dates existing
  memories by the offset delta: a row written at 14:00 in one zone reads as
  14:00 in the next. A property of naive storage, identical whether the
  change lands live or at restart.
- **Host constraint:** this Windows box has no tzdata, so only `UTC`/`GMT`/
  `Z` resolve and the owner can set nothing else here until
  `pip install tzdata`. Tests must never pin a specific non-UTC zone's
  accept/refuse outcome - `UTC` to accept, a name no database contains to
  refuse.

## Phase 19 architecture (standing, 19.1 through 19.3)

### A fallback chain, not a substitution (19.1)

`vision/processor.py` `ProcessorChain` asks each processor in turn and
takes the first non-empty answer. `WindowTitleProcessor` is always last,
so it is the floor rather than an alternative. The design constraint that
is easy to get wrong: **the bundled processors decline in two different
ways.** `OllamaVisionProcessor.describe` returns `""` for every failure it
has - dead daemon, HTTP error, model not pulled, unencodable frame -
while `CloudVisionProcessor.describe` *raises* `ProviderUnavailableError`
once it has actually tried its providers (a None or empty frame still
returns `""` without raising). A chain that advanced on only one of those
would fall through for one backend and go silent for the other, which is
the exact bug 19.1 exists to fix, reintroduced a layer up. Both are
exercised against the real classes.

Deliberately **not** `FallbackProvider`. That class is the same shape for
text providers, and reusing it would make `vision/` import
`brain/providers/` - the one dependency edge this package's docstring says
does not exist - to gain a five-line loop that advances on the wrong
condition. Deliberately **not** a concatenation: the pixel model can read
the window title out of the pixels, so two descriptions in the prompt
would pay tokens to say the same thing twice.

Failures are warned about **once per position**, then logged at debug.
The manager re-observes every `min_interval` seconds, so a backend that is
down stays down, and a warning per attempt would bury the log. Same
`_warned` idiom `GdiScreenCapture` already uses. An *empty* return is
never warned about at all: it is the protocol's documented "nothing worth
saying", and the layer that knows why already logged it.

### Risk that follows the data, not the verb (19.2)

`describe_screen`'s risk is decided **per instance**, in `__init__`, from
the processor it was built around: SENSITIVE while the pixels stay on the
machine, DANGEROUS once anything in the chain can hand them to a third
party. Reading the owner's screen and sending a picture of it to a hosted
model are not the same act, and section 30 does not let the second ride on
the first's permission.

The mechanism is a `sends_pixels_offsite` class attribute -
`False` on `WindowTitleProcessor`, `MockVisionProcessor` and
`OllamaVisionProcessor` (loopback, or a host the owner typed, is the
owner's own daemon), `True` on `CloudVisionProcessor`, and a property on
`ProcessorChain` that `any()`s across its links. `any()` rather than the
first link's answer, because the shipped order is
`[Ollama, Cloud, WindowTitle]` and the leaky one sits in the *middle*.

Read everywhere through `getattr(x, "sends_pixels_offsite", False)`, so
it is a fact a processor may advertise rather than a member the
`VisionProcessor` protocol demands - a processor written before the flag
existed still works and is taken at its quieter word. `ToolRegistry` reads
`tool.risk` off the instance and the approval gate reads it again per
call, so the per-instance value is the one that decides anything.
Over-reporting is the safe direction: a chain whose cloud link has no
usable key still says DANGEROUS, which costs one confirmation prompt,
where the mistake the other way costs an upload nobody approved.

**Generalises past vision.** Any tool whose risk depends on where data
*goes* rather than what it touches should put the answer on the component
that knows, and read it per instance. Audio in phase 20 is the same
question.

### A method that ignores the owner's switch, and what that costs a caller

`VisionManager.refresh()` does **not** consult `self.enabled`; only
`get_context()` does. Anything calling `refresh()` directly therefore has
to make the owner's check itself, or it will look at a screen the owner
switched off - silently, with nothing downstream able to tell. Section 2
with pixels attached.

`describe_screen` makes that check twice: `tools/factory.py` registers the
tool only while `vision.is_available()`, and `execute` refuses if vision
was switched off since. The gate on availability rather than on the
manager merely existing is the load-bearing half - a manager is always
built.

### Verification that does not pay for what it verifies (19.2)

`get_context()` re-observes once its throttle expires, and with
`min_interval: 0` on **every** call. A `verify()` written against it would
capture the screen twice per tool call and, with a hosted model in the
chain, upload it twice. Two read-only properties exist for this:
`VisionManager.last_observation` (what is held, no observing) and
`.seconds_since_observation` (age, or None).

The age is measured from when observing **started** - `refresh` stamps
`_last_seen` before calling `_observe()` - so a vision model that takes
twenty seconds leaves a twenty-second-old observation and has done nothing
wrong. `STALE_AFTER = 60.0` is generous for that reason: it is not a
latency policy, it catches a description served out of a cache by
something that never looked. "I looked and saw X" and "I did not look and
X is what I remember" are the same string, and freshness is the only
reading that separates them.

`verify` deliberately does not compare against what `execute` returned.
The manager is shared with ambient vision, so an observation landing
between the two calls would change the held description for a perfectly
good reason and fail a call that did nothing wrong.

### The whole prompt sees one manager

`launcher/services.py` builds the vision manager once and passes it into
`_build_tools`, after the pipeline and for the same reason. Two managers
would mean two capture backends, two processor chains and two answers to
"what is on screen" - one feeding the prompt, one feeding the tool. The
factory's `vision` parameter is the manager this process already has, not
one made there.

## Phase 18 architecture (standing, 18.1 through 18.5)

### A safeguard that cannot be checked is refused, not skipped (18.5)

The `window` argument on `click_mouse`, `type_text` and `press_keys` means
*only act if this window is in front*. It exists because a plan two steps
long - focus the editor, then type - has a gap the tool cannot see: between
the steps the owner can alt-tab to their bank, and step two types into
whatever is there now.

When the guard cannot be evaluated - no window source, an unreadable
desktop, nothing reporting itself active - it **raises**. Skipping it would
give the caller the words without the protection, and a caller who asked
for a guard and silently did not get one is worse off than one who was told
no. This is the general rule for any conditional safeguard in the tool
layer, not a detail of input synthesis.

The same reasoning sets where effort goes: all four input tools are
DANGEROUS, the top of the existing three-rung ladder, and no fourth rung
was invented. A new risk level would have to be learned by the settings
contract, the Android DTO, `config.yaml` and the Hub before it protected
anything, and it would protect nothing the owner cannot already get by
leaving `dangerous` out of `tools.auto_approve`. The guard is the part the
ladder cannot express, so that is what was built.

### An API that reports success for something else (18.5)

`SetCursorPos(2420, 1580)` on a 1920x1080 desktop returns **true** and
leaves the pointer at **(1919, 1079)**. It clamps silently and reports
success. `SendInput` with a wrong `cbSize` returns 0 and leaves
`GetLastError()` at **0** - nothing inserted, no error to read.

Two standing consequences:

- **Refuse before acting where the API would silently substitute.**
  `_target` rejects off-desktop coordinates rather than letting Windows
  clamp them, because a click aimed off screen would otherwise land on
  whatever sits in the corner and be reported as having worked.
- **Where the only evidence is a count, return the count.** `_send` returns
  `(accepted, submitted)` and `_sent` compares them, because for that whole
  family of failure there is no exception and no error code. This is the
  concrete form of section 11's "must not rely only on: the command
  executed without throwing".

### A postcondition names what it cannot see (18.5)

Three tools, three different honest answers, and the differences are the
point:

- `move_mouse` and `click_mouse` re-read the cursor. `click_mouse` claims
  **nothing** about the click having had an effect - only the receiving
  application knows whether the button under the pointer was enabled.
- `press_keys` asserts no modifier this call pressed is still held. That is
  the only durable trace a key press leaves, and it is readable through
  `GetAsyncKeyState`, which was measured seeing a *synthesised* modifier.
  It does not claim `ctrl+s` saved anything.
- `type_text` re-checks only that the named window is still in front, and
  says in words that it does not claim the characters arrived. Nothing can
  re-ask that.

`verify` returning `None` where the desktop is unreadable is deliberate and
distinct from failing: the executor treats None as no postcondition offered,
which is honest - nothing was learned - and "absence of a check" is not "a
failed check". A limit that cannot be closed is stated rather than papered
over: `SendInput` blocked by UIPI is reported through neither the return
value nor `GetLastError`, so a full accepted count does not prove arrival at
an elevated window.

### The test suite does not act on the owner's machine (18.5)

Anything that synthesises input has a suite that must not use it. The owner
may be at this computer while tests run, so `TestTheWindowsBackend` reads
only - geometry, cursor, `window_at`, key state, `sizeof(INPUT)` - and its
single write moves the pointer and restores it in a `finally`. Backend
behaviour that genuinely needs the event stream is tested by stubbing
`_bind` truthy and replacing `_key_event` with a recorder, so chord release
order, KEYUP flags and UTF-16 surrogate pairs are all asserted without a
keyboard.

### Pixels are a file, not a return value (18.4)

A screenshot cannot be a tool's return value - a tool returns text that
goes into a prompt - so `take_screenshot` writes a file and returns one
short sentence about it. That single fact settles most of the design:

- **The destination is 18.3's grant, not a new one.** `tools.writable_paths`
  and 18.3's own `_contained`, `_atomic_write` and `_shown`, imported from
  `tools/builtins/filesystem.py` rather than reimplemented. Two
  resolve-and-compare implementations can drift, and the one that drifts is
  a path escape. `core/settings_store.py::ALLOWED` gained nothing: a
  settable screenshot path would be a second setting answering the same
  question as `writable_paths`, able to disagree with it.
- **The path is proved before the screen is read.** Every refusal in
  `_target` happens before a single pixel exists, because a screenshot held
  in memory on a failure path is a privacy leak that leaves nothing behind
  to find. This is the standing rule for anything in this layer that
  produces sensitive content: prove the destination first.
- **Registration is gated twice.** Inside `if writable:` *and* on
  `default_screen_capture() is not None`. The second gate is the factory's
  standing rule rather than a special case - a tool whose dependency is
  absent is not registered, so it is missing rather than present and
  failing.

### A capability whose backing is absent is None, never a mock (18.4)

`default_screen_capture()` prefers `mss`, falls back to `GdiScreenCapture`
on Windows, and returns **None** otherwise. Not `MockScreenCapture`: a mock
wired in by default would let a screenshot tool report success having
written a blank image, which is the failure the whole verification effort
exists to prevent. Same rule as the tool factory, one layer down.

`GdiScreenCapture` mirrors `ScreenshotCapture` deliberately - same
constructor, same two protocol members, same `_resolve` semantics and same
warn-once behaviour - so a configured `monitor` means the same display
whichever backend happens to be installed.

### Where a per-call argument meets a stateful backend, pass a factory (18.4)

`ScreenshotTool` takes `capture_factory: monitor -> ScreenCapture | None`,
not a capture object. The alternative is mutating a shared backend's
`monitor` between calls, and that backend carries a warn-once flag whose
entire purpose is to fire - so reuse would suppress the warning for a
second bad index and capture the wrong screen silently. Standing shape for
any tool whose per-call argument configures a stateful dependency.

### What a verify may not claim (18.4)

`take_screenshot.verify` reads the file back and parses the PNG header,
because "the write did not throw" leaves a file behind when a disk fills or
an antivirus rewrites it as it lands. It does **not** claim the picture
shows the screen, and says so in the docstring. Nothing can re-ask that:
the screen has already changed, and a second capture is a different moment.

The precedent this sets, alongside `focus_window`'s: **a verify asserts
what is readable now and states plainly what it cannot.** A one-time
correctness proof - here, agreement with PIL's independent `ImageGrab`
against flipped and channel-swapped references - belongs in the test suite
and the state files, not pretended at on every call.

### A tool that acts on the desktop, and one that only reads it

Four PC tools live in two modules under `tools/builtins/`, and the split
between them is the standing rule, not the file layout:

**A reader's postcondition is its return value.** `system_information`,
`list_processes` and `list_windows` have no `verify()` and must not grow one.
Re-reading asserts nothing the caller cannot already see, and a `verify()`
there would be ceremony that makes section 11 look satisfied where nothing
was ever at risk.

**An actuator's postcondition has to be asked separately, from a different
direction than the call.** `focus_window` is the only actuator in 18.1, and
it exists in this shape because `SetForegroundWindow` returns zero and
changes nothing whenever Windows' foreground lock applies. So `execute`
performs the action and reports what it *asked for*; `verify` reads the
foreground window back, on a bounded 1.5s poll because focus is
asynchronous. That is the section 11 shape for anything added in 18.2/18.3:
the evidence must come from re-reading the world, not from the call
returning.

### The OS lives behind a source object

Each module carries a Protocol, a Mock and a real implementation - the triple
`vision/capture.py` established. `WindowSource`/`MockWindowSource`/
`WindowsWindowSource`, and `ProcessSource` with two real backends
(`PsutilProcessSource`, `TasklistProcessSource`) chosen by availability.
`MockWindowSource(honour_focus=False)` reproduces the foreground lock exactly,
which is how the failure path stays testable on a machine whose real
foreground must not be disturbed.

**Both `default_*_source()` functions return `None` when nothing can read this
OS**, so the factory's existing absent-dependency rule applies: a tool whose
dependency is absent is *not registered*, and is therefore missing rather than
present and broken. Any 18.2/18.3 tool follows the same rule - `mss` is not
installed here, so a screenshot tool must be absent, not stubbed.

`list_windows` and `focus_window` are registered **together and share one
source object**. Two sources would be two enumerations of the same desktop,
and `focus_window` matching against a listing the owner never saw is how the
wrong window gets brought forward.

### Registered is not enabled

`_pc_tools()` adds these to the registry; `config.yaml`'s `tools.allowed`
still lists only `current_time` and `remember`. Section 2 cuts both ways: the
owner must be able to enable a PC tool freely through settings, and must not
discover one already enabled without having said so. The guard test reads
`config.yaml` from disk rather than through the loader, so a defaulting bug in
the loader cannot hide a silent enable.

### What the desktop reading deliberately withholds

Window handles are never printed - they are matching keys, not owner
information, and a stale handle in a transcript is a way to act on the wrong
window later. `system_information` omits username, hostname and home
directory, with a test asserting their absence: section 30's habit applied one
level out from credentials. A fact that cannot be read is omitted, never
zeroed, so "unreadable" cannot be mistaken for "zero".

### An empty listing means different things in different places

`list_windows` treats an empty desktop as a **success** and `list_processes`
treats an empty listing as a **failure**. Every operating system has
processes, so nothing to report can only mean the reading broke; a session
genuinely can have no visible titled window. The asymmetry is documented at
both sites so it reads as a decision.

### The owner writes the command; the model fills the blanks

`run_command` (DANGEROUS, `tools/builtins/commands.py`) is the whole of
section 24's "terminal commands", and its shape is that sentence read
literally: *"Do not give arbitrary LLM text direct unrestricted shell
execution without a controlled tool boundary."* The model never composes a
command line and never names a program. The **owner** declares named argv
lists in `tools.commands`; the model supplies a declared key plus values for
whatever slots the owner wrote.

Two declaration shapes are accepted on purpose - a bare argv list, and a
mapping with `argv`, `description`, `parameters`, `cwd`, `timeout` - because
the short one is what an owner actually types and the long one is what they
need once they want a description or a working directory. Anything else is
refused at load time with a message naming the fix, and the refusal for a
bare *string* is specifically worded, because `applications` (18.1) accepts
exactly that shape, so `unit_tests: "pytest -q"` is a predictable mistake
rather than a random one.

### One slot per argv element, and `shell=False`

A slot is a whole argv element or part of one, substituted into that element
alone. There is no splitting, no joining, and no shell. The consequence is
the invariant the phase exists for: a value cannot become a second command
and cannot become a second *argument*. `["--flag", "{value}", "--after"]`
filled with `x --injected y` reaches the program as three elements.

This is verified against real subprocesses rather than mocks. A mock asserts
what the author already believed about the platform; only the platform can
show the belief is true.

### The re-parsing platforms are refused - and only where text can reach them

A resolved `.bat`/`.cmd` re-parses its arguments through cmd.exe even under
`shell=False`. Measured on CPython 3.11.15: `& && | ^ >` are neutralised (the
CVE-2024-1874 defensive quoting is present), but a literal `"` breaks out and
the remainder runs as commands, and `%CD%`-style references expand. So a
command whose executable resolves to `.bat`/`.cmd` **and which has a fillable
slot** is refused before anything is spawned; one with no slot runs.

The scope is the point. With no slot there is no model-supplied text for
cmd.exe to re-parse - the argv is entirely the owner's, and refusing it would
be an arbitrary restriction on the owner's own configuration. This is not an
exotic path: `shutil.which` resolves `npm`, `npx` and `code` to `.CMD` shims.

### A brace the owner typed is not the model's text

Braces left in the argv after substitution are **passed through**, not
refused. `find . -exec rm {} \;`, `grep -E "a{2,3}"` and `jq "{name: .n}"`
are ordinary commands, and the braces in them are the owner's text, so
section 24 - which fences *model-supplied* text - does not apply. Only
brace content that reads like a misspelled slot (`{ pattern }`,
`{my-pattern}`) draws a load-time warning naming the correct spelling. Warn,
never override.

### The child does not inherit the owner's keys

`core/credentials.py` deliberately puts stored keys into `os.environ`, and
children inherit `os.environ`, and this tool's output reaches the transcript.
Section 30 says a key must never appear in chat history, so
`_child_environment` strips before spawning: every name in
`brain/router.py::PROVIDER_KEYS` and `core/credentials.py::SECRET_ENV_VARS`,
imported **at call time** so a provider added later is covered with no edit
here, plus a `KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH` sweep for the
owner's own secrets.

One documented exception: `SSH_AUTH_SOCK` survives, because it is the *path*
of a socket rather than the contents of one, and `git` over SSH stops working
without it.

Note for anyone testing this: every name currently in those two tables also
matches the pattern sweep, so no existing provider can demonstrate that the
imports do anything. The mechanism is only verified by a test that
monkeypatches in a name the pattern would miss.

### The tool's bound sits outside the command's bound

`tools/timeout.py` bounds `execute` on a daemon thread it cannot kill, so
`self.timeout = longest_declared + KILL_GRACE + 5.0`. An inner bound would
abandon that thread mid-kill and leave the process it was killing alive with
nobody watching. A declared `timeout: 0` leaves both bounds unbounded rather
than being clamped to something sensible: `tools/timeout.py` documents 0 as
the deliberate no-bound hatch, and clamping the owner's explicit 0 would be
the silent mutation of owner configuration section 2 forbids. It warns.

### Output goes to temp files, not pipes

A killed process tree can leave a grandchild holding the inherited stdout
pipe: measured 29.25 s to return from `subprocess.run(timeout=1.0)`, and
`taskkill /F /T` did not unblock the read. Temp files plus `wait(timeout)`,
`kill()` and `taskkill /F /T /PID` return in ~1 s **with whatever output the
command had already produced**. An overrun therefore reports partial output
plus the fact that it was stopped, instead of holding the reply open.

The honest check for "did the tree die" is the process table, not the elapsed
time - a re-timing test only shows the call returned.

### The exit status is the verification

`run_command` has no `verify()`, and the reason is the opposite of the usual
one. The exit status is the program's own verdict, read back after the fact -
not "the command executed without throwing", which section 11 rejects. A
non-zero exit is an honest failure carrying stderr, never a success with sad
text attached. Re-asking would mean running the command again: double the
side effects, no new information, and this tool cannot know what any given
owner-declared command was supposed to change. A test asserts the absence is
deliberate.

### `tools.commands` is deliberately not settable over the wire

`core/settings_store.py::ALLOWED` exposes `tools.enabled`,
`tools.auto_approve` and `tools.timeout`, and **not** `tools.commands`. A
settable `commands` would let anything holding the bearer token declare
`["cmd", "/c", "{x}"]` and then fill in `{x}` - arbitrary shell execution
reached *around* the tool boundary through the settings API rather than
through it. This is the one place where "the owner may configure anything"
yields, and it yields to the same section 2 clause that grants it: owner
configuration freedom "does NOT mean that an LLM is allowed to bypass
application-level safety, permission, authentication, or OS security
boundaries."

Guarded from both sides: a Python test on `ALLOWED`, and a Kotlin test that
the live settings document's `configurable` list carries no `tools.commands`
prefix.

### The device displays the grant and cannot change it

`ToolsConfigDto.commands` is `Map<String, JsonElement>` - raw JSON, because
the server accepts two declaration shapes and this screen only displays them.
Parsing them into a sealed Kotlin type would be a second implementation of a
schema the PC already owns, and it would fail closed on a shape a newer
server added. It is reported precisely because it cannot be set from there:
the owner can see on their phone what their PC has been authorised to run.

A settings body that predates the field must still parse, so an older server
that never heard of `run_command` does not break the settings screen.

### Reading a folder and being allowed to overwrite it are two grants

`tools.allowed_paths` governs `read_file` and `list_directory`.
`tools.writable_paths` governs `write_file`, `append_to_file`,
`create_directory` and `delete_file`. They are separate lists, read
separately in `tools/factory.py`, and the second is **never** defaulted
from the first.

The inheriting version would fail section 2 in the specific way section 2
warns about. An owner adds their notes folder so Aura can look something
up; under inheritance that same act grants permission to overwrite every
file in it, and the owner is never asked. An owner who wants both lists
the folder in both places.

The rule holds in both directions. A write grant does not imply a read
grant either: `verify()` reads the file back, but that is a tool checking
its own postcondition, not the owner having permitted `read_file` there.

### `writable_paths` is not settable over the wire

Absent from `core/settings_store.py::ALLOWED`, for the same reason
`tools.commands` is. A settable writable root would let anything holding
the bearer token add `C:/` and then have `write_file` replace whatever it
liked - filesystem access reached *around* the tool boundary through the
settings API instead of through it. Section 2's own limit: owner
configuration freedom does not extend to letting an LLM bypass
application-level permission boundaries.

Guarded on both sides - a Python test on `ALLOWED`, and a Kotlin test
that `configurable` carries no `tools.writable_paths` prefix, so a future
server that starts sending them one at a time is caught too.

### The temporary lives in the target's own directory

`_atomic_write` creates its temporary with
`tempfile.mkstemp(dir=str(target.parent), ...)`. The `dir=` is
load-bearing: `os.replace` across volumes fails on Windows with
`ERROR_NOT_SAME_DEVICE`, because CPython calls `MoveFileExW` without
`MOVEFILE_COPY_ALLOWED`. On this host `%TEMP%` is on `C:` and the
repository on `D:`, so a temporary in the system temp directory would
make every write to a folder on any other drive fail.

`mkstemp` rather than the repository's usual `<path>.tmp` because the
path came from a model, so `notes.md.tmp` may be a file the owner already
has. A unique name in the same directory keeps both properties: no
collision, and the replace stays atomic.

Bytes are written untranslated. `Path.write_text` turns `\n` into `\r\n`
on Windows, so a two-line file would not contain what was asked for and
the byte comparison in `verify` could never pass.

### A failed write leaves what the owner had

Temporary file, `fsync`, then `os.replace`; on any exception the
temporary is removed and the original stands. This is the pattern
`core/settings_store.py`, `core/credentials.py` and
`proactive/ledger.py` already use, and the reason is stronger here: their
half-written file is Aura's own, this one is the owner's.

Cleanup is best effort. If the temporary cannot be removed the write has
still failed correctly, and raising a second error from the cleanup would
replace the real reason with a confusing one.

### This is where a postcondition is actually cheap to read

All four writers have a real `verify()`, and the contrast across the PC
layer is the point:

- `write_file` reads the bytes back and compares them. The condition is
  durable and exact.
- `focus_window` can read its condition back, but the answer decays, so
  it polls on a bounded 1.5s window.
- `run_command` cannot re-ask at all: the exit status *is* the
  postcondition, and asking again means running the command twice for
  double the side effects and no new information.
- `read_file` and `list_directory` have **no** `verify()`, because a
  read's postcondition is its return value.

`append_to_file`'s verify is honest about its own limit: it proves the
added text arrived, not that nothing else changed. The prior length is
not among the arguments `verify` receives, and stashing it on the
instance would be a lie the moment two appends overlap.

### Destroying something is asked for, never assumed

An existing file needs `overwrite=true`; `append_to_file` refuses a file
that is not there rather than creating it the way shell `>>` would;
`delete_file` never takes a directory; `create_directory` is idempotent
and says "already exists" rather than "created".

Each refusal exists because the silent version is a specific failure a
model produces. A model that means `notes-2026.md` and types `notes.md`
destroys a year of writing. A mistyped append target becomes a new file,
reports success, and leaves the owner's real log empty while Aura reports
writing to it daily. Recursive deletion has a different blast radius from
everything else here and nothing to read back afterwards to learn what
was lost.

Oversize content is refused rather than truncated: truncation is silent
data loss, and it would pass `verify` only if `verify` truncated
identically. `append_to_file` bounds the resulting total, not just the
addition, or a bounded append repeated is an unbounded file.

### Paths are shown relative to their root

Every message these tools produce ends up in a prompt and therefore
leaves the machine, and an absolute path names the owner's home directory
and username - the same reason `system_information` reports the disk and
not the user. `_shown` renders root-relative POSIX paths and falls back
to the bare filename for a path it cannot place, because it is called on
the way out of error paths too and must not raise.

A relative path gets its own refusal naming the actual problem, because
"outside the allowed directories" is true and useless to a caller who
will just try another bare filename. Containment semantics are unchanged:
with multiple roots there is no non-arbitrary root to resolve a relative
path against.

### Containment resolves first, and what that does and does not catch

`_contained` calls `Path.expanduser().resolve()` before comparing against
the roots, so `..` traversal, symlinks and directory junctions all land
outside and are refused. It matters more for writing than for reading: a
followed link would replace the owner's real file elsewhere and leave the
link looking untouched.

A **hard link** is not caught and cannot be - both names are equally
real, so there is nothing for `resolve()` to see through. Not reachable
through Aura today, since no tool creates links.

## Phase 17 architecture (standing)

- **Two authorities on "done", and only one of them decides.** The
  **server** owns *is the goal met*: `plan_for` -> `task_graph.build` ->
  `is_finished` / `is_stuck`, and the model ends a run by emitting
  `complete`. The **device** owns something narrower and must not be
  confused with it - *may I stop this loop without asking?*
  (`shouldAutoComplete`, `isSearchTaskComplete`, `isSelectionTaskComplete`
  in `AuraAccessibilityService`'s companion object). That is a latency
  optimisation, not an authority: the cost of a false *no* is one round
  trip, and the cost of a false *yes* is a task abandoned half-done with
  the owner told it succeeded. So the device is allowed to be wrong only
  in the first direction. Phase 17 named this boundary rather than merging
  the two, because merging would have meant either the device re-deriving
  the plan or the server being consulted every tick.

- **`is_finished` / `is_stuck` are observability, not control.** Their
  only callers are `brain/conversation.py`, which emits `TaskFinishedEvent`
  and `TaskStuckEvent`. Nothing branches on them. Reading them as the
  server's *enforcement* of completion is the mistake to avoid: the
  enforcement is the model's `complete` action.

- **One vocabulary per concept, read by everyone who decides on it.**
  `AuraActionExecutor.SEARCH_VERBS` is a single companion constant with
  three readers (the query sanitiser, `shouldAutoComplete`,
  `isSearchTaskComplete`); `multiStepKeywords` and `selectionCues` are one
  declaration each - `selectionCues` had been declared twice, identically,
  in two functions. The rule this encodes: a list that only one function
  reads will drift from the list the next function needs, and the drift is
  silent because both agree with themselves. `tests/test_agent_protocol.py`
  extracts these lists **out of the Kotlin source** rather than keeping a
  Python copy, for the same reason - a Python-side copy would agree with
  itself forever while the device drifted.

- **Containment, not equality, is what has to hold across the wire.** The
  device's `multiStepKeywords` and the planner's `CONJUNCTIONS` are *not*
  the same set and should not be made so; what must hold is that every
  separator the planner splits on is one the device also treats as
  multi-step, so the device can never call single-step a request the
  server would decompose. `test_the_device_conjunctions_cover_the_planners`
  asserts that direction and only that direction.

- **A structural guard for every vocabulary invariant.** An assertion over
  a declared list passes trivially if the list is declared and never read -
  which is precisely the pre-phase-17 state, where `SEARCH_VERBS` existed
  and `shouldAutoComplete` did not consult it. So the Python tests also
  read the *bodies* of `shouldAutoComplete` and `isSearchTaskComplete`
  (`device_early_exit_body()`, `device_search_completion_body()`) and fail
  if the reference is gone. Mutations M6 and M8 are what prove the guards
  are not themselves vacuous.

- **"Work named after the search" is positional, not lexical.**
  `hasClauseAfterSearch` measures from the **last** search verb, because a
  conjunction *before* the search is the ordinary two-clause request
  ("open YouTube and search Minecraft") which must keep ending at the
  submit. A containment test for conjunctions would have broken it. The
  positional rule was also chosen over widening `selectionCues`, because
  three of the six trailing verbs the planner recognises (`tap`, `click`,
  `bấm`) are not in the device's selection list at all, and adding bare
  `open` / `mở` there would be misread as a launch verb.

- **A serialized field needs a test that crosses the serializer.**
  `AppInfo.activity` was read by `brain/agent_mode.py` and
  `brain/prompt_builder.py` and was never assigned; `packageName` carries
  `@SerialName("package")` and `activity` deliberately does not, so a
  rename on either side would leave the field permanently absent again and
  every decision test would still pass. Two tests now encode the wire key
  by `Json.encodeToJsonElement`, and mutation M4 (renaming the key to
  `"screen"`) fails **only** those two - which is the evidence that the
  other nine never covered it.

## Phase 16 architecture (standing)

- **A tool's postcondition lives on the tool as an optional `verify()`,
  and the executor - not the tool - enforces it.** `ToolExecutor` runs
  `_run`, then `_verified(tool, arguments, result)`, then `_finish`. The
  section 11 rule is that a success may not rest on "it did not throw",
  so `_verified` re-asks the condition the call was meant to establish
  and downgrades an `ok` result the postcondition denies. Because
  `_finish` is what emits `ToolCompletedEvent`, a downgrade appears on
  the event bus as `ok=False` with no extra wiring - the same surface
  the phase-15 boundary test guards.

- **`verify()` is optional-by-absence and never on `ToolProtocol`.** The
  Protocol is still exactly three members (`name`, `risk`, `execute`)
  and `test_the_protocol_stayed_narrow` fails if a fourth appears. The
  executor looks `verify` up with `getattr`; a tool without one is not
  penalised. It joins `timeout`, `describe` and `required_parameters` as
  a capability the framework reads only where present.

- **Three fixed rules inside `_verified`, each pinned by a mutation
  test:** a failed result is never re-verified; a `verify()` that raises
  **fails closed** (returning the success would assert precisely "it ran
  without throwing", the sentence section 11 forbids); `verify()`
  returning `None` asserts nothing and leaves the result as-is.

- **This is not the device verifier.** `brain/recovery.py`,
  `brain/task_graph.py` and `brain/planner.py::is_done` reconcile
  **device** actions reported back as `ActionRecord`s against
  `CognitiveState`. A local Python tool never creates an `ActionRecord`
  and never enters that graph; its failure becomes model-visible text
  through `conversation._render_result` and is bounded by
  `TOOL_CALL_LIMIT = 3`. The two verification worlds do not overlap.
  `recovery.py`'s FAILED-vs-UNVERIFIED wording was borrowed by
  description, not imported - no new brain -> tools dependency.

- **`capabilities` (section 22's seventh member) is deliberately not
  implemented.** `risk` drives approval, `tools.allowed` decides what
  runs, `parameters` declares the interface - a `capabilities` field
  would have no consumer. It is added when a phase brings a real reader.
  Same discipline as `recovery.py::RETRY_LIMITS = {}`.

- **`remember` is the only tool with a `verify()` today**, because it is
  the one whose silent-failure mode was live: `execute` now fails on a
  `None` write from `remember_user_stated` (empty-slug key) with a reason
  naming the key, and `verify` reads the fact back through
  `user_model.value_of` - the same path the prompt recalls with, so it
  proves reachability, not just row-existence. `open_application`
  deliberately has no `verify()`: it verifies inside `execute` (resolve
  the executable, watch the process through a grace period) and its
  postcondition cannot be honestly re-asked afterwards. Absence of
  `verify()` means "execute told the whole truth", never "unverified" -
  stated in the `tools/base.py` Protocol docstring for the next author.

## Phase 15 architecture (standing)

- **`proactive/memories.py` is the appreciation source, and it exists
  because the category could not fire.** `ProactiveEngine` has taken a
  `memories` callable since it was written; `launcher/services.py` never
  passed one. So `engine.memories` was `None` in every real process,
  `_gather_memories()` returned `()`, and `if context.relevant_memories`
  guarded dead code. The probe, before any edit: `'_build_proactive'
  passes memories=: False` / `engine.memories is: None` /
  `context.relevant_memories: ()`.
- **It reads the `project` category and deliberately not `plan`.**
  `proactive/tasks.py` owns `plan`, for reminders. Mining one category for
  two kinds of unprompted message means saying the same thing twice in two
  voices.
- **One store, not two.** The source is handed `pipeline.episodic`, the
  same object `EpisodicTaskSource` gets, so an appreciation cannot be
  about a project recorded in a database the reminders never read. There
  is a test asserting the identity, not just that both are non-None.
- **Nothing in it writes or paraphrases a fact.** The text handed out is
  the user's own stored sentence. Window: older than 24h (below that,
  repeating what somebody said an hour ago is not warmth) and newer than
  14 days (past that it is bringing up something they have moved on
  from); shorter than 12 characters is a fragment, not a subject, because
  the composer interpolates it into "Been thinking about {subject}."
- **A row with an unreadable `occurred_at` is skipped, and the skip is
  the whole point.** `parse_timestamp` is tolerant because it reads
  columns written by several versions of Aura. Without the `continue` the
  comparison is `oldest <= None`, which raises, which
  `_gather_memories` swallows as "nothing to say" - so the cost of one
  bad row is not one lost memory but *every* memory, for as long as that
  row sits in the fortnight the query covers. This was the one mutant the
  first round did not kill.
- **`KNOWN_CATEGORIES` closes the set where sending is decided.**
  `proactive/decision.py` says of `Category`: "a category that is not
  listed here cannot be sent at all." That was true of the decision engine
  and false of the policy, which took a plain string and looked the
  cooldown up with `.get()` - so an unlisted category arrived with no
  per-category throttle. Probed: `"insight"` sent **5 of 5** distinct
  messages in five seconds; `"task"` sent **1 of 5**.
- **Refused, not given a default cooldown.** Inventing a plausible number
  for a category nobody declared would hide the caller bug and ship the
  spam (§44 forbids inventing behaviour). Refusing makes the sentence
  `decision.py` already claims actually true. It does not restrict the
  owner (§2) because an unknown category cannot come from owner
  configuration - `category_cooldown_seconds` keys are merged over the
  defaults and the owner may set any value for a *real* category.
- **The set is derived from the enum, not retyped.**
  `frozenset(category.value for category in Category)`. A hand-maintained
  copy of a closed set is the thing that drifted in the first place, and
  there is now also a test asserting every `Category` member has an entry
  in `DEFAULT_CATEGORY_COOLDOWN`.
- **Guard placement is part of the design.** After `enabled` and the
  empty-message check (cheaper, more fundamental), before quiet hours, so
  the reason an operator reads names the actual problem rather than the
  time of day. The reason string contains the offending category and the
  set it is not in.
- **Nothing on the event bus acts on an event, and that is now a test
  rather than a coincidence.** Enumerated at a fully built composition
  root: `events.log` (1, via `subscribe_all`, so it lives in `_wildcard`
  and a walk of `_handlers` alone misses it), `avatar.animation` (6),
  `avatar.state` (1, subscribed to base `Event`), `avatar.controller` (1),
  `voice.tts.engine` (2), `server.notifications` (1, attached by
  `server/runtime.py` rather than by `build_services`). All render, speak,
  log or queue. `subscribe` takes any callable, `publish` calls every
  match and swallows handler exceptions, so the day something that acts is
  attached, an event becomes an action with nothing in the diff saying so.
  The test walks both lists and fails on any owner module outside a
  declared presentation set - verified to fire by attaching a fake
  `tools.autopilot` handler.
- **`seconds_since_user`'s docstring was wrong and is now accurate.** It
  claimed every consuming rule uses the value "to *hold back*, never to
  fire". The active-conversation guard does; the greeting, task and
  appreciation rules all fire on a *large* value, and infinity is the
  largest. Probed: `last_user_message_at=None` -> `inf` -> `send: True,
  category: greeting, reason: user away, no record of a previous
  message`. Only `greeted_this_part` and the engine's presence source
  stand between an empty history and an unprompted "welcome back".

## Phase 14 architecture (standing)
- **`companion/policy.py` reuses phase 13's `SendLedger`, not a copy of it.**
  The durable-JSON pattern from `core/settings_store.py:620-670` has one
  implementation, in `proactive/ledger.py`, and the companion gate imports
  it.
- **It deliberately does not reuse the file.**
  `LEDGER_PATH = DATA_DIR / "companion.json"` with
  `LEDGER_CATEGORY = "companion"`. One shared ledger would be less code and
  wrong: each gate would count the other gate's sends against its own
  budget, and the owner configured two budgets, not one.
- **All seven `server.companion.*` paths are settable and live-reapplied**:
  `enabled`, `relevance_threshold`, `cooldown_seconds`, `max_per_hour`,
  `quiet_hours`, `suppress_after_chat_seconds`,
  `duplicate_window_seconds`. Before this phase only `enabled` was
  reachable, which under section 2 means the owner's only remedy for a
  chatty assistant was silence.
- **`DEFAULT_DUPLICATE_WINDOW = 1800.0` keeps the old constant's value as a
  default only.** The number did not change, per section 41: a gate that
  behaved a certain way yesterday must not behave differently today merely
  because a knob became reachable.
- **Monotonic for intervals, wall time only to cross the disk**, as phase 10
  set and phase 13 refined - and **ages that come back negative are
  discarded rather than trusted**, because sleep/resume, an NTP correction
  or a timezone change can store a timestamp ahead of now, and a naive
  reading of that either wedges the gate shut or opens it completely.
- **`_last_notified` does not exist.** `allows()` derives it -
  `max((when for when, _ in recent), default=None)` - so there is one
  truth and it is the one that survives a restart. Section 8, and the same
  move phase 13 made on `_greeted`.
- **`reset()` clears the file, not just the deque.** A limit that comes
  back from disk after the owner dropped it is not a limit the owner
  controls (section 2).
- **The `NotificationOutbox` is not the rate-limit history, deliberately.**
  An outbox is a queue, not a receipt: something in it may never be
  delivered and something delivered may already have been dropped from it.
  Rate limiting is decided by what was actually sent.
- **The two gates keep independent budgets** - proactive 4/day, companion
  up to 12/hour - so section 20's "do not spam" holds per gate, not across
  both. Merging them would silently redefine two numbers the owner set,
  which section 2 forbids, so this is a recorded known issue awaiting an
  owner decision, not an oversight.
- **`data/companion.json` and its `.tmp` sibling are gitignored**, for the
  proactive ledger's reason: not a secret, but a record of what Aura said
  to this owner unprompted, written by the running server.

## Phase 13 architecture (standing)
- **`proactive/ledger.py` is the durable send history**, and it is a JSON
  file rather than a fourth memory table on purpose: `memory/models.py`
  says its three tables are "one per kind of knowing" and deliberately has
  no table for temporary context, because "the whole point is that it
  cannot silently become permanent". A rate-limit record is exactly that
  kind of expiring bookkeeping, so it does not belong there.
- **It stores time, category and message text.** The text is not optional
  detail: `duplicate_window_seconds` and `similarity_threshold` are
  questions about *what was said*, so a ledger of timestamps alone would
  restore four of the five limits and silently drop the fifth.
- **It follows `core/settings_store.py:620-670` exactly** - `{"version": 1}`
  root, unreadable file logged as `type(error).__name__` only and then
  ignored, non-dict root ignored, per-row validation that drops the
  offending row rather than the file, atomic `.tmp` + `os.replace`. It
  diverges in one place, documented in the module: `save` catches its own
  exceptions, because a failed write of a rate-limit record must not take
  down the send.
- **The ledger arrives from the composition root, never by default.**
  `ProactivePolicy(ledger=None)` and `build_proactive_engine(ledger=None)`
  keep a bare policy file-free; `launcher/services.py` supplies
  `SendLedger()`. This is the reasoning `core/app.py` already records for
  the clock, applied to a second faculty.
- **The policy loads it once, in `__init__`.** `allows` asks the history
  four separate questions under one lock, and re-reading per question
  would let the answers disagree inside a single decision.
- **`ProactiveEngine._greeted` no longer exists.** Because `part_of_day` is
  a pure function of any datetime, "did I greet this part of the day" is
  derivable from the ledger, so the dict was a section 8 duplicate of a
  fact already recorded - and the copy that did not survive a restart.
  `_prune_greetings` went with it.
- **Presence comes from the messages table.**
  `MemoryManager.last_said_at(role="user", session_id=None)` answers "when
  did the owner last speak" from the rows that already exist;
  `ProactiveEngine.note_chat()` remains as a live in-process cache with a
  narrower lifetime, not a parallel truth. `session_id` defaults to *every*
  session, unlike `get_recent` above it, because the phone supplies its own
  id.
- **`data/proactive.json` is gitignored**, with its `.tmp` sibling. Not a
  secret, but it is a record of what Aura said to this owner unprompted and
  it is written by the running server.

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
- **`AURA_WRITE_ANDROID_FIXTURES=1` must never be run on a host holding
  provider keys.** `tests/test_settings_fixture.py` rewrites **all three**
  documents under `android/app/src/test/resources/live/` from this process's
  live state, and a `.env` at the repository root is loaded into
  `os.environ` at import time under pytest. Phase 10 learned this the
  expensive way: the run wrote the masked tail of the owner's real Gemini key
  into `providers.json` and `settings.json` (section 30) and put the live
  provider chain into `provider_health.json`, breaking `SettingsContractTest`'s
  pin on the keyless deployment. The fixture file's own docstring says it
  compares *shape*, not values, precisely because `key_masked`, `configured`
  and the chain depend on which keys a host happens to have. **Deleting the
  key variables before `init_runtime()` is not enough, and phase 14 proved
  it by probe**: every provider constructor calls `load_dotenv()` itself
  (`server/main.py:19`, `brain/providers/gemini.py:29`, `groq.py:42`,
  `http_chat.py:260`, `mistral.py:44`), so the deletion is undone the moment
  the provider is built - a run done that way still emitted the real key
  tail, `"configured": true` and `"key_source": "environment"`. A genuinely
  keyless runtime needs `load_dotenv` patched to a no-op in those four
  provider modules plus `server.main`, `core.config` and `dotenv` itself,
  which was verified to yield `configured: False, key_source: ''`. Then diff
  every fixture against HEAD and confirm additions only. Not with
  `git checkout` (section 45), which would also revert the uncommitted
  `custom` provider entries phase 1 added. **In practice, prefer a targeted
  edit** - section 45 says so directly, and phase 14 added six paths and one
  `effective` field that way, 52 insertions and 0 deletions across the three
  fixtures.

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
