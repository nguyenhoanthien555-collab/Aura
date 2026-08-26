"""
Tool registry.

A named collection of tools, and nothing more. It does not run anything -
that is ToolExecutor's job, and keeping the two apart means there is
exactly one code path where execution can happen and exactly one place
where permission is checked.

It holds anything satisfying ToolProtocol, not just subclasses of Tool.
That is what lets a plugin ship a tool without importing this package,
and it is why registration checks the shape at the boundary: a malformed
tool is far easier to diagnose here than halfway through a call.
"""

from core.logger import logger
from tools.base import ToolProtocol, ToolRisk, describe_tool


class ToolRegistry:

    def __init__(self, tools: list[ToolProtocol] | None = None):

        self._tools: dict[str, ToolProtocol] = {}

        for tool in tools or []:
            self.register(tool)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, tool: ToolProtocol) -> None:
        """
        Add a tool.

        Rejects unnamed tools and duplicate names rather than silently
        shadowing: a tool that quietly replaces another is how a
        "read_file" ends up meaning something unexpected.

        The shape is checked here rather than trusted, because this is the
        boundary a plugin arrives through. isinstance against a
        runtime_checkable Protocol confirms the attributes exist; `risk` is
        then checked for what it is, since a Protocol cannot check types
        and an unreadable risk level would bypass the approval gate.
        """

        name = (getattr(tool, "name", "") or "").strip()

        if not name:
            raise ValueError("Tool must have a name")

        if not isinstance(tool, ToolProtocol):
            raise ValueError(
                f"Tool '{name}' does not satisfy ToolProtocol "
                f"(needs name, risk and execute)"
            )

        if not isinstance(tool.risk, ToolRisk):
            raise ValueError(
                f"Tool '{name}' has risk {tool.risk!r}, which is not a "
                f"ToolRisk - the approval gate could not read it"
            )

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

    def get(self, name: str) -> ToolProtocol | None:
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return sorted(self._tools)

    def all(self) -> list[ToolProtocol]:
        return [self._tools[name] for name in self.names()]

    def by_risk(self, risk: ToolRisk) -> list[ToolProtocol]:

        return [tool for tool in self.all() if tool.risk == risk]

    def describe(self) -> str:
        """
        Every tool, as text.

        Every registered tool, with no policy filter - which is what
        makes this the wrong thing to put in a prompt. The TOOLS section
        comes from `ToolExecutor.catalogue()`, which describes only
        `available()`, because a model offered a tool the allow list
        forbids will request it, be denied, and spend a turn learning
        what the policy already knew.

        Both render through `describe_tool`, so a tool is described in
        one place no matter who asks. This one is for looking at the
        whole registry - diagnostics, and a test that a mixed set comes
        out whole.
        """

        return "\n".join(describe_tool(tool) for tool in self.all())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __iter__(self):
        return iter(self.all())
