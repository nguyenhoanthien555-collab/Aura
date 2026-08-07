"""
Plugin base types.

A plugin is a self-contained module that extends Aura without modifying
her internals. It sees the event bus, the tool registry and its own
configuration slice — nothing else.

Two shapes exist for the same reason Tool and ToolProtocol both do: the
ABC is the convenience base class every builtin inherits from, and the
Protocol is the structural contract that lets a plain object with the
right attributes qualify without subclassing anything.

Communication rules:

    MAY                       MUST NOT
    subscribe to events       import brain/
    publish events            import voice/, avatar/, vision/
    register tools            access the LLM directly
    read its own config       touch PromptBuilder
    provide knowledge         modify ChatEngine internals

A plugin that follows these rules can be enabled and disabled without
touching anything outside plugins/. Loading is the manager's job;
registration is the plugin's own, inside `initialize`.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


@runtime_checkable
class PluginProtocol(Protocol):
    """
    Structural contract for anything that can act as a plugin.

    Four members, and the shortness is the design. Every name here is a
    constraint on every plugin that will ever exist, including ones written
    outside this repository:

        name         stable identifier, used in config and logs
        version      informational, for diagnostics
        initialize   called once, with the context it may need
        shutdown     called once, before the process exits

    runtime_checkable so the manager can reject a malformed plugin at the
    boundary rather than failing halfway through a lifecycle call.
    """

    name: str
    version: str

    def initialize(self, context: "PluginContext") -> None:
        """Receive the handles this plugin may use. Called once."""
        ...

    def shutdown(self) -> None:
        """Release anything held. Called once before exit."""
        ...


# ---------------------------------------------------------------------------
# What a plugin receives
# ---------------------------------------------------------------------------


@dataclass
class PluginContext:
    """
    Everything a plugin is allowed to touch.

    Fields are None when the corresponding subsystem is not configured —
    tools is None when tools are disabled, and so on. A plugin that needs a
    tool registry checks for it rather than assuming.

    This is deliberately NOT a Protocol. It is a plain bag of values handed
    to the plugin by the manager; the plugin reads from it, never writes to
    it.
    """

    bus: object | None = None           # EventBus
    tools: object | None = None         # ToolRegistry  (not the executor)
    config: dict = field(default_factory=dict)   # This plugin's own config section

    def with_config(self, config: dict | None) -> "PluginContext":
        """
        The same context, carrying a different config slice.

        How each plugin comes to see only its own settings: the manager
        holds one context and narrows it per plugin. A copy rather than a
        mutation, so one plugin cannot observe another's configuration by
        holding on to the object it was given.
        """

        return PluginContext(
            bus=self.bus,
            tools=self.tools,
            config=config or {},
        )


# ---------------------------------------------------------------------------
# Convenience base class
# ---------------------------------------------------------------------------


class Plugin(ABC):
    """
    Base class for every plugin.

    Convenience, not obligation. Everything the framework requires is in
    PluginProtocol above, so a plugin that would rather not inherit anything
    does not have to; what this class adds is sensible defaults and a
    readable `__repr__`.

    Subclasses set `name` and `version`, then optionally override
    `initialize` and `shutdown`. Both are no-ops by default, so a plugin
    that only needs to register tools on startup writes only `initialize`.
    """

    name: str = ""
    version: str = "0.1.0"

    @abstractmethod
    def initialize(self, context: PluginContext) -> None:
        """Receive the handles this plugin may use."""

    def shutdown(self) -> None:
        """Release anything held."""

    def __repr__(self) -> str:
        return f"<Plugin {self.name} v{self.version}>"
