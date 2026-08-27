# Current task

Make AURA's Android capabilities runtime-grounded and executable through the invariant:

`intent -> discovery -> capability registry -> permission -> health/dependency -> ToolExecutor -> real Android -> ToolResult -> LLM`.

Current focus: finish verification, commit/push the strict Android capability integration, and confirm the committed APK plus production configuration.

Current verified device state: the physical package is installed and both AURA
accessibility services are enabled/bound. The companion sent a live heartbeat
to an authenticated local server and all 14 canonical Android capabilities
were `AVAILABLE`.

Completed milestones in this task:

- Per-tool Android capabilities are dynamically registered and resolved from
  companion status.
- ToolExecutor and `/api/device/invoke` are the server execution gates.
- The HTTP harness preserves live capability evidence and structured failure
  codes.
- The legacy direct agent-step body is disabled; AgentRunDriver is the active
  companion agent path.
- Discovery ranking was verified for six Android intents; every intended
  capability ranked first, and `select_best_executable` returned none while
  the real device was unavailable.
- Full Python suite completed with 3228 passed, 2 skipped, 1 deselected, and
  5 pre-existing settings-restart failures. The focused capability/device
  suite completed with 545 passed. Android Gradle unit tests and compilation
  succeed with the documented JDK 21/TEMP/TMP workaround.
- Final local live API check confirms 14 Android capabilities are `AVAILABLE`,
  with `authorization=granted`, `health=healthy`, and no stale reason.
- Safe physical execution succeeded for foreground app, UI tree, UI search,
  screenshot, tap/back/home, launch, wait, verify, text input, and the fixed
  node-scoped backspace path. A real missing-node failure and unknown-tool
  rejection also returned structured results.
- The companion dispatcher now re-checks its own runtime capability status
  immediately before dispatching a known Android tool.
