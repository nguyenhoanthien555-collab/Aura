"""
Tools.

    ToolProtocol  what a tool is
    ToolRegistry  what exists
    ToolPolicy    what is permitted
    ToolExecutor  the only place anything runs

Nothing in brain/ imports this package. When tool calling is wired into
the conversation, the executor will be injected in through a port, the
same way memory and vision already are.

The dependency arrow points one way, and it points inwards. A tool
imports `tools.base` for convenience or nothing at all if it is written
against ToolProtocol structurally; it never imports brain/, voice/ or
avatar/. That is what keeps a tool something Aura can be granted rather
than something she is built out of.
"""

from tools.base import (
    Parameter,
    Tool,
    ToolProtocol,
    ToolResult,
    ToolRisk,
    describe_tool,
    fail,
    ok,
)
from tools.executor import ToolExecutor, ToolPolicy
from tools.factory import build_tools
from tools.registry import ToolRegistry

__all__ = [
    "Tool",
    "ToolProtocol",
    "ToolRisk",
    "ToolResult",
    "Parameter",
    "ok",
    "fail",
    "describe_tool",
    "ToolRegistry",
    "ToolExecutor",
    "ToolPolicy",
    "build_tools",
]
