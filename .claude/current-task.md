# Current Task

## JARVIS modernization: phases 1-19 done, phase 20 next (uncommitted)

Phases 1-18 of the section 42 order are IMPLEMENTED, and **phase 19 is now
complete**. Split per section 43: 19.1 the processor chain, 19.2 the
on-demand tool, 19.3 verification - all IMPLEMENTED. Next is **phase 20,
Voice**. Every record is in `progress.md`; the outcomes and the phase 20
brief are below. This section carries only what the next session needs.

**Phase 19 - Vision (section 19). IMPLEMENTED.**

19.1 was a regression, not a missing feature. Turning `vision.capture_screen`
on made vision report *less*: the pixel processor **replaced**
`WindowTitleProcessor` instead of layering over it, and `VisionManager.refresh`
reads an empty description as "no observation" and drops the context to None.
Measured both ways on this machine - `False` gave "[screen] User is browsing
the web in Chrome", `True` gave `None`. `vision/processor.py` `ProcessorChain`
is the fix: first non-empty answer wins, titles last as the floor, and it
advances on **both** ways a processor declines, because
`OllamaVisionProcessor` returns `""` for every failure while
`CloudVisionProcessor` *raises*. A chain catching one would go silent for the
other backend.

19.2 added the on-demand half. `tools/builtins/vision.py` `DescribeScreenTool`,
one more tool through the same five gates. Three things to carry forward
because each was a bug avoided rather than a preference:

- **`refresh()`, not `get_context()`, in `execute`.** The throttle stops fifty
  turns becoming fifty screenshots and is exactly wrong when the model has
  just asked to look.
- **`execute` refuses when `vision.enabled` is off.** `refresh()` does not
  consult the flag; only `get_context()` does. Without the check the tool
  would look at a screen the owner had switched off - section 2 with pixels
  attached. Guarded in the factory too.
- **`verify` reads `last_observation` / `seconds_since_observation`, not
  `get_context()`.** The first draft used `get_context()`, which re-observes
  once its throttle expires and with `min_interval: 0` on every call - so
  verification would have paid for a second capture and, with a cloud link, a
  second upload.

**Standing rule this phase produced, worth applying to phases 20-25:** when a
tool's risk depends on where data *goes* rather than what it touches, put the
answer on the component that knows it and read it per instance. Reading the
screen is SENSITIVE; sending a picture of it to a third party is DANGEROUS,
and section 30 does not let the second ride on the first's permission. The
mechanism is a `sends_pixels_offsite` flag, `any()`-ed across a chain (the
cloud link sits mid-chain in the shipped order, so a first-link reading would
understate a live upload), read via `getattr(..., False)` so it is a fact a
component may offer rather than a protocol member. Over-report rather than
under-report: a mis-high risk costs one confirmation prompt, a mis-low one
costs an action nobody approved.

**A test-writing hazard found here, likely to recur.** A caplog assertion on a
`logger.debug` line passed alone and failed in the full suite. Under pytest
the root logger already has a handler when `core.logger` is imported, so
`setup_logger` returns early on `hasHandlers()` and never calls `setLevel` -
`Aura.level` is 0 (NOTSET) - but any earlier test running
`apply_config_level` leaves it at INFO, and a DEBUG record is then dropped
**at the logger** before caplog's root handler sees it. Use
`caplog.at_level("DEBUG", logger="Aura")`, the idiom
`tests/test_error_visibility.py` already uses. A bare `at_level` only raises
root's level.

Verified: `tests/test_vision_chain.py` 25 passed, `tests/test_vision_tool.py`
29 passed, mutation battery **15 of 15 caught, 0 survivors**, full suite
**3113 passed, 3 skipped, 1 deselected in 42.61s** (+54 over 18.5's 3059,
exactly the two new files), Android **BUILD SUCCESSFUL in 59s** with
`testDebugUnitTest` genuinely executed rather than UP-TO-DATE.

### Phase 20 brief - Voice

Section 20 of the directive. **Inspect before building**: there is already
voice code in this repository (`tests/test_voice_cancel.py` exists, which
means a provider with cancellation exists), so the first move is to map what
is there and what section 20 actually asks for that is missing - the same
shape as phase 19, where the work turned out to be a regression plus one
missing half rather than a greenfield build. Do not rewrite the working path
(section 41).

Carry the phase 19 rules into it: if voice output can reach a third party
(hosted TTS/STT), that is a `sends_pixels_offsite`-shaped question about audio
and the risk must say so; if a voice action has a postcondition, section 11
forbids "the call returned" as evidence; and whatever becomes settable needs
its Hub widget recorded as phase 23 debt rather than silently omitted.

---

**Phase 11 - Memory 3.0 (section 17). PART 1 DONE, tier work still owed.**

Part 1 was not the tier work: it was a dead owner control found while
mapping the subsystem. `memory.recall` is settable, in `LIVE_PATHS`,
documented in the handler table, and has a Hub toggle - and had **four**
independent breaks, so the owner's value reached nothing. `memory_lines`
gated on its own defaulted parameter instead of the flag the builder
wrote; `_reapply_memory` fetched the pipeline through `services.memory`
when `Services` keeps it as a *sibling*; the handler sat in the
unconditional group so `applied` was reported even with no pipeline
present; and the legacy `KeywordRetriever`/`NullRetriever` choice needed a
restart. All four fixed, plus the two docstrings that asserted the
behaviour which never happened. Details and the mutation-test results are
in `progress.md`.

**The part worth carrying forward, because it nearly went wrong.** Fixing
the read alone would have been a fresh section 2 violation pointing the
other way. `config.yaml` documents `recall` as keyword search over the
transcript, "OFF ON PURPOSE"; the phone calls it "Use memory in replies -
look things up from past conversations" and files it under **privacy**.
The phone wins: section 2 makes the settings screen the owner's contract
surface, and a privacy reading beats a capability reading because being
wrong that way puts past conversation content into a prompt the owner
refused. **Standing rule: before honouring a config key more strictly,
read what the owner's own UI promises the key does.** Consequence recorded
loudly rather than buried - the default is `recall: false`, so episodic
recall is now off where it had been running against configuration, and one
toggle restores it. The value in `config.yaml` was not touched.

### Phase 11 part 2 - DONE: the semantic tier has a caller

`remember` (`tools/builtins/memory.py`), SAFE, registered only when a
pipeline exists, listed in `config.yaml`'s `tools.allowed`, and wired
through `launcher/services.py`. Aura can now keep a durable keyed fact
about the owner from a conversation; before this she could not, whatever
they told her. Full suite **2264 passed, 2 skipped, 1 deselected**.

Two things worth carrying forward:

- **A third standing rule.** *Mutate the wiring, not only the function.*
  Dropping the pipeline argument from the one `_build_tools` call in
  `launcher/services.py` left the whole suite green, because every test
  built its own executor. The unit was right and the deployment was
  wrong - the same shape as the two reach gaps before it. Test the
  composition root, not just the component.
- **A survivor that should survive.** Deleting `remember` from
  `config.yaml` also left the suite green, and that is correct: it is the
  owner's documented off switch. Do not pin the owner's editable file
  with a test - it would fail when they exercise a documented option.

### Phase 11 part 3 - DONE: a section 10 defect fixed, the learned tier deferred

Part 3 went looking for a procedural fact worth learning and found a live
recognition bug instead. `same_app` matches a display name against a
package narrowly, and seven of ten common apps missed - Messenger
(`com.facebook.orca`), X (`com.twitter.android`), Gmail, Play Store,
Phone, TikTok, Messages. A miss means `is_done` is False for a launch
that *did* happen, so the next tick re-issues OPEN_APP: the
`open_app open_app open_app` loop section 10 names, reached from the
recognition side rather than the verification side.

Fixed with `APP_ALIASES` in `brain/planner.py` - whole normalised name to
a frozenset of **exact** packages. Both halves matter. A substring
reading of `"x"` matches nearly every package on a device, and a loose
`messenger` entry would let `com.facebook.katana` through. That is the
dangerous direction: a false positive advances a plan past a step that
never happened. A missing entry only falls back to the heuristic, so a
name absent from the table is no worse off than before.

One fix reaches six call sites (`planner.py:317`, `recovery.py:148,178,189`,
`task_graph.py:204,234`), which is why the end-to-end test asks `is_done`
rather than `same_app`.

**A fourth standing rule: a narrow check has two failure directions, and
only one of them looks like a bug.** Everyone tests that a match is not
too loose. The miss - a correct action that cannot be recognised as
correct - shows up as an infinite loop somewhere else entirely, and the
function under test looks fine in isolation. Test both directions on
every predicate that gates whether work is considered done.

Two procedural-tier designs were discarded on the repository's own
evidence, not for difficulty:
- **Plan caching** - worthless. `plan_for` is deterministic and
  `test_the_planner_never_calls_a_model` pins its signature to
  `["request"]`. No round trip to save, no variation to learn.
- **A module-global alias resolver** - contradicts the repo's idiom.
  `set_tool_confirmation` is a *method* on `launcher/runtime.py:249`
  called from `launcher/cli.py:48`; injection here is a composition root
  handing dependencies to objects. `same_app` is a free function owning
  no state.

The genuinely learned tier - the device reporting which package it
actually launched for the name it was given - needs a device-contract
change and section 35 hardware to verify. **Not Implemented**, said
plainly rather than sketched.

Verified: `tests/test_planner.py` **50 passed**; full suite **2286 passed,
2 skipped, 1 deselected**. Honest red first (3 failed / 32 passed against
the unmodified planner). Both mutations caught - lookup deleted: 10
failed; lookup made a substring match: 3 failed, including
Messenger->katana and Gmail->messaging.

### Phase 12 - DONE: the bus grew an observer, and the agent learned to speak

Two parts, and the second one found a defect in itself.

**Part 1.** `events/bus.py` was already correct - lock, snapshot inside,
dispatch outside, handler exceptions swallowed, base-class subscription
reaching subclasses. What it did not have was a single subscriber:
`subscribe_all` had zero production callers while two docstrings claimed
logging used it. `events/log.py` is now that subscriber, attached
unconditionally in `launcher/services.py` before anything can publish.

Its design is decided entirely by section 30: **strings are denied by
default** and written as `<N chars>` unless the owning event opts the field
in through a `log_fields` tuple; `SAFE_FIELDS` names the six fields that
exist to be read; containers are named but never walked, which is what
keeps a tool's `arguments` out of the log without a per-tool blocklist; and
Enum is tested before `str` because `AuraState`, `Mood` and `Expression`
are all `(str, Enum)` and would otherwise redact `state=thinking` into a
character count.

**Part 2.** Phases 4-11 - state, planner, task graph, verification,
recovery - published nothing at all. Three events now say what moved:
`TaskStepChangedEvent`, `TaskFinishedEvent`, `TaskStuckEvent`, published
from `_announce` in `brain/conversation.py`, **edge triggered**. Per-tick
publishing was never an option: `plan_for` is pure and the graph is
rebuilt every tick, so a publish per tick would emit the same step forever
and put section 10's repeat loop onto the bus as noise.

**Fifth standing rule, from part 2's own bug:** *an empty value that means
two different things will eventually be asked to mean both at once.*
`task_node == ""` meant both "not started" and "finished", so the guard
`if was == node: return` silently swallowed the finish of any task a
device reported already complete on its first tick. The fix was not a
special case but a second reading - `had_plan`, taken before `set_plan`
writes it - so that *arriving* at "no current node" is distinguishable
from sitting there. When a sentinel is doing two jobs, add the missing
distinction rather than a branch for the case you noticed.

**Sixth standing rule, from the mutation run:** *an equivalent mutation
proves nothing, so find out why it survived.* `elif graph.is_stuck:` ->
`or True` left the suite green, and that is correct - `is_stuck` means
exactly "no current node and not finished", so the disjunct cannot change
an answer at that point. But asking why exposed that the stuck branch had
no test at all. The equivalence had been hiding a coverage hole. Two tests
now cover it, and a separate survivor (`index` replaced by a constant `0`)
turned out to be a missing assertion: the only test looked at a first
tick, where the right index really is zero.

Verified: `2313 passed, 2 skipped, 1 deselected`. Every non-equivalent
mutation caught, two of them only after the tests were strengthened.

### Phase 13 - DONE: the limits stopped resetting themselves

The brief below asked what already survives a restart and what silently
does not. The answer was that the device side was fine and the *owner's
limits* were not, so the phase turned out to be smaller than it looked and
in a different place.

**Already satisfying section 19, verified not assumed.**
`NotificationWorker` is periodic with the 15-minute floor,
`NetworkType.CONNECTED`, `ExistingPeriodicWorkPolicy.KEEP`,
`Result.retry()` backoff, and it re-checks its gates on every run rather
than trusting what was true when it was scheduled. `brain/agent_mode`'s
`absorb` re-sends the whole action history each tick, so a server restart
mid-plan loses nothing. No Kotlin needed changing.

**What did not survive.** Every proactive limit the owner can configure -
`max_per_day`, `cooldown_seconds`, `category_cooldown_seconds`,
`duplicate_window_seconds` with `similarity_threshold` - was derived from
a RAM-only `deque`. A restart handed back a clean slate, so "no more than
four a day" was a number in the settings file that nothing enforced
across the only event that matters to it. `proactive/ledger.py` now keeps
that history on disk, built on the `core/settings_store.py` pattern
(`{"version": 1}`, per-row validation, `.tmp` + `os.replace`), threaded
through `ProactivePolicy(ledger=None)` and supplied only at the
composition root. Proved end to end: four sends, restart, `sent_today: 4`,
fifth refused, and tomorrow allowed - a persisted ledger must not become a
permanent ban.

**Seventh standing rule:** *a limit derived from state that dies with the
process is not a limit.* Durability is not a feature of a module, it is a
property the guarantee demands of whichever layer is asked to keep it. The
same reasoning deleted `ProactiveEngine._greeted`: because `part_of_day`
is pure, the ledger already held that fact, so the dict was a section 8
duplicate *and* the copy that died. Deriving it removed the duplicate and
stopped post-restart re-greeting in one move.

**Eighth standing rule, from a defect that was wired, green and dead:**
*a default that matches the file above it can still exclude the primary
client.* `MemoryManager.last_said_at` was written with
`session_id: str = "default"`, copying `get_recent` on the floor above.
The phone supplies its own session id (`server/session.py`), so every
message the owner ever sent from their phone was invisible to it, and
every test passed because every test used the default session. Probe a new
query against a real-world input before believing green tests. Section 44
is exactly this: wired is not implemented.

