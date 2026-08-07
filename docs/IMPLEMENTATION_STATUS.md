# Implementation Status

Current state of the Aura codebase after foundation + 10 feature sections.

## Completed Systems

### Core Foundation (Sprint 4)
- ✅ Message architecture with clean boundaries
- ✅ Dependency injection throughout
- ✅ Protocol-based optional collaborators
- ✅ Composition root pattern
- ✅ Path independence (all paths anchored to PROJECT_ROOT)
- ✅ Test infrastructure (pytest, conftest, 559 test functions written)

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
- ✅ `memory/retrieval.py` - Keyword-based recall over old transcript
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

## Test Status

**No test in this repository has been executed in a session that is
recorded here.** Shell execution was unavailable throughout, so the
suite has never been run to a reported result. Per project requirements:
*"Never fabricate execution results. If infrastructure prevents
execution, explicitly state that tests were NOT executed."*

That applies to the earlier "287 tests passing" claim as much as to
Sections 8 and 9 — it was never measured and has been removed.

What is actually known, by counting `^def test_` in the working tree:

| File | Test functions |
|---|---|
| `test_personality_style.py` | 80 |
| `test_tools.py` | 52 |
| `test_plugins.py` | 42 |
| `test_companion_memory.py` | 40 |
| `test_tool_framework.py` | 39 |
| `test_voice.py` | 39 |
| `test_memory_v2.py` | 38 |
| `test_voice_edge.py` | 38 |
| `test_config.py` | 31 |
| `test_integration_flows.py` | 31 |
| `test_pipeline.py` | 23 |
| `test_vision.py` | 23 |
| `test_avatar.py` | 22 |
| `test_voice_cancel.py` | 22 |
| `test_character_consistency.py` | 21 |
| `test_events.py` | 17 |
| `test_gemini_integration.py` | 1 (opt in) |

559 test functions across 17 files. A count is not a result: collection
could fail, and a function that is collected is not a function that
passes.

### Verified by static review instead

Sections 8 and 9 were checked by reading each assertion against the
implementation it exercises. That found one real defect:

- `tests/test_plugins.py` called `ModuleType(...)` in its `fake_module`
  helper without importing it. Eight discovery tests would have failed
  on `NameError`. **Fixed** — `from types import ModuleType` added.

Everything else lines up: `ToolRegistry` has the `register`/`unregister`/
`names` the plugin tests use, `EventBus.subscribe` returns the
unsubscribe callable `session_stats` stores, `handler_count` exists,
`PromptBuilder.build` takes `identity` as a keyword before `style`, and
every symbol `test_character_consistency.py` imports is exported from
`brain/consistency.py`.

Static review is not execution. It catches missing names and changed
signatures; it does not catch a wrong assertion about correct code.

## Configuration Schema

Current `config.yaml` structure:

```yaml
app:
  name: Aura
  version: 0.2.0

llm:
  provider: mock | gemini
  model: gemini-2.5-flash
  temperature: 0.7
  max_output_tokens: 4096

memory:
  history_limit: 20
  profile: true
  recall: false
  max_facts: 8
  max_recalled: 3
  companion: true
  max_companion: 10
  max_highlights: 3

personality:
  style:
    enabled: true
    strip_filler: true
    hint: ""
    avoid_repeats: 3
  
  consistency:
    enabled: true
    after_messages: 6
    contradiction_after: 20
    anchor: ""

voice:
  tts:
    enabled: false
    provider: auto | edge | pyttsx3 | sapi
    voice: ""
    rate: "+5%"
    pitch: "+10Hz"
    volume: 100
    timeout: 60.0
    playback_timeout: 300.0
    playback: true
  
  stt:
    enabled: false
    provider: mock | whisper
    model: base
    language: ""
    record_seconds: 5.0
    wake_word: ""

vision:
  enabled: false
  min_interval: 2.0
  capture_screen: false
  monitor: 1

avatar:
  enabled: true
  size: 160
  scale: 1.0
  opacity: 0.95
  position: null
  sprites_dir: ""

tools:
  enabled: false
  allowed: []
  auto_approve: [safe]
  timeout: 30.0
  allowed_paths: []
  applications: {}

plugins:
  enabled: []
  directory: ""
  config: {}

logging:
  level: INFO
```

## Not Yet Implemented

From the original roadmap, still pending:

### Section 11: Testing
- **Status:** Code complete, 559 test functions written, none executed
- **Next:** Run `./.venv/Scripts/python.exe -m pytest -q` once shell
  execution is available, fix what fails, repeat until green
- **Expected:** unknown, and it stays unknown until it runs

### Section 12: Documentation
- **Status:** In progress (this session)
- **Completed:** README.md, ARCHITECTURE.md, this file
- **Remaining:** Developer guide, update roadmap with completion markers

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
| `brain/` | 25 (including `providers/`) |
| `voice/` | 23 (including `stt/`, `tts/`, their providers) |
| `tests/` | 22 (16 `test_*`, 6 `manual_*`) |
| `tools/` | 11 (including `builtins/`) |
| `memory/` | 8 |
| `avatar/` | 8 |
| `plugins/` | 7 (including `builtins/`) |
| `tts/` | 6 (legacy — see below) |
| `core/` | 6 |
| `vision/` | 5 |
| `launcher/` | 4 (plus `launcher.py` at root) |
| `events/` | 3 |

Total: 131 Python files, including `main.py`, `launcher.py`, and
`conftest.py` at the root. Line count not measured — shell execution was
unavailable this session.

**Legacy duplicate:** a top-level `tts/` package (`base.py`, `manager.py`,
`providers/edge.py`, `elevenlabs.py`, `kokoro.py`) exists alongside
`voice/tts/`. The live implementation is `voice/tts/`; the top-level
package predates it. Flagged for the cleanup pass, not removed yet —
removal should follow a check that nothing imports it.

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
2. **Local only** - No remote access or web interface
3. **SQLite memory** - No distributed memory backend
4. **Session-only companion memory** - Resets on restart
5. **No authentication** - Designed for single user on local machine
6. **Windows-optimized** - Primary development/testing on Windows
7. **Test execution pending** - the suite has never been run to a
   reported result in any recorded session

## Next Steps

1. **Execute test suite** - Once shell classifier returns
2. **Fix any test failures** - Iterate until green
3. **Complete documentation** - Developer guide, updated roadmap
4. **Clean up** - Remove obsolete files, unused imports
5. **User testing** - Real-world usage validation
