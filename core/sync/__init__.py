"""
AURA P4: Distributed Continuity & Always-Sync.
Event-sourced, durable, offline-first synchronization core.
"""

from core.sync.models import (
    SyncNode,
    SyncNodeType,
    SyncState,
    SyncEvent,
    SyncEventType,
    OutboxStatus,
    InboxStatus,
    ConflictType,
)
from core.sync.identity import NodeIdentityManager
from core.sync.event_log import EventLog
from core.sync.outbox import OutboxManager
from core.sync.inbox import InboxProcessor
from core.sync.conflict import ConflictManager
from core.sync.engine import SyncEngine
from core.sync.client import SyncRelayClient

__all__ = [
    "SyncNode",
    "SyncNodeType",
    "SyncState",
    "SyncEvent",
    "SyncEventType",
    "OutboxStatus",
    "InboxStatus",
    "ConflictType",
    "NodeIdentityManager",
    "EventLog",
    "OutboxManager",
    "InboxProcessor",
    "ConflictManager",
    "SyncEngine",
    "SyncRelayClient",
]
