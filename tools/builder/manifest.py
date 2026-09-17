"""
Tool Manifest specification and serialization for self-extending tools.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ToolManifest:
    """
    Machine-readable blueprint and contract for a tool.
    """

    name: str
    version: int = 1
    description: str = ""
    capability: str = ""
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Dict[str, Any] = field(default_factory=dict)
    permissions: List[str] = field(default_factory=list)
    risk_level: str = "safe"
    risk: str = "safe"
    confirmation_policy: str = "never"
    side_effect: str = "READ_ONLY"
    execution_mode: str = "inline"
    timeout_policy: Dict[str, Any] = field(default_factory=lambda: {"timeout_seconds": 30.0})
    retry_policy: Dict[str, Any] = field(default_factory=lambda: {"max_attempts": 3, "backoff": 1.0})
    idempotency: bool = False
    verification_required: bool = False
    postcondition: str = ""
    evidence_kind: str = "DIRECT"
    runtime_platform: str = "local"
    executor: str = "ToolExecutor"
    provenance: Dict[str, Any] = field(default_factory=dict)

    # Legacy / Compatibility fields
    parameters: List[Dict[str, Any]] = field(default_factory=list)
    source_code: str = ""
    test_code: str = ""
    source_digest: str = ""
    gap_id: str = ""
    requirements: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.name

    def __post_init__(self):
        if self.risk != "safe" and self.risk_level == "safe":
            self.risk_level = self.risk
        elif self.risk_level != "safe" and self.risk == "safe":
            self.risk = self.risk_level
        if not self.source_digest and self.source_code:
            self.compute_digest()
        # Keep parameters and input_schema aligned
        if self.parameters and not self.input_schema:
            props = {}
            required = []
            for p in self.parameters:
                p_name = p.get("name", "")
                props[p_name] = {
                    "type": p.get("type", "string"),
                    "description": p.get("description", ""),
                }
                if p.get("required", True):
                    required.append(p_name)
            self.input_schema = {
                "type": "object",
                "properties": props,
                "required": required,
            }

    def compute_digest(self) -> str:
        """Compute SHA-256 digest of source code."""
        digest = hashlib.sha256(self.source_code.strip().encode("utf-8")).hexdigest()
        self.source_digest = digest
        return digest

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolManifest":
        risk_val = data.get("risk_level") or data.get("risk") or "safe"
        return cls(
            name=data.get("name") or data.get("id") or "",
            version=int(data.get("version", 1)),
            description=data.get("description", ""),
            capability=data.get("capability", ""),
            input_schema=dict(data.get("input_schema") or {}),
            output_schema=dict(data.get("output_schema") or {}),
            permissions=list(data.get("permissions") or []),
            risk_level=risk_val,
            confirmation_policy=data.get("confirmation_policy", "never"),
            side_effect=data.get("side_effect", "READ_ONLY"),
            execution_mode=data.get("execution_mode", "inline"),
            timeout_policy=dict(data.get("timeout_policy") or {"timeout_seconds": 30.0}),
            retry_policy=dict(data.get("retry_policy") or {"max_attempts": 3, "backoff": 1.0}),
            idempotency=bool(data.get("idempotency", False)),
            verification_required=bool(data.get("verification_required", False)),
            postcondition=data.get("postcondition", ""),
            evidence_kind=data.get("evidence_kind", "DIRECT"),
            runtime_platform=data.get("runtime_platform", "local"),
            executor=data.get("executor", "ToolExecutor"),
            provenance=dict(data.get("provenance") or {}),
            parameters=list(data.get("parameters") or []),
            source_code=data.get("source_code", ""),
            test_code=data.get("test_code", ""),
            source_digest=data.get("source_digest", ""),
            gap_id=data.get("gap_id", ""),
            requirements=list(data.get("requirements") or []),
            metadata=dict(data.get("metadata") or {}),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "ToolManifest":
        data = json.loads(json_str)
        return cls.from_dict(data)