**Left alone, with evidence.** `ProactiveEngine._rotation` also resets to
zero, so the composer re-offers identical text after a restart - and the
now-durable duplicate window refuses it. The guarantee moved to the layer
that can keep it, so the volatile counter is harmless. Checked by probe,
not waved away.

Verified: full suite **2354 passed, 2 skipped, 1 deselected**;
`tests/test_proactive.py` alone **106 passed**; **31 mutations across five
files, 31 caught, 0 survivors**. No Kotlin changed, so no Android build
was run and none is claimed.

### Phase 14 - DONE: the notification gate stopped forgetting

Section 20 is one sentence - *"Do not spam notifications."* Phase 13 had
just proved that sentence is a durability question rather than a threshold,
and the companion gate had the same defect one floor down. The inspection
the brief demanded found four things, all read rather than assumed, and
they did not all turn out to be bugs.

**The gate could not count past a restart.** `_last_notified`, `_last_chat`
and the 32-slot `_recent` deque were RAM-only, and the gate's clock is
`time.monotonic` - a reading that is not merely lost across a restart but
not *comparable* across one. So `max_per_hour`, `cooldown_seconds`,
`suppress_after_chat_seconds` and the duplicate window all came back as
"never notified". `max_per_hour` is documented in that very file as "a hard
ceiling that survives a bad relevance score"; an hour is longer than a
process, so it survived nothing of the kind.

**The owner had one control where the sibling gate has six.** `config.yaml`
carries six `server.companion.*` settings and `core/settings_store.py` made
exactly one settable: `enabled`. An owner who found Aura chatty could
switch her off and had nothing between that and the default (sections 2,
20). All six are now in `ALLOWED`, all six reapply live, and the bounds are
argued rather than picked: five minutes is the shortest cooldown, which is
what makes twelve the highest *reachable* hourly ceiling, and
`suppress_after_chat_seconds` floors at 0 because an owner who wants to be
interrupted mid-conversation is asking for less silence from their own
assistant and section 2 says that is theirs.

**`DUPLICATE_WINDOW` was a constant beside a setting.** The proactive gate
had made the same idea the owner's number. Two notions of "already said
this" that can disagree is one too many, so it became
`DEFAULT_DUPLICATE_WINDOW` with `duplicate_window_seconds` beside it.

**The outbox was re-examined and left alone.** `NotificationOutbox`'s own
docstring argues for volatility - 30-minute expiry, "the outbox is a queue,
not a receipt" - and that argument survives section 20. Persisting it would
have been reflex, not reasoning. `NotificationWorker.kt` needed nothing
either: it re-checks its gates per run and backs off. No Kotlin behaviour
changed.

**The design, and why it reuses a file format but not a file.**
`proactive.ledger.SendLedger` already answers the same four questions about
the same kind of history, so it is reused - but pointed at
`data/companion.json`, because one shared ledger would make each gate count
the other's sends and silently turn "four a day" and "six an hour" into a
single budget the owner never asked for (section 2). Monotonic still owns
every interval the rules compare; wall time only crosses the disk, and
stored rows are translated back into this process's frame *by age* on load,
because an age is the one thing about a monotonic reading that still means
something tomorrow. Negative ages (a clock that went back - DST, NTP, an
owner fixing their zone) are discarded rather than obeyed: trusting one
would place a send in the future and wedge the gate silently until real
time caught up.

`_last_notified` was deleted rather than persisted. It always equalled the
newest `_recent` entry and `deque(maxlen=...)` always keeps the newest, so
it was the same fact twice (section 8) - and deriving it left exactly one
thing to keep on disk. Presence reuses phase 13's
`MemoryManager.last_said_at` instead of inventing a second source.

**Ninth standing rule, and it cost two mutation rounds to learn:** *a path
in `LIVE_PATHS` and a handler that reapplies it are two different facts.*
The first mutation pass had 28 targets and 19 caught; two of the survivors
were the live-reapply pair, green because the tests asserted *membership*
in `LIVE_PATHS` rather than driving a PATCH and reading the policy
afterwards. That is the phase 11 `memory.recall` defect shape exactly, and
membership tests cannot see it. Three tests now PATCH a real runtime: one
proves the six numbers land on the live policy before the reply is sent,
one proves the next decision changes, and one proves that with no gate
running the reply says `restart_required` rather than lying in `applied`.

The other three survivors were age arithmetic that a single-entry test
cannot distinguish - an implementation that stamped every row "just now"
agrees with the correct code until two sends are far apart. Five tests were
added for those, including a clock that moves backwards.

Verified: full suite **2387 passed, 2 skipped, 1 deselected**;
`tests/test_companion.py` alone **110 passed**; **28 mutations, 28 caught,
0 survivors** after the tests were strengthened. Android:
`cleanTestDebugUnitTest` then `testDebugUnitTest` as separate invocations,
**359 tests, 0 failures, 0 errors across 26 suites**, timestamps checked
fresh rather than trusting UP-TO-DATE (section 34).

**Android fixture regeneration, and a correction to the standing rule.**
Six new settable paths moved `configurable`, so the live fixture and the
Kotlin count both had to move (50 -> 56). The recorded rule said to
regenerate through a keyless runtime; that is **not reachable on this
host**, and the reason is worth keeping: every provider constructor calls
`load_dotenv()` itself, so deleting `GEMINI_API_KEY` before
`init_runtime()` is undone the moment the provider is built. A keyless dump
attempted here came back carrying `key_source: "environment"` and a real
key's last four characters. The fixture was therefore updated by
**targeted edit** - which section 45 asks for anyway - and every fixture
diffed against HEAD: 52 insertions, 0 deletions across the three.

**Known issue, recorded rather than fixed.** The two speaking gates keep
independent budgets (proactive four a day, companion up to twelve an hour),
so section 20's "do not spam" is not enforced *across* both. Merging them
would silently change both of the owner's configured numbers, which section
2 forbids; it needs an owner-facing decision, not a quiet fix.

### Phase 15 - DONE: the category that could not fire

Section 21's binding sentence is *"AURA must not silently perform
arbitrary high-impact actions merely because it detected an event."* The
brief below predicted the interesting work would be the boundary test.
It was half right: the boundary held, and the inspection it demanded
turned up two real defects on the way to proving it.

**An entire category was unreachable in production.**
`Category.APPRECIATION` has a decision branch, a 24-hour cooldown, two
composer templates and **three passing tests**, and had never fired in a
real process. `ProactiveEngine` has taken a `memories` callable since it
was written; `launcher/services.py::_build_proactive` never passed one,
so `engine.memories` was `None`, `_gather_memories()` returned `()`, and
`if context.relevant_memories:` guarded dead code. Probed before editing,
not inferred.

What let it survive is the shape of its tests: all three hand-built
`ProactiveContext` with `relevant_memories=("...",)` - the one field
production leaves empty. They exercised the behaviour correctly and
constructed an input the composition root has no path to producing.
**Tenth standing rule: when a test hand-builds the input object, ask
which field production fills and which it leaves empty.** A field that
only ever has a value inside a test is a fixture describing a system that
does not exist. This is a third variety of the same section 44 failure -
phase 11 read a copied default, phase 14 asserted a declaration, phase 15
supplied unreachable state.

`proactive/memories.py` (new, 131 lines) closes it. `EpisodicMemorySource`
reads the `project` category - deliberately not `plan`, which
`proactive/tasks.py` already owns for reminders - within a window of
older than 24h and newer than 14 days, subjects of at least 12
characters, newest first, three at most, and hands back **the owner's own
stored sentence** rather than anything paraphrased. It is handed
`pipeline.episodic`, the same object the task source gets, and there is a
test asserting that identity rather than that both are non-None.

**An unlisted category had no throttle at all.** `proactive/decision.py`
states in prose that "a category that is not listed here cannot be sent
at all" - true of the decision engine, false of `ProactivePolicy`, which
took a plain `str` and looked the cooldown up with `.get()`, skipping the
whole per-category branch when it missed. Probed against the real policy,
five distinct messages one second apart: `"task"` sent **1 of 5**,
`"insight"` sent **5 of 5**. That is exactly the spam route the closed set
exists to prevent, and it was the open question the brief listed.

Fixed by `KNOWN_CATEGORIES = frozenset(category.value for category in
Category)` plus a refusal in `allows()`. **Refuse, not a default
cooldown**: inventing a plausible number for a category nobody declared
would hide the caller bug and ship the spam (section 44), where refusing
makes the sentence `decision.py` already claims actually true. It does
not restrict the owner (section 2) - an unknown category cannot come from
owner configuration, and every real category remains tunable. Derived
from the enum rather than retyped, because a hand-maintained copy of a
closed set is the thing that drifted in the first place.

**Seven tests had been leaning on that defect**, which is a large part of
why it lasted. `TestTheProactiveLimitsSurviveARestart` used `"check_in"`,
not a `Category` member and never was - and because an unlisted category
had no per-category cooldown, using one left whichever rule each test was
about as the only rule standing. The repair names a real category and
stands the others down **explicitly** with
`category_cooldown_seconds={CATEGORY: 0}`, because several of those tests
discard the reason string and a test asserting "not allowed" while a
different rule does the refusing passes for the wrong reason.

**The section 21 boundary held, and is now a test rather than a
coincidence.** Enumerated at a fully built root: `avatar.animation` (6),
`events.log` (1), `avatar.state` (1), `avatar.controller` (1),
`voice.tts.engine` (2), `server.notifications` (1). All render, speak, log
or queue; none acts. But `subscribe` takes any callable and `publish`
swallows handler exceptions, so the property was true by accident of the
subscriber list. `TestNothingOnTheBusActsOnAnEvent` walks **both**
`_wildcard` and `_handlers` - `subscribe_all` stores into a separate list
that `handler_count()` includes and a walk of `_handlers` alone misses,
which is why the first probe printed 8 against a count of 9 - and fails on
any owner module outside a declared presentation set. A second test
asserts the bus is not *vacuously* clean, since an empty bus would pass
the first one and that is the phase 12 defect, not safety. Verified to
fire against a synthesised `tools.autopilot` acting handler.

**A docstring that was wrong about direction.**
`proactive/context.py::seconds_since_user` claimed every consuming rule
uses the value "to hold back, never to fire". The active-conversation
guard does; greeting, task and appreciation all fire on a **large** value,
and infinity is the largest. Probed: `last_user_message_at=None` yields
`inf`, which yields `send: True, category: greeting`. Corrected to name
the firing path.

Verified: `tests/test_proactive.py` **126 passed** (106 -> 126); full
suite **2407 passed, 2 skipped, 1 deselected** (2387 -> 2407, zero
regressions); both red checks reading `2 failed, 2 passed` with the fix
backed out; **15 mutations, round one 14/15, round two 15/15 with 0
survivors.** The single round-one survivor was `if when is None: continue`
-> `pass`, an untested branch whose real cost is that one unreadable row
makes `oldest <= None` raise, which `_gather_memories` swallows, which
loses **every** memory rather than one. No Android change, so no Gradle
run is claimed.

### Phase 16 - Universal Tool System (section 22). DONE. Brief kept below, answers appended.

Section 42 order. Section 22 wants each tool to expose *name,
description, parameters, capabilities, permissions, `execute()` and
`verify()`*, and says plainly *"Tool implementations should not contain
provider/model-specific logic."* The last clause already holds - checked
this session, no builtin imports a provider - so this phase is about the
first clause, and it is largely an audit that turns into a small, honest
amount of building rather than a rewrite. The framework is real and good;
section 22 asks two things of it that are not there yet.

**What already exists, read rather than assumed:**

- `tools/base.py` carries `ToolProtocol` (three members: `name`, `risk`,
  `execute`) and the `Tool` ABC (adds `description`, `parameters`,
  `timeout`, `describe()`, `required_parameters()`). The docstring is
  explicit that the short protocol is the real interface and everything
  else is optional-by-absence, looked up with `getattr` where used.
- `tools/executor.py` is the single choke point, with five documented
  gates: enabled -> registered -> allow-listed -> risk-approved -> plain
  arguments. Gate 4 defaults to refusal; no confirm callback means no
  DANGEROUS/SENSITIVE tool runs. This is section 30/24 already satisfied,
  and must not be weakened.
- Four builtins: `current_time` (SAFE), `remember` (SAFE), `read_file`
  and `list_directory` (SENSITIVE), `open_application` (DANGEROUS).
- Every call already publishes `ToolInvokedEvent` and
  `ToolCompletedEvent` on the bus - so the phase 15 boundary test is
  what watches this surface.

**The two real gaps, and neither is a rewrite:**

1. **`verify()` is in the section 22 contract and nowhere in the tool
   layer.** `grep def verify tools/` is empty. Verification exists, but
   in `brain/task_graph.py` / `brain/recovery.py` (phase 7) and on the
   device - it verifies *actions in a plan*, not *tool calls*. The
   honest question this phase must answer from the repo, not invent: does
   a tool's postcondition belong on the tool (section 22's shape) or does
   the executor already have everything a `verify()` would check?
   `open_application` is the test case - it *already* resolves the
   executable before spawning and reports an accurate failure, which is a
   postcondition check written inline. Adding an optional `verify()`
   consulted by the executor after `execute()` would formalise that
   without duplicating the plan-level verifier. Optional-by-absence, like
   every other capability here, so no existing tool breaks.

2. **`capabilities` is named in section 22 and absent as a concept.**
   `risk` is the permission axis and is well-argued; `capabilities`
   would be the *declarative* answer to "what does this tool need"
   (filesystem, network, a running pipeline) that today is implicit in
   the constructor arguments. This needs a repository decision before any
   code: is a capabilities list load-bearing for anything, or is it
   documentation that duplicates `risk` and the allow-list? Do not add a
   field nothing reads - that is the "wired, green and dead" defect from
   the front, built on purpose.

**Inspect before building**, in this order: `tools/base.py` (the
contract), `tools/executor.py:195` (`execute`, where a `verify()` call
would land, after `_run`), `tools/builtins/apps.py:96` (the inline
postcondition that a formal `verify()` would generalise),
`brain/task_graph.py:182` (`is_finished`/`is_stuck`, so the tool-level
and plan-level notions of "done" are reconciled, not duplicated), and
`brain/recovery.py` (so a tool `verify()` that fails feeds the existing
recovery path rather than a second one).

Open questions for the inspection to answer rather than assume:

- Whether `verify()` should be **on the tool** or **derived by the
  executor** from what the tool returns. Section 22 says on the tool;
  the repo may show the executor already has the information, in which
  case section 44 (do not build what is not needed) argues for the
  smaller change and a recorded reason.
