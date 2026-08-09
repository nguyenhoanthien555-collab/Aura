# Aura Architecture

How the pieces fit, and why they fit that way. This describes the system as
it currently stands, not as it was planned.

## The shape of it

Nine packages. Each one imports `core/` and `events/` and nothing else
horizontal:

```
                        launcher/
                     (composition root)
                            |
        +-------+-------+---+---+-------+-------+
        |       |       |       |       |       |
     brain/  memory/  voice/  vision/ avatar/ tools/
        |       |       |       |       |       |
        +-------+-------+---+---+-------+-------+
                            |
                     core/    events/
                                |
                            plugins/
```

`brain/` does not import `voice/`. `voice/` does not import `brain/`. The
only module that imports several subsystems together is
`launcher/services.py`, and that is the entire reason the others can stay
apart.

Anything that looks like it needs a horizontal dependency goes through the
event bus instead. TTS speaks replies because it subscribes to
`ResponseEvent`, not because the brain calls it.

---

## Server mode — same composition root, different wiring

`server/runtime.py` calls `build_services` from `launcher/services.py` with
two differences:

1. **No avatar** — `avatar.enabled = False` so nothing imports tkinter.
2. **Remote vision** — screen observations arrive from an Android device
   via `/api/screen`. `vision.remote.build_remote_vision` creates a
   `RemoteVisionManager` that implements the same port as the local
   `VisionManager`. The rest of the system (Brain, Companion) cannot tell
   the difference.

The `ServerRuntime` owns:
- `services` — the full `Services` bundle from `build_services`
- `screen_source` — the remote vision source (or `None` if disabled)
- `companion_engine` — unprompted notifications (or `None` if disabled)
- `notifications` — device-targeted outbox

Routes in `server/routes/` only:
- Validate / authenticate / deserialize
- Call the corresponding `ServerRuntime` method
- Serialize the result

No route constructs a provider, builds a prompt, queries memory, or decides
what a screen means. All of that lives in the shared core.

### Server endpoints

| Method | Path | Runtime method |
|--------|------|----------------|
| `GET` | `/health` | `health_status()` |
| `POST` | `/api/chat` | `chat(message, session_id)` |
| `WS` | `/api/chat/stream` | `chat_stream(message, session_id)` |
| `POST` | `/api/screen` | `observe_screen(observation)` |
| `POST` | `/api/screen/upload` | (multipart, same runtime) |
| `GET` | `/api/notifications` | `notifications.collect(device_id)` |

Authentication: `Authorization: Bearer <token>` (env `AURA_SERVER_AUTH_TOKEN`).
WebSocket carries token in query string (`?token=`) because the handshake has
no header slot.

---

## Android Companion — the remote client

The Android app (`android/`) is a Kotlin/Jetpack Compose client that speaks
the same protocol the desktop CLI uses, over HTTPS/WSS.

### Android architecture

```
┌─────────────────────────────────────────────────────────────┐
│  MainActivity (Compose Navigation)                          │
├─────────────────────────────────────────────────────────────┤
│  ChatViewModel ──► AuraRepository ──► AuraApi (REST)        │
│       │                │                  AuraStreamClient (WS)│
│       │                ▼                                     │
│       │           SettingsProvider (interface)               │
│       │                │                                     │
│       ▼                ▼                                     │
│  ChatUiState    SettingsStore (EncryptedSharedPreferences)   │
├─────────────────────────────────────────────────────────────┤
│  ScreenObservationService (AccessibilityService)            │
│       │                                                     │
│       ▼                                                     │
│  ObservationThrottle (client-side filter)                   │
├─────────────────────────────────────────────────────────────┤
│  NotificationWorker (WorkManager, 15-min poll)              │
└─────────────────────────────────────────────────────────────┘
```

### Key seams

**`SettingsProvider` (interface)** — read-only access to server URL, token,
device ID, feature flags. `SettingsStore` implements it (needs `Context` +
Keystore). `FakeSettings` implements it for JVM tests. The network layer
(`ApiFactory`, `AuraStreamClient`, `AuraRepository`) and `ChatViewModel`
depend on the interface, so they are testable without Android.

