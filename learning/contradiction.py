# -*- coding: utf-8 -*-
"""
AURA Contradiction Detector & Experience Protection System.

Defends the neural self-learning pipeline against contradictory, adversarial,
or corrupting operational experiences before dataset inclusion.
Verifies experiences against Stable Core truths, safety boundaries,
identity axioms, and tool capability registries.
"""

from dataclasses import dataclass, field
import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from core.logger import logger


# Registered valid tool names in AURA system
BASE_AURA_TOOLS = {
    "current_time",
    "react_to_message",
    "system_information",
    "list_processes",
    "list_windows",
    "focus_window",
    "move_mouse",
    "click_mouse",
    "type_text",
    "press_keys",
    "android.back",
    "android.find_node",
    "android.get_foreground_app",
    "android.get_ui_tree",
    "android.home",
    "android.launch_app",
    "android.list_apps",
    "android.long_press",
    "android.press_key",
    "android.screenshot",
    "android.swipe",
    "android.tap",
    "android.type_text",
    "android.verify",
    "android.wait_for",
    "android.key_back",
    "android.key_home",
    "screenshot",
    "lock_screen",
    "launch_app",
    "system_info",
    "shell_command",
}


_CACHED_AURA_TOOLS: Optional[Set[str]] = None


def get_known_aura_tools() -> Set[str]:
    global _CACHED_AURA_TOOLS
    if _CACHED_AURA_TOOLS is not None:
        return _CACHED_AURA_TOOLS
    try:
        import logging
        prev_level = logging.getLogger().level
        logging.disable(logging.WARNING)
        try:
            from tools.factory import build_registry
            reg = build_registry()
            _CACHED_AURA_TOOLS = BASE_AURA_TOOLS | set(reg.names())
        finally:
            logging.disable(logging.NOTSET)
            logging.getLogger().setLevel(prev_level)
        return _CACHED_AURA_TOOLS
    except Exception:
        _CACHED_AURA_TOOLS = BASE_AURA_TOOLS
        return _CACHED_AURA_TOOLS


# Unsupported capabilities that MUST be refused (derived from Stable Core)
UNSUPPORTED_HARDWARE_KEYWORDS = [
    r"\b(đèn pin|flashlight)\b",
    r"\b(bluetooth)\b",
    r"\b(tin nhắn sms|gửi sms|send sms)\b",
    r"\b(camera trước|front camera|mở camera điện thoại)\b",
]

# Destructive command patterns that MUST require confirmation
DESTRUCTIVE_COMMAND_PATTERNS = [
    r"(?i)(rm\s+-rf\s+[/~*]|format\s+[c-z]:|drop\s+database|del\s+/f\s+/s\s+/q\s+c:\\)",
    r"(?i)(shutdown\s+/s\s+/f|dd\s+if=/dev/zero|mkfs\.)",
    r"(?i)(delete\s+all\s+files|xóa\s+toàn\s+bộ\s+(tệp|dữ liệu)|rmdir\s+/s|remove-item\s+.*-recurse)",
]

# Foreign identity claims that must NEVER be accepted
FOREIGN_IDENTITY_PATTERNS = [
    r"(?i)\b(chatgpt|openai|claude|anthropic|gemini|bard|copilot)\b",
]


@dataclass
class ContradictionVerdict:
    is_valid: bool
    contradiction_type: str = ""  # NONE, CORE, SAFETY, IDENTITY, UNREGISTERED_TOOL, ADVERSARIAL_CORRECTION
    reason: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "contradiction_type": self.contradiction_type,
            "reason": self.reason,
            "evidence": self.evidence,
        }


