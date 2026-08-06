"""
Text to speech provider interface.

Kept to a single method on purpose. Anything with `speak(text)` is a
valid voice for Aura, including a three line class written by a user.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class TTSProvider(Protocol):
    """
    Speaks a line of text.

    Blocking is allowed. Whether speech happens on the calling thread is
    the engine's problem, not the provider's.
    """

    def speak(self, text: str) -> None:
        ...


def is_provider_available(provider) -> bool:
    """
    Best effort availability check.

    `is_available` is optional in the protocol, so a provider that does
    not define it is assumed to work. A provider that defines it and
    raises is treated as unavailable rather than propagating the error -
    availability checks run during startup, where a crash is costly and
    a missing voice is not.
    """

    check = getattr(provider, "is_available", None)

    if check is None:
        return True

    try:
        return bool(check())
    except Exception:
        return False