**`AuraRepository`** — the single door to the server. Owns the session ID
(continuity). Methods: `send()`, `stream()`, `health()`, `sendScreen()`,
`uploadScreenshot()`, `collectNotifications()`. Returns `AuraResult.Ok/Fail`.

**`AuraStreamClient`** — WebSocket streaming. One socket per message.
Protocol: connect → send one frame → read `started` → `chunk*` → `complete`.
Auth via `?token=` + `?session_id=`. Falls back to REST on refused handshake
or empty stream — `ChatViewModel` handles this transparently.

**`ObservationThrottle`** — client-side gate mirroring the server's
`companion/detector.py`. Rate limit (min interval), Jaccard similarity on
token sets, volatile token collapsing (counters, percentages). Empty screens
never sent. First screen always passes.

**`ScreenObservationService`** — AccessibilityService. Walks the view tree
(max depth 12, max parts 300, max text 8000). Skips password fields.
Checks `settings.current.screenObservationEnabled` on every event.
Only runs when the user explicitly enables it.

**`NotificationWorker`** — WorkManager periodic (15 min). Re-checks
`notificationsEnabled` and `companion_enabled` from `/health` before each
poll. Tap-to-open via `MainActivity` intent extra.

### Session continuity

Server generates `session_id` on first reply. Android echoes it on every
subsequent request (REST body `session_id`, WS query `session_id`). This is
the whole of conversational continuity — no client-side session logic beyond
storing that string.

### Security

- No LLM provider keys in APK (server owns them)
- Token stored in `EncryptedSharedPreferences` (Keystore-backed)
- Cleartext permitted only in `debug` build (`network_security_config.xml`)
- Release requires HTTPS (platform-enforced)
- No logging of Authorization header (no logging interceptor)

---

## One turn, end to end (desktop)

```
"hey, what broke"
    |
    v
ConversationManager.chat()
    |
    +-- UserInputEvent published
    |
    +-- history()          memory -> list[Message], oldest first
    |
    +-- PromptBuilder.build()
    |       SYSTEM  PERSONALITY  CONTEXT  MEMORY  VISION
    |       HISTORY  IDENTITY  STYLE  USER
    |
    +-- ThinkingEvent published
    |
    v
BrainRouter.generate(prompt) -> Provider -> str
    |
    +-- ResponseStyler.style()      filler stripped
    |
    +-- memory.save(user) + memory.save(assistant)
    |
    +-- ResponseEvent published     -> TTS speaks, avatar animates
    |
    v
Response(text=...)
```

`chat_stream()` and `sentences()` are the same turn delivered
incrementally. All three share `_prepare()`, so they cannot drift apart in
what they publish or what context they assemble.

## Prompt section order is the design

`brain/prompt_sections.py` fixes the order, and the order is load-bearing:

```
SYSTEM         hard rules
PERSONALITY    who she is (prompts/personality.md)
CONTEXT        files the user attached
MEMORY         recalled facts about the user
VISION         what she can see
HISTORY        the transcript
IDENTITY       who she is, restated
STYLE          how this reply should read
USER           what was asked
```

A model follows the instruction it read most recently far more reliably
than one at the top of a long prompt. The last three sections are all
instructions, ordered by permanence: identity outlasts a single reply's
style, and both go *after* the transcript they exist to counteract.

Every section except HISTORY and USER disappears entirely when it has
nothing to say. An unused subsystem costs zero tokens, not merely few.

## The three personality layers

Deliberately three short instructions rather than one paragraph, in
descending permanence:

| Layer | File | Answers |
|---|---|---|
| consistency | `brain/consistency.py` | who she is, and does not stop being |
| style | `brain/style.py` | how a reply of hers reads |
| mood | `brain/mood.py` | how she happens to feel right now |

A model asked to hold one long instruction obeys the end of it. Asked to
hold three short ones, it obeys three.

### Character consistency

