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
import time
from typing import Optional

from core.config import load_config
from core.logger import logger
from brain.agent_mode import is_intent_probe, read_intent
from brain.response import Response
from events.bus import EventBus
from launcher.services import build_services
from server.notifications import NotificationOutbox


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
            config: Optional config dict. If None, loads from config.yaml.
            memory: Optional MemoryManager. If None, the default
                `data/memory.db` store is opened.
        """
        self.config = config or load_config()

        # Ensure server config section exists
        if "server" not in self.config:
            self.config["server"] = {}

        # Server mode has no display. Copy the config before disabling the
        # avatar so a caller-supplied dict is not mutated, and so nothing
        # imports tkinter in a container.
        server_config = dict(self.config)
        server_config["avatar"] = {"enabled": False}

        self.screen_source = None
        self.companion_engine = None
        self.notifications = NotificationOutbox()

        # The bus is built here rather than inside `build_services` because
        # remote vision has to be constructed *before* the services that
        # consume it, and it publishes to the same bus everything else uses.
        bus = EventBus()

        vision = self._build_remote_vision(server_config, bus)

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
        if processor is None:
            logger.error("Cloud vision is not configured; remote vision remains disabled")
            return None

        manager, source = build_remote_vision(
            events=bus,
            min_interval=float(settings.get("min_interval", 8.0)),
            enabled=True,
            processor=processor,
        )

        self.screen_source = source

        logger.info(
            "Cloud screen vision enabled (min_interval=%.1fs)",
            manager.min_interval,
        )

        return manager

    def _build_companion(self, config: dict) -> None:
        """
        The unprompted-notification engine, when it is turned on.

        Given the same LLM the conversation uses - not a second client,
        a second key or a second model.
        """

        from companion.engine import build_companion_engine

        settings = ((config.get("server") or {}).get("companion")) or {}

        if not settings.get("enabled", False):
            return

        self.companion_engine = build_companion_engine(
            config,
            events=self.services.bus,
            llm=self.services.engine.conversation.llm,
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
        logger.info("%s server ready", name)

    def stop(self) -> None:
        """Stop the runtime."""
        if not self.started:
            return

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
        response = self.services.engine.chat(message, source=source, context=context)

        if is_intent_probe(context):
            # Normalised here rather than on the device, so the rule that
            # anything ambiguous is CONVERSATION has exactly one
            # implementation and a client only has to compare two
            # strings. A model that answers "Action." or explains itself
            # is read the same way whatever is asking.
            return Response(text=read_intent(response.text))

        return response

    def chat_stream(self, message: str, session_id: str = "default", source: str = "text", context: dict | None = None):
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
            context=context
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

    def health_status(self) -> dict:
        """Get health status for /api/health."""
        return {
            "status": "healthy" if self.started else "starting",
            "version": self.config.get("app", {}).get("version", "0.2.0"),
            "uptime_seconds": self.uptime,
            "runtime": {
                # active_chain() is what was actually built (e.g.
                # "gemini->groq"); the plain provider_name would only say
                # what was configured. It builds the lazy provider, so it
                # must not be called on a hot path - health is not one.
                "llm_provider": getattr(self.engine.conversation.llm, "active_chain", lambda: "unknown")(),
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
