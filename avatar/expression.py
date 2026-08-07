"""
Expressions.

The third axis of what Aura is showing:

    AuraState   what she is doing      speaking
    Mood        how she feels          teasing
    Expression  what her face shows    playful

State and mood are durable. An expression is not: SURPRISED is a face
held for a moment and then released, not a state of being. That
difference is the whole design here.

    derived     from (state, mood), held until either of them moves
    flashed     set explicitly, held for a few seconds, then released

`ExpressionDirector` owns both, so there is exactly one answer to "what
is her face doing" at any moment, the same way AvatarStateMachine is the
one answer for state.

Nothing in brain/ imports this and nothing here imports brain/. The
director follows a state machine, and a state machine follows the bus, so
the conversation layer publishes facts and never learns that a face
exists.

There is no timer inside. `tick(now)` releases expired flashes and the
caller decides when to call it - a Tk `after` loop, a renderer frame, a
test passing 0.0 and then 99.0. That keeps the whole module runnable with
no clock, no thread and no display.
"""

from typing import Callable, Protocol, runtime_checkable

from events.types import (
    AuraState,
    Expression,
    ExpressionChangedEvent,
    Mood,
)


# How long a flashed expression lasts when the caller does not say.
DEFAULT_HOLD = 2.0


@runtime_checkable
class ExpressionPolicy(Protocol):
    """
    Decides which face goes with a state and a mood.

    Swappable on purpose: a character with a different temperament is a
    different policy, not a different avatar system. A VTuber preset that
    wants FOCUSED whenever she is speaking implements this in six lines
    and gets injected.
    """

    def expression_for(self, state: AuraState, mood: Mood) -> Expression:
        ...


# ----------------------------------------------------------------------
# The default policy
# ----------------------------------------------------------------------

# What each mood looks like when nothing else is happening.
#
# CURIOUS has no expression of its own - a curious face and a thinking
# face are the same head tilt - so it borrows THINKING. Mapping it to
# CONFUSED instead would be wrong: confusion is not knowing, curiosity is
# wanting to know, and they should not animate the same way.
MOOD_FACES: dict[Mood, Expression] = {
    Mood.NEUTRAL: Expression.NEUTRAL,
    Mood.HAPPY: Expression.HAPPY,
    Mood.CURIOUS: Expression.THINKING,
    Mood.FOCUSED: Expression.FOCUSED,
    Mood.TEASING: Expression.TEASING,
    Mood.SLEEPY: Expression.SLEEPY,
}

# States that override the mood face while they last.
#
# Only THINKING does. Speaking and listening keep whatever the mood
# implies, because a happy Aura talking should look happy - replacing her
# face with a generic "talking" expression is how a character stops
# reading as a character.
STATE_FACES: dict[AuraState, Expression] = {
    AuraState.THINKING: Expression.THINKING,
}


class DefaultExpressionPolicy:
    """
    Aura's temperament, as a lookup.

    Mood wins except while she is thinking, and even then a mood that
    already implies concentration keeps its own face - a FOCUSED Aura
    working through something looks focused, not quizzical.
    """

    #: Moods whose face survives the THINKING state.
    KEEPS_FACE_WHILE_THINKING = frozenset({Mood.FOCUSED, Mood.SLEEPY})

    def expression_for(self, state: AuraState, mood: Mood) -> Expression:

        if not isinstance(mood, Mood):
            mood = Mood.NEUTRAL

        face = MOOD_FACES.get(mood, Expression.NEUTRAL)

        if state in STATE_FACES and mood not in self.KEEPS_FACE_WHILE_THINKING:
            return STATE_FACES[state]

        return face


# ----------------------------------------------------------------------
# The director
# ----------------------------------------------------------------------

Listener = Callable[[Expression], None]


