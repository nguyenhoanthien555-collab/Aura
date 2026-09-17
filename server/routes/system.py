"""
System diagnostics, storage metrics, and automated database backup endpoints.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from memory.backup import (
    create_database_backup,
    get_backup_history,
    get_storage_stats,
    verify_database_integrity,
)
from server.auth import verify_token

router = APIRouter(prefix="/api/system", tags=["system"], dependencies=[Depends(verify_token)])


class BackupRequest(BaseModel):
    tag: str = Field(default="", description="Optional label for the backup snapshot")
    max_backups: int = Field(default=5, ge=1, le=50, description="Max backups to retain")


@router.get("/storage")
async def storage_metrics() -> Dict[str, Any]:
    """Retrieve database, WAL, and table storage metrics."""
    return get_storage_stats()


@router.get("/backups")
async def list_backups() -> List[Dict[str, Any]]:
    """List available online database backups."""
    return get_backup_history()


@router.post("/backup")
async def trigger_backup(req: BackupRequest = BackupRequest()) -> Dict[str, Any]:
    """Create an atomic online backup snapshot of memory.db."""
    try:
        backup_path = create_database_backup(tag=req.tag, max_backups_to_keep=req.max_backups)
        return {
            "status": "success",
            "backup_file": backup_path.name,
            "size_bytes": backup_path.stat().st_size,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Backup failed: {exc}")


@router.post("/integrity")
async def check_integrity() -> Dict[str, Any]:
    """Execute SQLite PRAGMA integrity_check on the live database."""
    healthy, msg = verify_database_integrity()
    return {
        "healthy": healthy,
        "details": msg,
    }
