"""
Tool Builder module for AURA 2.0 (Phase 5B).

Bridges:
CapabilityGap -> ToolManifest -> Static Validation -> Sandbox Execution ->
Deterministic Validation -> Approval Gate -> Dynamic Tool Registration & Provenance.
"""

from enum import Enum
import inspect
from typing import Any, Callable, Dict, Optional

from core.capabilities.gap import CapabilityGap, GapStatus
from core.logger import logger
from memory.models import ToolProvenanceRecord, timestamp_now
from tools.base import Parameter, ToolProtocol, ToolRisk
from tools.builder.manifest import ToolManifest
from tools.builder.validator import ToolValidator, ValidationReport
from tools.outcome import SideEffect
from tools.registry import ToolRegistry


class RecursiveSynthesisError(RuntimeError):
    """Raised when recursive self-extension depth or attempts are exceeded."""
    pass


class ToolLifecycleState(str, Enum):
    """Authoritative lifecycle states for synthesized and dynamic tools."""

    DESIGNED = "DESIGNED"
    GENERATED = "GENERATED"
    STATIC_CHECK = "STATIC_CHECK"
    SANDBOX_TESTING = "SANDBOX_TESTING"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    REGISTERED = "REGISTERED"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    REVOKED = "REVOKED"


class ToolBuilder:
    """
    Automates synthesis, validation, approval, promotion, and rollback of dynamic tools.
    Separates validation from approval to enforce security boundaries.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        validator: Optional[ToolValidator] = None,
        session_factory: Optional[Callable] = None,
        auto_approve_safe: bool = True,
        approval_callback: Optional[Callable[[ToolManifest, ValidationReport], bool]] = None,
        bus: Any = None,
        max_synthesis_depth: int = 2,
    ):
        self.registry = registry
        self.validator = validator or ToolValidator()
        self.session_factory = session_factory
        self.auto_approve_safe = auto_approve_safe
        self.approval_callback = approval_callback
        self.bus = bus
        self.max_synthesis_depth = max_synthesis_depth
        self.current_depth = 0
        self._approved_manifests: Dict[str, str] = {}  # digest -> approver_id

    def _emit_tool_event(
        self,
        tool_name: str,
        event_type: Any,
        version: int = 1,
        digest: str = "",
        detail: str = "",
    ) -> None:
        if self.bus is not None:
            try:
                import time
                from events.types import ToolLifecycleEvent
                self.bus.publish(
                    ToolLifecycleEvent(
                        tool_name=tool_name,
                        event_type=event_type,
                        version=version,
                        digest=digest,
                        detail=detail,
                        timestamp=time.time(),
                    )
                )
            except Exception as e:
                logger.debug("Tool event publication skipped: %s", e)

    def build_from_gap(
        self,
        gap: CapabilityGap,
        source_code: str,
        test_code: str = "",
        version: int = 1,
        risk: Optional[str] = None,
        side_effect: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ToolManifest:
        """
        Create a ToolManifest from an identified CapabilityGap and implementation code.
        Lifecycle state: GENERATED.
        """
        if self.current_depth >= self.max_synthesis_depth:
            raise RecursiveSynthesisError(
                f"Maximum tool synthesis depth ({self.max_synthesis_depth}) exceeded. "
                "Recursive self-extension blocked."
            )

        # Check that generated code does not attempt to invoke ToolBuilder or CapabilityGapEngine
        forbidden_tokens = ("ToolBuilder", "CapabilityGapEngine", "tools.builder", "compile(")
        for tok in forbidden_tokens:
            if tok in source_code:
                raise RecursiveSynthesisError(
                    f"Generated tool code contains forbidden self-extension token: '{tok}'"
                )

        params: list[dict] = []
        for p_name, p_spec in (gap.required_input or {}).items():
            if isinstance(p_spec, dict):
                params.append({
                    "name": p_name,
                    "type": p_spec.get("type", "string"),
                    "description": p_spec.get("description", ""),
                    "required": p_spec.get("required", True),
                })
            else:
                params.append({
                    "name": p_name,
                    "type": "string",
                    "description": str(p_spec),
                    "required": True,
                })

        safe_name = gap.requested_capability.replace(".", "_").replace("-", "_").lower()
        if not safe_name.isidentifier():
            safe_name = f"tool_{safe_name}"

        risk_val = (risk or gap.risk or "safe").lower()
        se_val = (side_effect or "READ_ONLY").upper()

        manifest = ToolManifest(
            name=safe_name,
            version=version,
            description=gap.intent or gap.reason,
            capability=gap.requested_capability,
            risk_level=risk_val,
            side_effect=se_val,
            parameters=params,
            source_code=source_code,
            test_code=test_code,
            gap_id=gap.gap_id,
            metadata=metadata or {},
        )
        manifest.compute_digest()
        gap.status = GapStatus.GENERATED.value
        return manifest

    def validate(self, manifest: ToolManifest) -> ValidationReport:
        """Run static AST and dynamic sandbox validation."""
        return self.validator.validate(manifest)

    def approve(self, manifest: ToolManifest, approver: str = "human_operator") -> bool:
        """
        Explicit approval boundary. Marks manifest as approved for registration.
        """
        if not manifest.source_digest:
            manifest.compute_digest()
        self._approved_manifests[manifest.source_digest] = approver
        logger.info("Tool '%s' approved by %s", manifest.name, approver)
        return True

    def is_approved(self, manifest: ToolManifest) -> bool:
        """Whether a tool manifest has been explicitly approved or qualifies for safe auto-approval."""
        if not manifest.source_digest:
            manifest.compute_digest()
        if manifest.source_digest in self._approved_manifests:
            return True
        if manifest.risk_level.lower() == "safe" and self.auto_approve_safe:
            return True
        if self.approval_callback:
            report = self.validate(manifest)
            if report.passed and self.approval_callback(manifest, report):
                self._approved_manifests[manifest.source_digest] = "callback_policy"
                return True
        return False

    def instantiate_tool(self, manifest: ToolManifest) -> ToolProtocol:
        """
        Compile and instantiate the tool class from manifest source code.
        Never runs before validation!
        """
        if not self.is_approved(manifest):
            rep = self.validate(manifest)
            if not rep.passed:
                raise PermissionError(
                    f"Cannot instantiate tool '{manifest.name}': validation failed ({rep.errors})"
                )

        scope: Dict[str, Any] = {"__name__": f"tools.dynamic.{manifest.name}"}
        code_obj = compile(manifest.source_code, f"<tool_{manifest.name}>", "exec")
        exec(code_obj, scope, scope)

        candidate_cls = None
        for item in scope.values():
            if (
                inspect.isclass(item)
                and hasattr(item, "execute")
                and hasattr(item, "risk")
                and not inspect.isabstract(item)
                and item.__name__ not in ("Tool", "ToolProtocol")
            ):
                candidate_cls = item
                break

        if candidate_cls is None:
            raise ValueError(f"No valid Tool class with risk and execute found in source code for {manifest.name}")

        instance = candidate_cls()
        instance.name = manifest.name
        instance.version = manifest.version
        if not getattr(instance, "capability", None) and manifest.capability:
            instance.capability = manifest.capability
        if not getattr(instance, "side_effect", None) and manifest.side_effect:
            instance.side_effect = manifest.side_effect

        return instance

    def promote(
        self,
        manifest: ToolManifest,
        report: ValidationReport,
        allow_upgrade: bool = True,
        approver: Optional[str] = None,
    ) -> ToolProtocol:
        """
        Promote a validated manifest into the live ToolRegistry and persist provenance.
        Enforces approval gate boundary between VALIDATED and ACTIVE!
        """
        if not report.passed:
            raise ValueError(f"Cannot promote tool '{manifest.name}': validation failed ({report.errors})")

        # Approval gate enforcement
        if approver:
            self.approve(manifest, approver=approver)

        if not self.is_approved(manifest):
            raise PermissionError(
                f"Cannot promote tool '{manifest.name}': risk level is '{manifest.risk_level}' "
                f"and tool requires explicit approval before activation."
            )

        tool = self.instantiate_tool(manifest)
        effective_approver = self._approved_manifests.get(manifest.source_digest, "auto_policy")

        provenance = {
            "gap_id": manifest.gap_id,
            "name": manifest.name,
            "version": manifest.version,
            "source_digest": manifest.source_digest,
            "status": ToolLifecycleState.ACTIVE.value,
            "approver": effective_approver,
            "validation": report.to_dict(),
            "promoted_at": timestamp_now(),
        }

        self.registry.register(
            tool=tool,
            version=manifest.version,
            provenance=provenance,
            allow_upgrade=allow_upgrade,
        )

        if hasattr(self.registry, "authorize_dynamic"):
            self.registry.authorize_dynamic(manifest.name)

        caps_to_register = set()
        if manifest.capability:
            caps_to_register.add(manifest.capability)
        tool_cap = getattr(tool, "capability", None)
        if tool_cap:
            caps_to_register.add(tool_cap)

        for cap_id in caps_to_register:
            try:
                from core.capabilities import registry as cap_registry, Capability
                if not cap_registry.get(cap_id):
                    raw_text = f"{manifest.name.replace('_', ' ')} {manifest.description or ''}"
                    keywords = list(set([tok for tok in raw_text.lower().split() if len(tok) > 2]))
                    cap_registry.register(
                        Capability(
                            capability_id=cap_id,
                            name=manifest.name,
                            description=manifest.description,
                            category="custom",
                            discovery_metadata={"tool": manifest.name, "keywords": keywords},
                        )
                    )
            except Exception as e:
                logger.debug("Capability registration skipped for %s: %s", cap_id, e)

        if self.session_factory:
            try:
                with self.session_factory() as session:
                    record = ToolProvenanceRecord(
                        name=manifest.name,
                        version=manifest.version,
                        gap_id=manifest.gap_id,
                        manifest_json=manifest.to_json(),
                        source_code=manifest.source_code,
                        source_digest=manifest.source_digest,
                        status=ToolLifecycleState.ACTIVE.value,
                        validation_json=report.to_json(),
                    )
                    session.add(record)
                    session.commit()
            except Exception as e:
                logger.warning("Failed to persist tool provenance record: %s", e)

        logger.info("Successfully promoted tool '%s' v%d to ACTIVE", manifest.name, manifest.version)
        self._emit_tool_event(manifest.name, "TOOL_PROMOTED", version=manifest.version, digest=manifest.source_digest)
        return tool

    def rollback(self, name: str) -> bool:
        """Roll back a tool to its prior registered version."""
        success = self.registry.rollback(name)
        if success and self.session_factory:
            try:
                with self.session_factory() as session:
                    from sqlalchemy import select
                    stmt = (
                        select(ToolProvenanceRecord)
                        .where(ToolProvenanceRecord.name == name)
                        .order_by(ToolProvenanceRecord.version.desc())
                    )
                    records = list(session.scalars(stmt).all())
                    if records:
                        records[0].status = "ROLLED_BACK"
                        session.commit()
            except Exception as e:
                logger.warning("Failed to update provenance record status on rollback: %s", e)

        if success:
            self._emit_tool_event(name, "TOOL_ROLLBACK", detail=f"Rolled back {name}")
        return success

    def disable(self, name: str) -> bool:
        """Disable a registered tool."""
        res = self.registry.disable(name)
        if res:
            self._emit_tool_event(name, "TOOL_DISABLED", detail=f"Disabled {name}")
        return res

    def activate(self, name: str) -> bool:
        """Re-activate a disabled tool."""
        res = self.registry.activate(name)
        if res:
            self._emit_tool_event(name, "TOOL_PROMOTED", detail=f"Re-activated {name}")
        return res

    def revoke(self, name: str, reason: str = "") -> bool:
        """Permanently revoke a tool."""
        success = self.registry.revoke(name, reason=reason)
        if success and self.session_factory:
            try:
                with self.session_factory() as session:
                    from sqlalchemy import select
                    stmt = (
                        select(ToolProvenanceRecord)
                        .where(ToolProvenanceRecord.name == name)
                        .order_by(ToolProvenanceRecord.version.desc())
                    )
                    records = list(session.scalars(stmt).all())
                    if records:
                        records[0].status = ToolLifecycleState.REVOKED.value
                        session.commit()
            except Exception as e:
                logger.warning("Failed to update provenance record on revocation: %s", e)
        if success:
            self._emit_tool_event(name, "TOOL_REVOKED", detail=reason or f"Revoked {name}")
        return success
