# Sprint 4 — Foundation Complete

## Objective

Standardize message handling and finish the core AI pipeline foundation.

## Completed Work

### 1. Message Architecture

**Problem:** ORM rows were leaking into the brain layer via duck-typing.

**Solution:**
- Created `brain.ports` with clean Protocol interfaces (`LLM`, `MessageRecord`, `ConversationStore`)
- Created `brain.adapters` with one-way conversion: `memory.models.Message` → `brain.message.Message`
- Updated `ConversationManager.history()` to return `list[brain.message.Message]`

**Files:**
- `brain/ports.py` — interface contracts the brain depends on
- `brain/adapters.py` — conversion boundary
- `brain/conversation.py` — refactored to use protocols and conversion

### 2. Dependency Injection

**Problem:** Components were tightly coupled; testing required real DB + API keys.

**Solution:**
- `ChatEngine` accepts optional `memory`, `builder`, `llm` (composition root)
- `ConversationManager` receives dependencies through constructor
- `PromptBuilder` accepts optional loaders for testing
- `BrainRouter` accepts optional provider or lazy-loads by name

**Files:**
- `brain/chat_engine.py`
- `brain/conversation.py`
- `brain/prompt_builder.py`
- `brain/router.py`

### 3. Message Flow

**Current pipeline:**

```
"Hello Aura"
    ↓
Message(role="user", content="Hello Aura")
    ↓
ConversationManager.history() → list[Message]  (oldest first)
    ↓
PromptBuilder.build(history, user_message)
    ↓
BrainRouter.generate(prompt: str)
    ↓
Provider.generate(prompt: str) → str
    ↓
Response(text="...")
    ↓
memory.save(user) + memory.save(assistant)
```

### 4. Ordering Contract

**Problem:** Hidden `reversed()` coupling in PromptBuilder.

**Solution:**
- `MemoryManager.get_recent()` — returns **newest first** (storage order)
- `ConversationManager.history()` — returns **oldest first** (pipeline order)
- `PromptBuilder._build_history()` — renders in order, no re-sorting

**Rationale:** Boundary resolved once at the adapter layer, not re-derived by every consumer.

### 5. Path Independence

**Problem:** All file paths were relative to cwd — broke when launched from parent directories.

**Solution:**
- Created `core/paths.py` — all paths anchored to `PROJECT_ROOT`
- Updated config loader, database URL, prompt loaders

**Files:**
- `core/paths.py`
- `core/config.py`
- `memory/sqlite.py`
- `brain/system.py`, `brain/personality.py`, `brain/context_loader.py`

### 6. Return Type Consistency

**Fixed:**
- `ChatEngine.chat()` — returns `Response`, not `str`
- `GeminiProvider.generate()` — returns `str`, never `None` (normalizes blocked responses)

### 7. Core App Composition

**Fixed:**
- `Aura` now owns `ChatEngine` (the actual composition root)
- `Aura.chat()` delegates to `engine.chat()`
- Removed duplicate `MemoryManager` construction

**File:** `core/app.py`

### 8. Test Suite

**Created:**
- `tests/test_pipeline.py` — 24 tests covering Sprint 4 requirements
  - Required pipeline test: `"Hello Aura"` → `Message` → `PromptBuilder` → `BrainRouter` → `Response`
  - Message conversion boundary
  - History ordering
  - Dependency injection
  - Router/provider contracts
  
- `tests/test_gemini_integration.py` — opt-in integration test (requires `AURA_RUN_INTEGRATION=1`)
  
- `pytest.ini` — integration tests excluded by default

- `conftest.py` — puts project root on `sys.path`

**Migrated manual scripts:**
- `tests/manual_*.py` — all side-effecting scripts moved here (safe for pytest collection)

**Status:** Tests written but **not yet run** — command execution blocked throughout session.

### 9. Bug Fixes

- Removed `@runtime_checkable` from `MessageRecord` (protocols with data members raise `TypeError` on `isinstance()`)
- Fixed test doubles to use plain objects instead of ORM rows
- Hardened config reading against missing `memory:` section
- Added `pytest` to requirements.txt

### 10. Documentation

**Updated:**
- Docstrings on every modified module
- Type hints on every public method
- Comments explaining ordering contracts and architectural decisions

