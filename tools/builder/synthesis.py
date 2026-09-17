"""
Autonomous Tool Synthesis Engine for AURA 2.0 (Phase 5B.2).

Bridges:
CapabilityGap
    ↓
Tool Design & Synthesis Request
    ↓
LLM Code Synthesis (untrusted candidate data)
    ↓
Deterministic AST Validation & Sandbox Testing
    ↓
Deterministic Approval & Policy Gates
    ↓
Tool Promotion & Dynamic Registration
    ↓
Execution via ToolExecutor
"""

import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from brain.ports import LLM
from core.capabilities import registry as default_capability_registry
from core.capabilities.gap import CapabilityGap, CapabilityMatchState, GapStatus
from core.logger import logger
from tools.base import ToolProtocol, ToolRisk
from tools.builder.builder import ToolBuilder, ToolLifecycleState, RecursiveSynthesisError
from tools.builder.manifest import ToolManifest
from tools.builder.validator import ToolValidator, ValidationReport
from tools.outcome import SideEffect
from tools.registry import ToolRegistry


# Banned tokens in synthesized tool source code (strict recursion protection)
FORBIDDEN_SYNTHESIS_TOKENS = (
    "ToolBuilder",
    "CapabilityGapEngine",
    "tools.builder",
    "ToolRegistry",
    "ToolPolicy",
    "ToolProvenanceRecord",
    "TaskRuntime",
    "execute_compound_task",
    "__subclasses__",
    "__globals__",
    "compile(",
    "eval(",
    "exec(",
    "__import__",
    "subprocess",
    "os.system",
    "os.popen",
    "socket",
    "urllib",
    "requests",
    "httpx",
)


@dataclass
class ToolSynthesisRequest:
    """
    Contract for requesting candidate tool synthesis from a capability gap.
    """

    intent: str
    requested_capability: str
    tool_name: str = ""
    description: str = ""
    input_requirements: Dict[str, Any] = field(default_factory=dict)
    output_requirements: Dict[str, Any] = field(default_factory=dict)
    permission_requirements: List[str] = field(default_factory=list)
    verification_requirements: Dict[str, Any] = field(default_factory=dict)
    existing_tools: List[str] = field(default_factory=list)
    existing_capabilities: List[str] = field(default_factory=list)
    coding_constraints: List[str] = field(default_factory=list)
    platform: str = "local"
    risk_level: str = "safe"
    side_effect: str = "READ_ONLY"
    error_feedback: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ToolSynthesisResult:
    """
    Untrusted candidate tool data produced by the synthesis model.
    The model NEVER directly controls ACTIVE, APPROVED, REGISTERED, or authorization.
    """

    success: bool
    manifest: Optional[ToolManifest] = None
    source_code: str = ""
    test_code: str = ""
    reasoning: str = ""
    provider: str = ""
    model: str = ""
    source_digest: str = ""
    failure_reason: str = ""
    raw_response: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "tool_name": self.manifest.name if self.manifest else "",
            "source_digest": self.source_digest,
            "provider": self.provider,
            "model": self.model,
            "failure_reason": self.failure_reason,
            "reasoning": self.reasoning,
        }


