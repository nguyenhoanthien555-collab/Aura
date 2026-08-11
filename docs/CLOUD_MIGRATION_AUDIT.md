# Aura Cloud Migration Audit

**Date:** 2026-08-09
**Branch:** feature/aura-identity
**Tests at the time of the audit:** 659 passing

> **Status: historical snapshot, superseded in part.**
>
> This document records what the codebase looked like when the migration
> audit was taken, including problems that have since been fixed. It is
> kept for the reasoning, not as a current status page. Read it as "what
> we found", not "what is true now".
>
> As of the Phase 7 sweep the suite reports **1160 passed, 1 deselected**
> (`.venv/Scripts/python.exe -m pytest -q`). The findings this audit
> raised as AURA-P0-* and AURA-P1-* were worked through in Phases 1-6;
> `.claude/task-queue.md` carries the per-item status and
> `.claude/progress.md` the record of what changed.
>
> For current state, prefer `docs/IMPLEMENTATION_STATUS.md` and
> `.claude/project-state.md`.

---

## 1. EXISTING ARCHITECTURE OVERVIEW

### Entry Points

| Entry Point | Purpose |
|-------------|---------|
| `main.py` | Original Sprint 4 text harness - simple `Aura().start()` + `chat()` |
| `launcher.py` | Desktop runtime with avatar, CLI, voice, vision flags |

### Runtime Composition

```
AuraRuntime (launcher/runtime.py)
├── Services (launcher/services.py) - Composition root
│   ├── EventBus (events/bus.py)
│   ├── MemoryManager (memory/manager.py) - SQLite conversation history
│   ├── ProfileStore (memory/profile.py) - Durable user facts
│   ├── MemoryKnowledgeProvider (memory/knowledge.py) - Profile + keyword recall
│   ├── CompanionMemory (memory/companion.py) - Session context (facts, goals, projects, style)
│   ├── ChatEngine (brain/chat_engine.py) - Composition root for conversation
│   │   ├── ConversationManager (brain/conversation.py) - Owns one turn
│   │   ├── PromptBuilder (brain/prompt_builder.py) - Assembles prompt sections
│   │   ├── BrainRouter (brain/router.py) - Lazy provider selection
│   │   ├── ResponseStyler (brain/style.py) - Reply style layer
│   │   └── IdentityAnchor (brain/consistency.py) - Character consistency
│   ├── VisionManager (vision/manager.py) - Rate-limited screen observation
│   ├── TTSEngine (voice/tts/engine.py) - Subscribes to ResponseEvent
│   ├── SpeechToTextEngine (voice/stt/engine.py) - Microphone + provider
│   ├── ToolExecutor (tools/executor.py) - Permission checks + execution
│   ├── AvatarController (avatar/controller.py) - Derives display state from events
│   └── PluginManager (plugins/manager.py) - Plugin lifecycle
```

### Brain Call Chain

```
User Input
    ↓
AuraRuntime.chat(message)  (launcher/runtime.py:169)
    ↓
Services.engine.chat(message)  (brain/chat_engine.py:120)
    ↓
ConversationManager.chat()  (brain/conversation.py:83)
    ↓
_prepare() → builds prompt via PromptBuilder
    ↓
ThinkingEvent published
    ↓
LLM.generate(prompt) via BrainRouter
    ↓
Response(text) created
    ↓
_styled() via ResponseStyler
    ↓
Memory saves user_msg + reply
    ↓
ResponseEvent published
    ↓
Return Response
```

### Streaming Call Chain

```
ConversationManager.chat_stream()
    ↓
_prepare() (shared with chat)
    ↓
ThinkingEvent
    ↓
StreamStartedEvent
    ↓
for fragment in stream_of(llm, prompt):
    → StreamChunkEvent + yield fragment
    ↓
StreamFinishedEvent (styled text)
    ↓
Memory saves
    ↓
ResponseEvent(streamed=True)
```

### Provider Lifecycle

```
BrainRouter (lazy init on first .provider access)
    ↓
_create_provider(name) → MockProvider | GeminiProvider | GroqProvider |
                        MistralProvider | OpenRouterProvider | OllamaProvider
    ↓
Provider.generate(prompt) → str
    ↓
Provider.stream(prompt) → Iterator[str]  (optional capability)
```

### Memory Flow

```
ConversationManager.chat()
    ↓
memory.save(role, content)  (MemoryManager - SQLite messages table)
    ↓
ProfileStore.remember(key, value)  (UserFact table - explicit /remember)
    ↓
KeywordRetriever.search(query)  (lexical recall from messages table)
    ↓
MemoryKnowledgeProvider.get_knowledge(query) → [profile facts + recalled lines]
    ↓
CompanionMemory.get_knowledge(query) → [facts, preferences, goals, projects, style, highlights]
    ↓
_CompositeKnowledge merges both
```

### Event Flow

```
EventBus (synchronous pub/sub)
    ├── UserInputEvent(text, source)
    ├── ThinkingEvent
    ├── StreamStartedEvent
    ├── StreamChunkEvent(text, index)
    ├── StreamFinishedEvent(text, ok, chunks)
    ├── ResponseEvent(text, streamed)
    ├── ErrorEvent(message, source)
    ├── VisionUpdateEvent(source, description)
    ├── StateChangedEvent(state)
    ├── MoodChangedEvent(mood, reason)
    ├── ExpressionChangedEvent(expression, hold, reason)
    ├── ListeningEvent(active)
    ├── TranscriptEvent(text)
    ├── SpeakingEvent(text, active, voice, duration)
    ├── ToolInvokedEvent(name, arguments)
    ├── ToolCompletedEvent(name, ok, detail)
    └── ... (avatar lifecycle events)
```

