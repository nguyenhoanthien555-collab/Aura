"""
Avatar backends.

Interfaces for the renderers Aura does not have yet: Live2D, VRM, a
sprite sheet of PNGs, and an Open-LLM-VTuber compatible surface. There is
no implementation here and there is not meant to be one. Every method
below is a shape a future renderer satisfies structurally; none of them
imports a graphics library, and importing this module pulls in two enums
and nothing else.

The reason for writing them now rather than when the first one is built:
the shape of the interface decides what the rest of the system has to
know. Deciding it against four backends at once produces something none
of them has to work around. Deciding it against whichever one is built
first produces an interface with that one's assumptions baked in, and the
second backend arrives as a rewrite.

Capabilities, not a class hierarchy
-----------------------------------

`AvatarRenderer` in renderer.py stays exactly as it is: set_state, show,
hide, close. That is what a renderer must do. Everything a renderer
*might* do is a separate protocol here, checked with isinstance:

    ExpressionRenderer   set_expression   a face
    MotionRenderer       play             a named animation
    LipSyncRenderer      set_mouth        a mouth opening, 0.0 to 1.0
    BlinkRenderer        blink            eyelids
    ModelRenderer        load_model       swap the character

None of them inherits from AvatarRenderer, and AvatarRenderer gains
nothing. That is deliberate and it is the same rule already documented in
renderer.py: AvatarRenderer is runtime_checkable, so adding a method to
it would make every renderer that lacks it - including a user's own -
stop satisfying isinstance. Capability protocols add without breaking.

A caller asks before it calls:

    if isinstance(renderer, ExpressionRenderer):
        renderer.set_expression(Expression.HAPPY)

which is what AvatarController already does with getattr for set_mood.

What each backend is expected to implement
------------------------------------------

    PNG          AvatarRenderer, ExpressionRenderer
    Live2D       + MotionRenderer, LipSyncRenderer, BlinkRenderer
    VRM          + MotionRenderer, LipSyncRenderer, BlinkRenderer
    OpenLLMVT    a remote surface: whatever it forwards

Nothing enforces that. A Live2D renderer that only does expressions is a
valid Live2D renderer with fewer capabilities, and the system degrades to
what it can do rather than failing to start.

Vocabulary, not translation
---------------------------

Expression values are already lowercase strings chosen to be usable
directly as Live2D expression ids, VRM blendshape names or sprite file
stems. `AvatarModel` carries an optional mapping for a model whose author
named things differently, so the adaptation lives in one small object
next to the model rather than in a lookup table in the renderer.
"""

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from events.types import AuraState, Expression


# ----------------------------------------------------------------------
# Capabilities
# ----------------------------------------------------------------------

@runtime_checkable
class ExpressionRenderer(Protocol):
    """A renderer that can show a face."""

    def set_expression(self, expression: Expression) -> None:
        ...


@runtime_checkable
class MotionRenderer(Protocol):
    """
    A renderer that can play a named animation.

    `motion` is a string rather than an enum because motion names belong
    to a model, not to Aura. A Live2D model ships with whatever its
    author called them, and an enum here would either be wrong for every
    model or would need extending for each new one.
    """

    def play(self, motion: str, loop: bool = False) -> None:
        ...


@runtime_checkable
class LipSyncRenderer(Protocol):
    """
    A renderer that can drive a mouth.

    `amount` is 0.0 (closed) to 1.0 (fully open) - a normalised opening
    rather than an audio sample, a viseme or a phoneme. That is the
    smallest thing every backend can act on: Live2D maps it to
    ParamMouthOpenY, VRM to an 'aa' blendshape, a sprite renderer to
    "pick the open mouth above 0.5".

    Whoever computes it - an RMS envelope, a viseme timeline, a fixed
    flap while SpeechStartedEvent is outstanding - stays outside this
    interface.
    """

    def set_mouth(self, amount: float) -> None:
        ...


@runtime_checkable
class BlinkRenderer(Protocol):
    """A renderer that can blink on request. Consumes BlinkEvent."""

    def blink(self, double: bool = False) -> None:
        ...


