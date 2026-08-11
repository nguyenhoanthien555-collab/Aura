# Aura API

The HTTP surface of Aura in server mode. Five endpoints, one WebSocket.

Every route is thin by design: it validates, authenticates, calls into
Aura Core, and serialises the answer. No route contains personality
logic, prompt construction, memory algorithms, provider logic, vision
reasoning or TTS. Those live where they lived before — `brain/`,
`memory/`, `vision/`, `voice/` — and server mode reuses them through the
same composition root the desktop uses (`launcher/services.py`).

---

## Running the server

```bash
# From the repository root
python launcher.py --server

# Or with an explicit bind and port
python launcher.py --server --host 127.0.0.1 --port 8000
```

Equivalent, if you prefer uvicorn directly:

```bash
python -m uvicorn server.main:app --host 127.0.0.1 --port 8000
```

Aura Core is constructed **once**, during application startup. A request
never builds a provider, never opens a memory manager and never reloads
the personality.

Interactive docs are served at `/docs` (Swagger) and `/redoc`.

---

## Authentication

Every endpoint requires a bearer token:

```
Authorization: Bearer <AURA_SERVER_AUTH_TOKEN>
```

The token comes from the environment and nowhere else:

```bash
# Generate one
python -c "import secrets; print(secrets.token_urlsafe(32))"

# Put it in .env (gitignored)
AURA_SERVER_AUTH_TOKEN=<the generated value>
```

If `AURA_SERVER_AUTH_TOKEN` is empty the API accepts unauthenticated
requests and logs a warning at startup. That configuration is only
acceptable bound to `127.0.0.1`. See [SECURITY.md](SECURITY.md).

Comparison is constant-time (`secrets.compare_digest`). A failure returns
`401` with `WWW-Authenticate: Bearer` and never echoes the submitted
token.

---

## `GET /api/health`

Liveness and what actually came up.

```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/health
```

```json
{
  "status": "healthy",
  "version": "0.2.0",
  "uptime_seconds": 42.7,
  "runtime": {
    "llm_provider": "gemini",
    "memory": "connected",
    "vision": "disabled",
    "voice_output": "disabled",
    "voice_input": "disabled",
    "screen": "disabled",
    "companion": "disabled",
    "proactive": "disabled"
  }
}
```

`llm_provider` is the chain that was **actually built** — `"gemini->groq"`
when a fallback initialized, not merely what `config.yaml` requested.
Obtaining it constructs the lazy provider, which is why this is a
diagnostic route and not something to poll.

`screen`, `companion` and `proactive` each say whether a class of
unprompted message can arrive: screen observations are only accepted
when `screen` is enabled, and the two notification engines are only
meaningful when their flag says so. A client should not offer a setting
for a capability the server cannot deliver.

The payload deliberately contains no API keys, no token, no filesystem
paths, no hostnames and no memory contents. `tests/test_server.py`
asserts this against a marker list rather than trusting review.

---

## `GET /api/ready`

Readiness: can this process answer a chat turn? **Unauthenticated**, so a
container healthcheck or platform probe can use it.

```bash
curl http://localhost:8000/api/ready
```

```json
{"ready": true, "llm_provider": "gemini->groq", "problems": []}
```

`200` when ready, `503` when not:

```json
{"ready": false, "llm_provider": "unknown",
 "problems": ["provider chain unavailable (ValueError)"]}
```

This is the question a liveness probe cannot answer: a server whose
provider chain never initialized is perfectly alive and cannot do the one
thing it exists for.

It reports only what a chat turn requires — a started runtime and a
constructible provider. Vision, voice, screen and companion are optional
by design and excluded on purpose: a probe that failed because TTS was
off would restart a healthy server forever. It does **not** call the
provider; a probe that made a network request per poll would bill you for
being observed and turn one outage into a restart loop.

`problems` carries failure *categories* (the exception type, not its
text), because this route is public.

---

## `POST /api/chat`

