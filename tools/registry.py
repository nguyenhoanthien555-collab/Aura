"""
Tool registry.

A named collection of tools, and nothing more. It does not run anything -
that is ToolExecutor's job, and keeping the two apart means there is
exactly one code path where execution can happen and exactly one place
where permission is checked.
"""

from core.logger import logger
from tools.base import Tool, ToolRisk


class ToolRegistry:

    def __init__(self, tools: list[Tool] | None = None):

        self._tools: dict[str, Tool] = {}

        for tool in tools or []:
            self.register(tool)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, tool: Tool) -> None:
        """
        Add a tool.

        Rejects unnamed tools and duplicate names rather than silently
        shadowing: a tool that quietly replaces another is how a
        "read_file" ends up meaning something unexpected.
        """

        name = (getattr(tool, "name", "") or "").strip()

        if not name:
            raise ValueError("Tool must have a name")

        if name in self._tools:
            raise ValueError(f"Tool already registered: {name}")

        self._tools[name] = tool

        logger.debug("Registered tool: %s (%s)", name, tool.risk.value)

    def unregister(self, name: str) -> bool:

        return self._tools.pop(name, None) is not None

    def clear(self) -> None:
        self._tools.clear()

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return sorted(self._tools)

    def all(self) -> list[Tool]:
        return [self._tools[name] for name in self.names()]

    def by_risk(self, risk: ToolRisk) -> list[Tool]:

        return [tool for tool in self.all() if tool.risk == risk]

    def describe(self) -> str:
        """
        Every tool, as text.

        This is the string that will be dropped into the TOOLS prompt
        section once tool calling is enabled.
        """

        return "\n".join(tool.describe() for tool in self.all())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __iter__(self):
        return iter(self.all())
