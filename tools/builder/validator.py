"""
Deterministic validator for candidate tool code and manifests.

Performs static AST security/structure inspection, schema checking, hash verification,
and dynamic sandbox test execution.
"""

import ast
import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set

from core.logger import logger
from tools.base import ToolRisk
from tools.builder.manifest import ToolManifest
from tools.outcome import SideEffect
from tools.sandbox.runner import SandboxResult, SandboxRunner

# Imports considered dangerous for generated tools unless specifically authorized
DISALLOWED_MODULES: Set[str] = {
    "ctypes",
    "cffi",
    "pty",
    "socketserver",
    "multiprocessing",
    "subprocess",
    "winreg",
    "_winapi",
    "msvcrt",
    "builtins",
    "importlib",
    "pickle",
    "shelve",
    "marshal",
}

# Forbidden function calls in generated tool code
DISALLOWED_CALLS: Set[str] = {
    "eval",
    "exec",
    "compile",
    "__import__",
    "os.system",
    "os.popen",
    "os.spawn",
    "os.posix_spawn",
    "subprocess.Popen",
    "subprocess.run",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
}

# Forbidden attribute lookups that can be used for reflection bypasses
DISALLOWED_ATTRIBUTES: Set[str] = {
    "__subclasses__",
    "__globals__",
    "__code__",
    "__builtins__",
    "__class__",
    "__bases__",
    "__mro__",
}



@dataclass
class ValidationReport:
    """Outcome of static and dynamic tool validation."""

    passed: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    test_results: List[Dict[str, Any]] = field(default_factory=list)
    checks: Dict[str, bool] = field(default_factory=dict)
    duration: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


