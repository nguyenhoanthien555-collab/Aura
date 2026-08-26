"""
Settings and provider management.

The endpoints the Android Control Hub talks to. Every one of them is
behind `verify_token` - the same bearer token that guards chat - because
these routes change which model answers, which keys are used and whether
Aura may speak unprompted. An unauthenticated caller who could PATCH
here could point Aura at their own provider.

WHAT NEVER LEAVES THIS MODULE
-----------------------------
An API key. `GET /api/providers` reports whether a key is configured and
its masked tail; `PUT` accepts one and returns the mask; `POST .../test`
uses one and returns a latency. No route returns a key, echoes one back,
or puts one in an error - `CredentialStore.secret_for` is called in
exactly one place (the provider test, indirectly through the router) and
its result is never serialized.

Errors from this module are deliberately plain strings that name the
setting, e.g. "proactive.max_per_day must be between 1 and 20". They are
written for a person holding a phone, and they contain no exception text
(see `server/errors.py` for why that matters).
"""

import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field

from core.credentials import (
    CredentialError,
    CredentialNotPersisted,
    get_credential_store,
)
from core.logger import logger
from core.settings_store import ALLOWED, SettingsError
from server.auth import verify_token
from server.runtime import get_runtime


router = APIRouter(prefix="/api", tags=["settings"])


# ----------------------------------------------------------------------
# Models
# ----------------------------------------------------------------------

class SettingsPatch(BaseModel):
    """
    A partial settings update, nested or flat.

    `settings` is free-form because the allow-list in
    `core/settings_store.py` is the real schema and restating it as a
    pydantic model would be a second source of truth that could drift.
    Unknown paths are rejected there, by name.
    """

    settings: Dict[str, Any] = Field(default_factory=dict)


class ApiKeyBody(BaseModel):
    """A provider key. Capped: no real key is anywhere near 512 chars."""

    key: str = Field(min_length=1, max_length=512)


class ProviderTestBody(BaseModel):
    provider: str = Field(min_length=1, max_length=40)
    model: Optional[str] = Field(default=None, max_length=120)


# ----------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------

@router.get("/settings")
async def get_settings(token: str = Depends(verify_token)):
    """
    The effective configuration, the overlay, and provider key state.

    "Effective" means what this process is actually running on: config.yaml
    merged with the Control Hub overlay. The phone renders from this, so a
    setting shown as on is on *on the server*, not just on the device.
    """

    runtime = get_runtime()

    payload = runtime.settings_service.get()

    payload["configurable"] = sorted(ALLOWED)

    return payload


@router.patch("/settings")
async def update_settings(
    body: SettingsPatch,
    background_tasks: BackgroundTasks,
    token: str = Depends(verify_token)
):
    """
    Update configuration overrides.

    A 422 means nothing changed and the message names the offending
    setting. A 200 reports `applied` (live now) separately from
    `restart_required` (persisted, needs a restart) - the UI shows the
    difference rather than implying every toggle is instant.
    """

    runtime = get_runtime()

    try:
        report = runtime.settings_service.apply(body.settings)

    except SettingsError as error:
        # Deliberately returned verbatim: these messages are written for
        # the user and contain no internals.
        raise HTTPException(status_code=422, detail={"error": "invalid_setting", "message": str(error)})

    except Exception as error:
        logger.error("Settings update failed: %s", type(error).__name__, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "settings_failed", "message": "Settings could not be applied"},
        )

    report["effective"] = runtime.settings_service.effective()

    # Automatically restart the Python process in-place if any change requires it.
    # This prevents the user from having to manually restart the service on platforms
    # like Render, which would wipe the ephemeral disk and lose the settings just saved.
    if report.get("needs_restart"):
        import os
        import sys
        import asyncio

        async def _do_restart():
            await asyncio.sleep(1.0)
            logger.info("Performing in-place restart to apply settings...")
            try:
                # sys.argv[0] is often the absolute script path. To avoid ModuleNotFoundError
                # for absolute imports like `server.config`, we must ensure the working
                # directory is in PYTHONPATH.
                env = os.environ.copy()
                env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")
                os.execve(sys.executable, ["python"] + sys.argv, env)
            except Exception as e:
                logger.error("Failed to execve: %s", e)

        background_tasks.add_task(lambda: asyncio.create_task(_do_restart()))

    return report


