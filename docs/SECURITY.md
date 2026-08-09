# Security

Aura in server mode holds a conversation history, a personality, a set of
provider keys, and — if screen observation is enabled — a live view of
what the user is looking at. This document is what protects those.

## The threat that actually matters

This is a personal deployment: one user, one phone, one server. The
realistic threats are not sophisticated:

1. The port ends up reachable from the internet without a token.
2. A secret gets committed.
3. A secret gets logged.
4. An error response leaks an internal path, host or key fragment.
5. The Android APK ships something it should not have.

Everything below is aimed at those five.

---

## Authentication

Every endpoint — including `/api/health` — requires a bearer token.

```
Authorization: Bearer <token>
```

**Generate one:**

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

**Where it lives:** `AURA_SERVER_AUTH_TOKEN` in `.env`, which is
gitignored. Nowhere else. Not in `config.yaml` (committed), not in a
default, not in a constant, not in the Android source.

**How it is compared:** `secrets.compare_digest` — constant time, so a
wrong token cannot be recovered a byte at a time from response timing.

**What a failure returns:** `401` with `WWW-Authenticate: Bearer` and a
generic reason. The submitted token is never echoed back and never
written to a log.

### The unauthenticated case

An empty `AURA_SERVER_AUTH_TOKEN` disables authentication. This is
deliberate — it makes local development on `127.0.0.1` frictionless — and
the server logs a warning at startup when it happens:

```
AURA_SERVER_AUTH_TOKEN is not set - the API is UNAUTHENTICATED.
Bind to localhost only, or set a token before exposing it.
```

**Never run with an empty token on a non-loopback bind.** The default
host in `.env.example` is `127.0.0.1` for exactly this reason; `0.0.0.0`
is an explicit choice you make after setting a token.

### WebSocket

Browsers cannot set headers on a WebSocket handshake, so
`/api/chat/stream` takes the token as a query parameter:

```
ws://host:8000/api/chat/stream?token=<token>
```

A bad token is rejected **before** `accept()` (close code `1008`), so an
unauthenticated client never reaches an open socket.

The trade-off is real and worth naming: query strings appear in proxy
logs and browser history in a way headers do not. Behind a reverse proxy,
disable query-string logging for that path (see
[DEPLOYMENT.md](DEPLOYMENT.md)).

---

## Secrets

### What is a secret

| Secret | Where it lives | Who may see it |
|---|---|---|
| `AURA_SERVER_AUTH_TOKEN` | `.env` on the server | The server, and the phone that holds it |
| `GEMINI_API_KEY` | `.env` on the server | The server only |
| Conversation memory | `data/memory.db` | The server only |

### Rules

- **No hardcoding.** No secret has a default value in code. `ServerSettings.auth_token`
  defaults to `""`, which means "off", not "some value".
- **No secret in `config.yaml`.** That file is committed. The `server:`
  section in it holds thresholds and intervals, and a comment saying so.
- **No LLM API key on Android.** The phone talks to Aura; Aura talks to
  the provider. A provider key never crosses that boundary, which is the
  main architectural reason the phone is a client and not a second brain.
- **No secret in the APK.** The bearer token is entered by the user at
  first run and stored in `EncryptedSharedPreferences`. It is not a
  build-time constant, not in `gradle.properties`, and not in
  `strings.xml`. See [ANDROID.md](ANDROID.md).
- **No token in logs.** Authentication failures log the outcome, never
  the submitted value.

### `.gitignore`

Already covers `.env`, `*.db`, `data/`, and `logs/`. Verify before your
first commit on a new machine:

```bash
git check-ignore -v .env data/memory.db
```

Both should print a matching rule. If either prints nothing, stop and fix
`.gitignore` before committing.

### If a secret is committed

Rotating is cheaper than rewriting history, and for a personal
deployment it is sufficient:

1. Generate a new token / rotate the provider key at the provider.
2. Update `.env` on the server and re-enter the token on the phone.
3. Then, if you like, rewrite history — but treat the old value as
   compromised regardless.

---

## Error responses

Exception text is the most common accidental leak in an HTTP API, because
it is so convenient to return. Aura does not return it.

A provider failure produces, server-side:

```
ERROR Chat failed (message_id=b93a...): ConnectionError:
  HTTPConnectionPool(host='localhost', port=11434): Max retries exceeded
```

and, over the wire:

```json
{"error": "chat_failed", "message_id": "b93a...", "elapsed_seconds": 3.1}
```

The `message_id` correlates the two, so a failure is still diagnosable
from a log without the client ever learning the internal host, the port,
the filesystem layout or any fragment of a key.

