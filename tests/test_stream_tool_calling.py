"""
Tests for Tool Awareness, Speculative Stream Buffering, and Streaming Tool Calling.
"""

import json
from typing import Iterator

from brain.conversation import ConversationManager, _is_tool_request_prefix
from brain.prompt_builder import PromptBuilder
from brain.prompt_sections import TOOLS
from tools.base import Tool, ToolResult, ToolRisk, ok, Parameter
from tools.executor import ToolExecutor, ToolPolicy
from tools.registry import ToolRegistry


class DummyStore:
    def __init__(self):
        self.turns = []

    def get_recent(self, limit=20, session_id="default"):
        return self.turns

    def save(self, user_msg, aura_msg, session_id="default"):
        self.turns.append((user_msg, aura_msg))


class ScriptedStreamLLM:
    """Mock LLM supporting both streaming and multi-round generate."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.prompts = []

    def stream(self, prompt: str) -> Iterator[str]:
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("No more responses scripted")
        resp = self.responses.pop(0)
        # Yield in small chunk fragments
        chunk_size = 4
        for i in range(0, len(resp), chunk_size):
            yield resp[i : i + chunk_size]

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("No more responses scripted")
        return self.responses.pop(0)


class EchoMathTool(Tool):
    name = "mock_calc"
    description = "Mock calculator tool"
    risk = ToolRisk.SAFE
    capability = "sandbox.execute"
    parameters = (
        Parameter(name="expr", description="expression to evaluate", required=True),
    )

    def execute(self, expr: str = "", **kwargs) -> ToolResult:
        return ok(f"evaluated:{expr}", tool=self.name)


def test_is_tool_request_prefix_detection():
    # Regular conversational prefixes
    assert _is_tool_request_prefix("Chào anh, hôm nay thế nào?")[0] is False
    assert _is_tool_request_prefix("Hello Aura")[0] is False
    assert _is_tool_request_prefix("Tớ là Aura")[0] is False

    # Tool call prefixes
    assert _is_tool_request_prefix('{"tool": "python_sandbox"')[0] is True
    assert _is_tool_request_prefix('```json\n{"tool": "create_custom_tool"')[0] is True
    assert _is_tool_request_prefix('{"tool":')[0] is True

    # Ambiguous short prefix waiting for more characters
    is_tool, is_decided = _is_tool_request_prefix('{"some_key":')
    assert is_tool is False
    assert is_decided is False  # Length < 40, needs more chars to know if "tool" appears


def test_prompt_builder_contains_tool_awareness():
    from brain.message import Message

    builder = PromptBuilder()
    prompt = builder.build(
        history=[],
        user_message=Message(role="user", content="Cần tính toán"),
        tools="mock_calc: calculate numbers",
    )
    assert TOOLS in prompt
    assert "TOOL AWARENESS & CAPABILITIES" in prompt
    assert "python_sandbox" in prompt
    assert "create_custom_tool" in prompt


def test_chat_stream_normal_text_yields_immediately():
    llm = ScriptedStreamLLM("Chào anh, hôm nay anh có khoẻ không?")
    mem = DummyStore()
    conv = ConversationManager(memory=mem, builder=PromptBuilder(), llm=llm)

    chunks = list(conv.chat_stream("Xin chào"))
    full_text = "".join(chunks)
    assert "Chào anh" in full_text
    # Should have streamed multiple small fragments without swallowing
    assert len(chunks) > 1


def test_chat_stream_tool_calling_swallows_json_and_returns_grounded_answer():
    # Round 1: Model emits tool call JSON
    # Round 2: Model answers using the tool result
    tool_call_json = '{"tool": "mock_calc", "arguments": {"expr": "2+2"}}'
    final_answer = "Kết quả phép tính 2+2 là evaluated:2+2."

    llm = ScriptedStreamLLM(tool_call_json, final_answer)
    mem = DummyStore()

    registry = ToolRegistry()
    registry.register(EchoMathTool())
    policy = ToolPolicy(enabled=True, allowed=frozenset({"mock_calc"}))
    executor = ToolExecutor(registry=registry, policy=policy)

    conv = ConversationManager(
        memory=mem,
        builder=PromptBuilder(),
        llm=llm,
        tools=executor,
    )

    chunks = list(conv.chat_stream("Tính 2+2 giúp anh", offer_tools=True))
    output = "".join(chunks)

    # CRITICAL INVARIANT: The raw tool JSON must NEVER be streamed to the user
    assert '{"tool":' not in output
    assert '"arguments":' not in output

    # The final grounded answer must be returned
    assert "Kết quả phép tính 2+2 là evaluated:2+2." in output
    assert len(llm.prompts) == 2  # 1st for tool call, 2nd for grounded answer


def test_chat_stream_with_sandbox_python_tool():
    from tools.builtins.sandbox import ExecuteSandboxPythonTool

    tool_call_json = '{"tool": "python_sandbox", "arguments": {"code": "print(2**10)"}}'
    final_answer = "2 lũy thừa 10 là 1024."

    llm = ScriptedStreamLLM(tool_call_json, final_answer)
    mem = DummyStore()

    registry = ToolRegistry()
    sandbox_tool = ExecuteSandboxPythonTool()
    registry.register(sandbox_tool)

    policy = ToolPolicy(enabled=True, allowed=frozenset({"python_sandbox"}))
    executor = ToolExecutor(registry=registry, policy=policy)

    conv = ConversationManager(
        memory=mem,
        builder=PromptBuilder(),
        llm=llm,
        tools=executor,
    )

    chunks = list(conv.chat_stream("Tính 2^10 trong sandbox", offer_tools=True))
    output = "".join(chunks)

    assert '{"tool":' not in output
    assert "2 lũy thừa 10 là 1024." in output
    # Second prompt must contain TOOL RESULTS with the real output 1024 from sandbox
    assert "1024" in llm.prompts[1]
