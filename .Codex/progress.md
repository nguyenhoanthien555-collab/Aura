# Progress

## 2026-08-27

- Read repository instructions and audited the working tree.
- Confirmed a physical ADB device: `IBCQMB4PTGNZJVTO`, `device`, model `CPH2251`, Android 13/API 33.
- Confirmed `com.aura.companion` is installed (`versionName 0.1.0`) and both AURA accessibility services are enabled/bound.
- Found Android provider tools inherit `capability = "dummy"`; only coarse Android capabilities are registered.
- Found `/api/device/invoke` and `scripts/aura_android.py` call Android tools directly instead of `ToolExecutor`.
- Baseline targeted Python tests passed: 81 passed.
- The companion currently points at `http://192.168.1.252:8000/`; this host is `192.168.1.35`, and no server is listening now.

## Live grounding milestone — 2026-08-27

- No companion heartbeat now resolves Android capabilities to `UNAVAILABLE`,
  not `BLOCKED_PERMISSION`.
- `ToolExecutor` preserves structured capability-gate failure payloads, and
  the HTTP Android harness queries `/api/capabilities` before its local gate.
- The dead legacy `runAgentSteps` implementation in the companion is
  disabled; `AgentRunDriver` and `AccessibilityToolDispatcher` are active.
- ADB still verifies the physical device and installed package, but current
  secure settings show neither AURA accessibility service enabled. Therefore
  no heartbeat or real Android execution is currently possible; no permission
  was toggled automatically.
- Live API inventory reports all Android capabilities `UNAVAILABLE` with
  reason `no Android companion poll heartbeat has been received`.
- Targeted Python regression suites pass: 107 passed.
- Extended capability/discovery/input/plugin regression suites pass: 313
  passed. Natural-language ranking selected the intended Android capability
  for screen inspection, UI search, button press, foreground app, home, and
  text input; all were correctly filtered out as non-executable while the
  heartbeat was absent.
- Full Python suite: 3228 passed, 2 skipped, 1 deselected, 5 unrelated
  settings-restart failures. The failures are in existing
  `server/routes/settings.py` background restart handling
  (`asyncio.create_task` called from a synchronous Starlette worker), not in
  the capability changes.
- Gradle Android build is blocked before compilation by the local JDK /
  Gradle loopback-daemon error (`java.net.SocketException: Invalid argument:
  connect`).
- Final current-code live API check: 14 Android capabilities, all
  `UNAVAILABLE`, same heartbeat reason; `android.get_foreground_app` through
  `/api/device/invoke` returned `CAPABILITY_UNAVAILABLE` with
  `execution=not_attempted`. The audit server was stopped afterward.
- Added a just-in-time capability gate inside the companion's
  `AccessibilityToolDispatcher`, so a permission/health change after the
  server advertised a directive still blocks before Android primitives run.

## Physical execution and build milestone — 2026-08-27

- Re-verified ADB device `IBCQMB4PTGNZJVTO` (OPPO CPH2251, Android 13/API
  33), package `com.aura.companion`, package process, and both enabled/bound
  AURA accessibility services.
- Built the debug APK with the repository Gradle wrapper using bundled JDK 21
  and normalized Windows TEMP/TMP; installed it with `adb install -r` and
  verified package version `0.1.0`, service declarations, and accessibility
  state.
- An authenticated local server received real companion polls. Live inventory:
  14 canonical Android capabilities, all `AVAILABLE`, `granted`, and
  `healthy`.
- Real `/api/device/invoke` results through the physical companion included
  foreground app `com.aura.companion`, UI tree, visible-node search, JPEG
  screenshot (`821x1825`, 105393 bytes), verified text entry into the harmless
  Aura draft field, verified node-scoped backspace clearing it, verified Home
  to `com.android.launcher`, verified launch back to Aura, `wait_for` met, and
  `verify package_is=com.aura.companion` met.
- Real failure grounding: a missing visible node returned `ok=false`,
  `NODE_NOT_FOUND`, and a fresh accessibility-tree observation; an unknown
  device tool returned `TOOL_NOT_FOUND` without device execution.
- Fixed result delivery for device failures without observations by allowing
  `observation_id=null` at the HTTP boundary; fixed `android.press_key` to
  accept a node-scoped backspace/clear action. The physical press-key test
  passed after reinstall.
- Fixed stale capability explanations: transitioning to `AVAILABLE` now
  clears an old blocking reason from registry metadata.
- Android unit tests pass (`:app:testDebugUnitTest`); targeted Python suites
  pass (`313 passed`). Test doubles were updated with explicit capability
  mappings required by the strict no-unmapped-tool rule; their focused suite
  now passes (`232 passed`).

- Re-ran the combined capability, Android provider, bridge, runtime, route,
  security, input, plugin, and tool-framework suites: `545 passed`.
- Re-ran the full Python suite: `3228 passed, 2 skipped, 1 deselected,
  5 failed`. All five failures are the pre-existing settings restart path in
  `server/routes/settings.py`, where a Starlette sync background worker calls
  `asyncio.create_task` without a running event loop; no Android test failed.
- Re-ran Android `:app:testDebugUnitTest`: `BUILD SUCCESSFUL` with only the
  existing SDK XML compatibility warning and AccessibilityNodeInfo deprecation
  warnings.
