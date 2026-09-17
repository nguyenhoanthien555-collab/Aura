"""
AURA Experience Store.

Persists real operational experiences from chat turns and agent task steps into SQLite.
Applies privacy screening to prevent credential/secret leakage into training candidate datasets,
and computes quality scores based on authoritative evidence and verification verdicts.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from core.ids import new_run_id
from core.logger import logger
from memory.models import AuraExperienceRecord, timestamp_now
from memory.sqlite import SessionLocal, db_lock, init_learning_tables

from enum import Enum

# Sensitive token / key patterns to redact or mark non-eligible
SENSITIVE_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|token|password|auth|bearer|credential|private[_-]?key)"),
    re.compile(r"(?i)sk-[a-zA-Z0-9]{20,}"),
    re.compile(r"(?i)ghp_[a-zA-Z0-9]{20,}"),
]


class ExperienceTaxonomy(str, Enum):
    VERIFIED_SUCCESS = "VERIFIED_SUCCESS"
    VERIFIED_FAILURE = "VERIFIED_FAILURE"
    USER_PROVIDED = "USER_PROVIDED"
    SYSTEM_GENERATED = "SYSTEM_GENERATED"
    TOOL_VERIFIED = "TOOL_VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    CONTRADICTORY = "CONTRADICTORY"
    QUARANTINED = "QUARANTINED"


def classify_taxonomy_tag(
    outcome: str,
    verifier_result: str,
    user_feedback: str,
    privacy_class: str,
    learning_eligible: bool,
    selected_tool: str = "",
    has_contradiction: bool = False,
) -> str:
    """Authoritative classification into standard 8-tier taxonomy."""
    if privacy_class == "SENSITIVE":
        return ExperienceTaxonomy.QUARANTINED.value
    if has_contradiction or verifier_result == "CONTRADICTED":
        return ExperienceTaxonomy.CONTRADICTORY.value
    if not learning_eligible:
        return ExperienceTaxonomy.QUARANTINED.value
    if user_feedback in ("USER_CORRECTED", "CORRECT"):
        return ExperienceTaxonomy.USER_PROVIDED.value
    if verifier_result == "VERIFIED":
        if selected_tool:
            return ExperienceTaxonomy.TOOL_VERIFIED.value
        if outcome == "SUCCESS":
            return ExperienceTaxonomy.VERIFIED_SUCCESS.value
        return ExperienceTaxonomy.VERIFIED_FAILURE.value
    if verifier_result == "UNVERIFIED":
        return ExperienceTaxonomy.UNVERIFIED.value
    return ExperienceTaxonomy.SYSTEM_GENERATED.value


@dataclass
class Experience:
    experience_id: str
    session_id: str
    task_id: str = ""
    run_id: str = ""
    input_text: str = ""
    model_decision: str = "ANSWER"  # ANSWER, TOOL_CALL, CLARIFICATION, CONFIRMATION_REQUIRED, PLAN, UNCERTAIN
    selected_tool: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)
    tool_result: Dict[str, Any] = field(default_factory=dict)
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    verifier_result: str = "UNVERIFIED"  # VERIFIED, INFERRED, CONTRADICTED, UNKNOWN
    final_response: str = ""
    outcome: str = "SUCCESS"  # SUCCESS, FAILED, TIMEOUT, UNKNOWN, RECOVERED
    user_feedback: str = ""
    privacy_class: str = "INTERNAL"  # PUBLIC, INTERNAL, PRIVATE, SENSITIVE
    learning_eligible: bool = True
    quality_score: float = 0.5
    category: str = "general"
    taxonomy_tag: str = "UNVERIFIED"
    created_at: str = field(default_factory=timestamp_now)

    def to_dict(self) -> dict:
        return asdict(self)


class AuraExperienceStore:
    """Manages recording, privacy screening, scoring, and retrieval of Aura experiences."""

    def __init__(self, session_factory=SessionLocal):
        self.session_factory = session_factory
        init_learning_tables()

    @staticmethod
    def screen_privacy(text: str, arguments: Dict[str, Any]) -> Tuple[str, bool]:
        """
        Returns (privacy_class, is_eligible).
        If secrets, tokens, or credentials are found, marks SENSITIVE and ineligible.
        """
        raw_combined = text + " " + json.dumps(arguments)
        for pattern in SENSITIVE_PATTERNS:
            if pattern.search(raw_combined):
                return "SENSITIVE", False

        return "INTERNAL", True

    @staticmethod
    def score_quality(
        outcome: str,
        verifier_result: str,
        user_feedback: str,
        has_evidence: bool,
    ) -> float:
        """
        Authoritative quality scoring:
        - Verified success: 0.9 - 1.0
        - Explicit user correction: 0.95 (valuable learning opportunity)
        - Inferred / partial: 0.6
        - Contradicted / fabricated: 0.1 (negative example)
        - Timeout / unknown: 0.4
        """
        if user_feedback == "CORRECT":
            return 1.0
        if user_feedback == "USER_CORRECTED":
            return 0.95

        score = 0.5
        if outcome == "SUCCESS":
            score += 0.2
        elif outcome in ("FAILED", "TIMEOUT", "UNKNOWN"):
            score -= 0.2

        if verifier_result == "VERIFIED":
            score += 0.3
        elif verifier_result == "INFERRED":
            score += 0.1
        elif verifier_result == "CONTRADICTED":
            score -= 0.4

        if has_evidence:
            score += 0.1

        return max(0.0, min(1.0, round(score, 2)))

    def record_experience(
        self,
        session_id: str,
        input_text: str,
        model_decision: str = "ANSWER",
        task_id: str = "",
        run_id: str = "",
        selected_tool: str = "",
        arguments: Optional[Dict[str, Any]] = None,
        tool_result: Optional[Dict[str, Any]] = None,
        evidence: Optional[List[Dict[str, Any]]] = None,
        verifier_result: str = "UNVERIFIED",
        final_response: str = "",
        outcome: str = "SUCCESS",
        user_feedback: str = "",
        category: str = "general",
        taxonomy_tag: Optional[str] = None,
    ) -> Experience:
        """Records a new operational experience with privacy screening and quality scoring."""
        args_dict = arguments or {}
        res_dict = tool_result or {}
        ev_list = evidence or []

        privacy_class, is_eligible = self.screen_privacy(input_text, args_dict)
        quality = self.score_quality(outcome, verifier_result, user_feedback, bool(ev_list))

        # Contradicted claims should not train positive behavior
        if verifier_result == "CONTRADICTED" and not user_feedback:
            is_eligible = False

        if not taxonomy_tag:
            taxonomy_tag = classify_taxonomy_tag(
                outcome=outcome,
                verifier_result=verifier_result,
                user_feedback=user_feedback,
                privacy_class=privacy_class,
                learning_eligible=is_eligible,
                selected_tool=selected_tool,
                has_contradiction=(verifier_result == "CONTRADICTED"),
            )

        from core.ids import new_run_id

        exp_id = f"exp_{new_run_id()[:16]}"
        now = timestamp_now()

        rec = AuraExperienceRecord(
            experience_id=exp_id,
            session_id=session_id,
            task_id=task_id,
            run_id=run_id,
            input_text=input_text,
            model_decision=model_decision,
            selected_tool=selected_tool,
            arguments_json=json.dumps(args_dict),
            tool_result_json=json.dumps(res_dict),
            evidence_json=json.dumps(ev_list),
            verifier_result=verifier_result,
            final_response=final_response,
            outcome=outcome,
            user_feedback=user_feedback,
            privacy_class=privacy_class,
            learning_eligible=is_eligible,
            quality_score=quality,
            category=category,
            taxonomy_tag=taxonomy_tag,
            created_at=now,
        )

        with db_lock:
            session = self.session_factory()
            try:
                session.add(rec)
                session.commit()
            except Exception as e:
                session.rollback()
                logger.error("Failed to persist experience %s: %s", exp_id, e)
                raise
            finally:
                session.close()

        # Emit to sync engine for distributed replication
        try:
            from core.sync.engine import SyncEngine
            SyncEngine(session_factory=self.session_factory).log_local_experience(rec)
        except Exception as sync_err:
            logger.debug("Sync logging skipped: %s", sync_err)

        logger.debug(
            "Persisted experience %s: outcome=%s, verifier=%s, quality=%.2f, eligible=%s",
            exp_id,
            outcome,
            verifier_result,
            quality,
            is_eligible,
        )

        return Experience(
            experience_id=exp_id,
            session_id=session_id,
            task_id=task_id,
            run_id=run_id,
            input_text=input_text,
            model_decision=model_decision,
            selected_tool=selected_tool,
            arguments=args_dict,
            tool_result=res_dict,
            evidence=ev_list,
            verifier_result=verifier_result,
            final_response=final_response,
            outcome=outcome,
            user_feedback=user_feedback,
            privacy_class=privacy_class,
            learning_eligible=is_eligible,
            quality_score=quality,
            category=category,
            taxonomy_tag=taxonomy_tag,
            created_at=now,
        )

    def list_eligible_experiences(
        self,
        min_quality: float = 0.6,
        limit: int = 100,
        category: Optional[str] = None,
    ) -> List[Experience]:
        """Retrieves experiences eligible for training candidate generation."""
        with db_lock:
            session = self.session_factory()
            try:
                query = session.query(AuraExperienceRecord).filter(
                    AuraExperienceRecord.learning_eligible == True,
                    AuraExperienceRecord.quality_score >= min_quality,
                    AuraExperienceRecord.privacy_class != "SENSITIVE",
                )
                if category:
                    query = query.filter(AuraExperienceRecord.category == category)

                records = query.order_by(AuraExperienceRecord.created_at.desc()).limit(limit).all()

                experiences = []
                for r in records:
                    experiences.append(
                        Experience(
                            experience_id=r.experience_id,
                            session_id=r.session_id,
                            task_id=r.task_id,
                            run_id=r.run_id,
                            input_text=r.input_text,
                            model_decision=r.model_decision,
                            selected_tool=r.selected_tool,
                            arguments=json.loads(r.arguments_json or "{}"),
                            tool_result=json.loads(r.tool_result_json or "{}"),
                            evidence=json.loads(r.evidence_json or "[]"),
                            verifier_result=r.verifier_result,
                            final_response=r.final_response,
                            outcome=r.outcome,
                            user_feedback=r.user_feedback,
                            privacy_class=r.privacy_class,
                            learning_eligible=r.learning_eligible,
                            quality_score=r.quality_score,
                            category=r.category,
                            taxonomy_tag=getattr(r, "taxonomy_tag", "UNVERIFIED") or "UNVERIFIED",
                            created_at=r.created_at,
                        )
                    )
                return experiences
            finally:
                session.close()