- Whether `capabilities` earns its place or duplicates `risk`. If it
  does not drive a gate, it does not get added.
- What "high-impact" from section 21 binds to here. Phase 15 left this
  open precisely because no event could reach a tool; the answer is the
  `risk` axis plus gate 4, and this phase should state that connection
  explicitly rather than build a second permission system.

Carried in and not to be re-discovered: the executor's five gates are
correct and section-30-critical; builtins are provider-clean; the tool
bus events are already the surface the phase 15 boundary test guards.

**Answers (the three open questions above, settled from repository evidence):**

- **`verify()` goes on the tool, getattr-optional, consulted by the
  executor.** Section 22 says on the tool and the repo agreed: the
  executor cannot derive a postcondition it does not know, and asking
  the tool keeps `_run` honest without a provider-specific branch. It is
  never added to `ToolProtocol` (`test_the_protocol_stayed_narrow` would
  fail); it joins `timeout`/`describe` as optional-by-absence. The
  executor asks it in `_verified()`, between `_run` and `_finish`, so a
  denied success reaches the bus as `ok=False` for free.
- **`capabilities` was NOT added.** Nothing would read it - `risk`
  drives approval, `tools.allowed` decides what runs, `parameters`
  declares the interface. A field with no consumer is the
  wired-green-and-dead defect, worse here because the name implies a
  measurement. Recorded as a decision; added when a phase brings a
  reader. (`recovery.py::RETRY_LIMITS = {}` is the same discipline.)
- **"High-impact" binds to `risk` + gate 4, and the answer needed no new
  system.** The phase found and closed a live section-11 defect instead:
  `remember` discarded `remember_user_stated`'s return and reported
  `ok=True` / "remembered ??? = Thien" while writing **zero rows** - a
  CJK key (`名前`) is silently lost on a Vietnamese owner's machine.
  Fixed at two layers (`execute` fails on a `None` write with the key
  named; `verify()` reads back through `value_of`). Full record in
  `progress.md` "Phase 16" and checklist row 16. Verification: baseline
  115 -> suites 232 -> full backend **2423 passed, 2 skipped,
  1 deselected**, +16, 0 regressions; **5 of 5 mutations caught**. No
  Android change, so no Gradle run is claimed.

### Phase 17 - Accessibility integration (section 42 order). DONE. Brief kept below, outcome first.

**Both owed defects are implemented and mutation-verified.** Full record in
`.claude/progress.md` under "Phase 17 - two authorities on 'done'".

    Android   378 passed, 0 failures, fresh ts 2026-08-25T00:44:52
    backend   2428 passed, 2 skipped, 1 deselected
    9 mutations, 9 caught, 0 survivors

What the phase settled, and the sentence to carry forward: **the server
owns "is the goal met"** (`plan_for` -> `task_graph.build` ->
`is_finished`/`is_stuck`, and the model ends a task with `complete`);
**the device owns only "may I stop without asking"**, which is a latency
optimisation and is allowed to be wrong only in the direction that costs a
round trip. Both device heuristics were wrong the other way and are fixed:
`shouldAutoComplete` now also reads the shared search vocabulary, and
`isSearchTaskComplete` now declines when a clause follows the search.
`AppInfo.activity` is populated from `TYPE_WINDOW_STATE_CHANGED`, so
`focus.screen` is real and the launch-only verification restriction in
`brain/task_graph.py` / `brain/recovery.py` can be revisited.

**What phase 17 deliberately left, for whoever picks it up:**

- **No device-to-server "task ended" signal.** `CognitiveState.clear_task()`
  still has no production caller; it fires only on a session or
  conversation change. So `state.plan` / `state.task_node` describe a
  finished task until the next request overwrites them, and
  `TaskFinishedEvent` fires only when the last tick the device happened to
  send already saw every node settled. The only channel is
  `repository.send(AGENT_TICK, ...)`, a full model turn - so this wants a
  non-LLM endpoint, which is a wire change on both sides and was not
  smuggled into this phase.
- **The launch-only verification restriction is now unjustified.**
  `brain/task_graph.py:40-60` and `brain/recovery.py:25-45` both say
  postconditions beyond a launch cannot be asserted "because the device
  never fills in the activity name". It does now. Those comments and the
  restriction they justify should be revisited with the field working -
  but that is section 11 work, and doing it in the same change that made
  the field real would have made a regression in either indistinguishable.

**The brief as it stood before the work, kept for the reasoning:**

Phase 17 is the first phase since phase 9 to touch Kotlin, so section 34
comes back into force: **`cleanTestDebugUnitTest` then `testDebugUnitTest`
as separate invocations**, freshness proven from the JUnit XML
`timestamp` attributes rather than from BUILD SUCCESSFUL, never trusting
an UP-TO-DATE task. `AGENT_SESSION.md` / the local agent harness in
`local_agent/` is uncommitted user work and must be preserved (section
45): `git status` and `git diff` before editing, no `git reset --hard`,
no `git checkout -- .`.

**The two defects this phase owes, carried from phase 15's known issues
and the phase-9 audit - to confirm against the repo before coding, not
to assume still-open:**

- **Device completion heuristics vs the graph.** The Android service
  decides a task is done three ways - `shouldAutoComplete` (~926),
  `isSearchTaskComplete` (~963), `isSelectionTaskComplete` (~983) - and
  the backend graph decides independently with `is_finished` / `is_stuck`
  (`brain/task_graph.py`). Today the device is the completion authority
  and the graph reconciles what it reports. The phase-17 job is to make
  those two agree on purpose rather than by luck: name which side owns
  "done", and make the other defer to it explicitly. This is a
  reconciliation, not a rewrite - neither side is wrong, they are
  unaligned.
- **`AppInfo.activity` is never set**, so `focus.screen` is permanently
  the empty string. Confirmed in the phase-9 audit. Any screen-aware
  behaviour is reading a field nothing populates - the wired-green-and-
  dead defect on the device side. Find where `AppInfo` is built from the
  accessibility event and set `activity` from the event's component, or
  record with evidence why it cannot be set there.

**Before writing Kotlin, read rather than assume:**

- `android/app/src/main/java/com/aura/companion/accessibility/AuraAccessibilityService.kt`
  is already heavily modified and uncommitted (405 changed lines, the
  open_app verification race and action safety from recent commits).
  Inspect the current state; do not reintroduce anything those commits
  removed.
- `AgentLifecycleTest.kt` is untracked user work - read it to learn what
  lifecycle contract is already asserted before adding to it.
- The section-11 device-verification world (`brain/recovery.py`,
  `task_graph.py`, `planner.py`) is the backend half of the completion
  story; phase 17 is where the Kotlin half is made to line up with it.

**Standing rule reinforced by phases 11/14/15/16:** when a test
hand-builds an input object, ask which field production actually fills.
`AppInfo.activity` is the device-side instance - a field a test could
set and production never does.

### Phase 18.1 - PC observation + window management. DONE.

Four tools, registered and **not enabled**: `system_information` and
`list_processes` (`tools/builtins/system.py`), `list_windows` and
`focus_window` (`tools/builtins/desktop.py`), joined through a new
`tools/factory.py::_pc_tools()`. 85 tests in `tests/test_pc_tools.py`;
backend `2513 passed, 2 skipped, 1 deselected`; mutation testing
`21 of 21 caught, 0 survivors` on the second battery and `12 of 12` on the
first. No new config keys - `tools.allowed` is still
`['current_time', 'remember']`, and a test reads `config.yaml` directly to
keep it that way (section 2: the owner must be able to enable these, and
must not find them already enabled).

**Four things worth carrying, because each was a decision rather than an
implementation detail.**

**Section 11, demonstrated rather than asserted.** `SetForegroundWindow`
returns zero and changes nothing under Windows' foreground lock, so "the
call did not throw" is exactly the sentence section 11 forbids resting on.
`execute` performs the action and says *"asked for"*; `verify` reads the
foreground window back and fails the call when the wrong window is in
front. A live probe showed `execute` returning ok=True and `verify` failing
1.50s later, and `test_the_executor_downgrades_a_focus_that_failed_
verification` drives it through `ToolExecutor` - a postcondition the tool
checks and the framework ignores protects nobody.

**A reading tool needs no `verify()`,** because a read's postcondition *is*
its return value. `system_information` and `list_processes` have none, and
that is deliberate, not an omission.

**`default_*_source()` returns `None`, not a mock.** Both source factories
were changed so the factory's existing absent-dependency rule applies: a
tool whose dependency is missing is not registered, so it is *missing*
rather than *present and broken*. `list_windows`/`focus_window` register
**together and share one source object** - two would be two enumerations of
the same desktop, and `focus_window` matching against a listing the owner
never saw is how the wrong window gets brought forward.

**Two defects the live machine exposed, which no mock would have.** This
desktop had two windows titled exactly `Settings`, so the ambiguity message
("name one of them more precisely") was impossible advice, and the failure
message read as nonsense (`'Settings' did not come to the front - 'Settings'
is still the active window`). Fixed with an optional `pid` parameter -
already visible in `list_windows` output - threaded through `_match`,
`execute` and `verify`, plus an `_as_pid` coercion.

**And one false claim of my own, deleted.** The `WindowsWindowSource`
docstring asserted that an undeclared `GetForegroundWindow` returns a
truncated handle. Measured: it does not. 203 windows enumerated, largest
handle `0x1202A0`, undeclared and declared calls returned the identical
value; Windows keeps USER handles inside 32 bits deliberately. Section 44
forbids inventing API behaviour, so the docstring now records the measured
truth - and the real version of that bug lives one module over:
`GetTickCount64` undeclared goes negative after 596.5 hours of uptime and
this machine measured 300.7, about twelve days of headroom. Both are now
structural guard tests, since neither is behaviourally observable today.

**Not exercised, recorded rather than glossed:** the real cross-window
focus switch under the foreground lock. The owner's foreground window was
an active fullscreen game and a real switch would have yanked focus out of
it, so the ctypes path was exercised on the already-front window (zero
visible effect) and the switch-fails case used `honour_focus=False` on the
mock.

### Phase 18.2 - controlled command execution (section 24). DONE.

`tools/builtins/commands.py` (~1090 lines), one tool, `run_command`,
`ToolRisk.DANGEROUS`. Section 24 verbatim: *"Do not give arbitrary LLM text
direct unrestricted shell execution without a controlled tool boundary."*
The model never supplies a command line. The **owner** declares named argv
lists in `tools.commands`; the model supplies the key plus values for
declared slots, one slot per argv element, `shell=False`.

**Verified, executed:** `tests/test_command_tool.py` **154 passed**;
backend **2667 passed, 2 skipped, 1 deselected**; Android
`cleanTestDebugUnitTest testDebugUnitTest --no-build-cache` **378 tests, 0
failures** (`:app:testDebugUnitTest` executed, not UP-TO-DATE); mutation
battery **30 of 30 caught, 0 survivors, 0 bad anchors**.

**Where the model's text stops being text.** Fifteen payloads (`a && b`,
`$(echo hi)`, `` `echo hi` ``, `%PATH%`, `quote " inside`, a literal
newline, `--looks-like-a-flag`) each come back from a real program as
**exactly one** `sys.argv` entry with the punctuation intact. A value
cannot add an argument: `["--flag", "{value}", "--after"]` filled with
`x --injected y` arrives as three elements, not five. This is the property
the whole phase exists for, and it is tested against a real subprocess
rather than a mock, because a mock cannot demonstrate it.

**The batch-file refusal, and why it is narrower than first recorded.** The
brief said a `.bat` re-parses arguments under `shell=False` and a canary
was created. Re-measuring on Python 3.11.15 found that claim too broad:
`& && | ^ >` alone are neutralised (the CVE-2024-1874 fix is present). A
nine-payload battery found the precise live condition - **a literal `"` in
the value breaks out and the rest runs as commands**. Both quote-bearing
payloads created their canaries; `%CD%` and `%PATH:~0,12%` also expand
inside a batch file. The same payloads against a real `.exe` arrive in
`sys.argv` byte-for-byte with nothing created and nothing expanded, and
`.cmd` behaves identically to `.bat`. So the refusal stands and is
scoped: a resolved `.bat`/`.cmd` **with a fillable slot** is refused before
anything is spawned; with no slot it runs, because there is no
model-supplied text for cmd.exe to re-parse. `shutil.which` resolves `npm`,
`npx` and `code` to `.CMD` shims on this machine, so this is the common
case rather than the exotic one. The test uses the payload that actually
created a file and asserts the canary does not exist.

**A defect the tests found in my own code, worth carrying.** The
leftover-brace check refused any argv still containing braces after
substitution. That breaks three ordinary commands: `find . -exec rm {} \;`,
`grep -E "a{2,3}"`, `jq "{name: .n}"`. The reasoning behind the refusal was
also wrong - passing `{pattern}` literally does not "run a command nobody
wrote", it just performs the wrong search, and the braces are the *owner's*
text, so section 24 does not apply. Replaced with `NEAR_SLOT`, which warns
only when the brace content reads as a misspelled slot (`{ pattern }`,
`{my-pattern}`) and says nothing for ordinary program syntax. No refusal.
This is the section 2 shape: warn, do not override.

**A section 30 exposure path nobody asked about, closed.**
`core/credentials.py` deliberately puts stored keys into `os.environ`, so a
child process inherits them - and this tool's output goes into the
transcript, which section 30 says a key must never reach. `_child_environment`
imports `brain/router.py::PROVIDER_KEYS` and
`core/credentials.py::SECRET_ENV_VARS` **at call time** so a provider added
later is covered without an edit here, plus a pattern sweep
(`KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH`) for the owner's other
secrets, with exactly one documented exception: `SSH_AUTH_SOCK`, which is
the path of a socket rather than the contents of one and without which
`git` over SSH stops working. The end-to-end test asks the child itself
whether anything credential-shaped arrived, and whether the *value* arrived
under some other name.

**Three mutation survivors, each a real gap rather than an equivalent
mutant.** Recording these because the diagnosis is the useful part:

1. *"a command line string is split instead of refused"* survived because a
   string is also caught by the generic "not a list" branch - so the
   command was still refused, with the useless message instead of the
   actionable one. Fixed by asserting the *message*: `applications` accepts
   exactly that shape, so writing a command line is the likely mistake and
   the message must name the fix.
2. *"credential scrub keeps Aura's own names"* survived because every name
   in `PROVIDER_KEYS`/`SECRET_ENV_VARS` happens to contain `KEY` or `TOKEN`
   and the pattern sweep catches it anyway. The mechanism that matters - a
   *future* provider being covered without an edit - was verified by
   nothing. Fixed with a test that monkeypatches in a name the pattern
   would miss (`ACME_SESAME`), so only the live import can withhold it.
