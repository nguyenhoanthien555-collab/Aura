# Implementation Status

Current state of the Aura codebase after foundation + 10 feature sections.

## Completed Systems

### Core Foundation (Sprint 4)
- ✅ Message architecture with clean boundaries
- ✅ Dependency injection throughout
- ✅ Protocol-based optional collaborators
- ✅ Composition root pattern
- ✅ Path independence (all paths anchored to PROJECT_ROOT)
- ✅ Test infrastructure (pytest, conftest) — see Test Status below

### Section 1: Expression System
- ✅ `avatar/expression.py` - Mood-driven expression selection
- ✅ Expression-to-sprite mapping
- ✅ Integration with avatar state machine

### Section 2: Mood System
- ✅ `brain/mood.py` - Conversation mood tracking
- ✅ Event-driven mood updates
- ✅ Mood influences expression and style

### Section 3: Streaming Responses
- ✅ `brain/streaming.py` - Fragment streaming with sentence aggregation
- ✅ `ConversationManager.chat_stream()` - incremental token delivery
- ✅ `ConversationManager.sentences()` - sentence-level regrouping for TTS
- ✅ Stream events: StreamStartedEvent, StreamChunkEvent, StreamFinishedEvent

### Section 4: Avatar Backend Abstractions
- ✅ `avatar/backends.py` - Backend protocol for Live2D/VRM/PNG
- ✅ `avatar/animation.py` - Animation event handling
- ✅ Renderer abstractions (Tkinter implemented, Live2D/VRM stubs)

### Section 5: Edge TTS Improvements
- ✅ `voice/tts/providers/edge.py` - Full Edge TTS implementation
- ✅ `voice/tts/pacing.py` - Natural speech pacing with commas/periods
- ✅ `voice/tts/audio.py` - Audio format normalization
- ✅ `voice/tts/streaming.py` - Chunk-by-chunk synthesis
- ✅ `voice/tts/values.py` - SSML value parsing (rate, pitch, volume)
- ✅ Cancellation support with process cleanup
- ✅ Timeout handling (synthesis + playback)
- ✅ Configuration: rate, pitch, volume, timeout, playback_timeout

### Section 6: Memory Improvements
- ✅ `memory/profile.py` - Persistent user facts (SQLite)
- ✅ `memory/retrieval.py` - Keyword-based recall over old transcript.
  Implemented and tested, but `memory.recall` ships **false** - see the
  rationale in config.yaml. Lexical token overlap, not semantic: there is
  no embedding model and no vector store anywhere in this codebase.
- ✅ `memory/companion.py` - Session-only companion memory
  - Facts, preferences, goals, projects
  - Coding style observations
  - Conversation highlights
- ✅ `memory/knowledge.py` - Composite knowledge provider
- ✅ Prompt builder integration (MEMORY section)

### Section 7: Tool Framework Decoupling
- ✅ Structural tool protocol (no inheritance required)
- ✅ `tools/registry.py` - Central tool registration
- ✅ `tools/executor.py` - Execution with permission model
- ✅ `tools/timeout.py` - Bounded wait without thread killing
- ✅ Two-lock permission: `tools.enabled` + `tools.allowed`
- ✅ Risk levels: SAFE, RISKY, DANGEROUS
- ✅ Auto-approval by risk level
- ✅ Confirmation handler injection

### Section 8: Plugin System
- ✅ `plugins/base.py` - Plugin protocol and base class
- ✅ `plugins/manager.py` - Lifecycle management
- ✅ `plugins/discovery.py` - Module discovery
- ✅ `plugins/factory.py` - Configuration-driven construction
- ✅ `plugins/builtins/session_stats.py` - Example plugin
- ✅ PluginContext: bus, tools, config
- ✅ Two-lock permission: discovery + `plugins.enabled`
- ✅ Initialization and shutdown with degradation
- ✅ Plugin receives only its own config slice

### Section 9: Character Consistency
- ✅ `brain/consistency.py` - Prompt-construction drift prevention
- ✅ `CharacterAnchor` with threshold-based engagement
- ✅ Three-tier system:
  - `< 6 messages` - nothing (zero tokens)
  - `>= 6 messages` - identity + drift guard
  - `>= 20 messages` - + contradiction awareness
