from typing import List, Dict, Callable, Any
from core.logger import logger

class PermissionResolver:
    def __init__(self):
        # Static grants remain useful for local, non-device capabilities.
        self._granted_permissions: Dict[str, bool] = {}
        # Device permissions are facts reported by the live provider, not
        # configuration flags. A checker returns bool or a detail mapping.
        self._checks: Dict[str, Callable[[], Any]] = {}

    def grant(self, permission: str) -> None:
        self._granted_permissions[permission] = True

    def revoke(self, permission: str) -> None:
        self._granted_permissions[permission] = False

    def register_check(self, permission: str, check: Callable[[], Any]) -> None:
        self._checks[permission] = check

    def clear_check(self, permission: str) -> None:
        self._checks.pop(permission, None)

    def is_granted(self, permission: str) -> bool:
        granted, _ = self.check(permission)
        return granted

    def check(self, permission: str) -> tuple[bool, str]:
        check = self._checks.get(permission)
        if check is None:
            return self._granted_permissions.get(permission, False), ""

        try:
            result = check()
        except Exception as error:
            logger.warning("Permission check failed for %s: %s", permission, error)
            return False, f"permission check failed: {type(error).__name__}"

        if isinstance(result, dict):
            return bool(result.get("granted", False)), str(result.get("reason", ""))

        return bool(result), ""

    def missing_details(self, required_permissions: List[str]) -> List[tuple[str, str]]:
        missing = []
        for permission in required_permissions:
            granted, reason = self.check(permission)
            if not granted:
                missing.append((permission, reason))
        return missing

    def check_capabilities_permissions(self, required_permissions: List[str]) -> List[str]:
        """
        Returns a list of missing permissions from the required ones.
        """
        return [permission for permission, _ in self.missing_details(required_permissions)]