3. *"run_command registered with no declarations"* survived because
   `RunCommandTool({}).available` is already empty and the inner check
   blocks registration either way. What the outer `if commands:` gate
   actually decides is whether a stock server logs *"none of the 0 declared
   command(s) could be used"* on every startup - a warning about a problem
   the owner does not have, in a log where a real one has to stand out.
   Both the test and the code comment now say that.

**The two bounds, and which is outer.** The executor bounds `execute` on a
daemon thread it cannot kill, so `self.timeout = longest + KILL_GRACE +
5.0` puts the tool's bound *outside* every command's own bound. Inside, the
thread would be abandoned mid-kill and the process it was killing would
survive with nobody watching. `timeout: 0` makes both bounds unbounded
rather than being clamped, because `tools/timeout.py` documents 0 as "no
bound" and clamping it would be the silent override section 2 forbids - it
is warned about instead.

**Temp files, not pipes, measured again.** A batch file leaving a `ping`
grandchild returned after **29.25 s** under `subprocess.run(timeout=1.0)`
because the grandchild held the inherited stdout pipe. Temp files +
`wait(timeout)` + `kill()` + `taskkill /F /T /PID` returned in **1.08 s**
with output captured. The Windows test asserts under 10 s and then reads
`tasklist` to confirm no `PING.EXE` survived; the portable test runs three
overruns in a row so leftovers would accumulate.

**No `verify()`, and the reason is the opposite of the usual one.** The exit
status *is* the postcondition, read back from the world - not "the command
executed without throwing", which section 11 rejects. Re-asking would mean
re-running the command, doubling the side effects to learn nothing new, and
this tool does not know what any given command was supposed to change. A
test asserts the absence is deliberate.

**Nothing is enabled.** `config.yaml` ships `commands: {}`, `allowed` is
still `['current_time', 'remember']`, `auto_approve` is still `['safe']`,
and `run_command` is not registered with the shipped config. Four tests
read `config.yaml` **from disk** rather than through the loader, so a
defaulting bug in the loader cannot hide a silent enable. `tools.commands`
is deliberately absent from `core/settings_store.py::ALLOWED` (56 entries;
its only `tools.*` keys are `enabled`, `auto_approve`, `timeout`) - a
settable `commands` would let anything holding the bearer token declare
`["cmd", "/c", "{x}"]` and then fill in `{x}`, which is arbitrary shell
execution reached through the settings API instead of through the tool
boundary. Guarded on both sides: a Python test on `ALLOWED`, and a Kotlin
test that the live document's `configurable` list contains no
`tools.commands` prefix.

**The device now sees it.** `core/config.py` gaining `commands: {}` surfaced
`effective.tools.commands` in the settings payload, which failed
`test_fixtures_match_the_routes`. Regenerating fixtures needs
`AURA_WRITE_ANDROID_FIXTURES=1`, which must never run on this host, so the
one field was added to
`android/app/src/test/resources/live/settings.json` by targeted edit.
`ToolsConfigDto` gained `commands: Map<String, JsonElement>` - raw JSON
because the server accepts two shapes and this screen only displays it, and
parsing it here would be a second implementation of a schema the PC owns.
Its docstring now names all four read-only capability grants. The live-
fixture test asserts the shipped server declares no commands and does not
allow `run_command`, so the owner would find out from their phone.

**Still owed on 18.2:** no Hub widget renders `tools.commands` - the DTO
carries it and nothing displays it yet. That is phase 23 work, and it is a
different kind of debt from the fourteen settable-but-unrendered paths
already recorded: this one is read-only display.

### Phase 18.3 - filesystem writes (section 24). DONE.

Four DANGEROUS tools in the module that already held the readers,
`tools/builtins/filesystem.py` (137 -> 688 lines): `write_file`,
`append_to_file`, `create_directory`, `delete_file`. All four have a real
`verify()`, which is the first place in the PC layer where the
postcondition is both readable and cheap.

**The decision the phase turns on: a reading grant is not a writing
grant.** `tools.writable_paths` is a second list, read separately, and
deliberately not defaulted from `tools.allowed_paths`. An owner who
listed a folder so Aura could look something up did not thereby say she
may overwrite it, and inheriting the read roots would have handed them a
capability they never asked for - the "already made" decision section 2
forbids. An owner who wants both lists the folder twice. Tested from both
ends: `allowed_paths` alone registers no writer, `writable_paths` alone
registers no reader, and with two different folders each tool refuses the
other's.

**A measurement that changed the code.** `_atomic_write` creates its
temporary with `tempfile.mkstemp(dir=str(target.parent), ...)`, and the
`dir=` is load-bearing rather than tidy: `os.replace` across volumes
fails on Windows with ERROR_NOT_SAME_DEVICE, because CPython does not
pass `MOVEFILE_COPY_ALLOWED`. Measured here - `%TEMP%` is on `C:`, the
repository on `D:` - so a temporary in the system temp directory would
have made every write to a folder on any other drive fail. `tmp_path`
cannot catch it, because pytest puts `tmp_path` under `%TEMP%`, so the
same-volume case always holds there; the mutation battery is what
surfaced it. Now covered by a cross-volume test that goes looking for a
second drive and skips when there is not one, plus a structural
assertion that always runs. `mkstemp` rather than `<path>.tmp` for a
second reason: the path came from a model, so `notes.md.tmp` may be a
file the owner already has.

**Two defects in my own tests, found by mutating my own code.** Of 22
mutations, 20 were caught on the first pass. (1) The temp-collision test
put its decoy at `notes.tmp` while the repository's own
`with_suffix(suffix + ".tmp")` pattern produces `notes.md.tmp`, so the
test named a file no plausible implementation would touch and could
never fail; it now plants both spellings. (2) The Kotlin assertions I
added would all have passed with a misspelled `@SerialName`, because
both path lists default to an empty list and every body I asserted
against was empty - so the inline body now carries a real writable root
and the test reads the value back. That also closed the same
pre-existing gap for `allowed_paths`, whose value that body has always
carried with nothing reading it. Confirmed load-bearing by misspelling
the `@SerialName` and watching the suite go red.

**Verified, actually executed:** `2786 passed, 3 skipped, 1 deselected`
from a `2667 passed, 2 skipped` baseline - 119 new tests in
`tests/test_filesystem_writes.py`, 0 regressions. The one new skip is
the symlink-escape test: creating a symlink on Windows needs a privilege
the runner does not hold, so the same escape is exercised by a
**directory junction**, which needs none and which `resolve()` sees
through equally - the junction is removed inside the test, because
`shutil.rmtree` follows one into its target. Android:
`cleanTestDebugUnitTest` + `testDebugUnitTest` executed,
`27 suites, 378 tests, 0 failures, 0 errors, 0 skipped`.

**Nothing is enabled.** `config.yaml` ships `writable_paths: []`, none of
the four names is in `allowed`, `auto_approve` is still `['safe']`, and
the guard tests read `config.yaml` **from disk** so a defaulting bug in
the loader cannot hide a silent enable. `tools.writable_paths` is
deliberately absent from `core/settings_store.py::ALLOWED`, for the same
reason `tools.commands` is: a settable one would let anything holding the
bearer token add `C:/` and then have `write_file` replace whatever it
liked - filesystem access reached *around* the tool boundary through the
settings API. Guarded on both sides, Python and Kotlin.

**Still owed on 18.3:** no Hub widget renders `tools.writable_paths`. The
DTO parses and displays nothing yet, joining `tools.commands` as
read-only display debt for phase 23.

### Phase 18.4 - screenshots (section 24). DONE.

**IMPLEMENTED.** One DANGEROUS tool, `take_screenshot`, plus the capture
backend it needed and a PNG encoder. 112 tests in
`tests/test_screenshot.py`, all 48 mutations caught, full suite
`2898 passed, 3 skipped, 1 deselected`.

**It needed a new capture backend, and finding out why fixed a live
defect.** The 18.4 brief assumed `vision.capture.ScreenshotCapture` could
be reused. It cannot: it wraps `mss`, `mss` is an optional dependency, and
it is **not installed on the owner's machine** (commented out in
`requirements.txt`). So `capture_screen: true` in `config.yaml` had been
silently doing nothing on this host - `_build_vision` tried `mss`, got
unavailable, and the pixel half of vision was unreachable code. Rather
than add a dependency (section 41), `GdiScreenCapture` was written beside
it: `GetDC` -> `CreateCompatibleDC` -> `CreateCompatibleBitmap` ->
`BitBlt` -> `GetDIBits` through ctypes, exactly as 18.1's `list_windows`
reads `user32`. `default_screen_capture()` prefers `mss` when present and
falls back to GDI, and `launcher/services.py` now goes through it. The
whole PC layer, 18.1 through 18.4, stays installable by doing nothing.

**The PNG encoder is stdlib.** `encode_png` in `vision/capture.py` -
`zlib` + `struct`, because PNG is four chunks and a deflate stream. Not
PIL: pillow is *present* here but is listed optional, and a screenshot
that needed it would be the one capability an owner could not use without
installing something. `OllamaVisionProcessor._to_png` still uses PIL and
was deliberately left alone (section 41); what it should eventually do is
fall back to `encode_png` when PIL is missing. Recorded, not done.

**Verification is where the real work was, because a byte count proves
nothing here.** `GetDIBits` returning `width * height * 3` bytes is
exactly the sentence section 11 forbids resting on: a vertically mirrored
image has the same length, and so does one with red and blue swapped.
Both are live risks in this code - a positive `biHeight` gives bottom-up
rows, and GDI hands back BGRX not RGB. So the pixels were checked against
PIL's `ImageGrab`, an independent implementation of the same capture:
**2400 sampled pixels, 2400 identical, worst channel delta 0**, while the
same reference flipped vertically matched only 90.3% and with red/blue
swapped only 4.9%. That comparison is a test, not a one-off measurement,
and the mutation battery confirms both defects are caught by it.

**`verify()` says what it can and refuses to say more.** It reads the file
back and parses the signature and IHDR, because "the write did not throw"
leaves a file behind when a disk fills or an antivirus rewrites it as it
lands. It does **not** claim the picture shows the screen: the screen has
already changed, and a second capture would be a different moment. Said
plainly in the docstring so nobody fills in the perceived gap later.

**Nothing is captured on a path that will be refused.** `execute` proves
the destination - containment, `.png` suffix, overwrite, parent exists -
before it reads a single pixel, and five tests assert a counting fake
capture's `captures == 0` on each refusal. A screenshot held in memory on
a failure path is a privacy leak that leaves nothing to find afterwards.
The mutation battery proves this is load-bearing: moving `_target` after
`capture.capture()` is caught by five tests.

**No new grant, no new setting.** The destination goes through
`tools.writable_paths` and through 18.3's own `_contained`,
`_atomic_write` and `_shown`, imported rather than reimplemented - two
containment checks can drift, and the one that drifts is a path escape.
An owner who granted no writable folder gets no screenshot tool at all:
registration is inside the existing `if writable:` block, gated a second
time on `default_screen_capture() is not None`, so a headless machine is
never offered a screenshot it cannot take. `core/settings_store.py::ALLOWED`
gained nothing.

**Which display is the owner's decision.** The index is configured;
`monitor` per call overrides it. The tool takes a *factory*
(`monitor -> ScreenCapture | None`) rather than a capture object,
because the alternative is mutating a shared backend between calls - and
that backend carries a warn-once flag whose whole purpose is to fire, so
reuse would suppress the warning for a second bad index. An index no
display answers to falls back to the primary and warns, never to index 0,
which is every monitor stitched into one wide image and looks exactly like
a hallucination from the outside.

**DPI awareness is thread-scoped and reversible.** The process-wide call
is permanent and is not a choice a screenshot should make for the whole
application; `SetThreadDpiAwarenessContext(-4)` was probed and confirmed
restorable before being relied on. Its benefit is **unverifiable on this
host** - single display at 100% - and that is recorded rather than
claimed.

**What the mutation battery found, which is the part worth reading.** 48
mutations, 43 caught on the first pass. Two of the five survivors were my
own mis-specified mutations; three were real test defects:

- `test_true_is_not_display_one` **could never fail.** It asserted
  `_resolve(monitor=True) == PRIMARY_DISPLAY`, and `PRIMARY_DISPLAY` is 1
  while `True == 1` in Python - so passing the bool straight through as an
  index satisfied the assertion. Replaced with a type check, plus the two
  cases where the bool guard is genuinely observable: `monitor: no` is
  `int(False)` is 0, which is a *different screen* from the fallback, and
  a bool must produce the configuration warning rather than quietly work.
- The DPI restore test **measured its baseline too late.** The first
  capture of the session leaves the thread aware, so with the restore
  removed a later measurement compared an already-changed value against
  itself and agreed. Replaced with a recording undo, plus a test that the
  undo runs on the failure path, plus one proving the undo is a real
  restore and not a no-op.
- Two mutations were reported as caught by `<collection error>` rather
  than by a named test. Cause: `monkeypatch.setattr(os, "name", "posix")`
  patches the real module, and `pathlib` reads `os.name` to choose a Path
  flavour - so an assertion that *fails* inside the patched window makes
  pytest raise `cannot instantiate 'PosixPath' on your system` while
  formatting the failure, an INTERNALERROR that aborts the session, names
  no test, and skips everything not yet run. A green run never notices.
  Replaced with a `not_windows()` helper patching `vision.capture.os`.

After those fixes: **48/48 caught, every one attributed to a named test.**

**Still owed on 18.4:** `OllamaVisionProcessor._to_png` should fall back
to `encode_png` when PIL is absent. The GDI DPI benefit is unverifiable on
this host. Multi-monitor `_monitors()` ordering is only testable against a
fake here - the primary-first sort is unobservable on a single display,
and that is stated rather than glossed.

### Phase 18.5 - keyboard and mouse input synthesis. IMPLEMENTED.

Section 24 is complete. `move_mouse`, `click_mouse`, `type_text` and
`press_keys` in `tools/builtins/input.py`, behind `SendInput` and
`SetCursorPos` through ctypes, registered by `tools/factory.py` sharing the
18.1 window source. Full record in `progress.md`; five things worth
carrying:

**The brief asked whether synthesised input needs a stricter gate than
DANGEROUS. The answer, written down as asked: no, and the reasoning is in
`_InputTool`'s docstring.** A fourth risk level would have to be learned by
the settings contract, the Android DTO, `config.yaml` and the Hub before it
protected anything, and it would protect nothing the owner cannot already
get by leaving `dangerous` out of `tools.auto_approve`. `ToolRisk`'s own
docstring already names "click" as its DANGEROUS example. The effort went
instead into the `window` guard, which the ladder genuinely cannot express:
*only act if this window is in front, and refuse - not skip - when that
cannot be checked.*

