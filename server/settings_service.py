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
        Two mechanisms, both live, and neither was before Phase 11.
        `MemoryPipeline.recall_enabled` is read by `memory_lines` on the
        next turn; `Services.knowledge.retriever` is swapped between
        `KeywordRetriever` and `NullRetriever`, the same choice
        `launcher/services.py` makes at build time, and is read per turn
        through `_recalled`. Conditional, because a deployment with
        `memory.pipeline` off and no knowledge provider has nothing to
        move. Until Phase 11 this path was listed here and in LIVE_PATHS
        while `recall_enabled` was written at build time and read by
        nobody, and the handler reached for the pipeline through
        `services.memory`, where it is not.
    server.screen.min_interval
        `VisionManager._is_fresh` reads `self.min_interval` on every
        observation, so setting the attribute moves the next one.
    tools.enabled, tools.auto_approve, tools.timeout
        All three are `ToolPolicy` fields and the executor reads them per
        call (`tools/executor.py`: `policy.enabled` at 119 and 157,
        `policy.auto_approve` at 179, `policy.timeout` at 273). Replacing
        `executor.policy` with one rebuilt from the effective config
        therefore applies them immediately. The tool *registry* is not
        rebuilt, which is why `tools.allowed_paths` and `tools.applications`
        are not settable at all.
    voice.tts.voice, voice.tts.volume
        Passed through at each synthesis from `self.voice`/`self.volume` on
        the provider, so they can be moved on a live one.
    temporal.timezone
        `TemporalClock.use_timezone` re-resolves the zone on the clock
        object itself, and `launcher/services.py` hands that one object to
        every subsystem that reads a time, so the change arrives at all of
        them at once. `restart_required` when this process has no clock.

Applied live when the subsystem is running, restart_required when it is
not:
    The three above that need a live object - the vision manager, the tool
    executor, the TTS provider - are reported as `restart_required` when
    that object does not exist in this process, which on a headless server
    is the normal case for vision and voice. The report is derived from
    whether the assignment actually happened, not from a table.

Persisted, needs restart:
    memory.profile/pipeline/history_limit/retrieval_scope, vision.enabled,
    vision models, voice.tts.enabled/provider/playback, voice.stt.enabled,
    server.screen.enabled, server.companion.enabled
        These gate construction in `build_services` / `_build_remote_vision`
        / `_build_companion`. `vision.enabled` gates one more thing since
        phase 19.2: `tools/factory.py` registers `describe_screen` only
        while vision is on, so turning vision on over PATCH persists and
        takes effect on the next start rather than adding the tool to a
        live executor - the same restart this section already promises. The pipeline is built once per process on
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

        `voice.tts.playback` is `create_audio_player(enabled=...)`
        (`voice/factory.py:203`) and `voice.tts.provider` selects the
        class, so neither can move on a built engine.
