"""
Server-mode Aura runtime.

The one place server mode differs from desktop mode: no avatar, no
terminal, and screen observations arrive from a device instead of from
this machine's display.

Everything else is the desktop's own code. `build_services` is the same
composition root `launcher/runtime.py` uses, so Brain, Memory,
Personality, Providers and the event bus are built once here and reused
by every request. A request never constructs a provider.
"""
import os
import time
from typing import Optional

from core.config import load_config
from core.logger import logger
from brain.agent_mode import is_intent_probe, read_intent
from brain.response import Response
from events.bus import EventBus
from launcher.services import build_services
from server.notifications import NotificationOutbox


def _materialize_config() -> dict:
    """
    The effective config, with the Control Hub overlay applied.

    Built in the runtime constructor and handed to `build_services` so
    that everything assembled once - vision processors, tool registry,
    voice engines - sees the user's settings from the start. Provider
    construction reads `load_config()` again on first use, which is fine:
    `load_config` applies the same overlay, and keys were applied to the
    environment by `_bootstrap_stores`.
    """

    from core.settings_store import get_runtime_settings

    return get_runtime_settings().effective(load_config())


def _bootstrap_stores() -> None:
    """
    Load the credential store and overlay before anything is built.

    `CredentialStore.__init__` reads the encrypted key file and
    `apply()` pushes stored keys into the environment - both must happen
    before the first provider is constructed. The settings overlay file
    is read the same way (its load is what makes a stored `llm.provider`
    take effect from boot). Both keep their default on-disk paths; a
    deployment that mounts `data/` persists them across restarts.
    """

    from core.credentials import get_credential_store
    from core.settings_store import get_runtime_settings

    applied = get_credential_store().apply()
    if applied:
        logger.info("Provider keys applied from credential store: %s", ", ".join(applied))

    get_runtime_settings()



