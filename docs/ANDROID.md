# Aura Android Companion — Build & Integration Guide

This document covers building the Android app, the protocol it speaks, and how it connects to Aura Cloud Core.

## Prerequisites

- **JDK 17+** (Temurin, Zulu, or Oracle)
- **Android SDK** (API 35 / Android 15)
- **Gradle 8.9** (the wrapper uses this exact version)
- **Android Studio** (Koala or later) or command-line SDK tools

The project uses a Gradle wrapper. The wrapper **must be generated**, not copied or fabricated:

```bash
cd android
# From an environment with Gradle on PATH (Android Studio ships one)
gradle wrapper --gradle-version 8.9
./gradlew :app:assembleDebug
```

> The repository does not commit `gradlew`, `gradlew.bat` or `gradle/wrapper/`. This is intentional: the wrapper JAR is a binary that should be generated from a trusted Gradle installation, not checked in.

## Architecture

```
┌─────────────────────┐         HTTPS / WSS          ┌──────────────────┐
│  Android App        │ ──────────────────────────►  │  Aura Cloud Core │
│  (Kotlin, Compose)  │ ◄──────────────────────────  │  (FastAPI)       │
└─────────────────────┘                              └──────────────────┘
         ▲                                                  ▲
         │                                                  │
         │  Accessibility Service                          │  ServerRuntime
         │  (screen observation)                           │  (shared Brain,
         ▼                                                  │   Memory, LLM)
┌─────────────────────┐
│  Notification       │
│  Worker (WorkMgr)   │
└─────────────────────┘
```

### Key principles

1. **No secrets in the APK** — The app stores only the server URL and a bearer token, both entered by the user at first run. LLM provider keys (Gemini, OpenAI, Ollama) live on the server.
2. **Single composition root** — The server reuses `launcher.services.build_services`, the same function the desktop build uses. Routes only validate, authenticate, deserialize, call the runtime, and serialize.
3. **Settings as a read-only interface** — `SettingsProvider` (interface) is what the network layer and ViewModels depend on. `SettingsStore` (implementation) needs `Context` + Keystore and is only used by the Settings screen. This seam makes the network layer testable on a plain JVM.
4. **Streaming with REST fallback** — The app opens a WebSocket to `/api/chat/stream`. If the handshake is refused (proxy, CDN, misconfig) it silently retries the same message over `POST /api/chat`. The user sees no error; the reply arrives whole instead of growing.

## Endpoints the app uses

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/health` | Server status + runtime map |
| `POST` | `/api/chat` | REST chat (fallback) |
| `WS` | `/api/chat/stream` | Streaming chat |
| `POST` | `/api/screen` | Screen observation (text + accessibility) |
| `POST` | `/api/screen/upload` | Screenshot multipart upload |
| `GET` | `/api/notifications?device_id=` | Poll for companion notifications |
| `GET` | `/api/settings` | Effective config + the settable allow-list |
| `PATCH` | `/api/settings` | Change a setting (all-or-nothing) |
| `POST` | `/api/settings/reset` | Revert all overrides, or named paths |
| `GET` | `/api/providers` | Capabilities, key state, primary/fallback |
| `GET` | `/api/providers/health` | Active chain, whether it is in fallback |
| `POST` | `/api/providers/test` | Probe one provider |
| `PUT` | `/api/providers/{p}/key` | Store an API key (mask returned) |
| `DELETE` | `/api/providers/{p}/key` | Forget a stored API key |

The last eight are the Control Hub's. See `docs/API.md` for their
contracts — particularly that a mask is never accepted as a key, and that
`restart_required` names settings which were saved but are not yet live.

### Authentication

All endpoints, including `/api/health`, require `Authorization: Bearer <token>` when `AURA_SERVER_AUTH_TOKEN` is configured. The token is entered once under Settings → Connection and travels in the WebSocket query string (`?token=`), because a WebSocket handshake has no Authorization header.

The token is a bearer credential for every one of the routes above, including the ones that store API keys, so the app masks it by default, reveals it only on an explicit tap, and prints it nowhere else — not in the hub, not in Advanced, not in diagnostics.

### Session continuity

The server generates a `session_id` on the first reply. The app echoes it on every subsequent request (REST body `session_id`, WS query `session_id`). This is the whole of conversational continuity — no client-side session logic beyond storing that string.

## Building

### Debug APK (for device testing)

```bash
cd android
./gradlew :app:assembleDebug
```

Output: `app/build/outputs/apk/debug/app-debug.apk`

Install on a device:
```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

