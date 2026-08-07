# Aura

Aura is a conversational AI companion with personality, memory, voice, vision, and extensibility. She's designed to be warm, direct, and helpful — a partner for thinking through problems, working on projects, and getting things done.

## What Makes Aura Different

- **Personality that stays consistent** — Aura maintains her character across long conversations through prompt construction techniques that prevent drift into generic assistant register
- **Companion memory** — Session context about you, your projects, preferences, and coding style, combined with long-term profile facts
- **Voice in, voice out** — Optional speech recognition and neural TTS with natural pacing
- **Contextual awareness** — Can observe your desktop environment when enabled
- **Tool-capable** — File operations, launching applications, and extensible through plugins
- **Modular architecture** — Every subsystem (voice, vision, tools, avatar) is optional and degrades gracefully

## Installation

### Requirements

- Python 3.10 or later
- Windows, macOS, or Linux
- A Gemini API key (free tier works fine)

### Core Setup

```bash
# Clone the repository
git clone <repository-url>
cd aura

# Create virtual environment
python -m venv .venv

# Activate (Windows)
.\.venv\Scripts\activate

# Activate (macOS/Linux)
source .venv/bin/activate

# Install core dependencies
pip install -r requirements.txt
```

### API Key

Create a `.env` file in the project root:

```
GEMINI_API_KEY=your_key_here
```

Get a free API key at [https://aistudio.google.com/](https://aistudio.google.com/)

### Optional Features

Aura runs as a text companion with just the core dependencies. Install extras only if you want them:

```bash
# Edge TTS - Aura's intended voice (warm, expressive neural speech)
pip install edge-tts

# Speech recognition
pip install sounddevice faster-whisper

# Screen capture for vision
pip install mss

# Cross-platform TTS fallback
pip install pyttsx3
```

## Quick Start

### Text Mode

```bash
python launcher.py
```

You'll get an interactive CLI. Type naturally, use `/help` for commands.

### Voice Mode

Edit `config.yaml`:

```yaml
voice:
  tts:
    enabled: true
    provider: edge     # or "auto" for system default
  stt:
    enabled: true
    provider: whisper
```

Then run:

```bash
python launcher.py
```

Use `/voice` to record a message, or enable continuous listening with a wake word.

## Architecture

Aura is built from nine subsystems, each completely independent:

```
launcher/          Application runtime and service composition
brain/             Conversation engine and prompt construction
memory/            Short and long term memory (SQLite + in-memory)
voice/             Speech recognition and TTS
vision/            Desktop context (window titles + optional screen capture)
avatar/            Visual representation (Tkinter window)
tools/             Function execution framework
plugins/           Third-party extensions
events/            Pub/sub bus connecting everything
```

### Key Design Principles

**No circular imports** — Each package imports only from `core/` and `events/`. The launcher is the only place that imports multiple subsystems together.

**Protocols over inheritance** — Optional collaborators use `@runtime_checkable` protocols. A user's custom TTS provider needs three methods and zero inheritance.

**Fail soft** — A broken TTS engine produces silent replies, not crashes. Every optional subsystem can be None.

**One composition root** — `launcher/services.py` builds everything. Nothing else constructs ChatEngine or MemoryManager.

## Configuration

`config.yaml` is created on first run with safe defaults. The structure:

```yaml
llm:
  provider: mock | gemini
  model: gemini-2.5-flash
  temperature: 0.7

memory:
  history_limit: 20
  profile: true          # Long-term facts about you
  recall: false          # Keyword search over old transcript
  companion: true        # Session context (projects, preferences, style)

personality:
  style:
    enabled: true
    strip_filler: true   # Remove "Certainly!", "I apologize", etc.
    avoid_repeats: 3     # Don't reuse recent opening phrases
    
  consistency:
    enabled: true
    after_messages: 6            # When to engage the identity guard
    contradiction_after: 20      # When to add contradiction awareness

voice:
  tts:
    enabled: false
    provider: auto | edge | pyttsx3 | sapi
    rate: "+5%"
    pitch: "+10Hz"
    
  stt:
    enabled: false
    provider: whisper
    wake_word: ""        # Empty = push-to-talk only

vision:
  enabled: false
  capture_screen: false  # true needs `mss` package
  min_interval: 2.0      # Seconds between observations

tools:
  enabled: false
  allowed: []            # Tool names: read_file, list_directory, etc.
  auto_approve: [safe]   # Risk levels that run without asking
  allowed_paths: []      # Directories read_file may touch

plugins:
  enabled: []            # Plugin names, or `true` for all discovered
  directory: ""          # Extra search path beyond plugins/builtins/
```

## CLI Commands

```
/help                   Show commands
/voice                  Listen once through microphone
/say <text>             Speak text out loud
/remember key value     Store a fact about you
/forget key             Remove a fact
/profile                Show remembered facts
/look                   Refresh vision context
/tools                  List permitted tools
/run name [k=v ...]     Execute a tool
/state                  Current avatar state
/quit                   Exit
```

## Development

### Running Tests

```bash
# All tests
pytest

# One subsystem
pytest tests/test_memory_v2.py

# Verbose
pytest -v

# With output
pytest -s
```

### Project Structure

```
aura/
├── avatar/          Visual controller and state machine
├── brain/           ChatEngine, PromptBuilder, conversation flow
├── core/            Config, logging, paths, shared utilities
├── docs/            Architecture notes and sprint summaries
├── events/          EventBus and event types
├── launcher/        Runtime, CLI, service composition
├── memory/          Conversation history, profile, retrieval
├── plugins/         Plugin framework and builtins
├── prompts/         personality.md and system prompt
├── tests/           Comprehensive test suite (287 tests)
├── tools/           Tool registry, executor, built-in tools
├── voice/           STT and TTS engines
├── vision/          Desktop context provider
├── config.yaml      User configuration
├── launcher.py      Desktop CLI entry point
├── main.py          Original test harness
└── requirements.txt Core dependencies
```

### Adding a Plugin

Create `plugins/builtins/your_plugin.py`:

```python
from plugins.base import Plugin, PluginContext

class YourPlugin(Plugin):
    name = "your_plugin"
    version = "0.1.0"
    
    def initialize(self, context: PluginContext) -> None:
        # Subscribe to events
        if context.bus:
            context.bus.subscribe(
                "ResponseEvent",
                self._on_response
            )
        
        # Register tools
        if context.tools:
            context.tools.register(your_tool)
    
    def _on_response(self, event):
        # React to Aura's replies
        pass
```

Enable it in `config.yaml`:

```yaml
plugins:
  enabled: [your_plugin]
```

### Code Style

- Type hints on public APIs
- Docstrings on modules and non-obvious functions
- Test new functionality
- Keep subsystems decoupled — if you're tempted to import `brain/` from `voice/`, the dependency goes the wrong way

## Current Status

**Completed:**
- Core conversation engine with streaming
- Memory system (profile + companion + keyword retrieval)
- Voice I/O with Edge TTS and Whisper
- Vision system (window titles + screen capture)
- Avatar with expression and animation events
- Tool framework with permission model
- Plugin system with lifecycle management
- Personality style layer (filler stripping, opening variation)
- Character consistency guard (long conversation drift prevention)

**Not Yet Implemented:**
- Web/mobile interfaces
- Multi-user support
- Cloud deployment
- Live2D/VRM avatar backends

## License

[Add your license here]

## Contributing

[Add contribution guidelines]
