"""
Plugin system tests.

Covers discovery, registration, lifecycle, enablement, and the example
bundled plugin. The three things Section 8 adds:

    PluginProtocol      structural contract, narrow by design
    discovery           three ways to advertise, automatic or explicit
    manager lifecycle   initialize → shutdown, reverse order

Everything degrades. A malformed plugin is logged and skipped, not a reason
to stop Aura from starting.
"""

import tempfile
from pathlib import Path
from textwrap import dedent
from types import ModuleType

import pytest

from events.bus import EventBus
from events.types import ResponseEvent, UserInputEvent

from plugins.base import Plugin, PluginContext, PluginProtocol
from plugins.discovery import discover, plugins_in
from plugins.factory import build_manager, build_plugins
from plugins.manager import PluginManager
from tools.base import ToolRisk
from tools.registry import ToolRegistry


# ----------------------------------------------------------------------
# Doubles
# ----------------------------------------------------------------------


class MinimalPlugin:
    """
    Structural, not inherited.

    The whole point of Section 8: a plugin written against the protocol
    needs nothing from plugins.base except the risk level for its tools.
    """

    name = "minimal"
    version = "1.0.0"

    def __init__(self):
        self.initialized = False
        self.shut_down = False

    def initialize(self, context: PluginContext) -> None:
        self.initialized = True

    def shutdown(self) -> None:
        self.shut_down = True


class CountingPlugin(Plugin):
    """The ordinary path: inherits the base class."""

    name = "counter"
    version = "1.0.0"

    def __init__(self):
        self.init_count = 0
        self.shutdown_count = 0
        self.bus = None

    def initialize(self, context: PluginContext) -> None:
        self.init_count += 1
        self.bus = context.bus

    def shutdown(self) -> None:
        self.shutdown_count += 1


class BrokenPlugin(Plugin):
    """Always raises during initialize."""

    name = "broken"
    version = "1.0.0"

    def initialize(self, context: PluginContext) -> None:
        raise RuntimeError("initialization always fails")


class ToolRegisteringPlugin(Plugin):
    """
    Registers a tool on startup.

    The tool is structural - `name`, `risk` and `execute` only - which
    is the Section 7 promise being cashed in.
    """

    name = "tool_plugin"
    version = "1.0.0"

    def __init__(self):
        self.registry = None
        self.tool = None

    def initialize(self, context: PluginContext) -> None:
        if context.tools is None:
            return

        self.tool = SimpleTool()
        context.tools.register(self.tool)
        self.registry = context.tools

    def shutdown(self) -> None:
        if self.registry and self.tool:
            self.registry.unregister(self.tool.name)


class SimpleTool:
    """A tool with no base class."""

    name = "simple"
    risk = ToolRisk.SAFE

    def execute(self, **arguments) -> str:
        return "ran"


# ----------------------------------------------------------------------
# The Protocol
# ----------------------------------------------------------------------


def test_the_base_class_satisfies_the_protocol():
    assert isinstance(CountingPlugin(), PluginProtocol)


def test_a_plugin_needs_no_base_class_at_all():
    """
    The whole point of Section 8. A plugin can ship this object without
    importing anything from plugins/ except the Protocol.
    """

    plugin = MinimalPlugin()

    assert isinstance(plugin, PluginProtocol)


def test_something_that_is_not_a_plugin_is_refused_at_registration():

    class NotAPlugin:
        name = "impostor"
        version = "1.0.0"

    with pytest.raises(ValueError, match="PluginProtocol"):
        PluginManager().register(NotAPlugin())


def test_an_unnamed_plugin_is_refused():

    class Nameless(Plugin):
        name = ""
        version = "1.0.0"

        def initialize(self, context):
            pass

    with pytest.raises(ValueError, match="must have a name"):
        PluginManager().register(Nameless())


def test_a_duplicate_name_is_refused():
    """
    Two plugins called "weather" is a configuration mistake. Picking one
    arbitrarily hides it.
    """

    manager = PluginManager([CountingPlugin()])

    with pytest.raises(ValueError, match="already registered"):
        manager.register(CountingPlugin())


# ----------------------------------------------------------------------
# Manager lifecycle
# ----------------------------------------------------------------------