A personality described once at the top of the prompt gets further away
with every turn, and the model starts answering like the transcript it has
been reading rather than like Aura. Five drift modes: robotic, overly
formal, overly emotional, self-contradicting, lost identity.

The fix is prompt construction only — nothing inspects a reply, matches a
pattern, or rewrites a word. `CharacterAnchor` reads one number, how many
messages are in the prompt, and returns text placed near the user's
message where recency makes it stick.

Three tiers:

```
< 6 messages     nothing. A three exchange conversation has not drifted,
                 so the section is empty and costs zero tokens.

>= 6 messages    identity + drift guard

>= 20 messages   + contradiction clause, held back until there is enough
                 transcript to actually contradict
```

The guard grows once, not twice — a thousand-message conversation produces
the same section as a twenty-one-message one.

### Style

Subtractive only. `strip_filler` deletes assistant boilerplate the model
emitted out of habit ("Certainly!", "Is there anything else I can help you
with?"). It never rewrites a sentence and never touches anything inside
code, so it cannot change what a reply says — only how much throat
clearing surrounds it.

Because it operates over a whole reply, it cannot run on a stream fragment:
deciding whether an opening clause is filler needs the clause to have
finished. Fragments therefore stream unstyled, and
`StreamFinishedEvent.text` carries the styled version.

## Memory

Three sources, composed:

```
ProfileStore        SQLite      facts you told her to remember
KeywordRetriever    SQLite      search over older transcript (off by default)
CompanionMemory     in-memory   session context: projects, goals, style
```

`_CompositeKnowledge` in `launcher/services.py` merges the durable and
companion providers, applying the line cap after merging so neither source
crowds out the other. Durable goes first: who the user is outranks what
this session contains.

### The memory/brain boundary

One-directional and explicit:

```
memory.models.Message   (ORM row: id, timestamp, embeddings)
        |
   brain/adapters.py
        |
        v
brain.message.Message   (pipeline dataclass: role, content)
```

The brain never constructs storage rows. It calls
`store.save(role, content)` and lets memory decide how to persist.
Detached ORM rows cannot blow up mid-prompt with a lazy-load error.

### Ordering contract

Resolved once, at the boundary:

- `MemoryManager.get_recent()` returns **newest first** (storage order)
- `ConversationManager.history()` returns **oldest first** (pipeline order)
- `PromptBuilder._build_history()` renders in order, re-sorting nothing

## Protocols, not inheritance

Optional collaborators are `@runtime_checkable` Protocols with narrow
interfaces. A user's custom TTS provider needs the right methods and zero
inheritance.

Every protocol here is kept deliberately short, and for one reason:
`runtime_checkable` means every name added to a protocol breaks
`isinstance` for every implementation already written against it,
including ones outside this repository. Widening a protocol is a breaking
change that does not look like one.

### The defensive reader pattern

Optional collaborators are read through a module-level function rather
than called directly:

```python
def anchor_of(source, messages: int) -> str:
    if source is None:
        return ""
    getter = getattr(source, "anchor", None)
    if getter is None:
        return ""
    try:
        return getter(messages) or ""
    except Exception:
        return ""
```

`hint_of`, `anchor_of`, and `stream_of` all follow this shape. A broken
collaborator costs a prompt section, never the reply. The user's answer is
the thing they asked for; its wording is not worth failing a turn over.

## Events

`events/bus.py` is a synchronous pub/sub bus keyed on event type.
Publishers know nothing about subscribers.

The events the brain publishes are *facts* — "the user said X", "a reply
arrived" — never UI state. Deriving a display state from those facts is
the avatar's job, so there is exactly one owner of that state.

Domain facts, published by the brain and the voice system:

```
UserInputEvent      ThinkingEvent       ResponseEvent
StreamStartedEvent  StreamChunkEvent    StreamFinishedEvent
ErrorEvent          ListeningEvent      TranscriptEvent
SpeakingEvent       VisionUpdateEvent
```

Derived cues, published by `avatar/animation.py` from those facts:

```
ThinkingStartedEvent  ThinkingFinishedEvent
TypingStartedEvent    TypingFinishedEvent
SpeechStartedEvent    SpeechFinishedEvent
BlinkEvent            StateChangedEvent
MoodChangedEvent      ExpressionChangedEvent
```

The derived events are *siblings* of the domain events under `Event`,
never subclasses. The bus delivers to base classes, so a director
listening for `SpeakingEvent` that published a subclass of it would
receive its own output and recurse forever. Sibling types make that
impossible by construction.

Every `_emit` is wrapped: a subscriber that raises loses its own
notification, not the turn.

## Tools

Two locks, both of which must be opened:

1. `tools.enabled` must be true
2. the tool's name must appear in `tools.allowed`

On top of that, risk levels not listed in `auto_approve` need a live
confirmation. With no confirmation handler attached, they simply cannot
run — the executor's default is refusal, and the CLI installs a handler
only because it can actually reach a human.

Tools are structural: `name`, `description`, `risk`, and `execute`. No
base class required. `tools/timeout.py` bounds the wait rather than the
tool — a hung call is abandoned, not killed, because killing a thread
mid-write leaves locks held and files half written.

## Plugins

The same two-lock shape as tools: a plugin has to be discovered, and its
name has to appear in `plugins.enabled`.

A plugin receives the bus and the tool **registry**, never the executor.
That distinction is the whole point: a plugin may add a capability, and it
may not decide whether that capability is permitted. A plugin's tool still
has to be named in `tools.allowed` before it can run.

```
MAY                       MUST NOT
subscribe to events       import brain/
publish events            import voice/, avatar/, vision/
register tools            access the LLM directly
read its own config       touch PromptBuilder
provide knowledge         modify ChatEngine internals
```

Each plugin receives a narrowed `PluginContext` carrying only its own
config slice — a copy rather than a mutation, so one plugin cannot observe
another's settings by holding on to the object it was given.

Plugins are built last in `build_services`, because a plugin may register
tools and subscribe to events, so everything it might touch has to exist
first. They are shut down first, for the mirror reason.

## Threading

Stated once, because it is the only subtle part.

Tk must run on the thread that created it, and it wants the main thread.
So `AuraRuntime.run()` puts the interactive loop on a worker and gives the
main thread to the avatar. With no GUI present the worker runs on the main
thread instead and nothing is spawned.

The worker is wrapped so it always tears the GUI down: without that, an
exception on the worker leaves a floating avatar attached to a session
that no longer exists.

## Degradation

Every optional subsystem can be absent, and absence is the default for
anything that watches or acts:

| Subsystem | Missing dependency | Result |
|---|---|---|
| TTS | no `edge-tts` | falls back to SAPI, then a silent mock |
| STT | no `sounddevice` | `stt` is None, `/voice` reports disabled |
| Vision | no `mss` | window titles only |
| Avatar | no display | null renderer, no window |
| Tools | not configured | `run_tool` returns a failure result |
| Plugins | none discovered | `plugins` is None |

A machine with no display, no microphone, and no speakers still runs Aura
as a text companion.

## Composition root

`launcher/services.py` reads config, builds every subsystem, and hands
back one `Services` bundle. `ChatEngine` is the only thing that
constructs `MemoryManager`, `PromptBuilder`, `BrainRouter`,
`ResponseStyler`, and `CharacterAnchor`.

Everything else receives dependencies through constructors, which is what
lets tests inject fakes without a database or an API key.

`BrainRouter` creates its provider on first `generate()`, not in
`__init__`. Constructing Aura therefore never requires network access or
credentials; Gemini loads only when a message is actually sent.

## Entry points

| File | Purpose |
|---|---|
| `launcher.py` | desktop runtime: avatar, CLI, flags |
| `main.py` | plain text harness, unchanged since the foundation |

`launcher.py` sits next to the `launcher/` package. Python resolves
packages before modules, so `import launcher.cli` always finds the
package while `python launcher.py` runs the file as `__main__`. Nothing
should ever `import launcher` expecting the file.