### Vision Flow

```
VisionManager.get_context()
    ↓
_check throttle (min_interval default 2s)
    ↓
refresh() → _observe()
    ↓
_active_window() via WindowsWindowReader (ctypes user32)
_grab() via ScreenshotCapture (mss) if capture_screen enabled
    ↓
Processor.describe(frame, title)
    ├── WindowTitleProcessor → "User is editing Python code in VS Code"
    └── OllamaVisionProcessor → local vision model description
    ↓
VisionContext(source, description)
    ↓
VisionUpdateEvent published if description changed
```

### Desktop-Only Dependencies

| Component | Windows-Only | Dependency |
|-----------|-------------|------------|
| `avatar/renderer.py` | Yes | `tkinter` (ships with Python) |
| `vision/capture.py` - WindowsWindowReader | Yes | `ctypes.windll.user32` |
| `vision/capture.py` - ScreenshotCapture | No | `mss` (cross-platform) |
| `voice/tts/providers/sapi.py` | Yes | `ctypes.windll.sapi` |
| `voice/tts/providers/pyttsx.py` | No | `pyttsx3` (cross-platform) |
| `voice/tts/providers/edge.py` | No | `edge-tts` (cross-platform) |
| `voice/stt/microphone.py` | No | `sounddevice` (cross-platform) |

### Synchronous vs Async Behavior

| Component | Behavior |
|-----------|----------|
| EventBus | Synchronous - handlers run inline |
| BrainRouter.generate() | Synchronous (blocking HTTP) |
| BrainRouter.stream() | Synchronous generator |
| MemoryManager | Synchronous SQLAlchemy |
| VisionManager | Synchronous |
| TTSEngine | Synchronous (blocks on provider) |
| SpeechToTextEngine | Synchronous |
| AuraRuntime.run() | Spawns worker thread for CLI if avatar needs main thread |

---

## 2. TEST BASELINE

**At the time of this audit:** 659 passed, 1 skipped, 1 deselected
**Duration:** 2.00s

That was the baseline the migration work started from. It is not the
current number - see the status note at the top of this file.

---

## 3. MIGRATION STRATEGY

### Phase 0 ✅ Complete
- Repository audit complete
- Baseline tests recorded (659 passing)

### Phase 1: Server Runtime (P0)
- Create `server/` module for FastAPI server
- Reuse existing `AuraRuntime` and `Services` composition
- Add server mode to `AuraRuntime` (no avatar, no CLI)
- Single initialization of Brain, Memory, Providers

### Phase 2: API Layer (P0)
- FastAPI with thin routes
- `/api/health` - status, version, uptime
- `/api/chat` - session-based chat using existing pipeline
- Bearer token authentication
- CORS configuration

### Phase 3: Streaming (P0)
- WebSocket `/api/chat/stream` 
- Reuse existing `ConversationManager.chat_stream()`
- Publish StreamChunkEvent via WebSocket

### Phase 4: Android MVP (P1)
- Kotlin + Jetpack Compose project
- Network layer for health/chat/stream
- Secure token storage (Android Keystore)
- Chat UI with loading states

### Phase 5+: Screen, Companion, Voice, Docker, Deployment

---

## 4. KEY REUSE POINTS (NO REWRITES)

| Existing | Reuse As |
|----------|----------|
| `AuraRuntime` | Server core - just don't start avatar/CLI |
| `Services` | Dependency injection bundle |
| `ChatEngine` | Single chat entry point |
| `ConversationManager` | Chat + streaming logic |
| `BrainRouter` | Provider management |
| `MemoryManager` | Conversation persistence |
| `ProfileStore` | User facts |
| `MemoryKnowledgeProvider` | Long-term memory |
| `CompanionMemory` | Session context |
| `EventBus` | Event distribution |
| `VisionManager` | Screen context for `/api/screen` |
| `TTSEngine` | Voice responses (later) |
| `PromptBuilder` | Prompt assembly |

---

## 5. CONFIGURATION SEPARATION

### Server Config (new)
```yaml
server:
  host: "0.0.0.0"
  port: 8000
  auth_token: ""  # from env
  cors_origins: ["*"]
```

### Android Config (new)
```yaml
android:
  server_url: "http://192.168.x.x:8000"
  device_id: ""  # generated
```

---

## 6. FILES TO CREATE

### Phase 1: Server
- `server/__init__.py`
- `server/main.py` - FastAPI app
- `server/routes/health.py`
- `server/routes/chat.py`
- `server/routes/ws_chat.py`
- `server/auth.py` - Bearer token
- `server/session.py` - Session management
- `server/runtime.py` - Server-mode AuraRuntime

### Phase 2: Config
- `.env.example`
- Update `.gitignore`
- Update `config.yaml` with server section

### Phase 3: Android
- `android/` - Gradle project

---

## 7. RISKS & MITIGATIONS

| Risk | Mitigation |
|------|------------|
| Desktop mode breaks | Keep avatar/CLI in launcher.py, add server mode flag |
| Provider reinitialization | Reuse BrainRouter lazy init - single instance |
| Memory session isolation | Add session_id to conversation, use separate DB or prefix |
| Streaming over HTTP | WebSocket with existing StreamChunkEvent format |
| Windows deps in server | Guard avatar/vision init with config flags |