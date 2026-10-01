"""
System diagnostics, storage metrics, and automated database backup endpoints.
"""

from __future__ import annotations

import os
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


@router.get("/telemetry")
async def host_telemetry() -> Dict[str, Any]:
    """Retrieve comprehensive live host hardware and environment telemetry."""
    import psutil
    from core.hardware_probe import probe_host_environment
    from server.runtime import get_runtime

    runtime = get_runtime()
    env = probe_host_environment()

    cpu_percent = psutil.cpu_percent(interval=None)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage(os.path.abspath(os.sep))

    battery_info = None
    try:
        battery = psutil.sensors_battery()
        if battery is not None:
            battery_info = {
                "percent": round(battery.percent, 1),
                "power_plugged": battery.power_plugged,
                "secs_left": battery.secsleft if battery.secsleft != getattr(psutil, "POWER_TIME_UNLIMITED", -1) else -1,
            }
    except Exception:
        pass

    return {
        "host": {
            "hostname": env.hostname or "AuraHost",
            "model": f"{env.manufacturer} {env.model}".strip() or "Standard PC",
            "os": f"{env.os_name} {env.os_version}".strip(),
            "cpu_name": env.cpu_name,
            "cpu_cores": env.cpu_cores_logical,
            "cpu_percent": cpu_percent,
            "ram_total_gb": env.ram_total_gb,
            "ram_available_gb": round(ram.available / (1024**3), 2),
            "ram_used_percent": round(ram.percent, 1),
            "storage_total_gb": env.primary_storage_total_gb,
            "storage_free_gb": round(disk.free / (1024**3), 2),
            "storage_used_percent": round(disk.percent, 1),
            "gpus": env.gpus,
            "battery": battery_info,
            "uptime_seconds": runtime.uptime,
        },
        "aura": {
            "version": runtime.config.get("app", {}).get("version", "0.2.0"),
            "status": "healthy" if runtime.started else "starting",
            "llm_provider": runtime._provider_chain_label(),
            "memory_connected": runtime.memory is not None,
            "vision_enabled": runtime.vision is not None and runtime.vision.enabled,
        },
    }
