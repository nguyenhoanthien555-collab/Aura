# AURA Phase 5B — Architectural Forensic Audit

**Scope**: Local autonomous runtime, durable tasks, capability-gap detection,
self-extensible tools.
**Date**: 2026-09-05 · **Branch**: `feature/aura-identity` · **HEAD**: `42ff374`
**Product code changed by this audit: NONE.** Per brief §34 this is an audit only.

## Evidence rules used

Every conclusion below carries a `path:line` citation. Two confidence markers
appear throughout, and they are not decorative:

- **[V]** — I read or grepped the cited code in this session. Load-bearing.
- **[H]** — harvested from an earlier automated sweep and **not** independently
  re-read. Treat as a lead, spot-check before acting on it.

Where a marker is absent the statement is a synthesis of adjacent [V] findings
and is labelled as inference in place.

One inherited claim was **falsified** during this audit and is corrected in §2:
the agent-runtime tool envelope does *not* flatten outcomes to a boolean. It
carries the error code; the *consumer* ignores it. The distinction changes the
size of the Phase 5B fix from a wire-format migration to a few lines, so it
matters.

## The one-paragraph finding

Phase 5B's two hardest semantic requirements — **timeout is UNKNOWN, never
FAILED**, and **never blindly retry a possibly non-idempotent mutation** — are
already *decided* in this repository, correctly, at both ends of the pipeline:
`ToolStatus.established` and `retryability_of()` in `tools/outcome.py:143-284`
[V], and `_grade_success` in `brain/verify/rules.py:59-116` [V], which grades an
unrecognised status (TIMEOUT included) as `ClaimState.UNKNOWN` rather than as a
failure. What does not exist is **transport of those facts across the middle of
the system**. Four specific seams erase them (§3), each with a one-to-three-line
cause: `tools/providers/android_provider.py:113-118` builds the failure
`ToolResult` with no `status=` and no `error_code=`, so `tools/base.py:110-114`
derives `FAILED` [V]; `tools/builtins/commands.py:607-615` does the same for a
killed subprocess [V]; `server/routes/device.py:253-265` collapses every
non-success into one of three wire codes [V]; and `agent/runtime.py:784`
branches on `envelope.get("ok")` alone [V]. The same pattern holds for the other
two requirements — capability-gap records are already computed by
`core/capabilities/discovery.py:237-301` `explain()` with **zero callers**, and
tool revocation already exists as `ToolRegistry.unregister` [V]. The dominant
Phase 5B risk is
therefore **duplication, not absence**: a new state machine, a new evidence
model or a new retry policy would each be the second copy of something already
written, and the two copies would disagree the first time an action was retried
— which is the exact failure `brain/task_graph.py:14-25` [V] already forbids in
prose.

**One live defect outranks every design question here**: `run_in_threadpool` is
called at `server/routes/agent.py:370` and **never imported** anywhere in that
file [V] — no top-level import, no local import inside `agent_intent` — so
`POST /api/agent/intent` raises `NameError` on every request at HEAD. It is also
the *only* response path whose verifier correctly recovers a TIMEOUT into
UNKNOWN (§6), so the behaviour Phase 5B is being asked to build is currently
unreachable in production. See §15 Group E, R3, and PR 5B.0.

---

## 1. Current architecture map

Four composition paths reach a runtime; only two matter for Phase 5B.

| Path | Entry | Builds | Server/cloud imports |
|---|---|---|---|
| Desktop app | `launcher.py:187-189` → `launcher/runtime.py:32` → `launcher.services.build_services` | full service graph | none [H] |
| Server | `python -m server.main` → `server/main.py:106-115` lifespan → `ServerRuntime.__init__` overlay (`server/runtime.py:106-125`) → the **same** `build_services` (`:138`) | full graph + Android tool overlay | by definition |
| Chat-only | `python main.py` → `core/app.py:14-37` | `ChatEngine` + `TemporalClock` **only**, never `build_services` | none [H] |
| Server via launcher | `launcher.py:127-171` | re-invocation of path 2 | — |

Layering, inward-pointing except where noted:

```
events/            EventBus, Event taxonomy (events/types.py)   ← no deps
core/              ids, paths, config, trace, observations,
                   cognitive, capabilities/, credentials
tools/             outcome.py (vocabulary) → base.py (ToolResult)
                   → registry.py → executor.py → builtins/, providers/
brain/             providers/ (LLM) · verify/ (claims→evidence)
                   conversation.py · planner.py · task_graph.py · recovery.py
agent/             runtime.py — the agent loop (AgentRun)
memory/            sqlite.py + stores — the ONLY durable layer
server/            routes/, device_gateway.py, runtime.py
android/           Kotlin companion: polls, executes, reports
plugins/           granted capabilities, in-process importlib
```

Three modules are named "capabilities" and must never be conflated:

- `core/capabilities/` — device/skill capabilities, availability as a live fact.
- `brain/capabilities.py` — `TaskClass`, model-lane selection.
- `brain/providers/capabilities.py` — LLM provider feature registry
  (`CapabilityStatus` = verified / unknown / unsupported) [V].

Two **inverted dependencies** exist: `core/` and `tools/` reach into `server.*`
lazily and try/except-guarded — `core/capabilities/factory.py:61-92`,
`core/capabilities/introspection.py:20`, `tools/factory.py:306-315`,
`tools/providers/android_bridge.py:456` [H]. Failure mode is silent degradation,
not ImportError. Mitigated by the `DeviceBridge` Protocol
(`tools/providers/android_bridge.py:41-50`) with four interchangeable
implementations [H].

