# Roadmap

Where Aura has been and where she is going. Sections are numbered as they
were specified in the original brief; the numbering is kept even where the
work landed out of order, because the tests and commit messages reference
it.

## Done

### Foundation

Message architecture, dependency injection, protocol boundaries, the
composition root, path independence, and the test harness. Everything
later sections build on.

Key outcome: `memory.models.Message` (ORM) and `brain.message.Message`
(pipeline) are separated by `brain/adapters.py`, so memory can change its
schema without touching the brain.

### Section 1 — Expression system

`avatar/expression.py`. Mood-driven expression selection and
expression-to-sprite mapping, wired into the avatar state machine.

### Section 2 — Mood system

`brain/mood.py`. Conversation mood tracked from events, feeding both
expression choice and reply style. The third and least permanent of the
three personality layers.

### Section 3 — Streaming responses

`brain/streaming.py`, plus `chat_stream()` and `sentences()` on
`ConversationManager`. Fragments for a UI, whole sentences for a voice.

Two consequences were made visible in the events rather than hidden: the
style filter runs over a whole reply so fragments stream unstyled and
`StreamFinishedEvent.text` carries the styled version; and a provider
failure mid-stream raises without saving the partial reply, because a half
turn in history is indistinguishable from a real one on the next prompt.

### Section 4 — Avatar backend abstractions

`avatar/backends.py`, `avatar/animation.py`. A backend protocol so Live2D
and VRM can arrive later without touching the controller. Tkinter is the
implemented backend; the others are stubs.

### Section 5 — Edge TTS improvements

`voice/tts/providers/edge.py` and its supporting modules: `pacing.py`
(natural rhythm at commas and periods), `audio.py` (format
normalization), `streaming.py` (chunk-by-chunk synthesis), `values.py`
(one SSML-shaped scale shared by every provider).

Cancellation with process cleanup, separate synthesis and playback
timeouts.

### Section 6 — Memory improvements

`memory/profile.py` (durable facts), `memory/retrieval.py` (keyword recall
over older transcript), `memory/companion.py` (session context: projects,
goals, preferences, coding style, highlights), `memory/knowledge.py`
(composition).

Durable sources are queried before session ones: who the user is outranks
what this session happens to contain.

### Section 7 — Tool framework decoupling

Tools became structural — `name`, `description`, `risk`, `execute`, no
base class. `tools/timeout.py` bounds the wait rather than the tool: a
hung call is abandoned, not killed, because killing a thread mid-write
leaves locks held and files half written.

Two-lock permission model, risk levels, auto-approval by level, injected
confirmation handler.

### Section 8 — Plugin system

`plugins/base.py`, `manager.py`, `discovery.py`, `factory.py`, and
`builtins/session_stats.py` as the worked example.

A plugin receives the bus and the tool *registry*, never the executor: it
may add a capability, and it may not decide whether that capability is
permitted. Its tool still has to be named in `tools.allowed`.

### Section 9 — Character consistency

`brain/consistency.py`. Prompt construction only — nothing inspects a
reply, matches a pattern, or rewrites a word.

`CharacterAnchor` reads one number (messages in the prompt) and returns a
guard placed after the transcript, where recency makes it stick. Three
tiers: silent below 6 messages, identity + drift from 6, contradiction
clause from 20. The section grows once, not twice.

### Section 10 — Animation events

`avatar/animation.py`. `AnimationDirector` subscribes to conversation
facts and derives the finer-grained cues a face wants:
`ThinkingStarted/Finished`, `TypingStarted/Finished`,
`SpeechStarted/Finished`, `BlinkEvent`.

The derived events are siblings of the domain events under `Event`, not
subclasses, because the bus delivers to base classes and a director
publishing a subclass of what it listens for would recurse. Every pair is
edge triggered, so a renderer never sees a finish without its start.

The avatar derives its own display state from conversation facts, so
there is exactly one owner of that state.

### Section 12 — Documentation

`README.md`, `docs/ARCHITECTURE.md`, `docs/IMPLEMENTATION_STATUS.md`,
`docs/DEVELOPER_GUIDE.md`, this file. The stale `SPRINT_4_SUMMARY.md` was
removed — its accurate content is folded into ARCHITECTURE.md, and its
"tests not yet run" section had been superseded.

## In progress

### Section 11 — Testing

559 test functions across 17 files are written. **None of them have been
executed in any session recorded here.** Shell execution was unavailable
throughout, so there is no measured result for any part of this suite —
not for Sections 8 and 9, and not for the foundation either.

Sections 8 and 9 were instead reviewed statically, assertion by assertion
against the code they exercise. That found and fixed one real defect: a
missing `from types import ModuleType` in `tests/test_plugins.py` that
would have failed eight discovery tests on `NameError`. Static review
catches missing names and changed signatures; it does not catch a wrong
assertion about correct code.

This is the next thing to do, and it blocks the cleanup pass:

```bash
./.venv/Scripts/python.exe -m pytest -q
```

Fix whatever fails, run again, repeat until green. Do not treat the
sections above as finished until this has actually run.

## Not started

### Cleanup

Deliberately last, because removing code before the tests run means
removing it without knowing what depended on it.

Known items:

- A top-level `tts/` package (`base.py`, `manager.py`,
  `providers/edge.py`, `elevenlabs.py`, `kokoro.py`) sits alongside the
  live `voice/tts/`. It predates the current implementation. Check for
  importers, then remove.
- Unused imports across modules touched by Sections 1–10.
- `main.py` is the original text harness and overlaps `launcher.py`.
  Decide whether it is still worth keeping as the minimal path.

### Future work

Not specified in the current brief, listed so the shape of the system can
accommodate them:

- Live2D and VRM backends behind the existing `avatar/backends.py`
  protocol
- Persistent companion memory (currently session-only, resets on restart)
- Web and mobile front ends over `AuraRuntime`
- Multi-user support, which the current single-SQLite-session design does
  not accommodate
- Retry and graceful degradation on provider failure — right now a
  provider error propagates to the caller

## Known limitations

Honest list, current as of this document:

1. One conversation thread. No parallel conversations.
2. Local only. No remote access.
3. SQLite memory, single session per `MemoryManager` instance. Concurrent
   `ChatEngine` instances share the database with separate sessions.
4. Companion memory is in-memory and resets on restart.
5. No authentication. Designed for one person on their own machine.
6. Primary development and testing on Windows.
7. The test suite has never been executed to a reported result. Every
   claim above about behaviour is a claim about code that was read, not
   code that was run.
