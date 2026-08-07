"""
Session statistics.

The bundled example, and it is here to show the shape rather than to be
impressive. It exercises every part of the plugin contract exactly once:

    initialize   subscribe to the bus, register a tool, read its config
    shutdown     unsubscribe, unregister, leave nothing behind

What it does is count turns and report them when asked. What it
demonstrates is that a plugin can observe a conversation and give Aura a
new capability without importing brain/, voice/ or avatar/, and without
anything in Aura importing this file by name.

The tool it registers is written structurally - three attributes and a
method, no base class - which is the Section 7 promise being cashed in.
The only thing it imports from tools/ is the risk level, because a risk
level is data and inventing a second one here would put a tool outside
the approval gate.
"""

import time

from events.types import ResponseEvent, UserInputEvent
from plugins.base import Plugin, PluginContext
from tools.base import ToolRisk


DEFAULT_FORMAT = "%d turns in %s"


class SessionStatsTool:
    """
    Reports how long this session has run and how much was said.

    Structural, not inherited: `name`, `risk` and `execute` are the whole
    contract. SAFE because it reads nothing outside Aura's own memory of
    the current process.
    """

    name = "session_stats"
    description = "How long this session has run and how many turns it has"
    risk = ToolRisk.SAFE

    def __init__(self, stats: "SessionStatsPlugin"):
        self._stats = stats

    def execute(self, **arguments) -> str:
        return self._stats.render()


class SessionStatsPlugin(Plugin):

    name = "session_stats"
    version = "1.0.0"

    def __init__(self, clock=time.monotonic):
        # Injected so a test does not have to wait for time to pass.
        self._clock = clock

        self._started = self._clock()

        self.turns = 0
        self.replies = 0

        self._unsubscribe: list = []
        self._registry = None
        self._format = DEFAULT_FORMAT

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self, context: PluginContext) -> None:
        """
        Take what is offered and no more.

        Both handles are optional. An Aura with no bus and no tools still
        loads this plugin; it simply has nothing to attach to, which is a
        quieter failure than refusing to start.
        """

        self._format = (context.config or {}).get("format") or DEFAULT_FORMAT

        self._started = self._clock()

        if context.bus is not None:
            self._attach(context.bus)

        if context.tools is not None:
            self._register(context.tools)

    def shutdown(self) -> None:
        """
        Undo exactly what initialize did.

        A plugin that leaves a subscription behind keeps receiving events
        after it has been disabled, which is how a "disabled" plugin ends
        up still doing things.
        """

        for cancel in self._unsubscribe:
            try:
                cancel()
            except Exception:
                pass

        self._unsubscribe.clear()

        if self._registry is not None:
            try:
                self._registry.unregister(self.name)
            except Exception:
                pass

            self._registry = None

    # ------------------------------------------------------------------
    # Attachment
    # ------------------------------------------------------------------

    def _attach(self, bus) -> None:

        try:
            self._unsubscribe.append(
                bus.subscribe(UserInputEvent, self._on_input)
            )
            self._unsubscribe.append(
                bus.subscribe(ResponseEvent, self._on_response)
            )
        except Exception:
            # A bus that does not support subscription is not a reason to
            # fail the whole plugin - the tool half still works.
            pass

    def _register(self, registry) -> None:

        try:
            registry.register(SessionStatsTool(self))
            self._registry = registry
        except Exception:
            # Most likely a duplicate name, which means something else
            # already provides this. Not ours to override.
            self._registry = None

    # ------------------------------------------------------------------
    # Counting
    # ------------------------------------------------------------------

    def _on_input(self, event) -> None:
        self.turns += 1

    def _on_response(self, event) -> None:
        self.replies += 1

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def elapsed(self) -> float:
        """Seconds since this plugin was initialized."""

        return max(0.0, self._clock() - self._started)

    def render(self) -> str:
        """One line, in whatever shape the config asked for."""

        try:
            return self._format % (self.turns, _duration(self.elapsed()))
        except (TypeError, ValueError):
            # A malformed format string in config loses the formatting,
            # not the answer.
            return DEFAULT_FORMAT % (self.turns, _duration(self.elapsed()))


def _duration(seconds: float) -> str:
    """Seconds as something a person would say out loud."""

    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)

    if hours:
        return f"{hours}h {minutes}m"

    if minutes:
        return f"{minutes}m {seconds}s"

    return f"{seconds}s"


def plugin() -> SessionStatsPlugin:
    """
    How this module advertises itself.

    A factory rather than a module level instance, and the reason is
    lifetime: discovery may run more than once in one process - a test
    suite, an embedding host, a settings screen that reloads - and a
    shared instance would carry the previous run's subscriptions and
    counts into the next one. A fresh plugin per discovery has no such
    memory.
    """

    return SessionStatsPlugin()