**Section 11 had a better answer than the brief expected.** Two
measurements, both taken before writing code:

- `SetCursorPos(2420, 1580)` on this 1920x1080 desktop returns **true** and
  leaves the pointer at **(1919, 1079)**. Silent clamping, reported as
  success. So the pointer postcondition is load-bearing, not decorative,
  and `_target` refuses off-desktop coordinates before moving.
- `SendInput` with a wrong `cbSize` returned 0 with `GetLastError()` at
  **0**. The accepted-event count is the only signal for that whole family
  of failure. `sizeof(INPUT)` here is **40**.
- `GetAsyncKeyState` was measured seeing a *synthesized* VK_SHIFT, which
  makes "no modifier this call pressed is still held" a genuine
  postcondition rather than the fake the brief warned about.

**The honesty limit that stands:** Microsoft documents that `SendInput`
blocked by UIPI is reported through neither the return value nor
`GetLastError`, so a full accepted count does not prove arrival at an
elevated window. `_sent`'s message names that rather than claiming
delivery. `type_text`'s verify checks only that the named window is still
in front and says in words that it does not claim the characters arrived.

**The guard validated itself before any test ran.** The e2e probe was going
to drive Notepad; its pre-check found **`cf-backup-codes.txt - Notepad`
already open** on this machine and refused. Notepad was abandoned for a
tkinter window we own - which is also the better oracle, since the widget's
contents are what the application actually received. Six oracles, FAILURES:
none, including `shift+q` producing `Q` (proving chord release ordering).

**Verification.** 161 tests in `tests/test_input.py`; mutation battery **45
mutations, 0 survived, 0 misapplied**, which found three real gaps: a
factory test blind to a second desktop enumeration, a `press_keys`
description that never said where the keys land, and no unit coverage at
all of the backend's event stream (added as `TestTheEventsSentToWindows`,
which asserts release order, KEYUP flags, the extended flag on both halves,
and UTF-16 surrogate pairs without touching a keyboard). Full suite **3059
passed, 3 skipped, 1 deselected in 44.84s**.

**The suite only reads the real machine.** `TestTheWindowsBackend` never
presses a key or a mouse button - the owner may be using this computer
while it runs. Its one write moves the pointer and restores it in a
`finally`.

Owed and recorded: the foreground lock is still unexercised on hardware
(carried from 18.1); UIPI blocking was not exercised; no Hub widget shows
these four tools are available (phase 23 display debt, with
`take_screenshot`, `tools.commands`, `tools.writable_paths`).

### Next: phase 19 - Vision (section 42 order)

Phase 18 is closed, so section 42 moves to **19 Vision**, then 20 Voice, 21
Plugins/Skills, 22 Diagnostics, 23 Settings/UI integration, 24 Performance,
25 Full integration / release candidate.

**Read before writing - much of this exists and section 41 forbids
rewriting it.** `vision/` already holds real work from earlier phases:
`vision/capture.py` has `ScreenCapture`, `GdiScreenCapture`,
`ScreenshotCapture` (mss), `encode_png`, `default_screen_capture()` and
`PRIMARY_DISPLAY`, all built and tested in 18.4. There is an
`OllamaVisionProcessor`. Establish what the *gap* is before building
anything: 18.4 delivered pixels to a file, and a vision **phase** is
presumably pixels to a model and back into the conversation.

**The deferred 18.4 item is settled, and it is settled the other way.**
That item was "`OllamaVisionProcessor._to_png` should fall back to
`encode_png` when PIL is missing", and the sentence justifying it - "PIL is
not installed on this host" - **was wrong**. `import PIL` gives 11.1.0 here,
and `pillow` is a **hard requirement** at `requirements.txt:11`, not an
extra. What is not installed is `mss`, which is a different dependency and
the one 18.4 actually routed around. So the fallback would be code guarding
against a state the requirements file forbids, and section 41 says not to
add it. Recorded as **decided, not deferred again**. The stale half is in
`requirements.txt` instead: lines 42-46 still list `pillow` under "Optional
extras" and claim "Vision with screen capture requires BOTH mss and
pillow", which 18.4 made false in both halves.

### What inspection actually found (measured, 2026-08-25)

Vision is not missing. It is wired end-to-end already: pixels reach a model
and a description reaches the prompt, on the desktop through
`GdiScreenCapture` + `OllamaVisionProcessor`, and from a phone through
`POST /api/screen/upload` + `CloudVisionProcessor`. So phase 19 is not a
build-from-nothing phase, and per section 44 it cannot be reported as one.
Four gaps were measured rather than assumed:

**1. Turning pixel vision on makes vision report *less*, not more.** Run on
this host against the real `config.yaml`:

    capture_screen=False  processor=WindowTitleProcessor    context=[screen] User is browsing the web in ...
    capture_screen=True   processor=OllamaVisionProcessor   context=None

Ollama is **not running here** (`WinError 10061`, connection refused), so
`describe()` returns `""`, and `VisionManager.refresh()` reads an empty
description as *no observation at all* - so `_context` becomes None. The
pixel processor **replaces** the title processor instead of layering over
it, which means the owner is invited by a config key to turn vision from a
working description into nothing. This is the spine of the phase.

**2. The cloud processor cannot describe a desktop frame.** Measured:
`CloudVisionProcessor._compact` on a raw-RGB `Frame` raises
`ProviderUnavailableError("Screenshot could not be decoded")`, because
`_compact` calls `Image.open(io.BytesIO(frame.data))` and raw RGB is not an
encoded image. An encoded frame works (2x2 PNG in, 334-byte JPEG out). So
the phone path works and the same processor is structurally unable to take
what `GdiScreenCapture` produces - and `_build_vision_processor` never
offers it a desktop frame anyway, so a desktop owner with a cloud key and
no Ollama daemon has no pixel vision at all.

**3. Section 2: the switch that matters is not settable.**
`core/settings_store.py::ALLOWED` carries `vision.enabled`,
`vision.cloud_model` and `vision.ollama_model` - but **not**
`vision.capture_screen`, which is what decides whether pixels are read, nor
`vision.min_interval`. The owner can change which vision model answers and
cannot turn looking at the screen on or off.

**4. Nothing lets Aura look because she was asked to.** All vision is
ambient: `get_context()` once a turn, throttled, and only when
`capture_screen` is on. Phase 18.4's `take_screenshot` writes a *file*.
Nothing turns pixels into a description on request, so "what is on my
screen right now" is answered from a window title even with a vision model
configured. Section 22's tool boundary is where that belongs.

### The shape, split per section 43

- **19.1** A description that degrades instead of going silent (gap 1), the
  cloud processor made able to accept a raw-RGB frame (gap 2), cloud vision
  reachable from the desktop behind an owner switch that defaults to off -
  a screenshot leaving the machine must not start happening because a key
  exists in `.env` (section 30) - and the missing owner controls (gap 3).
- **19.2** On-demand vision through the tool boundary (gap 4), a
  `ToolProtocol` with a real `verify()`, registered and not enabled.
- **19.3** Tests, a mutation battery, and the state files.

One thing deliberately **not** changed: a title-derived description is
reported with `source="screen"` today, which slightly overstates what was
looked at. That is pre-existing behaviour from before this phase, `source`
is set by the manager rather than the processor, and changing it would
reshape `VisionContext` for every consumer. Recorded, not touched.

**Confirm rather than assume, in this order:**

1. `git status` and `git diff` first (section 45). `local_agent/` and a
   dozen other paths are uncommitted user work.
2. What already routes an image to a provider - `brain/providers/`,
   `brain/router.py`, and whether any provider contract carries image
   parts today. The unified model contract (phase 2) is the thing that
   would have to know about images; check whether it already does.
3. Whether `config.yaml` has a `vision:` section, and what the Android
   settings contract exposes. A new capability with no owner control is a
   section 2 problem, and a new config key needs the Android fixtures
   (`android/app/src/test/resources/live/settings.json`) - which must
   **never** be regenerated with `AURA_WRITE_ANDROID_FIXTURES=1` on this
   host, because a real key sits in `.env`.
4. Whether the accessibility/agent path (phase 17) already screenshots the
   phone, and if so what it does with the image. Two vision paths that
   disagree would be worse than one.

### Phase 18 - Windows / PC Agent (section 24) - original brief, kept

Section 24's hard constraint, verbatim: *"Do not give arbitrary LLM text
direct unrestricted shell execution without a controlled tool boundary."*
That boundary already exists and must be reused rather than reinvented -
`tools/executor.py` and its five gates (enabled, registered, allow-listed,
risk-approved, plain arguments), where gate 4 defaults to refusal. Phase 16
built it and phase 16's record explains why `capabilities` was left out.

**Read before writing, and confirm still-open rather than assume:**

- `local_agent/` is uncommitted user work (section 45). `git status` and
  `git diff` before touching anything near it; no `git reset --hard`, no
  `git checkout -- .`. It may already be part of the answer here.
- `tools/base.py::ToolProtocol` is exactly three members and
  `test_the_protocol_stayed_narrow()` is the drift guard forbidding
  widening. A PC tool is a `ToolProtocol` with an optional `verify()`, not
  a new abstraction.
- `tools/factory.py::_builtin_tools` is where a tool joins the registry,
  and `config.yaml`'s `tools.allowed` is the owner's allow list. Section 2:
  the owner must be able to enable things freely through settings; a new
  tool must not be silently un-disableable, and must not be silently
  enabled either.
- Section 11 applies: a PC tool's `verify()` must not be "the command
  executed without throwing". Phase 16's `RememberTool.verify()` reads the
  value back out of storage - that is the shape.

**Then, in section 42 order:** 19 Vision, 20 Voice, 21 Plugins, 22
Diagnostics, 23 Settings/UI, 24 Performance, 25 RC.

**Two items phase 17 leaves that are not phase 18:** the missing
device-to-server "task ended" signal, and the now-unjustified launch-only
verification restriction in `brain/task_graph.py` / `brain/recovery.py`.
Both are described at the top of the phase 17 section above.

### Deferred from phase 11 part 3 (section 17)

#### Tier survey, verified against the repository

Verified against the repository, not assumed:
- **episodic** - exists and is good (`EpisodicStore` + `RankedRetriever`,
  scored `0.6*relevance + 0.25*recency + 0.15*importance`, 14-day
  half-life, `_valid_now` keeps future-dated rows out unless the category
  is `plan`).
- **working** - exists but unnamed: `TemporaryContext`, 3h TTL, 12
  entries, never persisted. The open question is whether it is distinct
  from the conversation window or the same thing under two names.
- **semantic** - `user_model` is now reachable at runtime via the
  `remember` tool (part 2), so the tier can learn. Still fragmented
  across two other unrelated things called "fact": `user_facts`
  (CLI-write-only) and `companion.Fact` (never written in production).
  Consolidating is the work; deleting owner data is not (section 41).
- **procedural** - still does not exist as a tier. `procedural`,
  `consolidat*`, `decay`, `salience` are zero matches repository-wide.
  Part 3 fixed the one concrete procedural fact that was hurting
  (name -> package, via `APP_ALIASES`) and deferred the learned version,
  which needs a device-contract change and section 35 hardware.

Also confirmed: section 17's *"do not blindly save every conversation line
as permanent memory"* is **already satisfied**. `MemorySelector` applies
six hard rejections, a first-person requirement and a scored threshold
before anything is stored; the unconditional both-sides transcript write
is section 15's visible history, which is a different guarantee. Do not
"fix" that.

Two more claims from the subsystem map still to verify before relying on
them: no migration framework exists (table creation is split between
`init_database` and `init_pipeline_tables`, the latter called only when
`memory.pipeline` is on), and `memory/__init__.py` does not re-export the
Memory 2.0 half.

**Phase 10 - Time Awareness (section 16).** The inspection was right about
where to look and the answer was reach, not absence: `core/temporal.py`
already held `TemporalClock`, `TemporalContext`, `resolve_timezone`,
`describe` and the `TIME` prompt section, all of it predating this mandate.
Three gaps, all closed.

The CLI root had no clock. `core/app.py::Aura.__init__` built `ChatEngine()`
bare, and `ChatEngine` leaves `clock=None` deliberately so a bare engine
stays byte-for-byte the Sprint 4 prompt pipeline - which means the clock
arrives from a composition root or not at all. `launcher/services.py` always
builds one, so the server was fine and `python main.py` was not: whole
conversations with no CURRENT TIME section, and a model with no date in its
prompt invents one rather than declining. Fixed at the root only; the
documented `clock=None` default is untouched.

And `temporal.timezone` was readable but unsettable - in `core/config.py`,
read by `from_config`, reported in `effective` to the phone, and absent from
`ALLOWED`. A control the owner can see and cannot move is what section 2
rules out, and the deployment the key exists for is exactly the one that
cannot edit `config.yaml`. Now settable (`_timezone_name`, `ALLOWED` 49 ->
50), live-applied through `LIVE_PATHS` + `_reapply_temporal`, and refused
with a reason when the name does not resolve.

`TemporalClock.use_timezone` mutates in place rather than returning a new
clock, and that is the design: `launcher/services.py` hands one clock object
to six subsystems so the prompt's time and a stored memory's time cannot
disagree, and a replacement would move one of them and leave five behind.
The constructor and the setter disagree on purpose - a bad name at startup
degrades to system local, the same name through the setter changes nothing -
because the constructor has nothing better to keep and a running clock does.

The third gap was found during self-review and is the one that reached
furthest: **the Android agent-tick prompt had no time at all.** The machine
branch of `_prepare` passed no `temporal`, `build()`'s tick branch dropped
it, and `_build_agent_prompt` had never had the parameter. This is live and
not theoretical - `input_text` takes free text and the owner's request
arrives verbatim, so "hom nay" or "tomorrow morning" with no date in the
prompt is a date the model invents.

Closing it meant reversing an existing pin, which is worth knowing about
because the next session may hit the same shape. `test_memory_integration.py`
asserted a machine prompt has neither TIME nor MEMORY, and its own docstring
gave the rule: *every section that exists to make Aura sound like herself is
absent*. TIME does not satisfy that rule - it is a world fact, like the
DEVICE STATE the tick already carries. The two were pinned together because
they shipped together. So the test was read first, then narrowed to its
memory half (it lives in a memory integration file), renamed, and its
docstring now records the reversal and why. **The standing rule: when a test
blocks a change, read its docstring for the rule it claims to enforce and
check whether the assertion actually follows from it. Sometimes it does and
the change is wrong.** MEMORY stayed out, and that half is right.

