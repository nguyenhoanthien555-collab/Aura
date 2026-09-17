"""
Durable Invocation Ledger for AURA tool executions.
Guarantees replay protection and crash/restart recovery across processes.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from core.logger import logger
from memory.models import ToolInvocationRecord, timestamp_now
from memory.sqlite import SessionLocal, db_lock, init_tool_invocation_tables


def canonical_request_hash(tool: str, arguments: dict[str, Any]) -> str:
    """Computes a deterministic SHA-256 hash of tool arguments."""
    serialized = json.dumps(
        {"tool": tool, "arguments": arguments},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class DurableInvocationLedger:
    """
    Durable storage and query interface for tool invocations.
    Guarantees that a re-delivered tool invocation (e.g. after network drop,
    ACK loss, or server crash) is not executed twice.
    """

    def __init__(self, session_factory=SessionLocal):
        self._session_factory = session_factory
        init_tool_invocation_tables()

    def record_received(
        self,
        invocation_id: str,
        tool: str,
        arguments: dict[str, Any],
        run_id: str = "",
        tool_call_id: str = "",
    ) -> ToolInvocationRecord:
        req_hash = canonical_request_hash(tool, arguments)
        with db_lock:
            session = self._session_factory()
            try:
                rec = session.query(ToolInvocationRecord).filter(
                    (ToolInvocationRecord.invocation_id == invocation_id)
                    | (
                        (ToolInvocationRecord.tool_call_id == tool_call_id)
                        & (ToolInvocationRecord.tool_call_id != "")
                    )
                ).first()

                if rec:
                    session.refresh(rec)
                    session.expunge(rec)
                    return rec

                now = timestamp_now()
                rec = ToolInvocationRecord(
                    invocation_id=invocation_id,
                    run_id=run_id,
                    tool_call_id=tool_call_id,
                    tool=tool,
                    lifecycle_state="RECEIVED",
                    request_hash=req_hash,
                    arguments_json=json.dumps(arguments, ensure_ascii=False, default=str),
                    result_json="",
                    evidence_hash="",
                    created_at=now,
                    updated_at=now,
                )
                session.add(rec)
                session.commit()
                session.refresh(rec)
                session.expunge(rec)
                return rec
            except Exception as e:
                session.rollback()
                logger.error(f"Failed to record received invocation {invocation_id}: {e}")
                raise
            finally:
                session.close()

    def record_executing(self, tool_call_id_or_inv_id: str) -> bool:
        """
        Atomically transition lifecycle_state from RECEIVED to EXECUTING.
        Returns True if this caller successfully claimed execution rights.
        Returns False if already EXECUTING, COMPLETED, FAILED, or record not found.
        """
        with db_lock:
            session = self._session_factory()
            try:
                rec = session.query(ToolInvocationRecord).filter(
                    (ToolInvocationRecord.invocation_id == tool_call_id_or_inv_id)
                    | (ToolInvocationRecord.tool_call_id == tool_call_id_or_inv_id)
                ).first()
                if rec:
                    if rec.lifecycle_state in ("EXECUTING", "COMPLETED", "FAILED"):
                        return False
                    rec.lifecycle_state = "EXECUTING"
                    rec.updated_at = timestamp_now()
                    session.commit()
                    return True
                return False
            except Exception as e:
                session.rollback()
                logger.warning(f"Failed to mark executing for {tool_call_id_or_inv_id}: {e}")
                return False
            finally:
                session.close()

    def record_completed(
        self,
        tool_call_id_or_inv_id: str,
        result: dict[str, Any],
        evidence_hash: str = "",
    ) -> None:
        with db_lock:
            session = self._session_factory()
            try:
                rec = session.query(ToolInvocationRecord).filter(
                    (ToolInvocationRecord.invocation_id == tool_call_id_or_inv_id)
                    | (ToolInvocationRecord.tool_call_id == tool_call_id_or_inv_id)
                ).first()
                if rec:
                    rec.lifecycle_state = "COMPLETED"
                    rec.result_json = json.dumps(result, ensure_ascii=False, default=str)
                    if evidence_hash:
                        rec.evidence_hash = evidence_hash
                    rec.updated_at = timestamp_now()
                    session.commit()
            except Exception as e:
                session.rollback()
                logger.error(f"Failed to mark completed for {tool_call_id_or_inv_id}: {e}")
            finally:
                session.close()

    def record_failed(
        self,
        tool_call_id_or_inv_id: str,
        error: dict[str, Any],
    ) -> None:
        with db_lock:
            session = self._session_factory()
            try:
                rec = session.query(ToolInvocationRecord).filter(
                    (ToolInvocationRecord.invocation_id == tool_call_id_or_inv_id)
                    | (ToolInvocationRecord.tool_call_id == tool_call_id_or_inv_id)
                ).first()
                if rec:
                    rec.lifecycle_state = "FAILED"
                    rec.result_json = json.dumps(error, ensure_ascii=False, default=str)
                    rec.updated_at = timestamp_now()
                    session.commit()
            except Exception as e:
                session.rollback()
                logger.warning(f"Failed to mark failed for {tool_call_id_or_inv_id}: {e}")
            finally:
                session.close()

    def get_invocation(self, tool_call_id_or_inv_id: str) -> ToolInvocationRecord | None:
        if not tool_call_id_or_inv_id:
            return None
        with db_lock:
            session = self._session_factory()
            try:
                rec = session.query(ToolInvocationRecord).filter(
                    (ToolInvocationRecord.invocation_id == tool_call_id_or_inv_id)
                    | (ToolInvocationRecord.tool_call_id == tool_call_id_or_inv_id)
                ).first()
                if rec:
                    session.refresh(rec)
                    session.expunge(rec)
                return rec
            finally:
                session.close()

    def check_replay(self, tool_call_id_or_inv_id: str) -> dict[str, Any] | None:
        """
        Check whether a tool invocation has already been processed.

        Returns:
            - Prior result dict if COMPLETED.
            - Explicit AMBIGUOUS_CRASH_RECOVERY dict if EXECUTING (side effect
              may have occurred but completion was never recorded — unsafe to
              re-execute).
            - Prior error dict if FAILED.
            - None if RECEIVED (no side effect yet — safe to proceed) or if
              the invocation has never been seen.
        """
        if not tool_call_id_or_inv_id:
            return None
        rec = self.get_invocation(tool_call_id_or_inv_id)
        if rec is None:
            return None

        if rec.lifecycle_state == "COMPLETED":
            if rec.result_json:
                try:
                    return json.loads(rec.result_json)
                except Exception:
                    return {"ok": True, "status": "COMPLETED", "raw_result": rec.result_json, "tool": rec.tool}
            return {"ok": True, "status": "COMPLETED", "tool": rec.tool, "corrupted_result": True}

        if rec.lifecycle_state == "EXECUTING":
            # CRITICAL: The invocation was marked EXECUTING before the side
            # effect ran.  A crash between the side effect and the
            # record_completed() call leaves us here.  We MUST NOT return
            # None (which would cause the caller to re-execute), because
            # the side effect may have already occurred.
            return {
                "ok": False,
                "status": "AMBIGUOUS_CRASH_RECOVERY",
                "tool": rec.tool,
                "error": {
                    "code": "CRASH_AFTER_SIDE_EFFECT",
                    "message": (
                        f"Invocation {tool_call_id_or_inv_id} was in EXECUTING "
                        f"state after process restart. The side effect may have "
                        f"occurred but completion was never recorded. Re-execution "
                        f"refused to prevent duplicate side effects."
                    ),
                },
                "recovery_state": "EXECUTING",
                "invocation_id": rec.invocation_id,
            }

        if rec.lifecycle_state == "FAILED":
            if rec.result_json:
                try:
                    return json.loads(rec.result_json)
                except Exception:
                    return {"ok": False, "status": "FAILED", "tool": rec.tool, "raw_result": rec.result_json}
            return {"ok": False, "status": "FAILED", "tool": rec.tool, "corrupted_result": True}

        if rec.lifecycle_state != "RECEIVED":
            # Any unrecognized or corrupted state (e.g. INTERRUPTED, QUARANTINED, corrupted)
            # must never return None to prevent accidental duplicate execution.
            return {
                "ok": False,
                "status": "AMBIGUOUS_CORRUPTED_RECORD",
                "tool": rec.tool,
                "error": {
                    "code": "CORRUPTED_LEDGER_STATE",
                    "message": f"Invocation {tool_call_id_or_inv_id} has corrupted/unrecognized state {rec.lifecycle_state}.",
                },
                "recovery_state": rec.lifecycle_state,
                "invocation_id": rec.invocation_id,
            }

        # RECEIVED — no side effect has started; safe for caller to proceed.
        return None

