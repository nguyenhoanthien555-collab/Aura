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

### Section 11 — Testing

Done. `.venv/Scripts/python.exe -m pytest -q` reports **1550 passed, 1
deselected** as of the Phase 9 sweep — 1297 `def test_` functions across
39 files, the difference being parametrization. The one deselected test
is the opt-in Gemini integration test; `pytest.ini` excludes it with
`-m "not integration"` so the suite needs no API keys.

`.github/workflows/tests.yml` runs the same command on every push and
pull request.

An earlier revision of this file said none of these tests had ever been
executed, because shell execution was unavailable in those sessions.
That is no longer the case.

## Not started

### Cleanup

Deliberately last, because removing code before the tests run means
removing it without knowing what depended on it.

Known items:

- ~~A top-level `tts/` package alongside the live `voice/tts/`.~~ Removed
  in Phase 7 after confirming zero importers.
- Unused imports across modules touched by Sections 1–10.
- `main.py` is the original text harness and overlaps `launcher.py`.
  Decide whether it is still worth keeping as the minimal path.

### Future work

Not specified in the current brief, listed so the shape of the system can
accommodate them:

- Live2D and VRM backends behind the existing `avatar/backends.py`
  protocol
- Persistent companion memory (currently session-only, resets on restart)
- Multi-user support, which the current single-SQLite-session design does
  not accommodate

Two entries that were here have since been built: a mobile front end over
`AuraRuntime` (`server/` plus the Kotlin app in `android/`), and retry and
graceful degradation on provider failure (`brain/providers/fallback.py`,
driven by `llm.fallback_providers`).

## Known limitations

Honest list, re-checked against the code in the Phase 7 sweep:

1. One conversation thread per session. No parallel conversations within
   a session.
2. SQLite memory. Concurrent `ChatEngine` instances share the database
   file with separate sessions.
3. Companion memory is in-memory and resets on restart. `memory/companion.py`
   holds Protocols plus an in-memory implementation and no schema — the
   durable half is `memory/profile.py` (SQLite `UserFact`).
4. Recall is lexical, not semantic. `KeywordRetriever` does a keyword
   search over the messages table. There is no vector store and no
   embedding model anywhere in this codebase.
5. Multi-user is not supported. Server sessions isolate conversation
   *state*, not identity; the profile store is one person's.
6. Primary development and testing on Windows.
7. Desktop actions require the desktop. The server process can only run
   tools inside its own container — a cloud deployment cannot touch the
   user's PC, and is built to say so rather than claim success.

Items 2, 5 and 7 in the previous revision of this list ("local only, no
remote access", "no authentication", "the test suite has never been
executed") are obsolete: `server/` exposes an authenticated HTTP and
WebSocket API with a mandatory bearer token, and the suite runs at 1160
passed, 1 deselected.