class ContradictionDetector:
    """Validates operational experiences against foundational invariants."""

    @classmethod
    def check_experience(cls, exp_dict: Dict[str, Any]) -> ContradictionVerdict:
        """
        Audits an individual experience record.
        Returns ContradictionVerdict (is_valid=False if contradictory).
        """
        user_input = exp_dict.get("input_text", "").strip()
        decision = exp_dict.get("model_decision", "ANSWER")
        tool = exp_dict.get("selected_tool", "")
        asst_response = exp_dict.get("final_response", "")
        feedback = exp_dict.get("user_feedback", "")

        # 1. Check Identity Contradictions
        for pat in FOREIGN_IDENTITY_PATTERNS:
            if re.search(pat, asst_response):
                return ContradictionVerdict(
                    is_valid=False,
                    contradiction_type="IDENTITY",
                    reason=f"Assistant response asserts foreign or hallucinated identity matching {pat}",
                    evidence={"response": asst_response[:120]},
                )

        # 2. Check Tool Capability Integrity
        if decision == "TOOL_CALL" and tool:
            known_tools = get_known_aura_tools()
            if tool.lower() not in known_tools:
                return ContradictionVerdict(
                    is_valid=False,
                    contradiction_type="UNREGISTERED_TOOL",
                    reason=f"Selected tool '{tool}' is not in registered capability catalogue",
                    evidence={"selected_tool": tool, "registered_tools_count": len(known_tools)},
                )

        # 3. Check Stable Core Unsupported Hardware Refusals
        for pat in UNSUPPORTED_HARDWARE_KEYWORDS:
            if re.search(pat, user_input, re.IGNORECASE):
                # If user asks for unsupported hardware, model MUST refuse, NOT call tool
                if decision == "TOOL_CALL":
                    return ContradictionVerdict(
                        is_valid=False,
                        contradiction_type="CORE",
                        reason=f"Request targets unsupported hardware ({pat}), but experience records positive TOOL_CALL",
                        evidence={"input": user_input, "tool": tool},
                    )
                # If conversational response claims capability
                low_resp = asst_response.lower()
                positive_claims = ["tôi đã bật", "đã mở", "đã gửi", "turned on", "i have sent", "sure, i will turn"]
                if any(p in low_resp for p in positive_claims):
                    return ContradictionVerdict(
                        is_valid=False,
                        contradiction_type="CORE",
                        reason=f"Request targets unsupported hardware, but response falsely claims completion",
                        evidence={"input": user_input, "response": asst_response[:120]},
                    )

        # 4. Check Safety Constraints on Destructive Actions
        for pat in DESTRUCTIVE_COMMAND_PATTERNS:
            if re.search(pat, user_input) or re.search(pat, json.dumps(exp_dict.get("arguments", {}))):
                if decision != "CONFIRMATION_REQUIRED":
                    return ContradictionVerdict(
                        is_valid=False,
                        contradiction_type="SAFETY",
                        reason="Destructive command executed without mandatory CONFIRMATION_REQUIRED protocol",
                        evidence={"input": user_input, "decision": decision},
                    )

        # 5. Check User Correction Integrity
        if feedback in ("USER_CORRECTED", "CORRECT"):
            for pat in FOREIGN_IDENTITY_PATTERNS:
                if re.search(pat, asst_response):
                    return ContradictionVerdict(
                        is_valid=False,
                        contradiction_type="ADVERSARIAL_CORRECTION",
                        reason="User correction attempts to inject prohibited identity claim",
                        evidence={"feedback": feedback, "response": asst_response[:120]},
                    )

        return ContradictionVerdict(is_valid=True, contradiction_type="NONE", reason="Passes all invariants")

    @classmethod
    def audit_dataset_file(cls, jsonl_path: str) -> Dict[str, Any]:
        """Scans a candidate JSONL dataset file for contradictions."""
        total = 0
        clean = 0
        flagged = []

        with open(jsonl_path, "r", encoding="utf-8") as f:
            for idx, line in enumerate(f):
                if not line.strip():
                    continue
                total += 1
                record = json.loads(line)
                
                user_msg = ""
                asst_msg = ""
                tool_name = ""
                decision = "ANSWER"
                
                for m in record.get("messages", []):
                    if m.get("role") == "user":
                        user_msg = m.get("content", "")
                    elif m.get("role") == "assistant":
                        asst_msg = m.get("content", "")
                        if m.get("tool_calls"):
                            decision = "TOOL_CALL"
                            tool_name = m["tool_calls"][0].get("name", "")

                exp_dict = {
                    "input_text": user_msg,
                    "final_response": asst_msg,
                    "model_decision": decision,
                    "selected_tool": tool_name,
                    "arguments": record.get("provenance", {}),
                    "user_feedback": "",
                }

                verdict = cls.check_experience(exp_dict)
                if verdict.is_valid:
                    clean += 1
                else:
                    flagged.append({
                        "line": idx + 1,
                        "example_id": record.get("id", f"line_{idx+1}"),
                        "verdict": verdict.to_dict(),
                    })

        return {
            "total_examples": total,
            "clean_examples": clean,
            "contradiction_count": len(flagged),
            "status": "CLEAN" if len(flagged) == 0 else "CONTRADICTIONS_DETECTED",
            "flagged_examples": flagged,
        }
