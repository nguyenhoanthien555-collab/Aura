from typing import Callable, Dict, Any, Optional
from core.capabilities.models import Capability, CapabilityState
from core.logger import logger

HealthCheckFunc = Callable[[], Dict[str, Any]]

class HealthCheckRegistry:
    def __init__(self):
        self._checks: Dict[str, HealthCheckFunc] = {}

    def register_check(self, capability_id: str, check_func: HealthCheckFunc) -> None:
        self._checks[capability_id] = check_func

    def run_check(self, capability_id: str) -> Dict[str, Any]:
        check_func = self._checks.get(capability_id)
        if not check_func:
            return {"healthy": True, "reason": "No health check configured."}
        
        try:
            return check_func()
        except Exception as e:
            logger.error(f"Health check failed for {capability_id}: {e}")
            return {"healthy": False, "reason": str(e)}
