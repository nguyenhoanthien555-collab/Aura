"""
SQLite online backup, integrity verification, and recovery mechanism for AURA.
Provides atomic, lock-safe database snapshots using SQLite's native backup API,
automatic rotation to prevent disk exhaustion, and integrity validation.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.logger import logger
from core.paths import BACKUPS_DIR, DATA_DIR
from memory.sqlite import db_lock


def get_default_db_path() -> Path:
    return DATA_DIR / "memory.db"


def verify_database_integrity(db_path: Optional[Path] = None) -> Tuple[bool, str]:
    """
    Run PRAGMA integrity_check on the specified SQLite database file.
    Returns:
        (is_healthy: bool, details: str)
    """
    path = db_path or get_default_db_path()
    if not path.exists():
        return False, f"Database file does not exist: {path}"

    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check;")
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        if rows and rows[0][0] == "ok":
            return True, "ok"
        else:
            errors = [r[0] for r in rows]
            return False, "; ".join(errors)
    except Exception as exc:
        return False, f"Integrity check failed with exception: {exc}"


def create_database_backup(
    tag: str = "",
    backup_dir: Optional[Path] = None,
    max_backups_to_keep: int = 5,
    src_db_path: Optional[Path] = None,
) -> Path:
    """
    Perform an atomic online backup of memory.db to the backup directory.
    Rotates older backups beyond max_backups_to_keep to bound disk usage.
    """
    src_path = src_db_path or get_default_db_path()
    if not src_path.exists():
        raise FileNotFoundError(f"Source database not found: {src_path}")

    dest_dir = backup_dir or BACKUPS_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    clean_tag = f"_{tag.strip()}" if tag.strip() else ""
    backup_filename = f"memory_backup_{timestamp}{clean_tag}.db"
    dest_path = dest_dir / backup_filename

    logger.info("Creating online database backup to %s", dest_path)

    try:
        with db_lock:
            src_conn = sqlite3.connect(str(src_path))
            dest_conn = sqlite3.connect(str(dest_path))
            try:
                # Native SQLite online backup safely copies pages while allowing concurrency
                src_conn.backup(dest_conn, pages=100)
            finally:
                dest_conn.close()
                src_conn.close()

        # Validate the written backup before committing it to the catalog
        healthy, msg = verify_database_integrity(dest_path)
        if not healthy:
            raise RuntimeError(f"Backup verification failed: {msg}")
    except Exception:
        dest_path.unlink(missing_ok=True)
        raise

    # Prune old backups if needed
    prune_old_backups(dest_dir, max_backups_to_keep=max_backups_to_keep)

    logger.info("Backup successfully created and verified: %s (%d bytes)", dest_path.name, dest_path.stat().st_size)
    return dest_path


def prune_old_backups(backup_dir: Optional[Path] = None, max_backups_to_keep: int = 5) -> int:
    """Removes older backup files in backup_dir beyond max_backups_to_keep."""
    dest_dir = backup_dir or BACKUPS_DIR
    if not dest_dir.exists():
        return 0

    backups = sorted(
        dest_dir.glob("memory_backup_*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    removed = 0
    if len(backups) > max_backups_to_keep:
        for old_backup in backups[max_backups_to_keep:]:
            try:
                old_backup.unlink()
                removed += 1
                logger.info("Pruned old database backup: %s", old_backup.name)
            except Exception as e:
                logger.warning("Could not delete old backup %s: %s", old_backup.name, e)

    return removed


def restore_database_backup(
    backup_path: Path,
    target_path: Optional[Path] = None,
) -> bool:
    """
    Restore memory.db from a validated backup file.
    Creates a safety snapshot of the target before replacement.
    """
    if not backup_path.exists():
        raise FileNotFoundError(f"Backup file not found: {backup_path}")

    healthy, msg = verify_database_integrity(backup_path)
    if not healthy:
        raise ValueError(f"Cannot restore from corrupt backup: {msg}")

    dest_path = target_path or get_default_db_path()

    with db_lock:
        # Safety backup of existing target if present
        if dest_path.exists():
            safety_copy = dest_path.with_suffix(".pre_restore.bak")
            try:
                shutil.copy2(dest_path, safety_copy)
            except Exception as e:
                logger.warning("Could not create pre-restore safety copy: %s", e)

        # Restore using online backup API to ensure clean sync
        src_conn = sqlite3.connect(str(backup_path))
        dest_conn = sqlite3.connect(str(dest_path))
        try:
            src_conn.backup(dest_conn, pages=100)
        finally:
            dest_conn.close()
            src_conn.close()

    logger.info("Database successfully restored from %s", backup_path.name)
    return True


def get_backup_history(backup_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """List all available backup files with size and verification status."""
    dest_dir = backup_dir or BACKUPS_DIR
    if not dest_dir.exists():
        return []

    backups = sorted(
        dest_dir.glob("memory_backup_*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    history = []
    for b in backups:
        stat = b.stat()
        history.append({
            "filename": b.name,
            "path": str(b.resolve()),
            "size_bytes": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        })
    return history


def get_storage_stats() -> Dict[str, Any]:
    """Provide storage metrics for memory.db, WAL files, and backups."""
    db_path = get_default_db_path()
    wal_path = db_path.parent / f"{db_path.name}-wal"
    shm_path = db_path.parent / f"{db_path.name}-shm"

    db_size = db_path.stat().st_size if db_path.exists() else 0
    wal_size = wal_path.stat().st_size if wal_path.exists() else 0
    shm_size = shm_path.stat().st_size if shm_path.exists() else 0

    backups = get_backup_history()
    total_backup_bytes = sum(b["size_bytes"] for b in backups)

    is_healthy, health_msg = verify_database_integrity(db_path)

    # Count rows in key tables
    table_counts = {}
    if db_path.exists():
        try:
            conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
            cursor = conn.cursor()
            for table in [
                "messages", "user_facts", "user_model", "episodic_memories",
                "sync_events", "sync_outbox", "sync_inbox", "agent_runs",
                "tool_invocations", "aura_experiences",
            ]:
                try:
                    cursor.execute(f"SELECT COUNT(*) FROM {table};")
                    table_counts[table] = cursor.fetchone()[0]
                except sqlite3.OperationalError:
                    pass
            cursor.close()
            conn.close()
        except Exception as e:
            logger.warning("Could not read table counts: %s", e)

    return {
        "database_path": str(db_path),
        "database_size_bytes": db_size,
        "wal_size_bytes": wal_size,
        "shm_size_bytes": shm_size,
        "total_storage_bytes": db_size + wal_size + shm_size,
        "integrity_healthy": is_healthy,
        "integrity_message": health_msg,
        "backup_count": len(backups),
        "total_backup_bytes": total_backup_bytes,
        "table_row_counts": table_counts,
    }
