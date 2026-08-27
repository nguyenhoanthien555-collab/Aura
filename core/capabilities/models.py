from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

class CapabilityState(str, Enum):
    AVAILABLE = "AVAILABLE"
    BLOCKED_PERMISSION = "BLOCKED_PERMISSION"
    BLOCKED_DEPENDENCY = "BLOCKED_DEPENDENCY"
    BLOCKED_PLATFORM = "BLOCKED_PLATFORM"
    BLOCKED_CONFIGURATION = "BLOCKED_CONFIGURATION"
    DISABLED = "DISABLED"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"
    UNHEALTHY = "UNHEALTHY"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    # Kept as a compatibility alias for older callers. Runtime inventory
    # uses AVAILABLE plus the explicit blocking states above.
    AVAILABLE_WITH_PERMISSION = "AVAILABLE"

@dataclass
class Capability:
    capability_id: str
    name: str
    description: str
    category: str
    required_permissions: List[str] = field(default_factory=list)
    required_tools: List[str] = field(default_factory=list)
    required_plugins: List[str] = field(default_factory=list)
    required_dependencies: List[str] = field(default_factory=list)
    platform: Optional[str] = None
    availability_state: CapabilityState = CapabilityState.NOT_IMPLEMENTED
    authorization_state: str = "unknown"
    execution_state: str = "unknown"
    health_state: str = "unknown"
    version: str = "1.0"
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Dict[str, Any] = field(default_factory=dict)
    side_effects: bool = False
    risk_level: str = "safe"
    discovery_metadata: Dict[str, Any] = field(default_factory=dict)
