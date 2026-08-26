"""
Capability providers.

A provider owns one family of capabilities - Android, desktop, files,
web - and exposes each as an ordinary tool. The agent never learns how a
tap is performed; it learns that `android.tap` exists, and the provider
turns that name into whatever the device actually requires.

This is the seam between "what the agent can ask for" and "how it is
done", and it has to be the only seam. The old design had three parallel
Android pathways (the accessibility agent's action strings, the chat
tool path, and ad-hoc intent handling) which each grew their own parsing,
verification and error prose. One provider interface registered into the
one ToolRegistry is what collapses them: a new capability is added once,
described once, gated by the one policy, and every caller - agent loop,
CLI harness, MCP client, tests - reaches it the same way.

A provider registers; it does not execute. Execution stays behind the
ToolExecutor gates exactly as before.
"""

from abc import ABC, abstractmethod

from core.logger import logger
from tools.base import ToolProtocol
from tools.registry import ToolRegistry


class CapabilityProvider(ABC):
    """
    One family of capabilities, offered as named tools.

    Subclasses set `namespace` and return fully-named tools from
    `capabilities()`. The naming convention `<namespace>.<capability>`
    (`android.tap`, `file.read`) is what keeps two providers that both
    have a "read" concept from colliding in the registry, which rejects
    duplicate names outright.
    """

    namespace: str = ""
    description: str = ""

    @abstractmethod
    def capabilities(self) -> list[ToolProtocol]:
        """Every tool this provider offers, fully named."""
        ...

    def register_into(self, registry: ToolRegistry) -> int:
        """
        Register every capability into `registry`.

        Returns the count registered. A tool that fails registration is
        skipped with a warning rather than aborting the family: one
        malformed capability must not make every other one invisible.
        The count is returned so a caller (and a test) can notice when
        fewer tools arrived than were declared.
        """

        registered = 0

        for tool in self.capabilities():
            try:
                registry.register(tool)
                registered += 1
            except ValueError as error:
                logger.warning(
                    "Provider %s could not register %s: %s",
                    self.namespace or type(self).__name__,
                    getattr(tool, "name", "<unnamed>"),
                    error,
                )

        return registered

    def available(self) -> bool:
        """
        Whether this provider can work at all right now.

        Default true; a provider whose backend device is absent overrides
        this so its tools are simply not advertised rather than advertised
        and failing at call time.
        """

        return True