def test_a_fresh_manager_has_nothing_enabled():
    manager = PluginManager([CountingPlugin()])

    assert not manager.is_enabled("counter")
    assert manager.status()["counter"] == "disabled"


def test_enable_marks_a_plugin_as_enabled():
    manager = PluginManager([CountingPlugin()])

    manager.enable("counter")

    assert manager.is_enabled("counter")
    assert manager.status()["counter"] == "enabled"


def test_enable_all_enables_everything():
    manager = PluginManager([CountingPlugin(), MinimalPlugin()])

    manager.enable_all()

    assert manager.is_enabled("counter")
    assert manager.is_enabled("minimal")


def test_disable_removes_from_enabled():
    manager = PluginManager([CountingPlugin()])

    manager.enable("counter")
    manager.disable("counter")

    assert not manager.is_enabled("counter")


def test_initialize_runs_every_enabled_plugin():
    plugin = CountingPlugin()
    manager = PluginManager([plugin])

    manager.enable("counter")
    manager.initialize(PluginContext())

    assert plugin.init_count == 1


def test_initialize_skips_disabled_plugins():
    plugin = CountingPlugin()
    manager = PluginManager([plugin])

    manager.initialize(PluginContext())

    assert plugin.init_count == 0


def test_initialize_passes_the_context():
    plugin = CountingPlugin()
    bus = EventBus()
    manager = PluginManager([plugin])

    manager.enable("counter")
    manager.initialize(PluginContext(bus=bus))

    assert plugin.bus is bus


def test_a_plugin_that_raises_during_initialize_is_marked_broken():
    broken = BrokenPlugin()
    manager = PluginManager([broken])

    manager.enable("broken")
    manager.initialize(PluginContext())

    assert manager.is_broken("broken")
    assert manager.status()["broken"] == "broken"


def test_a_broken_plugin_does_not_stop_the_rest():
    broken = BrokenPlugin()
    working = CountingPlugin()
    manager = PluginManager([broken, working])

    manager.enable_all()
    manager.initialize(PluginContext())

    assert manager.is_broken("broken")
    assert working.init_count == 1


def test_shutdown_runs_in_reverse_initialization_order():
    """
    A plugin that depends on another's registration finishes last.
    """

    order = []

    class Recording(Plugin):
        version = "1.0.0"

        def __init__(self, name):
            self.name = name

        def initialize(self, context):
            pass

        def shutdown(self):
            order.append(self.name)

    manager = PluginManager([Recording("first"), Recording("second")])

    manager.enable_all()
    manager.initialize(PluginContext())

    manager.shutdown()

    # Initialized first, second; shut down second, first
    assert order == ["second", "first"]


def test_shutdown_skips_broken_plugins():
    broken = BrokenPlugin()
    manager = PluginManager([broken])

    manager.enable("broken")
    manager.initialize(PluginContext())

    # Does not raise
    manager.shutdown()


def test_a_plugin_that_raises_during_shutdown_does_not_stop_the_rest():

    class CrashingShutdown(Plugin):
        name = "crashy"
        version = "1.0.0"

        def initialize(self, context):
            pass

        def shutdown(self):
            raise RuntimeError("shutdown always fails")

    crashy = CrashingShutdown()
    working = CountingPlugin()

    manager = PluginManager([crashy, working])

    manager.enable_all()
    manager.initialize(PluginContext())

    manager.shutdown()

    assert working.shutdown_count == 1


# ----------------------------------------------------------------------
# Context narrowing
# ----------------------------------------------------------------------


def test_each_plugin_receives_its_own_config():
    """
    One plugin cannot read another's settings, because it never receives
    the full map.
    """

    class ConfigReading(Plugin):
        name = "reader"
        version = "1.0.0"

        def __init__(self):
            self.config = None

        def initialize(self, context: PluginContext) -> None:
            self.config = context.config

    plugin = ConfigReading()
    manager = PluginManager([plugin])

    manager.enable("reader")
    manager.initialize(
        PluginContext(
            config={
                "reader": {"setting": "value"},
                "other": {"secret": "hidden"},
            }
        )
    )

    assert plugin.config == {"setting": "value"}
    assert "other" not in plugin.config