One turn of conversation. Enters the existing Brain pipeline — the same
`ChatEngine` the desktop CLI drives — so personality, memory, knowledge
retrieval and vision context all apply exactly as they do locally.

**Request**

```json
{
  "session_id": "optional-client-supplied-id",
  "message": "what am I working on?",
  "context": {},
  "metadata": {}
}
```

| Field | Type | Notes |
|---|---|---|
| `session_id` | string, ≤128 chars | Generated server-side if omitted |
| `message` | string, 1–8000 chars | Empty is rejected `422` |
| `context` | object | Passed through; reserved |
| `metadata` | object | Passed through; echoed back |

**Response**

```json
{
  "session_id": "6f1c...",
  "reply": "You were debugging the vision throttle.",
  "message_id": "b93a...",
  "metadata": {"elapsed_seconds": 1.83, "source": "text"}
}
```

**Example**

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "hello"}'
```

**Failures**

| Status | Meaning |
|---|---|
| `401` | Missing, malformed or wrong token |
| `422` | Empty message, or over the length limit |
| `429` | The provider is rate limited |
| `503` | The provider is temporarily unavailable |
| `500` | Anything else |

A failure carries `{"error": <code>, "message": <safe text>,
"message_id": ..., "elapsed_seconds": ...}` and **not** the exception
text. Provider exceptions routinely embed hosts, ports, filesystem paths
and occasionally key fragments; those are logged server-side and never
returned.

| `error` | Status | Meaning |
|---|---|---|
| `rate_limited` | `429` | Provider rate limit; retry later (also a `Retry-After` header when the provider supplied one) |
| `provider_unavailable` | `503` | Transient provider outage; a fallback provider may be trying next |
| `chat_failed` | `500` | Anything else — see server logs with the `message_id` |

The codes are produced by `server/errors.py` from the typed provider
errors, and the same mapping drives the WebSocket path.

---

## `WebSocket /api/chat/stream`

The same conversation, streamed. Wraps the existing
`brain/streaming.py` generator rather than reimplementing generation, and
does not buffer the reply before sending it.

**Connecting**

```
ws://localhost:8000/api/chat/stream?token=<AURA_SERVER_AUTH_TOKEN>
```

The token is a query parameter because browsers cannot set headers on a
WebSocket handshake. A bad token is rejected **before** the socket is
accepted (close code `1008`), so an unauthenticated client never reaches
an open connection.

**Send**

```json
{"session_id": "optional", "message": "explain the throttle"}
```

**Receive** — one frame per event:

```json
{"type": "started",  "session_id": "...", "message_id": "..."}
{"type": "chunk",    "text": "The vision ", "index": 0}
{"type": "chunk",    "text": "manager reuses ", "index": 1}
{"type": "complete", "text": "The vision manager reuses...",
                     "elapsed_seconds": 2.41,
                     "first_chunk_seconds": 0.38,
                     "total_chunks": 24}