Two things recorded rather than solved. Timestamps are naive local, so
changing zone re-dates existing memories by the offset delta; identical
whether applied live or at restart, so not a reason to demote it. And this
Windows host has no tzdata, so the owner can only set `UTC` here until
`pip install tzdata` - which is why the validator's error names the package,
and why one test skips instead of pretending to pass.

Verification: `2225 passed, 2 skipped, 1 deselected` (from 2201; 22 written
red first, 2 HTTP tests added in self-review). Android `classes=26 tests=359
failures=0 errors=0`, freshness read off the JUnit XML timestamps. No route
change was needed and that was proved by test, not by reading.

**One incident worth carrying forward.** Regenerating the Android live
fixtures with `AURA_WRITE_ANDROID_FIXTURES=1` rewrites **all three** of them
from the host's live provider state, and this host has a real Gemini key in
`.env` that dotenv loads at import time - so the masked tail of the owner's
key landed in two checked-in fixtures (section 30) and the live provider
chain broke the Kotlin test's keyless pin. Repaired by regenerating through a
runtime with every provider variable deleted *before* `init_runtime()`, not
by `git checkout` (section 45 forbids it, and it would have reverted phase
1's `custom` provider work). **Standing rule (corrected in phase 14): never run that flag on a host with
keys in `.env`. Deleting every provider variable before `init_runtime()` is
**not sufficient** - probed in phase 14: every provider constructor calls
`load_dotenv()` itself, which puts a deleted variable straight back, and a
dump taken that way came back with `key_source: "environment"` and the real
key's masked tail. A genuinely keyless runtime needs `load_dotenv` patched
to a no-op in `brain.providers.{gemini,groq,mistral,http_chat}`,
`server.main`, `core.config` *and* `dotenv` itself, before the delenv calls
(verified keyless: `configured: False`, `key_source: ""`). When the change is
small, prefer a targeted edit of the fixture - section 45 asks for that
anyway. Either way, diff every fixture against HEAD and confirm additions
only.**

#### Superseded: the phase 15 brief (kept for the reasoning)

Section 42 order. Section 21's constraint is the one sentence that matters:
*"AURA must not silently perform arbitrary high-impact actions merely
because it detected an event."*

Verified this session, not assumed: **every event subscriber in production
is a presentation consumer.** `avatar/animation.py`, `avatar/controller.py`,
`avatar/state.py`, `brain/mood.py`, `events/log.py`, `launcher/cli.py`,
`plugins/builtins/session_stats.py`, `server/notifications.py`,
`voice/tts/engine.py`, `voice/tts/streaming.py` - and that is the complete
list. Nothing on the bus opens an app, writes a file, or calls a tool. So
section 21 holds today *by construction*, and the honest question for this
phase is whether a property that is currently true by accident of what
happens to subscribe should be a test that fails when someone attaches an
acting handler.

Also already known and carried in, so the inspection does not rediscover
them:

- `tick()` is pull-driven. Aura can only consider speaking while a client
  polls; a 03:00 message to a phone that is not polling is not sent late,
  it is not sent at all. The engine's own docstring says so and
  `docs/DEPLOYMENT.md` records what provisioning would close it. Section 19
  forbids pretending otherwise.
- `ProactiveEngine._rotation` resets to zero, so the composer re-offers
  identical text after a restart - and the now-durable duplicate window
  refuses it. Checked by probe in phase 13, harmless.
- The two gates' independent budgets, above.

**Inspect before building**, in this order: `proactive/decision.py` (pure,
four categories, and the place a fifth would have to justify itself),
`proactive/engine.py:189` (`tick`, where all three vetoes meet),
`proactive/context.py` (what Aura is allowed to notice at all), and
`server/routes/notifications.py` (the poll that drives it).

Open questions for the inspection to answer rather than assume: whether
`ProactiveContext` can be assembled from anything that is not already
owner-visible; whether a category can currently be added without a
cooldown, which would be a spam route the closed set was meant to prevent;
and what section 21's "high-impact" means for the one acting surface Aura
does have - the tool system - given that no event can reach it today.

#### Superseded: the phase 14 brief (kept for the reasoning)

Section 42 order. 13 is done, and it ended holding a thread that leads
straight here.

Section 20's whole content is **"Do not spam notifications."** Phase 13
proved that sentence is not a prompt instruction or a threshold, it is a
durability question - and the notification path has the *same* defect the
proactive path just had, one floor down. Not assumed; read this session:

- `companion/policy.py` keeps `_last_notified`, `_last_chat` and a
  `_recent` deque of 32 in RAM, and its clock is `time.monotonic`.
  Monotonic time does not even survive a restart in a comparable form, so
  `max_per_hour`, `cooldown_seconds`, `suppress_after_chat_seconds` and
  the 30-minute duplicate window all reset to "never notified". The
  seventh standing rule applies verbatim.
- `config.yaml` exposes six companion settings
  (`relevance_threshold`, `cooldown_seconds`, `max_per_hour`,
  `quiet_hours`, `suppress_after_chat_seconds`, `enabled`) and
  `core/settings_store.py` makes exactly **one** of them settable:
  `server.companion.enabled` (line 531). The proactive engine next to it
  exposes six. Section 2 says the owner configures Aura through settings;
  five of these are owner-facing tuning that the owner cannot reach.
- `DUPLICATE_WINDOW = 1800.0` is a module constant in `companion/policy.py`
  while the proactive side made the same idea a setting with a
  `similarity_threshold` beside it. Two notions of "already said this"
  that can disagree.
- `server/notifications.py`'s `NotificationOutbox` is in memory and dies
  with `ServerRuntime`. Its own docstring argues that this is right -
  "notifications are worth minutes, not days", 30-minute expiry - so
  durability here is a *decision to re-examine against section 20*, not an
  obvious bug. Re-examine it; do not reflexively persist it.

**Inspect before building**, and in this order: `companion/policy.py`
(the gate), `companion/engine.py:167` (the only `note_notified` caller),
`server/notifications.py` (the outbox), `server/routes/notifications.py`
(the poll, which also drives `consider_proactive`), and
`android/.../work/NotificationWorker.kt` (which is already correct - it
re-checks every gate per run, backs off, and needs nothing).

Open questions for the inspection to answer rather than assume: whether
the ledger from phase 13 should be reused for companion sends or whether
one shared send-history belongs under both policies (they ask the same
four questions of the same kind of history, which argues for one home);
whether `time.monotonic` can be kept for intervals if the durable record
is wall-clock; and what the *device* does with two notifications that
arrive in one poll after an outage, which is the spam case section 20
most plausibly means and which no code currently considers.

#### Superseded: the phase 13 brief (kept for the reasoning)

Section 42 order. 12 gated 13-15 and is now done, so the bus is available
to build on rather than to build.

Section 19 comes with its own hard limit, quoted because it is the part
most easily wished away: **"Do NOT pretend an Android application can
bypass OS restrictions."** Doze, App Standby, background execution limits
and OEM battery managers are real, and a design that assumes a process
lives forever is a design that will be reported as "Aura stopped
answering overnight". So the honest target is not a process that never
dies but a system that **survives being killed** and says so.

**Inspect before building.** There is already a foreground-service notion
on the device side (the Accessibility Agent runs as one), a
`CompanionNotificationEvent` with an outbox, and a proactive engine that
already publishes. Read all three before adding anything: the question is
what already survives a restart and what silently does not.

Open questions for the inspection to answer rather than assume: whether
the phone's existing service is foreground, bound or neither; whether the
outbox is durable across process death or in-memory; what happens to a
task graph mid-plan when the process dies (phase 12's `TaskStuckEvent`
suggests the answer is "nothing tells anyone"); whether any wake or alarm
scheduling exists; and on the desktop side, whether `launcher/` has any
notion of running unattended at all or only of serving requests. Section
20's "do not spam notifications" and section 21's ban on silent
high-impact actions both constrain what a revived background process is
allowed to *do* on waking, which matters more than the waking.

#### Superseded: the phase 12 brief (kept for the reasoning)

Order comes from section 42: 12 is the Event Bus, and 13-15 (Background
Service, Notifications, Proactive) all sit on top of it, so it is the
gating piece for four phases rather than one.

**Inspect before building, as every phase before this one.** A bus almost
certainly exists in some form - `build_services` already threads a `bus`
into `_build_tools`, and tools take an `events=` argument - so the first
move is to read what that object already is, who publishes to it, who
subscribes, and whether anything crosses the process boundary to the
phone. Do not write a second bus beside a working one.

Open questions for the inspection to answer rather than assume: whether
the existing bus is synchronous or queued; whether a subscriber that
raises can take down a publisher; whether events survive a restart or are
purely in-process; and whether the Android side has any event notion at
all or only request/response. Sections 20 and 21 both constrain what the
bus is allowed to trigger - "do not spam notifications", and no silent
high-impact action merely because an event fired - so the subscriber
contract matters more than the publish path.

#### Superseded: the phase 11 brief (kept for the reasoning, not the plan)

Section 17 asks for four tiers - working, episodic, semantic, procedural -
and pairs them with one prohibition that is really a design constraint: "Do
not blindly save every conversation line as permanent memory."

**Inspect before building.** Memory is one of the oldest subsystems here and
it is already substantial: `ChatEngine` owns it, `RankedRetriever` already
ranks with recency borrowed from the shared `TemporalClock` (phase 10's
change reaches it), and `_remember` already decides what gets written. So the
first move is to find out which of the four tiers exist under other names,
which are genuinely absent, and where the "blindly save everything" line
currently sits - not to write a new memory system beside the working one.
Search for the retriever, the store, the writer and their tests, and read
what the write path already filters, before changing anything.

Open questions the inspection should answer, not assumptions to implement:
whether working memory is distinct from the conversation window or the same
thing under two names; whether anything today is procedural (how to do a
thing) as opposed to episodic (what happened); whether promotion between
tiers exists in any form; and whether the phone has any notion of memory at
all or only of the transcript (phase 9).

## Phases 1-9 (superseded by the section above)

Phases 1-9 of the section 42 order are IMPLEMENTED. Details are in
`.claude/progress.md`; this section carries only what the next session needs.

**Phase 9 - Conversation Persistence (section 15).** The requirement is one
sentence: the chat UI must not lose visible history when the application
closes. The inspection that had to come first found the phone rendering from
memory that died with the process - `ChatViewModel._state` was seeded with an
empty `ChatUiState()`, there was no history route on the client, and no local
store. The half that mattered more was invisible: `AuraRepository._sessionId`
died with the process too, so restoring the bubbles from anywhere without
also restoring the id would have shown a transcript beside a server session
that had never heard of it.

Both halves now survive, and by one object so they cannot drift. `data/chat`
holds `Transcript.kt` (the contract - `Transcript` for the ViewModel,
`SessionStore` for the repository, each with a `None` no-op), a pure
`TranscriptCodec` that is JVM-testable because it touches no Android type, and
`TranscriptStore` - one `EncryptedSharedPreferences`-backed implementation of
both interfaces, in its own preferences file so per-turn churn does not
rewrite the URL and token. `ui/chat/ChatTranscript.kt` maps to and from
`ChatMessage`, and lives in `ui` because `data` may not import it. Both new
constructor params default to the `None` objects, which is why every existing
call site and test compiled untouched.

Three guarantees are structural rather than remembered: `StoredMessage` has
no `streaming` column so a half-arrived reply cannot come back looking live;
`keep()` returns early on an empty projection, because every route to an
empty screen other than `newConversation()` is a failure and writing it back
would destroy the transcript rather than fail to load it (section 41); and
`adopt()` is now the only writer of the session id, where `send()` used to
set it directly. That last one changed behaviour slightly - a blank session
id from the server is no longer adopted.

Two things deliberately not deleted: `SettingsViewModel.save()` and
`disconnect()` clear the session id but keep the transcript, because the
transcript is what the user said and tidying an inconsistency they can see
for themselves is not worth destroying it. And `MAX_MESSAGES` is 200 - past
that the oldest messages stop surviving a restart, which is a real recorded
loss, not an oversight.

Verification: 359 Android tests, 0 failures, across 26 classes (+29 from
330), JUnit timestamps checked against `date -u` rather than trusting BUILD
SUCCESSFUL. APK rebuilt and `TranscriptStore` confirmed inside the dex.
Python unchanged at 2201 passed, 1 skipped, 1 deselected. Two deliberate
mutations proved the tests bite: 6/10 failed, then 1/11.

### Next: phase 10 - Time Awareness (section 16)

Section 16 has two prohibitions and they point in opposite directions:
"Never rely on the model guessing the current time" and "Do not hard-code
dates". **`TemporalContext` already exists in this repository** - it is one
of the frozen projections listed in the architecture notes - so the first
move is to read what it already provides and where it is consumed, not to
write a new clock. The likely gap is reach rather than absence: whether every
reply path actually receives the time, whether the Android side has any
notion of it at all, and whether a stale timestamp resolved once per session
can drift across a long conversation. Find out which of those is true before
changing anything.

## Phases 1-8 (superseded)

Phases 1-8 of the section 42 order are IMPLEMENTED. Details are in
`.claude/progress.md`; this section carries only what the next session needs.

**Phase 8 - Persona Engine + Validator (sections 13/14).** The engine already
existed: `brain/persona.py` carries pronoun registers, context modes, dials
and addressing preferences, and was wired through to a per-turn PERSONA
section before this mandate started. What was missing was the half both
sections name in the same words - "prompt instructions alone are
insufficient". So the prompt stated the register and hoped, and a model that
agreed to cau/to and then wrote "may" in the third paragraph reached the user
uncorrected *and* got saved to the transcript, feeding the drift back next
turn as an example of how Aura talks.

`brain/persona_validator.py` closes that with one public function,
`validate(text, state)`. Its scope is exactly the promises `_pronoun_line`
and `RESTRAINT` already make to the model and not one word wider, because
enforcing an unstated rule would correct a model for obeying its
instructions. The register comes from `resolve`, never from a constant - an
owner writing in tui/bro gets "Bro thu lai di" - and `address.preferred` is
checked first, so section 2 holds: an owner who asked to be called "may" is
called "may". Code and quoted text are protected by construction, reusing
`style.hide_code` (promoted from private) rather than a second answer to
"what counts as code". Machine turns are exempt structurally: they build no
`_Turn`, so their persona is None and a JSON action comes back verbatim.

Two of `RESTRAINT`'s sentences are deliberately **unenforced** with the
reason in the module docstring - the repeated-opener rule has no safe
subtractive fix (deleting "Khong -" inverts an answer, and `style.py` already
strips the stance-free openers), and "never reach for a trend word" cannot be
judged without the sentence around it. A section 7 over-reach was also
declined: stripping model self-identification would make Aura lie to her
owner about her own configuration.

### Next: phase 9 - Conversation Persistence (section 15)

Section 15's requirement is one sentence: "AURA's chat UI must NOT lose
visible history when the application closes." The server side already
persists - `ConversationStore` is what `_remember` writes to and `history()`
reads from, keyed by session. So **inspect what the Android app does with
history before writing anything**: the question is whether the phone renders
from a local store, from the server on reconnect, or from memory that dies
with the process. Find that out first; the fix depends entirely on which.

Note that phase 8 made the transcript hold the *corrected* reply, which is
what makes persisted history safe to feed back to a model - a stored
uncorrected reply would be a drift example that survives a restart.

---

## Phases 1-7 (superseded by the section above)

Phases 1-7 of the section 42 order are IMPLEMENTED. Details for each are in
`.claude/progress.md`; this section carries only what the next session needs.

**Phase 7 - Verification + Recovery (sections 11/12).** The gap was not
missing verification - the device already does the real per-kind check with
bounded polls. The gap was that the *result* never crossed the wire in a
form the server could read: `completedActions` is appended only on the
verified branch, and failure travelled only as `last_action_error`, free
prose in five shapes with no `(kind, target)` in it. So `fail_action`,
`should_retry` and `enter_recovery` had sat in `core/cognitive.py` since
phase 4 with no production caller, and FAILED/BLOCKED/RECOVERING were
unreachable.

Closed by a sibling wire field. `AccessibilitySnapshot.failedActions`
carries `open_app(com.android.chrome) [FAILED x2]` in the same format
`completed_actions` already uses, defaulted so an older APK still
deserialises. `brain/recovery.py` holds the policy: `DEFAULT_RETRY_LIMIT`
is 2 because that is the floor the device already enforces
(`MAX_ACTION_ATTEMPTS`), pinned across languages in
`tests/test_agent_protocol.py`. `RETRY_LIMITS` is deliberately empty -
every entry would have to be justified against something, and today every
device action is a UI gesture whose ceiling the service sets.

Recovery *entry* is gated on `graph.is_stuck`, and that gate is the part
most worth not breaking: one foreground package cannot distinguish an app
that was killed from one behind a permission dialog, so an ungated
reconciler relaunches healthy apps every tick - manufacturing the exact
`open_app open_app open_app` loop section 10 exists to prevent. Closing is
ungated, because it must fire on the tick the app returns.

Two real defects fixed rather than described: the service's retry key was
`"${action.action}:${action.nodeId}"`, which is the literal string
`open_app:null` for *every* app, so two failed Chrome launches refused
YouTube's *first* attempt; and the three dead producers above now have
callers.

**Verified, not assumed:** `2152 passed, 1 skipped, 1 deselected in 19.56s`
(baseline 2093, +59, zero regressions). Kotlin: `cleanTestDebugUnitTest
testDebugUnitTest` BUILD SUCCESSFUL, 330 tests across 24 classes, 0
failures, both compile and test tasks EXECUTED. `assembleDebug` BUILD
SUCCESSFUL, APK 19,427,232 bytes. Red first: `ImportError: cannot import
name 'read_action_failures'`.

**What phase 7 does not claim:** the APK has not been installed or driven
on hardware, so `failed_actions` has never been observed crossing a real
wire. `invalidated` re-checks launches only, because `focus.screen` arrives
permanently empty (see below).

### Next: phase 8 - Persona Engine + Validator (sections 13/14)

Section 13 says prompt-only is "insufficient" and section 14 says "prompt
instructions alone are insufficient" - so this phase is the enforcement
layer, not more prompt text. `brain/persona.py` already exists from the
pre-mandate identity work; **inspect it before writing anything**, and
extend rather than replace. The validator has to correct pronouns
(AURA to "tớ", USER to "cậu"), refuse `mày`/`tao`/`ông`/`bà` unless the
owner has changed that preference, and damp `bro`/skull-emoji spam into
something contextual - while leaving quoted user text and code alone, which
section 14 states explicitly.

Machine turns must be exempt. `brain/agent_mode.is_machine_turn` is the
single predicate for that, and a validator that rewrote a JSON action would
break the parser.

### Carried forward, not forgotten

- **Phase 17 debt, three items, one now closed.**
  (a) Reconcile the device's completion heuristics (`shouldAutoComplete`,
  `isSearchTaskComplete`, `isSelectionTaskComplete`) with the graph's
  `is_finished`/`is_stuck` - the device still holds completion authority.
  (b) `AppInfo.activity` is never set, so `focus.screen` is permanently
  `""`, which is why postcondition re-checks cover launches only.
  (c) ~~`open_app:null` retry-key collision~~ - **fixed in phase 7.**