### Release APK (not yet configured)

The `release` build type exists in `app/build.gradle.kts` but has no `signingConfig`. A release build requires a keystore that must **not** be committed. See the "Signing" section below.

## Running the app

1. Launch the app. If it is unconfigured, use **Open settings** in the empty conversation (or the Settings icon in the top bar).
2. Enter the server URL (e.g. `https://aura.example.com` or `http://192.168.1.10:8000` for local LAN).
   - The app normalizes bare hosts: `host:port` → `http://host:port/` for private addresses, `https://host/` for public.
3. Enter the bearer token (from `AURA_SERVER_AUTH_TOKEN` on the server).
4. Tap **Test connection**. This saves the normalized URL and token, then verifies authenticated `GET /api/health`; wait for the success result.
5. Return to chat, then type a message — the first reply creates the session.

### Screen observation

1. Enable **Screen observation** in Settings.
2. Android will prompt for the Accessibility permission — grant it.
3. The app now sends screen text (via the Accessibility Service) to the server when the throttle permits.
4. The server runs the companion engine on accepted screens and may push a notification.

> The user must explicitly enable this. Default is **off**. The app never captures the screen when disabled.

### Notifications

1. Enable **Notifications** in Settings (default on).
2. The WorkManager polls `/api/notifications` every 15 minutes.
3. A companion notification appears — tap to open the app and see the message in the chat history.
4. Inline reply is **not yet implemented**; the tap-to-open flow is the supported path.

## Project structure (Android)

