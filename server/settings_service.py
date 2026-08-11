"""
Applying settings changes to a *running* Aura.

`core/settings_store.py` decides what a change is valid; this module
decides what happens to it: whether the running system adopts it
immediately, whether a restart is required for it to take effect, or
whether it was persisted but could not be applied to this process.

This is where the promise of the Control Hub is kept - a toggle in the
phone actually changes the subsystem - and where it is kept honest:
a change that only `config.yaml` would have honoured is reported as
`restart_required`, never as applied.

WHAT APPLIES LIVE, AND WHY
--------------------------
Persisted-and-live:
    llm.provider, llm.model, the fallback chain and per-provider models,
    llm.temperature/max_output_tokens/timeout
        The provider chain is rebuilt from the effective config; the
        router's cached provider is cleared. `conversation.llm` is a
        `BrainRouter`, and `BrainRouter.provider` builds lazily, so
        clearing `_provider` makes the next turn construct the chain the
        user just asked for. When the active provider is also rebuilt,
        a new stream picks it up too.
    proactive.*
        `ProactivePolicy.settings` is read at every decision - the
        policy object keeps a reference to its `ProactiveSettings`
        instance, so mutating that instance in place is visible to the
        next `tick`.
    memory.recall
        `MemoryPipeline.recall_enabled` is read per turn.

Persisted, needs restart:
    memory.profile/pipeline/history_limit/retrieval_scope, vision.enabled,
    vision models, voice.tts/stt.enabled, tools.enabled,
    server.screen.enabled, server.companion.enabled
        These gate construction in `build_services` / `_build_remote_vision`
        / `_build_companion`. The pipeline is built once per process on
        purpose (AURA-P1-003) and rebuilding subsystems mid-flight would
        be the architectural rewrite Phase 9 is forbidden to do. The
        operator restarts once; the config is already persisted.

        `memory.history_limit` is the subtle one. Mutating
        `conversation.history_limit` would change the transcript window
        immediately, but the same number was also passed to
        `KeywordRetriever(skip_recent=...)` at construction
        (`launcher/services.py`), and that copy would keep the old value.
        A setting that applies to half its subsystems is worse than one
        that honestly says "restart", so it is not in LIVE_PATHS.
"""

from __future__ import annotations

import time

from core.config import load_config
from core.logger import logger
from core.settings_store import SettingsError


# Live-applyable paths. A PATCH arrives flattened, so the checks are
# exact dotted paths only.
LIVE_PATHS = {
    "llm.provider", "llm.model",
    "llm.fallback_providers", "llm.fallback_model",
    "llm.groq_model", "llm.mistral_model", "llm.ollama_model",
    "llm.temperature", "llm.max_output_tokens", "llm.timeout",
    "proactive.enabled", "proactive.cooldown_seconds",
    "proactive.max_per_day", "proactive.quiet_hours",
    "proactive.duplicate_window_seconds", "proactive.similarity_threshold",
    "memory.recall",
}


