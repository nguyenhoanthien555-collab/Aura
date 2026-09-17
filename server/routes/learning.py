"""
Learning and self-adaptation endpoints for AURA.

Exposes endpoints to query learning status, browse eligible experiences,
generate training datasets from operational history, and trigger local training jobs.
"""

import json
import os
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from core.logger import logger
from learning.experience import AuraExperienceStore
from learning.pipeline import LearningCandidatePipeline
from learning.training import TrainingJobRunner
from memory.models import AuraExperienceRecord, TrainingJobRecord
from memory.sqlite import SessionLocal, db_lock
from server.auth import verify_token

router = APIRouter(prefix="/api/learning", tags=["learning"])

_experience_store: Optional[AuraExperienceStore] = None
_pipeline: Optional[LearningCandidatePipeline] = None
_training_runner: Optional[TrainingJobRunner] = None


def get_store() -> AuraExperienceStore:
    global _experience_store
    if _experience_store is None:
        _experience_store = AuraExperienceStore()
    return _experience_store


def get_pipeline() -> LearningCandidatePipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = LearningCandidatePipeline(store=get_store())
    return _pipeline


def get_training_runner() -> TrainingJobRunner:
    global _training_runner
    if _training_runner is None:
        _training_runner = TrainingJobRunner()
    return _training_runner


class GenerateDatasetRequest(BaseModel):
    min_quality: float = 0.6
    limit: int = 200
    dataset_name: str = "aura_learning_dataset"


class TrainModelRequest(BaseModel):
    base_brain_id: str
    dataset_path: str
    output_brain_id: Optional[str] = None
    output_version: str = "1.1.0"
    inject_regression: bool = False


@router.get("/status")
async def get_learning_status():
    """Returns learning pipeline status, eligible experience counts, and recent training jobs."""
    store = get_store()
    with db_lock:
        session = SessionLocal()
        try:
            total_experiences = session.query(AuraExperienceRecord).count()
            eligible_experiences = (
                session.query(AuraExperienceRecord)
                .filter(
                    AuraExperienceRecord.learning_eligible == True,
                    AuraExperienceRecord.privacy_class != "SENSITIVE",
                )
                .count()
            )
            recent_jobs = (
                session.query(TrainingJobRecord)
                .order_by(TrainingJobRecord.started_at.desc())
                .limit(10)
                .all()
            )
            job_dicts = []
            for j in recent_jobs:
                job_dicts.append({
                    "job_id": j.job_id,
                    "base_brain_id": j.base_brain_id,
                    "output_brain_id": j.output_brain_id,
                    "status": j.status,
                    "metrics": json.loads(j.metrics_json or "{}"),
                    "started_at": j.started_at,
                    "completed_at": j.completed_at,
                })
        finally:
            session.close()

    return {
        "status": "active",
        "total_experiences": total_experiences,
        "eligible_experiences": eligible_experiences,
        "recent_training_jobs": job_dicts,
    }


@router.get("/experiences")
async def list_experiences(
    min_quality: float = Query(0.6, ge=0.0, le=1.0),
    limit: int = Query(50, ge=1, le=500),
    category: Optional[str] = Query(None),
    token: str = Depends(verify_token),
):
    """Lists privacy-screened and quality-scored experiences eligible for training."""
    store = get_store()
    exps = store.list_eligible_experiences(
        min_quality=min_quality,
        limit=limit,
        category=category,
    )
    return {
        "count": len(exps),
        "experiences": [e.to_dict() for e in exps],
    }


@router.post("/dataset")
async def generate_dataset(
    request: GenerateDatasetRequest,
    token: str = Depends(verify_token),
):
    """Extracts eligible experiences into a formatted training dataset JSONL file."""
    pipe = get_pipeline()
    manifest = pipe.generate_candidate_dataset(
        min_quality=request.min_quality,
        limit=request.limit,
        dataset_name=request.dataset_name,
    )
    if not manifest:
        raise HTTPException(
            status_code=404,
            detail="No eligible experiences found matching quality threshold",
        )
    return {
        "success": True,
        "manifest": manifest.to_dict(),
    }


@router.post("/train")
async def trigger_training(
    request: TrainModelRequest,
    token: str = Depends(verify_token),
):
    """Executes a local model training/adaptation job."""
    runner = get_training_runner()
    try:
        job = runner.create_job(
            base_brain_id=request.base_brain_id,
            dataset_path=request.dataset_path,
            output_brain_id=request.output_brain_id,
            output_version=request.output_version,
        )
        completed_job = runner.run_job(
            job.job_id,
            inject_regression=request.inject_regression,
        )
        return {
            "success": completed_job.status == "COMPLETED",
            "job": completed_job.to_dict(),
        }
    except Exception as e:
        logger.error("Training job failed: %s", e)
        raise HTTPException(status_code=400, detail=str(e))