- **New known issue.** Node ids are per-snapshot walk indices
  (`node_$nodeCounter` in `AccessibilityNodeSerializer.kt`), so `(kind,
  target)` bounds catch repetition of the same target but not flailing
  across many ids on one screen. The fix belongs where the ids are minted,
  not in the retry policy.
- **Phase 23 UI debt.** The Hub has no graphical control for the five
  `llm.task_models.*` lanes or `llm.custom_base_url`/`llm.custom_model`.
  All seven are in `configurable` and settable via the settings PATCH API
  today. Deferred per section 32 (functionality before UI).
- **Section 35 live tests owed.** APK is fresh
  (`android/app/build/outputs/apk/debug/app-debug.apk`) but has not been
  installed or driven. The four mandated scenarios remain unrun, and
  submit's 3000ms budget is reasoned, not measured.
- **External input required.** The owner's AgentRouter base URL.

## JARVIS modernization: phase 4 done, phase 5 next (uncommitted)

Phases 1-4 of the section 42 order are IMPLEMENTED. Phase 4 is new work -
`grep -rln "cognitive\|CognitiveState"` found nothing in the project before
this.

**Phase 4 - Cognitive State.** `core/cognitive.py`. `CognitiveState` is the
mutable owner of what Aura is in the middle of; `snapshot()` hands out a
frozen `CognitiveSnapshot`, the same bargain `ProactiveContext` makes.
Tracks session, owner, conversation, intent, goal, plan, task node, focus
(application + screen), active tools, and every action as
pending/succeeded/failed with an attempt count plus a recovery scope. Time
is borrowed from an injected `TemporalClock` and never stored. An action is
identified by `(kind, target)`, so `has_succeeded("open_app", pkg)` is a
lookup rather than a model call; `begin_action` on succeeded work is a
no-op unless `enter_recovery` named that exact action, which is the
invariant that kills the open_app loop.

`CognitiveStore` keys one state per session behind a lock - not a field on
`ConversationManager`, whose own comment says per-turn state on a shared
engine "is a race, not a cache". Sweeps idle entries touch-before-sweep so
a live task cannot expire under its own reader.

`brain/agent_mode.absorb` feeds it from the tick, using only the
`kind(args) [VERIFIED]` format `formatActionHistory` actually emits and
`AccessibilityAgentTest` pins. `last_action_error` is deliberately not
parsed - five free-prose shapes, no recoverable target, and inventing one
is what section 44 forbids. Wired at `_prepare`'s machine branch; store
built once in `launcher/services.py`, after the clock it borrows.

**Verified, not assumed:** `1965 passed, 1 skipped, 1 deselected in 32.91s`
(baseline 1879, so +86, zero regressions). Proven red first each time.

**What phase 4 does not claim:** the state is written and readable, but
nothing acts on it yet. Planner (5), task graph (6) and the prompt line
that would say "already done, do not repeat" (17) are the consumers.

### Carried forward, not forgotten

- **Confirmed Android defect, next up.** `"submit"` is missing from
  `KNOWN_ACTIONS` in `AgentActionParser.kt`. Verified on all four sides:
  `brain/prompt_builder.py:444` offers it to the model,
  `AuraActionExecutor.kt:223` implements it, `AuraAccessibilityService.kt`
  lines 869/870/884 test for it in the completion heuristics, and the
  parser rejects it as unsupported - burning one of three parse failures.
  A model obeying the mandated section 23 flow (open → verify → locate
  search → focus → enter → **submit** → verify results) is refused. It
  also needs real verification: `verifyStateChange` has no `submit` rule,
  so it falls to `else` and gets one snapshot after 250ms - the same race
  class as the open_app bug just fixed, for which the bounded-poll fix
  pattern already exists.
- **Phase 23 UI debt.** The Hub has no graphical control for the five
  `llm.task_models.*` lanes or `llm.custom_base_url`/`llm.custom_model`.
  All seven are in `configurable` and settable via the settings PATCH API
  today. Deferred per section 32 (functionality before UI).
- **Android APK owed.** Section 34 requires `assembleDebug` because
  `ControlDto.kt` changed, then the section 35 live scenarios.
- **External input required.** The owner's AgentRouter base URL. The
  architecture is generic and configurable; nothing was invented.

### Next: phase 5 - Planner

Distinguish conversation from task execution:
USER REQUEST → INTENT → PLAN → TASK GRAPH → EXECUTION → VERIFICATION →
COMPLETION. The point of section 9 is that the LLM must not rediscover the
whole task from scratch after every action - which is now possible to
avoid, because phase 4 gives it somewhere to have remembered.

---

## JARVIS modernization: phases 1-3 done, phase 4 next (uncommitted)

Working the master directive's section 42 order. Status vocabulary and the
verification log are in `.claude/modernization-checklist.md`.

**Phase 1 - Provider / credential foundation: IMPLEMENTED.**
`brain/providers/custom.py` `CustomProvider`, an OpenAI-compatible provider
with no default URL and no default model. Key and endpoint resolve in one
constructor; nothing in the request path reads the model *name*, so
`llm.custom_model = claude-sonnet-5` on the owner's gateway cannot redirect
to Anthropic. `OWNER_DEFINED_ENDPOINTS` in `brain/router.py` turns an absent
endpoint or model into a named skip reason rather than
`initialization raised ValueError`. 33 new tests in
`tests/test_custom_endpoint.py`.

**Phase 2 - Unified Model Contract: IMPLEMENTED.** `brain/capabilities.py`
(`TaskClass`, `classify_task`, `CapabilityLLM` + `generate_for`), wired at
the single `_generate` seam in `brain/conversation.py`.

**Phase 3 - Model Router 2.0: IMPLEMENTED.** `brain/model_router.py`
`CapabilityRouter`, five owner-settable lanes, wrapped at the composition
root only when a lane is configured.

Verified: backend `1879 passed, 1 skipped, 1 deselected in 29.82s` (baseline
1817); Android `classes=22 tests=312 skipped=0 failures=0 errors=0` after a
clean, so freshly executed.

### Carried forward, not forgotten

- **Phase 23 debt.** No Hub UI controls yet for the five
  `llm.task_models.*` lanes or `llm.custom_base_url` / `llm.custom_model`.
  All seven are in `configurable` and settable via the settings PATCH API
  today. Deferred on section 32's "functionality first".
- **Android APK.** `ControlDto.kt` changed, so section 34 requires an
  `assembleDebug` before the final report, plus the section 35 live
  Accessibility scenarios.
- **External input required.** The owner's AgentRouter base URL. The
  architecture is generic and configurable; no URL was invented.

### Next: phase 4 - Cognitive State

Single shared state object (conversation, user, intent, task, goal, plan,
task node, application, screen, active tools, pending/completed/failed
actions, recovery state, time, timezone, session). Section 8 forbids
duplicating independent versions of this across modules, so the first job is
finding what already tracks pieces of it and giving those a single home
rather than adding a parallel one.

## Provider failure isolation: a dead primary no longer kills Aura (DONE, uncommitted)

The modernization mandate's named production failure - "entering an
Anthropic API key through AgentRouter caused AURA to stop functioning" -
reproduced from a minimal case and fixed at its structural cause.

**Root cause: an asymmetry in `BrainRouter._create_provider`, not a
provider bug.** Every *fallback* was built inside a try/except carrying a
comment that an optional provider must not become a mandatory one. The
*primary* was built inline and `None` raised immediately:

    primary = self._instantiate_provider(name, config)
    if primary is None:
        raise ValueError(f"Primary provider {name} could not be initialized ...")

So the provider the operator merely *selected* was fatal, while the
provider they configured as a backup was survivable. Reproduced: primary
`anthropic` with no key, `groq` and `mistral` both healthy in the chain ->
`ValueError` from the lazy `provider` property -> every message fails.
That violates mandate sections 5, 7, 8 and 44 at once.

**Fix:** both paths now go through a new `BrainRouter._build(name,
config)` which never raises and returns `(provider | None, reason)`. The
chain is assembled from whatever survives.

- primary dead + fallbacks healthy -> Aura answers through the chain,
  logged at ERROR naming the provider and the reason
- primary dead + nothing else -> still fatal (unchanged), but the message
  now names the real reason via `_skip_reason` instead of the blanket
  "missing API key or config"
- `llm.provider` is never rewritten: `provider_name` still reports what
  the operator chose, `active_chain()` reports what actually answers
- "no failover" warning now names `provider_names[0]` rather than `name`,
  so a dead primary does not send the reader to the wrong key

**Tests:** 6 new in `tests/test_provider_resolution.py`, each verified to
FAIL against HEAD's router before passing (reverted `brain/router.py`,
ran them, saw all 6 fail on the old `raise`). The pre-existing
`test_a_missing_primary_key_is_still_a_hard_failure` is untouched and
still passes - it covers primary-dead-with-no-fallbacks, which is
correctly still fatal.

**Separate defect found while running the mandated suite and fixed:**
`test_settings_contract.py::TestHealthSurvivesADeadProvider::
test_the_provider_error_message_is_not_in_the_response` asserted
`"401" not in response.text` over the whole health body, but
`runtime.uptime_seconds` is an unrounded `time.time()` delta - so ~1.2%
of health responses contain the digits 401 somewhere in their decimals
(measured: 1230/100000 over realistic uptimes; e.g. 7.330945879406765)
and the test failed for a reason unrelated to any leak. It failed once in
four runs during this session. Now scoped to the field that would carry
the status - `runtime.llm_provider == "unavailable (ValueError)"` - which
is *stronger* than the old substring check: verified by injecting a
deliberate leak into `_provider_chain_label` and watching it fail, then
restoring `server/runtime.py` byte-for-byte.

