# Aura Cloud Core — Free-Tier Deployment Guide

This document covers deploying Aura Cloud Core to a $0/month hosting platform. The laptop **must not be required** for normal remote operation: Android → Internet → Cloud backend → configured LLM provider.

## Target platforms (verified free tiers, August 2026)

| Platform | Free tier | Persistence | Cold start | Notes |
|----------|-----------|-------------|------------|-------|
| **Render** | Web Service, 750 hrs/mo | Disk (opt-in) | ~30s | Native Docker, GitHub deploy, custom domains |
| **Fly.io** | 3 shared-cpu-1x VMs | Volume (1 GB free) | ~2s | `fly.toml` + `fly launch`; global anycast |
| **Railway** | $5 credit/mo (≈500 hrs) | Volume | ~10s | Simple, but credit-based not time-based |
| **Google Cloud Run** | 2M requests/mo | Cloud SQL / Filestore | <1s | Container-native, scales to zero |
| **Koyeb** | 1 free service | Volume (1 GB) | ~5s | Simple Docker deploy |

> **Verify current limits before committing.** Free tiers change. Check the provider's pricing page the day you deploy. Do not hardcode assumptions into the repo.

## Prerequisites

- A GitHub repository with this codebase
- A Dockerfile (provided at repo root)
- `.env` with `AURA_SERVER_AUTH_TOKEN` and provider keys
- `AURA_SERVER_CORS_ORIGINS` set to your Android app's origin (or `*` for testing)

## Option 1: Render Python Web Service (recommended for Gemini)

### 1. Create a Web Service

1. Connect your GitHub repo to Render
2. New → Web Service → select the repo
3. Settings:
   - **Runtime**: Python
   - **Build command**: `pip install -r requirements-server.txt`
   - **Start command**: `python -m server.main`
   - Do not set a port manually. Render supplies `PORT`; Aura reads it and
     binds `0.0.0.0:$PORT`.
4. Environment variables (all from `.env`):
   - `AURA_SERVER_AUTH_TOKEN` — **required**, generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`
   - `GEMINI_API_KEY` — if using Gemini
   - `AURA_SERVER_CORS_ORIGINS` — your Android app's origin, or `https://*.onrender.com`
   - `AURA_SERVER_LOG_LEVEL=INFO`

### 2. Persistent disk (for SQLite)

1. In the service settings → **Disks** → **Add Disk**
2. Name: `aura-data`, Mount path: `/app/data`, Size: 1 GB (free)
3. This persists `data/memory.db` across deploys and restarts.

**This step is not optional if you want Aura to remember anything.**
`memory/sqlite.py` writes `data/memory.db` under the application
directory, and a Render container filesystem is ephemeral: without the
disk, every deploy, restart, crash and idle-spindown starts Aura from an
empty database. Conversation history, the user profile and companion
state all go with it. Nothing warns you — the server comes up healthy and
simply has no past.

The mount path must be exactly `/app/data`; that is where `DATA_DIR`
points inside the image, and it is the same path `docker-compose.yml`
mounts the `aura-data` volume on. The disk is within Render's free tier,
so this is a configuration step, not a paid feature.

If you deliberately want a stateless deployment — a demo, a smoke test —
skip the disk and expect exactly that.

### 3. Ollama on Render (if using local LLM)

Render does not allow sidecar containers on the free tier. Options:

- **Use a hosted Ollama** (e.g., `https://ollama.example.com`) and set `OLLAMA_HOST` to it
- **Use Gemini/OpenAI** instead (API keys stay on the server)
- **Run Ollama on a separate free service** (another Render Web Service with a GPU instance — not free) or a VPS

> For a truly free deployment with local LLM, Fly.io allows a second container on the same network, or use Cloud Run with a separate Cloud Run service for Ollama.

### 4. Deploy

Push the branch Render is configured to track — it builds and deploys
automatically. First deploy takes ~2-3 minutes (Docker build). Subsequent
deploys are faster with layer caching.

> **Check which branch that is before trusting this step.** The server
> lives on `feature/aura-identity`; `main` has no `server/` directory at
> all, so a service tracking `main` cannot serve `/api/*`. Set the branch
> in Render → Settings → Build & Deploy → Branch.

