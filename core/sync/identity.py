"""
Node Identity Management for AURA P4.
Ensures stable, unique, non-IP-based identity per installation.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from core.paths import DATA_DIR
from core.sync.models import SyncNode, SyncNodeType, SyncState
from memory.models import SyncNodeRecord
from memory.sqlite import SessionLocal, db_lock, init_sync_tables


class NodeIdentityManager:
    _instance: SyncNode | None = None
    _identity_file: Path = DATA_DIR / "node_identity.json"

    @classmethod
    def get_local_node(cls, node_type: str = SyncNodeType.LAPTOP.value) -> SyncNode:
        if cls._instance is not None:
            return cls._instance

        init_sync_tables()
        DATA_DIR.mkdir(parents=True, exist_ok=True)

        # 1. Check file
        if cls._identity_file.exists():
            try:
                data = json.loads(cls._identity_file.read_text(encoding="utf-8"))
                node = SyncNode.from_dict(data)
                cls._instance = node
                cls._persist_to_db(node)
                return node
            except Exception:
                pass

        # 2. Check DB
        with db_lock:
            session = SessionLocal()
            try:
                rec = session.query(SyncNodeRecord).filter_by(node_type=node_type).first()
                if rec:
                    node = SyncNode(
                        node_id=rec.node_id,
                        node_type=rec.node_type,
                        installation_id=rec.installation_id,
                        status=rec.status,
                        schema_version=rec.schema_version,
                        created_at=rec.created_at,
                        last_seen=rec.last_seen,
                        metadata=json.loads(rec.metadata_json or "{}"),
                    )
                    cls._instance = node
                    cls._persist_to_file(node)
                    return node
            finally:
                session.close()

        # 3. Generate new stable identity
        short_id = uuid.uuid4().hex[:12]
        node_id = f"node_{node_type.lower()}_{short_id}"
        installation_id = str(uuid.uuid4())
        node = SyncNode(
            node_id=node_id,
            node_type=node_type,
            installation_id=installation_id,
            status=SyncState.ONLINE.value,
        )
        cls._instance = node
        cls._persist_to_file(node)
        cls._persist_to_db(node)
        return node

    @classmethod
    def _persist_to_file(cls, node: SyncNode):
        try:
            cls._identity_file.write_text(json.dumps(node.to_dict(), indent=2), encoding="utf-8")
        except Exception:
            pass

    @classmethod
    def _persist_to_db(cls, node: SyncNode):
        with db_lock:
            session = SessionLocal()
            try:
                rec = session.query(SyncNodeRecord).filter_by(node_id=node.node_id).first()
                if not rec:
                    rec = SyncNodeRecord(
                        node_id=node.node_id,
                        node_type=node.node_type,
                        installation_id=node.installation_id,
                        status=node.status,
                        schema_version=node.schema_version,
                        created_at=node.created_at,
                        last_seen=node.last_seen,
                        metadata_json=json.dumps(node.metadata),
                    )
                    session.add(rec)
                else:
                    rec.last_seen = node.last_seen
                    rec.status = node.status
                session.commit()
            except Exception:
                session.rollback()
            finally:
                session.close()

    @classmethod
    def reset_for_tests(cls):
        cls._instance = None
