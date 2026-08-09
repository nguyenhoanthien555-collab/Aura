"""
Aura API Server - FastAPI application.

Main entry point for the server mode.
"""
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server.config import settings
from server.runtime import init_runtime, is_initialized, shutdown_runtime
from server.routes import health, chat, ws_chat, screen, notifications
from core.logger import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan.

    Aura Core is built exactly once, here. Requests never construct a
    provider, a memory manager or a personality - they reuse this one.

    A runtime that already exists is left alone: tests install a runtime
    wired to a mock provider before the app starts, and startup must not
    overwrite it.
    """
    if not is_initialized():
        logger.info("Starting Aura API server...")
        init_runtime()
        logger.info(
            "Aura API server started on %s:%s", settings.host, settings.port
        )

        if not settings.is_auth_enabled:
            logger.warning(
                "AURA_SERVER_AUTH_TOKEN is not set - the API is UNAUTHENTICATED. "
                "Bind to localhost only, or set a token before exposing it."
            )

    yield

    # Shutdown
    logger.info("Shutting down Aura API server...")
    shutdown_runtime()
    logger.info("Aura API server stopped")


app = FastAPI(
    title="Aura API",
    description="Aura Cloud Core API",
    version="0.2.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(health.router)
app.include_router(chat.router)
app.include_router(ws_chat.router)
app.include_router(screen.router)
app.include_router(notifications.router)


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "Aura API",
        "version": "0.2.0",
        "status": "running",
        "docs": "/docs",
    }


@app.head("/", include_in_schema=False)
async def root_head():
    """Render probes the public readiness route with HEAD."""
    return None


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        reload=False,
    )
