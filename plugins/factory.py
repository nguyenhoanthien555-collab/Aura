"""
Plugin construction.

Builds the plugin system from configuration. Handles discovery from both
bundled and user directories, policy decisions about what runs, and
construction of the context plugins receive.

The registry vs. executor parallel from tools carries over: discovery
populates the manager with everything that could run, enablement policy
decides what actually does, and the context is handed out only after both
are resolved.
"""

from core.config import load_config
from core.logger import logger
from plugins.base import PluginContext
from plugins.discovery import discover
from plugins.manager import PluginManager


def build_manager(config: dict | None = None) -> PluginManager:
    """
    Build a manager populated with every plugin that could be found.

    Policy (which plugins to enable) is separate from discovery, so
    construction never decides what runs — that is the caller's job.
    """

    config = config or {}

    manager = PluginManager()

    directory = config.get("directory")

    for plugin in discover(directory=directory):

        try:
            manager.register(plugin)

        except ValueError as error:
            logger.warning("Could not register plugin: %s", error)

    return manager


def build_plugins(
    config: dict | None = None,
    bus=None,
    tools=None,
) -> PluginManager:
    """
    Build the plugin system Aura will use.

    `bus` is the event publisher; `tools` is the tool registry (not the
    executor). Both are optional, so a stripped-down Aura with no events
    and no tools can still load plugins — they simply receive an empty
    context.

    Returns an initialized manager. What gets enabled is decided by
    `config["plugins"]["enabled"]`, which may be:

        - a list of names    enable exactly those
        - True               enable everything discovered
        - False or missing   enable nothing (default)

    An empty list disables everything explicitly, which is different from
    leaving the key absent — the former is "I looked and chose none", the
    latter is "I have not configured this yet".
    """

    config = config if config is not None else load_config()

    settings = config.get("plugins") or {}

    manager = build_manager(settings)

    # Policy: what runs
    enabled = settings.get("enabled")

    if enabled is True:
        manager.enable_all()

    elif isinstance(enabled, (list, tuple)):
        manager.enable(*enabled)

    # Context: what they may touch
    context = PluginContext(
        bus=bus,
        tools=tools,
        config=settings.get("config") or {},
    )

    manager.initialize(context)

    if len(manager) > 0:
        logger.info("Plugins: %s", manager.summary())

    return manager
