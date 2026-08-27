from typing import Dict, Any, List
from core.capabilities.models import Capability, CapabilityState
from core.capabilities import registry
from core.logger import logger

class ExternalSkillAdapter:
    """
    Base adapter for external skill ecosystems like OpenViking and MCP.
    """
    def __init__(self, provider_name: str):
        self.provider_name = provider_name
        
    def sync_capabilities(self, external_skills: List[Dict[str, Any]]):
        """
        Takes raw skills from OpenViking/MCP and registers them into AURA's capability registry.
        """
        for skill in external_skills:
            cap_id = f"{self.provider_name}.{skill.get('name')}"
            cap = Capability(
                capability_id=cap_id,
                name=skill.get('name', 'Unknown'),
                description=skill.get('description', ''),
                category=self.provider_name,
                required_permissions=skill.get('required_permissions', []),
                discovery_metadata={"source": self.provider_name}
            )
            registry.register(cap)
            logger.info(f"Registered external capability: {cap_id}")

class OpenVikingAdapter(ExternalSkillAdapter):
    def __init__(self):
        super().__init__("openviking")

class MCPAdapter(ExternalSkillAdapter):
    def __init__(self):
        super().__init__("mcp")
