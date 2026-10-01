"""
Built-in tools for Python sandbox execution and autonomous custom tool creation.

Provides:
- ExecuteSandboxPythonTool (`python_sandbox`): Safe, isolated execution of Python code
- SynthesizeCustomTool (`create_custom_tool`): Dynamic synthesis, validation, and promotion
  of custom tools into the active ToolRegistry
"""

from typing import Any, Optional

from core.logger import logger
from tools.base import Parameter, Tool, ToolResult, ToolRisk
from tools.outcome import Evidence, EvidenceKind, SideEffect, ToolStatus
from tools.sandbox.runner import SandboxRunner


class ExecuteSandboxPythonTool(Tool):
    """
    Execute Python 3 code in an isolated, scrubbed sandbox subprocess.
    """

    name = "python_sandbox"
    description = (
        "Execute Python 3 code in a secure, isolated sandbox environment. "
        "Returns stdout, stderr, execution duration, and exit code. "
        "Use this tool whenever you need to perform calculations, algorithms, "
        "data analysis, logic verification, or text transformations."
    )
    risk = ToolRisk.SAFE
    capability = "sandbox.execute"
    side_effect = SideEffect.IDEMPOTENT

    parameters = (
        Parameter(
            name="code",
            description="The complete Python 3 source code snippet to execute.",
            required=True,
            type="string",
        ),
        Parameter(
            name="timeout",
            description="Optional execution timeout in seconds (default 20.0s, max 30.0s).",
            required=False,
            type="number",
        ),
    )

    def __init__(self, runner: Optional[SandboxRunner] = None):
        self.runner = runner or SandboxRunner(default_timeout=20.0)

    def execute(self, code: str, timeout: float = 20.0, **kwargs) -> ToolResult:
        code_str = str(code or "").strip()
        if not code_str:
            return ToolResult(
                ok=False,
                error="No code provided to execute in sandbox",
                status=ToolStatus.FAILED.value,
                capability=self.capability,
            )

        eff_timeout = min(max(1.0, float(timeout or 20.0)), 30.0)

        try:
            res = self.runner.execute_code(code_str, timeout=eff_timeout)
        except Exception as exc:
            logger.warning("SandboxRunner raised exception: %s", exc)
            return ToolResult(
                ok=False,
                error=f"Sandbox execution failed with system error: {exc}",
                status=ToolStatus.FAILED.value,
                capability=self.capability,
            )

        if res.timed_out:
            return ToolResult(
                ok=False,
                error=f"Execution timed out after {eff_timeout:.1f}s",
                status=ToolStatus.TIMEOUT.value,
                capability=self.capability,
                data={
                    "timed_out": True,
                    "duration": res.duration,
                    "stdout": res.stdout,
                    "stderr": res.stderr,
                },
            )

        output_text = res.stdout.strip() if res.stdout else ""
        if res.stderr and not res.ok:
            output_text = f"Errors:\n{res.stderr.strip()}\nOutput:\n{output_text}".strip()

        evidence = [
            Evidence(
                kind=EvidenceKind.RETURN_VALUE,
                source=self.name,
                verified=res.ok,
                detail=f"exit_code={res.exit_code}, duration={res.duration:.2f}s",
            )
        ]

        return ToolResult(
            ok=res.ok,
            output=output_text or f"(process finished with exit code {res.exit_code})",
            error=res.error if not res.ok else None,
            status=ToolStatus.SUCCESS.value if res.ok else ToolStatus.FAILED.value,
            capability=self.capability,
            data={
                "exit_code": res.exit_code,
                "stdout": res.stdout,
                "stderr": res.stderr,
                "duration": res.duration,
            },
            evidence=evidence,
        )


