"""
Replication adapters for AURA entities:
- Experience Replication: AuraExperienceRecord <-> SyncEvent (preserves provenance, evidence, taxonomy)
- Tool Replication: Discovered dynamic tools <-> SyncEvent (governance, sandbox, schema)
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from core.logger import logger
from core.sync.models import SyncEvent, SyncEventType
from memory.models import AuraExperienceRecord, ToolProvenanceRecord
from memory.sqlite import SessionLocal, db_lock


class ExperienceReplicationAdapter:
    """Replicates AuraExperienceRecord instances preserving full provenance."""

    @staticmethod
    def to_event(experience: AuraExperienceRecord, origin_node_id: str, sequence: int = 0) -> SyncEvent:
        payload = {
            "experience_id": getattr(experience, "experience_id", f"exp_{experience.run_id}"),
            "session_id": experience.session_id,
            "task_id": getattr(experience, "task_id", ""),
            "run_id": experience.run_id,
            "input_text": getattr(experience, "input_text", getattr(experience, "goal", "")),
            "model_decision": getattr(experience, "model_decision", "ANSWER"),
            "selected_tool": experience.selected_tool,
            "arguments": json.loads(experience.arguments_json or "{}"),
            "tool_result": json.loads(experience.tool_result_json or "{}"),
            "evidence": json.loads(experience.evidence_json or "[]"),
            "verifier_result": experience.verifier_result,
            "final_response": experience.final_response,
            "outcome": experience.outcome,
            "user_feedback": experience.user_feedback,
            "privacy_class": experience.privacy_class,
            "learning_eligible": experience.learning_eligible,
            "quality_score": experience.quality_score,
            "category": experience.category,
            "taxonomy_tag": experience.taxonomy_tag,
            "created_at": experience.created_at,
        }
        provenance = {
            "origin_node_id": origin_node_id,
            "run_id": experience.run_id,
            "synced_at": datetime.now().isoformat(timespec="seconds"),
        }
        return SyncEvent(
            event_id=f"evt_exp_{experience.run_id}",
            origin_node_id=origin_node_id,
            event_type=SyncEventType.EXPERIENCE_CREATED.value,
            entity_type="experience",
            entity_id=experience.run_id,
            logical_sequence=sequence,
            payload=payload,
            provenance=provenance,
        )

    @staticmethod
    def apply_event(event: SyncEvent, session_factory=SessionLocal) -> bool:
        """Apply incoming experience event into local database."""
        if event.entity_type != "experience":
            return False

        with db_lock:
            session = session_factory()
            try:
                p = event.payload
                exp_id = p.get("experience_id") or f"exp_{event.entity_id}"
                existing = session.query(AuraExperienceRecord).filter(
                    (AuraExperienceRecord.experience_id == exp_id) | (AuraExperienceRecord.run_id == event.entity_id)
                ).first()
                if existing:
                    return True  # Already present

                rec = AuraExperienceRecord(
                    experience_id=exp_id,
                    run_id=str(p.get("run_id", event.entity_id)),
                    session_id=str(p.get("session_id", "default")),
                    task_id=str(p.get("task_id", "")),
                    input_text=str(p.get("input_text", p.get("goal", ""))),
                    model_decision=str(p.get("model_decision", "ANSWER")),
                    selected_tool=str(p.get("selected_tool", "")),
                    arguments_json=json.dumps(p.get("arguments", {})),
                    tool_result_json=json.dumps(p.get("tool_result", {})),
                    evidence_json=json.dumps(p.get("evidence", [])),
                    verifier_result=str(p.get("verifier_result", "UNVERIFIED")),
                    final_response=str(p.get("final_response", "")),
                    outcome=str(p.get("outcome", "SUCCESS")),
                    user_feedback=str(p.get("user_feedback", "")),
                    privacy_class=str(p.get("privacy_class", "INTERNAL")),
                    learning_eligible=bool(p.get("learning_eligible", True)),
                    quality_score=float(p.get("quality_score") if p.get("quality_score") is not None else 0.5),
                    category=str(p.get("category", "general")),
                    taxonomy_tag=str(p.get("taxonomy_tag", "UNVERIFIED")),
                    created_at=str(p.get("created_at", datetime.now().isoformat(timespec="seconds"))),
                )
                session.add(rec)
                session.commit()
                logger.info(f"Replicated experience {event.entity_id} from {event.origin_node_id}")
                return True
            except Exception as e:
                session.rollback()
                logger.error(f"Failed to replicate experience {event.entity_id}: {e}")
                return False
            finally:
                session.close()


class ToolReplicationAdapter:
    """Replicates validated tools and capability declarations across nodes."""

    @staticmethod
    def to_event(
        tool_name: str,
        schema: dict[str, Any],
        evidence: list[dict[str, Any]],
        provenance_info: dict[str, Any],
        origin_node_id: str,
        sequence: int = 0,
    ) -> SyncEvent:
        payload = {
            "tool_name": tool_name,
            "schema": schema,
            "evidence": evidence,
            "status": "AVAILABLE_GLOBALLY",
        }
        provenance = {
            "origin_node_id": origin_node_id,
            "details": provenance_info,
            "synced_at": datetime.now().isoformat(timespec="seconds"),
        }
        return SyncEvent(
            event_id=f"evt_tool_{tool_name}_{origin_node_id}",
            origin_node_id=origin_node_id,
            event_type=SyncEventType.TOOL_VERIFIED.value,
            entity_type="tool",
            entity_id=tool_name,
            logical_sequence=sequence,
            payload=payload,
            provenance=provenance,
        )

    @staticmethod
    def apply_event(event: SyncEvent, session_factory=SessionLocal) -> bool:
        """Apply incoming tool replication event."""
        if event.entity_type != "tool":
            return False

        with db_lock:
            session = session_factory()
            try:
                tool_name = event.entity_id
                rec = session.query(ToolProvenanceRecord).filter_by(name=tool_name).first()
                p = event.payload
                prov = event.provenance

                if not rec:
                    rec = ToolProvenanceRecord(
                        name=tool_name,
                        version=int(p.get("version", 1)),
                        gap_id=str(p.get("gap_id", "")),
                        manifest_json=json.dumps(p.get("schema", {})),
                        source_code=str(p.get("source_code", "")),
                        source_digest=str(p.get("source_digest", event.payload_hash)),
                        status="PROMOTED",
                        validation_json=json.dumps({"evidence": p.get("evidence", [])}),
                    )
                    session.add(rec)
                else:
                    rec.status = "PROMOTED"
                    rec.manifest_json = json.dumps(p.get("schema", {}))

                session.commit()
                logger.info(f"Replicated tool capability {tool_name} from {event.origin_node_id}")
                return True
            except Exception as e:
                session.rollback()
                logger.error(f"Failed to replicate tool {event.entity_id}: {e}")
                return False
            finally:
                session.close()