```
android/
├── app/
│   ├── build.gradle.kts
│   ├── proguard-rules.pro
│   └── src/
│       ├── main/
│       │   ├── AndroidManifest.xml
│       │   ├── java/com/aura/companion/
│       │   │   ├── AuraApplication.kt         # Hand-wired AppContainer
│       │   │   ├── MainActivity.kt            # Compose navigation
│       │   │   ├── data/
│       │   │   │   ├── AuraRepository.kt      # Single door to the server
│       │   │   │   ├── AuraResult.kt          # Ok / Failed sealed class
│       │   │   │   ├── remote/
│       │   │   │   │   ├── ApiFactory.kt      # OkHttp + Retrofit wiring
│       │   │   │   │   ├── AuraApi.kt         # REST interface
│       │   │   │   │   ├── AuraStreamClient.kt # WebSocket streaming
│       │   │   │   │   └── Dto.kt             # Serialization models
│   │   │   │   │   └── ControlDto.kt     # Settings + provider payloads
│       │   │   │   └── settings/
│       │   │   │       ├── SettingsProvider.kt # Read-only interface (testable)
│       │   │   │       ├── SettingsStore.kt   # EncryptedSharedPreferences
│       │   │   │       └── FakeSettings.kt    # Test double
│       │   │   ├── screen/
│       │   │   │   ├── ScreenObservationService.kt # Accessibility service
│       │   │   │   └── ObservationThrottle.kt # Client-side filtering
│       │   │   ├── work/
│       │   │   │   └── NotificationWorker.kt  # 15-min polling
│       │   │   ├── ui/
│       │   │   │   ├── chat/
│       │   │   │   │   ├── ChatScreen.kt
│       │   │   │   │   ├── ChatComponents.kt
│       │   │   │   │   ├── ChatViewModel.kt
│       │   │   │   │   └── ChatUiState.kt
│       │   │   │   ├── components/
│       │   │   │   │   ├── SettingsComponents.kt   # Rows, cards, sections
│       │   │   │   │   ├── ProviderComponents.kt   # Provider/model/key cards
│       │   │   │   │   └── InputComponents.kt      # Dialogs, sliders, steppers
│       │   │   │   ├── hub/                        # Control Hub, 10 sections
│       │   │   │   │   ├── HubScreen.kt            # Grid, HubRoutes, HubSection
│       │   │   │   │   ├── HubViewModel.kt         # One Activity-scoped instance
│       │   │   │   │   ├── DevicePermissions.kt    # Android grant probes
│       │   │   │   │   ├── AuraSection.kt          # Status, version, chain
│       │   │   │   │   ├── ModelsSection.kt        # Providers, models, API keys
│       │   │   │   │   ├── AwarenessSection.kt     # Screen observation
│       │   │   │   │   ├── MemorySection.kt
│       │   │   │   │   ├── ProactiveSection.kt
│       │   │   │   │   ├── VisionSection.kt
│       │   │   │   │   ├── VoiceSection.kt
│       │   │   │   │   ├── NotificationsSection.kt
│       │   │   │   │   ├── GeneralSection.kt       # Appearance + Advanced
│       │   │   │   │   └── ConnectionSection.kt    # Server URL + token
│       │   │   │   └── settings/
│       │   │   │       ├── SettingsScreen.kt   # Superseded by the hub
│       │   │   │       └── SettingsViewModel.kt # Used by ConnectionSection
│       │   │   └── companion/ (future)
│       │   │
│       │   ├── res/
│       │   │   ├── xml/
│       │   │   │   ├── network_security_config.xml  # Cleartext for debug
│       │   │   │   ├── accessibility_service_config.xml
│       │   │   │   └── data_extraction_rules.xml
│       │   │   ├── values/strings.xml
│       │   │   ├── values/themes.xml
│       │   │   ├── values-night/themes.xml
│       │   │   ├── drawable/ic_*.xml
│       │   │   └── mipmap-*/ic_launcher*.xml
│       │   │
│       │   └── debug/res/xml/network_security_config.xml  # Cleartext permitted
│       │
│       └── test/
│           └── java/com/aura/companion/
│               ├── data/
│               │   ├── AuraRepositoryTest.kt       # MockWebServer, ~20 tests
│               │   ├── remote/
│               │   │   └── AuraStreamClientTest.kt # Real WS, 14 tests
│               │   └── settings/
│               │       ├── UrlNormalisationTest.kt # 12 tests
│               │       └── FakeSettings.kt
│               ├── screen/
│               │   └── ObservationThrottleTest.kt  # 17 tests
│               └── ui/chat/
│                   └── ChatViewModelTest.kt        # 15 tests, streaming + fallback
```

## Testing

Unit tests run on the JVM (no device/emulator needed):

```bash
cd android
./gradlew :app:testDebugUnitTest
```

Tests cover (§21):
- Server URL normalisation (12 cases)
- Chat request wire format, session continuity, auth header rotation
- Error mapping: 401/403 → Unauthorized, 502/504 → Waking, 503 `screen_disabled` → Unavailable, 500 → ServerFailure (body never shown)
- Streaming protocol: started → chunk* → complete; error frames; refused handshake; mid-reply close
- Screen throttle: interval, Jaccard similarity, volatile tokens (counters, percentages), app-switch
- ViewModel send lifecycle: streaming success, socket close without terminal frame, REST fallback, failure marks message failed, retry, unconfigured server

> Instrumentation tests (Robolectric/AndroidJUnitRunner) for `SettingsStore` (Keystore), `ScreenObservationService` (accessibility), and `NotificationWorker` (WorkManager) are **not yet written** and are marked UNTESTED in the final report.

## Signing (release)

Release builds require a keystore. The keystore **must not** be committed.

1. Generate a keystore (once):
   ```bash
   keytool -genkeypair -v -keystore aura-release.keystore -alias aura \
     -keyalg RSA -keysize 2048 -validity 10000
   ```