**A stale deployment fails in a way that looks like a connection
problem.** The Control Hub routes (`/api/settings`, `/api/providers`,
`/api/providers/health`) arrived after the chat and health routes, so a
service built from an older commit answers `/api/health` with 200,
answers chat normally, and returns 404 for all three settings routes. The
phone reports that honestly — *Connected / Settings unavailable*, with the
reason on Settings → Diagnostics — rather than calling the server dead.
If the Diagnostics screen shows "Token accepted: Yes" and "Settings API:
Unavailable", the fix is a redeploy, not a new token.

### 5. Custom domain (optional)

Render provides `https://<service>.onrender.com` free. Add a custom domain in Settings → Custom Domains.

## Option 2: Fly.io (recommended for Ollama sidecar)

### 1. Install `flyctl` and login

```bash
curl -L https://fly.io/install.sh | sh
fly auth login
```

### 2. Launch the app

```bash
cd D:\AURA
fly launch --name aura-cloud --region <closest> --no-deploy
```

This creates `fly.toml`. Edit it:

```toml
app = "aura-cloud"
primary_region = "iad"

[build]
  dockerfile = "Dockerfile"

[env]
  AURA_SERVER_HOST = "0.0.0.0"
  AURA_SERVER_PORT = "8000"
  AURA_SERVER_CORS_ORIGINS = "*"
  AURA_SERVER_LOG_LEVEL = "INFO"

[http_service]
  internal_port = 8000
  force_https = true
  auto_stop_machines = true   # scales to zero when idle
  auto_start_machines = true
  min_machines_running = 0

[[vm]]
  memory = "512mb"
  cpu_kind = "shared"
  cpus = 1
```

### 3. Add a volume for SQLite

```bash
fly volumes create aura_data --region iad --size 1
```

Add to `fly.toml`:

```toml
[mounts]
  source = "aura_data"
  destination = "/app/data"
```

### 4. Secrets (never in `fly.toml`)

```bash
fly secrets set AURA_SERVER_AUTH_TOKEN="$(python -c "import secrets; print(secrets.token_urlsafe(32))")"
fly secrets set GEMINI_API_KEY="your-key"
# or
fly secrets set OLLAMA_HOST="http://ollama.internal:11434"
```

### 5. Ollama sidecar (optional, same private network)

Create a second app for Ollama:

```bash
fly launch --name aura-ollama --image ollama/ollama:latest --region iad --no-deploy
```

Edit its `fly.toml` to expose port 11434 internally, then reference it as `http://aura-ollama.internal:11434` from the main app's `OLLAMA_HOST`.

### 6. Deploy

```bash
fly deploy
```

Fly builds remotely (no local Docker needed) and deploys. Cold start is ~2s with `auto_stop_machines = true`.

## Option 3: Google Cloud Run

### 1. Enable APIs

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com
```

### 2. Build and push

```bash
gcloud builds submit --tag gcr.io/<PROJECT_ID>/aura-cloud
```

### 3. Deploy

```bash
gcloud run deploy aura-cloud \
  --image gcr.io/<PROJECT_ID>/aura-cloud \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars AURA_SERVER_AUTH_TOKEN="$(python -c "import secrets; print(secrets.token_urlsafe(32))"),GEMINI_API_KEY=xxx,AURA_SERVER_CORS_ORIGINS=* \
  --memory 512Mi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 1
```

### 4. Persistence

Cloud Run's filesystem is ephemeral. For SQLite you need:
- **Cloud SQL (PostgreSQL)** — migrate `memory/sqlite.py` to use `postgresql://` URL. The server code uses SQLAlchemy so this is a config change.
- **Filestore (NFS)** — mount at `/app/data`. More complex setup.

> For a $0 deployment with SQLite, Render or Fly.io with a volume is simpler.

## Provider keys (server-side only)

