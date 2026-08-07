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

    roots = config.get("allowed_paths") or []

    if roots:
        from tools.builtins.filesystem import (
            ListDirectoryTool,
            ReadFileTool,
        )

        tools.append(ReadFileTool(roots))
        tools.append(ListDirectoryTool(roots))

    applications = config.get("applications") or {}

    if applications:
        from tools.builtins.apps import OpenApplicationTool

        tools.append(OpenApplicationTool(applications))

    return tools


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

    return executor
