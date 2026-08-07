# Folder Structure

Every Python module in the working tree, with what it is for. Counted and
listed from the actual tree, not from the plan.

## Root

```
main.py               original text harness (foundation era)
launcher.py           desktop runtime: avatar, CLI, flags
conftest.py           puts the project root on sys.path for pytest
config.yaml           user configuration, created on first run
requirements.txt      core dependencies, with optional extras documented
pytest.ini            integration tests excluded by default
.env                  GEMINI_API_KEY (not committed)
```

`launcher.py` sits next to the `launcher/` package. Python resolves
packages before modules, so `import launcher.cli` finds the package while
`python launcher.py` runs the file as `__main__`.

## core/ — 6 files

Shared foundations. Every other package may import this one.

```
__init__.py
app.py                Aura, the foundation-era composition object
config.py             DEFAULT_CONFIG, load_config, deep_merge
events.py             legacy event helpers
logger.py             the shared logger
paths.py              PROJECT_ROOT and everything anchored to it
```

`paths.py` exists because relative paths broke when Aura was launched from
a parent directory.

## events/ — 3 files

```
__init__.py
bus.py                EventBus: synchronous pub/sub keyed on event type
types.py              every event dataclass
```

Events are facts ("the user said X"), never UI state.

## brain/ — 25 files

The conversation engine. Imports `core/` and `events/`, nothing else
horizontal.

```
__init__.py
chat_engine.py        composition root for the conversation
conversation.py       ConversationManager: owns one turn
adapters.py           ORM record -> pipeline Message, one direction only
message.py            Message dataclass (role, content)
response.py           Response dataclass
ports.py              LLM, ConversationStore, MessageRecord, and friends
router.py             BrainRouter: picks and lazily builds a provider
llm.py                provider-facing helpers

prompt_builder.py     assembles the prompt from its sections
prompt_sections.py    the section headers, in load-bearing order
prompt.py             prompt helpers
system.py             loads prompts/system.md
personality.py        loads prompts/personality.md
context_loader.py     loads prompts/contexts/*.md

consistency.py        character consistency guard      (Section 9)
style.py              reply style layer
mood.py               conversation mood                 (Section 2)
streaming.py          fragment and sentence streaming   (Section 3)

providers/
    __init__.py
    base.py           shared provider behaviour
    gemini.py         Google Gemini
    mock.py           offline default
    ollama.py         local models
    openai.py         OpenAI-compatible endpoints
```

`consistency.py`, `style.py`, and `mood.py` are the three personality
layers, in descending permanence.

## memory/ — 8 files

```
__init__.py
manager.py            MemoryManager: conversation history
models.py             SQLAlchemy models
sqlite.py             engine and session setup
profile.py            durable facts the user asked her to remember
retrieval.py          KeywordRetriever, NullRetriever
companion.py          session context: projects, goals, style  (Section 6)
knowledge.py          MemoryKnowledgeProvider: composes the above
```

## voice/ — 23 files

```
__init__.py
factory.py            builds providers and the microphone from config

stt/
    __init__.py
    engine.py         SpeechToTextEngine, KeywordWakeWord
    microphone.py     capture
    provider.py       the STT protocol
    providers/
        __init__.py
        mock.py
        whisper.py    faster-whisper

tts/
    __init__.py
    engine.py         TTSEngine: subscribes to ResponseEvent
    provider.py       the TTS protocol
    audio.py          format normalization           (Section 5)
    pacing.py         natural rhythm at punctuation  (Section 5)
    streaming.py      chunk-by-chunk synthesis       (Section 5)
    values.py         one SSML-shaped scale for every provider
    providers/
        __init__.py
        edge.py       Edge TTS — Aura's intended voice
        mock.py       silent fallback
        pyttsx.py     cross-platform
        sapi.py       Windows built-in voices
```

## tts/ — 6 files (legacy)

```
__init__.py
base.py
manager.py
providers/
    edge.py
    elevenlabs.py
    kokoro.py
```

Predates `voice/tts/`, which is the live implementation. Flagged for the
cleanup pass; removal should follow a check for importers.

## vision/ — 5 files

```
__init__.py
manager.py            VisionManager: rate-limited observation
capture.py            ScreenshotCapture (needs mss)
context.py            VisionContext: source and description
processor.py          turns a capture into a description
```

Off by default. Reading someone's screen is opt in, always.

## avatar/ — 8 files

```
__init__.py
controller.py         AvatarController: derives display state from events
renderer.py           the Tkinter renderer, and the null renderer
window.py             the floating window
state.py              the state machine
expression.py         mood -> expression                (Section 1)
animation.py          animation event handling          (Section 10)
backends.py           backend protocol for Live2D/VRM   (Section 4)
```

Degrades to a null renderer with no display.

## tools/ — 11 files

```
__init__.py
base.py               ToolRisk, ToolResult, fail()
registry.py           registration and lookup
executor.py           permission checks and execution
factory.py            builds the registry and executor from config
timeout.py            bounds the wait, not the tool    (Section 7)
builtins/
    __init__.py
    apps.py           launch permitted applications
    clock.py          time and date
    filesystem.py     read_file, list_directory
```

Two locks: `tools.enabled` and the name in `tools.allowed`.

## plugins/ — 7 files

```
__init__.py
base.py               PluginProtocol, Plugin, PluginContext
manager.py            PluginManager: lifecycle
discovery.py          finds plugin modules
factory.py            builds from config
builtins/
    __init__.py
    session_stats.py  the worked example
```

A plugin gets the bus and the tool registry — never the executor.

## launcher/ — 4 files

```
__init__.py
services.py           the composition root: builds every subsystem
runtime.py            AuraRuntime: owns the assembled system
cli.py                AuraCLI: the terminal front end
```

`services.py` is the only module that imports brain, memory, voice,
vision, avatar, tools, and plugins together. That is precisely why none of
those import each other.

## prompts/

```
system.md             hard rules
personality.md        who Aura is
contexts/
    coding.md
    desktop.md
    minecraft.md
    research.md
    vision.md
```

## tests/ — 23 files (17 `test_*`, 6 `manual_*`)

```
test_pipeline.py                foundation pipeline
test_config.py                  config loading and merging
test_events.py                  the bus
test_memory_v2.py               profile, retrieval, knowledge
test_companion_memory.py        session context      (Section 6)
test_voice.py                   engines and providers
test_voice_edge.py              Edge TTS             (Section 5)
test_voice_cancel.py            cancellation         (Section 5)
test_vision.py                  observation and prompt section
test_avatar.py                  controller and state
test_tools.py                   registry and executor
test_tool_framework.py          structural tools     (Section 7)
test_plugins.py                 plugin lifecycle     (Section 8)
test_character_consistency.py   the drift guard      (Section 9)
test_personality_style.py       style layer
test_integration_flows.py       cross-subsystem
test_gemini_integration.py      opt in: AURA_RUN_INTEGRATION=1

manual_clear_memory.py          side-effecting scripts, run by hand
manual_env_check.py
manual_gemini_test.py
manual_list_models.py
manual_memory_test.py
manual_prompt_test.py
```

`test_*` files are hermetic. `manual_*` files touch the network or the
database and are named so pytest does not collect them.

## docs/

```
ARCHITECTURE.md           how the pieces fit and why
IMPLEMENTATION_STATUS.md  what is built, what is not
DEVELOPER_GUIDE.md        how to work on it
ROADMAP.md                where it has been and is going
FOLDER_STRUCTURE.md       this file
```

## Totals

131 Python files outside `.venv/`. Largest packages are `brain/` (25) and
`voice/` (23); the rest are under a dozen each.
