"""
The bus, written down.

`subscribe_all` existed with no production caller, and two docstrings
said otherwise: `events/__init__.py` claims "Avatar, voice, and logging
subscribe", and `subscribe_all` itself says "Used by logging and by the
avatar debug overlay". Neither was true, so nothing that crossed the bus
was observable. When a proactive notification fired and never reached the
phone there was no way to tell whether it was published at all, dropped
by the outbox age limit, or drained by a different device - three
different bugs with one symptom.

This is the observer, and its whole design is decided by section 30.

Keys must never appear in normal logs. The same argument covers the
owner's conversation, which is why the rule here is *default-deny on
strings*: a str field is written as its length and nothing else, unless
the event that owns it opts it in by name. A secret is a string. A count
is not, so numbers, bools and enums are written as themselves.

Default-deny matters more than the redaction itself. It means a new event
added to `events/types.py` next month is safe in the log the day it is
written, by nobody having done anything. The opposite default would be
safe only for as long as everyone remembered.
"""

from enum import Enum

from core.logger import logger
from events.types import BlinkEvent, ErrorEvent, StreamChunkEvent


# Fields whose *value* is written out, keyed by nothing - a name on this
# list is opted in wherever it appears.
#
# Flat rather than per-event on purpose. Two events already share `name`
# and `ok`, and a per-event table would let the same field be private in
# one and public in another, which is the kind of disagreement nobody
# notices until it is in a log file. An event with a field that needs a
# different answer says so itself, with its own `log_fields`.
#
# Each of these is written in Aura's own source rather than taken from
# the owner or chosen by a model:
#
#   name        a tool or provider identifier from the registry
#   source      which subsystem spoke ("llm", "text", "voice")
#   reason      free text Aura wrote for a human to read; the events that
#               carry one all say it is never parsed
#   priority    a fixed three-value vocabulary
#   detail      a tool's own summary of what it did
#   message     an ErrorEvent's diagnostic. Section 29 requires the
#               taxonomy to carry a reason without leaking secrets, so
#               the redaction for that happens where the message is
#               built, not here.
SAFE_FIELDS = frozenset(
    {"name", "source", "reason", "priority", "detail", "message"}
)


# Events that are never written at all.
#
# Not a severity judgement - both of these are useful facts, and both are
# delivered to their real subscribers exactly as before. They are excluded
# because writing them down destroys the log rather than filling it.
#
#   BlinkEvent          fires on an idle timer and keeps firing while Aura
#                       sits doing nothing, so a log left running
#                       overnight would be blinks with a trace buried
#                       somewhere inside it. Nothing is learned from the
#                       thousandth blink that the first did not say.
#
#   StreamChunkEvent    one per fragment, so a 300 token reply costs 300
#                       lines - and the count is already reported once,
#                       accurately, as `StreamFinishedEvent.chunks`.
#
# Matched with isinstance rather than by name, so a subclass inherits the
# exclusion along with the behaviour that earned it.
QUIET_EVENTS: tuple[type, ...] = (BlinkEvent, StreamChunkEvent)


def describe(event) -> str:
    """
    One event as a log line body: `field=value` pairs, safely.

    Returns "" for an event with nothing safe to say, so the caller
    prints its type name alone rather than a trailing empty bracket.
    """

    fields = getattr(event, "__dataclass_fields__", None)

    if not fields:
        return ""

    opted_in = SAFE_FIELDS | set(getattr(event, "log_fields", ()) or ())

    parts: list[str] = []

    for name in fields:

        value = getattr(event, name)

        # Enum before str, and this is the one ordering that matters.
        # `AuraState`, `Mood` and `Expression` are all declared
        # `(str, Enum)`, so a plain isinstance(value, str) catches them
        # and would redact `state=thinking` - the single most useful
        # thing on the bus - down to a character count.
        if isinstance(value, Enum):
            parts.append(f"{name}={value.value}")

        elif isinstance(value, bool):
            # Before int, because bool is a subclass of it and
            # `ok=1` reads worse than `ok=True`.
            parts.append(f"{name}={value}")

        elif isinstance(value, (int, float)):
            parts.append(f"{name}={value}")

        elif isinstance(value, str):

            if name in opted_in:
                parts.append(f"{name}={value}")
            elif value:
                # Redacted is not invisible. The length is what makes an
                # empty reply distinguishable from no reply at all, and
                # it leaks nothing.
                parts.append(f"{name}=<{len(value)} chars>")

        # Anything else - a dict, a dataclass, a list - is named and not
        # opened. Walking it would be the one place a secret could hide
        # from every rule above, and this is what keeps a tool's
        # `arguments` out: they were chosen by a model from the owner's
        # conversation, and they arrive here as a dict, so the container
        # is reported and its contents never are. An earlier version also
        # named `arguments` in a skip list; a mutation emptying that list
        # left every test green, which is how it was found to be a second
        # mechanism for a guarantee this line already makes.
        elif value is not None:
            parts.append(f"{name}=<{type(value).__name__}>")

    return " ".join(parts)


class EventLogger:
    """
    Writes every event that crosses the bus, at a level per severity.

    Two levels, not one. Everything is DEBUG, because a line per event at
    INFO would be a flood on every conversation turn and would train the
    owner to ignore the log - the same argument section 20 makes about
    notifications. `ErrorEvent` is WARNING, because a failure is the one
    thing worth seeing without turning debug on.

    Owns no state beyond its subscription, so attaching a second one is
    harmless.
    """

    def __init__(self, log=None):
        # Injected for tests that want the calls rather than the text.
        # Defaults to Aura's own logger, which is what `caplog` reads.
        self.log = log or logger
        self._release = None

    def attach(self, bus):
        """
        Start listening, and return a release callable.

        Same shape as `AvatarStateMachine.attach`, `MoodTracker` and
        `TTSEngine`: attaching hands back the way to stop, so a plugin or
        a test does not have to reach into the bus to undo it.
        """

        self.detach()

        self._release = bus.subscribe_all(self._write)

        return self._release

    def detach(self) -> None:
        if self._release is not None:
            self._release()
            self._release = None

    def _write(self, event) -> None:

        if isinstance(event, QUIET_EVENTS):
            return

        name = type(event).__name__

        try:
            body = describe(event)
        except Exception as error:
            # The bus already swallows handler exceptions, so raising here
            # would surface as "Event handler failed" once per event -
            # noise that buries whatever was being debugged. Say which
            # type could not be read and keep the name, which is the part
            # that was worth logging anyway.
            self.log.debug("event %s (unreadable: %s)", name, error)
            return

        line = f"event {name}" + (f" {body}" if body else "")

        if isinstance(event, ErrorEvent):
            self.log.warning(line)
        else:
            self.log.debug(line)


def attach_event_log(bus) -> EventLogger:
    """
    Build the observer and connect it, for a composition root.

    A function rather than a line in `launcher/services.py` so the CLI
    root and the server root cannot end up with different versions of
    "log the bus".
    """

    watcher = EventLogger()
    watcher.attach(bus)

    return watcher


__all__ = [
    "EventLogger",
    "attach_event_log",
    "describe",
    "QUIET_EVENTS",
    "SAFE_FIELDS",
]
