"""
Bounding how long a tool may take.

A tool is the one place where Aura hands control to code that can block
forever: a file read on a dead network share, a subprocess that never
exits, a socket with no deadline. Without a bound, a single tool call
stops the conversation rather than failing it.

What this can and cannot do is worth stating plainly, because the
difference matters:

    it CAN     stop waiting, and turn the wait into a failed result
    it CANNOT  kill the tool that is still running

Python has no safe way to terminate a thread from outside it - anything
that could would leave locks held and files half written. So the call is
abandoned rather than killed: the worker is a daemon, so it never keeps
the process alive, and the caller gets its answer on time. A tool that
hangs leaks one idle thread until the process ends, which is the honest
cost of not corrupting whatever it was in the middle of.

The same reasoning is why a tool that legitimately blocks can opt out
with `timeout = 0` rather than being forced through a thread that cannot
help it.
"""

import threading

from core.logger import logger


# Long enough for a slow disk or a cold subprocess, short enough that a
# hung tool is noticed within one reply rather than one conversation.
DEFAULT_TOOL_TIMEOUT = 30.0


class ToolTimeout(Exception):
    """A tool did not return in time. Raised to the executor, never past it."""


def call_with_timeout(call, timeout: float, name: str = "tool"):
    """
    Run `call()` and stop waiting after `timeout` seconds.

    Returns whatever `call` returned, re-raises whatever it raised, and
    raises ToolTimeout if it is still running when the time is up.

    A timeout of 0 or less runs the call inline on this thread. That is
    not a loophole but a deliberate escape hatch: a tool that is known to
    block for a long time gains nothing from a thread that cannot stop
    it, and loses the ability to touch thread-affine state.
    """

    if not timeout or timeout <= 0:
        return call()

    outcome: dict = {}
    finished = threading.Event()

    def run() -> None:

        try:
            outcome["value"] = call()

        except BaseException as error:      # noqa: BLE001 - re-raised below
            # BaseException rather than Exception so a KeyboardInterrupt
            # raised inside a tool reaches the caller exactly as it would
            # have on the inline path, instead of being swallowed into a
            # thread nobody is watching.
            outcome["error"] = error

        finally:
            finished.set()

    worker = threading.Thread(
        target=run,
        name=f"aura-tool-{name}",
        daemon=True,
    )

    worker.start()

    if not finished.wait(timeout):

        logger.warning(
            "Tool %s did not finish within %ss; abandoning the call",
            name,
            _pretty(timeout),
        )

        raise ToolTimeout(f"{name} timed out after {_pretty(timeout)}s")

    if "error" in outcome:
        raise outcome["error"]

    return outcome.get("value")


def seconds_or(value, fallback: float) -> float:
    """
    Read a seconds value out of config, falling back rather than raising.

    A negative or unreadable timeout must not quietly become "give up
    instantly", which is what `float("-1")` would mean to the caller.
    Zero is left alone: it is the documented way to say "no bound".
    """

    if isinstance(value, bool) or value is None:
        return fallback

    try:
        seconds = float(value)
    except (TypeError, ValueError):
        logger.warning("Unusable tool timeout %r, using %s", value, fallback)
        return fallback

    if seconds < 0:
        logger.warning("Negative tool timeout %r, using %s", value, fallback)
        return fallback

    return seconds


def _pretty(seconds: float) -> str:
    """30.0 reads better as 30."""

    return f"{seconds:g}"


__all__ = [
    "DEFAULT_TOOL_TIMEOUT",
    "ToolTimeout",
    "call_with_timeout",
    "seconds_or",
]
