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

    def chat(self, message: str, session_id: str = "default", source: str = "text"):
        """
        Process a chat message.

        Args:
            message: User message
            session_id: Session identifier (for future per-session memory)
            source: Message source ("text" or "voice")

        Returns:
            Response object with .text
        """

        # The user is in the conversation right now, so nothing
        # unprompted should fire on top of it for a while.
        if self.companion_engine is not None:
            self.companion_engine.note_chat()

        # For now, all sessions share the same memory
        # Future: implement per-session memory isolation
        return self.services.engine.chat(message, source=source)

    def chat_stream(self, message: str, session_id: str = "default", source: str = "text"):
        """
        Process a chat message with streaming.

        Yields text fragments.
        """

        if self.companion_engine is not None:
            self.companion_engine.note_chat()

        return self.services.engine.conversation.chat_stream(
            message,
            contexts=None,
            source=source
        )

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

    def health_status(self) -> dict:
        """Get health status for /api/health."""
        return {
            "status": "healthy" if self.started else "starting",
            "version": self.config.get("app", {}).get("version", "0.2.0"),
            "uptime_seconds": self.uptime,
            "runtime": {
                "llm_provider": getattr(self.engine.conversation.llm, "provider_name", "unknown"),
                "memory": "connected" if self.memory else "unavailable",
                "vision": "enabled" if self.vision and self.vision.enabled else "disabled",
                "voice_output": "enabled" if self.services.tts else "disabled",
                "voice_input": "enabled" if self.services.stt else "disabled",
                "screen": "enabled" if self.screen_enabled else "disabled",
                "companion": (
                    "enabled" if self.companion_engine is not None else "disabled"
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
