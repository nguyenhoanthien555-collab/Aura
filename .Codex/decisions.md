# Architectural decisions

## 2026-08-27 capability audit

- Keep the existing polling gateway and Android dispatcher as the transport/execution backend.
- Make each executable Android tool map to its own registered capability; do not use `dummy`, tool-name fallback, or coarse-only capability records.
- Device transport requests must pass through `ToolExecutor`; the Android dispatcher remains the final device-side executor and returns structured reports.
- Runtime Android permission/health state must come from a live device heartbeat rather than unconditional startup grants.
- A missing companion heartbeat is `UNAVAILABLE`, not a missing Android
  permission, because permission facts are unknowable until the companion
  reports them. A screen-capture user switch reported by a live companion is
  `BLOCKED_PERMISSION`.
- The HTTP harness mirrors the server capability endpoint before its local
  ToolExecutor gate; it must not invent device availability merely because a
  relay URL exists.
- The phone's accessibility permission is not changed by the audit. A missing
  AURA service entry in Android secure settings is a manual user action and
  must remain an explicit prerequisite for live heartbeat and execution.

## 2026-08-27 physical integration

- Use the bundled JDK 21 with explicit `TEMP`/`TMP=C:\\Windows\\Temp` for
  Gradle on this Windows desktop. The repository wrapper and Android build
  files remain unchanged; the default JDK 17/desktop environment reproduces
  the loopback `Invalid argument` failure.
- Device result reports may legitimately have no observation on an execution
  failure, so `observation_id` is optional at the HTTP boundary. Successful
  observation/action reports still carry authoritative observation IDs.
- The active screenshot path is `AgentRunDriver`/`AccessibilityToolDispatcher`
  plus `AccessibilityScreenshotCapture`; the old `AuraAccessibilityService`
  agent-step loop is disabled and must not be revived to satisfy old tests.
- Local physical testing may temporarily use the app's normal configured URL
  field, but the phone must be restored to the production URL before final
  verification; the token remains encrypted/masked.