@runtime_checkable
class ModelRenderer(Protocol):
    """A renderer that can swap the character it is drawing."""

    def load_model(self, model: "AvatarModel") -> bool:
        ...


# ----------------------------------------------------------------------
# Model description
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class AvatarModel:
    """
    Which character to draw, and where its files are.

    A description, not a loaded asset: it holds paths and names, never a
    texture, a handle or an open file. That is what lets it live in
    config, be passed between processes, and be constructed in a test
    with no files on disk at all.

    `expressions` and `motions` map Aura's vocabulary onto this model's.
    Empty means the names already match, which is the intended case -
    Expression values are lowercase strings precisely so a model whose
    expressions are named "happy" and "sleepy" needs no mapping.
    """

    name: str = ""
    kind: str = "png"                    # png | live2d | vrm | remote
    path: str = ""

    expressions: dict[str, str] = field(default_factory=dict)
    motions: dict[str, str] = field(default_factory=dict)

    scale: float = 1.0

    def expression_id(self, expression: Expression) -> str:
        """This model's name for an expression, or Aura's if unmapped."""

        key = getattr(expression, "value", str(expression))

        return self.expressions.get(key, key)

    def motion_id(self, motion: str) -> str:
        """This model's name for a motion, or the one given."""

        return self.motions.get(motion, motion)


# ----------------------------------------------------------------------
# Idle behaviour
# ----------------------------------------------------------------------

# Which motion to loop in each state, by name. A model that has no motion
# under one of these names ignores it; a renderer that cannot play
# motions at all never reads this.
#
# These are the names Open-LLM-VTuber and most Live2D sample models use,
# so a stock model works without a mapping.
IDLE_MOTIONS: dict[AuraState, str] = {
    AuraState.IDLE: "Idle",
    AuraState.LISTENING: "Idle",
    AuraState.THINKING: "Thinking",
    AuraState.SPEAKING: "Speaking",
}


def idle_motion_for(state: AuraState) -> str:
    """The looping motion a state implies. "" when there is none."""

    return IDLE_MOTIONS.get(state, "")


# ----------------------------------------------------------------------
# Remote surfaces
# ----------------------------------------------------------------------

@runtime_checkable
class RemoteAvatar(Protocol):
    """
    An avatar running in another process - a browser, a VTuber app.

    Open-LLM-VTuber is the case this exists for: its renderer is a web
    page driven over a websocket, so "drawing" here means forwarding a
    message. The transport is deliberately absent from the interface. A
    websocket client, a local HTTP post and a test double that appends to
    a list are all the same shape.

    `send` takes a dict rather than a typed message because the payload
    belongs to the remote protocol, and pinning it here would make this
    module own a schema it cannot verify. The adapter that speaks a
    particular protocol owns that, and it is the only thing that changes
    when the protocol version does.
    """

    def send(self, message: dict) -> bool:
        ...

    def is_connected(self) -> bool:
        ...


class NullRemoteAvatar:
    """
    A remote avatar that is not there.

    The default, so nothing has to branch on None, and the record of what
    would have been sent is exactly what a test wants to assert on.
    """

    def __init__(self):
        self.sent: list[dict] = []

    def send(self, message: dict) -> bool:
        self.sent.append(message)
        return False

    def is_connected(self) -> bool:
        return False


def capabilities_of(renderer) -> set[str]:
    """
    Which of the optional protocols a renderer satisfies.

    For diagnostics and for a launcher that wants to log what the avatar
    can do at startup. Names rather than types so the result is printable
    and comparable in a test.
    """

    found = set()

    for name, protocol in (
        ("expression", ExpressionRenderer),
        ("motion", MotionRenderer),
        ("lipsync", LipSyncRenderer),
        ("blink", BlinkRenderer),
        ("model", ModelRenderer),
    ):
        try:
            if isinstance(renderer, protocol):
                found.add(name)
        except TypeError:
            continue

    return found


__all__ = [
    "ExpressionRenderer",
    "MotionRenderer",
    "LipSyncRenderer",
    "BlinkRenderer",
    "ModelRenderer",
    "AvatarModel",
    "RemoteAvatar",
    "NullRemoteAvatar",
    "IDLE_MOTIONS",
    "idle_motion_for",
    "capabilities_of",
]