**Tests:** backend **1817 passed, 1 skipped, 1 deselected** across four
consecutive full runs (baseline was 1811/1/1; +6 new). No Kotlin file
changed.

**Still owed on this work item:** nothing in the router. The wider
mandate items NOT started are listed in progress.md - chiefly a
first-class AgentRouter/OpenAI-compatible-proxy provider (so a key and
its endpoint travel together), the persona output validator, Android
conversation persistence, and the planner/task graph.

## open_app verification race (DONE, uncommitted)

"mở youtube" relaunched YouTube: the first open_app launched it at the
ActivityTaskManager level, but verification ran 250ms after startActivity
returned and read the still-previous foreground package
(com.aura.companion) -> UNVERIFIED -> next agent step re-issued the same
open_app -> duplicate launch.

Proven from code: `executeActionWithRecovery` (AuraAccessibilityService.kt)
did `startActivity` then a fixed `delay(250)` then ONE `rootInActiveWindow`
snapshot -> `verifyOpenApp` identity check. YouTube took ~900ms to draw in
the field; the 250ms settle was shorter than the launch. No polling, no
bounded wait existed for open_app.

Fixed with bounded eventual verification, coroutine-based (no new
main-thread blocking - the agent loop already runs on
`CoroutineScope(Dispatchers.Main)` with suspend `delay`):

- `waitForForegroundPackage(target, timeout=2500ms, interval=150ms)` in
the companion - polls the active-window package until it IS the target
or the budget runs out; injectable clock/sleep so a JVM test drives it
deterministically; logs each sample + VERIFIED/TIMEOUT.
- `verifyActionOutcome` - open_app routes to the poll; every other
action keeps the original `delay(250)` + one snapshot (click/type/etc.
unchanged). Identity semantics preserved: success requires the target
package to actually be foreground - never "startActivity didn't throw".
- Already-foreground short-circuit BEFORE `startActivity`: if
pre-action package == target, return Verified without relaunching
(placed after the SafetyGuard check so blocked packages still block).
- Blank target falls back to the old generic path (any-package-change).

Tests: new `OpenAppForegroundPollingTest` (5 tests, fake clock/sleep):
already-foreground -> 1 sample, 0 sleep; delayed arrival -> eventual
success; never arrives -> bounded timeout; blank target -> no polling;
slow launch (2.2s) inside budget -> success. Full Android unit suite
308 tests / 0 failures; `assembleDebug` builds. Not installed on device
(this session has no attached phone) - the field reproduction from the
bug report is the validation owed next.

## Persona contract wired into the prompt pipeline (DONE, uncommitted)

The personality-overhaul brief's highest-leverage piece was dead code:
`brain/persona.py` (978 lines - pronoun registers, context modes, dials,
addressing preferences, `AuraPersona`/`NullPersona`/`build_persona`,
defensive `persona_of`/`render_of`) was referenced by no module, and
`PERSONA` was imported by `prompt_builder.py` but never emitted. Now
wired the same way `style` and `identity` are:

- `PromptBuilder._build_persona` + `persona` param; the section is
  emitted directly under PERSONALITY - the per-turn refinement of the
  personality description, in the system slot for every provider.
- `ConversationManager.persona` collaborator; `_compose` derives it per
  turn via `render_of(self.persona, persona_of(self.persona, history,
  user_msg))`. Register continuity survives provider fallback because
  the transcript does - a fallback model resolves the same style.
- `ChatEngine.persona` defaults to `build_persona(personality.persona)`;
  `DEFAULT_CONFIG` gained the section (enabled, `pronoun_style` pin,
  optional humour/brainrot ceilings read as caps in every mode).
- `split_prompt` and `split_prompt_to_messages` gained the PERSONA
  header - required, or the Anthropic/OpenAI-compatible adapters would
  have received the contract as user content instead of instructions.
- Agent `complete` message now uses `AGENT_VOICE` (the one allowed
  personality injection into the agent prompt).
- `REVISION` marker comment corrected: `brain/persona_guard.py` never
  existed; a revision pass is deliberately not built (brief Section 22).

Not done, per the mandate: no model/provider-specific persona branches
(the whole point), no persona guard / second generation pass, no changes
to providers, fallback chain, memory, tools, Android execution logic,
`prompts/personality.md`, `brain/style.py` or `brain/consistency.py`.

Tests: 33 new in `tests/test_persona.py`. Full Python suite **1811
passed, 1 skipped, 1 deselected, 0 failed**. `live/settings.json`
fixture regenerated with the persona block (env-specific key churn
reverted); Android `SettingsContractTest` 42/42 against it - the DTOs
drop `personality` via `ignoreUnknownKeys`, so no Kotlin change. Rest of
the Android suite untouched by this work (297 green in the open_app
session).

Remaining: nothing device-side. The unverifiable-by-unit-test question
is whether models actually follow the register line - that needs a real
model and a real conversation.

## open_app could never launch anything (DONE, uncommitted)

"mở YouTube" reached `Task timed out: maximum number of steps reached.`
on the physical phone, with every one of the ten steps returning
`POST /api/chat 200`.

**Root cause: Android package-visibility filtering, not the model.**
`AuraActionExecutor`'s `open_app` branch calls
`packageManager.getLaunchIntentForPackage(pkg)`, which resolves the
target's MAIN/LAUNCHER activity - a *package query*. Since API 30 queries
are filtered to what the manifest declares an interest in, and this
manifest declared nothing while `targetSdk` is 35. So the call returned
`null` for every third-party app, including a correctly named installed
one. `open_app` returned `false` on both `executeActionWithRecovery`
attempts, the loop wrote `last_action_error`, the model retried, and ten
steps went by without `startActivity` ever being reached.

Confirmed against developer.android.com/training/package-visibility:
an `AccessibilityService` is **not** among the automatic-visibility
exemptions, and starting another app's activity is explicitly allowed
*regardless of visibility* - so `startActivity` was fine and only the
query was blocked.

**Provider-independent.** The Gemini 429 / Groq 403 / Mistral failover in
the Render logs was concurrent, not causal. Gemini would have failed
identically. `/api/chat 200` only reports that the model call succeeded;
the failure was entirely device-side, after a valid parse.

**Ruled out by inspection, in the order asked:** malformed Mistral output
(not required - defect is provider-independent); action-schema mismatch
and tool/argument-name mismatch (`AgentAction.packageName` is
`@SerialName("package")`, exactly matching the AGENT RULES template);
parser rejection (`AgentActionParser` is deliberately tolerant -
`isLenient`, `coerceInputValues`, fence stripping, brace matching);
non-model iterations (only the `failedActionsCount >= 2` skip, which is a
consequence of the failure, not its cause).

**Fixed:** a `<queries>` MAIN/LAUNCHER intent in `AndroidManifest.xml`.
Chosen over `QUERY_ALL_PACKAGES` because it is exactly the question
`open_app` asks, needs no Play policy declaration, and does not expose
apps with no launcher entry.

**Also fixed:** `AuraAccessibilityService.failureReason` replaced the one
generic `last_action_error` sentence. `"Action open_app on null failed.
Target not clickable or not found."` was false twice over - `open_app`
has no target node - and named neither the package tried nor why it did
not launch, leaving the model nothing to correct. That is the remaining
way a genuinely wrong package name burns ten steps. Non-`open_app`
actions keep the original sentence verbatim, because those really do
reference a node from the tree the model was just shown.

**Regression test:** `OpenAppLaunchabilityTest` (5 tests). Verified to
fail before the fix - `AssertionError` at the `<queries>` assertion with
the block removed - not merely to pass after it.

**Build change that the test needed to be real:** `app/build.gradle.kts`
declares `src/main/AndroidManifest.xml` as an input to `Test` tasks. The
test reads the manifest at runtime, which Gradle cannot see, so deleting
the `<queries>` block and re-running reported `BUILD SUCCESSFUL` from
cache. A regression test that silently does not run when the file it
guards is edited is worse than none, because the green build is read as
evidence.

**Not done, per the mandate:** provider chain untouched, `maxSteps`
untouched, no retries added, action schema preserved, recovery design
preserved.

**Tests:** 297 Android / 20 classes, 0 failures (was 292 / 19).
44 backend agent tests pass; no Python file changed.

**Device verification still owed:** `open_app` has been proven to fail
for a structural reason and the structural cause is fixed, but a launch
has not yet been observed on hardware. Install and say "mở YouTube".

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


---

# CURRENT POSITION (2026-08-24)

## Closed: phases 1-6

Phase 6 (Task Graph) is IMPLEMENTED and verified. `brain/task_graph.py` gives
every step of a plan one of §10's seven node states, names the single node worth
working on now, and renders the result into the agent prompt. Full record in
progress.md; one-line status in the checklist.

Backend: **2093 passed, 1 skipped, 1 deselected in 19.14s** (2030 -> 2093, 0
regressions). Android not re-run because no Kotlin changed in phase 6.

The open question left by the phase 5 hand-off — whether the graph is a new
module or an extension of `core/cognitive.py`, since "something has to give" —
was settled by reading the repo: **nothing had to give.** All seven states are
derivable from facts `CognitiveState` already records, so node state is a
projection computed on demand, `set_plan` and `enter_node` are untouched, and
§8's ban on duplicated state is satisfied by construction rather than by
discipline.

Three things worth carrying forward:

- **`current_step` and `render_plan` moved out of `brain/planner.py`** into
  `task_graph.py`, and `_is_done`/`_same_app`/`_describe`/`_SATISFIED_BY` were
  promoted to public there for the graph to import. Planner now owns request
  parsing and step description only. Anything looking for "which step are we
  on" belongs in task_graph; two renderers would be free to disagree.
- **Only PENDING, SUCCESS and SKIPPED occur in production today.** RUNNING
  needs `begin_action`; FAILED, BLOCKED and RECOVERING need `fail_action` /
  `enter_recovery`. Nothing calls those. Phase 7 is exactly the phase that
  makes the other four real, which is why it follows this one.
- **`AppInfo` still never sets `activity`** (`AuraAccessibilityService.kt:195-202`),
  so `CognitiveState.focus.screen` is permanently `""` in production. This is
  why SKIPPED is restricted to launches: no other step kind has evidence to
  stand on. Still phase 17's to fix.

## Next: Phase 7, Verification + Recovery (§11/§12)

Mandate, verbatim on the two points that matter: verification "must not rely
only on: 'the command executed without throwing'", and recovery must "never
blindly repeat the same action forever. Add bounded retry policies."

### What already exists, read rather than assumed

- **`CognitiveState` already has the entire vocabulary, with no production
  caller.** `begin_action` (`core/cognitive.py:551`), `fail_action` (601),
  `should_retry(kind, target, limit=2)` (614), `attempts_for` (546),
  `enter_recovery` (646), `leave_recovery` (657), `recovering_from` (640).
  Phase 7 is the *caller* of these, not the author of them. Writing a second
  retry ledger beside them would be the §8 failure this project keeps
  avoiding.
- **`absorb` (`brain/agent_mode.py:156`) records successes only**, and says why
  in its docstring: `last_action_error` is free prose in five shapes, none
  reliably naming the action it refers to.
- **The five shapes, confirmed:** `parsed.reason` (`AuraAccessibilityService.kt:266`),
  the already-executed notice (283), "failed repeatedly (N times)" (291),
  "executed but UI did not change (N times)" (324), and `failureReason(action)`
  (336). The objection holds — none of these carries a `(kind, target)`.
- **The channel that does work.** `completedActions` gets an entry only inside
  the `ExecutionResult.Verified` branch (307), formatted by
  `formatActionHistory` (954) as `kind(args) [VERIFIED]` — the exact format
  `read_action_history` parses and `AccessibilityAgentTest` pins. Successes
  travel structurally; failures do not travel at all.
- **The device already bounds retries, in its own currency.**
  `failedActionsCount`, keyed `"${action.action}:${action.nodeId}"`, with a
  `>= 2` threshold checked at 287-293 and incremented at 319-326 (Unverified)
  and 334 (Failed). That bound happens to equal Python's `should_retry` default
  of 2 — the same number written twice, in two languages, agreeing by
  coincidence.

### Two real defects to fix, not describe

1. **The device's retry key collides for `open_app`.** `nodeId` is null for a
   launch, so the key is `"open_app:null"` for *every* app. Two failed attempts
   at Chrome and the next `open_app` — YouTube, a different app entirely — is
   refused outright with "failed repeatedly. Target is not actionable." One
   app's failures veto another's first attempt. This is squarely §12's bounded-
   retry concern, so it belongs here rather than in phase 17 where it was first
   recorded.
2. **Failure never reaches the server structurally.** The server cannot mark a
   node FAILED, cannot know an attempt was spent, and cannot enter recovery,
   because the only failure signal is prose addressed to the model. The graph
   can render all seven states; the wire supplies evidence for three.

### The shape that follows, to confirm against the repo before coding

Mirror the channel that already works rather than parse the one that does not.
`completed_actions` carries `kind(args) [VERIFIED]`; a sibling list carrying
`kind(args) [FAILED: reason]` would be a format **defined on both sides at
once**, which is a different thing from inventing an interpretation of free
text after the fact — the precise objection recorded in `absorb`'s docstring.
That is what makes FAILED, BLOCKED and RECOVERING live without guessing.

Open, and to be settled from the code rather than assumed: whether the retry
bound becomes one policy the server owns and the device asks for, or stays two
numbers that a cross-language test pins together (the `SEARCH_VERBS` precedent).
The second is cheaper and honest; the first is what §12 actually describes.

**This is the first phase since 4 to touch Kotlin, so §34 applies in full:**
`cleanTestDebugUnitTest` then `testDebugUnitTest` — an UP-TO-DATE task is not
evidence — plus `assembleDebug`.

## Carried forward, still owed

- **Phase 23 UI debt.** The Hub has no graphical control for the five
  `llm.task_models.*` lanes or `llm.custom_base_url`/`llm.custom_model`. All
  seven are in `configurable` and settable through the settings PATCH API
  today. Deferred per §32, functionality before UI.
- **§35 live device scenarios.** The APK exists
  (`android/app/build/outputs/apk/debug/app-debug.apk`, 19,427,232 bytes) but
  has not been installed or driven on hardware. Submit's 3000ms poll budget is
  reasoned, not measured.
- **Phase 17 owes** reconciliation of the device's completion heuristics
  (`shouldAutoComplete` 926, `isSearchTaskComplete` 963,
  `isSelectionTaskComplete` 983) with the graph's `is_finished` / `is_stuck`.
  The device remains the completion authority until then.
- **External input required.** The owner's AgentRouter base URL. The
  architecture is generic and configurable; nothing was invented in its place.

---

