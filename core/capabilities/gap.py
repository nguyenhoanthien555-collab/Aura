"""
Capability gap detection and representation for AURA 2.0 (Phase 5B).

Distinguishes:
- CAPABILITY_EXISTS
- NO_CAPABILITY_EXISTS
- CAPABILITY_EXISTS_BUT_UNAVAILABLE
- CAPABILITY_EXISTS_BUT_PERMISSION_DENIED
- CAPABILITY_EXISTS_BUT_CURRENT_PLATFORM_DOES_NOT_SUPPORT_IT

Prevents hallucinations and produces structured gap definitions for the
self-extensible tool pipeline.
"""

from dataclasses import dataclass, field
from enum import Enum
import sys
from typing import Any, Dict, List, Optional, Tuple

from core.capabilities import registry as default_registry
from core.capabilities.discovery import SkillDiscovery, discovery as default_discovery
from core.capabilities.models import CapabilityState
from core.ids import new_gap_id
from memory.models import timestamp_now


class CapabilityMatchState(str, Enum):
    """Deterministic classification of capability availability."""

    EXACT_MATCH = "EXACT_MATCH"
    PARTIAL_MATCH = "PARTIAL_MATCH"
    REQUIRES_PERMISSIONS = "REQUIRES_PERMISSIONS"
    SYNTHESIZABLE = "SYNTHESIZABLE"
    HARD_GAP = "HARD_GAP"

    # Backward compatibility aliases
    CAPABILITY_EXISTS = "EXACT_MATCH"
    NO_CAPABILITY_EXISTS = "SYNTHESIZABLE"
    CAPABILITY_EXISTS_BUT_UNAVAILABLE = "PARTIAL_MATCH"
    CAPABILITY_EXISTS_BUT_PERMISSION_DENIED = "REQUIRES_PERMISSIONS"
    CAPABILITY_EXISTS_BUT_CURRENT_PLATFORM_DOES_NOT_SUPPORT_IT = "HARD_GAP"


class GapStatus(str, Enum):
    IDENTIFIED = "IDENTIFIED"
    DESIGNED = "DESIGNED"
    GENERATED = "GENERATED"
    VALIDATED = "VALIDATED"
    RESOLVED = "RESOLVED"
    REJECTED = "REJECTED"


@dataclass
class CapabilityGap:
    """
    Structured description of a missing or blocked capability.
    """

    gap_id: str
    requested_capability: str
    intent: str
    reason: str
    match_state: str = CapabilityMatchState.SYNTHESIZABLE.value
    required_input: Dict[str, Any] = field(default_factory=dict)
    required_output: Dict[str, Any] = field(default_factory=dict)
    platform: str = "local"
    permissions: List[str] = field(default_factory=list)
    risk: str = "safe"
    constraints: List[str] = field(default_factory=list)
    candidate_solution: str = ""
    status: str = GapStatus.IDENTIFIED.value
    provenance: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=timestamp_now)

    @property
    def required_capability(self) -> str:
        return self.requested_capability

    @property
    def is_synthesizable(self) -> bool:
        return self.match_state in (
            CapabilityMatchState.SYNTHESIZABLE.value,
            CapabilityMatchState.SYNTHESIZABLE,
            "NO_CAPABILITY_EXISTS",
        )

    def to_dict(self) -> dict:
        return {
            "gap_id": self.gap_id,
            "requested_capability": self.requested_capability,
            "intent": self.intent,
            "reason": self.reason,
            "match_state": self.match_state,
            "required_input": self.required_input,
            "required_output": self.required_output,
            "platform": self.platform,
            "permissions": self.permissions,
            "risk": self.risk,
            "constraints": self.constraints,
            "candidate_solution": self.candidate_solution,
            "status": self.status,
            "provenance": self.provenance,
            "created_at": self.created_at,
        }


