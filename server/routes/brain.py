"""
Brain management endpoints for AURA.

Exposes endpoints to query active Brain status, hardware profile, versioned Brain packages,
and execute atomic promotion or rollback.
"""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from brain.hardware import detect_hardware
from brain.package import BrainManager
from brain.providers.local_aura import LocalAuraBrain
from learning.promotion import LearningCoordinator
from server.auth import verify_token

router = APIRouter(prefix="/api/brain", tags=["brain"])

_brain_manager: Optional[BrainManager] = None
_learning_coordinator: Optional[LearningCoordinator] = None


def get_brain_manager() -> BrainManager:
    global _brain_manager
    if _brain_manager is None:
        _brain_manager = BrainManager()
    return _brain_manager


def get_learning_coordinator() -> LearningCoordinator:
    global _learning_coordinator
    if _learning_coordinator is None:
        mgr = get_brain_manager()
        _learning_coordinator = LearningCoordinator(manager=mgr)
    return _learning_coordinator


class PromoteBrainRequest(BaseModel):
    candidate_id: str
    baseline_id: Optional[str] = None


class RollbackBrainRequest(BaseModel):
    reason: Optional[str] = "manual_rollback"


@router.get("")
async def get_brain_info():
    """Returns active Brain details and host hardware profile."""
    mgr = get_brain_manager()
    active_pkg = mgr.get_active_package()
    hw = detect_hardware()

    return {
        "status": "online",
        "active_brain": active_pkg.manifest.to_dict() if active_pkg else None,
        "hardware": hw.to_dict(),
    }


@router.get("/health")
async def get_brain_health():
    """Returns health status of the local Brain."""
    mgr = get_brain_manager()
    active_pkg = mgr.get_active_package()

    return {
        "status": "HEALTHY" if active_pkg else "DEGRADED",
        "active_brain_id": active_pkg.brain_id if active_pkg else None,
        "version": active_pkg.version if active_pkg else None,
    }


@router.get("/versions")
async def list_brain_versions(token: str = Depends(verify_token)):
    """Lists all discovered Brain packages and their lifecycle status."""
    mgr = get_brain_manager()
    packages = mgr.discover_packages()
    return {
        "packages": [p.manifest.to_dict() for p in packages.values()],
        "active_brain_id": mgr.state.get("active_brain_id"),
        "previous_brain_id": mgr.state.get("previous_brain_id"),
    }


@router.post("/promote")
async def promote_brain(request: PromoteBrainRequest, token: str = Depends(verify_token)):
    """Evaluates and atomically promotes a candidate Brain to ACTIVE."""
    coord = get_learning_coordinator()
    try:
        ok, comp = coord.evaluate_and_promote(request.candidate_id, request.baseline_id)
        return {
            "success": ok,
            "candidate_id": request.candidate_id,
            "comparison": comp.to_dict(),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/rollback")
async def rollback_brain(request: RollbackBrainRequest, token: str = Depends(verify_token)):
    """Rolls back ACTIVE brain to previous known-good Brain."""
    coord = get_learning_coordinator()
    ok = coord.rollback(reason=request.reason or "manual_rollback")
    if not ok:
        raise HTTPException(status_code=400, detail="Rollback failed. No valid previous brain found.")
    mgr = get_brain_manager()
    return {
        "success": True,
        "active_brain_id": mgr.state.get("active_brain_id"),
        "message": "Brain successfully rolled back to previous version.",
    }