```

`chunk.text` is the **new fragment only**, never the accumulated reply —
append, do not replace. `complete.text` is the finished reply after
styling, which is not always the concatenation of the chunks: the style
filter needs a whole reply before it can tell an opening filler clause
from a real sentence. A client that rendered chunks should replace its
buffer with `complete.text`. The difference is only ever a deleted filler
phrase; no fact, path, command or version is altered.

**Timing** — `first_chunk_seconds` is time-to-first-token and
`elapsed_seconds` is the whole turn. Both are measured server-side.

**Errors**

```json
{"type": "error", "code": "message_too_long"}
```

| Code | Meaning |
|---|---|
| `invalid_json` | The frame was not JSON |
| `empty_message` | No message text |
| `message_too_long` | Over `max_message_length` |
| `rate_limited` | Provider rate limit (carries `retry_after` when known) |
| `provider_unavailable` | Transient provider outage |
| `stream_failed` | The provider failed mid-generation, cause unrecognised |
| `internal_error` | Anything else, outside generation |

`rate_limited` and `provider_unavailable` are the same categories the
REST path uses — one mapping in `server/errors.py` serves both, so the
two transports cannot drift. An error frame also carries a `message`
field with client-safe text; as on the REST path, the provider's own
exception text is never sent.

The generator is pumped through `starlette.concurrency.iterate_in_threadpool`,
so a blocking provider call cannot stall the event loop — `/api/health`
stays responsive while a long answer streams.

---

## `POST /api/screen`

A device reports what is on its screen. Off unless
`server.screen.enabled` is true in config; a disabled server answers
`503` rather than accepting data it will never look at.

**Request**

```json
{
  "session_id": "...",
  "device_id": "phone-1",
  "application": "GitHub",
  "package": "com.github.android",
  "screen_text": "Build failed: 4 tests did not pass",
  "accessibility_context": {"nodes": ["Retry", "View log"]},
  "timestamp": 1734567890.0
}
```

`accessibility_context` is free-form. Android's accessibility tree has a
shape that varies by app, so it is flattened to text server-side rather
than modelled — a schema for "whatever the accessibility API returned"
would be fiction.

**Response**

```json
{
  "session_id": "...",
  "status": "accepted",
  "accepted": true,
  "observation_id": "obs-17",
  "decision": {
    "should_notify": false,
    "reason": "below relevance threshold (0.45 < 0.70)",
    "priority": "normal",
    "message": "",
    "confidence": 0.45,
    "cooldown": 0.0
  }
}
```

`decision` is returned **even when Aura stays quiet**, and `reason` names
the gate that stopped her. Without that the only way to tune thresholds
would be guesswork.

The observation does two independent things:

1. It feeds the existing Vision pipeline, so the next turn of
   conversation already knows what the user is looking at.
2. It runs the companion pipeline, which decides — separately — whether
   this is worth interrupting them over.

### `POST /api/screen/upload`

Multipart variant carrying a screenshot (`screenshot` file field, plus
`session_id`, `device_id`, `application`, `package`, `timestamp` form
fields). Rejects anything over `max_upload_bytes` with `413`.

Pixels are optional. The text pipeline works without them, and a
screenshot only earns its bandwidth when a vision model is configured to
read one.

---

## `GET /api/notifications`

Collect whatever Aura decided to say while the device was away.

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/notifications?device_id=phone-1"
```

```json
{
  "notifications": [
    {
      "notification_id": "e2b1...",
      "message": "Your build failed — 4 tests didn't pass.",
      "reason": "relevant, and a reasonable moment",
      "priority": "high",
      "confidence": 0.91,
      "source": "GitHub",
      "created_at": 1734567890.0
    }
  ],
  "count": 1,
  "companion_enabled": true
}
```

Collection is **destructive**: a notification handed to a device is gone,
so a client that polls twice never sees the same remark twice.
Notifications older than 30 minutes are dropped unread — a remark about a
build that failed an hour ago is noise.

Returns an empty list rather than an error when the companion is off, so
a client can poll unconditionally without branching on server config.

---

## Configuration

The `server:` section of `config.yaml` (committed — **no secrets**):

```yaml
server:
  session_ttl_seconds: 3600

  screen:
    enabled: false          # a device may push screen observations
    min_interval: 8.0       # seconds between vision refreshes
    min_change_ratio: 0.25

  companion:
    enabled: false          # unprompted notifications
    relevance_threshold: 0.7
    cooldown_seconds: 300
    max_per_hour: 6
    quiet_hours: []         # e.g. [[23, 7]] for 23:00-07:00
    suppress_after_chat_seconds: 120
```

Host, port, token and CORS come from the environment (`AURA_SERVER_*`),
never from this file. See [.env.example](../.env.example).

Both `screen` and `companion` default to **false**. Watching someone's
screen and speaking without being spoken to are both things a companion
should start doing because a human asked, not because a config key was
missing.
