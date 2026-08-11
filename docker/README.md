# Aura Cloud Core — Docker

This image runs the FastAPI server (`server.main:app`) with Uvicorn. It is the
same binary the desktop build uses; no code is duplicated and no server-only
logic lives outside `server/`.

The image is built from `D:\AURA` and expects `.env` (or the environment
directly) to provide `AURA_SERVER_AUTH_TOKEN` and any provider keys. Nothing
in the image contains a secret.

## Build

```bash
docker build -t aura-cloud-core:latest .
```

## Run

```bash
docker run -d \
  --name aura \
  -p 8000:8000 \
  --env-file .env \
  -v aura-data:/app/data \
  -v aura-logs:/app/logs \
  aura-cloud-core:latest
```

- `--env-file .env` — supplies `AURA_SERVER_AUTH_TOKEN`, `GEMINI_API_KEY`,
  `OLLAMA_HOST`, etc.
- `-v aura-data:/app/data` — persists `data/memory.db` (the SQLite database
  used by `memory.sqlite`). Without this volume the database is lost on every
  container restart.
- `-v aura-logs:/app/logs` — optional; keeps `logs/` across restarts.

## Health

```bash
curl http://localhost:8000/health
```

Response shape:
```json
{
  "status": "ok",
  "version": "0.2.0",
  "uptime_seconds": 12.5,
  "runtime": {
    "llm_provider": "ollama",
    "memory": "connected",
    "vision": "disabled",
    "voice_output": "disabled",
    "voice_input": "disabled",
    "screen": "enabled",
    "companion": "disabled"
  }
}
```

## Logs

```bash
docker logs -f aura
```

Structured JSON logs (when `AURA_SERVER_LOG_LEVEL=INFO`) go to stdout; no
separate log shipper is required.

## Environment

See `.env.example` for the complete list. The server reads only
`AURA_SERVER_*` and the provider keys (`GEMINI_API_KEY`, `OLLAMA_HOST`,
`EDGE_TTS_VOICE`, etc.). The Android app never sees any of them.

## Persistent storage

The only stateful file is `data/memory.db`. It is a single SQLite file
protected by a process-wide `RLock` in `memory/sqlite.py`. On a container
platform with an ephemeral filesystem (Render, Fly.io, Railway, Cloud Run)
you **must** mount a volume at `/app/data` or configure an external database.
See `docs/DEPLOYMENT.md` for platform-specific guidance.

## CORS

`AURA_SERVER_CORS_ORIGINS` defaults to `*`. In production set it to the
exact origin(s) your frontend uses, e.g. `https://aura.example`. The Android
app sends no `Origin` header, so it is unaffected by this setting.

## Stop

```bash
docker stop aura
docker rm aura
```

Volumes survive removal; the database is preserved for the next container.