"""
Avatar renderer.

The drawing surface, behind an interface. Today there is a Tk window and
a headless null renderer; a Live2D or sprite based renderer only has to
satisfy the same four methods.

A renderer never decides anything. It is told a state and draws it.
"""

from typing import Protocol, runtime_checkable

from events.types import AuraState


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
        self.visible = False
        self.closed = False

    def set_state(self, state: AuraState) -> None:
        self.states.append(state)

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
