"""
Tool construction.

Builds a registry and an executor from the `tools:` config section.

The registry is populated with everything Aura knows how to do; the
policy decides which of those the user has actually permitted. Keeping
registration and permission separate means an unlisted tool is visible
and inert rather than absent - the difference between "you have not
allowed this" and a confusing "unknown tool".
"""

from core.logger import logger
from tools.base import ToolProtocol
from tools.executor import ToolExecutor, ToolPolicy
from tools.registry import ToolRegistry


def build_registry(
    config: dict | None = None,
    memory=None,
    vision=None,
) -> ToolRegistry:
    """
    `memory` is a MemoryPipeline and `vision` is a VisionManager. Both are
    optional for the same reason `allowed_paths` is: a tool whose
    dependency is absent is not registered, so it is missing rather than
    present and broken.

    `vision` is the manager this process already built, not one made
    here. Two managers would mean two capture backends, two processor
    chains and two answers to "what is on screen".
    """

    config = config or {}
    register_core_capabilities(config)

    registry = ToolRegistry()

    for tool in _builtin_tools(config, memory, vision):

        try:
            registry.register(tool)
        except ValueError as error:
            logger.warning("Could not register tool: %s", error)

    return registry


def _builtin_tools(
    config: dict,
    memory=None,
    vision=None,
) -> list[ToolProtocol]:

    tools: list[ToolProtocol] = []

    from tools.builtins.clock import CurrentTimeTool
    from tools.builtins.chat import ReactToMessageTool

    tools.append(CurrentTimeTool())
    tools.append(ReactToMessageTool())

    roots = _list_setting(config, "allowed_paths")

    if roots:
        from tools.builtins.filesystem import (
            ListDirectoryTool,
            ReadFileTool,
        )

        tools.append(ReadFileTool(roots))
        tools.append(ListDirectoryTool(roots))

    writable = _list_setting(config, "writable_paths")

    if writable:
        # A second list, read separately, and deliberately not defaulted
        # from `allowed_paths`. Section 2 cuts both ways: an owner who
        # listed a directory so Aura could read their notes did not
        # thereby say she may overwrite them, and inheriting the read
        # roots here would grant a capability they never asked for. An
        # owner who wants both lists the directory in both places.
        #
        # Gated on the list being non-empty for the same reason
        # `applications` and `commands` are: with no writable root every
        # call is a PermissionError, so registering four tools would
        # advertise capabilities that cannot work and the model would
        # spend turns finding that out.
        from tools.builtins.filesystem import (
            AppendToFileTool,
            CreateDirectoryTool,
            DeleteFileTool,
            WriteFileTool,
        )

        tools.append(WriteFileTool(writable))
        tools.append(AppendToFileTool(writable))
        tools.append(CreateDirectoryTool(writable))
        tools.append(DeleteFileTool(writable))

        # Section 24's screenshots, gated on the same list and for the
        # same reason: the picture is a file appearing on disk, so the
        # grant that governs it is the one the owner gave for writing
        # files. Inventing a separate destination setting would be a
        # second answer to a question `writable_paths` already answers,
        # and two answers can disagree.
        #
        # Gated a second time on a capture backend existing, which is the
        # factory's standing rule rather than a special case: a tool whose
        # dependency is absent is not registered, so it is missing rather
        # than present and failing. On a machine with no display - a
        # headless server, a container - `default_screen_capture()`
        # returns None and the model is never offered a screenshot it
        # cannot take.
        from vision.capture import default_screen_capture

        if default_screen_capture() is not None:

            from tools.builtins.screen import ScreenshotTool

            tools.append(ScreenshotTool(writable))

        else:
            logger.info(
                "Screen capture unavailable, take_screenshot not registered"
            )

    if memory is not None:
        # Section 17: the semantic tier's only runtime caller. Gated on the
        # pipeline rather than on a config flag, because the pipeline is
        # itself gated on `memory.pipeline` upstream - a second switch here
        # would let the two disagree, and the failure would be a tool that
        # registers and then writes to nothing.
        from tools.builtins.memory import RememberTool

        tools.append(RememberTool(memory))

    applications = _mapping_setting(config, "applications")

    if applications:
        from tools.builtins.apps import OpenApplicationTool

        tools.append(OpenApplicationTool(applications))

    commands = _mapping_setting(config, "commands")

    if commands:
        # Gated on the owner having declared something, exactly as
        # `applications` is, and for a stronger version of the same reason.
        # With no declarations `run_command` can do nothing at all - every
        # name it is asked for is a PermissionError - so registering it
        # would advertise a capability to the model that does not exist,
        # and the model would spend turns discovering that. A tool that is
        # absent is a clearer answer than a tool that refuses everything.
        #
        # The `available` check below would keep it unregistered on its own,
        # so this outer gate is not what stops the tool existing. What it
        # stops is the warning: without it, a stock server logs "none of the
        # 0 declared command(s) could be used" on every startup, which
        # reports a problem the owner does not have in a log where a real
        # one has to stand out.
        from tools.builtins.commands import RunCommandTool

        tool = RunCommandTool(commands)

        if tool.available:
            tools.append(tool)
        else:
            # Declarations existed and none of them survived validation.
            # `_normalise` has already said why, per command, at warning
            # level; this says what it cost, because "no commands are
            # usable" is not something the per-command warnings add up to
            # on their own.
            logger.warning(
                "run_command not registered: none of the %d declared "
                "command(s) could be used",
                len(commands),
            )

    # Section 19's on-demand half. Gated on vision being switched on
    # rather than on the manager merely existing: `VisionManager` is
    # always built, and `refresh()` does not consult `enabled` - only
    # `get_context()` does - so a tool registered around a disabled
    # manager would be a way to look at a screen the owner said not to
    # look at. Section 2 is about the owner's configuration being obeyed,
    # not only about it being editable.
    #
    # Registered is still not enabled. `describe_screen` is absent from
    # `tools.allowed` in the shipped config, so this grants nothing on
    # its own; it takes the owner naming it, and then - being SENSITIVE
    # at best - a confirmation per call unless they widen
    # `tools.auto_approve` too.
    if vision is not None and vision.is_available():

        from tools.builtins.vision import DescribeScreenTool

        tools.append(DescribeScreenTool(vision))

    else:
        logger.debug(
            "describe_screen not registered: vision is off"
        )

    tools.extend(_pc_tools())

    return tools