class ExpressionDirector:
    """
    Single owner of Aura's current expression.

    Follows a state machine for the derived face and accepts explicit
    flashes for momentary ones. Announces every real change once, on the
    bus and to its own listeners.
    """

    def __init__(
        self,
        policy: ExpressionPolicy | None = None,
        events=None,
        publish: bool = True,
    ):

        self.policy = policy or DefaultExpressionPolicy()
        self.events = events
        self.publish = publish

        self.expression = Expression.NEUTRAL

        self._state = AuraState.IDLE
        self._mood = Mood.NEUTRAL

        # A flashed expression and the moment it expires. `_until` is
        # None whenever the current face is derived rather than flashed.
        self._flashed: Expression | None = None
        self._until: float | None = None

        self._listeners: list[Listener] = []

    # ------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------

    def follow(self, machine) -> Callable[[], None]:
        """
        Track an AvatarStateMachine.

        Two hops rather than subscribing to the bus directly: the machine
        already turns events into (state, mood), and duplicating that
        derivation would give two answers that could disagree.

        Returns a callable that stops following.
        """

        self.events = self.events or getattr(machine, "events", None)

        release_state = machine.on_change(self.set_state)
        release_mood = machine.on_mood_change(self.set_mood)

        self.set_state(machine.state)
        self.set_mood(machine.mood)

        def unfollow() -> None:
            for release in (release_state, release_mood):
                try:
                    release()
                except Exception:
                    pass

        return unfollow

    def on_change(self, listener: Listener) -> Callable[[], None]:
        """Call `listener` whenever the expression actually changes."""

        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    # ------------------------------------------------------------------
    # Inputs
    # ------------------------------------------------------------------

    def set_state(self, state: AuraState) -> None:

        if not isinstance(state, AuraState):
            return

        self._state = state
        self._settle(reason=f"state:{state.value}")

    def set_mood(self, mood: Mood) -> None:

        if not isinstance(mood, Mood):
            return

        self._mood = mood
        self._settle(reason=f"mood:{mood.value}")

    def flash(
        self,
        expression: Expression,
        hold: float = DEFAULT_HOLD,
        reason: str = "",
        now: float = 0.0,
    ) -> bool:
        """
        Show a momentary expression.

        This is how EXCITED, SURPRISED, CONFUSED and EMBARRASSED are
        reached - no mood produces them, because none of them is a way to
        feel for ten minutes. Something that knows the moment calls this:
        a plugin celebrating a green test run, a command, a future
        reaction system.

        `now` is the caller's clock. Pass the same source to `tick`.
        Returns True when the face actually changed.
        """

        if not isinstance(expression, Expression):
            return False

        self._flashed = expression
        self._until = now + max(0.0, hold) if hold else None

        return self._show(expression, hold=hold, reason=reason or "flash")

    def tick(self, now: float) -> bool:
        """
        Release a flashed expression whose hold has elapsed.

        Returns True when it released one. Safe to call at any rate; a
        director with nothing flashed does two comparisons and returns.
        """

        if self._until is None or now < self._until:
            return False

        self._flashed = None
        self._until = None

        return self._settle(reason="released")

    def release(self) -> bool:
        """Drop a flashed expression immediately."""

        return self.tick(float("inf"))

    # ------------------------------------------------------------------
    # Derivation
    # ------------------------------------------------------------------

    @property
    def derived(self) -> Expression:
        """The face the current state and mood imply, ignoring flashes."""

        try:
            return self.policy.expression_for(self._state, self._mood)
        except Exception:
            return Expression.NEUTRAL

    @property
    def flashing(self) -> bool:
        return self._flashed is not None

    def _settle(self, reason: str) -> bool:
        """Show the derived face, unless a flash is still holding."""

        if self._flashed is not None:
            return False

        return self._show(self.derived, hold=0.0, reason=reason)

    def _show(
        self,
        expression: Expression,
        hold: float,
        reason: str,
    ) -> bool:

        if expression == self.expression:
            return False

        self.expression = expression

        for listener in list(self._listeners):
            try:
                listener(expression)
            except Exception:
                # A broken renderer must not break expression tracking.
                pass

        if self.publish and self.events is not None:
            try:
                self.events.publish(
                    ExpressionChangedEvent(
                        expression=expression,
                        hold=hold,
                        reason=reason,
                    )
                )
            except Exception:
                pass

        return True

    def __repr__(self) -> str:
        return f"ExpressionDirector({self.expression.value})"


def parse_expression(
    name: str,
    fallback: Expression = Expression.NEUTRAL,
) -> Expression:
    """
    Read an expression out of a string, tolerantly.

    For config, plugins and future commands. An unrecognised name falls
    back rather than raising - the same rule parse_mood follows, for the
    same reason.
    """

    text = (name or "").strip().lower()

    for expression in Expression:
        if expression.value == text:
            return expression

    return fallback


__all__ = [
    "Expression",
    "ExpressionPolicy",
    "DefaultExpressionPolicy",
    "ExpressionDirector",
    "MOOD_FACES",
    "STATE_FACES",
    "DEFAULT_HOLD",
    "parse_expression",
]
