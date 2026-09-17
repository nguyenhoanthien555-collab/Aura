"""
Tests for AURA Exhaustive Default-On Configuration Sweep.

Verifies that:
1. All safe implemented features default to ON in canonical DEFAULT_CONFIG.
2. Mutable settings are registered in ALLOWED in core/settings_store.py.
3. Critical safety guardrails (full_autonomy_enabled, auto_approve) remain locked.
"""

import pytest
from core.config import DEFAULT_CONFIG
from core.settings_store import ALLOWED
from learning.autonomy_guard import AutonomyGateManager


def test_safe_features_default_on_in_default_config():
    """All safe implemented capabilities must be DEFAULT-ON in DEFAULT_CONFIG."""
    # Tools
    assert DEFAULT_CONFIG["tools"]["enabled"] is True, "tools.enabled must be True"
    assert DEFAULT_CONFIG["tools"]["auto_approve"] == ["safe"], "tools.auto_approve must strictly be ['safe']"

    # Proactive
    assert DEFAULT_CONFIG["proactive"]["enabled"] is True, "proactive.enabled must be True"

    # Server companion & screen
    assert DEFAULT_CONFIG["server"]["companion"]["enabled"] is True, "server.companion.enabled must be True"
    assert DEFAULT_CONFIG["server"]["screen"]["enabled"] is True, "server.screen.enabled must be True"

    # Memory
    assert DEFAULT_CONFIG["memory"]["recall"] is True, "memory.recall must be True"
    assert DEFAULT_CONFIG["memory"]["profile"] is True, "memory.profile must be True"
    assert DEFAULT_CONFIG["memory"]["pipeline"] is True, "memory.pipeline must be True"
    assert DEFAULT_CONFIG["memory"]["seed_profile"] is True, "memory.seed_profile must be True"
    assert DEFAULT_CONFIG["memory"]["semantic"]["enabled"] is True, "memory.semantic.enabled must be True"

    # Response verification
    assert DEFAULT_CONFIG["response"]["verify"]["enabled"] is True, "response.verify.enabled must be True"
    assert DEFAULT_CONFIG["response"]["verify"]["repair"] is True, "response.verify.repair must be True"

    # Personality
    assert DEFAULT_CONFIG["personality"]["style"]["enabled"] is True
    assert DEFAULT_CONFIG["personality"]["consistency"]["enabled"] is True
    assert DEFAULT_CONFIG["personality"]["persona"]["enabled"] is True


def test_allowed_settings_include_all_safe_features():
    """Settings allow-list must include all canonical configurable features."""
    expected_allowed = [
        "tools.enabled",
        "proactive.enabled",
        "server.companion.enabled",
        "server.screen.enabled",
        "memory.semantic.enabled",
        "memory.recall",
        "memory.profile",
        "memory.pipeline",
    ]
    for key in expected_allowed:
        assert key in ALLOWED, f"{key} must be present in ALLOWED"


def test_hard_safety_guardrails_remain_locked():
    """CRITICAL: Safety gates and full autonomy MUST NOT be defaulted on."""
    guard = AutonomyGateManager(gate_file="logs/test_autonomy_guard.json")
    assert guard.state["full_autonomy_enabled"] is False, "full_autonomy_enabled must strictly be False"
    assert "dangerous" not in DEFAULT_CONFIG["tools"]["auto_approve"], "dangerous tools must never be auto-approved"