def _pc_tools() -> list[ToolProtocol]:
    """
    Section 24's PC layer, as much of it as this machine can actually do.

    Registered, not enabled. Every one of these is absent from
    `tools.allowed` in the shipped config, so registering them grants
    nothing on its own - the owner turns each on by name, and gate three
    in the executor is what stands between a registered tool and a running
    one. Section 2 cuts both ways here: the owner must be able to enable
    these freely, and must not find them already enabled without having
    said so.

    No config section gates them, because there is no configuration to
    get wrong. `open_application` needs an owner-written mapping of
    nicknames to commands and so is gated on having one; reading this
    machine's own properties needs nothing but the machine.

    What is gated is the reading underneath. Section 34 in miniature: a
    process list with no process source, or a window list on a platform
    with no windows to enumerate, would register and then answer every
    question with a shrug - so each is offered only where the source that
    answers it exists.
    """

    tools: list[ToolProtocol] = []

    from tools.builtins.system import (
        ListProcessesTool,
        SystemInformationTool,
        default_process_source,
    )

    # No gate: `platform`, `os` and `shutil` are standard library and
    # answer on every platform Aura runs on. A field they cannot fill is
    # left out of the description rather than reported as a zero.
    tools.append(SystemInformationTool())

    processes = default_process_source()

    if processes is not None:
        tools.append(ListProcessesTool(processes))
    else:
        logger.debug(
            "list_processes not registered: no process source on this platform"
        )

    from tools.builtins.desktop import (
        FocusWindowTool,
        ListWindowsTool,
        default_window_source,
    )

    windows = default_window_source()

    if windows is not None:
        # One source object, shared. Two would be two enumerations of the
        # same desktop, and `focus_window` matching against a listing the
        # owner never saw is how the wrong window gets brought forward.
        tools.append(ListWindowsTool(windows))
        tools.append(FocusWindowTool(windows))
    else:
        logger.debug(
            "list_windows and focus_window not registered: no window source"
        )

    from tools.builtins.input import default_input_synthesizer

    synthesizer = default_input_synthesizer()

    if synthesizer is not None:

        from tools.builtins.input import (
            ClickMouseTool,
            MoveMouseTool,
            PressKeysTool,
            TypeTextTool,
        )

        # The same window source the desktop tools got, deliberately. The
        # `window` argument on these three is a safety guard - "only type
        # if this window is in front" - and a guard reading a second,
        # separately-timed enumeration would be answering about a desktop
        # nobody else saw. Passing None here would not disable the guard,
        # it would make the guard refuse, which is the right failure but
        # the wrong reason on a machine that can read its own windows.
        tools.append(MoveMouseTool(synthesizer, windows))
        tools.append(ClickMouseTool(synthesizer, windows))
        tools.append(TypeTextTool(synthesizer, windows))
        tools.append(PressKeysTool(synthesizer, windows))

    else:
        logger.debug(
            "move_mouse, click_mouse, type_text and press_keys not "
            "registered: input synthesis is unavailable on this platform"
        )

    # Android tools attached via the shared device registry
    try:
        from server.routes.agent import get_device_registry
        device_reg = get_device_registry()
        existing_names = {getattr(t, "name", "") for t in tools}
        for tool in device_reg.all():
            if tool.name not in existing_names:
                tools.append(tool)
    except Exception as error:
        logger.debug("Android tools not attached to builtin tools: %s", error)

    return tools