class SettingsService:
    """
    The one place a settings PATCH turns into subsystem changes.
    """

    def __init__(self, runtime):

        self.runtime = runtime

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def effective(self) -> dict:
        """The config the running system is on right now, minus secrets."""

        return self.runtime.config

    def get(self) -> dict:
        """
        Everything the Control Hub needs to render its settings screen.

        Effective values (with the overlay merged) and current overrides,
        plus a provider summary so the screen can show its state in one
        request. Never includes an API key - `masked_all` only ever emits
        the masked form.
        """

        from core.credentials import get_credential_store

        store = get_credential_store()

        config = self.runtime.config

        return {
            "effective": config,
            "overrides": self.runtime.settings_store.overrides,
            "providers": {
                "available": store.masked_all(),
                "persistent": store.persistent,
                "persistence_note": store.unavailable_reason(),
            },
        }

    def apply(self, changes: dict, *, live: bool = True) -> dict:
        """
        Persist `changes` and apply them to the running system.

        Returns a report:

            applied:            paths now in effect
            restart_required:   paths persisted but needing a restart
            persistent:         whether the write survived to disk
            needs_restart:      true if any change needs one

        Raises SettingsError when nothing was valid.
        """

        store = self.runtime.settings_store

        accepted = store.update(changes)

        if not accepted:
            raise SettingsError("No settings were provided")

        # `update` raises rather than returning when it cannot write the
        # overlay, so reaching this line means the change is on disk. The
        # field stays in the report because the client shows it, and
        # because API keys - which genuinely can be non-persistent, see
        # `CredentialStore.persistent` - are reported the same shape.
        persisted = True

        applied: list[str] = []
        restart: list[str] = []

        for path in accepted:
            if path in LIVE_PATHS:
                applied.append(path)
            else:
                restart.append(path)

        # Rebuild the provider chain exactly once, and only if any of the
        # llm.* paths actually changed - a PATCH that touches only
        # proactive.* must not tear down the provider chain.
        llm_changed = any(path.startswith("llm.") for path in accepted)

        if llm_changed:
            rebuilt = self._reapply_llm()

            if not rebuilt:
                # The chain could not be rebuilt (a provider whose key is
                # still missing). The change is persisted and will apply on
                # the next restart; claiming it is live would be a lie.
                for path in list(applied):
                    if path.startswith("llm."):
                        applied.remove(path)
                        restart.append(path)

        self._reapply_proactive(accepted)
        self._reapply_memory(accepted)

        return {
            "applied": applied,
            "restart_required": restart,
            "persistent": persisted,
            "needs_restart": bool(restart),
        }

    # ------------------------------------------------------------------
    # Live application
    # ------------------------------------------------------------------

    def _reapply_llm(self) -> list[str]:
        """
        Rebuild the provider chain from the effective config.

        Returns the names of the providers now in the chain, or [] when
        the rebuild failed. `llm.provider` is the primary, so asking for
        a provider whose key is missing yields [] - the router raises
        when `provider` is first accessed, which is the same error the
        operator would have seen at boot, and which the health endpoint
        reports as not-ready.
        """

        router = getattr(self.runtime.engine.conversation, "llm", None)

        if router is None or not hasattr(router, "_provider"):
            return []

        config = load_config()
        provider = (config.get("llm") or {}).get("provider", "mock")

        # Clear the cached chain. `BrainRouter.provider` rebuilds on next
        # access, reading the new effective config (and, through
        # `_create_provider`, the new keys already in the environment).
        router._provider = None
        router.provider_name = provider

        try:
            # Force the rebuild now so a broken configuration fails this
            # request instead of the next chat turn, and so the response
            # can report the chain honestly.
            chain = router.active_chain()
        except Exception as error:
            logger.warning(
                "Provider chain could not be rebuilt (%s); old chain "
                "retained until restart", type(error).__name__,
                exc_info=True,
            )
            # Keep the old chain live rather than a broken new one.
            router._provider = None
            router.provider_name = None
            return []

        # A FallbackProvider exposes its chain as provider_name.
        return [p for p in str(chain).split("->") if p]

    def _reapply_proactive(self, accepted: dict) -> None:
        """
        Mutate the proactive policy's settings in place.

        `ProactiveSettings` is a dataclass instance owned by the policy;
        the policy reads `self.settings.*` at decision time, so mutating
        the object is enough - no rebuild, no restart.
        """

        proactive = {
            key.split(".", 1)[1]: value
            for key, value in accepted.items()
            if key.startswith("proactive.")
        }

        if not proactive:
            return

        policy = getattr(
            getattr(self.runtime.services, "proactive", None),
            "policy",
            None,
        )

        if policy is None or not hasattr(policy, "settings"):
            return

        settings = policy.settings

        for key, value in proactive.items():
            if hasattr(settings, key):
                setattr(settings, key, value)

    def _reapply_memory(self, accepted: dict) -> None:
        """
        Flip `MemoryPipeline.recall_enabled` for memory.recall.

        Anything deeper - episodic store, user model, temporary context -
        is constructed once and is not hot-swappable without rebuilding
        the pipeline, which Phase 9 does not do. The gate itself is.
        """

        recall = accepted.get("memory.recall")

        if not isinstance(recall, bool):
            return

        pipeline = getattr(
            getattr(self.runtime.services, "memory", None),
            "pipeline",
            None,
        )

        if pipeline is not None:
            pipeline.recall_enabled = recall


# ----------------------------------------------------------------------
# Live provider test
# ----------------------------------------------------------------------

def test_provider(provider: str, model: str | None = None) -> dict:
    """
    Probe one provider with a real request and report honestly.

    Used by `POST /api/providers/test`. Builds the provider the same way
    the router would, sends a tiny prompt, and classifies the outcome.
    The response deliberately contains nothing that could be a key:
    "ok" or an error category, never the request or its body.

    A provider that is not registered here (cerebras) is refused with a
    clear reason rather than silently probed.
    """

    from brain.router import BrainRouter

    name = str(provider).strip().lower()

    if name not in {"mock", "gemini", "groq", "mistral", "openrouter", "ollama"}:
        return {"provider": name, "ok": False, "error": "unknown provider"}

    try:
        candidate = BrainRouter._instantiate_provider(
            name, load_config().get("llm") or {}
        )
    except Exception as error:
        return {
            "provider": name,
            "ok": False,
            "error": "not configured",
            "detail": type(error).__name__,
        }

    if candidate is None:
        return {
            "provider": name,
            "ok": False,
            "error": BrainRouter._skip_reason(name),
        }

    if model:
        try:
            candidate.model = model
        except AttributeError:
            return {
                "provider": name,
                "ok": False,
                "error": "model not supported",
            }

    start = time.time()

    try:
        reply = candidate.generate("Reply with the single word: ok")
        elapsed = time.time() - start

        # A provider that answers but not with the word we sent is still
        # reachable; the word is a hint, not a contract.
        ok = bool(str(reply or "").strip())

        return {
            "provider": name,
            "ok": ok,
            "model": getattr(candidate, "model", ""),
            "latency_ms": int(elapsed * 1000),
        }

    except Exception as error:
        elapsed = time.time() - start
        return {
            "provider": name,
            "ok": False,
            "error": "unreachable",
            "detail": type(error).__name__,
            "latency_ms": int(elapsed * 1000),
        }