- ✅ IDENTITY section in prompt (between HISTORY and STYLE)
- ✅ Integration with `ConversationManager` and `PromptBuilder`
- ✅ Configuration: enabled, after_messages, contradiction_after, anchor override
- ✅ `anchor_of()` defensive reader
- ✅ `NullAnchor` for disabled state

### Section 10: Animation Events
- ✅ `avatar/animation.py` - `AnimationDirector` derives animation cues
- ✅ Paired lifecycle events in `events/types.py`:
  `ThinkingStarted/Finished`, `TypingStarted/Finished`,
  `SpeechStarted/Finished`
- ✅ `BlinkEvent` on an injected clock via `tick(now)` - no internal timer
- ✅ Derived events are siblings of domain events, never subclasses, so a
  director cannot receive its own output
- ✅ Every pair edge triggered - no finish without its start
- ✅ Avatar controller subscribes to conversation events
- ✅ Expression changes flow through the bus as `ExpressionChangedEvent`

### Phases 6-9: Server mode, Memory 2.0, Control Hub

The ten sections above are the desktop foundation. Four later phases run
on top of it, none of which replaced any of it:

- ✅ **Server mode** (`server/`) - authenticated FastAPI HTTP + WebSocket
  over the same `build_services` composition root the desktop uses.
  Startup refuses a missing `AURA_SERVER_AUTH_TOKEN`. See `docs/API.md`.
- ✅ **Memory 2.0** (`memory/pipeline.py`, `memory/user_model.py`) -
  episodic memories, temporary context and a confirmed/inferred user
  model over the same SQLite session as the transcript. Recall is ranked
  and bounded; still lexical, still no vector store.
- ✅ **Temporal context** (`core/temporal.py`) - one injected clock, so
  no subsystem reads the wall clock on its own.
- ✅ **Proactive system** (`proactive/`) - decision engine plus anti-spam
  gates, all off by default. Pull-driven: it gets its turn when something
  polls `/api/notifications`. There is no background scheduler, which is
  a limitation and not a detail — see Known Limitations.
- ✅ **Control Hub** (`server/routes/settings.py`, `core/settings_store.py`,
  `core/credentials.py`, `android/.../ui/hub/`) - eight bearer-authenticated
  routes let the Android app read effective config, change allow-listed
  settings, inspect providers and store a provider API key encrypted. A
  key is only ever read back masked. See `docs/SECURITY.md`.
- ✅ **Android companion** (`android/`) - Compose chat with WebSocket
  streaming and REST fallback, accessibility screen observation, a
  10-section settings hub. 132 JVM unit tests. See `docs/ANDROID.md`.

## Test Status

The suite runs. Measured with `.venv/Scripts/python.exe -m pytest -q`
during the Phase 9 sweep:

```
1550 passed, 1 deselected
```

The Android app has its own suite, run separately because it needs a JDK
and the Android SDK rather than the Python environment:

```
cd android && ./gradlew :app:testDebugUnitTest :app:lintDebug
132 tests, 0 failures — lint: 0 errors, 44 warnings
```

The deselected test is `tests/test_gemini_integration.py`, gated by
`-m "not integration"` in `pytest.ini` and opt-in via
`AURA_RUN_INTEGRATION=1`. It is the only test that touches the network,
so the hermetic suite passes with no API keys at all — which is what CI
runs.

1550 collected comes from 1297 `def test_` functions across 39 files; the
difference is parametrization. Per-file counts are not reproduced here
because they go stale on every commit — get them from pytest, not from
this file.

An earlier revision of this document said no test had ever been executed
and listed 559 functions across 17 files. Both statements were true when
written (shell execution was unavailable in those sessions) and are now
superseded.

## Configuration Schema

The shipped `config.yaml`, abridged to the keys and defaults that decide
behaviour. `core/config.py` holds the full default tree and merges this
file over it, so an absent key is a default rather than an error.