class ToolSynthesisEngine:
    """
    Orchestrates autonomous tool design, LLM synthesis, deterministic validation,
    and promotion into ToolRegistry.
    """

    def __init__(
        self,
        llm: LLM,
        builder: ToolBuilder,
        validator: Optional[ToolValidator] = None,
        capability_registry=None,
    ):
        self.llm = llm
        self.builder = builder
        self.validator = validator or builder.validator
        self.capability_registry = capability_registry or default_capability_registry

    def is_eligible_for_synthesis(self, gap: CapabilityGap) -> Tuple[bool, str]:
        """
        Deterministic eligibility check: rejects ambiguous, duplicate, unsupported,
        or excessive-risk synthesis requests.
        """
        if not gap.is_synthesizable:
            return False, f"Capability match state is '{gap.match_state}'; not synthesizable"

        # Check recursion depth
        if self.builder.current_depth >= self.builder.max_synthesis_depth:
            return False, f"Max synthesis depth ({self.builder.max_synthesis_depth}) exceeded"

        # Check if capability or tool already exists in live registry
        target_name = gap.requested_capability.replace(".", "_").replace("-", "_").lower()
        if self.builder.registry.has(target_name) and self.builder.registry.is_active(target_name):
            return False, f"Tool '{target_name}' already exists and is active in registry"

        cap_obj = self.capability_registry.get(gap.requested_capability)
        if cap_obj is not None and getattr(cap_obj, "state", None) == "AVAILABLE":
            return False, f"Capability '{gap.requested_capability}' is already registered and available"

        # Check platform feasibility
        current_plat = "win32" if sys.platform == "win32" else "linux"
        if gap.platform in ("android", "emulator") and current_plat not in ("android", "emulator"):
            return False, f"Autonomous Android tool generation is unsupported on host platform '{current_plat}'"

        # Check risk level
        risk_val = (gap.risk or "safe").lower()
        if risk_val not in ("safe", "sensitive", "dangerous"):
            return False, f"Invalid or unrecognized risk level: '{gap.risk}'"

        return True, ""

    def build_synthesis_prompt(self, request: ToolSynthesisRequest) -> str:
        """
        Renders the strict system instruction and user prompt for candidate tool generation.
        Enforces security rules, AST constraints, and protocol conformance.
        """
        system_instruction = (
            "You are generating an untrusted candidate tool for AURA.\n"
            "The candidate will be statically validated and sandbox-tested.\n"
            "You cannot mark the tool approved or active.\n"
            "You cannot bypass AURA ToolExecutor.\n"
            "You cannot access host files, network, subprocesses, reflection, or security-sensitive APIs.\n"
            "You must produce the requested schema/source/tests only."
        )

        input_fields_desc = []
        for name, spec in request.input_requirements.items():
            t_type = spec.get("type", "string") if isinstance(spec, dict) else str(spec)
            input_fields_desc.append(f"- {name} ({t_type})")

        inputs_str = "\n".join(input_fields_desc) if input_fields_desc else "- none (empty args)"

        user_content = f"""===== CANDIDATE TOOL DESIGN SPECIFICATION =====
Intent: {request.intent}
Requested Capability: {request.requested_capability}
Target Tool Name: {request.tool_name or request.requested_capability.replace('.', '_').lower()}
Risk Level: {request.risk_level}
Side Effect: {request.side_effect}
Inputs Required:
{inputs_str}

===== ARCHITECTURAL CONTRACT REQUIREMENTS =====
1. The tool MUST inherit from `tools.base.Tool`.
2. MUST declare `name`, `capability`, `risk = ToolRisk.{request.risk_level.upper()}`, `side_effect = SideEffect.{request.side_effect}`.
3. MUST declare `parameters = (Parameter(name=..., type=..., required=...), ...)`.
4. MUST implement `def execute(self, ...) -> ToolResult:`.
   - ToolResult MUST use `ok=True`, `output=str(...)`, `status=ToolStatus.SUCCESS.value`, `data={{...}}`, and `evidence=[Evidence(kind=EvidenceKind.RETURN_VALUE, source=self.name, verified=True, detail=...)]`.
   - On error, return `ToolResult(ok=False, error=..., status=ToolStatus.FAILED.value)`.
5. Imports allowed: only standard safe math/string libraries and `from tools.base import Tool, ToolRisk, ToolResult, Parameter` and `from tools.outcome import Evidence, EvidenceKind, ToolStatus, SideEffect`.
6. FORBIDDEN: subprocess, socket, network, filesystem mutation, eval, exec, compile, __import__, __globals__, reflection.
7. You MUST also provide self-contained test code verifying `execute()` on representative inputs.
8. MUST be strictly valid Python 3 syntax (no trailing semicolons, no malformed expressions).
{f'''
===== PREVIOUS ATTEMPT FAILED (MUST FIX THESE ERRORS) =====
{request.error_feedback}
''' if request.error_feedback else ''}
===== OUTPUT FORMAT =====
Respond ONLY with a JSON object adhering to this schema:
{{
  "name": "{request.tool_name or request.requested_capability.replace('.', '_').lower()}",
  "description": "Short explanation of what the tool does",
  "source_code": "<complete python source code of the tool class>",
  "test_code": "<complete python test code executing the tool class and asserting correctness>",
  "reasoning": "Brief rationale for the implementation"
}}
"""
        return f"{system_instruction}\n\n{user_content}"

    def parse_synthesis_response(self, raw_text: str) -> Tuple[bool, str, str, str, str, str]:
        """
        Deterministically extracts tool name, description, source code, test code, and reasoning
        from JSON or fenced markdown blocks in the model's response.
        Returns: (ok, name, description, source_code, test_code, error)
        """
        text = raw_text.strip()

        # 1. Attempt raw JSON parse
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return (
                    True,
                    data.get("name", "").strip(),
                    data.get("description", "").strip(),
                    data.get("source_code", "").strip(),
                    data.get("test_code", "").strip(),
                    "",
                )
        except json.JSONDecodeError:
            pass

        # 2. Extract JSON block from markdown code fence
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(1))
                if isinstance(data, dict):
                    return (
                        True,
                        data.get("name", "").strip(),
                        data.get("description", "").strip(),
                        data.get("source_code", "").strip(),
                        data.get("test_code", "").strip(),
                        "",
                    )
            except json.JSONDecodeError:
                pass

        # 3. Fallback: Search for python source and test code fences
        py_blocks = re.findall(r"```(?:python)?\s*(.*?)\s*```", text, re.DOTALL)
        if py_blocks:
            src = py_blocks[0].strip()
            test_c = py_blocks[1].strip() if len(py_blocks) > 1 else ""
            return True, "", "", src, test_c, ""

        return False, "", "", "", "", "Failed to parse JSON or code blocks from synthesis response"

    def synthesize(self, request: ToolSynthesisRequest) -> ToolSynthesisResult:
        """
        Queries the LLM provider to synthesize candidate code for the requested capability.
        Produces untrusted candidate data.
        """
        prompt = self.build_synthesis_prompt(request)
        provider_name = getattr(self.llm, "provider_name", type(self.llm).__name__)
        model_name = getattr(self.llm, "model", "default")

        try:
            logger.info("Requesting autonomous tool synthesis via %s for %s", provider_name, request.requested_capability)
            raw_response = self.llm.generate(prompt)
        except Exception as gen_err:
            logger.error("LLM synthesis generation failed: %s", gen_err)
            return ToolSynthesisResult(
                success=False,
                provider=provider_name,
                model=model_name,
                failure_reason=f"Model generation error: {gen_err}",
            )

        ok, name, desc, source_code, test_code, parse_err = self.parse_synthesis_response(raw_response)
        if not ok or not source_code:
            logger.warning("Synthesis candidate parsing failed: %s", parse_err)
            return ToolSynthesisResult(
                success=False,
                provider=provider_name,
                model=model_name,
                raw_response=raw_response,
                failure_reason=parse_err or "No source code extracted",
            )

        # Deterministic name normalization
        tool_name = (name or request.tool_name or request.requested_capability.replace(".", "_").lower()).strip()
        if not tool_name.isidentifier():
            tool_name = f"tool_{tool_name.replace('-', '_')}"

        # Hash candidate source code
        source_digest = hashlib.sha256(source_code.strip().encode("utf-8")).hexdigest()

        # Check for forbidden recursion tokens
        for tok in FORBIDDEN_SYNTHESIS_TOKENS:
            if tok in source_code or tok in test_code:
                logger.warning("Candidate tool contains forbidden token '%s'; rejected", tok)
                return ToolSynthesisResult(
                    success=False,
                    source_code=source_code,
                    test_code=test_code,
                    source_digest=source_digest,
                    provider=provider_name,
                    model=model_name,
                    failure_reason=f"Candidate code contains forbidden security/recursion token: '{tok}'",
                )

        # Build candidate ToolManifest
        params: List[Dict[str, Any]] = []
        for p_name, p_spec in request.input_requirements.items():
            p_type = p_spec.get("type", "string") if isinstance(p_spec, dict) else "string" if isinstance(p_spec, dict) else str(p_spec)
            params.append({
                "name": p_name,
                "type": p_type,
                "description": p_spec.get("description", "") if isinstance(p_spec, dict) else "",
                "required": p_spec.get("required", True) if isinstance(p_spec, dict) else True,
            })

        manifest = ToolManifest(
            name=tool_name,
            version=1,
            description=desc or request.description or request.intent,
            capability=request.requested_capability,
            parameters=params,
            risk_level=request.risk_level.lower(),
            side_effect=request.side_effect.upper(),
            source_code=source_code,
            test_code=test_code,
            source_digest=source_digest,
            metadata={
                "synthesized_by": provider_name,
                "synthesized_model": model_name,
            },
        )

        return ToolSynthesisResult(
            success=True,
            manifest=manifest,
            source_code=source_code,
            test_code=test_code,
            provider=provider_name,
            model=model_name,
            source_digest=source_digest,
            raw_response=raw_response,
        )

    def synthesize_and_promote(
        self,
        gap: CapabilityGap,
        approver: str = "auto_policy",
        allow_upgrade: bool = False,
    ) -> Tuple[Optional[ToolProtocol], ValidationReport, ToolSynthesisResult]:
        """
        Executes the end-to-end self-extension lifecycle:
        1. Eligibility check
        2. LLM synthesis (untrusted candidate generation)
        3. Deterministic static AST + sandbox test validation
        4. Deterministic approval evaluation
        5. Promotion, dynamic authorization & SQLite persistence
        """
        eligible, reason = self.is_eligible_for_synthesis(gap)
        if not eligible:
            logger.warning("Capability gap %s not eligible for synthesis: %s", gap.gap_id, reason)
            report = ValidationReport(passed=False, errors=[f"ELIGIBILITY_REJECTED: {reason}"])
            synth_res = ToolSynthesisResult(success=False, failure_reason=reason)
            return None, report, synth_res

        target_tool_name = gap.requested_capability.replace(".", "_").replace("-", "_").lower()
        if not target_tool_name.isidentifier():
            target_tool_name = f"tool_{target_tool_name}"

        req = ToolSynthesisRequest(
            intent=gap.intent,
            requested_capability=gap.requested_capability,
            tool_name=target_tool_name,
            description=gap.reason,
            input_requirements=gap.required_input,
            output_requirements=gap.required_output,
            platform=gap.platform,
            risk_level=gap.risk or "safe",
        )

        gap.status = GapStatus.DESIGNED.value

        manifest = None
        validation_report = None
        synth_result = None
        max_attempts = 3

        for attempt in range(max_attempts):
            synth_result = self.synthesize(req)
            if not synth_result.success or not synth_result.manifest:
                if attempt < max_attempts - 1:
                    req.error_feedback = f"Synthesis response could not be parsed: {synth_result.failure_reason}. Ensure strictly valid JSON adhering to schema."
                    continue
                gap.status = GapStatus.REJECTED.value
                report = ValidationReport(passed=False, errors=[synth_result.failure_reason])
                return None, report, synth_result

            manifest = synth_result.manifest
            manifest.gap_id = gap.gap_id
            gap.status = GapStatus.GENERATED.value

            # Deterministic AST and dynamic sandbox validation
            validation_report = self.validator.validate(manifest)
            if validation_report.passed:
                break

            logger.warning("Synthesized tool '%s' attempt %d failed validation: %s", manifest.name, attempt + 1, validation_report.errors)
            if attempt < max_attempts - 1:
                req.error_feedback = f"Previous code failed validation with errors: {validation_report.errors}. Fix all syntax/semantic errors and ensure valid Python code."
        else:
            gap.status = GapStatus.REJECTED.value
            return None, validation_report, synth_result

        gap.status = GapStatus.VALIDATED.value

        # Approval gate evaluation
        if not self.builder.is_approved(manifest):
            if approver and approver != "auto_policy":
                self.builder.approve(manifest, approver=approver)
            elif manifest.risk_level.lower() == "safe" and self.builder.auto_approve_safe:
                self.builder.approve(manifest, approver="auto_policy")
            else:
                logger.warning("Synthesized tool '%s' requires manual approval (risk: %s)", manifest.name, manifest.risk_level)
                return None, validation_report, synth_result

        # Promotion: instantiates, registers, authorizes dynamically, and writes SQLite provenance
        try:
            tool_instance = self.builder.promote(
                manifest=manifest,
                report=validation_report,
                allow_upgrade=allow_upgrade,
                approver=approver,
            )
            gap.status = GapStatus.RESOLVED.value
            logger.info("Successfully synthesized, validated, and promoted dynamic tool: %s", manifest.name)
            return tool_instance, validation_report, synth_result
        except Exception as prom_err:
            logger.error("Promotion failed for '%s': %s", manifest.name, prom_err)
            gap.status = GapStatus.REJECTED.value
            validation_report.errors.append(f"PROMOTION_ERROR: {prom_err}")
            validation_report.passed = False
            return None, validation_report, synth_result