**Durable state today**: exactly one place — `memory/sqlite.py`, SQLite at
`DATA_DIR/memory.db`, WAL, `synchronous=NORMAL`, `busy_timeout=5000`
(`:74-76`), one re-entrant `db_lock` (`:93`), `expire_on_commit=False`, and an
established additive-migration idiom (`init_pipeline_tables`, "Additive and
idempotent — `create_all` issues CREATE TABLE IF NOT EXISTS", `:125-140`) [V].
Everything else — `AgentRuntime._runs`, `DeviceGateway._pending/_results`,
`ObservationStore`, `CognitiveStore`, `CapabilityRegistry._capabilities` — is
process memory. `core/cognitive.py:53` states it outright: "nothing here writes
to disk. `memory/` is the durable layer" [V].

---

## 2. Current tool execution flow

`ToolExecutor.execute` (`tools/executor.py:238-460`) is the single gate. It never
raises (`:244`) and every stage that refuses names its own canonical status [V].

```
execute(name, arguments)
 1. stamp started_at, publish ToolInvokedEvent          :252-260
 2. check(name)  →  policy gate                         :262-293
      "tool use is disabled"  → DENIED / TOOLS_DISABLED
      "unknown tool: …"       → INVALID_ARGUMENTS / TOOL_NOT_FOUND
      not in policy.allowed   → DENIED / NOT_ALLOWED
 3. capability binding: no `capability` attr → NOT_IMPLEMENTED   :296-321
 4. resolve_capability(id) ≠ AVAILABLE → DENIED|UNAVAILABLE      :323-381 [H]
 5. _approved(tool)  → risk vs policy.auto_approve
                       → DENIED / CONFIRMATION_REQUIRED          :383-412
 6. _run(tool, args)  → call_with_timeout(...)                   :462-498
 7. replace(capability, authorization="granted",
            execution="completed" if ok else "failed")           :414-421
 8. _verified(tool, args, result) → POSTCONDITION Evidence       :500-608
 9. output-schema validation; mismatch → UNKNOWN /
    OUTPUT_SCHEMA_MISMATCH (a success whose data is
    untrustworthy is not a success)                              :423-457
10. _finish → stamp identity, emit ToolCompletedEvent +
    one `tool_execution` diagnostics line                        :776-897
```

Properties that matter for Phase 5B, all [V]:

- **Wholly synchronous.** No `asyncio`, no `async def`, no `threading`, no
  `subprocess`, no `importlib`, no `exec`/`eval` anywhere in `executor.py`.
  Every execution blocks its calling thread.
- **No retry anywhere.** The executor never re-invokes a tool. `retryability` is
  *computed* (`tools/base.py:149-160`) and *reported* (`:837`, `:864-875` [H])
  but never acted on. This is the correct starting point for requirement (e):
  there is no blind-retry to remove, only a policy to consume.
- **No isolation.** A tool runs in-process, in the caller's thread, with the
  caller's full environment and privileges.
- `ToolResult.ok` is reconciled **from** `status` (`tools/base.py:104-114`), so
  UNKNOWN can never be truthy. When `status` is absent it is *derived* as
  SUCCESS/FAILED — the default that quietly erases TIMEOUT in §3.
- `ToolProtocol` is three members wide (`name`, `risk`, `execute`);
  `description`, `parameters`, `timeout`, `verify`, `side_effect`,
  `capability` are all optional-by-absence via `getattr`. **This is the primary
  Phase 5B extension seam** — a generated tool needs no new protocol.

### Correction to an inherited claim

An earlier sweep reported that the agent-runtime envelope flattens outcomes to a
boolean. That is wrong. `agent/runtime.py:735-756` [V] builds:

```python
envelope = {"tool_call_id": …, "call_id": …, "tool": …, "arguments": …,
            "ok": bool(report.get("ok"))}
if envelope["ok"]: envelope["result"] = …
else:              envelope["error"] = {"code": …, "message": …}
if postcondition is not None: envelope["postcondition"] = postcondition
```

The error **code** and the **postcondition** both ride along. The defect is one
level down: `_absorb_envelopes` (`:780-788`) reads only `envelope.get("ok")` and
increments `consecutive_failures` for anything falsy [V]. The information
survives the wire and dies at the consumer.

---

## 3. Current timeout flow

There is **one** timeout mechanism, **one** default and **no** taxonomy.

`tools/timeout.py` (139 lines, read whole) [V]:

- `DEFAULT_TOOL_TIMEOUT = 30.0` (`:35`) — the only tool default.
- `ToolTimeout(Exception)` (`:38`) — "raised to the executor, never past it".
- `call_with_timeout(call, timeout, name)` (`:42-100`): `if not timeout or
  timeout <= 0: return call()` — an inline escape hatch (`:55-56`); otherwise
  `copy_context()`, a `daemon=True` thread `f"aura-tool-{name}"`, and
  `if not finished.wait(timeout): raise ToolTimeout(...)`.
- The docstring is explicit and correct (`:12-20`): "it CAN stop waiting… it
  CANNOT kill the tool that is still running… the call is abandoned rather than
  killed… A tool that hangs leaks one idle thread until the process ends, which
  is the honest cost of not corrupting whatever it was in the middle of."
- `seconds_or` (`:103-125`): negative/unreadable → fallback; **zero is preserved**
  as the documented way to say "no bound".

The executor's handling is semantically right (`tools/executor.py:473-486`) [V]:
`ToolTimeout` → `status=ToolStatus.TIMEOUT.value`, `error_code=CODE_TIMEOUT`,
with the comment "Not 'the tool failed' but 'we stopped waiting'… the call may
still be running… whose whole meaning is 'outcome genuinely unknown'".

### Where the correct decision is destroyed

Four distinct erasure points, each independently citable:

1. **`tools/providers/android_provider.py:113-118`** [V] — the Android failure
   path builds `ToolResult(ok=False, error=f"{code}: {message}", tool=…,
   data=report)`. **No `status=`, no `error_code=`, no `side_effect=`.** Via
   `tools/base.py:110-114` the status is derived as **FAILED**, and
   `error_category` becomes UNKNOWN because `error_code` is empty. A gateway
   `TIMEOUT` and a gateway `CANCELLED` both become FAILED here. Since
   `brain/verify/rules.py:95-102` grades FAILED as **CONTRADICTED** [V], a phone
   that merely did not answer within 30 s causes AURA to *repair away* a
   statement that may well be true. This is requirement (d) violated on the only
   live mutation path.
2. **`tools/builtins/commands.py:607-615`** [V] — a command killed for overrunning
   returns `fail(f"{key} did not finish within {timeout:g}s and was stopped.")`
   with **no `status=`** → derived FAILED. The process was killed mid-flight;
   `_kill_tree` may even report it survived (`:698-703`). That is textbook
   UNKNOWN reported as failure.
3. **`server/routes/device.py:253-265`** [V] — the whole `ToolResult` is reduced
   to `{"ok", "error": {"code", "message"}}` with only three possible codes
   (`EXECUTION_FAILED`, `BLOCKED_PERMISSION`, `CAPABILITY_UNAVAILABLE`). TIMEOUT
   becomes `EXECUTION_FAILED`.
4. **`agent/runtime.py:784`** [V] — `if not envelope.get("ok")` — the code that
   *did* arrive is never read.

### Where it survives

`brain/verify/ledger.py:295-321` [V] — `_status_from_error(code)` maps
`category_for_code(code)` → UNAVAILABLE / DENIED / INVALID_ARGUMENTS /
**TIMEOUT**, else FAILED. `CODE_TIMEOUT = "TIMEOUT"` (`tools/outcome.py:345`) is
byte-identical to the gateway's wire code (`server/device_gateway.py:145`), so
this mapping *works* [V]. It is reached only from `ledger_from_transcript`, i.e.
only from `POST /api/agent/intent` — **which is dead at HEAD (§7 note, §18 R1).**

Note also `CODE_CANCELLED → ToolErrorCategory.EXECUTION` (`outcome.py:371`) [V],
so even on the good path a cancelled invocation grades FAILED, not CANCELLED.

### The missing step

Nothing anywhere **observes real state after a timeout**. `_verified`
(`tools/executor.py:536-537`) opens with `if not result.ok: return result` [V], so
the postcondition check — the one mechanism that could turn UNKNOWN into
VERIFIED or FAILED honestly — is never consulted for a timed-out call. The
machinery, the status and the Evidence kind all exist; only the wiring
`TIMEOUT → observe → classify` is absent.

### Timeout constants, complete inventory

| Constant | Value | Site | Phase 5B class |
|---|---|---|---|
| `DEFAULT_TOOL_TIMEOUT` | 30.0 | `tools/timeout.py:35` [V] | INVOCATION_TIMEOUT |
| `AndroidTool.timeout` | 30.0 | `tools/providers/android_provider.py:132` [V] | BRIDGE_TIMEOUT |
| `DeviceGateway.submit(timeout_s=)` | 30.0 | `server/device_gateway.py:101` [V] | BRIDGE_TIMEOUT |
| `HttpChatProvider(timeout=)` | 45.0 | `brain/providers/http_chat.py:255` [V] | MODEL_TIMEOUT |
| `DEFAULT_COMMAND_TIMEOUT` / `KILL_GRACE` | 20.0 / 5.0 | `tools/builtins/commands.py:130,134` [V] | SANDBOX_TIMEOUT |
| poll long-poll | 10.0 active / 20.0 idle | `DeviceInvocationPoller.kt:193-194` [V] | — |
| OkHttp read | 120 s | android client [H] | NETWORK_TIMEOUT |

There is no TASK_DEADLINE and no POSTCONDITION_TIMEOUT anywhere. A model timeout
is indistinguishable from unreachability: `brain/providers/http_chat.py:380-381`
catches `(URLError, TimeoutError)` and raises one
`ProviderUnavailableError(f"{self.label} is unreachable")` [V].

---

## 4. Current Android bridge flow

The phone **polls**; the brain is always the server. There is no push.

```
ToolExecutor → AndroidTool.execute (android_provider.py:136-152) [V]
   → DeviceBridge.invoke  (Protocol, android_bridge.py:41-50) [H]
       GatewayDeviceBridge → DeviceGateway.submit (device_gateway.py:95-148) [V]
          enqueue PendingInvocation, wait on threading.Condition, bounded
   ← POST /api/device/poll   (device.py:283) → gateway.poll(timeout_s) [V]
   ← device executes: DeviceToolDispatcher, JIT capability re-check [H]
   → POST /api/device/results → gateway.complete(invocation_id, report) [V]
   → submit() returns the report verbatim, or the gateway's own
     TIMEOUT / CANCELLED failure
   → normalise_device_report (android_bridge.py:495-550) [V-partial]
   → tool_result_from_report (android_provider.py:87-118) [V]
```

Four structural findings, all [V]:

**F1 — `poll()` is a peek, not a pop.** `server/device_gateway.py:254-266`
returns `self._pending[0]` and leaves it queued. There is no lease, no claim, no
`delivered_at`, no in-flight marker. Removal happens only in `complete()`
(`:286-290`) or on timeout (`:132-136`).

**F2 — device affinity does not exist.** `server/routes/device.py:283` calls
`gateway.poll(request.timeout_s)`; `device_id` is used only for the heartbeat at
`:282`. Two companions against one server both receive the same invocation and
both execute it.

**F3 — the device-side idempotency guard is real and it holds today.**
`DeviceInvocationPoller.kt:116` — `val submission = completedReports[
invocation.invocationId] ?: run { …execute… }` — a re-polled invocation whose
report is cached is **re-delivered, never re-executed**, and
`pollForever:102-104` awaits `answer(invocation)` inline so execution is strictly
serialised: the loop cannot poll again mid-execution. Combined, F1+F2 are *not* a
live double-execution bug on a single device. They become one when (a) a second
companion connects, or (b) the app restarts — `completedReports` is an in-memory
`LinkedHashMap` trimmed to `MAX_CACHED_REPORTS` oldest-first (`:181-186`).

**F4 — after a gateway timeout, the truth is discarded.** `submit()` removes the
invocation from `_pending` on timeout (`:132-136`) and it is not in `_results`;
`complete()` then computes `known` from exactly those two collections and
**returns False for an unknown id** (`:276-284`), with the correct rationale
("either a replay or a bug"). So the device's real report — for a mutation that
may have fully succeeded — arrives, is refused, and is never converted into
Evidence. This is the single most important seam for requirement (d).

Additional notes: `PendingInvocation` (`:39-57`) is already JSON-shaped with
`to_dict()`; `new_invocation_id()` (`:34-35`) mints `invo_` + 16 hex, which
matches `core/ids.py:30 ID_PATTERN` but bypasses `new_id()` and is not one of the
five documented prefixes [V]. `cancel_run(run_id)` (`:149`) resolves a run's
pending invocations as CANCELLED — the one existing cancellation primitive.

**Side-effect declarations are almost entirely absent.** `SideEffect` is imported
in `android_provider.py:34` and used **once**, on `AndroidListApps`
(`:361 side_effect = SideEffect.READ_ONLY`) [V]. Every mutating Android tool
(`_Mutation`, `:160-163`, `risk = ToolRisk.DANGEROUS`) declares **no**
side-effect, and `_normalise` (`tools/executor.py:627-645`) does not stamp one
from the tool [V]. So every mutation's `ToolResult.side_effect` is `""` →
`SideEffect.UNKNOWN` (`tools/base.py:160`). Requirement (e)'s protection
currently holds *by accident*: `retryability_of` never reaches its
NON_IDEMPOTENT branch for these tools and falls through to UNKNOWN, which is
non-retryable only because `Retryability.may_retry` admits SAFE alone
(`tools/outcome.py:221-225`) [V].

---

## 5. Current capability system

Three registration paths, an in-process dict, and no persistence.

- **Canonical**: `core/capabilities/factory.py:4-104` `register_core_capabilities`
  — a hard-coded imperative script: 13 non-Android capabilities each with
  `discovery_metadata={"tool": …}`, then 15 Android capabilities from a literal
  tuple with `required_permissions=["android.accessibility"]` and
  `required_dependencies=["android.companion"]`; `check_android_gateway` and
  `check_android_accessibility` registered for all 15; 10 static permission
  grants. The `config` parameter is accepted, normalised, then **never read** [H].
- **Overwrite**: `AndroidProvider._register_capabilities`
  (`tools/providers/android_provider.py:496-527`) replaces the factory versions
  with richer data, forced on every `get_inventory()` by
  `core/capabilities/introspection.py:17-23` [H].
- **Dormant**: `ExternalSkillAdapter.sync_capabilities`
  (`core/capabilities/adapters.py:13-28`) — tests only [H].

Storage is a bare dict `CapabilityRegistry._capabilities`
(`core/capabilities/registry.py:7`), module singletons at
`core/capabilities/__init__.py:7-9`: **no persistence, no lock, no versioning,
no `unregister` — only `clear()`** [H].

`resolve_capability` (`core/capabilities/__init__.py:11-36`) runs four stages with
no caching: existence → `NOT_IMPLEMENTED`; permission → `BLOCKED_PERMISSION`;
health → `run_check` (**absence of a check means healthy**,
`core/capabilities/health.py:16-17`); else AVAILABLE. It is a read that
**writes** to the shared object, unlocked, called O(N) per turn — while every
other `core/` singleton holds a lock [H]. There is **no dependency stage and no
platform stage** despite the docstring: `required_dependencies` is only echoed
into the inventory, and `Capability.platform` / `required_tools` are never read;
`discovery_metadata["tool"]` is never validated against `ToolRegistry` [H].

Gap *detection* already exists and is unused: `SkillDiscovery.explain`
(`core/capabilities/discovery.py:237-301`) returns
`{intent, ranked, selected, diagnosis}` with `no_capability_matched` /
`all_candidates_blocked`, per-candidate `missing_permissions` /
`unhealthy_dependencies`, and `top_candidate`. **`explain` and
`select_best_executable` have zero production callers**; only `discover()` is
live, from `agent/runtime.py:463` and `:573` [H].

Four anti-hallucination backstops exist [H]: `_hallucinated_call_report`
(`agent/runtime.py:510-537`), the executor gate, the device-side JIT re-check
(`DeviceToolDispatcher.kt:136-154`), and the response verifier
(`brain/verify/rules.py:148-202` → `HallucinationType.FABRICATED_CAPABILITY`).

One live **false-AVAILABLE** was reported [H] and is worth verifying before
Phase 5B relies on capability state: `core/capabilities/factory.py` registers
`desktop.input` unconditionally with a granted permission and no health check,
while `tools/factory.py:279` registers input tools only when a synthesizer
exists — so on a machine without input synthesis the prompt lists "Input
Synthesis" as executable.

---

## 6. Current verifier / evidence flow

Phase 3 supplies the vocabulary, Phase 4 the grading. Both are sound.

**Evidence primitive** (`tools/outcome.py:479-617`) [V]: `EvidenceKind` =
POSTCONDITION / OBSERVATION / RECEIPT / RETURN_VALUE / MEMORY; frozen `Evidence`
with **tri-state** `verified: bool | None` ("None is not a weaker True");
`_EVIDENCE_WEIGHT` = POSTCONDITION 3, OBSERVATION 3, RECEIPT 2, RETURN_VALUE 1;
`evidence_state()` → NONE / UNVERIFIED / VERIFIED / CONTRADICTED.

**Producers** [V]: `ToolExecutor._verified` (`tools/executor.py:500-608`) is the
POSTCONDITION producer — `getattr(tool, "verify", None)`, optional by absence,
three deliberate limits (a failure is never re-verified; a `verify` that raises
fails closed with `verified=False`; `None` asserts nothing).
`_evidence_from_report` / `tool_result_from_report`
(`tools/providers/android_provider.py:87-118`) is the Android producer, per
ADR-010.

**Ledger** (`brain/verify/ledger.py`) [V]: the header is unambiguous — "**The
request-scoped evidence ledger**… a per-request account of what the runtime
actually established… The verifier reads it, never the model text". `ToolEvidence`
is frozen with `as_dict()` (`:63`) and derives `state` from `evidence_state()`
(`:56-58`) "so the verifier and the executor agree by construction".
`EvidenceLedger.__init__(request_id: str = "")` (`:140`) is the lifetime seam.

**Grading** (`brain/verify/rules.py:59-116`) [V] — `_grade_success(status, state)`:

| Tool status | Evidence state | Claim state |
|---|---|---|
| SUCCESS | VERIFIED | **VERIFIED** |
| SUCCESS | CONTRADICTED | CONTRADICTED ("succeeded but its own verification came back false") |
| SUCCESS | NONE / UNVERIFIED | INFERRED |
| PARTIAL | any | CONTRADICTED ("only part of it happened") |
| FAILED / DENIED / UNAVAILABLE | any | CONTRADICTED |
| UNKNOWN | any | UNKNOWN |
| **anything else (TIMEOUT, CANCELLED, INVALID_ARGUMENTS)** | any | **UNKNOWN** — "tool status {status} does not establish the outcome" (`:110-113`) |

That last row is Phase 5B requirement (d), already correct, already tested. The
verifier is not the problem; its inputs are.

`_verify_action_claim` (`:117-146`) is the strictest rule: no matched tool
evidence → UNKNOWN + `UNSUPPORTED_ACTION`; matched → `_grade_success`;
CONTRADICTED → `FABRICATED_TOOL_RESULT`. Claim→tool binding is **token overlap**
(`ledger.claim_words` / `matching_tool`, `:213-282`), not semantic — a known
sharp edge, unchanged by Phase 5B.

**Injection**: `ResponseVerifier` is built with `default_capability_provider` at
`launcher/services.py:300` [H] and consulted by `ConversationManager` on both
`chat` and `chat_stream`; `POST /api/agent/intent` verifies via
`ledger_from_transcript` + `verify_run_reply` (`brain/verify/verify.py:279-302`)
[H]. **`POST /api/agent/step` has no verifier at all** — the live device path is
the unverified one.

**The Phase 5B blocker**: a request-scoped ledger cannot accumulate evidence
across a task that spans many requests, restarts and a reconnect. `request_id` at
`:140` plus `as_dict()` on all three evidence records makes a task-scoped
lifetime a change of *scope*, not of *model*.

---

## 7. Current provider / model flow

`brain/providers/` — 19 modules, 2 540 lines. Vocabulary and failure taxonomy are
already correct; the local story is the problem.

**Provider capability registry** (`brain/providers/capabilities.py`) [V]:
`CapabilityStatus` = VERIFIED / UNKNOWN / UNSUPPORTED, with the evidence rule
stated in the header — "VERIFIED: demonstrated by an actual successful request…
UNKNOWN: structurally present but never demonstrated… UNSUPPORTED: structurally
absent. Read from the code, not assumed". `mark_function_calling_verified`
(`:119-141`) is the only transition into VERIFIED, promoted by the first working
FC round trip. It mutates a module-global dict with **no lock and no
persistence**, so VERIFIED is lost on every restart.

**The decisive line** (`:71-73`) [V]:

```python
_FUNCTION_CAPABLE = frozenset({
    "gemini", "openai", "cerebras", "custom", "deepseek", "qwen", "xai",
})
```

`ollama` is **absent** → marked UNSUPPORTED. Confirmed structurally:
`git grep -l 'def generate_with_tools' brain/` returns exactly
`fallback.py`, `gemini.py`, `openai_compatible.py`, and
`brain/providers/ollama.py:17-131` implements only `generate` and `stream` [V].
Capability-first routing therefore skips Ollama for any tool-bearing request and
raises `CapabilityUnavailableError` → HTTP 501 (`brain/providers/errors.py:53-67`
[V], deliberately **not** a subclass of `ProviderUnavailableError` because "no
amount of retrying teaches Groq to accept a tool catalogue").

**So: the currently-shipped local provider cannot run tools.** Local ⇒ no agent
runtime. That, not the Android side, is the real cloud dependency.

**The escape hatch already exists.** `brain/providers/custom.py` [V] — read in
full — is an FC-capable member of `_FUNCTION_CAPABLE` whose docstring names the
target explicitly: "an AgentRouter deployment, a company proxy, LiteLLM in front
of five vendors, **vLLM or llama.cpp or LM Studio on the owner's own machine**".
It declares the dialect and nothing else: URL from `llm.custom_base_url` /
`CUSTOM_BASE_URL`, model from `llm.custom_model`, key from `CUSTOM_API_KEY`,
`requires_base_url = True`, `default_url = ""`, `default_model = ""`. It also
guarantees the key cannot reach another vendor, pinned by
`tests/test_custom_endpoint.py`.

One friction point [V]: `brain/providers/http_chat.py:262-268` makes the API key
**mandatory** — `if not self.api_key: raise ValueError(f"{self.api_key_env} is
not configured")`. A keyless `llama-server` therefore needs a placeholder
`CUSTOM_API_KEY`; there is no keyless path.

**Failure taxonomy** (`brain/providers/errors.py`, 67 lines, read whole) [V]:
`ProviderUnavailableError` (transient), `ProviderAuthError` (ValueError subclass,
failover continues), `ProviderParameterError` (the one auto-repairable 4xx),
`ProviderRateLimitError` (with `retry_after`, `is_account_limit`),
`CapabilityUnavailableError` (permanent). **There is no `ProviderTimeoutError`** —
`http_chat.py:380-381` collapses `(URLError, TimeoutError)` into "is unreachable".

**Transport is blocking stdlib `urlopen`** (`http_chat.py:369`) with
`timeout=self.timeout` (default 45.0, `:255`) [V]. This is why
`server/routes/agent.py:545` — `directive = runtime.advance(run)` called
**directly inside an `async def`** with no `run_in_threadpool` [V], unlike
`server/routes/chat.py:51` and `server/routes/device.py:162,283` — blocks the
FastAPI event loop for up to 45 s per model round. It violates
`DO_NOT_BREAK.md §3` and is R2 in §18.

---

## 8. Current cloud dependencies

**There is no hard cloud dependency in product code.** `git grep -n
'onrender\|render.com'` across all tracked non-Markdown files [V] returns exactly
three hits, none of them a runtime dependency: a `strings.xml` help string
(`android/.../values/strings.xml:33`, an *example* URL) and two lines of
`UrlNormalisationTest.kt`. Render is a *deployment* AURA can be pointed at, never
a module it imports.

What is actually cloud-shaped today, in descending severity:

1. **Model inference** — the one real dependency, and it is §7's finding, not a
   network-topology finding: the only local provider (`ollama`) is UNSUPPORTED for
   function calling, so *local ⇒ no tools ⇒ no agent runtime*. Everything else
   below is configuration.
2. **Server location** — the phone holds one URL in
   `EncryptedSharedPreferences` and polls it. Nothing on the device assumes that
   URL is remote; §13 shows loopback is already permitted in a **release** build.
3. **API keys** — `http_chat.py:262-268` [V] makes a key mandatory even for a
   keyless local endpoint. A placeholder satisfies it; there is no keyless path.
4. **Nothing else.** SQLite is local (`memory/sqlite.py`), the tool registry is
   in-process, the capability registry is in-process, the verifier is pure, the
   diagnostics sink is a local JSONL file.

Conclusion for §14: cloud-optionalisation is **not** an extraction project. It is
(a) a local FC-capable provider path and (b) an on-device host for the existing
server. Both are additive.

---

## 9. Capability gap analysis

This is the section where the audit's thesis is sharpest: **the mechanism Phase 5B
asks for is already written and has zero production callers.**

`core/capabilities/discovery.py:237-301` — `explain(intent, threshold=0.5)` [V].
Verified by `git grep -n 'explain(' -- core/ brain/ server/ tools/ launcher/
agent/`, which returns **exactly one line: the definition itself.** No caller. It
computes, per intent, the matched capability, its live state, the missing
permissions and the failing dependencies — precisely the structured
"capability gap" record the brief specifies — and nothing consumes it.

What *is* wired instead: `CapabilityRegistry.evaluate` /
`available_capabilities` feed the prompt (`brain/prompt.py`) and
`/api/capabilities`, i.e. capability state reaches the model only as a
**list of what works**. When a request falls outside that list there is no
structured record that a gap was hit — the model is left to narrate the absence,
which is exactly the hallucination surface the brief names.

Second finding, verified this session: **existence ≠ availability is enforced
inconsistently across the registration seam.** `core/capabilities/factory.py:14`
[V] registers `desktop.input` unconditionally:

```python
registry.register(Capability(capability_id="desktop.input", name="Input Synthesis",
    ..., required_permissions=["desktop.control"], discovery_metadata={"tool": "click_mouse"}))
```

while `tools/factory.py:276-286` [V] registers the four input tools only
`if synthesizer is not None`. On a host without input synthesis the capability is
therefore AVAILABLE (permission granted, no health probe) while the bound tool
`click_mouse` does not exist. The 15 Android capabilities do **not** have this
bug — they carry `required_dependencies=["android.companion"]` and a live health
check (ADR-010) — so the fix is to make the desktop group follow the Android
pattern, not to invent a new one.

Third finding: **the three "capabilities" namespaces are unrelated and must stay
that way** [V] — `core/capabilities/` (device/skill state),
`brain/capabilities.py` (`TaskClass`, routing), `brain/providers/capabilities.py`
(`CapabilityStatus`, model features). A Phase 5B "capability gap" is a
`core/capabilities/` fact; a "provider cannot do tools" is a
`brain/providers/capabilities.py` fact. Conflating them would produce a
capability system that reports a missing Gemini key as a missing device.

---

## 10. Durable task design proposal

Three constraints from existing code fix almost the whole design.

**Constraint A — the only durable substrate is the SQLite database.**
`memory/sqlite.py:55-150` [V]: one file at `DATA_DIR/memory.db`,
`journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000`, a process-wide
`RLock`, `expire_on_commit=False`. `init_pipeline_tables` is the precedent for
extension: "Additive and idempotent — `create_all` issues CREATE TABLE IF NOT
EXISTS". **Proposal: new tables in that database, via that idiom.** Not JSON files
under `.aura/`, not a second database, not a new dependency, not a queue broker.

**Constraint B — store facts, never derived progress.** `brain/task_graph.py:14-25`
[V] forbids it in terms that apply verbatim to a durable task record: "*Nothing
here is stored.* Every state is derived… A node state written down somewhere
would be a second record of progress… the two copies would disagree the first
time an action was retried." So the durable row holds the `Plan`, the
`CognitiveState` snapshot (`core/cognitive.py:668` `snapshot` + `:665` `revision`
already exist [V]) and the accumulated Evidence. `NodeState` stays computed by
`task_graph` on load.

**Constraint C — ids already exist.** `core/ids.py:1-60` [V] documents `task_` as
"one user request that may take several runs", plus `run_`, `call_`, `obs_`,
`msg_`. A durable task is a `task_` row with N `run_` rows. Only `step_` is
missing; `device_gateway.py:34-35` mints `invo_` outside `new_id()` and should be
folded in.

**State vocabulary** — derived from `ToolStatus`, not invented beside it:
QUEUED → RUNNING → (WAITING_DEVICE | WAITING_MODEL) → CHECKPOINTED → VERIFYING →
{COMPLETED, FAILED, **UNKNOWN**, CANCELLED, ABANDONED_DEADLINE}. `UNKNOWN` is
mandatory and terminal-until-observed: it is what a TIMEOUT resolves to before
`observe → evaluate postcondition → decide`.

**Resume rule, forced by `retryability_of`** (`tools/outcome.py:143-284` [V]): on
resume, a step whose last record is TIMEOUT and whose `side_effect` is
NON_IDEMPOTENT/UNKNOWN **must not be re-invoked**. It must first be *observed*
(the tool's `verify`, or the Android postcondition path), then graded. Only
`SideEffect.READ_ONLY`/`IDEMPOTENT` (`Retryability.SAFE`) may be replayed
blindly. This is requirement (e), and the truth table for it is already written.

**Evidence lifetime** — `EvidenceLedger.__init__(request_id="")`
(`brain/verify/ledger.py:140` [V]) becomes task-scoped by widening that scope and
rehydrating from the stored `ToolEvidence.as_dict()` records (`:63`). No new
evidence model.

---

## 11. Tool self-generation design proposal

**What already exists and must be reused** [V]:

- **Registration and revocation.** `tools/registry.py:33-75` — `register`
  validates `isinstance(tool, ToolProtocol)` and `isinstance(tool.risk, ToolRisk)`
  "because this is the boundary a plugin arrives through", and raises
  `ValueError(f"Tool already registered: {name}")` on a duplicate.
  **`unregister(name) -> bool` already exists** — the revocation primitive is
  present. What is missing is a **version field**: registry keys are bare names,
  so `tool@2` cannot coexist with `tool@1`, and rollback is
  unregister-then-register rather than a pointer move.
- **The extension seam.** `ToolProtocol` is **three members wide** — `name`,
  `risk`, `execute` — and everything else (`verify`, `timeout`, `side_effect`,
  `output_schema`, `capability`, `version`) is optional-by-absence, read via
  `getattr` in the executor. A generated tool is therefore a legal `ToolProtocol`
  today with **no change to the protocol at all.** This is the single most
  important reuse finding in §11.
- **Manifest.** `tools/schema.py` already emits `output_schema()`,
  `tool_definition()` and `mcp_export()`. A generated-tool manifest should be
  `tool_definition()` plus provenance fields, not a parallel format.
- **Narrow grants.** `plugins/base.py:73-104` [V] — `PluginContext` is "everything
  a plugin is allowed to touch": `bus`, `tools`, `config`, with `with_config`
  copying rather than mutating "so one plugin cannot observe another's
  configuration". That is the right shape for a generated tool's permission grant.
  Note `PluginProtocol.version` exists (`:56`) but is documented as
  "informational, for diagnostics" — not a resolution key.

**What must NOT be reused**: `plugins/discovery.py:1-60` [V] loads by in-process
`importlib.import_module` and says so — "importing is already the riskiest thing
this file does". Brief §13 forbids exactly that for generated code. Plugins are
the precedent for *registration*, never for *execution*.

**Lifecycle** DESIGNED → GENERATED → VALIDATED → SANDBOX_TESTED → REGISTERED →
{ACTIVE, ROLLED_BACK, REVOKED}, with the brief §15 rule enforced structurally: the
transition into REGISTERED is made by deterministic code
(`validate_output`-style schema checks + sandbox test results + registry
validation), never by a model verdict. The model may only reach GENERATED.

**Provenance** belongs in the same SQLite database as §10 (prompt hash, model id,
generation `run_id`, source digest, validation results, sandbox verdict, who
approved). This makes `unregister` auditable rather than silent.

---

## 12. Sandbox design proposal

`tools/builtins/commands.py` (1 124 lines) [V] is the sandbox foundation and it is
substantially better than a from-scratch attempt would be:

| Concern | Existing mechanism | Line |
|---|---|---|
| Process isolation | `_isolation()` — POSIX `start_new_session=True`, Windows `CREATE_NO_WINDOW` | `commands.py` |
| Hard kill | `_kill_tree()` — `killpg` / `taskkill /T`, with `KILL_GRACE = 5.0`; "a process that ignored a kill is not a finished one" | `commands.py` |
| **Credential scrubbing** | `_child_environment()` / `_credential_names()` / `_aura_credential_names()` — secrets removed from the child env | `commands.py` |
| Output bounding | `MAX_OUTPUT = 2000`, `MAX_ERROR = 600` | `commands.py` |
| No shell injection | argv + slot templating, never a shell string | `commands.py` |
| Timeout | `DEFAULT_COMMAND_TIMEOUT = 20.0` | `commands.py` |

Brief §26's credential rule is therefore **already satisfied** by
`_child_environment`. What is missing for generated code, and must be added:
filesystem confinement (a per-execution working directory, no repo access),
network denial by default, resource limits (`RLIMIT_AS`/`RLIMIT_CPU` on POSIX; a
job object or plain wall-clock + memory cap on Windows), and an import allow-list.

**One defect to fix in passing** (`commands.py:607-615` [V]): a command killed on
timeout returns `fail(...)` with **no `status=`**, so `ToolResult.__post_init__`
derives FAILED. A sandbox timeout is a *sandbox* TIMEOUT and must be
`ToolStatus.TIMEOUT` + `CODE_TIMEOUT`, or the sandbox will teach the verifier the
same lie the Android path teaches it today (§3).

Brief §27 — "do not use the live phone as the first test environment" — is
satisfied by the existing test posture: the Python suite drives a
loopback/scripted `DeviceBridge` and the Android JVM tests drive fakes through
`PackageSource` (ADR-010's offline-verifiability section). Generated-tool tests
belong in that same offline harness.

---

## 13. Android local-host design

**Load-bearing discovery, verified this session**: the **release** network policy
already permits cleartext to loopback.
`android/app/src/main/res/xml/network_security_config.xml` [V]:

```xml
<base-config cleartextTrafficPermitted="false"> … </base-config>
<domain-config cleartextTrafficPermitted="true">
    <domain includeSubdomains="false">10.0.2.2</domain>
    <domain includeSubdomains="false">localhost</domain>
    <domain includeSubdomains="false">127.0.0.1</domain>
</domain-config>
```

The comment states the intent — "two exceptions that cannot be reached from the
Internet: the emulator's host loopback and localhost" — and pointedly records
that **no user-added CA trust** is granted. So a server on the device at
`http://127.0.0.1:8000` is reachable **from a release build with no policy change
and no weakening.** The cleartext-everywhere allowance stays confined to the debug
build (`app/src/debug/res/xml/…`), which exists only for
`http://<laptop-ip>:8000`.

That removes the hardest-looking obstacle. What remains is genuinely hard and must
not be understated: the Python runtime itself. The server is FastAPI + SQLAlchemy
+ stdlib `urlopen` on CPython; hosting it *inside* the APK is a large,
unevidenced undertaking (Chaquopy/Termux-class), and this audit makes **no claim**
that it is feasible on this timeline.

The honest staging is therefore:

1. **Loopback-first, host unchanged** — the phone already polls a URL; point it at
   `127.0.0.1` and the transport is proven (ADR-010's live section used exactly
   this via `adb reverse`). This needs **no code change at all**.
2. **Local model, remote-free** — §7/§14: `llm.provider: custom` against an
   on-network or on-device OpenAI-compatible server. Configuration only.
3. **On-device Python host** — a separate, explicitly-scoped investigation, not a
   Phase 5B PR.

Anything stronger would be a guess, and §2 of the brief forbids guessing.

---

## 14. Cloud-optionalisation plan

Reachable **today, by configuration alone** — no code change [V]:

```yaml
llm:
  provider: custom
  custom_base_url: http://127.0.0.1:8080/v1   # llama.cpp / vLLM / LM Studio
  custom_model: <model id the server reports>
```

plus a placeholder `CUSTOM_API_KEY` (mandatory per `http_chat.py:262-268`).
`custom` is in `_FUNCTION_CAPABLE` (`brain/providers/capabilities.py:71-73`) and
FC-capable by inheritance from `openai_compatible.py`, so **tools work**. This is
the whole local story and it already exists.

Three additive changes make it honest rather than incidental:

1. **`ProviderTimeoutError`** in `brain/providers/errors.py` (67 lines, read whole
   [V] — it is absent). Today `http_chat.py:380-381` collapses
   `(URLError, TimeoutError)` into "is unreachable", so MODEL_TIMEOUT is
   indistinguishable from unreachability and cannot be graded as UNKNOWN.
2. **Keyless local endpoints** — relax the mandatory-key raise for a loopback /
   private-range base URL only, or accept the placeholder as the documented
   contract. Either is fine; silently sending a placeholder to a vendor is not,
   and `custom.py`'s existing key-confinement test already guards that.
3. **Persist `CapabilityStatus.VERIFIED`** — `mark_function_calling_verified`
   (`capabilities.py:119-141`) mutates a module global, so the first successful
   local FC round trip is forgotten on restart. Persisting it in the §10 database
   makes "this local model can do tools" a durable fact.

`ollama` is deliberately left as-is: adding `generate_with_tools` to it is a
larger change than pointing `custom` at any OpenAI-compatible local server, and
the UNSUPPORTED marking is currently *correct*.

**Note on `DO_NOT_BREAK.md §1`** [V] — "All LLM inference MUST strictly route to
`http://127.0.0.1:8080/v1`". `git grep '8080|gpt-oss|MXFP4'` finds hits only in
`.aura/` harness docs and `DO_NOT_BREAK.md:4` itself. **It is unimplemented in
product code.** Item 1 above is what would make it true.

---

## 15. Exact files / modules that must change

Ordered by dependency, with the change stated precisely. Every entry is [V].

**Group A — stop erasing status (no new files, ~20 lines total).** This is the
whole of requirements (d) and (e); §3 showed the vocabulary and the verifier are
already right and only the middle lies.

| File | Line | Change |
|---|---|---|
| `tools/providers/android_provider.py` | 87-118 | `tool_result_from_report` failure branch must pass `status=` (mapping wire `TIMEOUT`→`ToolStatus.TIMEOUT`, `CANCELLED`→`CANCELLED`, else FAILED), `error_code=`, and `side_effect=` from the tool. Today it passes none, so every gateway timeout derives FAILED. |
| `tools/builtins/commands.py` | 607-615 | A killed command must return `status=ToolStatus.TIMEOUT`, `error_code=CODE_TIMEOUT`. Today `fail(...)` with no `status=` derives FAILED. |
| `server/routes/device.py` | 225-275 | `_execute_bound` flattens `ToolResult` to `{"ok", "error": {code, message}}` with only three codes. Must carry `status`, `error_code`, `side_effect`, `evidence` on the wire. |
| `agent/runtime.py` | 780-788 | `_absorb_envelopes` reads only `envelope.get("ok")`. Must read `error.code` / `status` (the envelope already carries them — `:735-756`). |
| `tools/executor.py` | 414-421 | The unconditional `execution="completed" if result.ok else "failed"` rewrite destroys `_run`'s honest `"not_attempted"` on timeout. Must preserve a non-established execution field. |
| `tools/executor.py` | 627-645 | `_normalise` must stamp `side_effect` from the tool; without it `retryability` is UNKNOWN and rule (e) cannot be enforced. |

**Group B — observe-then-classify.** `tools/executor.py:531-537` opens `_verified`
with `if not result.ok: return result`, so a timed-out call is **never**
postcondition-checked. Requirement (d) needs a TIMEOUT branch that runs the
tool's `verify` (or the Android postcondition read) *before* grading. This is the
one genuinely new control-flow addition in Group A/B.

**Group C — durable runtime (new modules).**

- `memory/sqlite.py` — new additive `init_task_tables()` following
  `init_pipeline_tables`' CREATE-TABLE-IF-NOT-EXISTS idiom (`:55-150`).
- `memory/models.py` — new task / run / step-record / tool-provenance models.
- `core/ids.py` — add `step_`; fold `invo_` in (currently minted outside
  `new_id()` at `server/device_gateway.py:34-35`).
- `brain/verify/ledger.py` — widen `EvidenceLedger.__init__(request_id="")`
  (`:140`) to a task scope; rehydrate from stored `ToolEvidence.as_dict()`.
- `events/types.py:426-480` — add `task_id` / `run_id` to `TaskStepChangedEvent`,
  `TaskFinishedEvent`, `TaskStuckEvent`. All fields are already defaulted, so this
  is backwards-compatible.
- `core/cognitive.py` — no behaviour change; `snapshot` (`:668`) / `revision`
  (`:665`) become the serialisation point. Its own header ("nothing here writes to
  disk") stays true: `memory/` does the writing.

**Group D — device leasing.** `server/device_gateway.py:254-300` — `poll()` is a
peek (`return self._pending[0]`), there is no device filter
(`server/routes/device.py:283` passes only a timeout), and `complete()` refuses
unknown ids (`if not known: return False`) so a post-timeout report is discarded
instead of becoming late Evidence. Needs a lease (owner + expiry) and an
accept-late-report path.

**Group E — event-loop and provider honesty.**

- `server/routes/agent.py:545` — wrap `runtime.advance(run)` in
  `run_in_threadpool`, as `chat.py:51` / `device.py:162,283` already do.
- `server/routes/agent.py:370` — **`run_in_threadpool` is used and never
  imported** (verified: no top-level import, no local import in `agent_intent`).
  `POST /api/agent/intent` raises `NameError` at HEAD. One-line fix; it also
  restores the only path whose verifier correctly recovers TIMEOUT.
- `brain/providers/errors.py` — add `ProviderTimeoutError`;
  `brain/providers/http_chat.py:380-381` must stop collapsing `TimeoutError` into
  "is unreachable".
- `brain/providers/capabilities.py:119-141` — persist the VERIFIED promotion.
- `brain/providers/http_chat.py:262-268` — keyless local endpoint path (§14).

**Group F — generated tools (new modules, no protocol change).**
`tools/registry.py:33-75` gains a version-aware key (it already has
`unregister`); a new `tools/generated/` package holds manifest, provenance,
deterministic validation and the sandbox runner. `ToolProtocol` itself is
unchanged — three members wide is already enough.

**Group G — capability gaps.** Wire `core/capabilities/discovery.py:237-301`
(`explain`, currently **zero callers**) into the request path so a gap becomes a
structured record. Fix `core/capabilities/factory.py:14` to gate `desktop.input`
on a health check, matching the Android group.

---

## 16. Exact files / modules that should NOT change

These are correct as written; changing them is how Phase 5B would break Phase 3/4.
Brief §33's "never remove the verifier / EvidenceLedger / postcondition
verification" maps directly onto this list.

| File / module | Why it must not change |
|---|---|
| `tools/outcome.py` (all 617 lines) | The vocabulary is already right: `established` excludes TIMEOUT/CANCELLED/UNKNOWN; `retryability_of` already forbids replaying a non-idempotent unknown; tri-state `Evidence.verified`; `CODE_TIMEOUT` already matches the gateway wire code byte-for-byte. Phase 5B *uses* this; it does not extend it. |
| `brain/verify/rules.py:59-146` | `_grade_success` already grades TIMEOUT/CANCELLED as UNKNOWN and SUCCESS-with-false-postcondition as CONTRADICTED. Requirement (d) is implemented here. Touching it would be re-deciding a decided thing. |
| `brain/verify/verify.py`, `hallucination.py`, `repair.py`, `claims.py` | The claim→evidence boundary. Widening the ledger's *scope* (§15 Group C) is not a change to these. |
| `brain/task_graph.py` | Its binding rule ("nothing here is stored") is what keeps the durable design honest. Do not add a stored `NodeState`. |
| `tools/base.py:80-160` `ToolResult` reconciliation | `ok` derived FROM `status` is what makes UNKNOWN un-truthy. The *callers* that omit `status=` are the bug, not this. |
| `tools/schema.py` | `validate_output` mapping a schema-violating success to UNKNOWN is exactly the deterministic-validation primitive brief §15 demands. Reuse for generated tools. |
| `plugins/discovery.py` | Correct for plugins, forbidden as a model for generated code (brief §13). Leave it alone rather than hardening it into a sandbox. |
| `android/app/src/main/res/xml/network_security_config.xml` | Already permits loopback in *release* and denies user CA trust. §13 needs nothing from it. Weakening it would be a real security regression. |
| `android/.../AppInventory.kt`, `DeviceToolDispatcher.kt` | ADR-010, live-verified 2026-09-05. `DeviceInvocationPoller.kt:116` re-delivers rather than re-executes — the right semantics; make it durable, don't rewrite it. |
| `memory/` retrieval / semantic path | Orthogonal to Phase 5B. §10 adds tables beside it, never inside it. |
| `local_agent/` | A standalone dev-audit harness, imported by no product module. Not a Phase 5B component. |
| `tools/executor.py` gate cascade (`:238-300`) | Closed-by-default `ToolPolicy` (`enabled=False`, `allowed=frozenset()`, `auto_approve={SAFE}`). Generated tools must pass *through* this, never around it. |

---

## 17. Proposed PR sequence

Brief §30/§31 forbid a giant rewrite and forbid one PR per phase. Each PR below is
independently shippable, independently testable offline, and leaves the tree green.

| PR | Title | Scope | Depends on |
|---|---|---|---|
| **5B.0** | Restore `/api/agent/intent` | The missing `run_in_threadpool` import (`agent.py:370`) + wrap `advance()` at `:545`. ~3 lines. | — |
| **5B.1** | Status truthfulness | §15 Group A + B: `android_provider.py`, `commands.py`, `device.py` wire, `runtime.py:784`, `executor.py:414-421`, `_normalise` side-effect stamping, TIMEOUT→observe branch in `_verified`. | 5B.0 |
| **5B.2** | Idempotency enforcement | Make `retryability_of` load-bearing at every retry site (`brain/recovery.py`, `core/cognitive.py:614` `should_retry`, agent runtime). No blind replay of NON_IDEMPOTENT/UNKNOWN. | 5B.1 |
| **5B.3** | Task persistence (facts only) | `init_task_tables`, task/run/step models, `step_` id, task-scoped `EvidenceLedger`, `task_id` on the three task events. No behaviour change yet — write-and-read-back only. | 5B.1 |
| **5B.4** | Resume + cancellation | Rehydrate a task, recompute `NodeState` via `task_graph`, resume under the 5B.2 rule, honour cancel. Survives process restart. | 5B.3 |
| **5B.5** | Device leasing + late reports | `device_gateway` lease (owner + expiry), device-filtered `poll`, accept a post-timeout `complete()` as late Evidence. | 5B.1 |
| **5B.6** | Capability gap records | Wire `discovery.explain` into the request path; gate `desktop.input` on health; structured gap in the prompt and in diagnostics. | — |
| **5B.7** | Local provider path | `ProviderTimeoutError`, keyless local endpoint, persist FC VERIFIED, documented `custom` local config. | — |
| **5B.8** | Sandbox runner | Generalise `commands.py`'s isolation/kill/env-scrub into a reusable runner + FS confinement, network denial, rlimits, import allow-list. Fix `commands.py:607-615` if 5B.1 has not. | 5B.1 |
| **5B.9** | Tool manifest + provenance | Manifest from `tool_definition()`, provenance rows, versioned registry key, `unregister`-based revocation and rollback. Registration still manual. | 5B.3, 5B.8 |
| **5B.10** | Generated-tool pipeline | DESIGNED→…→REGISTERED with the deterministic gate making the final call (brief §15). Offline tests only; no live phone (brief §27). | 5B.9 |
| **5B.11** | Compound task planning | Only now: multi-step plans on the durable runtime. `TOOL_CALL_LIMIT = 3` (`brain/tool_calling.py:31`) and the chat loop's `seen`/`call_key` dedupe (`brain/conversation.py:596-645`) stay for chat; compound work runs on the task path. | 5B.4 |

5B.0/5B.1/5B.6/5B.7 are parallelisable; nothing before 5B.3 touches the database.

---

## 18. Risks

**R1 — Duplication, not absence, is the dominant risk.** Four of the brief's
requirements are already implemented somewhere and merely unwired: TIMEOUT→UNKNOWN
(`rules.py:110-113`), no-blind-retry (`retryability_of`), capability gaps
(`discovery.explain`), revocation (`registry.unregister`). A Phase 5B that "adds"
these builds a second, disagreeing copy. **Mitigation**: every PR in §17 is phrased
as *wire the existing thing*, and §16 lists what must not be reimplemented.

**R2 — Event-loop blocking.** `server/routes/agent.py:545` calls the synchronous
`advance()` (a 45 s `urlopen` worst case) directly in an `async def`. Under a
durable runtime with more model rounds this gets worse, not better. Fixed by 5B.0.

**R3 — `/api/agent/intent` is dead at HEAD.** `run_in_threadpool` used at `:370`,
never imported → `NameError` on every call. It is also the *only* path whose
verifier correctly recovers TIMEOUT, which means the correct behaviour is currently
unreachable in production and any live claim about it is untested. Highest
severity-per-line in the audit.

**R4 — Device queue has no affinity or lease.** `poll()` peeks the head of a
global list (`device_gateway.py:254-266`) and `device.py:283` passes no device_id.
With one phone this is invisible; with two, or with a reconnect mid-flight, two
devices can execute the same mutating invocation. `DeviceInvocationPoller.kt:116`
guards re-execution **in memory only**, so an app restart removes the guard —
exactly the Phase 5B scenario.

**R5 — Post-timeout reports are destroyed.** `complete()` returns False for an id
already removed by the timeout path (`device_gateway.py:276-284`). The one piece of
evidence that would resolve UNKNOWN → VERIFIED/FAILED is discarded. Requirement (d)
cannot be satisfied end-to-end until 5B.5.

**R6 — Request-scoped evidence cannot span a durable task.**
`EvidenceLedger` is documented as per-request (`ledger.py` header). Without 5B.3, a
resumed task re-verifies against an empty ledger and grades honest work UNKNOWN.

**R7 — Generated-code escape.** The existing precedent (`plugins/discovery.py`)
is in-process `importlib`, which brief §13 forbids. The risk is reaching for it
because it is there. Mitigation: 5B.8 lands the subprocess boundary **before**
5B.10 exists.

**R8 — Model-as-authority drift.** Brief §15 is explicit that deterministic
infrastructure decides registration. The temptation is a "the model reviewed it"
shortcut. Mitigation: the state machine has no edge from GENERATED to REGISTERED
that a model can traverse.

**R9 — Windows sandbox weakness.** `commands.py`'s isolation is genuinely
cross-platform, but `RLIMIT_*` is POSIX-only. On the primary dev host (Windows 11)
resource limits will be weaker; do not claim parity.

**R10 — On-device Python is unproven.** §13 stages around it deliberately. The
risk is a PR that assumes it. No claim of feasibility is made here.

**R11 — `desktop.input` false AVAILABLE.** `factory.py:14` registers
unconditionally while `tools/factory.py:276-286` registers the tools only when a
synthesizer exists, so the prompt can advertise a capability with no tool behind
it. Small, but it is the exact failure mode §9 is meant to eliminate.

**R12 — Claim→tool binding is token overlap.** `ledger.matching_tool` (`:213-282`)
is lexical, not semantic. Generated tools with model-chosen names will make this
noisier. Out of scope, but do not let a generated tool's name be free-form
prose.

---

## 19. Open architectural questions

1. **Task↔session ownership.** `core/ids.py` says `task_` is "one user request
   that may take several runs". Does a durable task outlive its session, and if a
   different session resumes it, whose memory scope applies?
2. **Concurrency policy.** One task at a time per device is implied by
   `DeviceInvocationPoller`'s serialised `answer` loop (`:102-104`). Is that a
   contract to keep, or an artefact to fix in 5B.5?
3. **Deadline semantics.** `TASK_DEADLINE` needs an owner: is it a wall-clock
   budget per task, per step, or per device round? Nothing today has a
   task-level deadline; every timeout is per-call.
4. **UNKNOWN resolution policy.** How many observation attempts, over what window,
   before UNKNOWN becomes terminal? The brief mandates observe-then-decide but not
   the retry budget for the *observation*.
5. **Approval for generated tools.** Does registration require a human, or is a
   `ToolRisk.SAFE` + READ_ONLY generated tool auto-registerable? `ToolPolicy`'s
   `auto_approve={SAFE}` suggests an answer but does not decide it.
6. **Version resolution.** With a versioned registry key, does a running task pin
   the version it started with? (It should — otherwise rollback changes a task's
   behaviour mid-flight.)
7. **Ledger rehydration fidelity.** `ToolEvidence.as_dict()` round-trips, but does
   a rehydrated ledger count as evidence of the *same* strength as a live one, or
   should stored evidence age out?
8. **Local-vs-remote provider policy.** If a local model is present but weaker,
   does AURA prefer local always, or fall back to cloud on capability grounds —
   and is that a `FallbackProvider` ordering question or a new policy?
9. **Where the sandbox runs when the host is the phone.** §13 stage 3 and §12
   collide: a subprocess boundary presumes a POSIX/Windows process model.
10. **`DO_NOT_BREAK.md §1` status.** It mandates `127.0.0.1:8080/v1` routing that
    product code does not implement. Is it a target or a stale note? It should be
    reconciled before §14 cites it as a requirement.

---

## 20. Recommended first implementation PR

**PR 5B.0 + 5B.1, landed together as "status truthfulness".**

Rationale: it is the smallest change that makes the largest correctness claim true,
it adds no schema, no dependency, no new module and no new concept, and every later
PR depends on it. §3 established that the vocabulary (`tools/outcome.py`) and the
grader (`brain/verify/rules.py:110-113`) are already correct and only the middle
lies; this PR stops the lying. Until it lands, a durable task runtime would
faithfully persist wrong statuses, and a sandbox would report its own timeouts as
failures.

Concretely:

1. `server/routes/agent.py` — import `run_in_threadpool`; wrap `advance()` at
   `:545`. Restores `/api/agent/intent` and unblocks the event loop.
2. `tools/providers/android_provider.py:87-118` — map the wire error code to
   `ToolStatus` / `error_code`, stamp `side_effect` from the tool.
3. `tools/builtins/commands.py:607-615` — a killed command is `TIMEOUT`, not
   `FAILED`.
4. `server/routes/device.py:225-275` — carry `status` / `error_code` /
   `side_effect` on the device wire instead of three collapsed codes.
5. `agent/runtime.py:780-788` — `_absorb_envelopes` reads `error.code` (already
   present in the envelope at `:735-756`).
6. `tools/executor.py` — stop rewriting `execution` at `:414-421`; stamp
   `side_effect` in `_normalise`; add the TIMEOUT→observe branch to `_verified`.

Verification, all offline (no phone, brief §27): extend the existing
`tests/test_tool_output_contract.py` and `tests/test_phase45_integration.py` with
a gateway-timeout case asserting `ClaimState.UNKNOWN` (not CONTRADICTED) end to
end, a killed-command case asserting `ToolStatus.TIMEOUT`, and a
non-idempotent-unknown case asserting no replay. The full-suite baseline to hold
is `3489 passed / 2 skipped / 1 deselected / 5 failed`, the 5 being the
pre-existing settings-restart set (`server/routes/settings.py:147,210`
`os.execve` [V]) — **do not "fix" those** (brief constraint, carried).

Expected outcome: a timed-out Android mutation stops being reported as a failure
that the verifier then grades CONTRADICTED, and starts being reported as UNKNOWN
pending observation — which is Phase 5B requirement (d) satisfied end to end, using
only code that already exists at both ends.

---

## Gap matrix

`E` = exists and is wired; `P` = partial or present-but-unwired; `M` = missing.
Every row cites code; nothing is inferred from documentation.

| Capability | Existing | Partial | Missing | Reusable component | Required change |
|---|---|---|---|---|---|
| Durable task execution | | | **M** | `memory/sqlite.py:55-150` (WAL, `init_pipeline_tables` additive idiom); `core/ids.py` `task_`/`run_` | New additive task tables; nothing persists a task today (`core/cognitive.py:53` "nothing here writes to disk") |
| Async tools | | **P** | | `ToolExecutor` runs sync; `run_in_threadpool` used in `chat.py:51`, `device.py:162,283` | Executor stays sync; async lives in the task runtime, not in `ToolProtocol` |
| Long-running tools | | **P** | | Per-call timeouts exist (7 constants, §3); `AndroidTool.timeout = 30.0` | No task-level deadline anywhere; add `TASK_DEADLINE` (open Q3) |
| Checkpointing | | **P** | | `core/cognitive.py:668` `snapshot`, `:665` `revision`; `Plan` in `brain/task_graph.py` | Persist the snapshot; the producer already exists |
| Resume | | | **M** | `task_graph` recomputes `NodeState` from facts (`:14-25`) | Rehydrate facts, recompute state; never store state |
| Cancellation | | **P** | | `POST /runs/{id}/cancel` (`agent.py:579`); `RunStatus`; `ToolStatus.CANCELLED` | `CODE_CANCELLED → ToolErrorCategory.EXECUTION` (`outcome.py:330-390`) makes a cancel grade FAILED; and cancel does not survive restart |
| Timeout recovery | | **P** | | `ToolStatus.TIMEOUT`, `established=False`, `rules.py:110-113` grades UNKNOWN; `ledger.py:295-321` recovers TIMEOUT from the code | Four erasure points (§3); `_verified` returns early on `not ok` (`executor.py:536-537`) so no observation happens |
| Retry policies | | **P** | | `core/cognitive.py:614` `should_retry`; `brain/recovery.py`; `Retryability` | Retry sites do not consult `retryability_of`; PR 5B.2 |
| Idempotency | | **P** | | `SideEffect` (`repeatable`/`mutates`) + `retryability_of` truth table (`outcome.py:143-284`) | `SideEffect` is set on exactly **one** tool (`android_provider.py:361`) and `_normalise` (`executor.py:627-645`) never stamps it → almost everything is UNKNOWN |
| Compound task planning | | **P** | | `Plan`/`brain/task_graph.py`; `agent/runtime.py` multi-round loop | Chat path capped at `TOOL_CALL_LIMIT = 3` (`brain/tool_calling.py:31`) with same-call dedupe (`conversation.py:596-645`); compound work needs the task path |
| Capability gap detection | | **P** | | `core/capabilities/discovery.py:237-301` `explain()` — computes matched capability, live state, missing permissions, failing dependencies | **Zero callers** (verified by grep). Wire it; do not rewrite it |
| Dynamic tool generation | | | **M** | `tools/schema.py` (`tool_definition`, `validate_output`); `ToolProtocol` is 3 members wide so a generated tool is already legal | New `tools/generated/` pipeline; no protocol change |
| Tool manifests | | **P** | | `tools/schema.py` `tool_definition()` / `mcp_export()`; `registry.definitions()` | Add provenance fields to the existing shape; no parallel format |
| Tool sandbox | | **P** | | `tools/builtins/commands.py`: `_isolation()`, `_kill_tree()`, `_child_environment()` (**credential scrubbing already done** — brief §26), argv templating, output caps | Add FS confinement, network denial, rlimits, import allow-list. Do **not** model on `plugins/discovery.py`'s in-process `importlib` |
| Generated-tool tests | | **P** | | Offline harness precedent: scripted `DeviceBridge`, `PackageSource` fakes (ADR-010) | Generate + run tests inside the sandbox before registration (brief §27: not on the phone) |
| Dynamic registration | **E** | | | `tools/registry.py:33-75` `register` validates `ToolProtocol` + `ToolRisk` "because this is the boundary a plugin arrives through" | Add a version-aware key; the validation gate itself is already right |
| Rollback | | **P** | | `unregister` + `register` | No version field, so rollback is destructive rather than a pointer move; a running task must pin its version (open Q6) |
| Tool versioning | | | **M** | `PluginProtocol.version` (`plugins/base.py:56`) exists but is "informational, for diagnostics" | Registry keys are bare names; `register` raises on duplicate |
| Tool revocation | **E** | | | `tools/registry.py` `unregister(name) -> bool` | Add provenance/audit so revocation is recorded, not silent |
| Android-local runtime | | **P** | | **Release** netsec already permits cleartext to `127.0.0.1`/`localhost` and denies user CA trust; poll transport proven live (ADR-010) | Loopback needs **no code change**; an on-device Python host is unproven (R10) |
| Local model provider | | **P** | | `brain/providers/custom.py` — FC-capable, in `_FUNCTION_CAPABLE`, docstring names "vLLM or llama.cpp or LM Studio on the owner's own machine" | Works **today by configuration**. `ollama` has no `generate_with_tools` → correctly UNSUPPORTED (`capabilities.py:71-73`) |
| Remote model provider | **E** | | | `FallbackProvider`, gemini/openai/cerebras/deepseek/qwen/xai; capability-first routing | None. Remote is already optional-by-configuration |
| Cloud independence | | **P** | | No product module imports Render (grep: 3 hits, all `strings.xml`/test) | Only real dependency is inference (§8); plus mandatory API key (`http_chat.py:262-268`) |
| Evidence generation | **E** | | | `Evidence` (tri-state), `EvidenceKind`, `evidence_state()`; producers `executor._verified` and `android_provider._evidence_from_report` | None to the model. Callers must stop dropping `status`/`side_effect` (§15 Group A) |
| Postcondition verification | | **P** | | `getattr(tool, "verify", None)` (`executor.py:531-608`); Android postcondition→POSTCONDITION seam (ADR-010, live-verified) | Never runs on a timed-out call (`:536-537`); post-timeout device reports are discarded (`device_gateway.py:276-284`) |
| Tool security isolation | | **P** | | `ToolPolicy` closed by default (`enabled=False`, `allowed=frozenset()`, `auto_approve={SAFE}`); `ToolRisk`; permission gates | Process-level isolation exists only inside `commands.py`; nothing generalises it for arbitrary generated code |

**Row count: 26.** Distribution: 4 `E`, 18 `P`, 4 `M`. That distribution *is* the
audit's conclusion — Phase 5B is overwhelmingly a wiring and lifetime problem, not
a construction problem, and the four genuinely missing items (durable execution,
resume, tool generation, tool versioning) all attach to seams that already exist.

---

## Audit completeness

All 20 required sections are present, plus the 26-row gap matrix. Confidence
markers: every load-bearing claim is **[V]** (read or grepped first-hand this
session). Remaining **[H]** claims, none of which a §17 PR depends on: the
`launcher/services.py:300` injection path was spot-checked (`ResponseVerifier(repair=repair)`,
defaulting to `default_capability_provider` via `verify.py:110`) and the
`brain/verify/verify.py:279-302` intent-path wiring, which was read but not
re-read after compaction.

One inherited claim was **falsified and corrected** rather than propagated: a
prior sweep asserted `agent/runtime.py:741,784` "flatten ToolStatus to a boolean
`ok`"; the envelope in fact carries `error.code`, `postcondition` and
`observation` (`:735-774`), and the defect is narrower — `_absorb_envelopes`
(`:780-788`) reads only `ok`. See §2.

**No product code was modified.** Per brief §34 this document is the only artifact.
