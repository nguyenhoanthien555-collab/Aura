"""
Avatar controller.

Joins the halves of the avatar and owns nothing else:

    bus  ->  AvatarStateMachine  ->  ExpressionDirector  ->  renderer
                                 ->  AnimationDirector   ->  bus

It contains no AI logic, sends nothing to the brain, and never inspects
the content of an event beyond what the state machine already derived.
Deleting this package must leave the rest of Aura working.

Everything past `set_state` is optional by absence. A renderer that
implements only the four required methods gets exactly the behaviour it
had before any of this existed; one that also has `set_expression`,
`blink` or `play` is given those too. Nothing is required, nothing
raises, and no protocol widened to add them.
"""

from core.logger import logger
from events.types import AuraState, BlinkEvent, Expression, Mood
from avatar.animation import AnimationDirector
from avatar.backends import idle_motion_for
from avatar.expression import ExpressionDirector
from avatar.renderer import AvatarRenderer, NullRenderer
from avatar.state import AvatarStateMachine


class AvatarController:

    def __init__(
        self,
        renderer: AvatarRenderer | None = None,
        machine: AvatarStateMachine | None = None,
        expressions: ExpressionDirector | None = None,
        animation: AnimationDirector | None = None,
    ):
        """
        `expressions` and `animation` default to real ones rather than to
        None: both are pure derivation over events the machine already
        sees, they publish only when given a bus, and a renderer that
        ignores them costs two dictionary lookups per state change.
        """

        self.renderer = renderer or NullRenderer()
        self.machine = machine or AvatarStateMachine()
        self.expressions = expressions or ExpressionDirector()
        self.animation = animation or AnimationDirector()

        self._unsubscribe = None
        self._unwatch = None
        self._unwatch_mood = None
        self._unwatch_expression = None
        self._unfollow = None
        self._unwatch_blink = None
        self._unanimate = None

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
        self._unwatch_mood = self.machine.on_mood_change(self._on_mood)

        self.expressions.events = self.expressions.events or bus
        self._unfollow = self.expressions.follow(self.machine)
        self._unwatch_expression = self.expressions.on_change(
            self._on_expression
        )

        self._unanimate = self.animation.attach(bus)
        self._unwatch_blink = bus.subscribe(BlinkEvent, self._on_blink)

    def detach(self) -> None:

        for release in (
            self._unsubscribe,
            self._unwatch,
            self._unwatch_mood,
            self._unwatch_expression,
            self._unfollow,
            self._unwatch_blink,
            self._unanimate,
        ):
            if release is not None:
                try:
                    release()
                except Exception:
                    pass

        self._unsubscribe = None
        self._unwatch = None
        self._unwatch_mood = None
        self._unwatch_expression = None
        self._unfollow = None
        self._unwatch_blink = None
        self._unanimate = None

    def tick(self, now: float) -> None:
        """
        Advance anything that needs a clock.

        The controller has no timer of its own - whoever owns the frame
        loop calls this. A Tk host calls it from `after`, a headless run
        never calls it and simply never blinks.
        """

        try:
            self.expressions.tick(now)
        except Exception as error:
            logger.debug("Expression tick failed: %s", error)

        try:
            self.animation.tick(now)
        except Exception as error:
            logger.debug("Animation tick failed: %s", error)

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

    @property
    def mood(self) -> Mood:
        return self.machine.mood

    @property
    def expression(self) -> Expression:
        return self.expressions.expression

    def _on_state(self, state: AuraState) -> None:

        try:
            self.renderer.set_state(state)
        except Exception as error:
            logger.debug("Avatar render failed: %s", error)

        self._play(idle_motion_for(state), loop=True)

    def _on_mood(self, mood: Mood) -> None:
        """
        Pass the mood on, if this renderer has expressions at all.

        `set_mood` is optional - see the note in avatar/renderer.py - so
        a renderer without it is skipped rather than crashed.
        """

        self._offer("set_mood", mood, what="mood")

    def _on_expression(self, expression: Expression) -> None:
        """
        Show a face, if this renderer has faces.

        Distinct from `_on_mood`: a renderer with only four sprites reads
        the mood and ignores this; a Live2D model does the opposite,
        because an expression is what it actually has a parameter for.
        """

        self._offer("set_expression", expression, what="expression")

    def _on_blink(self, event: BlinkEvent) -> None:

        self._offer(
            "blink", bool(getattr(event, "double", False)), what="blink"
        )

    def _play(self, motion: str, loop: bool = False) -> None:

        if not motion:
            return

        player = getattr(self.renderer, "play", None)

        if player is None:
            return

        try:
            player(motion, loop=loop)
        except TypeError:
            # A renderer whose play() takes only a name.
            try:
                player(motion)
            except Exception as error:
                logger.debug("Avatar motion failed: %s", error)
        except Exception as error:
            logger.debug("Avatar motion failed: %s", error)

    def _offer(self, method: str, value, what: str) -> None:
        """
        Call an optional renderer method, if it exists.

        The one place the optional-by-absence rule is implemented, so
        adding a capability is a line in `_on_*` rather than another
        getattr-and-try block.
        """

        setter = getattr(self.renderer, method, None)

        if setter is None:
            return

        try:
            setter(value)
        except Exception as error:
            logger.debug("Avatar %s render failed: %s", what, error)


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