# ----------------------------------------------------------------------
# Tool registration
# ----------------------------------------------------------------------


def test_a_plugin_can_register_a_tool():
    plugin = ToolRegisteringPlugin()
    registry = ToolRegistry()

    manager = PluginManager([plugin])

    manager.enable("tool_plugin")
    manager.initialize(PluginContext(tools=registry))

    assert "simple" in registry.names()


def test_the_tool_is_unregistered_on_shutdown():
    plugin = ToolRegisteringPlugin()
    registry = ToolRegistry()

    manager = PluginManager([plugin])

    manager.enable("tool_plugin")
    manager.initialize(PluginContext(tools=registry))

    manager.shutdown()

    assert "simple" not in registry.names()


# ----------------------------------------------------------------------
# Discovery
# ----------------------------------------------------------------------


def fake_module(name: str = "fake_plugin_module", **attributes):
    """
    A real module object, because that is what discovery is handed.

    Built rather than written to disk: `plugins_in` reads attributes off a
    module and never touches the filesystem, so a file would only add a
    tempdir to clean up.
    """

    module = ModuleType(name)

    for key, value in attributes.items():
        setattr(module, key, value)

    return module


def test_plugins_in_reads_an_instance():
    module = fake_module(PLUGIN=MinimalPlugin())

    found = plugins_in(module)

    assert [p.name for p in found] == ["minimal"]


def test_plugins_in_reads_a_factory():
    """The factory form, for a plugin whose construction needs to run."""

    module = fake_module(plugin=lambda: MinimalPlugin())

    found = plugins_in(module)

    assert [p.name for p in found] == ["minimal"]


def test_an_instance_that_is_not_a_plugin_is_ignored():
    module = fake_module(PLUGIN=object())

    assert plugins_in(module) == []


def test_a_factory_that_raises_is_ignored():

    def plugin():
        raise RuntimeError("construction failed")

    assert plugins_in(fake_module(plugin=plugin)) == []


# ----------------------------------------------------------------------
# The composition root
# ----------------------------------------------------------------------
#
# Phase 11's lesson, applied to plugins: every test above builds its own
# manager, so dropping the `_build_plugins` call from
# `launcher/services.py::build_services` - or passing it the wrong registry
# - would leave this file green while no plugin ever ran in a real process.
# These tests drive the one composition root the process actually uses.


def _echo_plugin_file(directory: Path) -> None:
    """A plugin on disk that registers one SAFE tool, in the shipped shape."""

    (directory / "echo_plugin.py").write_text(
        dedent(
            """
            from tools.base import ToolRisk


            class EchoTool:
                name = "echo_tool"
                capability = "echo"
                description = "Returns its argument back."
                risk = ToolRisk.SAFE

                def execute(self, **arguments):
                    return arguments.get("text", "")


            class EchoPlugin:
                name = "echo"
                version = "1.0.0"

                def initialize(self, context):
                    self._registry = None
                    if context.tools is not None:
                        context.tools.register(EchoTool())
                        self._registry = context.tools

                def shutdown(self):
                    if self._registry is not None:
                        self._registry.unregister("echo_tool")
                        self._registry = None


            def plugin():
                return EchoPlugin()
            """
        ).lstrip(),
        encoding="utf-8",
    )


def _root_config(directory: Path, allowed: list[str]) -> dict:
    return {
        # Everything optional off; this is about plugin wiring.
        "voice": {"tts": {"enabled": False}, "stt": {"enabled": False}},
        "vision": {"enabled": False},
        "avatar": {"enabled": False},
        "memory": {"recall": False, "profile": False, "pipeline": False},
        "tools": {"enabled": True, "allowed": allowed},
        "plugins": {
            "enabled": ["echo"],
            "directory": str(directory),
        },
    }