```yaml
app:
  name: Aura
  version: 0.2.0

llm:
  provider: gemini
  model: gemini-3.6-flash
  # Authoritative. The singular `fallback_provider` is its superseded
  # form, read only when this list is empty; setting both warns at boot.
  fallback_providers: [groq, mistral, openrouter]
  fallback_model: openrouter/free   # OpenRouter's model, not a provider
  ollama_model: qwen3:8b            # only read when ollama is in the chain
  timeout: 120
  max_output_tokens: 768

memory:
  history_limit: 10
  profile: true
  recall: false        # keyword search over the transcript; off by default
  max_facts: 5
  max_recalled: 2

voice:
  tts: {enabled: false}
  stt: {enabled: false}

vision:
  enabled: true         # screen awareness at all
  capture_screen: false # pixels. false = window titles only
  cloud_model: gemini-3.6-flash
  ollama_model: qwen2.5vl:7b
  fallback_model: openrouter/free
  max_pixels: 1500000
  jpeg_quality: 75

avatar:
  enabled: false

tools:
  enabled: true         # whether tools exist at all
  allowed: [current_time]   # which may run; empty grants nothing
  auto_approve: [safe]  # risk levels that skip confirmation
  allowed_paths: []     # empty = the filesystem tools do not register
  applications: {}      # empty = open_application does not register

server:
  screen: {enabled: true, min_interval: 8.0}

logging:
  level: INFO           # AURA_LOG_LEVEL in the environment outranks this
```

`tools.allowed` naming only `current_time` is deliberate, not an
oversight: it is SAFE and reads a clock. Everything that could change a
machine stays unlisted until someone adds it on purpose.

## Not Yet Implemented

From the original roadmap, still pending:

### Section 11: Testing
- **Status:** Executed. `.venv/Scripts/python.exe -m pytest -q` reports
  **1550 passed, 1 deselected** (the deselected one is the opt-in
  integration test, gated by `-m "not integration"` in `pytest.ini`).
- **CI:** `.github/workflows/tests.yml` runs the same hermetic suite on
  every push and pull request. No secrets: the suite must pass with no
  API keys at all. It does **not** run the Android suite — that needs an
  SDK the workflow does not install, so `./gradlew :app:testDebugUnitTest`
  is a local step.

### Section 12: Documentation
- **Status:** README.md, ARCHITECTURE.md, DEVELOPER_GUIDE.md and this
  file exist and were re-checked against the code in the Phase 7 sweep.

### Future Work (Not in Current Roadmap)
- Live2D backend implementation
- VRM backend implementation
- Web interface
- Mobile interface
- Multi-user support
- Cloud deployment
- Real-time collaboration features

## Architecture Highlights

**Zero circular imports** - Each package imports only `core/` and `events/`. The launcher is the single point that imports multiple subsystems together.

**Protocols over inheritance** - `@runtime_checkable` protocols for all optional collaborators. Every protocol is deliberately narrow to avoid breaking existing implementations.

**Degradation everywhere** - Every optional subsystem can be None. A missing TTS engine produces silent replies, not crashes.

**Single composition root** - `launcher/services.py` is the only place that constructs ChatEngine, MemoryManager, and all subsystems.

**Event-driven integration** - Subsystems communicate through EventBus, never direct calls. TTS speaks because it subscribes to ResponseEvent.

**Prompt section ordering** - Fixed order by permanence: SYSTEM → PERSONALITY → CONTEXT → MEMORY → VISION → HISTORY → IDENTITY → STYLE → USER. The last three are instructions ordered so models follow them.

**Defensive readers** - `anchor_of()`, `hint_of()`, `stream_of()` all wrap optional collaborators so a broken one costs a section, never a reply.

## File Counts

Python files by subsystem (counted from the working tree, excluding
`.venv/`):

| Package | Files |
|---|---|
| `tests/` | 40 (39 `test_*` plus `conftest.py`) |
| `brain/` | 32 (including `providers/`) |
| `voice/` | 21 (including `stt/`, `tts/`, their providers) |
| `server/` | 16 (HTTP + WebSocket API, including `routes/`) |
| `memory/` | 14 (transcript, pipeline, user model, retrieval) |
| `tools/` | 10 (including `builtins/`) |
| `vision/` | 10 |
| `avatar/` | 8 |
| `core/` | 8 (config, settings store, credentials, temporal) |
| `plugins/` | 7 (including `builtins/`) |
| `proactive/` | 7 (scheduler tick, decision engine, gates) |
| `scripts/` | 6 (side-effecting `manual_*`, run by hand) |
| `companion/` | 6 (unprompted-notification pipeline) |
| `launcher/` | 4 (plus `launcher.py` at root) |
| `events/` | 3 |