2. Store the keystore path, alias, and passwords in a **local** `gradle.properties` (gitignored) or CI secrets:
   ```properties
   AURA_KEYSTORE_PATH=../aura-release.keystore
   AURA_KEYSTORE_PASSWORD=***
   AURA_KEY_ALIAS=aura
   AURA_KEY_PASSWORD=***
   ```
3. In `app/build.gradle.kts`, add a `signingConfig` that reads these properties (only when all four are present).
4. Build:
   ```bash
   ./gradlew :app:assembleRelease
   ```

Until signing is configured, only the debug APK is available. **Do not claim release readiness from a debug build.**

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| `Connection refused` / `UnknownHostException` | Server URL wrong, server not running, or phone not on same network (for LAN) |
| `401 Unauthorized` | Token mismatch — re-enter in Settings |
| `503 screen_disabled` | Server has `server.screen.enabled=false` in config.yaml |
| `502 Bad Gateway` / `504 Gateway Timeout` | Free-tier container still starting (cold start). Wait ~30s and retry. |
| Send button spins forever | Fixed in `ChatViewModel.streamReply` — settles after collection even without a terminal frame. |
| Accessibility service not receiving events | Permission not granted, or service disabled in Settings → Accessibility → Aura Companion |
| Notifications never arrive | WorkManager needs ~15 min for first poll; check `server.companion.enabled=true` in config.yaml. Settings → Notifications shows which of the three gates is shut. |
| `422` when saving an API key | The field still holds the mask (`••••••••ABCD`). Type the real key, or leave it untouched to keep the current one. |
| A setting saves but nothing changes | It is restart-gated. The hub shows "Needs a restart of Aura" on those rows; the response's `restart_required` is the same fact. |
| A toggle is greyed out with a reason | Either the path is not in the server's allow-list, or the phone cannot do it (dynamic colour below Android 12). The reason is the tooltip. |

## Protocol compatibility

The Android DTOs in `Dto.kt` and the server models in `server/models.py` must stay in sync. The test `AuraRepositoryTest.chat_posts_to_api_slash_chat_in_the_servers_field_names` asserts snake_case field names (`session_id`, `message_id`, `device_id`, `package`, `screen_text`, `accessibility_context`).

If you change the server contract, update the Android DTOs and run the Android unit tests.

## Known limitations (current phase)

- No inline reply on notifications (tap-to-open only)
- **No push.** There is no FCM and no server-initiated delivery. The app
  polls `/api/notifications` on a WorkManager `PeriodicWorkRequest`, whose
  floor is 15 minutes, and only while notifications are enabled and a
  server is configured.
- **The proactive engine has no scheduler of its own.** It gets the
  chance to speak when something polls `/api/notifications` — in practice
  that poll. So turning phone notifications off does not merely mute
  proactive messages, it mostly stops the engine being asked. Settings →
  Proactive states this rather than showing one switch.
- **No voice on Android.** There is no `TextToSpeech` or
  `SpeechRecognizer` anywhere in the app. `voice.tts.enabled` and
  `voice.stt.enabled` run where Aura is deployed, and Settings → Voice
  says so instead of offering phone-side controls that would do nothing.
- **The hub is a control surface, not a mirror.** A change is sent to the
  server and the hub re-reads the result; it does not assume the write
  landed. A device-local toggle (theme, notifications) changes nothing on
  the server, and a server toggle changes nothing on the device.
- `ui/settings/SettingsScreen.kt` is superseded by the hub and no longer
  routed. It was kept rather than deleted; `SettingsViewModel` is still
  live and backs Settings → Connection.
- Screenshot upload uses JPEG; server validates MIME and size (8 MB default)
- Companion notifications require `server.companion.enabled=true` and the relevance threshold/cooldown are server-side
- SQLite persistence on ephemeral filesystems (Render free tier, Cloud Run) needs a mounted volume or external DB — see `docs/DEPLOYMENT.md`