@router.post("/settings/reset")
async def reset_settings(
    background_tasks: BackgroundTasks,
    body: Optional[Dict[str, Any]] = None, 
    token: str = Depends(verify_token)
):
    """
    Drop overrides and fall back to config.yaml.

    With `{"paths": [...]}` only those are dropped; with no body, all of
    them. Provider API keys are NOT touched - they live in the credential
    store, and a settings reset that silently deleted every key would be
    a destructive surprise.
    """

    runtime = get_runtime()

    paths = None

    if isinstance(body, dict) and body.get("paths") is not None:
        candidate = body.get("paths")
        if not isinstance(candidate, list):
            raise HTTPException(
                status_code=422,
                detail={"error": "invalid_setting", "message": "paths must be a list"},
            )
        paths = [str(entry) for entry in candidate]

    removed = runtime.settings_store.reset(paths)

    if removed:
        # The overlay shrank, so the values underneath it are the effective
        # ones again. Without this the next GET would still report what was
        # just reverted.
        runtime.settings_service.refresh_config()

    if removed and any(path.startswith("llm.") for path in removed):
        # The overlay's provider choice is gone, so the live chain must go
        # back to what config.yaml says rather than staying on the model
        # the user just reverted.
        runtime.settings_service._reapply_llm()
        
    needs_restart = bool(removed)
    if needs_restart:
        import os
        import sys
        import asyncio

        async def _do_restart():
            await asyncio.sleep(1.0)
            logger.info("Performing in-place restart to apply settings reset...")
            try:
                env = os.environ.copy()
                env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")
                os.execve(sys.executable, ["python"] + sys.argv, env)
            except Exception as e:
                logger.error("Failed to execve: %s", e)

        background_tasks.add_task(lambda: asyncio.create_task(_do_restart()))

    return {
        "reset": removed,
        "needs_restart": needs_restart,
        "message": "Settings reverted to the server configuration",
    }


# ----------------------------------------------------------------------
# Providers
# ----------------------------------------------------------------------