The same rule applies to `/api/screen` (`screen_failed`) and to the
WebSocket (`stream_failed`).

`tests/test_server.py` asserts this rather than trusting review: it
drives a deliberately broken provider and scans every response body for a
marker list — `api_key`, `auth_token`, `GEMINI_API_KEY`, `secret`,
`password`, `D:\AURA`, `/home/`, `C:\Users`.

---

## `/api/health`

Health is the endpoint most likely to be left open and least likely to be
reviewed, so its payload is constrained by test:

**Returns:** status, version, uptime, and a coarse per-subsystem
enabled/disabled map.

**Never returns:** API keys, the auth token, filesystem paths, hostnames,
model endpoints, or any memory content.

`llm_provider` is the configured provider *name* (`"gemini"`,
`"ollama"`, `"mock"`), read without constructing the provider — asking
whether the server is alive must not open a network connection or touch a
key.

---

## Request limits

Server-side, because "how big can this be" is not a client's decision:

| Limit | Default | Setting |
|---|---|---|
| Message length | 8 000 chars | `AURA_SERVER_MAX_MESSAGE_LENGTH` |
| Screen text | 20 000 chars | `AURA_SERVER_MAX_SCREEN_TEXT_LENGTH` |
| Screenshot upload | 8 MiB | `AURA_SERVER_MAX_UPLOAD_BYTES` |
| Session ID | 128 chars | fixed |
| Device ID | 128 chars | fixed |

Over-limit input is rejected with `422` (or `413` for uploads) before it
reaches the brain.

There is no rate limiting. For a single-user deployment behind a token
that is a considered omission, not an oversight — add it at the reverse
proxy if the server is exposed. See [DEPLOYMENT.md](DEPLOYMENT.md).

---

## CORS

`AURA_SERVER_CORS_ORIGINS` is a comma-separated list, or `*`.

A native Android client sends no `Origin` header, so it is unaffected by
CORS entirely — `*` is only needed if you point a browser at the API.
Narrow it if you do.

CORS is not an authentication mechanism. It restricts which *browser
pages* may read a response; it does not restrict who may make a request.
The token does that.

---

## Screen observation

The most invasive thing Aura can do, and the most heavily defaulted-off.

- **Off unless enabled.** `server.screen.enabled` defaults to `false`.
  A disabled server answers `503` rather than quietly accepting screen
  data.
- **The user enables it on the device too.** Android's
  AccessibilityService cannot be enabled programmatically — the user
  grants it in system settings, and can revoke it there. MediaProjection
  shows a system consent dialog every session. Aura does not attempt to
  work around either, and nothing in this repository asks for a
  privileged, rooted or OEM-signed capability.
- **Sensitive screens are vetoed, not scored.** `companion/evaluator.py`
  refuses to evaluate a screen containing password, passcode, card
  number, CVV, OTP, verification code, seed phrase or private-key text,
  or belonging to a password manager, authenticator or the system
  settings app. This is a hard veto that runs **before** the LLM call, so
  a login screen never enters a prompt. Erring toward silence costs a
  missed remark; erring the other way puts a password field in a request
  body.
- **Observations are held in one slot, in memory.** No screen history is
  written to disk. An observation older than 90 seconds stops answering,
  so a device that stopped reporting does not keep speaking for the
  screen it last showed.

---

## Notifications

`server.companion.enabled` defaults to `false`. Speaking without being
spoken to is opt-in.

When enabled, every gate defaults to silence: relevance threshold,
cooldown, hourly ceiling, duplicate suppression, quiet hours, and
suppression while the user is already mid-conversation. The default
posture is stated once in `companion/policy.py` and tested in
`tests/test_companion.py`.

Pending notifications live in a bounded in-memory queue and are dropped
unread after 30 minutes. Nothing about a screen is persisted.

---

## What is not covered

Stated plainly rather than implied:

- **No rate limiting.** Add it at the proxy.
- **No TLS in the application.** Terminate it at a reverse proxy. Over a
  LAN this is a real gap: a bearer token over plain HTTP is readable by
  anything on the network. Acceptable on a trusted home network; not
  acceptable on the internet. See [DEPLOYMENT.md](DEPLOYMENT.md).
- **No multi-user isolation.** Sessions share one memory database. This
  is a personal companion, not a service.
- **No audit log.** Requests are logged at INFO; there is no tamper-proof
  record.
- **Memory is not encrypted at rest.** `data/memory.db` is a plain SQLite
  file. Disk encryption on the host is the mitigation.
