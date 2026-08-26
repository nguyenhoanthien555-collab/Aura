# AURA Agent Architecture Migration Report

Branch: `feature/aura-identity` · Status: **P0 core complete and tested;
device-side loop rewiring pending; real-device validation NOT executed**

---

## 1. Legacy modules removed

Nothing was deleted in this pass - deliberately. PART 38 requires
migrating callers before deleting, and the largest caller
(`AuraAccessibilityService.runAgentSteps`) still runs on devices today.
The following are now **scheduled for removal** once the device ships
the step protocol:

| Legacy module | Replaced by |
|---|---|
| `AgentActionParser` + `KNOWN_ACTIONS` | `DeviceToolCatalog` (`AgentToolProtocol.kt`) + native function calling |
| free-form action extraction from model prose | provider-native tool calls (`generate_with_tools`) |
| prompt-driven action syntax (AGENT RULES action list) | JSON-Schema function catalogue (`tools/schema.py`) |
| device-owned loop state (`currentRequest`, `maxSteps` convergence) | server-owned `AgentRun` (`agent/runtime.py`) |
| success declared by model `complete` | postcondition-gated completion (`StopReason.GOAL_VERIFIED`) |

## 2. Legacy modules retained (and why)

* `AuraAccessibilityService.runAgentSteps` / `AuraActionExecutor` /
  `SafetyGuard` - still the working execution path on installed
  devices; their gesture/verification internals are exactly what the new
  dispatcher will call. Removal is a follow-up that must ship together
  with the app's `/api/agent/step` client.
* `brain/tool_calling.py` (text tool-call reader) - still used by the
  desktop/chat path, which has not migrated to native FC yet. It no
  longer serves the Android agent path by design.
* `brain/agent_mode.py` tick handling - remains until the phone stops
  sending `agent_tick` chat turns.

## 3. New architecture

```
User goal → Task → AgentRun (server-owned)
    ↓ rounds of:
Model.generate_with_tools(system, transcript, catalogue)
    ↓ ToolCallRequest(s)
Policy gates (ToolExecutor: enabled→registered→allowed→risk/approval→args)
    ↓ inline bridge            or   deferred directives
Structured envelope (ok/result/error/postcondition/observation_id)
    ↓ ObservationStore (per-run scoped, content-hashed)
next round … → verified completion | named failure
```

New files:

* `core/ids.py` - task/run/call/obs/session identifiers
* `core/observations.py` - Observation model, hashes, freshness,
  per-run scoping, `require_fresh`
* `tools/schema.py` - Parameter→JSON Schema, OpenAI `tools:` payload
* `tools/providers/base.py` - `CapabilityProvider`
* `tools/providers/android_bridge.py` - `DeviceBridge` protocol,
  deterministic `LoopbackDeviceBridge`, `DeclaredOnlyBridge`
* `tools/providers/android_provider.py` - 14 `android.*` tools with
  risk classes and typed parameters
* `brain/native_fc.py` - `ModelTurn`/`ToolCallRequest`/`extract_turn`
* `agent/runtime.py` - the authoritative loop (inline + deferred),
  envelopes, verification gating, compaction, enumerated termination
* `server/routes/agent.py` - `/api/agent/step`, run get/cancel
* `scripts/aura_android.py` - CLI harness (discover/inspect/mutate/
  verify, `--json`, `--dry-run`, REPL)
* Kotlin: `accessibility/AgentToolProtocol.kt` - directive/report wire
  types + `DeviceToolCatalog`

Modified: `tools/base.py` (`Parameter.type`, `ToolResult.data`),
`brain/providers/openai_compatible.py` (`generate_with_tools`),
`server/main.py` (agent router registered).

## 4. New tools

14 Android capabilities (PART 4 list, verbatim):
get_foreground_app, get_ui_tree, find_node, screenshot, tap, long_press,
swipe, type_text, press_key, back, home, launch_app, wait_for, verify.

## 5. Providers