def test_the_composition_root_initializes_plugins_and_their_tool_reaches_the_executor(
    tmp_path,
):
    """
    The wiring, not the function.

    A plugin discovered, enabled and initialized by the real
    `build_services`, whose registered tool is then offered through the one
    executor the process runs and runnable under the owner's `tools.allowed`.
    Any break in that chain - discovery skipped, the registry not handed
    over, initialization never called - fails here rather than only in a
    test-built manager.
    """

    from launcher.services import build_services

    _echo_plugin_file(tmp_path)

    services = build_services(config=_root_config(tmp_path, ["echo_tool"]))

    try:
        assert services.plugins is not None
        assert services.plugins.is_enabled("echo")
        assert not services.plugins.is_broken("echo")

        # Registered into the same registry the executor serves...
        assert services.tools.registry.has("echo_tool")

        # ...and reachable through the permission gate, because the owner
        # named it in tools.allowed.
        assert "echo_tool" in services.tools.available()

        result = services.tools.execute("echo_tool", {"text": "ping"})

        assert result.ok
        assert result.output == "ping"
    finally:
        if services.plugins is not None:
            services.plugins.shutdown()


def test_a_plugin_tool_the_owner_did_not_allow_is_registered_but_not_offered(
    tmp_path,
):
    """
    Registration is not permission.

    The plugin's tool lands in the registry either way; whether the model
    is ever offered it stays the owner's decision in `tools.allowed`. A
    plugin may add a capability; it may not grant one.
    """

    from launcher.services import build_services

    _echo_plugin_file(tmp_path)

    services = build_services(config=_root_config(tmp_path, ["current_time"]))

    try:
        assert services.tools.registry.has("echo_tool")

        assert "echo_tool" not in services.tools.available()
    finally:
        if services.plugins is not None:
            services.plugins.shutdown()


def test_plugins_left_disabled_the_composition_root_builds_no_manager(tmp_path):
    """
    The stock install.

    `plugins.enabled` empty means no manager at all - `Services.plugins`
    stays None and nothing in `plugins/` runs - which is what keeps a
    default deployment free of third-party code.
    """

    from launcher.services import build_services

    _echo_plugin_file(tmp_path)

    config = _root_config(tmp_path, [])
    config["plugins"]["enabled"] = []

    services = build_services(config=config)

    assert services.plugins is None


def test_a_factory_returning_a_non_plugin_is_ignored():
    assert plugins_in(fake_module(plugin=lambda: object())) == []


def test_plugins_in_scans_for_subclasses():
    """The last resort: a module that advertises nothing explicitly."""

    class Discovered(Plugin):
        name = "discovered"
        version = "1.0.0"

        def initialize(self, context):
            pass

    Discovered.__module__ = "scanned_module"

    module = fake_module("scanned_module", Discovered=Discovered)

    found = plugins_in(module)

    assert [p.name for p in found] == ["discovered"]


def test_plugins_in_ignores_imported_subclasses():
    """
    `from plugins.base import Plugin` at the top of a file must not
    register the base class, and a plugin imported from elsewhere must
    not be counted twice.
    """

    module = fake_module(
        "scanned_module",
        Plugin=Plugin,
        CountingPlugin=CountingPlugin,
    )

    assert plugins_in(module) == []


def test_a_plugin_whose_constructor_raises_is_skipped():

    class Awkward(Plugin):
        name = "awkward"
        version = "1.0.0"

        def __init__(self):
            raise RuntimeError("cannot construct")

        def initialize(self, context):
            pass

    Awkward.__module__ = "scanned_module"

    assert plugins_in(fake_module("scanned_module", Awkward=Awkward)) == []


def test_discover_finds_bundled_plugins():
    """
    At least one plugin exists in plugins/builtins, and discovery sees it.
    """

    found = discover(package="plugins.builtins")

    names = [p.name for p in found]

    assert "session_stats" in names


def test_discover_finds_plugins_in_a_directory():
    """
    A user-written plugin in an external directory is discovered and
    loaded under an `aura_plugin_` prefix.
    """

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        plugin_file = root / "custom.py"

        plugin_file.write_text(
            dedent("""
                from plugins.base import Plugin

                class CustomPlugin(Plugin):
                    name = "custom"
                    version = "1.0.0"

                    def initialize(self, context):
                        pass
            """),
            encoding="utf-8",
        )

        found = discover(directory=root)

        names = [p.name for p in found]

        assert "custom" in names


# ----------------------------------------------------------------------
# Factory
# ----------------------------------------------------------------------