class CapabilityGapEngine:
    """
    Deterministic engine that detects capability gaps by checking live registry state.
    """

    def __init__(
        self,
        discovery: Optional[SkillDiscovery] = None,
        registry=None,
    ):
        self.discovery = discovery if discovery is not None else default_discovery
        self.registry = registry if registry is not None else default_registry

    def evaluate_capability(
        self,
        intent: str,
        threshold: float = 0.5,
        target_platform: Optional[str] = None,
    ) -> Tuple[CapabilityMatchState, Optional[CapabilityGap]]:
        """
        Evaluates intent against the registry and returns the exact match state
        along with a structured CapabilityGap if not cleanly executable.
        """
        explanation = self.discovery.explain(intent, threshold=threshold)
        selected = explanation.get("selected")
        diagnosis = explanation.get("diagnosis", {})
        ranked = explanation.get("ranked", [])
        top = diagnosis.get("top_candidate")

        # 1. Available and ready to execute
        if selected is not None and selected.get("state") == CapabilityState.AVAILABLE.value:
            tool_name = selected.get("tool")
            if tool_name and hasattr(self.registry, "has") and not self.registry.has(tool_name):
                gap_id = new_gap_id()
                current_plat = target_platform or ("win32" if sys.platform == "win32" else "linux")
                gap = CapabilityGap(
                    gap_id=gap_id,
                    requested_capability=selected["capability_id"],
                    intent=intent,
                    reason=f"Capability '{selected['capability_id']}' exists but backing tool '{tool_name}' is not in tool registry",
                    match_state=CapabilityMatchState.SYNTHESIZABLE.value,
                    platform=current_plat,
                    status=GapStatus.IDENTIFIED.value,
                )
                return CapabilityMatchState.SYNTHESIZABLE, gap
            return CapabilityMatchState.CAPABILITY_EXISTS, None

        gap_id = new_gap_id()
        current_plat = target_platform or ("win32" if sys.platform == "win32" else "linux")

        # 2. No capability semantically matched
        if diagnosis.get("no_capability_matched") or not ranked:
            safe_cap_name = f"custom.{intent.strip()[:32].replace(' ', '_').lower()}"
            gap = CapabilityGap(
                gap_id=gap_id,
                requested_capability=safe_cap_name,
                intent=intent,
                reason="No matching capability found in registry for this request",
                match_state=CapabilityMatchState.NO_CAPABILITY_EXISTS.value,
                platform=current_plat,
                status=GapStatus.IDENTIFIED.value,
            )
            return CapabilityMatchState.NO_CAPABILITY_EXISTS, gap

        # Matched candidates exist but are blocked or unhealthy
        top_id = top["capability_id"] if top else ranked[0]["capability_id"]
        top_state = top["state"] if top else ranked[0]["state"]
        top_reason = top.get("reason", "") if top else ranked[0].get("reason", "")

        # 3. Platform incompatibility check
        if (
            "platform" in top_reason.lower()
            or "desktop" in top_id and current_plat == "android"
            or "android" in top_id and current_plat not in ("android", "emulator")
            and "desktop" in current_plat
            and not top_reason
        ):
            # Check if reason mentions platform or candidate is blocked by platform
            if top_state == CapabilityState.BLOCKED_PLATFORM.value or "platform" in top_reason.lower():
                gap = CapabilityGap(
                    gap_id=gap_id,
                    requested_capability=top_id,
                    intent=intent,
                    reason=f"Capability '{top_id}' exists but is not supported on platform '{current_plat}'",
                    match_state=CapabilityMatchState.CAPABILITY_EXISTS_BUT_CURRENT_PLATFORM_DOES_NOT_SUPPORT_IT.value,
                    platform=current_plat,
                    status=GapStatus.IDENTIFIED.value,
                )
                return CapabilityMatchState.CAPABILITY_EXISTS_BUT_CURRENT_PLATFORM_DOES_NOT_SUPPORT_IT, gap

        # 4. Permission denied check
        if top_state == CapabilityState.BLOCKED_PERMISSION.value or diagnosis.get("missing_permissions"):
            missing_info = diagnosis.get("missing_permissions", [])
            missing_perms = []
            for item in missing_info:
                missing_perms.extend(item.get("permissions", []))
            gap = CapabilityGap(
                gap_id=gap_id,
                requested_capability=top_id,
                intent=intent,
                reason=f"Capability '{top_id}' exists but required permissions are missing: {missing_perms or top_reason}",
                match_state=CapabilityMatchState.CAPABILITY_EXISTS_BUT_PERMISSION_DENIED.value,
                permissions=missing_perms,
                platform=current_plat,
                status=GapStatus.IDENTIFIED.value,
            )
            return CapabilityMatchState.CAPABILITY_EXISTS_BUT_PERMISSION_DENIED, gap

        # 5. Unavailable / Unhealthy
        gap = CapabilityGap(
            gap_id=gap_id,
            requested_capability=top_id,
            intent=intent,
            reason=f"Capability '{top_id}' exists but is currently unavailable ({top_state}): {top_reason}",
            match_state=CapabilityMatchState.CAPABILITY_EXISTS_BUT_UNAVAILABLE.value,
            platform=current_plat,
            status=GapStatus.IDENTIFIED.value,
        )
        return CapabilityMatchState.CAPABILITY_EXISTS_BUT_UNAVAILABLE, gap

    def detect_gap(
        self,
        intent: str,
        threshold: float = 0.5,
        required_capability: Optional[str] = None,
        requested_capability: Optional[str] = None,
    ) -> Optional[CapabilityGap]:
        """
        Explains intent against live capabilities. Returns a CapabilityGap if
        no executable capability exists to fulfill the request.
        """
        _, gap = self.evaluate_capability(intent, threshold=threshold)
        req_cap = required_capability or requested_capability
        if gap and req_cap:
            gap.requested_capability = req_cap
        return gap

    def render_gap_prompt(self, gap: CapabilityGap) -> str:
        """
        Renders an honest, non-hallucinated capability gap notification.
        """
        return (
            f"CAPABILITY GAP DETECTED:\n"
            f"- Gap ID: {gap.gap_id}\n"
            f"- Match State: {gap.match_state}\n"
            f"- Requested: {gap.requested_capability}\n"
            f"- Reason: {gap.reason}\n"
            f"- Platform: {gap.platform}\n"
            f"- Status: {gap.status}"
        )
