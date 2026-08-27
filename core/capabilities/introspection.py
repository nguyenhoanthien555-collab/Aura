"""
Runtime capability introspection and self-knowledge service.

Provides the single authoritative source of truth for:
- "What can AURA currently do?"
- "What Android capabilities are available?"
- "Why can't AURA do X?"
- "What permissions or dependencies are missing?"
"""

from typing import Dict, List, Any, Optional
from core.capabilities import registry, resolve_capability
from core.capabilities.models import Capability, CapabilityState
from core.logger import logger


def ensure_all_providers_registered():
    """Ensure all provider tools and capabilities are registered."""
    try:
        from server.routes.agent import get_device_registry
        get_device_registry()
    except Exception as error:
        logger.debug("Device registry not initialized: %s", error)


class CapabilityIntrospectionService:
    """
    Authoritative query engine for AURA's live capability state.
    """

    def __init__(self):
        pass

    def get_inventory(self) -> Dict[str, Dict[str, Any]]:
        """
        Get the live status of every capability in the registry.
        Evaluates permissions and health dynamically on every call.
        """
        ensure_all_providers_registered()

        inventory: Dict[str, Dict[str, Any]] = {}
        for cap in registry.all():
            state = resolve_capability(cap.capability_id)
            tool_name = cap.discovery_metadata.get("tool", "")

            inventory[cap.capability_id] = {
                "capability_id": cap.capability_id,
                "name": cap.name,
                "description": cap.description,
                "category": cap.category,
                "state": state.value,
                "authorization": cap.authorization_state,
                "health": cap.health_state,
                "execution": cap.execution_state,
                "required_permissions": list(cap.required_permissions),
                "required_dependencies": list(cap.required_dependencies),
                "bound_tool": tool_name,
                "reason": cap.discovery_metadata.get("state_reason", ""),
            }

        return inventory

    def query(self, query_str: str = "") -> List[Dict[str, Any]]:
        """
        Query capabilities matching a search string (category, id, name, or keyword).
        """
        inventory = self.get_inventory()
        if not query_str:
            return list(inventory.values())

        q = query_str.lower().strip()
        matches = []
        for item in inventory.values():
            if (
                q in item["capability_id"].lower()
                or q in item["name"].lower()
                or q in item["description"].lower()
                or q in item["category"].lower()
                or q in item["bound_tool"].lower()
            ):
                matches.append(item)

        return matches

    def render_summary(self, category: Optional[str] = None) -> str:
        """
        Render a structured, grounded Markdown summary of the live capability inventory
        specifically designed for LLM system prompt injection.
        """
        inventory = self.get_inventory()
        if category:
            inventory = {k: v for k, v in inventory.items() if v["category"].lower() == category.lower()}

        if not inventory:
            return ""

        available_items = []
        blocked_permission_items = []
        unavailable_items = []
        unhealthy_items = []
        not_implemented_items = []

        for item in inventory.values():
            state = item["state"]
            cap_id = item["capability_id"]
            name = item["name"]
            desc = item["description"]
            reason = item["reason"]
            tool = item["bound_tool"]
            tool_str = f" / tool: `{tool}`" if tool else ""

            if state == "AVAILABLE":
                available_items.append(f"- **{name}** (`{cap_id}`{tool_str}): {desc}")
            elif state == "BLOCKED_PERMISSION":
                reason_str = f" — *Reason: {reason}*" if reason else ""
                available_items_note = f"- **{name}** (`{cap_id}`): BLOCKED BY PERMISSION{reason_str}"
                blocked_permission_items.append(available_items_note)
            elif state == "UNAVAILABLE":
                reason_str = f" — *Reason: {reason}*" if reason else ""
                unavailable_items.append(f"- **{name}** (`{cap_id}`): CURRENTLY UNAVAILABLE{reason_str}")
            elif state == "UNHEALTHY":
                reason_str = f" — *Reason: {reason}*" if reason else ""
                unhealthy_items.append(f"- **{name}** (`{cap_id}`): UNHEALTHY DEPENDENCY{reason_str}")
            else:
                not_implemented_items.append(f"- **{name}** (`{cap_id}`): NOT IMPLEMENTED")

        lines = [
            "Below is the authoritative, LIVE capability inventory of what this AURA instance can and cannot currently do on this machine and connected devices.",
            "All self-knowledge and capability claims MUST follow this live status strictly:",
            "",
            "### AVAILABLE NOW (ACTUALLY EXECUTABLE):"
        ]

        if available_items:
            lines.extend(available_items)
        else:
            lines.append("- (No capabilities are currently available)")

        if blocked_permission_items:
            lines.append("")
            lines.append("### BLOCKED BY MISSING PERMISSION:")
            lines.extend(blocked_permission_items)

        if unavailable_items:
            lines.append("")
            lines.append("### CURRENTLY UNAVAILABLE:")
            lines.extend(unavailable_items)

        if unhealthy_items:
            lines.append("")
            lines.append("### UNHEALTHY DEPENDENCIES:")
            lines.extend(unhealthy_items)

        lines.extend([
            "",
            "### GROUNDING RULES FOR CAPABILITY CLAIMS:",
            "1. If a capability is under [AVAILABLE NOW], you possess it and can execute it when requested.",
            "2. If an Android capability is [AVAILABLE NOW], confirm that you have direct Android access and describe what you can do.",
            "3. If an Android capability is [CURRENTLY UNAVAILABLE] or [BLOCKED], state explicitly that it cannot be performed and quote the exact reason above.",
            "4. Never invent capabilities or claim an action succeeded without real tool execution evidence."
        ])

        return "\n".join(lines).strip()


_introspection_service = CapabilityIntrospectionService()


def get_introspection_service() -> CapabilityIntrospectionService:
    return _introspection_service
