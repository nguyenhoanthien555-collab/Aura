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
requirements-server.txt  the subset a container needs
pytest.ini            integration tests excluded by default
Dockerfile            the server image
docker-compose.yml    server plus a persistent volume
.dockerignore
.env.example          every variable, documented; committed
.env                  the real keys; gitignored, never committed
```

`launcher.py` sits next to the `launcher/` package. Python resolves
packages before modules, so `import launcher.cli` finds the package while
`python launcher.py` runs the file as `__main__`.

## core/ — 5 files

Shared foundations. Every other package may import this one.

```
__init__.py
app.py                Aura, the foundation-era composition object
config.py             DEFAULT_CONFIG, load_config, deep_merge
logger.py             the shared logger
paths.py              PROJECT_ROOT and everything anchored to it
```

`paths.py` exists because relative paths broke when Aura was launched from
a parent directory.

An empty `core/events.py` was removed in Phase 7. It held no code and had
no importers, and its name was a trap next to the live `events/` package.

## events/ — 3 files

```
__init__.py
bus.py                EventBus: synchronous pub/sub keyed on event type
types.py              every event dataclass
```

Events are facts ("the user said X"), never UI state.

## brain/ — 39 files

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
llm.py                back-compat shim re-exporting brain.ports.LLM

agent_mode.py         the machine-turn path: JSON in, JSON out
tool_calling.py       the tool-calling loop                (Phase 3)

prompt_builder.py     assembles the prompt from its sections
prompt_sections.py    the section headers, in load-bearing order
prompt.py             an early Prompt dataclass, superseded by
                      prompt_builder.py and now unreferenced
system.py             loads prompts/system.md
personality.py        loads prompts/personality.md
context_loader.py     loads prompts/contexts/*.md

consistency.py        character consistency guard      (Section 9)
style.py              reply style layer
mood.py               conversation mood                 (Section 2)
streaming.py          fragment and sentence streaming   (Section 3)

providers/
    __init__.py
    base.py           BaseProvider, split_prompt
    errors.py         the provider error taxonomy
    fallback.py       FallbackProvider: the failover chain
    gemini.py         Google Gemini
    groq.py           Groq
    mistral.py        Mistral
    openrouter.py     OpenRouter, text and vision
    ollama.py         local models
    http_chat.py      shared HTTP client: keys, errors, prompt split
    openai_compatible.py  the OpenAI wire format, on top of http_chat
    openai.py         OpenAI
    anthropic.py      Anthropic Claude (its own wire format)
    cerebras.py       Cerebras
    xai.py            xAI Grok
    deepseek.py       DeepSeek
    qwen.py           Alibaba DashScope
    mock.py           offline default
```

`consistency.py`, `style.py`, and `mood.py` are the three personality
layers, in descending permanence.

`router.py` is the list of providers that can actually be selected:
`PROVIDER_KEYS` names every cloud provider and the variable it needs,
`KEYLESS_PROVIDERS` holds `ollama`, and `mock` is handled directly.
`HTTP_CHAT_PROVIDERS` is the subset built on `http_chat.py` — one row per
provider, and the only place that names the module — so
`_instantiate_provider` has one generic branch for the six of them after
the five hand-written ones.

`http_chat.py` owns the system/user prompt split, so a provider cannot
forget it. `cerebras.py` was written and deliberately left unregistered
because its own `generate` skipped that split (AURA-P2-003); it is
registered now that the split lives one level up, and a test pins
`CerebrasProvider.generate is HttpChatProvider.generate`.
`anthropic.py` subclasses `http_chat.py` rather than
`openai_compatible.py`: different auth header, a top-level `system`, and
content blocks instead of a message string.

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

## voice/ — 21 files

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

## vision/ — 9 files

```
__init__.py
manager.py            VisionManager: rate-limited observation
capture.py            ScreenshotCapture (needs mss)
context.py            VisionContext: source and description
processor.py          turns a capture into a description
ollama_processor.py   local vision model (needs pillow)
cloud_processor.py    Gemini/OpenRouter vision, used in server mode
remote.py             screen observations supplied by the phone
debug.py              python -m vision.debug: verify what the model sees
```

Off by default. Reading someone's screen is opt in, always.

`processor.py` reads window titles and needs nothing installed;
`ollama_processor.py` reads pixels with a local vision model. The manager
defaults to the former, and `launcher/services.py` injects the latter when
`vision.capture_screen` is on, so importing `vision.capture` never pulls in
the image stack.

`cloud_processor.py` and `remote.py` are the server's half: there is no
screen to grab in a container, so the pixels arrive over HTTP from the
Android app and are described by a cloud model.

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

## tools/ — 10 files

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

## server/ — 14 files

The cloud half. FastAPI over the same `AuraRuntime` the desktop uses —
no avatar, no CLI, no microphone.