# What each provider can actually do, in this codebase, today. Claims are
# read off the implementations, not off the vendors' marketing:
#
#   streaming     the provider class defines stream()
#   tools         the tool loop is provider-agnostic (it reads the model's
#                 text), so every chat-capable provider supports it
#   vision        the provider is wired into vision/cloud_processor.py
#
# Three fields exist for the API-keys and model screens:
#
#   api_base      the endpoint this provider posts to by default. The
#                 *default*, never the effective value: a base URL override
#                 can carry a token in its query string on some gateways,
#                 and this response is rendered on a phone. Whether an
#                 override is in effect is reported as a boolean below.
#   api_key_env   the variable the key is read from. Safe to publish - the
#                 router already names it when explaining a skipped
#                 provider - and it is what lets the UI say where a
#                 host-provided key came from.
#   model_setting the settings path holding this provider's model, so the
#                 model picker does not need a copy of this mapping. Must
#                 agree with `brain.router`: for the shared-client
#                 providers that is `HTTP_CHAT_PROVIDERS[name][2]`, and
#                 `tests/test_provider_resolution.py` asserts it does.
#
# `configured` is added per request from the credential store.
PROVIDER_CAPABILITIES = {
    "gemini": {
        "label": "Google Gemini",
        "chat": True, "streaming": True, "tools": True, "vision": True,
        "keyless": False,
        "models": ["gemini-3.6-flash", "gemini-2.5-flash", "gemini-2.5-pro"],
        "api_base": "https://generativelanguage.googleapis.com",
        "api_key_env": "GEMINI_API_KEY",
        "model_setting": "llm.model",
    },
    "openai": {
        "label": "OpenAI",
        "chat": True, "streaming": True, "tools": True, "vision": False,
        "keyless": False,
        "models": ["gpt-5.1", "gpt-5.1-mini", "gpt-4.1"],
        "api_base": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "model_setting": "llm.openai_model",
    },
    "anthropic": {
        "label": "Anthropic Claude",
        "chat": True, "streaming": True, "tools": True, "vision": False,
        "keyless": False,
        "models": ["claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5"],
        "api_base": "https://api.anthropic.com/v1",
        "api_key_env": "ANTHROPIC_API_KEY",
        "model_setting": "llm.anthropic_model",
    },
    "groq": {
        "label": "Groq",
        "chat": True, "streaming": False, "tools": True, "vision": False,
        "keyless": False,
        "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
        "api_base": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "model_setting": "llm.groq_model",
    },
    "cerebras": {
        "label": "Cerebras",
        "chat": True, "streaming": True, "tools": True, "vision": False,
        "keyless": False,
        "models": ["llama-3.3-70b", "llama3.1-8b"],
        "api_base": "https://api.cerebras.ai/v1",
        "api_key_env": "CEREBRAS_API_KEY",
        "model_setting": "llm.cerebras_model",
    },
    "openrouter": {
        "label": "OpenRouter",
        "chat": True, "streaming": False, "tools": True, "vision": False,
        "keyless": False,
        "models": ["openrouter/free"],
        "api_base": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        # OpenRouter reads `fallback_model` first, because that is the
        # setting that has always named its model. See `brain/router.py`.
        "model_setting": "llm.fallback_model",
    },
    "mistral": {
        "label": "Mistral",
        "chat": True, "streaming": True, "tools": True, "vision": False,
        "keyless": False,
        "models": ["open-mistral-7b", "mistral-small-latest"],
        "api_base": "https://api.mistral.ai/v1",
        "api_key_env": "MISTRAL_API_KEY",
        "model_setting": "llm.mistral_model",
    },
    "xai": {
        "label": "xAI Grok",
        "chat": True, "streaming": True, "tools": True, "vision": False,
        "keyless": False,
        "models": ["grok-4", "grok-4-fast"],
        "api_base": "https://api.x.ai/v1",
        "api_key_env": "XAI_API_KEY",
        "model_setting": "llm.xai_model",
    },
    "deepseek": {
        "label": "DeepSeek",
        "chat": True, "streaming": True, "tools": True, "vision": False,
        "keyless": False,
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "api_base": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
        "model_setting": "llm.deepseek_model",
    },
    "qwen": {
        "label": "Qwen (DashScope)",
        "chat": True, "streaming": True, "tools": True, "vision": False,
        "keyless": False,
        "models": ["qwen-plus", "qwen-max", "qwen-turbo"],
        "api_base": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "QWEN_API_KEY",
        "model_setting": "llm.qwen_model",
    },
    "custom": {
        "label": "Custom endpoint (OpenAI-compatible)",
        "chat": True, "streaming": True, "tools": True, "vision": False,
        "keyless": False,
        # No vendor to claim a base for, and no model list to offer. The UI
        # has to ask for both; offering a choice would mean inventing one.
        "models": [],
        "api_base": "",
        "api_key_env": "CUSTOM_API_KEY",
        "model_setting": "llm.custom_model",
    },
    "ollama": {
        "label": "Ollama (local)",
        "chat": True, "streaming": True, "tools": True, "vision": False,
        "keyless": True,
        "models": [],
        "api_base": "http://localhost:11434",
        "api_key_env": "",
        "model_setting": "llm.ollama_model",
    },
    "mock": {
        "label": "Mock (offline)",
        "chat": True, "streaming": True, "tools": False, "vision": False,
        "keyless": True,
        "models": [],
        "api_base": "",
        "api_key_env": "",
        "model_setting": "",
    },
}

