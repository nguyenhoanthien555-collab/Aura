"""
Unit tests for SandboxRunner and Built-in Sandbox / Custom Tool Synthesis tools.
"""

import sys
import pytest
from tools.base import ToolRisk, ToolStatus
from tools.builtins.sandbox import ExecuteSandboxPythonTool, SynthesizeCustomTool
from tools.outcome import EvidenceKind
from tools.registry import ToolRegistry
from tools.sandbox.runner import SandboxRunner


def test_sandbox_runner_basic_execution():
    runner = SandboxRunner(default_timeout=15.0)
    res = runner.execute_code("print('AURA_SANDBOX_SUCCESS')")
    assert res.ok is True
    assert res.exit_code == 0
    assert "AURA_SANDBOX_SUCCESS" in res.stdout
    assert res.timed_out is False
    assert res.duration > 0.0


def test_sandbox_runner_timeout_and_process_cleanup():
    runner = SandboxRunner(default_timeout=1.0)
    # Code sleeps for 10 seconds, but timeout is set to 1.0s
    res = runner.execute_code("import time\ntime.sleep(10)", timeout=1.0)
    assert res.ok is False
    assert res.timed_out is True
    assert res.exit_code == -1
    assert "timed out after 1.0s" in res.error
    assert res.duration < 5.0  # Cleanly terminated without hanging


def test_execute_sandbox_python_tool_success():
    tool = ExecuteSandboxPythonTool()
    res = tool.execute(code="print(2**50)")
    assert res.ok is True
    assert res.status == ToolStatus.SUCCESS.value
    assert "1125899906842624" in res.output
    assert res.data["exit_code"] == 0
    assert len(res.evidence) > 0
    assert res.evidence[0].kind == EvidenceKind.RETURN_VALUE
    assert res.evidence[0].verified is True


def test_execute_sandbox_python_tool_syntax_error():
    tool = ExecuteSandboxPythonTool()
    res = tool.execute(code="def broken_syntax(")
    assert res.ok is False
    assert res.status == ToolStatus.FAILED.value
    assert res.data["exit_code"] != 0
    assert "SyntaxError" in (res.data.get("stderr") or "") or "SyntaxError" in res.output


def test_execute_sandbox_python_tool_empty_code():
    tool = ExecuteSandboxPythonTool()
    res = tool.execute(code="")
    assert res.ok is False
    assert "No code provided" in res.error


def test_synthesize_custom_tool_success():
    registry = ToolRegistry()
    synth_tool = SynthesizeCustomTool(registry=registry)

    custom_code = '''
from tools.base import Tool, ToolResult, ok, Parameter, ToolRisk
from tools.outcome import SideEffect

class ReverseTextTool(Tool):
    name = "reverse_text"
    description = "Reverse a string"
    risk = ToolRisk.SAFE
    capability = "custom.reverse_text"
    side_effect = SideEffect.IDEMPOTENT
    parameters = (
        Parameter(name="text", description="text to reverse", required=True, type="string"),
    )

    def execute(self, text: str = "", **kwargs) -> ToolResult:
        return ok(str(text)[::-1], tool=self.name)
'''

    test_code = '''
from tools.base import ToolResult
tool = ReverseTextTool()
res = tool.execute(text="aura")
assert res.ok is True
assert res.output == "arua"
'''

    res = synth_tool.execute(
        tool_name="reverse_text",
        description="Reverses input text",
        source_code=custom_code,
        test_code=test_code,
        risk_level="safe",
    )

    assert res.ok is True
    assert res.status == ToolStatus.SUCCESS.value
    assert "reverse_text" in res.output
    assert len(res.evidence) > 0
    assert res.evidence[0].kind == EvidenceKind.POSTCONDITION
    assert res.evidence[0].verified is True

    # Check registry integration
    assert registry.has("reverse_text")
    assert registry.is_dynamically_authorized("reverse_text")

    # Call newly created tool directly
    created_tool = registry.get("reverse_text")
    assert created_tool is not None
    exec_res = created_tool.execute(text="Antigravity")
    assert exec_res.ok is True
    assert exec_res.output == "ytivargitnA"


def test_synthesize_custom_tool_rejects_forbidden_tokens():
    registry = ToolRegistry()
    synth_tool = SynthesizeCustomTool(registry=registry)

    hazardous_code = '''
import subprocess
from tools.base import Tool, ToolResult, ok

class HazardousTool(Tool):
    name = "hazardous_tool"
    description = "Bad tool"
    def execute(self, **kwargs) -> ToolResult:
        return ok("bad")
'''

    res = synth_tool.execute(
        tool_name="hazardous_tool",
        description="Hazardous",
        source_code=hazardous_code,
    )

    assert res.ok is False
    assert "forbidden security token" in res.error
    assert not registry.has("hazardous_tool")
