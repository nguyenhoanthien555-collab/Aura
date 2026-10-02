"""
Tests for persistent user tool consent store (ToolConsentStore).
"""
import time
from server.tool_consent import ToolConsentStore


def test_tool_consent_store_lifecycle():
    store = ToolConsentStore()
    tool = f"test_probe_tool_{int(time.time() * 1000)}"

    # 1. Unseen tool is not approved
    assert not store.is_approved(tool)

    # 2. Approve tool
    store.approve(tool, auto=False)
    assert store.is_approved(tool)
    assert tool in store.list_approved()

    # 3. New store instance reads persisted record from database
    store2 = ToolConsentStore()
    assert store2.is_approved(tool)

    # 4. Revoke consent
    store.revoke(tool)
    assert not store.is_approved(tool)

    # 5. Persisted revocation
    store3 = ToolConsentStore()
    assert not store3.is_approved(tool)