# The variable that overrides `api_base` for each provider, reported as a
# boolean so the UI can say "using a custom endpoint" without the value
# leaving the server.
PROVIDER_BASE_URL_ENV = {
    "openai": "OPENAI_BASE_URL",
    "anthropic": "ANTHROPIC_BASE_URL",
    "cerebras": "CEREBRAS_BASE_URL",
    "groq": "GROQ_BASE_URL",
    "mistral": "MISTRAL_BASE_URL",
    "xai": "XAI_BASE_URL",
    "deepseek": "DEEPSEEK_BASE_URL",
    "qwen": "QWEN_BASE_URL",
    "custom": "CUSTOM_BASE_URL",
    "ollama": "OLLAMA_HOST",
}


@router.get("/providers")
async def list_providers(token: str = Depends(verify_token)):
    """
    Every provider Aura can build, what it supports, and its key state.

    Capabilities are per-implementation facts, not vendor claims: Groq
    has no `stream()` in this codebase, so `streaming` is false for Groq
    even though the API supports it. The UI shows what Aura can actually
    do.
    """

    runtime = get_runtime()

    store = get_credential_store()

    llm = (runtime.config.get("llm") or {})

    active = getattr(
        getattr(runtime.engine.conversation, "llm", None), "provider_name", ""
    )

    providers = []

    for name, capability in PROVIDER_CAPABILITIES.items():

        base_env = PROVIDER_BASE_URL_ENV.get(name, "")

        # The model this provider would actually be built with, read
        # through the same settings the router reads, so the picker shows
        # the effective value rather than the class default.
        setting = capability.get("model_setting") or ""
        model = llm.get(setting.split(".", 1)[1], "") if setting else ""

        providers.append({
            "name": name,
            **capability,
            "model": model,
            "base_url_env": base_env,
            # A boolean, not the URL: an override can carry a token in its
            # query string on some gateways.
            "api_base_overridden": bool(base_env and os.getenv(base_env)),
            "configured": bool(capability["keyless"]) or store.has(name),
            "key_masked": store.masked(name),
            "key_source": store.source_of(name),
            "is_primary": name == llm.get("provider"),
            "is_fallback": name in (llm.get("fallback_providers") or []),
        })

    return {
        "providers": providers,
        "primary": llm.get("provider", ""),
        "fallback_providers": list(llm.get("fallback_providers") or []),
        "active_chain": active,
        "key_storage": {
            "persistent": store.persistent,
            # Named to match the same field in `GET /api/settings`. One
            # shape for "can keys be stored here, and why not", so a
            # client can parse both with one type.
            "persistence_note": store.unavailable_reason(),
        },
    }


@router.get("/providers/health")
async def provider_health(token: str = Depends(verify_token)):
    """
    The chain as it stands, and whether Aura is running on a fallback.

    Reads state the `FallbackProvider` already keeps - no provider is
    called, so this is safe to poll. `in_fallback` is the question the
    recovery UI actually asks: is Aura answering from the provider I
    chose, or from a substitute?
    """

    runtime = get_runtime()

    router_obj = getattr(runtime.engine.conversation, "llm", None)

    requested = getattr(router_obj, "provider_name", "") or ""

    chain = ""
    active = ""
    problems: list[str] = []

    try:
        provider = getattr(router_obj, "provider", None)
        chain = getattr(provider, "provider_name", requested) or ""
        # FallbackProvider tracks which member last answered.
        active = getattr(provider, "active_provider_name", chain) or chain
    except Exception as error:
        problems.append(f"provider chain unavailable ({type(error).__name__})")

    members = [name for name in str(chain).split("->") if name]

    return {
        "requested": requested,
        "active": active,
        "chain": members,
        "in_fallback": bool(active and members and active != members[0]),
        "problems": problems,
        "ready": not problems,
        "providers": _per_provider_health(members, active),
    }