Total: 195 Python files, including `main.py`, `launcher.py`, and
`conftest.py` at the root. Counted from the working tree, excluding
`.venv/`, `.venv-py314-backup/`, `awesome-claude-skills/` and `android/`
— the first three are gitignored and none are Aura source.

The top-level `tts/` package that used to sit alongside `voice/tts/` was
removed in the Phase 7 cleanup. It had no importers and every file but
one was empty. `voice/tts/` is the live implementation.

## Dependencies

### Core (Required)
- `rich` - Terminal formatting
- `python-dotenv` - Environment variables
- `pyyaml` - Configuration
- `sqlalchemy` - Memory storage
- `google-genai` - LLM provider
- `pytest` - Testing

### Optional Extras
- `edge-tts` - Neural voice synthesis (recommended)
- `sounddevice` - Microphone capture
- `faster-whisper` - Speech recognition
- `pyttsx3` - Cross-platform TTS fallback
- `mss` - Screen pixel capture

## Known Limitations

1. **Single conversation thread** - No parallel conversations
2. **Single tenant** - every session reads and writes one transcript, one
   profile and one companion store. `session_id` scopes the metadata
   endpoint only; the auth token is the identity boundary. Two people
   sharing the token share Aura's memory.
3. **SQLite memory** - No distributed memory backend
4. **Session-only companion memory** - `memory/companion.py` holds
   Protocols plus an in-memory implementation and no schema, so it resets
   on restart. The durable half is `memory/profile.py`.
5. **Recall is lexical, not semantic** - token overlap over the messages
   table. No embedding model, no vector store, anywhere in this codebase.
6. **Windows-optimized** - Primary development/testing on Windows
7. **No desktop execution from the server** - Render cannot touch a
   physical PC, and says so rather than describing the action. See
   `tests/test_device_boundary.py`.
8. **No push, and no proactive scheduler** - the two are one limitation.
   Nothing on the server wakes the proactive engine; it gets its turn
   when `GET /api/notifications` is polled, and the only thing polling is
   the Android app's WorkManager job, whose platform floor is 15 minutes.
   So an "unprompted" message is delivered up to 15 minutes late, and
   turning phone notifications off mostly stops the engine being asked at
   all. There is no FCM and no server-initiated delivery.
9. **Build artifacts are tracked again** - `android/app/build/` and
   `android/.gradle/` are in `.gitignore`, but ~2100 files under them are
   still in the index from commits `4ba906e`/`1fe3368`, because
   `.gitignore` does not apply to already-tracked files. Every Android
   build therefore dirties hundreds of files and buries source changes in
   a diff. Fix with `git rm -r --cached android/app/build android/.gradle`
   in a commit of its own.

Two entries in the previous revision of this list - "local only, no
remote access" and "no authentication, designed for single user on local
machine" - were obsolete: `server/` exposes an authenticated HTTP and
WebSocket API and the bearer token is mandatory at startup
(AURA-P1-008). A third, "test execution pending", is superseded by the
Test Status section above.

## Next Steps

Three things, in the order they are worth doing:

1. **A delivery path for unprompted messages.** The proactive and
   companion engines both work and neither can reach a phone on its own.
   Today's delivery is the Android app's 15-minute WorkManager poll,
   whose floor is imposed by the platform. Either a server-side scheduler
   or FCM would fix it; both are real work and neither is started.
2. **The local Windows device agent** — the substantial feature work
   still outstanding. `.claude/task-queue.md` carries the four items;
   `docs/DEPLOYMENT.md` and `.claude/decisions.md` carry the transport
   decision (outbound long-poll, because a container cannot reach a home
   machine unsolicited).
3. **Android instrumentation tests.** `SettingsStore` (Keystore),
   `ScreenObservationService` (accessibility) and `NotificationWorker`
   (WorkManager) have no tests, because all three need a device or
   Robolectric. The 132 JVM tests cover the layers below them.