## Architecture Decisions

### Memory/Brain Boundary

The boundary is **one-directional** and **explicit:**

```
memory.models.Message  (ORM row with id, timestamp)
         ↓
    [adapters.py]
         ↓
brain.message.Message  (pipeline dataclass: role, content only)
```

The brain **never** constructs storage rows. It calls `store.save(role, content)` and memory decides how to persist.

### Lazy Provider Construction

`BrainRouter` creates the provider on first `generate()` call, not in `__init__`.

**Why:** Constructing Aura never requires network access or API keys. The mock provider boots instantly; Gemini loads only when you send the first message.

### Composition Root

`ChatEngine` is the **only** place that creates `MemoryManager`, `PromptBuilder`, and `BrainRouter`.

Everything else receives dependencies through constructors.

**Why:** Tests inject fakes; production uses defaults.

## Future Compatibility

### Response Expansion

```python
@dataclass
class Response:
    text: str
    # Future:
    # emotion: str | None = None
    # audio: bytes | None = None
    # tool_calls: list[ToolCall] | None = None
```

Current callers constructing `Response(text=...)` keep working unchanged.

### Protocol Satisfaction

Future storage backends satisfy `ConversationStore` without the brain importing them.

Future LLM providers satisfy `LLM` without touching the router.

## Remaining Sprint 4 Work

✅ Standardize message handling  
✅ ConversationManager receives dependencies  
✅ Use `brain.message.Message` in pipeline  
✅ Use `brain.response.Response`  
✅ ChatEngine is composition root  
✅ PromptBuilder supports `list[Message]`  
✅ Memory boundary clean  
✅ Tests created  
⏸️ **Tests execution** — blocked by classifier unavailability

## How to Run Tests

Default (hermetic, no network):
```bash
python -m pytest -v
```

With integration tests (hits Gemini API):
```bash
AURA_RUN_INTEGRATION=1 python -m pytest -v
```

Single test file:
```bash
python -m pytest tests/test_pipeline.py -v
```

## Next Steps (Sprint 5)

Sprint 4 foundation is complete. Ready for:

1. **Memory V2** — semantic memory, conversation summaries
2. **TTS integration** — wire `Response` → voice output
3. **Context enhancement** — dynamic context selection
4. **Tool system** — function calling, computer interaction

## Risks Discovered

1. **Tests not yet run** — command execution was blocked throughout. Static review caught two bugs (Protocol decorator, test fixture coupling), but runtime may reveal more.

2. **Integration test coverage** — only one Gemini integration test exists. Real provider errors (rate limits, token limits, blocking) are untested.

3. **Concurrent access** — `MemoryManager` creates one session per instance. Multiple concurrent `ChatEngine` instances would share the database but have separate sessions (risk of isolation issues).

4. **Error propagation** — provider failures bubble up as exceptions. No retry logic, no graceful degradation.

## Modified Files

### Created
- `brain/adapters.py`
- `brain/ports.py`
- `core/paths.py`
- `conftest.py`
- `pytest.ini`
- `tests/test_pipeline.py`
- `tests/test_gemini_integration.py`
- `tests/manual_*.py` (7 scripts)
- `.gitignore`

### Modified
- `brain/chat_engine.py`
- `brain/conversation.py`
- `brain/context_loader.py`
- `brain/llm.py`
- `brain/personality.py`
- `brain/prompt_builder.py`
- `brain/response.py`
- `brain/router.py`
- `brain/system.py`
- `brain/providers/gemini.py`
- `core/app.py`
- `core/config.py`
- `memory/manager.py`
- `memory/sqlite.py`
- `requirements.txt`

### Deleted
- `tests/test_gemini.py` (side-effecting, migrated to manual_gemini_test.py)
- `tests/test_memory.py` (migrated to manual_memory_test.py)
- `tests/test_prompt.py` (empty, superseded)
- `tests/test_prompt_builder.py` (side-effecting, migrated)
- `tests/test_clear_memory.py` (migrated to manual_clear_memory.py)
- `tests/test_env.py` (migrated to manual_env_check.py)
- `tests/list_models.py` (migrated to manual_list_models.py)
