"""
Avatar renderer.

The drawing surface, behind an interface. Today there is a Tk window and
a headless null renderer; a Live2D or sprite based renderer only has to
satisfy the same four methods.

A renderer never decides anything. It is told a state and draws it.
"""

from typing import Protocol, runtime_checkable

from events.types import AuraState, Expression, Mood


@runtime_checkable
class AvatarRenderer(Protocol):

    def set_state(self, state: AuraState) -> None:
        ...

    def show(self) -> None:
        ...

    def hide(self) -> None:
        ...

    def close(self) -> None:
        ...


# Three methods are deliberately NOT part of the Protocol above:
#
#     set_mood(mood: Mood) -> None
#     set_expression(expression: Expression) -> None
#     play(motion: str) -> None
#
# Adding any of them would make every existing renderer stop satisfying
# AvatarRenderer, and a sprite renderer with four PNGs has no use for
# them. `avatar.backends` declares each as its own capability protocol,
# so a Live2D renderer can be checked for what it supports.
#
# A renderer that wants expressions just defines the method; the
# controller looks for it and calls it if it is there. Optional by
# absence rather than by a base class full of `pass`.


class NullRenderer:
    """
    Renderer that draws nothing and remembers everything.

    This is what runs in tests, on a headless machine, and whenever the
    avatar is disabled in config. Because it records every state it was
    given, the full event to avatar path can be asserted without a
    display.
    """

    def __init__(self):

        self.states: list[AuraState] = []
        self.moods: list[Mood] = []
        self.expressions: list[Expression] = []
        self.motions: list[str] = []
        self.blinks = 0
        self.visible = False
        self.closed = False

    def set_state(self, state: AuraState) -> None:
        self.states.append(state)

    def set_mood(self, mood: Mood) -> None:
        self.moods.append(mood)

    def set_expression(self, expression: Expression) -> None:
        self.expressions.append(expression)

    def play(self, motion: str) -> None:
        self.motions.append(motion)

    def blink(self, double: bool = False) -> None:
        self.blinks += 1

    def show(self) -> None:
        self.visible = True

    def hide(self) -> None:
        self.visible = False

    def close(self) -> None:
        self.closed = True
        self.visible = False

    def is_available(self) -> bool:
        return True

    @property
    def state(self) -> AuraState | None:
        return self.states[-1] if self.states else None

    @property
    def mood(self) -> Mood | None:
        return self.moods[-1] if self.moods else None

    @property
    def expression(self) -> Expression | None:
        return self.expressions[-1] if self.expressions else None
