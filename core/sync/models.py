"""
Data models and schemas for AURA P4 Distributed Continuity.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class SyncNodeType(str, Enum):
    LAPTOP = "LAPTOP"
    ANDROID = "ANDROID"
    RELAY = "RELAY"


class SyncState(str, Enum):
    ONLINE = "ONLINE"
    SYNCING = "SYNCING"
    SYNCED = "SYNCED"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"
    ERROR = "ERROR"


class OutboxStatus(str, Enum):
    PENDING = "PENDING"
    SENDING = "SENDING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    QUARANTINED = "QUARANTINED"


class InboxStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSED = "PROCESSED"
    QUARANTINED = "QUARANTINED"


class SyncEventType(str, Enum):
    EXPERIENCE_CREATED = "EXPERIENCE_CREATED"
    TOOL_DISCOVERED = "TOOL_DISCOVERED"
    TOOL_VERIFIED = "TOOL_VERIFIED"
    STATE_CHECKPOINT = "STATE_CHECKPOINT"
    CAPABILITY_UPDATE = "CAPABILITY_UPDATE"


class ConflictType(str, Enum):
    HASH_MISMATCH = "HASH_MISMATCH"
    CONCURRENT_MUTATION = "CONCURRENT_MUTATION"


def canonical_json(data: Any) -> str:
    """Deterministic, sorted-key JSON serialization for hashing."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def compute_payload_hash(payload: Any) -> str:
    """SHA-256 hash of canonically serialized payload."""
    canonical = canonical_json(payload)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class SyncNode:
    node_id: str
    node_type: str = SyncNodeType.LAPTOP.value
    installation_id: str = ""
    status: str = SyncState.ONLINE.value
    schema_version: int = 1
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    last_seen: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SyncNode:
        return cls(
            node_id=str(data.get("node_id", "")),
            node_type=str(data.get("node_type", SyncNodeType.LAPTOP.value)),
            installation_id=str(data.get("installation_id", "")),
            status=str(data.get("status", SyncState.ONLINE.value)),
            schema_version=int(data.get("schema_version", 1)),
            created_at=str(data.get("created_at", "")),
            last_seen=str(data.get("last_seen", "")),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class SyncEvent:
    event_id: str
    origin_node_id: str
    event_type: str
    entity_type: str
    entity_id: str
    schema_version: int = 1
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    logical_sequence: int = 0
    payload: dict[str, Any] = field(default_factory=dict)
    payload_hash: str = ""
    parent_event_id: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    received_at: str | None = None

    def __post_init__(self):
        if not self.payload_hash:
            self.payload_hash = compute_payload_hash(self.payload)

    def verify_hash(self) -> bool:
        """Verify the payload matches the cryptographic hash."""
        expected = compute_payload_hash(self.payload)
        return self.payload_hash == expected

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SyncEvent:
        return cls(
            event_id=str(data.get("event_id", "")),
            origin_node_id=str(data.get("origin_node_id", "")),
            event_type=str(data.get("event_type", "")),
            entity_type=str(data.get("entity_type", "")),
            entity_id=str(data.get("entity_id", "")),
            schema_version=int(data.get("schema_version", 1)),
            created_at=str(data.get("created_at", "")),
            logical_sequence=int(data.get("logical_sequence", 0)),
            payload=dict(data.get("payload", {})),
            payload_hash=str(data.get("payload_hash", "")),
            parent_event_id=data.get("parent_event_id"),
            provenance=dict(data.get("provenance", {})),
            received_at=data.get("received_at"),
        )
