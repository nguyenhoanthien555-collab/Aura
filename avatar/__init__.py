"""
Avatar.

A face that watches the event bus. It reacts; it never decides.

    bus -> AvatarStateMachine -> renderer (Tk window, or nothing)
"""

from avatar.state import AvatarStateMachine
from avatar.renderer import AvatarRenderer, NullRenderer
from avatar.controller import AvatarController, create_renderer

__all__ = [
    "AvatarStateMachine",
    "AvatarRenderer",
    "NullRenderer",
    "AvatarController",
    "create_renderer",
]
