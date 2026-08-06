"""
Tools.

    ToolRegistry  what exists
    ToolPolicy    what is permitted
    ToolExecutor  the only place anything runs

Nothing in brain/ imports this package. When tool calling is wired into
the conversation, the executor will be injected in through a port, the
same way memory and vision already are.
"""

from tools.base import Parameter, Tool, ToolResult, ToolRisk, fail, ok
from tools.registry import ToolRegistry
from tools.executor import ToolExecutor, ToolPolicy
from tools.factory import build_tools

__all__ = [
    "Tool",
    "ToolRisk",
    "ToolResult",
    "Parameter",
    "ok",
    "fail",
    "ToolRegistry",
    "ToolExecutor",
    "ToolPolicy",
    "build_tools",
]
