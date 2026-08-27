"""
Tool for inspecting AURA's live runtime capabilities and permission states.
"""

from core.capabilities.introspection import get_introspection_service
from tools.base import Parameter, Tool, ToolRisk


class CheckCapabilitiesTool(Tool):

    name = "check_capabilities"
    capability = "system.info"
    description = (
        "Inspect the live capability registry to see what AURA can and cannot "
        "do right now, including Android connectivity, device permissions, and exact blocking reasons."
    )
    risk = ToolRisk.SAFE

    parameters = (
        Parameter(
            name="query",
            description="Optional keyword or category (e.g. 'android', 'screen', 'filesystem') to filter capabilities.",
            required=False,
        ),
    )

    def execute(self, query: str = "") -> str:
        service = get_introspection_service()
        results = service.query(query)
        if not results:
            return f"No capabilities found matching query: '{query}'"

        lines = [f"Found {len(results)} capabilities:"]
        for item in results:
            state = item["state"]
            name = item["name"]
            cap_id = item["capability_id"]
            reason = item["reason"]
            reason_str = f" (Reason: {reason})" if reason else ""
            lines.append(f"- [{state}] {name} ({cap_id}){reason_str}")

        return "\n".join(lines)
