import json
import pytest
from brain.prompt_builder import PromptBuilder
from brain.message import Message
from brain.providers.base import split_prompt
from server.runtime import ServerRuntime
from server.models import ChatRequest

def test_prompt_builder_without_agent_context():
    builder = PromptBuilder()
    prompt = builder.build(
        history=[],
        user_message=Message(role="user", content="hello"),
        context=None
    )
    assert "===== DEVICE STATE =====" not in prompt
    assert "===== ACCESSIBILITY TREE =====" not in prompt
    assert "===== AGENT RULES =====" not in prompt
    assert "hello" in prompt

def test_prompt_builder_with_agent_context():
    builder = PromptBuilder()
    context = {
        "device": {"width": 1080, "height": 2400},
        "app": {"package": "com.example.app", "activity": "ExampleActivity"},
        "accessibility_tree": {
            "package": "com.example.app",
            "nodes": [
                {
                    "id": "node_1",
                    "role": "button",
                    "text": "Search",
                    "clickable": True,
                    "bounds": [40, 120, 340, 190]
                }
            ]
        },
        "user_request": "search for minecraft"
    }
    
    prompt = builder.build(
        history=[],
        user_message=Message(role="user", content="tick"),
        context=context
    )
    
    assert "===== DEVICE STATE =====" in prompt
    assert "Package: com.example.app" in prompt
    assert "Activity: ExampleActivity" in prompt
    assert "Dimensions: 1080x2400" in prompt
    assert "===== ACCESSIBILITY TREE =====" in prompt
    assert "node_1" in prompt
    assert "Search" in prompt
    assert "===== AGENT RULES =====" in prompt
    assert 'The user has requested: "search for minecraft"' in prompt
    
    # Verify split_prompt separates agent rules into system instructions
    system_inst, user_cont = split_prompt(prompt)
    assert "===== AGENT RULES =====" in system_inst
    assert "===== ACCESSIBILITY TREE =====" not in system_inst
    assert "===== DEVICE STATE =====" not in system_inst
    assert "===== ACCESSIBILITY TREE =====" in user_cont
    assert "===== DEVICE STATE =====" in user_cont

def test_runtime_chat_forwards_context(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    
    from brain.providers.gemini import GeminiProvider
    captured_prompts = []
    
    def mock_gemini_generate(self, prompt):
        captured_prompts.append(prompt)
        return '{"action": "click", "node_id": "node_1"}'
        
    monkeypatch.setattr(GeminiProvider, "generate", mock_gemini_generate)
    
    runtime = ServerRuntime()
    context = {
        "device": {"width": 1080, "height": 2400},
        "app": {"package": "com.example.app", "activity": "ExampleActivity"},
        "accessibility_tree": {"package": "com.example.app", "nodes": []},
        "user_request": "click button"
    }
    
    response = runtime.chat(
        message="tick",
        session_id="test-session",
        source="text",
        context=context
    )
    
    assert response.text == '{"action": "click", "node_id": "node_1"}'
    assert len(captured_prompts) == 1
    assert "===== AGENT RULES =====" in captured_prompts[0]


def test_vietnamese_utf8_task_formatting():
    builder = PromptBuilder()
    
    # Check that Vietnamese characters in user_request are preserved correctly in the build prompt
    for vi_request in ["mở youtube", "tìm video minecraft", "mở cài đặt"]:
        context = {
            "device": {"width": 1080, "height": 2400},
            "app": {"package": "com.example.app", "activity": "ExampleActivity"},
            "accessibility_tree": {"package": "com.example.app", "nodes": []},
            "user_request": vi_request
        }
        prompt = builder.build(
            history=[],
            user_message=Message(role="user", content="tick"),
            context=context
        )
        assert f'The user has requested: "{vi_request}"' in prompt


def test_last_action_error_formatting():
    builder = PromptBuilder()
    context = {
        "device": {"width": 1080, "height": 2400},
        "app": {"package": "com.example.app", "activity": "ExampleActivity"},
        "accessibility_tree": {"package": "com.example.app", "nodes": []},
        "user_request": "click button",
        "last_action_error": "Action click on node_12 failed. Target not clickable."
    }
    prompt = builder.build(
        history=[],
        user_message=Message(role="user", content="tick"),
        context=context
    )
    assert "===== LAST ACTION ERROR =====" in prompt
    assert "Action click on node_12 failed. Target not clickable." in prompt


def test_search_rules_in_prompt():
    builder = PromptBuilder()
    context = {
        "device": {"width": 1080, "height": 2400},
        "app": {"package": "com.google.android.youtube", "activity": "MainActivity"},
        "accessibility_tree": {"package": "com.google.android.youtube", "nodes": []},
        "user_request": "open YouTube and search for lofi music and pick the first song (not an ad)"
    }
    prompt = builder.build(
        history=[],
        user_message=Message(role="user", content="tick"),
        context=context
    )
    assert 'SEARCH & RESULT SELECTION RULES:' in prompt
    assert '1. "search <query>", "search for <query>", and "tìm <query>" mean search for <query>.' in prompt
    assert '8. NEVER select an advertisement or sponsored result when user requests a non-ad result' in prompt
    assert '10. Once the requested result has been opened/played/selected successfully, STOP' in prompt


