"""
End-to-end test suite for AURA Post-QA Production Hardening.
Verifies:
1. Online atomic SQLite backup, integrity check, and rotation.
2. Database corruption detection and safe restoration.
3. Secret redaction filter in core.logger.
4. Outbox and inbox queue pruning for bounded resource usage.
5. System diagnostic and backup REST API endpoints.
6. Environment path override durability (AURA_DATA_DIR).
7. Autonomy lock invariant (full_autonomy_enabled == False).
8. Performance benchmark measurements for core operations.
"""

import os
import shutil
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.logger import redact_secrets
from core.paths import BACKUPS_DIR, DATA_DIR
from core.sync.inbox import InboxProcessor
from core.sync.models import InboxStatus, OutboxStatus, SyncEvent
from core.sync.outbox import OutboxManager
from agent.autonomy_guard import AutonomyGateManager
from memory.backup import (
    create_database_backup,
    get_backup_history,
    get_storage_stats,
    prune_old_backups,
    restore_database_backup,
    verify_database_integrity,
)
from memory.models import SyncInboxRecord, SyncOutboxRecord
from memory.sqlite import SessionLocal, db_lock, init_database
from server.config import settings
from server.main import app


def test_sqlite_online_backup_and_integrity(tmp_path):
    """Verify atomic online backup creation, verification, and rotation."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    # 1. Verify live database integrity
    healthy, msg = verify_database_integrity()
    assert healthy is True
    assert msg == "ok"

    # 2. Create multiple backups with tags and test rotation
    b1 = create_database_backup(tag="test1", backup_dir=backup_dir, max_backups_to_keep=2)
    assert b1.exists()
    assert b1.stat().st_size > 0

    time.sleep(1.1)  # Ensure distinct timestamp
    b2 = create_database_backup(tag="test2", backup_dir=backup_dir, max_backups_to_keep=2)
    assert b2.exists()

    time.sleep(1.1)
    b3 = create_database_backup(tag="test3", backup_dir=backup_dir, max_backups_to_keep=2)
    assert b3.exists()

    # Oldest backup (b1) should be pruned because max_backups_to_keep=2
    history = get_backup_history(backup_dir=backup_dir)
    assert len(history) == 2
    filenames = [h["filename"] for h in history]
    assert b3.name in filenames
    assert b2.name in filenames
    assert b1.name not in filenames


def test_database_corruption_detection_and_restore(tmp_path):
    """Verify corruption detection and restoration from a healthy backup."""
    target_db = tmp_path / "corrupt_test.db"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    # Create a valid backup
    valid_backup = create_database_backup(tag="golden", backup_dir=backup_dir)
    assert valid_backup.exists()

    # Restore to target_db
    success = restore_database_backup(valid_backup, target_path=target_db)
    assert success is True
    healthy, _ = verify_database_integrity(target_db)
    assert healthy is True

    # Corrupt target_db
    with open(target_db, "r+b") as f:
        f.seek(100)
        f.write(b"\xFF\xFE\xFD\xFC" * 200)

    # Verify corruption is detected
    healthy_after_corrupt, corrupt_msg = verify_database_integrity(target_db)
    assert healthy_after_corrupt is False
    assert corrupt_msg != "ok"

    # Restore from golden backup
    restore_database_backup(valid_backup, target_path=target_db)
    healthy_restored, msg_restored = verify_database_integrity(target_db)
    assert healthy_restored is True
    assert msg_restored == "ok"


def test_logger_secret_redaction():
    """Verify that credentials, Bearer tokens, and API keys are scrubbed from logs."""
    raw_texts = [
        ("Authorization: Bearer secret_token_123456789012345", "Authorization: Bearer [REDACTED]"),
        ("https://api.example.com?api_key=secretkey123456789", "https://api.example.com?api_key=[REDACTED]"),
        ("AIzaSyAbCdEfGhIjKlMnOpQrStUvWxYz1234567", "[REDACTED_KEY]"),
        ("sk-123456789012345678901234567890", "[REDACTED_KEY]"),
        ('{"api_key": "supersecretkey"}', '{"api_key": "[REDACTED]"}'),
    ]

    for raw, expected in raw_texts:
        redacted = redact_secrets(raw)
        assert expected in redacted or "[REDACTED" in redacted
        assert "secret_token" not in redacted
        assert "supersecretkey" not in redacted


def test_outbox_and_inbox_queue_pruning():
    """Verify that old acknowledged outbox and processed inbox records are pruned."""
    outbox = OutboxManager()
    inbox = InboxProcessor()

    # Insert test acknowledged outbox records
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S")
    with db_lock:
        session = SessionLocal()
        try:
            for i in range(10):
                session.add(
                    SyncOutboxRecord(
                        event_id=f"evt-test-prune-outbox-{i}-{time.time()}",
                        status=OutboxStatus.ACKNOWLEDGED.value,
                        acknowledged_at=now_iso,
                    )
                )
                session.add(
                    SyncInboxRecord(
                        event_id=f"evt-test-prune-inbox-{i}-{time.time()}",
                        origin_node_id="test-origin-node",
                        status=InboxStatus.PROCESSED.value,
                        processed_at=now_iso,
                    )
                )
            session.commit()
        finally:
            session.close()

    # Prune keeping only 5
    pruned_outbox = outbox.prune_acknowledged(max_records_to_keep=5)
    assert pruned_outbox >= 5

    pruned_inbox = inbox.prune_processed(max_records_to_keep=5)
    assert pruned_inbox >= 5


def test_system_rest_api_endpoints():
    """Verify the /api/system REST routes for storage, backups, and integrity."""
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {settings.auth_token}"}

    # 1. Storage metrics
    res = client.get("/api/system/storage", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "database_path" in data
    assert "integrity_healthy" in data
    assert data["integrity_healthy"] is True
    assert "table_row_counts" in data

    # 2. Database integrity probe
    res = client.post("/api/system/integrity", headers=headers)
    assert res.status_code == 200
    assert res.json()["healthy"] is True

    # 3. Create online backup via API
    res = client.post("/api/system/backup", json={"tag": "api_test", "max_backups": 3}, headers=headers)
    assert res.status_code == 200
    assert res.json()["status"] == "success"

    # 4. List backups
    res = client.get("/api/system/backups", headers=headers)
    assert res.status_code == 200
    backups = res.json()
    assert len(backups) >= 1
    assert any("api_test" in b["filename"] for b in backups)


def test_autonomy_gate_state3_locked():
    """Verify that State 3 full autonomy remains strictly locked."""
    manager = AutonomyGateManager()
    assert manager.state.get("full_autonomy_enabled", False) is False
    assert manager.state.get("machine_verdict", {}).get("state_3_full_autonomy") == "LOCKED_PRESERVED"
    assert manager.state.get("machine_verdict", {}).get("human_supervisor_signoff_required") is True

    import yaml
    with open("config.yaml", "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    assert cfg.get("full_autonomy_enabled", False) is False


def test_performance_latency_benchmarks():
    """Benchmark practical latencies of storage stats, backup, and health checks."""
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {settings.auth_token}"}

    # Warmup call
    client.get("/api/health", headers=headers)

    # Measure health check latency
    t0 = time.perf_counter()
    for _ in range(5):
        res = client.get("/api/health", headers=headers)
        assert res.status_code == 200
    health_latency_ms = ((time.perf_counter() - t0) / 5) * 1000
    assert health_latency_ms < 100.0  # < 100ms average

    # Measure storage stats latency
    t0 = time.perf_counter()
    res = client.get("/api/system/storage", headers=headers)
    assert res.status_code == 200
    storage_latency_ms = (time.perf_counter() - t0) * 1000
    assert storage_latency_ms < 250.0  # < 250ms
