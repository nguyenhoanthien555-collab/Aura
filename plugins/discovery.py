"""
Plugin discovery.

Finds plugin objects on disk and turns them into instances. It imports
modules and reads attributes; it never calls `initialize`, because
deciding which plugins may run is the manager's job and importing is
already the riskiest thing this file does.

Two directories are searched:

    plugins/builtins/     shipped with Aura
    whatever `plugins.directory` names, when it is set

A module advertises itself in one of three ways, checked in this order:

    PLUGIN = MyPlugin()        an instance, for a plugin with no setup
    def plugin(): ...          a factory, when construction needs to run
    class MyPlugin(Plugin)     a subclass, found by scanning the module

The instance form is first because it is the least surprising. The
factory exists for a plugin whose constructor needs to read something.
The scan is last and deliberately narrow: only subclasses of Plugin
defined in that module qualify, so importing Plugin at the top of a file
never accidentally registers the base class itself.

Everything here degrades. A module that raises on import is logged and
skipped, because one broken plugin must not stop Aura from starting.
"""

import importlib
import importlib.util
import inspect
import pkgutil
import sys
from pathlib import Path

from core.logger import logger
from plugins.base import Plugin, PluginProtocol


# Attribute names a module may use to advertise its plugin.
INSTANCE_ATTRIBUTE = "PLUGIN"
FACTORY_ATTRIBUTE = "plugin"

# Modules whose names start with this are never imported: `_helpers.py`
# next to a plugin is support code, not a plugin.
PRIVATE_PREFIX = "_"


def discover(
    package: str = "plugins.builtins",
    directory: str | Path | None = None,
) -> list[PluginProtocol]:
    """
    Every plugin instance that could be found.

    Bundled plugins first, then anything in `directory`. Order matters
    only for logging; the manager sorts out what actually runs.
    """

    found: list[PluginProtocol] = []

    found.extend(_from_package(package))

    if directory:
        found.extend(_from_directory(directory))

    return found


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def _from_package(package: str) -> list[PluginProtocol]:
    """Plugins inside an importable package, such as plugins.builtins."""

    try:
        module = importlib.import_module(package)
    except ImportError:
        # No bundled plugins is a normal state, not an error.
        logger.debug("No plugin package at %s", package)
        return []

    paths = getattr(module, "__path__", None)

    if not paths:
        return []

    found: list[PluginProtocol] = []

    for info in pkgutil.iter_modules(paths):

        if info.name.startswith(PRIVATE_PREFIX):
            continue

        imported = _import(f"{package}.{info.name}")

        if imported is not None:
            found.extend(plugins_in(imported))

    return found


def _from_directory(directory: str | Path) -> list[PluginProtocol]:
    """
    Plugins in a directory the user named.

    Each `*.py` file and each package with an `__init__.py` is imported
    under a `aura_plugin_` prefix, so a file called `json.py` cannot
    shadow the standard library for the rest of the process.
    """

    root = Path(directory).expanduser()

    if not root.is_dir():
        logger.warning("Plugin directory not found: %s", root)
        return []

    found: list[PluginProtocol] = []

    for path in sorted(root.iterdir()):

        if path.name.startswith(PRIVATE_PREFIX):
            continue

        if path.is_dir():
            entry = path / "__init__.py"
        elif path.suffix == ".py":
            entry = path
        else:
            continue

        if not entry.is_file():
            continue

        imported = _import_file(f"aura_plugin_{path.stem}", entry)

        if imported is not None:
            found.extend(plugins_in(imported))

    return found


# ---------------------------------------------------------------------------
# Importing
# ---------------------------------------------------------------------------


def _import(name: str):
    """Import a module by name, or None if it refused to load."""

    try:
        return importlib.import_module(name)

    except Exception as error:
        # Not just ImportError: a plugin's module body runs on import and
        # may raise anything at all.
        logger.warning("Could not import plugin module %s: %s", name, error)
        return None


def _import_file(name: str, path: Path):
    """Import a module from an explicit path, or None if it refused."""

    if name in sys.modules:
        return sys.modules[name]

    try:
        spec = importlib.util.spec_from_file_location(name, path)

        if spec is None or spec.loader is None:
            logger.warning("Could not load plugin from %s", path)
            return None

        module = importlib.util.module_from_spec(spec)

        # Registered before execution so a plugin split across files can
        # import its own siblings.
        sys.modules[name] = module

        spec.loader.exec_module(module)

        return module

    except Exception as error:
        sys.modules.pop(name, None)

        logger.warning("Could not import plugin at %s: %s", path, error)
        return None


# ---------------------------------------------------------------------------
# Reading a module
# ---------------------------------------------------------------------------


def plugins_in(module) -> list[PluginProtocol]:
    """
    The plugin instances a module advertises.

    Public because it is the one piece worth testing directly, and
    because a host embedding Aura may have a module in hand already.
    """

    instance = getattr(module, INSTANCE_ATTRIBUTE, None)

    if instance is not None:

        if isinstance(instance, PluginProtocol):
            return [instance]

        logger.warning(
            "%s.%s is not a plugin (needs name, version, initialize, "
            "shutdown)",
            module.__name__,
            INSTANCE_ATTRIBUTE,
        )
        return []

    factory = getattr(module, FACTORY_ATTRIBUTE, None)

    if callable(factory):
        return _from_factory(module, factory)

    return _by_scanning(module)


def _from_factory(module, factory) -> list[PluginProtocol]:

    try:
        instance = factory()

    except Exception as error:
        logger.warning(
            "%s.%s() failed: %s", module.__name__, FACTORY_ATTRIBUTE, error
        )
        return []

    if isinstance(instance, PluginProtocol):
        return [instance]

    logger.warning(
        "%s.%s() did not return a plugin", module.__name__, FACTORY_ATTRIBUTE
    )
    return []


def _by_scanning(module) -> list[PluginProtocol]:
    """
    Plugin subclasses defined in this module.

    `__module__` is checked so that `from plugins.base import Plugin` at
    the top of a file does not register the base class, and an imported
    plugin from elsewhere is not counted twice.
    """

    found: list[PluginProtocol] = []

    for _, value in inspect.getmembers(module, inspect.isclass):

        if not issubclass(value, Plugin) or value is Plugin:
            continue

        if value.__module__ != module.__name__:
            continue

        if inspect.isabstract(value):
            continue

        try:
            found.append(value())

        except Exception as error:
            logger.warning(
                "Could not construct plugin %s: %s", value.__name__, error
            )

    return found
