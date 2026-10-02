"""
Cross-Device Clipboard Synchronization Tests (Stage D).

Pins behavior for:
1. DesktopSetClipboardTool & DesktopGetClipboardTool (Windows ctypes / memory fallback).
2. AndroidSetClipboard & AndroidGetClipboard via LoopbackDeviceBridge.
3. Capability registration & health checks (desktop.clipboard, android.clipboard).
4. Factory gating (_pc_tools respects config.allowed, preserving cloud boundary invariant).
"""

import pytest

from core.capabilities import health, registry as capability_registry
from core.capabilities.factory import register_core_capabilities
from core.capabilities.models import CapabilityState
from tools.base import ToolRisk
from tools.builtins.desktop import DesktopGetClipboardTool, DesktopSetClipboardTool
from tools.factory import _pc_tools, build_registry
from tools.outcome import EvidenceKind, SideEffect, ToolStatus
from tools.providers.android_bridge import LoopbackDeviceBridge
from tools.providers.android_task_provider import (
    AndroidGetClipboard,
    AndroidSetClipboard,
    AndroidTaskProvider,
)
from tools.registry import ToolRegistry


def test_desktop_clipboard_tools_roundtrip():
    """Desktop clipboard tool can set and get text with full UTF-8/Unicode."""
    set_tool = DesktopSetClipboardTool()
    get_tool = DesktopGetClipboardTool()

    assert set_tool.name == "desktop.set_clipboard"
    assert set_tool.capability == "desktop.clipboard"
    assert set_tool.risk == ToolRisk.SAFE
    assert set_tool.side_effect == SideEffect.IDEMPOTENT

    assert get_tool.name == "desktop.get_clipboard"
    assert get_tool.capability == "desktop.clipboard"
    assert get_tool.side_effect == SideEffect.READ_ONLY

    sample_text = "Aura kính chào anh! Đây là token bí mật: 🚀 cyber-token-12345"
    set_res = set_tool.execute(sample_text)

    assert set_res.ok is True
    assert set_res.status == ToolStatus.SUCCESS
    assert set_res.data["length"] == len(sample_text)

    get_res = get_tool.execute()
    assert get_res.ok is True
    assert get_res.status == ToolStatus.SUCCESS
    assert get_res.data["text"] == sample_text
    assert get_res.data["has_clip"] is True


def test_desktop_set_clipboard_coercion():
    """Non-string values are converted to string cleanly."""
    set_tool = DesktopSetClipboardTool()
    get_tool = DesktopGetClipboardTool()

    set_res = set_tool.execute(123456)
    assert set_res.ok is True
    assert set_res.data["length"] == 6

    get_res = get_tool.execute()
    assert get_res.data["text"] == "123456"


def test_android_clipboard_tools_over_loopback():
    """AndroidSetClipboard and AndroidGetClipboard operate cleanly over LoopbackDeviceBridge."""
    bridge = LoopbackDeviceBridge()
    set_tool = AndroidSetClipboard(bridge)
    get_tool = AndroidGetClipboard(bridge)

    assert set_tool.name == "android.set_clipboard"
    assert set_tool.capability == "android.clipboard"
    assert set_tool.risk == ToolRisk.SAFE
    assert set_tool.side_effect == SideEffect.IDEMPOTENT

    assert get_tool.name == "android.get_clipboard"
    assert get_tool.capability == "android.clipboard"
    assert get_tool.side_effect == SideEffect.READ_ONLY

    url = "https://aura-xwm4.onrender.com/hub"
    set_res = set_tool.execute(text=url)

    assert set_res.ok is True
    assert set_res.status == ToolStatus.SUCCESS
    assert set_res.evidence is not None
    assert any(ev.kind == EvidenceKind.POSTCONDITION and ev.verified for ev in set_res.evidence)

    get_res = get_tool.execute()
    assert get_res.ok is True
    assert get_res.status == ToolStatus.SUCCESS
    assert get_res.data["result"]["text"] == url


def test_clipboard_capabilities_registered():
    """desktop.clipboard and android.clipboard are canonically registered."""
    register_core_capabilities({})

    cap_desktop = capability_registry.get("desktop.clipboard")
    assert cap_desktop is not None
    assert cap_desktop.name == "Desktop Clipboard"
    assert cap_desktop.category == "desktop"

    check_desktop = health.run_check("desktop.clipboard")
    assert check_desktop["healthy"] is True
    assert check_desktop["state"] == "AVAILABLE"

    cap_android = capability_registry.get("android.clipboard")
    assert cap_android is not None
    assert cap_android.name == "Android Clipboard Sync"
    assert cap_android.category == "android"


def test_factory_boundary_preservation():
    """Stock empty allowed config does NOT register desktop clipboard tools."""
    # Empty allowed
    tools_empty = _pc_tools(config={"allowed": []})
    names_empty = {t.name for t in tools_empty}
    assert "desktop.set_clipboard" not in names_empty
    assert "desktop.get_clipboard" not in names_empty

    # Explicitly allowed
    tools_allowed = _pc_tools(config={"allowed": ["desktop.set_clipboard", "desktop.get_clipboard"]})
    names_allowed = {t.name for t in tools_allowed}
    assert "desktop.set_clipboard" in names_allowed
    assert "desktop.get_clipboard" in names_allowed


def test_android_task_provider_registration():
    """AndroidTaskProvider registers clipboard tools into ToolRegistry."""
    bridge = LoopbackDeviceBridge()
    provider = AndroidTaskProvider(bridge)
    registry = ToolRegistry()

    provider.register_into(registry)

    assert "android.set_clipboard" in registry.names()
    assert "android.get_clipboard" in registry.names()
