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
from tools.schema import mcp_export, tool_definition


class ToolRegistry:

    def __init__(self, tools: list[ToolProtocol] | None = None):

        self._tools: dict[str, ToolProtocol] = {}
        self._versions: dict[str, list[ToolProtocol]] = {}
        self._disabled: set[str] = set()
        self._revoked: set[str] = set()
        self._provenance: dict[str, dict] = {}
        self._provenance_history: dict[str, list[dict]] = {}
        self._dynamically_authorized: set[str] = set()

        for tool in tools or []:
            self.register(tool)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        tool: ToolProtocol,
        version: int = 1,
        provenance: dict | None = None,
        allow_upgrade: bool = False,
    ) -> None:
        """
        Add a tool.

        Rejects unnamed tools and duplicate names rather than silently
        shadowing: a tool that quietly replaces another is how a
        "read_file" ends up meaning something unexpected.
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
            if not allow_upgrade:
                raise ValueError(f"Tool already registered: {name}")
            # Keep previous version for rollback
            self._versions.setdefault(name, []).append(self._tools[name])
            if name in self._provenance:
                self._provenance_history.setdefault(name, []).append(dict(self._provenance[name]))

        self._tools[name] = tool
        self._disabled.discard(name)
        if provenance is not None:
            self._provenance[name] = dict(provenance)

        logger.debug("Registered tool: %s (%s)", name, tool.risk.value)

    def disable(self, name: str) -> bool:
        """Disable a registered tool without deleting it."""
        if name not in self._tools:
            return False
        self._disabled.add(name)
        logger.info("Disabled tool: %s", name)
        return True

    def activate(self, name: str) -> bool:
        """Re-activate a disabled tool."""
        if name not in self._tools:
            return False
        if name in self._revoked:
            logger.warning("Cannot activate revoked tool: %s", name)
            return False
        self._disabled.discard(name)
        logger.info("Activated tool: %s", name)
        return True

    def revoke(self, name: str, reason: str = "") -> bool:
        """
        Permanently revoke a tool for safety or policy reasons.
        Revoked tools cannot be activated and cannot execute.
        """
        if name not in self._tools:
            return False
        self._revoked.add(name)
        self._disabled.add(name)
        self._dynamically_authorized.discard(name)
        logger.warning("Revoked tool %s: %s", name, reason or "no reason given")
        return True

    def is_revoked(self, name: str) -> bool:
        """Whether a tool has been revoked."""
        return name in self._revoked

    def rollback(self, name: str) -> bool:
        """Roll back a tool to its previous version, if one exists."""
        history = self._versions.get(name, [])
        if not history:
            return False
        previous = history.pop()
        self._tools[name] = previous
        self._disabled.discard(name)
        self._revoked.discard(name)
        if self._provenance_history.get(name):
            self._provenance[name] = self._provenance_history[name].pop()
        # Check if the rolled-back tool has active provenance
        prev_prov = self.provenance_for(name)
        if not prev_prov or prev_prov.get("status") != "ACTIVE":
            self._dynamically_authorized.discard(name)
        logger.info("Rolled back tool %s to previous version", name)
        return True

    def is_active(self, name: str) -> bool:
        """Whether a tool is registered, active, and not revoked."""
        return (
            name in self._tools
            and name not in self._disabled
            and name not in self._revoked
        )

    def authorize_dynamic(self, name: str) -> bool:
        """
        Mark a dynamically registered tool as authorized for execution.
        Requires that the tool exists, is not revoked, and has provenance.
        """
        if name not in self._tools or name in self._revoked:
            return False
        prov = self.provenance_for(name)
        if not prov:
            logger.warning("Cannot authorize dynamic tool '%s' without provenance", name)
            return False
        self._dynamically_authorized.add(name)
        logger.info("Dynamically authorized tool: %s", name)
        return True

    def deauthorize_dynamic(self, name: str) -> None:
        """Remove dynamic authorization for a tool."""
        self._dynamically_authorized.discard(name)

    def is_dynamically_authorized(self, name: str) -> bool:
        """
        Whether a tool is an authorized, active dynamic tool.
        A tool must be in _dynamically_authorized, and is_active() must be True.
        """
        if name not in self._dynamically_authorized:
            return False
        return self.is_active(name)

    def provenance_for(self, name: str) -> dict | None:
        """Audit / provenance record for a tool."""
        return self._provenance.get(name)

    def get_provenance(self, name: str) -> dict | None:
        """Alias for provenance_for."""
        return self.provenance_for(name)

    def version_of(self, name: str) -> int:
        """Return the current version number for the tool."""
        tool = self._tools.get(name)
        if tool is not None and getattr(tool, "version", None) is not None:
            return int(tool.version)
        prov = self.provenance_for(name) or {}
        return int(prov.get("version", 1))

    def get_version(self, name: str) -> int:
        """Alias for version_of."""
        return self.version_of(name)

    def inspect_tool(self, name: str) -> dict | None:
        """
        Full diagnostic inspection of tool registration and lifecycle state.
        """
        tool = self.get(name)
        if tool is None:
            return None
        prov = self.provenance_for(name) or {}
        return {
            "name": name,
            "version": self.version_of(name),
            "is_active": self.is_active(name),
            "is_disabled": name in self._disabled,
            "is_revoked": name in self._revoked,
            "previous_versions_count": len(self._versions.get(name, [])),
            "risk": getattr(tool, "risk", None).value if getattr(tool, "risk", None) else "unknown",
            "side_effect": getattr(tool, "side_effect", "UNKNOWN"),
            "provenance": prov,
        }

    def unregister(self, name: str) -> bool:
        self._disabled.discard(name)
        self._revoked.discard(name)
        self._dynamically_authorized.discard(name)
        self._versions.pop(name, None)
        self._provenance.pop(name, None)
        self._provenance_history.pop(name, None)
        return self._tools.pop(name, None) is not None

    def clear(self) -> None:
        self._tools.clear()
        self._versions.clear()
        self._disabled.clear()
        self._revoked.clear()
        self._provenance.clear()
        self._provenance_history.clear()
        self._dynamically_authorized.clear()

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

    def by_side_effect(self, side_effect) -> list[ToolProtocol]:
        """
        Every tool whose declared side-effect class matches.

        The retry-safety slice of the registry: the runtime asks this
        when it needs to know what a second run of something would cost,
        and an undeclared tool answers as UNKNOWN, never as harmless.
        """

        return [
            tool
            for tool in self.all()
            if getattr(tool, "side_effect", None) == side_effect
        ]

    def definitions(self) -> list[dict]:
        """
        Every tool, as canonical machine-readable definitions.

        Discovery, schema inspection, risk classification and versioning
        in one payload - the answer to "what can AURA actually do?"
        without reading source code. Availability is deliberately NOT
        folded in here: policy and capability state are live facts owned
        by `ToolExecutor.available()` and the capability registry, and a
        registry snapshot that pre-joined them would lie the moment
        either changed. Callers that want the runtime view join it with
        the executor's `available()` list.
        """

        return [tool_definition(tool) for tool in self.all()]

    def export_mcp(self) -> list[dict]:
        """
        The registry in MCP `tools/list` conceptual form.

        Standards-compatible (name, description, inputSchema) exactly as
        `tools.schema.mcp_export` renders it; the Aura-specific fields
        stay in `definitions()`. A registry method rather than a
        free function so there is one object to ask about the catalogue.
        """

        return mcp_export(self.all())

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