def _per_provider_health(members: list[str], active: str) -> dict[str, dict]:
    """
    Per-provider `configured` / `healthy`, without calling anything.

    WHY THIS DOES NOT PROBE
    -----------------------
    `healthy` here means "this provider was built into the chain that is
    currently serving", not "it answered a request a moment ago". Probing
    six providers on a route a settings screen opens would bill the
    deployment for being looked at, which is the same reason
    `/api/ready` refuses to call the provider. The button that really
    asks is `POST /api/providers/test`, and it is a button precisely
    because it costs a token.

    FAILURE ISOLATION
    -----------------
    One unreadable provider must not blank the whole map, so each entry
    is computed in its own `try`. A provider that raises is reported as
    `configured: false, healthy: false` with a category, never an
    exception message - this response is rendered on a phone.
    """

    from core.credentials import get_credential_store

    store = get_credential_store()
    out: dict[str, dict] = {}

    # Where the serving provider sits in the chain. Everything before it
    # was tried and did not answer; everything after it was never asked.
    try:
        active_index = members.index(active) if active in members else -1
    except ValueError:                                   # pragma: no cover
        active_index = -1

    for name, caps in PROVIDER_CAPABILITIES.items():
        try:
            keyless = bool(caps.get("keyless"))
            configured = keyless or store.has(name)

            position = members.index(name) if name in members else -1

            if position < 0:
                # Not in the chain at all: it is not being used, which is
                # not the same as broken.
                state = "idle" if configured else "unconfigured"
                healthy = False

            elif name == active:
                state = "active"
                healthy = True

            elif 0 <= active_index and position < active_index:
                # The chain moved past it. That is the one case where we
                # can honestly say a provider failed.
                state = "failed"
                healthy = False

            else:
                # In the chain, behind the active one. Never asked, so
                # nothing is known beyond whether it has a key.
                state = "standby"
                healthy = False

            out[name] = {
                "configured": configured,
                "healthy": healthy,
                "state": state,
                "in_chain": position >= 0,
            }

        except Exception as error:
            # One unreadable provider must not blank the map. A category,
            # never a message - this is rendered on a phone.
            out[name] = {
                "configured": False,
                "healthy": False,
                "state": "error",
                "in_chain": False,
                "problem": type(error).__name__,
            }

    return out


@router.post("/providers/test")
async def check_provider(body: ProviderTestBody, token: str = Depends(verify_token)):
    """
    Send one tiny prompt to a provider and report what happened.

    This costs a real token or two, so it is a button, never a poll. The
    response carries a latency and an error *category* - never the
    request, the key, or the provider's raw error text.
    """

    from server.settings_service import test_provider

    return test_provider(body.provider, body.model)


# ----------------------------------------------------------------------
# API keys
# ----------------------------------------------------------------------

@router.put("/providers/{provider}/key")
async def set_provider_key(
    provider: str, body: ApiKeyBody, token: str = Depends(verify_token)
):
    """
    Store an API key for `provider`, encrypted, and use it immediately.

    Returns the masked form so the UI can confirm what it saved without
    ever being told the key again. When the server has no secret to
    encrypt with, the key is applied to this process and the response
    says plainly that it will not survive a restart - rather than writing
    it to disk in plaintext.
    """

    store = get_credential_store()

    try:
        store.set(provider, body.key)

    except CredentialNotPersisted as error:
        # A valid key, in effect, that could not be written to disk (no
        # secret configured). That is a success with a caveat - the
        # operator needs the key *now*, and the warning says plainly that
        # it will not survive a restart.
        return {
            "provider": provider,
            "saved": True,
            "persistent": False,
            "key_masked": store.masked(provider),
            "warning": str(error),
        }

    except CredentialError as error:
        # Rejected - unknown provider, blank key, or a masked value sent
        # back as if it were a key. Nothing was saved and nothing applied.
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_key", "message": str(error)},
        )

    return {
        "provider": provider,
        "saved": True,
        "persistent": True,
        "key_masked": store.masked(provider),
    }


@router.delete("/providers/{provider}/key")
async def delete_provider_key(provider: str, token: str = Depends(verify_token)):
    """
    Forget a stored key, and unset it for this process.

    A key that came from the deployment's environment has nothing on disk
    to delete; it is unset here and returns on restart. `key_source` in
    `GET /api/providers` is what lets the UI say so beforehand.
    """

    store = get_credential_store()

    existed = store.delete(provider)

    return {
        "provider": provider,
        "deleted": existed,
        "key_masked": store.masked(provider),
    }
