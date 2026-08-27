"""
Aura API Server - FastAPI application.

Main entry point for the server mode.
"""
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Load environment variables from the project-root .env before anything
# initializes the runtime/provider chain.
#
# This is especially important for `python -m server.main`, because this
# entry point otherwise reaches BrainRouter before GEMINI_API_KEY is loaded
# into os.environ.
load_dotenv()

from server.config import cors_policy, enforce_auth_policy, settings
from server.runtime import init_runtime, is_initialized, shutdown_runtime
from server.routes import capabilities, health, chat, ws_chat, screen, notifications, settings as settings_routes
from server.routes import agent as agent_routes
from server.routes import device as device_routes
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

    # Before anything is served, and deliberately outside the
    # `is_initialized` guard below: a pre-installed runtime says something
    # about who built the engine, not about whether this process is safe to
    # expose. Raising here aborts startup - uvicorn will not bind the port.
    insecure_warning = enforce_auth_policy()
    if insecure_warning:
        logger.warning(insecure_warning)

    if not is_initialized():
        logger.info("Starting Aura API server...")
        init_runtime()
        logger.info(
            "Aura API server started on %s:%s", settings.host, settings.port
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

# CORS. The arguments come from `cors_policy` so that the one rule that
# matters - never wildcard origins together with credentials - lives with
# the settings it reads and can be tested without starting a server.
app.add_middleware(CORSMiddleware, **cors_policy())

# Routes
app.include_router(health.router)
app.include_router(capabilities.router)
app.include_router(chat.router)
app.include_router(ws_chat.router)
app.include_router(screen.router)
app.include_router(notifications.router)
app.include_router(settings_routes.router)
app.include_router(agent_routes.router)
app.include_router(device_routes.router)


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
