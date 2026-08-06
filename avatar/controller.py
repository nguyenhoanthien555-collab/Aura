"""
Avatar controller.

Joins the three halves of the avatar and owns nothing else:

    bus  ->  AvatarStateMachine  ->  renderer

It contains no AI logic, sends nothing to the brain, and never inspects
the content of an event beyond what the state machine already derived.
Deleting this package must leave the rest of Aura working.
"""

from core.logger import logger
from events.types import AuraState
from avatar.renderer import AvatarRenderer, NullRenderer
from avatar.state import AvatarStateMachine


class AvatarController:

    def __init__(
        self,
        renderer: AvatarRenderer | None = None,
        machine: AvatarStateMachine | None = None,
    ):

        self.renderer = renderer or NullRenderer()
        self.machine = machine or AvatarStateMachine()

        self._unsubscribe = None
        self._unwatch = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def attach(self, bus) -> None:
        """
        Follow a bus.

        Two hops on purpose: the machine listens to the bus, the renderer
        listens to the machine. The renderer therefore never sees an
        event type and cannot start reacting to conversation content.
        """

        self._unsubscribe = self.machine.attach(bus)
        self._unwatch = self.machine.on_change(self._on_state)

    def detach(self) -> None:

        for release in (self._unsubscribe, self._unwatch):
            if release is not None:
                try:
                    release()
                except Exception:
                    pass

        self._unsubscribe = None
        self._unwatch = None

    def start(self) -> None:

        self._on_state(self.machine.state)

        try:
            self.renderer.show()
        except Exception as error:
            logger.debug("Avatar show failed: %s", error)

    def stop(self) -> None:

        self.detach()

        try:
            self.renderer.close()
        except Exception as error:
            logger.debug("Avatar close failed: %s", error)

    def run(self) -> None:
        """
        Hand the current thread to the renderer, if it wants one.

        A Tk window blocks here until it closes. The null renderer
        returns immediately, so a headless run needs no special case.
        """

        loop = getattr(self.renderer, "run", None)

        if loop is None:
            return

        loop()

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    @property
    def state(self) -> AuraState:
        return self.machine.state

    def _on_state(self, state: AuraState) -> None:

        try:
            self.renderer.set_state(state)
        except Exception as error:
            logger.debug("Avatar render failed: %s", error)


# ----------------------------------------------------------------------
# Construction
# ----------------------------------------------------------------------

def create_renderer(
    enabled: bool = True,
    options: dict | None = None,
) -> AvatarRenderer:
    """
    Build the best renderer this machine can show.

    Falls back to NullRenderer when the avatar is disabled or no display
    exists, so `launcher.py` behaves identically over SSH and on a
    desktop - one has a face, the other does not.
    """

    options = options or {}

    if not enabled:
        return NullRenderer()

    from avatar.window import is_display_available

    if not is_display_available():
        logger.info("No display available, avatar disabled")
        return NullRenderer()

    try:
        from avatar.window import TkAvatarWindow

        position = options.get("position")

        return TkAvatarWindow(
            size=options.get("size", 160),
            scale=options.get("scale", 1.0),
            alpha=options.get("opacity", 0.95),
            position=tuple(position) if position else None,
            sprites_dir=options.get("sprites_dir") or None,
        )

    except Exception as error:
        logger.warning("Avatar window could not be created: %s", error)
        return NullRenderer()