| Provider | Env var | Notes |
|----------|---------|-------|
| Gemini | `GEMINI_API_KEY` | Google AI Studio key |
| OpenAI | `OPENAI_API_KEY` | Not yet wired in `BrainRouter` — see Known Limitations |
| Ollama | `OLLAMA_HOST` | Must be reachable from the cloud (see platform notes) |
| Edge TTS | `EDGE_TTS_VOICE` | Desktop only; server does not synthesise |

**Never put these in the Android app, the Docker image, or GitHub.**

## CORS

- Development (phone on LAN): `AURA_SERVER_CORS_ORIGINS=*`
- Production: `AURA_SERVER_CORS_ORIGINS=https://your-frontend.example.com`

The Android app sends no `Origin` header, so it works regardless. This setting only affects browser clients.

## Health check

Three routes, answering different questions. Point your platform's probe
at the one that matches what you want it to react to.

| Route | Auth | Answers |
|---|---|---|
| `/` | public | Is the HTTP server up? (liveness) |
| `/api/ready` | public | Can it answer a chat turn? (readiness) |
| `/api/health` | **bearer** | Full runtime diagnostics |

```bash
curl https://your-service.onrender.com/api/ready
```

Expected: `{"ready":true,"llm_provider":"gemini->groq","problems":[]}`.

`/api/ready` returns **503** with the reasons in `problems` when the
runtime has not finished starting or the provider chain cannot be built —
the case a liveness probe cannot see, where the server is perfectly alive
and cannot do the one thing it exists for. This is what
`docker-compose.yml` now uses as its healthcheck.

It reports only what a chat turn actually requires. Vision, voice, screen
and companion are optional by design and are deliberately excluded: a
probe that failed because TTS was off would restart a healthy server
forever. It also does not call the provider — a readiness probe that made
a network request per poll would bill you for being observed and turn one
provider outage into a restart loop.

It is unauthenticated for the same reason `/` is: a container healthcheck
holds no bearer token. The body is a boolean plus failure categories — no
configuration, no versions, no secrets.

For full runtime diagnostics, use `GET /api/health` with
`Authorization: Bearer <token>`.

## Cold start behaviour

Free tiers scale to zero. The first request after idle:

1. Platform starts the container (~2-30s)
2. Aura runtime initializes (loads config, opens SQLite, connects to LLM provider)
3. Request completes

The Android app handles this: it shows "Aura server is waking up..." after 4s and retries. The user sees the reply when it arrives.

## Monitoring

- **Render**: Built-in metrics (CPU, RAM, request latency, deploy logs)
- **Fly.io**: `fly logs`, `fly dashboard`, Grafana Cloud integration (free tier)
- **Cloud Run**: Cloud Logging + Cloud Monitoring (free tier generous)

No separate APM required for this scale.

## Upgrading

```bash
# Render: push the branch the service tracks (see "Deploy" above -
# the server code is on feature/aura-identity, not on main)
git push origin feature/aura-identity

# Fly.io:
fly deploy

# Cloud Run:
gcloud builds submit --tag gcr.io/<PROJECT_ID>/aura-cloud
gcloud run deploy aura-cloud --image gcr.io/<PROJECT_ID>/aura-cloud ...
```

Database migrations: none yet (SQLAlchemy `create_all` on startup). When schema changes, add a migration script and run it at startup or as a one-off job.

## Known Limitations (current phase)

1. **OpenAI not wired** — `BrainRouter._create_provider` has no `"openai"` branch, and the empty `brain/providers/openai.py` placeholder was removed in the Phase 7 cleanup. The providers that do work are `mock`, `gemini`, `groq`, `mistral`, `openrouter` and `ollama`; the chain that actually runs is `llm.provider` plus `llm.fallback_providers` in config.yaml. A provider whose key is missing is skipped by name at startup.
2. **SQLite on ephemeral FS** — Render/Fly volumes solve this; Cloud Run needs Cloud SQL or Filestore.
3. **Ollama sidecar** — Only Fly.io supports a free private-network sidecar natively. On Render you need a separate paid service or hosted Ollama.
4. **No horizontal scale** — Single container. For HA, run 2+ replicas with a shared Postgres (not SQLite).
5. **TLS termination** — Handled by platform (Render/Fly/Cloud Run all provide HTTPS). The container sees HTTP on port 8000.
