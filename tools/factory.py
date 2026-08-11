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


def build_registry(config: dict | None = None) -> ToolRegistry:

    config = config or {}

    registry = ToolRegistry()

    for tool in _builtin_tools(config):

        try:
            registry.register(tool)
        except ValueError as error:
            logger.warning("Could not register tool: %s", error)

    return registry


def _builtin_tools(config: dict) -> list[ToolProtocol]:

    tools: list[ToolProtocol] = []

    from tools.builtins.clock import CurrentTimeTool

    tools.append(CurrentTimeTool())

    roots = _list_setting(config, "allowed_paths")

    if roots:
        from tools.builtins.filesystem import (
            ListDirectoryTool,
            ReadFileTool,
        )

        tools.append(ReadFileTool(roots))
        tools.append(ListDirectoryTool(roots))

    applications = _mapping_setting(config, "applications")

    if applications:
        from tools.builtins.apps import OpenApplicationTool

        tools.append(OpenApplicationTool(applications))

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



def build_tools(
    config: dict | None = None,
    events=None,
    confirm=None,
) -> ToolExecutor:
    """
    Build the executor Aura will use.

    `confirm` is how a human says yes to a risky call. Leaving it None
    means nothing above the auto approved risk levels can ever run.
    """

    config = config or {}

    executor = ToolExecutor(
        registry=build_registry(config),
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
