from typing import Dict, List, Optional
from core.capabilities.models import Capability, CapabilityState
from core.logger import logger

class CapabilityRegistry:
    def __init__(self):
        self._capabilities: Dict[str, Capability] = {}

    def register(self, capability: Capability) -> None:
        if capability.capability_id in self._capabilities:
            logger.warning(f"Capability {capability.capability_id} already registered. Overwriting.")
        self._capabilities[capability.capability_id] = capability

    def get(self, capability_id: str) -> Optional[Capability]:
        return self._capabilities.get(capability_id)

    def all(self) -> List[Capability]:
        return list(self._capabilities.values())
        
    def by_state(self, state: CapabilityState) -> List[Capability]:
        return [cap for cap in self.all() if cap.availability_state == state]

    def update_state(self, capability_id: str, state: CapabilityState, reason: str = "") -> None:
        cap = self.get(capability_id)
        if cap:
            cap.availability_state = state
            if reason:
                cap.discovery_metadata["state_reason"] = reason
            else:
                # A runtime state transition back to a healthy/available
                # condition must not retain a stale blocking explanation.
                # Otherwise the inventory can say AVAILABLE while still
                # telling the model that the companion has no heartbeat.
                cap.discovery_metadata.pop("state_reason", None)

    def clear(self) -> None:
        self._capabilities.clear()
        
    def describe(self) -> str:
        """
        Generate a human-readable (and LLM-readable) description of all capabilities.
        Used for Agent Self-Knowledge.
        """
        from core.capabilities import resolve_capability

        categories = {}
        for cap in self.all():
            state = resolve_capability(cap.capability_id).value
            if state not in categories:
                categories[state] = []
            categories[state].append(f"- {cap.name} ({cap.capability_id}): {cap.description}")
            
        lines = []
        for state in sorted(categories.keys()):
            lines.append(f"{state}")
            lines.extend(sorted(categories[state]))
            lines.append("")
            
        return "\n".join(lines).strip()