# ----------------------------------------------------------------------
# Configuration, checked rather than assumed
#
# A tool that never registers because its config section is the wrong
# shape is invisible: nothing fails, the tool simply is not there, and
# `applications: []` sat in config.yaml doing exactly that (AURA-P0-004).
# An empty list and an empty mapping are both falsy, so the bug survived
# every `or {}` guard in the codebase.
# ----------------------------------------------------------------------

def _mapping_setting(config: dict, key: str) -> dict:
    """A `key: {...}` section, or an empty one with a warning saying why."""

    value = config.get(key)

    if value is None or value == [] or value == {}:
        return {}

    if not isinstance(value, dict):
        logger.warning(
            "tools.%s must be a mapping of name to command, not %s - "
            "write `%s: {}` for none. Ignoring it.",
            key,
            type(value).__name__,
            key,
        )
        return {}

    return value


def _list_setting(config: dict, key: str) -> list:
    """A `key: [...]` section, or an empty one with a warning saying why."""

    value = config.get(key)

    if value is None or value == [] or value == {}:
        return []

    if isinstance(value, (str, dict)):
        logger.warning(
            "tools.%s must be a list, not %s. Ignoring it.",
            key,
            type(value).__name__,
        )
        return []

    return list(value)


def _warn_about_policy(executor: ToolExecutor) -> None:
    """
    Say what the policy will and will not permit, before anything asks.

    Both of these are silent failures otherwise: a user who switched
    `enabled` on and stopped there gets a system that grants nothing, and
    a misspelled name in `allowed` grants nothing *and* looks correct.
    """

    policy = executor.policy

    if not policy.enabled:
        return

    if not policy.allowed:
        logger.warning(
            "tools.enabled is true but tools.allowed is empty, so no tool "
            "can run. Name the tools you want in tools.allowed."
        )
        return

    unknown = sorted(
        name for name in policy.allowed if not executor.registry.has(name)
    )

    if unknown:
        logger.warning(
            "tools.allowed names %s, which %s not registered. Known tools: %s",
            ", ".join(unknown),
            "is" if len(unknown) == 1 else "are",
            ", ".join(executor.registry.names()) or "none",
        )



from core.capabilities.factory import register_core_capabilities

def build_tools(
    config: dict | None = None,
    events=None,
    confirm=None,
    memory=None,
    vision=None,
) -> ToolExecutor:
    """
    Build the executor Aura will use.

    `confirm` is how a human says yes to a risky call. Leaving it None
    means nothing above the auto approved risk levels can ever run.
    """

    config = config or {}
    register_core_capabilities(config)

    executor = ToolExecutor(
        registry=build_registry(config, memory, vision),
        policy=ToolPolicy.from_config(config),
        events=events,
        confirm=confirm,
    )

    if executor.policy.enabled:
        logger.info(
            "Tools enabled: %s",
            ", ".join(executor.available()) or "none allowed",
        )
    else:
        logger.info("Tools disabled")

    _warn_about_policy(executor)

    return executor