"""

from __future__ import annotations

import time

from core.config import load_config
from core.logger import logger
from core.settings_store import SettingsError


# The companion gate's five tunable knobs, named once. They are both a
# LIVE_PATHS group and the argument to one handler, and two hand-written
# copies of the same list would eventually disagree about which of them
# applies live.
COMPANION_LIVE_PATHS = (
    "server.companion.relevance_threshold",
    "server.companion.cooldown_seconds",
    "server.companion.max_per_hour",
    "server.companion.quiet_hours",
    "server.companion.suppress_after_chat_seconds",
    "server.companion.duplicate_window_seconds",
)


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
    "server.screen.min_interval",
    *COMPANION_LIVE_PATHS,
    "tools.enabled", "tools.auto_approve", "tools.timeout",
    "voice.tts.voice", "voice.tts.volume",
    "temporal.timezone",
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

        # The overlay changed, so the runtime's config snapshot - what
        # every report reads - is now out of date. Refreshed before the
        # subsystem handlers run so that a failure in one of them cannot
        # leave the reports describing the previous state.
        self.refresh_config()

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

        # Subsystem-conditional paths. Each handler is called once, for the
        # whole group it owns, and answers whether the change reached a live
        # object. A no means the setting is on disk but not in effect, and
        # the report has to move it - `applied` is a promise.
        for paths, handler in (
            (("server.screen.min_interval",), self._reapply_screen),
            (("tools.enabled", "tools.auto_approve", "tools.timeout"),
             self._reapply_tools),
            (("voice.tts.voice", "voice.tts.volume"), self._reapply_voice),
            (("memory.recall",), self._reapply_memory),
            (("temporal.timezone",), self._reapply_temporal),
            # A lambda because this handler needs the paths themselves, not
            # just a signal that one of them was touched: five settings
            # share one policy object and they are written in one pass.
            (COMPANION_LIVE_PATHS,
             lambda: self._reapply_companion(accepted)),
        ):
            touched = [path for path in paths if path in accepted]

            if not touched:
                continue

            if handler():
                continue

            for path in touched:
                if path in applied:
                    applied.remove(path)
                    restart.append(path)

        return {
            "applied": applied,
            "restart_required": restart,
            "persistent": persisted,
            "needs_restart": bool(restart),
        }

    def refresh_config(self) -> dict:
        """
        Re-merge the overlay into the runtime's config snapshot.

        `ServerRuntime.config` is built once in the constructor, because
        `build_services` hands the same dict to every subsystem and a
        config that changed under a running pipeline would be worse than
        one that needs a restart. But it is also what every *report* reads
        - `GET /api/settings`'s `effective`, `GET /api/providers`'s
        primary and fallback list, the version in `/api/health` - and a
        report must not be a snapshot from process start.

        Without this, a successful PATCH persisted the value, applied it
        to the live subsystem, and then answered the next GET with the old
        one. The phone renders its controls from `effective`, so the
        switch the user had just moved would spring back while the server
        quietly used the new value: the same change reported two ways.

        Called after every write to the overlay. Cheap enough for that -
        it is one YAML read and a dict merge, on a path a person triggers
        by tapping a control, not on a chat turn.

        Note what this does *not* do: it does not reconfigure anything
        already built. A path that needs a restart still needs one; the
        `restart_required` report stays exactly as honest as it was.
        """

        from server.runtime import _materialize_config

        config = _materialize_config()

        # The constructor guarantees this key exists and code downstream
        # indexes it without checking. A refresh has to keep that true.
        if "server" not in config:
            config["server"] = {}

        self.runtime.config = config

        return config

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

    def _reapply_companion(self, accepted: dict) -> bool:
        """
        Mutate the companion policy's settings in place. True if it landed.

        The mutation is the same shape as `_reapply_proactive`, and for the
        same reason: `PolicySettings` is a mutable dataclass the policy
        owns, and `allows` reads `self.settings.*` at decision time, so the
        next screen observation sees the new number without a rebuild.

        What is *not* the same is where it is called from. The proactive
        engine is always in `services`; the companion engine is None
        whenever the feature is off, which on a headless server is the
        normal case. So this belongs in the conditional group and has to
        answer whether it reached a live object - phase 11 part 1 lost the
        `memory.recall` toggle to exactly this, a handler in the
        unconditional group returning nothing while the assignment silently
        never ran. `applied` is a promise.

        `server.companion.enabled` is deliberately not handled here. It
        gates construction in `_build_companion`, so there may be no policy
        to mutate at all, and turning the feature on mid-flight would need
        an LLM handle this method does not have. It stays a restart.
        """

        companion = {
            key.split(".", 2)[2]: value
            for key, value in accepted.items()
            if key.startswith("server.companion.")
            and key != "server.companion.enabled"
        }

        if not companion:
            return True

        policy = getattr(
            getattr(self.runtime, "companion_engine", None),
            "policy",
            None,
        )

        settings = getattr(policy, "settings", None)

        if settings is None:
            return False

        for key, value in companion.items():
            if hasattr(settings, key):
                setattr(settings, key, value)

        return True

    def _reapply_memory(self) -> bool:
        """
        Flip `MemoryPipeline.recall_enabled` for memory.recall.

        Anything deeper - episodic store, user model, temporary context -
        is constructed once and is not hot-swappable without rebuilding
        the pipeline, which Phase 9 does not do. The gate itself is.

        Two things were wrong here and both were the same mistake made
        twice - assuming a shape instead of reading one.

        The pipeline was fetched through `services.memory`, but `Services`
        keeps `pipeline` as a *sibling* of `memory`, not a child of it
        (`launcher/services.py`: `memory` on line 31, `pipeline` on line
        36). `services.memory` is the `MemoryManager`, which has no
        `pipeline` attribute, so the `getattr` default made this a
        guaranteed `None` and the assignment never ran once in
        production.

        And it returned `None` from the unconditional group, where a
        path stays in `applied` whatever happened. So the owner turning
        recall off got `applied: ["memory.recall"]` back on a deployment
        with no pipeline at all. `applied` is a promise; this handler now
        answers the question the conditional group asks, and a deployment
        with `memory.pipeline` off is told `restart_required` instead of
        being told yes.

        The value is read from the refreshed config rather than from
        `accepted` so this matches `_reapply_temporal` exactly - one
        handler protocol in that loop, not two. `apply` calls
        `refresh_config()` before any handler runs, so the snapshot is
        the value just written.
        """

        recall = ((self.runtime.config or {}).get("memory") or {}).get(
            "recall"
        )

        if not isinstance(recall, bool):
            return False

        services = getattr(self.runtime, "services", None)

        moved = False

        # Memory 2.0: the ranked episodic search.
        pipeline = getattr(services, "pipeline", None)

        if pipeline is not None and hasattr(pipeline, "recall_enabled"):
            pipeline.recall_enabled = recall
            moved = True

        # Sprint 5: keyword search over the transcript. Swappable because
        # `MemoryKnowledgeProvider.retriever` is a plain attribute read on
        # every turn through `_recalled`, so this half needs no restart
        # either - which is what keeps `applied` from being half true.
        if self._swap_legacy_retriever(services, recall):
            moved = True

        return moved

    def _swap_legacy_retriever(self, services, recall: bool) -> bool:
        """
        Point `Services.knowledge` at the retriever `memory.recall` asks
        for, the same choice `launcher/services.py` makes at build time.

        A session is required rather than optional: handed none,
        `KeywordRetriever.__init__` calls `init_database()` and opens one
        of its own, and a settings PATCH is not the place to open a
        database. Without one the older half is left as it is and the
        pipeline half decides the return - honest, because that is
        exactly what happened.
        """

        knowledge = getattr(services, "knowledge", None)

        if knowledge is None or not hasattr(knowledge, "retriever"):
            return False

        from memory.retrieval import KeywordRetriever, NullRetriever

        if not recall:
            knowledge.retriever = NullRetriever()
            return True

        session = getattr(getattr(services, "memory", None), "session", None)

        if session is None:
            return False

        settings = (self.runtime.config or {}).get("memory") or {}

        knowledge.retriever = KeywordRetriever(
            session=session,
            skip_recent=settings.get("history_limit", 20),
        )

        return True

    def _reapply_temporal(self) -> bool:
        """
        Move the one clock this process shares to the configured zone.

        In place, on `services.clock`, because that same object is what the
        prompt's TIME section, the memory pipeline, the ranked retriever's
        captured `now`, the quiet-hours check and the proactive engine are
        all holding. Rebuilding it would move the one that got the new
        object and leave the rest on the old zone.

        False when there is no clock in this process, or when the clock
        refuses the name - `use_timezone` keeps the zone already in effect
        in that case, so the value is on disk and not in force, which is
        what `restart_required` means. The validator already refused an
        unresolvable name, so the second case needs a timezone database
        that vanished between the two calls; it is handled because
        `applied` is a promise, not because it is likely.

        One consequence worth stating rather than discovering: timestamps
        are stored naive local (`core/temporal.py`), so changing zone
        re-dates existing memories by the offset delta - a row written at
        14:00 in one zone reads as 14:00 in the next. That is a property
        of naive storage and is identical whether the change lands now or
        at the next restart, so it is not an argument for demoting this to
        `restart_required`.
        """

        clock = getattr(
            getattr(self.runtime, "services", None), "clock", None
        )

        if clock is None or not hasattr(clock, "use_timezone"):
            return False

        settings = (load_config().get("temporal")) or {}

        return bool(clock.use_timezone(settings.get("timezone")))

    def _reapply_screen(self) -> bool:
        """
        Move the observation throttle on the live vision manager.

        Returns False when there is nothing to move it on - screen
        observation off in this process means `services.vision` is either
        absent or a manager without the attribute. The caller demotes the
        path to `restart_required` in that case rather than reporting a
        change to an object that does not exist.

        `VisionManager._is_fresh` compares against `self.min_interval` on
        every observation, so the assignment takes effect on the next one.
        """

        manager = getattr(
            getattr(self.runtime, "services", None), "vision", None
        )

        if manager is None or not hasattr(manager, "min_interval"):
            return False

        settings = ((load_config().get("server") or {}).get("screen")) or {}

        try:
            manager.min_interval = float(settings.get("min_interval", 8.0))
        except (TypeError, ValueError):            # pragma: no cover
            # The validator already bounded this; a failure here would mean
            # the manager rejects assignment, which is still not applied.
            return False

        return True

    def _reapply_tools(self) -> bool:
        """
        Replace the executor's policy with one built from the new config.

        The whole policy, not one field: `ToolPolicy.from_config` is the
        single place that turns the `tools:` section into a policy, and
        duplicating its coercions here would be a second reading of the
        same config that could disagree with the first.

        The tool *registry* is untouched, which is correct - the settable
        paths are all policy fields, and the three that decide which tools
        exist (`allowed_paths`, `applications`, `commands`) are deliberately
        not settable at all. `commands` most of all: a remotely declarable
        argv with a fillable slot would be arbitrary execution reached
        through the settings API rather than through the tool boundary.
        """

        executor = getattr(
            getattr(self.runtime, "services", None), "tools", None
        )

        if executor is None or not hasattr(executor, "policy"):
            return False

        from tools.executor import ToolPolicy

        try:
            executor.policy = ToolPolicy.from_config(
                load_config().get("tools") or {}
            )
        except Exception as error:                 # pragma: no cover
            logger.warning(
                "Tool policy could not be rebuilt (%s); the running policy "
                "is unchanged", type(error).__name__,
            )
            return False

        return True

    def _reapply_voice(self) -> bool:
        """
        Move voice and volume on the live TTS provider.

        Only those two: they are passed through at each synthesis, so the
        next utterance uses them. `provider` and `playback` were decided
        when the engine was built and are reported as needing a restart.

        `volume` goes through `normalise_percent`, the same coercion
        `EdgeTTSProvider.__init__` uses. The setting is validated as 0-100
        but the provider hands its value to edge-tts, which wants "+80%" -
        assigning the bare integer would produce speech at the wrong volume
        or none at all, from a setting the UI had just called applied.

        On a headless deployment `voice.tts.enabled` is false, there is no
        engine, and this returns False - which is the honest answer, not a
        failure.
        """

        provider = getattr(
            getattr(getattr(self.runtime, "services", None), "tts", None),
            "provider",
            None,
        )

        if provider is None:
            return False

        from voice.tts.values import normalise_percent

        settings = ((load_config().get("voice") or {}).get("tts")) or {}

        moved = False

        # Set only what the provider already has. A provider without these
        # attributes is not one this can configure, and creating them would
        # be a setting that writes a field nothing reads.
        if "voice" in settings and hasattr(provider, "voice"):
            provider.voice = str(settings["voice"]).strip()
            moved = True

        if "volume" in settings and hasattr(provider, "volume"):
            provider.volume = normalise_percent(
                settings["volume"], provider.volume
            )
            moved = True

        return moved


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

    A provider name the router cannot build is refused with a clear reason
    rather than silently probed. That set is read from the router's own
    registry, not written out here: the previous literal list had to be
    edited by hand every time a provider was added, and the failure mode
    was a newly wired provider answering "unknown provider" to the Test
    button - a bug that looks exactly like a typo by the operator.
    """

    from brain.router import KEYLESS_PROVIDERS, PROVIDER_KEYS, BrainRouter
    from brain.providers.errors import (
        ProviderAuthError,
        ProviderRateLimitError,
        ProviderUnavailableError,
    )

    name = str(provider).strip().lower()

    if name not in {"mock", *PROVIDER_KEYS, *KEYLESS_PROVIDERS}:
        return {"provider": name, "ok": False, "error": "unknown provider"}

    try:
        if name == "mock":
            # `_create_provider` handles mock before `_instantiate_provider`
            # is reached, so the router knows nothing about it and would
            # answer "unknown provider" for a provider Aura demonstrably
            # can build. Not fixed by teaching the router instead: a `mock`
            # *fallback* would answer every outage with a canned reply and
            # hide it.
            from brain.providers.mock import MockProvider
            candidate = MockProvider()
        else:
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

    except ProviderAuthError:
        # Distinguished from "unreachable" because the fix is completely
        # different and the phone shows this string. Still no detail from
        # the provider's body - only the category.
        return {
            "provider": name,
            "ok": False,
            "error": "invalid api key",
            "detail": "ProviderAuthError",
            "latency_ms": int((time.time() - start) * 1000),
        }

    except ProviderRateLimitError as error:
        return {
            "provider": name,
            "ok": False,
            "error": (
                "quota exhausted"
                if getattr(error, "is_account_limit", False)
                else "rate limited"
            ),
            "detail": "ProviderRateLimitError",
            "latency_ms": int((time.time() - start) * 1000),
        }

    except ProviderUnavailableError:
        return {
            "provider": name,
            "ok": False,
            "error": "unreachable",
            "detail": "ProviderUnavailableError",
            "latency_ms": int((time.time() - start) * 1000),
        }

    except Exception as error:
        elapsed = time.time() - start
        return {
            "provider": name,
            "ok": False,
            # Deliberately not "unreachable": an unclassified failure is
            # not evidence of a network problem, and saying so sends the
            # operator after the wrong thing.
            "error": "request failed",
            "detail": type(error).__name__,
            "latency_ms": int(elapsed * 1000),
        }
