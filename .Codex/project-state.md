# AURA project state

The capability-grounding implementation has been audited and exercised through the physical Android device. Server-side grounding, the Android companion transport, and the device-side dispatcher are active; the production deployment still needs to receive the pushed commit before its live API reflects these changes.

- Android companion package: `com.aura.companion`
- Connected ADB device: `IBCQMB4PTGNZJVTO` (OPPO CPH2251, API 33)
- `accessibility_enabled=1`; both `AuraAccessibilityService` and
  `ScreenObservationService` are enabled and bound.
- An authenticated local server received the companion poll heartbeat and
  reported 14 Android capabilities as `AVAILABLE`, with granted accessibility
  permission and healthy companion dependency.
- The working tree contains extensive pre-existing/uncommitted capability-grounding changes; preserve unrelated user changes.
- Persistent state files were missing at the start of this audit and are being established now.
- Targeted capability/device/discovery suites pass (313 tests). Android unit
  tests pass after updating an obsolete assertion for the disabled legacy
  agent loop. The full Python suite is being rerun after test fixtures were
  aligned with the strict capability requirement; known settings-restart
  failures are unrelated to Android.
- Gradle builds and unit tests succeed with the bundled JDK 21 and normalized
  `TEMP`/`TMP` variables. The default desktop shell/JDK 17 environment
  reproduces the loopback `Invalid argument` failure.
- The final physical test temporarily used an authenticated local server via
  the app's normal Connection screen; the phone URL was restored and verified
  as `https://aura-xwm4.onrender.com/` before handoff.
- The companion dispatcher includes a just-in-time runtime capability gate;
  Android primitives are reached only after that gate and catalog validation.
