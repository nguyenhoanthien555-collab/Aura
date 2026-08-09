# Aura Cloud Core — Multi-stage Dockerfile
#
# Build:  docker build -t aura-cloud-core:latest .
# Run:    docker run -d --name aura -p 8000:8000 --env-file .env \
#           -v aura-data:/app/data -v aura-logs:/app/logs aura-cloud-core:latest
#
# The image runs the same FastAPI application the desktop build uses.
# No server-only code paths exist outside server/; the composition root
# in launcher.services.build_services is shared with desktop mode.

# ----------------------------------------------------------------------
# Base: Python 3.12 slim — smaller than full, has glibc, no build tools
# ----------------------------------------------------------------------
FROM python:3.12-slim AS base

# System deps: runtime only. No build-essential, no gcc.
# libpq-dev is for potential future Postgres; harmless if unused.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user for defense in depth
RUN groupadd -r aura && useradd -r -g aura -m -d /app -s /sbin/nologin aura

WORKDIR /app

# ----------------------------------------------------------------------
# Dependencies: install from the pinned server requirements
# ----------------------------------------------------------------------
FROM base AS deps

COPY requirements-server.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements-server.txt

# ----------------------------------------------------------------------
# Runtime: copy the application source and the installed deps
# ----------------------------------------------------------------------
FROM base AS runtime

# Copy the pinned wheels from the deps stage
COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

# Application source (everything except what .dockerignore excludes)
COPY --chown=aura:aura . .

# Data and logs directories — volumes mount here at runtime
RUN mkdir -p /app/data /app/logs && chown -R aura:aura /app/data /app/logs

USER aura

# The server reads its config from the environment (AURA_SERVER_*)
# and from config.yaml (committed, no secrets).
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AURA_SERVER_HOST=0.0.0.0 \
    AURA_SERVER_PORT=8000

EXPOSE 8000

# Uvicorn with one worker per container. The process manager
# (systemd, Docker, Render, Fly, etc.) handles horizontal scale.
CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]