class SynthesizeCustomTool(Tool):
    """
    Synthesize, test in sandbox, validate, and register a new tool in ToolRegistry.
    """

    name = "create_custom_tool"
    description = (
        "Design, validate, test in sandbox, and register a brand new custom tool "
        "into Aura's live ToolRegistry. Once created, the tool becomes immediately "
        "available for Aura to call. Use this when the user asks to create or teach "
        "a new tool, or when a reusable capability is needed."
    )
    risk = ToolRisk.SAFE
    capability = "tools.synthesize"
    side_effect = SideEffect.NON_IDEMPOTENT

    parameters = (
        Parameter(
            name="tool_name",
            description="The unique snake_case name for the new tool (e.g. calculate_factorial).",
            required=True,
            type="string",
        ),
        Parameter(
            name="description",
            description="Clear explanation of what the tool does and what arguments it accepts.",
            required=True,
            type="string",
        ),
        Parameter(
            name="source_code",
            description=(
                "Python code defining the Tool subclass with name, description, risk, "
                "parameters, and execute(self, ...) returning ToolResult."
            ),
            required=True,
            type="string",
        ),
        Parameter(
            name="test_code",
            description="Python test code instantiating and verifying the tool's execute() method.",
            required=False,
            type="string",
        ),
        Parameter(
            name="risk_level",
            description="Risk level: 'safe', 'sensitive', or 'dangerous'. Default is 'safe'.",
            required=False,
            type="string",
        ),
    )

    def __init__(self, registry=None, builder=None, validator=None):
        self.registry = registry
        self.builder = builder
        self.validator = validator

    def _ensure_builder(self):
        if self.builder is not None:
            return self.builder
        from tools.builder.builder import ToolBuilder
        from tools.builder.validator import ToolValidator

        validator = self.validator or ToolValidator()
        self.builder = ToolBuilder(registry=self.registry, validator=validator, auto_approve_safe=True)
        return self.builder

    def execute(
        self,
        tool_name: str,
        description: str,
        source_code: str,
        test_code: str = "",
        risk_level: str = "safe",
        **kwargs,
    ) -> ToolResult:
        from tools.builder.manifest import ToolManifest
        from tools.builder.synthesis import FORBIDDEN_SYNTHESIS_TOKENS
        import hashlib

        clean_name = str(tool_name or "").strip().lower().replace("-", "_")
        if not clean_name.isidentifier():
            return ToolResult(
                ok=False,
                error=f"Tool name '{clean_name}' is not a valid Python identifier",
                status=ToolStatus.FAILED.value,
                capability=self.capability,
            )

        src = str(source_code or "").strip()
        if not src:
            return ToolResult(
                ok=False,
                error="source_code is required to build a custom tool",
                status=ToolStatus.FAILED.value,
                capability=self.capability,
            )

        # Check for forbidden recursion/hazardous tokens
        for tok in FORBIDDEN_SYNTHESIS_TOKENS:
            if tok in src or tok in (test_code or ""):
                return ToolResult(
                    ok=False,
                    error=f"Source code contains forbidden security token: '{tok}'",
                    status=ToolStatus.FAILED.value,
                    capability=self.capability,
                )

        # If no test code provided, generate a minimal instantiation check
        tests = str(test_code or "").strip()
        if not tests:
            tests = f"""
from tools.base import ToolResult
tool = {clean_name.capitalize()}Tool() if '{clean_name.capitalize()}Tool' in globals() else None
if tool is None:
    for obj in list(globals().values()):
        if isinstance(obj, type) and getattr(obj, 'name', '') == '{clean_name}':
            tool = obj()
            break
assert tool is not None, "Tool instance could not be created"
"""

        digest = hashlib.sha256(src.encode("utf-8")).hexdigest()
        manifest = ToolManifest(
            name=clean_name,
            version=1,
            description=str(description or "").strip(),
            capability=f"custom.{clean_name}",
            risk_level=str(risk_level or "safe").strip().lower(),
            side_effect="READ_ONLY",
            source_code=src,
            test_code=tests,
            source_digest=digest,
        )

        builder = self._ensure_builder()

        # 1. Deterministic validation (AST + Sandbox execution of test code)
        val_report = builder.validator.validate(manifest)
        if not val_report.passed:
            err_msg = "; ".join(val_report.errors)
            return ToolResult(
                ok=False,
                error=f"Tool validation failed: {err_msg}",
                status=ToolStatus.FAILED.value,
                capability=self.capability,
                data={"errors": val_report.errors},
            )

        # 2. Promote, dynamically authorize, and register
        try:
            builder.approve(manifest, approver="user_request")
            promoted_tool = builder.promote(manifest, val_report, approver="user_request")
            
            # Dynamically authorize tool in the registry so it can be invoked immediately
            if self.registry is not None and hasattr(self.registry, "dynamically_authorize"):
                self.registry.dynamically_authorize(clean_name)

            evidence = [
                Evidence(
                    kind=EvidenceKind.POSTCONDITION,
                    source=self.name,
                    verified=True,
                    reference=clean_name,
                    detail=f"Tool {clean_name} registered and dynamically authorized",
                )
            ]

            return ToolResult(
                ok=True,
                output=(
                    f"✓ Công cụ '{clean_name}' đã được tạo, kiểm thử thành công trong sandbox "
                    f"và đăng ký vào hệ thống! Aura hiện có thể sử dụng công cụ này ngay lập tức."
                ),
                status=ToolStatus.SUCCESS.value,
                capability=self.capability,
                data={
                    "tool_name": clean_name,
                    "digest": digest,
                    "status": "ACTIVE",
                },
                evidence=evidence,
            )

        except Exception as exc:
            logger.error("Promotion of tool %s failed: %s", clean_name, exc)
            return ToolResult(
                ok=False,
                error=f"Tool promotion failed: {exc}",
                status=ToolStatus.FAILED.value,
                capability=self.capability,
            )
