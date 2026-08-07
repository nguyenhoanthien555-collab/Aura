"""
Aura runtime.

Owns the assembled system and its lifetime. It is the object a UI holds:
a CLI, a hotkey daemon or a future tray application all drive Aura
through this and never reach into the subsystems directly.

Threading, stated once because it is the only subtle part:

    Tk must run on the thread that created it, and it wants the main
    thread. So `run()` puts the interactive loop on a worker and gives
    the main thread to the avatar. With no GUI present the worker runs
    on the main thread instead and nothing is spawned.
"""

import threading
from typing import Callable

from core.logger import logger
from events.types import AuraState
from launcher.services import Services, build_services


class AuraRuntime:

    def __init__(
        self,
        config: dict | None = None,
        services: Services | None = None,
    ):

        self.services = services or build_services(config)

        self.started = False
        self._stopping = False

    # ------------------------------------------------------------------
    # Shorthands
    # ------------------------------------------------------------------

    @property
    def bus(self):
        return self.services.bus

    @property
    def config(self) -> dict:
        return self.services.config

    @property
    def state(self) -> AuraState:

        avatar = self.services.avatar

        return avatar.state if avatar else AuraState.IDLE

    @property
    def has_gui(self) -> bool:
        """Whether the avatar needs the main thread."""

        avatar = self.services.avatar

        if avatar is None:
            return False

        return hasattr(avatar.renderer, "run")

    # ------------------------------------------------------------------
    # Lifetime
    # ------------------------------------------------------------------

    def start(self) -> None:

        if self.started:
            return

        self.started = True

        name = (self.config.get("app") or {}).get("name", "Aura")

        logger.info("%s starting", name)
        logger.info(self.services.summary())

        if self.services.avatar is not None:
            self.services.avatar.start()

    def stop(self) -> None:

        if self._stopping:
            return

        self._stopping = True

        # Plugins first: one may hold a subscription to something below,
        # and an event delivered to a half torn down plugin is a crash
        # nobody asked for.
        if self.services.plugins is not None:
            self.services.plugins.shutdown()

        if self.services.avatar is not None:
            self.services.avatar.stop()

        if self.services.stt is not None:
            self.services.stt.stop()

        logger.info("Aura stopped")

    def run(self, interactive: Callable[[], None]) -> None:
        """
        Run until `interactive` returns.

        With an avatar on screen the callable goes to a daemon thread and
        the main thread blocks in the GUI loop; closing the avatar ends
        the process either way.
        """

        self.start()

        if not self.has_gui:
            try:
                interactive()
            finally:
                self.stop()
            return

        worker = threading.Thread(
            target=self._guarded(interactive),
            name="aura-interactive",
            daemon=True,
        )

        worker.start()

        try:
            self.services.avatar.run()
        finally:
            self.stop()

    def _guarded(self, target: Callable[[], None]) -> Callable[[], None]:
        """
        Wrap the interactive loop so it always tears the GUI down.

        Without this, an exception on the worker leaves a floating
        avatar attached to a session that no longer exists.
        """

        def run() -> None:

            try:
                target()

            except Exception as error:
                logger.exception("Interactive session failed: %s", error)

            finally:
                avatar = self.services.avatar

                if avatar is not None:
                    try:
                        avatar.renderer.close()
                    except Exception:
                        pass

        return run

    # ------------------------------------------------------------------
    # Conversation
    # ------------------------------------------------------------------

    def chat(self, message: str, source: str = "text"):
        """One turn. Raises if the provider does."""

        return self.services.engine.chat(message, source=source)

    def listen(self) -> str:
        """
        Record one utterance and return the transcript.

        Returns "" when voice input is off or nothing was heard. The
        caller decides whether to feed it to `chat` - the microphone
        never talks to the brain on its own.
        """

        stt = self.services.stt

        if stt is None:
            return ""

        return stt.listen_once()

    def voice_turn(self) -> tuple[str, object | None]:
        """Listen, then answer. Returns (heard, response)."""

        heard = self.listen()

        if not heard:
            return "", None

        return heard, self.chat(heard, source="voice")

    def speak(self, text: str) -> bool:

        tts = self.services.tts

        if tts is None:
            return False

        return tts.speak(text)

    # ------------------------------------------------------------------
    # Memory
    # ------------------------------------------------------------------

    def remember(self, key: str, value: str) -> bool:

        profile = self.services.profile

        if profile is None:
            return False

        return profile.remember(key, value) is not None

    def forget(self, key: str) -> bool:

        profile = self.services.profile

        return bool(profile and profile.forget(key))

    def profile_lines(self) -> list[str]:

        profile = self.services.profile

        return profile.render() if profile else []

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    def run_tool(self, name: str, arguments: dict | None = None):

        executor = self.services.tools

        if executor is None:
            from tools.base import fail

            return fail("tools are not configured", tool=name)

        return executor.execute(name, arguments)

    def set_tool_confirmation(self, confirm) -> None:
        """
        Install the handler that approves risky tool calls.

        Set by whatever owns the user's attention. Until something does,
        the executor's default refusal stands.
        """

        if self.services.tools is not None:
            self.services.tools.confirm = confirm

    # ------------------------------------------------------------------
    # Plugins
    # ------------------------------------------------------------------

    def plugin_status(self) -> dict[str, str]:
        """
        What each discovered plugin is doing: enabled, disabled or broken.

        Read only. Enabling a plugin at runtime is deliberately not
        offered - a plugin registers tools and subscribes to events during
        `initialize`, and doing that half way through a conversation means
        a capability appearing between one turn and the next.
        """

        plugins = self.services.plugins

        return plugins.status() if plugins is not None else {}
