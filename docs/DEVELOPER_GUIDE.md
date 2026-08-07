# Developer Guide

Quick reference for working on the Aura codebase.

## First Steps

```bash
# Clone and setup
git clone <repo>
cd aura
python -m venv .venv
source .venv/bin/activate  # or .\.venv\Scripts\activate on Windows
pip install -r requirements.txt

# Create .env file
echo "GEMINI_API_KEY=your_key" > .env

# Run tests
pytest

# Start Aura
python launcher.py
```

## Project Structure

```
aura/
├── brain/           Conversation engine, prompt construction
├── memory/          Conversation history, profile, retrieval
├── voice/           Speech recognition and TTS
├── vision/          Desktop context (window titles + screen capture)
├── avatar/          Visual representation
├── tools/           Function execution framework
├── plugins/         Extension system
├── events/          Pub/sub event bus
├── launcher/        Runtime and service composition
├── core/            Config, logging, paths
├── tests/           Test suite
└── prompts/         personality.md
```

## Architecture Rules

**Zero circular imports** — Subsystems import only `core/` and `events/`, never each other. `launcher/services.py` is the single composition root.

**Protocols over inheritance** — Optional collaborators use `@runtime_checkable` protocols. Keep them narrow: adding a name breaks every existing structural implementation.

**Fail soft** — A broken TTS engine produces silent replies, not crashes. Check for None before calling optional subsystems.

**Event-driven** — Subsystems communicate through EventBus. TTS speaks because it subscribes to `ResponseEvent`, not because the brain calls it.

## Common Tasks

### Adding a new LLM provider

1. Create `brain/providers/your_provider.py`
2. Implement the `LLM` protocol from `brain/ports.py`:
   ```python
   class YourProvider:
       provider_name = "your_provider"
       
       def generate(self, prompt: str) -> str:
           # Return text, never None
           ...
       
       def generate_stream(self, prompt: str):
           # Yield fragments
           ...
   ```
3. Register in `brain/router.py`:
   ```python
   PROVIDERS = {
       "gemini": GeminiProvider,
       "your_provider": YourProvider,
   }
   ```
4. Add config defaults to `core/config.py`

### Adding a tool

Create `tools/builtins/your_tool.py`:

```python
from tools.base import ToolRisk

class YourTool:
    """Structural — no inheritance needed."""
    
    name = "your_tool"
    description = "What this tool does"
    risk = ToolRisk.SAFE  # SAFE | RISKY | DANGEROUS
    
    def execute(self, **arguments) -> str:
        # Return output or error message
        ...
```

Register in `tools/builtins/__init__.py`:

```python
from tools.builtins.your_tool import YourTool

BUILTIN_TOOLS = [
    YourTool(),
    # ...
]
```

Add to `tools.allowed` in `config.yaml`:

```yaml
tools:
  enabled: true
  allowed: [your_tool]
```

### Adding a plugin

Create `plugins/builtins/your_plugin.py`:

```python
from plugins.base import Plugin, PluginContext

class YourPlugin(Plugin):
    name = "your_plugin"
    version = "1.0.0"
    
    def initialize(self, context: PluginContext) -> None:
        # Subscribe to events
        if context.bus:
            context.bus.subscribe("ResponseEvent", self._on_response)
        
        # Register tools
        if context.tools:
            context.tools.register(your_tool)
    
    def shutdown(self) -> None:
        # Clean up subscriptions and registrations
        ...

def plugin():
    """Factory function for discovery."""
    return YourPlugin()
```

Enable in `config.yaml`:

```yaml
plugins:
  enabled: [your_plugin]
```

### Modifying the prompt

**System prompt**: Edit `prompts/system.md`

**Personality**: Edit `prompts/personality.md`

**Section order**: Fixed in `brain/prompt_sections.py`. The order is load-bearing — don't change it without understanding why it's that way. See ARCHITECTURE.md.

**Adding a section**: 
1. Add the header constant to `brain/prompt_sections.py`
2. Add a `_build_your_section()` method to `brain/prompt_builder.py`
3. Call it in `build()` at the right position in the order
4. Make it return `[]` when empty so unused sections cost zero tokens