class ServerRuntime:
    """
    Server-mode Aura runtime.

    Owns the assembled Aura system and its lifetime.
    Initializes once at startup, reused for all requests.
    """

    def __init__(self, config: dict | None = None, memory=None):
        """
        Initialize the server runtime.

        Args:
            config: Optional config dict. If None, loads from config.yaml
                and applies the Control Hub overlay. A caller-supplied
                config is used exactly as given - tests build one on
                purpose, and a settings file on the developer's disk must
                not leak into it.
            memory: Optional MemoryManager. If None, the default
                `data/memory.db` store is opened.
        """

        if config is None:
            # The real server path. Keys reach the environment before the
            # first provider is built, and the overlay is merged before
            # anything reads a setting.
            _bootstrap_stores()
            self.config = _materialize_config()
        else:
            self.config = config

        # Ensure server config section exists
        if "server" not in self.config:
            self.config["server"] = {}

        # Server mode has no display. Copy the config before disabling the
        # avatar so a caller-supplied dict is not mutated, and so nothing
        # imports tkinter in a container.
        server_config = dict(self.config)
        server_config["avatar"] = {"enabled": False}

        # In server mode, open-by-default tool policy for production runtime:
        # all registered tools are allowed, and per-tool consent is granted inline.
        tools_cfg = dict(server_config.get("tools") or {})
        if config is None:
            tools_cfg.setdefault("enabled", True)
            if not tools_cfg.get("allowed"):
                tools_cfg["allow_all"] = True
                tools_cfg["allowed"] = ["*"]
            tools_cfg.setdefault("auto_approve", ["safe", "sensitive", "dangerous"])
        elif tools_cfg.get("enabled", False) and not tools_cfg.get("allowed"):
            tools_cfg["allow_all"] = True
            tools_cfg["allowed"] = ["*"]
            tools_cfg.setdefault("auto_approve", ["safe", "sensitive", "dangerous"])
        server_config["tools"] = tools_cfg

        self.screen_source = None
        self.companion_engine = None
        self.daemon = None
        self.notifications = NotificationOutbox()

        # The bus is built here rather than inside `build_services` because
        # remote vision has to be constructed *before* the services that
        # consume it, and it publishes to the same bus everything else uses.
        bus = EventBus()

        vision = self._build_remote_vision(server_config, bus)
        if vision is None:
            from launcher.services import _build_vision
            vision = _build_vision(server_config, bus)

        self.services = build_services(
            server_config,
            bus=bus,
            memory=memory,
            vision=vision,
        )

        self._build_companion(server_config)

        self.notifications.attach(self.services.bus)

        self.started = False
        self.start_time: Optional[float] = None
        self._settings_service = None
        self._reflection_worker = None

    @property
    def settings_store(self):
        """
        The runtime settings overlay this process writes through.

        A property rather than a constructor field: the overlay is a
        process-wide singleton (like the credential store), and a runtime
        built by a test with an explicit config still needs to reach
        whichever overlay that test installed.
        """

        from core.settings_store import get_runtime_settings

        return get_runtime_settings()

    @property
    def settings_service(self):
        """The settings applier, built on demand."""

        from server.settings_service import SettingsService

        if self._settings_service is None:
            self._settings_service = SettingsService(self)

        return self._settings_service

    # ------------------------------------------------------------------
    # Server-only wiring
    # ------------------------------------------------------------------

    def _screen_settings(self, config: dict) -> dict:
        return ((config.get("server") or {}).get("screen")) or {}

    def _build_remote_vision(self, config: dict, bus):
        """
        Vision fed by a device, when screen observation is enabled.

        Returns None to leave `build_services` to build vision from the
        `vision:` section - which on a headless server is normally off.
        """

        settings = self._screen_settings(config)

        if not settings.get("enabled", False):
            return None

        from vision.cloud_processor import build_cloud_vision_processor
        from vision.remote import build_remote_vision

        processor = build_cloud_vision_processor(config)

        manager, source = build_remote_vision(
            events=bus,
            min_interval=float(settings.get("min_interval", 8.0)),
            enabled=True,
            processor=processor,
        )

        self.screen_source = source

        logger.info(
            "Screen vision enabled (min_interval=%.1fs, processor=%s)",
            manager.min_interval,
            "cloud" if processor else "remote_text",
        )

        return manager

    def _build_companion(self, config: dict) -> None:
        """
        The unprompted-notification engine, when it is turned on.

        Given the same LLM the conversation uses - not a second client,
        a second key or a second model.
        """

        from companion.engine import build_companion_engine
        from companion.policy import LEDGER_PATH
        from proactive.ledger import SendLedger

        settings = ((config.get("server") or {}).get("companion")) or {}

        if not settings.get("enabled", False):
            return

        # The two things that make the gate outlive the process. The ledger
        # is what stops the hourly ceiling and the duplicate window from
        # resetting to "never notified" on every restart (section 20), and
        # its own file rather than the proactive one - one shared file would
        # make each gate count the other's sends. `last_said_at` is the
        # messages table answering "is the owner here", which is a fact Aura
        # already records and used to keep a private, volatile second copy
        # of (section 21).
        self.companion_engine = build_companion_engine(
            config,
            events=self.services.bus,
            llm=self.services.engine.conversation.llm,
            ledger=SendLedger(LEDGER_PATH),
            last_user_message=self.services.memory.last_said_at,
        )

        logger.info(
            "Companion notifications enabled (threshold=%.2f, cooldown=%.0fs)",
            self.companion_engine.policy.settings.relevance_threshold,
            self.companion_engine.policy.settings.cooldown_seconds,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the runtime."""
        if self.started:
            return

        self.started = True
        self.start_time = time.time()

        name = (self.config.get("app") or {}).get("name", "Aura")

        logger.info("%s server starting", name)
        logger.info(self.services.summary())

        # Phase 5B: Startup Recovery for Durable Tasks
        try:
            from agent.task_runtime import TaskRuntime
            from server.routes.agent import get_device_registry
            from tools.base import ToolRisk
            from tools.executor import ToolExecutor, ToolPolicy

            task_runtime = TaskRuntime(bus=self.services.bus)
            registry = get_device_registry()
            executor = ToolExecutor(
                registry=registry,
                policy=ToolPolicy(
                    enabled=True,
                    allowed=frozenset(registry.names()),
                    auto_approve=frozenset({
                        ToolRisk.SAFE, ToolRisk.SENSITIVE, ToolRisk.DANGEROUS,
                    }),
                ),
            )
            resumed = task_runtime.resume_all_active(executor)
            if resumed:
                logger.info(
                    "Phase 5B Startup Recovery: processed %d active durable tasks",
                    len(resumed),
                )
        except Exception as recovery_err:
            logger.warning("Phase 5B Startup Recovery warning: %s", recovery_err)

        # Pillar 3: AURA 24/7 Autonomous Daemon
        try:
            daemon_cfg = (self.config.get("server") or {}).get("daemon") or self.config.get("daemon") or {}
            if daemon_cfg.get("enabled", False):
                from daemon.supervisor import AuraDaemon
                poll_interval = float(daemon_cfg.get("poll_interval", 1.0))
                proactive_cfg = self.config.get("proactive") or {}
                proactive_interval = float(proactive_cfg.get("check_interval_seconds", 60.0))
                backup_cfg = (self.config.get("server") or {}).get("backup") or self.config.get("backup") or {}
                backup_interval = float(backup_cfg.get("interval_seconds", 86400.0))
                pruning_cfg = (self.config.get("server") or {}).get("pruning") or self.config.get("pruning") or {}
                prune_interval = float(pruning_cfg.get("interval_seconds", 3600.0))

                self.daemon = AuraDaemon(
                    task_runtime=task_runtime if "task_runtime" in locals() else None,
                    tool_registry=registry if "registry" in locals() else None,
                    proactive_engine=getattr(self.services, "proactive", None),
                    notifications_outbox=self.notifications,
                    offline=False,
                    poll_interval=poll_interval,
                    proactive_interval=proactive_interval,
                    backup_interval=backup_interval,
                    prune_interval=prune_interval,
                )
                self.daemon.start()
                logger.info("AURA 24/7 Daemon started in ServerRuntime (poll=%.1fs, proactive=%.1fs, backup=%.1fs, prune=%.1fs)", poll_interval, proactive_interval, backup_interval, prune_interval)
        except Exception as daemon_err:
            logger.warning("AURA 24/7 Daemon startup warning: %s", daemon_err)

        logger.info("%s server ready", name)

    def stop(self) -> None:
        """Stop the runtime."""
        if not self.started:
            return

        if self.daemon is not None:
            try:
                self.daemon.stop()
            except Exception as e:
                logger.warning("AURA 24/7 Daemon shutdown warning: %s", e)
            self.daemon = None

        # Plugins first
        if self.services.plugins is not None:
            self.services.plugins.shutdown()

        if self.services.stt is not None:
            self.services.stt.stop()

        logger.info("Aura server stopped")
        self.started = False

    @property
    def uptime(self) -> float:
        """Server uptime in seconds."""
        if self.start_time is None:
            return 0.0
        return time.time() - self.start_time

    @property
    def engine(self):
        """Chat engine for conversation."""
        return self.services.engine

    @property
    def bus(self):
        """Event bus."""
        return self.services.bus

    @property
    def memory(self):
        """Memory manager."""
        return self.services.memory

    @property
    def vision(self):
        """Vision manager."""
        return self.services.vision

    @property
    def screen_enabled(self) -> bool:
        """True when a device may push screen observations."""
        return self.screen_source is not None

    # ------------------------------------------------------------------
    # Work
    # ------------------------------------------------------------------

    def chat(self, message: str, session_id: str = "default", source: str = "text", context: dict | None = None):
        """
        Process a chat message.

        Args:
            message: User message
            session_id: Session identifier. Scopes nothing in memory - see
                the single-tenant note below.
            source: Message source ("text" or "voice")
            context: Optional context dictionary

        Returns:
            Response object with .text
        """

        # The user is in the conversation right now, so nothing
        # unprompted should fire on top of it for a while. Both engines
        # hear about it: the screen-observation companion and the
        # proactive engine share one conversation, so they must share one
        # "the user just said something" signal.
        if self.companion_engine is not None:
            self.companion_engine.note_chat()

        if self.services.proactive is not None:
            self.services.proactive.note_chat()

        # Single tenant, deliberately (AURA-P1-005). `session_id` groups
        # requests for the session metadata endpoint; it does NOT partition
        # memory. Every session reads and writes one transcript, one
        # profile and one companion store.
        #
        # So the auth token is the identity boundary, and the only one.
        # Two people sharing the token share Aura's memory: each can see
        # the other's history through the prompt. That is the intended
        # deployment - one person, one Aura - and it is enforced by the
        # token being required at startup (AURA-P1-008).
        #
        # Partitioning this later means scoping MemoryManager, ProfileStore
        # and CompanionMemory by tenant, not just passing session_id down;
        # `tests/test_server.py` pins the current shared behaviour so the
        # change cannot happen silently.
        response = self.services.engine.chat(
            message,
            source=source,
            context=context,
            session_id=session_id,
        )

        if is_intent_probe(context):
            # Normalised here rather than on the device, so the rule that
            # anything ambiguous is CONVERSATION has exactly one
            # implementation and a client only has to compare two
            # strings. A model that answers "Action." or explains itself
            # is read the same way whatever is asking.
            return Response(text=read_intent(response.text))

        self._reflect_turn(message, getattr(response, "text", str(response)))

        return response

    def _reflect_turn(self, user_msg: str, assistant_reply: str):
        if not user_msg or not assistant_reply or os.environ.get("PYTEST_CURRENT_TEST"):
            return
        try:
            if self._reflection_worker is None:
                from memory.reflection import EpisodicReflectionWorker
                self._reflection_worker = EpisodicReflectionWorker()
            self._reflection_worker.reflect_turn(user_msg, assistant_reply)
        except Exception as err:
            logger.debug("Background reflection error: %s", err)

    def chat_stream(
        self,
        message: str,
        session_id: str = "default",
        source: str = "text",
        context: dict | None = None,
        offer_tools: bool = True,
    ):
        """
        Process a chat message with streaming.

        Yields text fragments.
        """

        if self.companion_engine is not None:
            self.companion_engine.note_chat()

        if self.services.proactive is not None:
            self.services.proactive.note_chat()

        return self.services.engine.conversation.chat_stream(
            message,
            contexts=None,
            source=source,
            context=context,
            session_id=session_id,
            offer_tools=offer_tools,
        )


    def consider_proactive(self) -> dict | None:
        """
        Let the proactive engine consider speaking. Usually it won't.

        Called from the notification poll, which is the only thing in this
        deployment that runs on a schedule - and it is the *device's*
        schedule, not the server's. The consequence is stated plainly in
        `proactive/engine.py` and repeated here because it is the kind of
        thing that gets forgotten: while nothing is polling, Aura cannot
        consider speaking at all. A message that would have been sent at
        03:00 to a phone that is not polling is never sent, not sent late.

        Safe to call on every poll. Every call runs the full decision and
        policy path, so a client polling every five seconds sees exactly
        what a client polling every five minutes sees.

        Returns the decision as a dict for the response, or None when
        proactive messaging is not wired up at all. A failure here must
        not fail the poll: a device collecting its notifications is doing
        something useful even if the decision path is broken.
        """

        if self.services.proactive is None:
            return None

        try:
            return self.services.proactive.tick().as_dict()
        except Exception as error:
            logger.warning("Proactive tick failed: %s", error)
            return None

    def observe_screen(self, observation) -> dict:
        """
        Record a screen observation and decide whether to say anything.

        Returns a JSON-safe summary: what was accepted, and the companion
        decision that followed - including the reason it stayed quiet,
        which is the only way to tune this from the outside.
        """

        if self.screen_source is None:
            return {
                "accepted": False,
                "reason": "screen observation is disabled",
                "decision": None,
            }

        accepted = self.screen_source.submit(observation)

        if accepted is None:
            return {
                "accepted": False,
                "reason": "observation was empty",
                "decision": None,
            }

        # Let the vision manager notice the new screen, so the next turn
        # of conversation already has it. Throttling and the "only when
        # it changed" event both live in there.
        if self.vision is not None:
            try:
                self.vision.get_context()
            except Exception as error:
                logger.debug("Vision refresh after screen push failed: %s", error)

        if self.companion_engine is None:
            return {
                "accepted": True,
                "reason": "recorded",
                "decision": None,
            }

        decision = self.companion_engine.observe(observation)

        return {
            "accepted": True,
            "reason": "recorded",
            "decision": decision.as_dict(),
        }

    def interrupt(self, reason: str = "User requested emergency stop") -> dict:
        """
        Emergency action interruption (barge-in mechanism).
        Immediately halts running tasks, active agent loops, and pending device actions.
        """
        logger.warning("Emergency interruption triggered: %s", reason)

        # 1. Device Gateway pending invocations
        cancelled_devices = 0
        try:
            from server.device_gateway import get_device_gateway
            cancelled_devices = get_device_gateway().cancel_all(reason=reason)
        except Exception as e:
            logger.warning("Failed to cancel device gateway invocations: %s", e)

        # 2. Durable Tasks
        cancelled_tasks = []
        try:
            from server.routes.agent import get_task_runtime
            task_rt = get_task_runtime()
            if hasattr(task_rt, "cancel_all_active_tasks"):
                cancelled_tasks = task_rt.cancel_all_active_tasks(reason=reason)
            else:
                for t in task_rt.list_active_tasks():
                    if task_rt.cancel_task(t.task_id, reason=reason):
                        cancelled_tasks.append(t.task_id)
        except Exception as e:
            logger.warning("Failed to cancel durable tasks: %s", e)

        # 3. Agent Runs
        cancelled_runs = []
        try:
            from server.routes.agent import get_agent_runtime
            agent_rt = get_agent_runtime()
            if hasattr(agent_rt, "cancel_all_active_runs"):
                cancelled_runs = agent_rt.cancel_all_active_runs()
        except Exception as e:
            logger.warning("Failed to cancel agent runs: %s", e)

        # 4. Bus notification
        try:
            if self.bus is not None:
                from events.types import AgentInterruptedEvent
                evt = AgentInterruptedEvent(
                    reason=reason,
                    cancelled_tasks=tuple(cancelled_tasks),
                    cancelled_runs=tuple(cancelled_runs),
                    cancelled_devices=cancelled_devices,
                    timestamp=time.time(),
                )
                self.bus.publish(evt)
        except Exception as e:
            logger.warning("Failed to publish interrupt event: %s", e)

        return {
            "interrupted": True,
            "cancelled_tasks": cancelled_tasks,
            "cancelled_runs": cancelled_runs,
            "cancelled_device_invocations": cancelled_devices,
            "message": "Em đã dừng lại ngay lập tức theo lệnh của anh rồi!",
        }

    def readiness(self) -> dict:
        """
        Whether this process can actually serve a chat turn.

        Liveness (the root route) answers "is the HTTP server up". That is
        what the container healthcheck used to ask, and a process whose
        provider chain never initialized answers it perfectly while being
        unable to do the one thing it exists for.

        Readiness is deliberately narrow. It reports the two things a chat
        turn cannot proceed without - a started runtime and a provider
        object to call - and nothing about optional collaborators, because
        an unready-because-TTS-is-off server would be restarted forever for
        no reason. Vision, voice, screen and companion are all optional by
        design, and `/api/health` already reports them.

        It does NOT call the provider. A readiness probe that makes a
        network request per poll bills the operator for being observed, and
        turns one provider outage into a restart loop.
        """

        problems: list[str] = []

        if not self.started:
            problems.append("runtime has not finished starting")

        chain = "unknown"

        try:
            llm = self.engine.conversation.llm
        except Exception:
            llm = None
            problems.append("conversation has no language model")

        if llm is not None:
            try:
                # Builds the lazy provider - the same construction a real
                # turn would do, which is exactly the failure worth
                # catching here (a missing key raises in __init__).
                chain = getattr(llm, "active_chain", lambda: "unknown")()
            except Exception as error:
                problems.append(
                    f"provider chain unavailable ({type(error).__name__})"
                )

        return {
            "ready": not problems,
            "llm_provider": chain,
            "problems": problems,
        }

    def _provider_chain_label(self) -> str:
        """
        What the provider chain actually is, or why it cannot be named.

        `active_chain()` reports what was *built* ("gemini->groq") where
        `provider_name` would only report what was configured, so it is
        worth the call. But it builds the lazy provider, and construction
        raises when the key is missing or invalid - which is precisely the
        state a user opens Settings to repair.

        A bare call here made `/api/health` return 500 in that state, and
        `/api/health` is the one request the Android app treats as proof
        the server is reachable and the token was accepted. So a dead
        provider presented as an unreachable server: the phone sent the
        user to the connection screen to retype a working token, while the
        screen that could actually fix it - AI & Models - was locked
        behind the same false verdict.

        The provider is one subsystem among eight in this report, and the
        others are already reported as strings rather than as exceptions.
        `readiness_status` above catches the same call for the same
        reason; this is that guard, applied to the endpoint that needed it
        more. `/api/providers/health` remains the place that explains
        *which* provider is unhappy.
        """

        try:
            llm = self.engine.conversation.llm
        except Exception:
            return "unavailable"

        try:
            return getattr(llm, "active_chain", lambda: "unknown")()
        except Exception as error:
            # The type name only - a provider's exception message can
            # carry the key it was rejected for.
            return f"unavailable ({type(error).__name__})"

    def _tools_label(self) -> str:
        """
        What the executor would currently run, as one string.

        Registered is not the honest number: the catalogue filters through
        `policy.allowed`, so a tool the owner has not named cannot run no
        matter how registered it is. That filtered count is the one an
        operator can act on.
        """

        tools = self.services.tools

        if tools is None or not tools.policy.enabled:
            return "disabled"

        return f"{len(tools.available())} available"

    def _plugins_label(self) -> str:
        """
        The plugin set, naming what failed.

        A bare count hides exactly the case an operator needs: a plugin
        enabled in config whose `initialize` raised. `PluginManager.status`
        already knows which names are broken; this is where that knowledge
        reaches the outside world - before this label existed, nothing in
        production ever called it.
        """

        plugins = self.services.plugins

        if plugins is None:
            return "off"

        label = plugins.summary()

        broken = sorted(
            name for name, state in plugins.status().items() if state == "broken"
        )

        if broken:
            label += f" ({', '.join(broken)} failed to initialize)"

        return label

    def health_status(self) -> dict:
        """Get health status for /api/health."""
        return {
            "status": "healthy" if self.started else "starting",
            "version": self.config.get("app", {}).get("version", "0.2.0"),
            "uptime_seconds": self.uptime,
            "runtime": {
                "llm_provider": self._provider_chain_label(),
                "memory": "connected" if self.memory else "unavailable",
                "vision": "enabled" if self.vision and self.vision.enabled else "disabled",
                "voice_output": "enabled" if self.services.tts else "disabled",
                "voice_input": "enabled" if self.services.stt else "disabled",
                "screen": "enabled" if self.screen_enabled else "disabled",
                "companion": (
                    "enabled" if self.companion_engine is not None else "disabled"
                ),
                "proactive": (
                    "enabled"
                    if (
                        self.services.proactive is not None
                        and self.services.proactive.policy.settings.enabled
                    )
                    else "disabled"
                ),
                "tools": self._tools_label(),
                "plugins": self._plugins_label(),
            }
        }


# Global runtime instance (initialized on startup)
_runtime: Optional[ServerRuntime] = None


def get_runtime() -> ServerRuntime:
    """Get the global runtime instance."""
    global _runtime
    if _runtime is None:
        _runtime = ServerRuntime()
        _runtime.start()
    return _runtime


def is_initialized() -> bool:
    """True once a runtime has been built (by startup or by a test)."""
    return _runtime is not None


def init_runtime(config: dict | None = None, memory=None) -> ServerRuntime:
    """Initialize the global runtime (call at startup)."""
    global _runtime
    _runtime = ServerRuntime(config, memory=memory)
    _runtime.start()
    return _runtime


def shutdown_runtime() -> None:
    """Shutdown the global runtime."""
    global _runtime
    if _runtime is not None:
        _runtime.stop()
        _runtime = None