def test_build_manager_discovers_and_registers():
    manager = build_manager({})

    # At least the bundled plugin
    assert len(manager) > 0
    assert "session_stats" in manager.names()


def test_build_plugins_enables_when_configured():
    bus = EventBus()
    registry = ToolRegistry()

    config = {
        "plugins": {
            "enabled": ["session_stats"],
        }
    }

    manager = build_plugins(config, bus=bus, tools=registry)

    assert manager.is_enabled("session_stats")


def test_build_plugins_enables_all_when_told_to():
    config = {
        "plugins": {
            "enabled": True,
        }
    }

    manager = build_plugins(config)

    assert len(manager.enabled) == len(manager.plugins)


def test_build_plugins_enables_nothing_by_default():
    manager = build_plugins({})

    # Discovered but not enabled
    assert len(manager.plugins) > 0
    assert len(manager.enabled) == 0


def test_an_empty_enabled_list_is_a_deliberate_none():
    """
    Different from leaving the key absent: the former is "I looked and
    chose none", the latter is "I have not configured this yet". Both
    enable nothing; only the shape of the config differs.
    """

    manager = build_plugins({"plugins": {"enabled": []}})

    assert len(manager.enabled) == 0
    assert len(manager.plugins) > 0


# ----------------------------------------------------------------------
# Bundled example: session_stats
# ----------------------------------------------------------------------


def test_session_stats_exists():
    """The bundled plugin is discoverable."""

    found = discover(package="plugins.builtins")

    names = [p.name for p in found]

    assert "session_stats" in names


def test_session_stats_counts_turns():
    from plugins.builtins.session_stats import SessionStatsPlugin

    plugin = SessionStatsPlugin(clock=lambda: 0.0)
    bus = EventBus()

    plugin.initialize(PluginContext(bus=bus))

    bus.publish(UserInputEvent(text="hello"))
    bus.publish(ResponseEvent(text="hi"))

    assert plugin.turns == 1
    assert plugin.replies == 1


def test_session_stats_registers_a_tool():
    from plugins.builtins.session_stats import SessionStatsPlugin

    plugin = SessionStatsPlugin(clock=lambda: 0.0)
    registry = ToolRegistry()

    plugin.initialize(PluginContext(tools=registry))

    assert "session_stats" in registry.names()


def test_session_stats_tool_reports_the_count():
    """
    The clock is injected, so a deadline test does not have to wait for
    time to actually pass. `initialize` restarts it, hence two readings.
    """

    from plugins.builtins.session_stats import SessionStatsPlugin

    # Three readings, because the clock is consulted three times:
    # constructing the plugin, initializing it, and asking for elapsed.
    # Only the last two matter - `initialize` restarts the timer.
    readings = iter([0.0, 100.0, 110.0])

    plugin = SessionStatsPlugin(clock=lambda: next(readings, 110.0))
    registry = ToolRegistry()
    bus = EventBus()

    plugin.initialize(PluginContext(bus=bus, tools=registry))

    bus.publish(UserInputEvent(text="hello"))
    bus.publish(ResponseEvent(text="hi"))

    output = registry.get("session_stats").execute()

    assert "1 turn" in output
    assert "10s" in output


def test_session_stats_unsubscribes_on_shutdown():
    from plugins.builtins.session_stats import SessionStatsPlugin

    plugin = SessionStatsPlugin(clock=lambda: 0.0)
    bus = EventBus()

    plugin.initialize(PluginContext(bus=bus))

    initial = bus.handler_count(UserInputEvent)

    plugin.shutdown()

    after = bus.handler_count(UserInputEvent)

    assert after < initial


def test_session_stats_unregisters_on_shutdown():
    from plugins.builtins.session_stats import SessionStatsPlugin

    plugin = SessionStatsPlugin(clock=lambda: 0.0)
    registry = ToolRegistry()

    plugin.initialize(PluginContext(tools=registry))

    plugin.shutdown()

    assert "session_stats" not in registry.names()


def test_session_stats_with_no_bus_or_tools_does_not_crash():
    from plugins.builtins.session_stats import SessionStatsPlugin

    plugin = SessionStatsPlugin(clock=lambda: 0.0)

    plugin.initialize(PluginContext())
    plugin.shutdown()