class ToolValidator:
    """
    Validates tool manifests and source code statically and dynamically.
    LLM output is NEVER validation; this deterministic validator is the sole authority.
    """

    def __init__(self, sandbox: Optional[SandboxRunner] = None):
        self.sandbox = sandbox or SandboxRunner()

    def validate(
        self,
        manifest: ToolManifest,
        test_timeout: float = 10.0,
    ) -> ValidationReport:
        """
        Full deterministic validation pipeline:
        1. Syntax & Manifest Schema
        2. Hash & Integrity
        3. Static AST Analysis (imports, calls, structure)
        4. Dynamic Sandbox Test Execution
        """
        start_time = time.monotonic()
        errors: List[str] = []
        warnings: List[str] = []
        test_results: List[Dict[str, Any]] = []
        checks: Dict[str, bool] = {}

        # 1. Manifest Schema & Name Checks
        if not manifest.name or not manifest.name.isidentifier():
            errors.append(f"Invalid tool name: '{manifest.name}' (must be a valid Python identifier)")
            checks["name_valid"] = False
        else:
            checks["name_valid"] = True

        try:
            ToolRisk(manifest.risk_level.lower())
            checks["risk_valid"] = True
        except ValueError:
            errors.append(f"Invalid risk '{manifest.risk_level}'; must be one of {[r.value for r in ToolRisk]}")
            checks["risk_valid"] = False

        raw_side_effect = manifest.side_effect.upper()
        if raw_side_effect.startswith("SIDEEFFECT."):
            raw_side_effect = raw_side_effect.split(".", 1)[1]
        try:
            SideEffect(raw_side_effect)
            checks["side_effect_valid"] = True
        except ValueError:
            errors.append(f"Invalid side_effect '{manifest.side_effect}'; must be one of {[s.value for s in SideEffect]}")
            checks["side_effect_valid"] = False

        if not manifest.source_code.strip():
            errors.append("source_code cannot be empty")
            checks["source_present"] = False
        else:
            checks["source_present"] = True

        # 2. Cryptographic Hash Integrity Check
        if manifest.source_code.strip():
            expected_hash = hashlib.sha256(manifest.source_code.strip().encode("utf-8")).hexdigest()
            if manifest.source_digest and manifest.source_digest != expected_hash:
                errors.append(
                    f"Integrity check failed: source_digest mismatch (expected {expected_hash}, got {manifest.source_digest})"
                )
                checks["hash_integrity"] = False
            else:
                checks["hash_integrity"] = True
                manifest.source_digest = expected_hash

        # 3. Static AST Analysis
        if manifest.source_code.strip():
            ast_errors, ast_warnings = self._inspect_ast(manifest.source_code)
            errors.extend(ast_errors)
            warnings.extend(ast_warnings)
            checks["ast_security"] = len(ast_errors) == 0

        # 4. Dynamic Sandbox Test Execution
        # Only run in sandbox if static analysis passed without errors!
        if not errors:
            test_res = self._run_sandbox_tests(manifest, timeout=test_timeout)
            test_results.append(test_res)
            checks["sandbox_execution"] = test_res.get("passed", False)
            if not test_res.get("passed", False):
                errors.append(f"Sandbox test failed: {test_res.get('error') or test_res.get('stderr')}")
        else:
            checks["sandbox_execution"] = False

        duration = time.monotonic() - start_time
        passed = len(errors) == 0

        return ValidationReport(
            passed=passed,
            errors=errors,
            warnings=warnings,
            test_results=test_results,
            checks=checks,
            duration=duration,
        )

    def _inspect_ast(self, source_code: str) -> tuple[List[str], List[str]]:
        errors: List[str] = []
        warnings: List[str] = []

        try:
            tree = ast.parse(source_code)
        except SyntaxError as e:
            return [f"Syntax error: {e}"], []

        has_tool_class = False

        for node in ast.walk(tree):
            # Check module imports
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_mod = alias.name.split(".")[0]
                    if root_mod in DISALLOWED_MODULES:
                        errors.append(f"Disallowed import: '{alias.name}'")
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root_mod = node.module.split(".")[0]
                    if root_mod in DISALLOWED_MODULES:
                        errors.append(f"Disallowed import from: '{node.module}'")

            # Check for banned calls: eval, exec, subprocess, etc.
            elif isinstance(node, ast.Call):
                func_name = ""
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    if isinstance(node.func.value, ast.Name):
                        func_name = f"{node.func.value.id}.{node.func.attr}"

                if func_name in DISALLOWED_CALLS:
                    errors.append(f"Disallowed function call: '{func_name}'")

            # Check for banned attribute lookups (e.g. __subclasses__, __globals__)
            elif isinstance(node, ast.Attribute):
                if node.attr in DISALLOWED_ATTRIBUTES:
                    errors.append(f"Disallowed attribute access: '{node.attr}'")

            # Look for a class definition with an execute method
            elif isinstance(node, ast.ClassDef):
                has_execute = any(
                    isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "execute"
                    for item in node.body
                )
                if has_execute:
                    has_tool_class = True

        if not has_tool_class:
            warnings.append("No class with an 'execute' method found in AST")

        return errors, warnings

    def _run_sandbox_tests(self, manifest: ToolManifest, timeout: float) -> Dict[str, Any]:
        """
        Build a test harness script and execute it in the sandbox.
        """
        if manifest.test_code.strip():
            harness = f"""
{manifest.source_code}

# --- TEST CODE ---
{manifest.test_code}
"""
        else:
            harness = f"""
{manifest.source_code}

# Automatic smoke test: locate Tool class and verify attributes
import inspect

tool_classes = [
    cls for name, cls in list(locals().items())
    if inspect.isclass(cls)
    and hasattr(cls, "execute")
    and hasattr(cls, "risk")
    and not inspect.isabstract(cls)
    and cls.__name__ not in ("Tool", "ToolProtocol")
]

if not tool_classes:
    raise RuntimeError("No valid tool class with risk and execute attributes found.")

instance = tool_classes[0]()
assert hasattr(instance, "execute"), "Tool instance missing execute method"
print("SMOKE TEST PASSED")
"""

        res: SandboxResult = self.sandbox.execute_code(harness, timeout=timeout)
        passed = res.ok and (res.exit_code == 0)

        return {
            "passed": passed,
            "exit_code": res.exit_code,
            "stdout": res.stdout,
            "stderr": res.stderr,
            "timed_out": res.timed_out,
            "duration": res.duration,
            "error": res.error,
        }