### Adding a voice provider

TTS provider in `voice/tts/providers/your_tts.py`:

```python
class YourTTSProvider:
    def __init__(self, config: dict):
        ...
    
    def speak(self, text: str, **options) -> bool:
        # Synthesize and play
        ...
    
    def is_available(self) -> bool:
        # Check dependencies
        ...
```

Register in `voice/factory.py`:

```python
def create_tts_provider(name: str, config: dict):
    providers = {
        "edge": EdgeTTSProvider,
        "your_tts": YourTTSProvider,
    }
    ...
```

## Testing

```bash
# All tests
pytest

# One file
pytest tests/test_pipeline.py

# Verbose
pytest -v

# With stdout
pytest -s

# Pattern match
pytest -k "test_memory"
```

### Writing tests

Use the fake collaborators pattern:

```python
def test_something():
    # Fake LLM that captures what it was given
    class CapturingLLM:
        def __init__(self):
            self.prompt = ""
        
        def generate(self, prompt: str) -> str:
            self.prompt = prompt
            return "response"
    
    llm = CapturingLLM()
    
    # Test the behavior
    manager = ConversationManager(
        memory=FakeStore(),
        builder=PromptBuilder(),
        llm=llm,
    )
    
    manager.chat("hello")
    
    assert "hello" in llm.prompt
```

### Test organization

- `tests/test_*.py` - Hermetic unit tests
- `tests/manual_*.py` - Side-effecting scripts (API calls, database writes)

Run manual scripts explicitly:

```bash
python tests/manual_gemini_test.py
```

## Debugging

### Enable debug logging

`config.yaml`:

```yaml
logging:
  level: DEBUG
```

### Inspect the prompt

```python
# In tests
builder = PromptBuilder()
prompt = builder.build(
    history=[Message(role="user", content="test")],
    user_message=Message(role="user", content="current"),
)
print(prompt)
```

Or use `tests/manual_prompt_test.py` to see the real prompt with your conversation history.

### Check what tools are registered

```python
runtime = AuraRuntime()
runtime.start()

if runtime.services.tools:
    print(runtime.services.tools.available())
```

### Watch events

```python
def log_event(event):
    print(f"Event: {type(event).__name__}", event)

runtime.bus.subscribe("ResponseEvent", log_event)
```

## Code Style

- **Type hints** on public APIs
- **Docstrings** on modules and non-obvious functions
- **Tests** for new functionality
- **Keep subsystems decoupled** — if you need to import `brain/` from `voice/`, the dependency is backwards

### Import order

```python
# Standard library
import sys
from typing import Protocol

# Third party
import yaml
from rich.console import Console

# Local core
from core.config import load_config
from events.types import ResponseEvent

# Local subsystem
from brain.message import Message
```

## Common Pitfalls

**Don't widen protocols** — Adding a method to `IdentityAnchor` breaks every structural implementation that already exists. Create a new protocol instead.

**Don't import horizontally** — `voice/` must not import `brain/`. Use the event bus or pass dependencies through the constructor.

**Don't construct subsystems directly** — Only `launcher/services.py` builds ChatEngine and MemoryManager. Everything else receives dependencies.

**Don't block the main thread** — Long-running operations belong in threads/processes. The avatar owns the main thread on desktop.

**Don't call optional collaborators directly** — Use defensive readers like `anchor_of()`, `hint_of()`. A broken collaborator should cost a section, never a reply.

## Release Checklist

1. Run the full test suite
2. Test with real API providers (Gemini, Edge TTS)
3. Test with voice, vision, tools all enabled
4. Check that avatar degrades gracefully with no display
5. Verify config.yaml is generated on first run
6. Update version in `core/config.py` DEFAULT_CONFIG
7. Update IMPLEMENTATION_STATUS.md
8. Tag the release

## Getting Help

- **Architecture questions**: Read ARCHITECTURE.md
- **What's implemented**: Read IMPLEMENTATION_STATUS.md  
- **Current status**: Check git branch and test results
- **Bugs**: Check if a test reproduces it first