`CapabilityProvider` base; `AndroidProvider` implemented over
`DeviceBridge`. Bridges: loopback (tests/CLI/benchmarks), declared-only
(server-side schema advertisement), http relay (CLI→server). Desktop /
filesystem / web / MCP providers are future subclasses of the same base.

## 6. Observation model

`Observation{observation_id, kind, source, observed_at, content_hash,
data, task_id, run_id, session_id, provenance}`; kinds for
foreground-app vs accessibility-tree vs screenshot vs postcondition -
current-app metadata and pixels stay separate kinds (PART 6).
`ObservationStore.require_fresh()` refuses stale instead of returning it.

## 7. Agent loop

Multi-round, server-owned, transport-independent (inline executor or
device-polling). Termination reasons enumerated: GOAL_VERIFIED,
COMPLETED_UNVERIFIED, FAILED, CANCELLED, RETRY_EXHAUSTED, MODEL_ERROR,
STEP_CEILING. The ceiling is a safety net only; silence is never
accepted as completion; a consecutive-failure bound replaces hope.

## 8. Security / policy

Android risk mapping onto the existing ladder (PART 16): reads SAFE,
screenshot SENSITIVE, mutations DANGEROUS - so the existing five
executor gates apply unchanged; approval callbacks were already proven
by the desktop tools. No new bypass exists.

## 9. MCP

Not yet implemented (P1). The registry/provider boundary is the planned
integration point; MCP tools will register like any other provider.
No separate loop will be created.

## 10. CLI harness

`python scripts/aura_android.py [--json] [--dry-run] [--bridge
loopback|http] [--demo] <group> <verb> [value]` plus interactive REPL.
Verified live against the loopback bridge: discovery with risks, JSON
reports, NODE_NOT_FOUND structured failure with exit code 1.

## 11. Skills

Not yet implemented (P1). The catalogue/schema layer gives skills a
stable surface to describe; nothing app-specific enters core (the
YouTube flow runs through generic tools only).

## 12. Memory / context changes

Compaction in `_compact`: rounds beyond the verbatim window have large
payload messages replaced by outcome summaries; goal and recent rounds
survive intact. Observations are ephemeral by default - recorded per
run, scoped per run, so observation state cannot leak into other tasks
or into permanent memory accidentally.

## 13-16. Files changed / dependencies / tests

Dependencies: **none added**.

New Python tests: `tests/test_agent_ids.py`, `test_observations.py`,
`test_android_provider.py`, `test_native_fc.py`,
`test_agent_runtime.py`, `test_agent_route.py` - 61 tests.
New Kotlin tests: `android/app/src/test/.../AgentToolProtocolTest.kt`
- 10 tests.

## Full test results

* Server suite: **3213 passed, 2 skipped, 1 deselected** (44 s) -
  includes every pre-existing suite; zero regressions.
* Android JVM suite: **388 passed, 0 failures** - all pre-existing app
  tests plus the new protocol suite.
* CLI exercised live against the loopback bridge (see §10).

## 17. Real-device results

**NOT EXECUTED.** `adb devices` shows no attached device, and the
Android app does not yet speak `/api/agent/step`. Per the brief, no
real-device claim is made. Required follow-up before Tests A-E:
(1) migrate `AuraAccessibilityService` to the step protocol using
`DeviceToolCatalog`, (2) install via `adb install -r`, (3) run the five
tests against a live phone.

## 18. Remaining limitations

1. Device-side loop still legacy (retained per §2); protocol types are
   ready and tested.
2. Only OpenAI-compatible providers implement `generate_with_tools`;
   Anthropic and friends still serve non-agent turns by text.
3. MCP, skills, model routing, scheduler, evaluation dashboard = P1/P2.
4. Run state lives in-process; persistence across server restarts is
   pending.
5. The CLI `http` relay expects a `/api/device/invoke` endpoint that is
   not served yet.

## 19. License / provenance

No source copied from Odysseus or CLI-Anything. Architecture patterns
were reimplemented clean-room from documented behavior (multi-round
tool loops, registries, capability filtering, discover/inspect/mutate/
verify CLIs, REPLs, JSON contracts). No license obligations incurred.