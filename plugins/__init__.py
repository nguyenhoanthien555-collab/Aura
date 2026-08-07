"""
Plugins.

    PluginProtocol  what a plugin is
    PluginManager   what exists and what runs
    PluginContext   what a plugin receives

Nothing in brain/ imports this package. When plugins are enabled, the
manager is built by the launcher and handed the event bus and tool
registry. Plugins register themselves; they are never imported by name
from anywhere in Aura.

The dependency arrow points one way, and it points inwards. A plugin
imports `plugins.base` for convenience or nothing at all if it is written
against PluginProtocol structurally; it never imports brain/, voice/ or
avatar/. That is what keeps a plugin something Aura can be granted rather
than something she is built out of.
"""

from plugins.base import Plugin, PluginContext, PluginProtocol
from plugins.discovery import discover, plugins_in
from plugins.factory import build_manager, build_plugins
from plugins.manager import PluginManager

__all__ = [
    "Plugin",
    "PluginProtocol",
    "PluginContext",
    "PluginManager",
    "discover",
    "plugins_in",
    "build_manager",
    "build_plugins",
]
