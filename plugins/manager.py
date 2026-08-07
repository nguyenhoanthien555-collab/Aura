"""
Plugin manager.

Owns the set of loaded plugins and orchestrates their lifecycle. It
discovers, enables, disables, initializes, and shuts them down — but it
never touches their internals. Plugins communicate through the context
they receive; the manager is the one that fills that context in.

Order matters in one place: plugins must be initialized before they can
be shut down, which is why the methods are separate rather than combined
into a context manager's __enter__/__exit__ pair.

Everything degrades. A plugin whose `initialize` raises is logged and
marked broken rather than stopping Aura. A plugin whose `shutdown`
raises is logged and ignored, because shutdown happens on the way out
and there is nothing sensible to do with the error.
"""

from core.logger import logger
from plugins.base import PluginContext, PluginProtocol


class PluginManager:

    def __init__(self, plugins: list[PluginProtocol] | None = None):

        self.plugins: list[PluginProtocol] = []

        self.enabled: set[str] = set()
        self.broken: set[str] = set()

        # Which plugins actually made it through `initialize`. Distinct
        # from `enabled`: a name can be enabled in config and never
        # initialized, and only something initialized has anything to
        # release. This is what `shutdown` iterates.
        self.initialized: list[str] = []

        self._context: PluginContext | None = None

        for plugin in plugins or []:
            self.register(plugin)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, plugin: PluginProtocol) -> None:
        """
        Add a plugin to the set this manager owns.

        The shape is checked here rather than trusted, because this is the
        boundary a discovered module arrives through. A malformed plugin is
        far easier to diagnose at load than halfway through a lifecycle
        call that cannot complete.

        Duplicate names are rejected rather than silently shadowing: two
        plugins called "weather" is a configuration mistake, and picking
        one arbitrarily hides it.
        """

        name = (getattr(plugin, "name", "") or "").strip()

        if not name:
            raise ValueError("Plugin must have a name")

        if not isinstance(plugin, PluginProtocol):
            raise ValueError(
                f"Plugin '{name}' does not satisfy PluginProtocol "
                f"(needs name, version, initialize and shutdown)"
            )

        if self.get(name) is not None:
            raise ValueError(f"Plugin already registered: {name}")

        self.plugins.append(plugin)

        logger.debug("Discovered plugin: %s v%s", name, plugin.version)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self, context: PluginContext) -> None:
        """
        Hand each enabled plugin the handles it may use.

        Called once, after discovery and enablement decisions are made.
        A plugin that fails here is marked broken rather than removed,
        so the manager can still report what it tried to load.

        Each plugin receives a narrowed context: its own config is the
        value of ``config[plugin.name]``, or an empty dict. One plugin
        cannot read another's settings, because it never receives the
        full map.
        """

        self._context = context

        for plugin in self.plugins:

            if plugin.name not in self.enabled:
                continue

            if plugin.name in self.broken:
                continue

            if plugin.name in self.initialized:
                continue

            try:
                plugin.initialize(
                    context.with_config(
                        (context.config or {}).get(plugin.name)
                    )
                )

                self.initialized.append(plugin.name)

                logger.info(
                    "Plugin initialized: %s v%s", plugin.name, plugin.version
                )

            except Exception as error:
                self.broken.add(plugin.name)

                logger.warning(
                    "Plugin %s failed to initialize: %s", plugin.name, error
                )

    def shutdown(self) -> None:
        """
        Ask each initialized plugin to clean up.

        Called once before exit. Errors are logged and ignored, because
        shutdown is already on the way out and nothing sensible can be
        done with them. The rest of the list still runs.

        Shutdown runs in reverse initialization order, so a plugin that
        depends on another's registration finishes last.
        """

        for name in reversed(self.initialized):

            plugin = self.get(name)

            if plugin is None:
                continue

            try:
                plugin.shutdown()

            except Exception as error:
                logger.debug("Plugin %s raised during shutdown: %s", name, error)

    # ------------------------------------------------------------------
    # Enablement
    # ------------------------------------------------------------------

    def enable(self, *names: str) -> None:
        """Mark these plugins as enabled. Idempotent."""

        self.enabled.update(names)

    def disable(self, *names: str) -> None:
        """
        Mark these plugins as disabled.

        Does not call `shutdown`. A plugin can be disabled before it
        is ever initialized — during configuration, before the bus or
        tools exist — so a single method cannot do both.
        """

        self.enabled.difference_update(names)

    def enable_all(self) -> None:
        """Enable every discovered plugin."""

        self.enable(*(plugin.name for plugin in self.plugins))

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, name: str) -> PluginProtocol | None:
        """The plugin with this name, or None."""

        for plugin in self.plugins:
            if plugin.name == name:
                return plugin

        return None

    def names(self) -> list[str]:
        """Every plugin name, whether enabled or not."""

        return [plugin.name for plugin in self.plugins]

    def is_enabled(self, name: str) -> bool:
        return name in self.enabled

    def is_broken(self, name: str) -> bool:
        return name in self.broken

    def status(self) -> dict[str, str]:
        """
        A snapshot of what is running.

        Returns a dict mapping name -> status ("enabled", "disabled",
        "broken"). Useful for diagnostics and for a settings UI.
        """

        result: dict[str, str] = {}

        for plugin in self.plugins:

            if plugin.name in self.broken:
                result[plugin.name] = "broken"
            elif plugin.name in self.enabled:
                result[plugin.name] = "enabled"
            else:
                result[plugin.name] = "disabled"

        return result

    def summary(self) -> str:
        """One line, for logs."""

        active = len(self.enabled - self.broken)
        total = len(self.plugins)

        return f"{active}/{total} plugins active"

    def __len__(self) -> int:
        return len(self.plugins)