```
__init__.py
main.py               the FastAPI app and its lifespan
config.py             ServerConfig: env-driven settings
auth.py               bearer token, mandatory unless AURA_ALLOW_INSECURE
errors.py             error responses that name the failure
models.py             request and response schemas
session.py            SessionStore: per-client conversation state
runtime.py            server-mode runtime construction
notifications.py      notification fan-out
routes/
    health.py         / , /api/ready , /api/health
    chat.py           POST /api/chat
    ws_chat.py        the streaming WebSocket
    screen.py         screen observations posted by the phone
    notifications.py  device notifications
```

The routes look unreferenced to a naive search: nothing imports them by
name except `main.py`, which registers each with `include_router`.

This process cannot touch the machine the user is sitting at. It runs
tools inside its own container or not at all, and `tests/test_device_boundary.py`
pins that it never claims otherwise.

## companion/ — 6 files

Proactive behaviour: deciding whether Aura should say something
unprompted, and refusing to become noise.

```
__init__.py
engine.py             observe(observation) -> CompanionDecision
detector.py           gate 1: did the screen actually change?
evaluator.py          gate 2: is the change worth a word?
policy.py             gate 3: is now a reasonable moment to say it?
decision.py           the frozen decision record
```

Three gates in order, stopping at the first "no". They are separate
because they fail differently — a wrong relevance call and a badly timed
one are not the same mistake. `decision.py` records the reasoning on a
"no" as well as a "yes", because a decision that only explains itself
when it fires cannot be tuned.

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

## tests/ — 42 files, all `test_*`

```
test_pipeline.py                foundation pipeline
test_config.py                  config loading and merging
test_events.py                  the bus
test_memory_v2.py               profile, retrieval, knowledge
test_companion.py               companion stores
test_companion_memory.py        session context      (Section 6)
test_voice.py                   engines and providers
test_voice_edge.py              Edge TTS             (Section 5)
test_voice_cancel.py            cancellation         (Section 5)
test_cli_voice.py               CLI voice wiring
test_vision.py                  observation and prompt section
test_vision_monitor.py          monitor selection
test_vision_ollama.py           local vision processor
test_remote_vision.py           phone-supplied screen source
test_avatar.py                  controller and state
test_tools.py                   registry and executor
test_tool_framework.py          structural tools     (Section 7)
test_tool_calling.py            the tool-calling loop
test_plugins.py                 plugin lifecycle     (Section 8)
test_character_consistency.py   the drift guard      (Section 9)
test_personality_style.py       style layer
test_integration_flows.py       cross-subsystem
test_machine_turns.py           agent turns vs chat turns
test_accessibility_agent.py     Android accessibility agent
test_agent_protocol.py          the machine-turn JSON contract
test_device_boundary.py         what the server may not claim to do
test_provider_resolution.py     which provider is chosen, and why
test_cloud_providers.py         the shared HTTP provider client
test_cloud_failover.py          the fallback chain
test_deploy_startup.py          the deploy boots on the deployed Python
test_error_visibility.py        failures reach a log or a caller
test_security_hardening.py      auth, CORS, limits
test_server.py                  routes and sessions
test_settings_api.py            the credential store and settings API
test_settings_contract.py       the Android ↔ server settings contract
test_memory_2.py                three memory kinds, one pipeline
test_memory_integration.py      a real turn reaching that pipeline
test_temporal.py                the injected clock
test_proactive.py               the proactive decision engine
test_vision_settings.py         which model each vision processor gets
test_notifications.py           notification routing
test_gemini_integration.py      opt in: AURA_RUN_INTEGRATION=1
```

All of these are hermetic except the last, which is marked `integration`
and deselected by default. The side-effecting `manual_*` scripts moved to
`scripts/` in Phase 7; they were never pytest modules.

## scripts/ — 6 files

```
manual_clear_memory.py          side-effecting, run by hand
manual_env_check.py
manual_gemini_test.py
manual_list_models.py
manual_memory_test.py
manual_prompt_test.py
```

## docs/

```
ARCHITECTURE.md           how the pieces fit and why
IMPLEMENTATION_STATUS.md  what is built, what is not
DEVELOPER_GUIDE.md        how to work on it
ROADMAP.md                where it has been and is going
FOLDER_STRUCTURE.md       this file
API.md                    the HTTP and WebSocket surface
ANDROID.md                the Kotlin app
DEPLOYMENT.md             free-tier hosting, platform by platform
SECURITY.md               the threat model and what enforces it
PERFORMANCE.md            measured costs
CLOUD_MIGRATION_AUDIT.md  historical snapshot, superseded in part
```

## Totals

168 Python files outside `.venv/`, `__pycache__/` and the vendored
`awesome-claude-skills/`. Largest are `brain/` (32) and `tests/` (32),
then `voice/` (21) and `server/` (14); the rest are under a dozen each.

There is also `android/`, which is Kotlin rather than Python and is
documented in `docs/ANDROID.md`.
