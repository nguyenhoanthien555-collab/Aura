"""
Durable task runtime for AURA 2.0 (Phase 5B).

Provides durable tasks, explicit step checkpoints, retry safety, compound task planning,
and execution that survive network drops, client disconnects, and process restarts.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import hashlib
import json
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from core.ids import (
    new_confirmation_id,
    new_clarification_id,
    new_step_id,
    new_task_id,
)
from core.logger import logger
from memory.models import (
    DurableConfirmationRecord,
    DurableClarificationRecord,
    DurableStepRecord,
    DurableTaskRecord,
    timestamp_now,
)
from memory.sqlite import SessionLocal, db_lock, init_task_tables
from tools.base import ToolResult
from tools.executor import ToolExecutor
from tools.outcome import (
    EvidenceKind,
    SideEffect,
    TimeoutKind,
    ToolStatus,
    retryability_of,
)


class TaskStatus(str, Enum):
    """Authoritative task lifecycle states."""

    CREATED = "CREATED"
    PENDING = "PENDING"
    PLANNING = "PLANNING"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    CANCELLED = "CANCELLED"
    PAUSED = "PAUSED"
    RECOVERING = "RECOVERING"

    @property
    def is_terminal(self) -> bool:
        return self in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.UNKNOWN,
            TaskStatus.CANCELLED,
        )


class StepStatus(str, Enum):
    """Step execution status."""

    PENDING = "PENDING"
    WAITING = "WAITING"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


@dataclass
class DurableStep:
    step_id: str
    task_id: str
    step_index: int
    name: str
    tool: str
    capability: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)
    expected_output: Dict[str, Any] = field(default_factory=dict)
    status: str = StepStatus.PENDING.value
    side_effect: str = SideEffect.UNKNOWN.value
    timeout_seconds: float = 30.0
    retry_policy: Dict[str, Any] = field(default_factory=lambda: {"max_attempts": 3, "backoff": 1.0})
    idempotent: bool = False
    depends_on: List[str] = field(default_factory=list)
    success_condition: str = ""
    failure_condition: str = ""
    recovery_strategy: str = ""
    verification_required: bool = False
    attempt: int = 0
    result: Dict[str, Any] = field(default_factory=dict)
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=timestamp_now)
    updated_at: str = field(default_factory=timestamp_now)

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "task_id": self.task_id,
            "step_index": self.step_index,
            "name": self.name,
            "tool": self.tool,
            "capability": self.capability,
            "arguments": self.arguments,
            "expected_output": self.expected_output,
            "status": self.status,
            "side_effect": self.side_effect,
            "timeout_seconds": self.timeout_seconds,
            "retry_policy": self.retry_policy,
            "idempotent": self.idempotent,
            "depends_on": self.depends_on,
            "success_condition": self.success_condition,
            "failure_condition": self.failure_condition,
            "recovery_strategy": self.recovery_strategy,
            "verification_required": self.verification_required,
            "attempt": self.attempt,
            "result": self.result,
            "evidence": self.evidence,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


SENSITIVE_ARG_KEYS = {
    "password", "secret", "token", "key", "auth", "credential", "private_key", "access_token"
}


def redact_arguments(args: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministically redact sensitive parameters before persisting or logging."""
    if not isinstance(args, dict):
        return args
    redacted = {}
    for k, v in args.items():
        k_str = str(k).lower()
        if any(s in k_str for s in SENSITIVE_ARG_KEYS):
            redacted[k] = "[REDACTED]"
        elif isinstance(v, dict):
            redacted[k] = redact_arguments(v)
        elif isinstance(v, list):
            redacted[k] = [redact_arguments(item) if isinstance(item, dict) else item for item in v]
        else:
            redacted[k] = v
    return redacted


def compute_invocation_fingerprint(
    task_id: str, step_id: str, tool: str, arguments: Dict[str, Any]
) -> str:
    """
    Computes a deterministic cryptographic SHA-256 fingerprint of the invocation context:
    (task_id, step_id, tool, canonical_arguments_json).
    Ensures that confirmation cannot be reused for a different task, step, tool, or altered arguments.
    """
    canonical_json = json.dumps(
        arguments or {},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    payload = f"{task_id}:{step_id}:{tool}:{canonical_json}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass
class DurableConfirmation:
    confirmation_id: str
    task_id: str
    step_id: str
    tool: str
    risk: str = "dangerous"
    side_effect: str = "mutating"
    description: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)
    redacted_arguments: Dict[str, Any] = field(default_factory=dict)
    status: str = "PENDING"  # PENDING, APPROVED, REJECTED, EXPIRED, CONSUMED, INVALIDATED, CANCELLED
    decision: str = ""
    decision_by: str = ""
    decided_at: str = ""
    created_at: str = field(default_factory=timestamp_now)
    expires_at: str = ""
    fingerprint: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "confirmation_id": self.confirmation_id,
            "task_id": self.task_id,
            "step_id": self.step_id,
            "tool": self.tool,
            "risk": self.risk,
            "side_effect": self.side_effect,
            "description": self.description,
            "arguments": self.redacted_arguments or redact_arguments(self.arguments),
            "status": self.status,
            "decision": self.decision,
            "decision_by": self.decision_by,
            "decided_at": self.decided_at,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "fingerprint": self.fingerprint,
        }


@dataclass
class DurableClarification:
    clarification_id: str
    task_id: str
    goal: str
    questions: List[str] = field(default_factory=list)
    answers: Dict[str, Any] = field(default_factory=dict)
    status: str = "PENDING"  # PENDING, ANSWERED, CANCELLED
    created_at: str = field(default_factory=timestamp_now)
    answered_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "clarification_id": self.clarification_id,
            "task_id": self.task_id,
            "goal": self.goal,
            "questions": self.questions,
            "answers": self.answers,
            "status": self.status,
            "created_at": self.created_at,
            "answered_at": self.answered_at,
        }


def format_task_user_response(task: "DurableTask") -> Dict[str, Any]:
    """
    Produces an authoritative, honest user-facing response grounded strictly in
    task lifecycle status, step execution records, and postcondition evidence.
    Never hallucinates ungrounded success.
    """
    # 1. Check waiting states first
    if task.status == TaskStatus.WAITING.value:
        # Check confirmation
        if task.confirmation and task.confirmation.get("status") == "PENDING":
            tool_name = task.confirmation.get("tool", "action")
            args = task.confirmation.get("arguments", {})
            return {
                "state": "WAITING_FOR_CONFIRMATION",
                "text": f"Action '{tool_name}' requires confirmation before proceeding. Arguments: {args}",
                "verified": False,
                "confirmation_id": task.confirmation.get("confirmation_id"),
                "tool": tool_name,
                "arguments": args,
            }
        # Check clarification
        if task.clarification and task.clarification.get("status") == "PENDING":
            questions = task.clarification.get("questions", [])
            q_str = " ".join(questions) if questions else "Please clarify your request."
            return {
                "state": "WAITING_FOR_CLARIFICATION",
                "text": f"Clarification needed: {q_str}",
                "verified": False,
                "clarification_id": task.clarification.get("clarification_id"),
                "questions": questions,
            }
        # Check synthesis wait
        if task.recovery_state and "SYNTHESIZ" in task.recovery_state:
            cap = task.recovery_state.split(":", 1)[-1] if ":" in task.recovery_state else "missing capability"
            return {
                "state": "WAITING_FOR_SYNTHESIS",
                "text": f"Missing capability '{cap}' detected; synthesizing tool in sandbox.",
                "verified": False,
                "capability": cap,
            }
        return {
            "state": "WAITING",
            "text": f"Task is waiting: {task.last_error or 'Awaiting operator input.'}",
            "verified": False,
        }

    # 2. Check UNKNOWN status (interrupted mutation, timeout on non-idempotent action)
    if task.status == TaskStatus.UNKNOWN.value or any(s.status == StepStatus.UNKNOWN.value for s in task.steps):
        unknown_steps = [s.name for s in task.steps if s.status == StepStatus.UNKNOWN.value]
        step_str = f" in step(s): {', '.join(unknown_steps)}" if unknown_steps else ""
        return {
            "state": "UNKNOWN_UNRESOLVED",
            "text": f"Action was initiated{step_str}, but the final state could not be verified. Outcome remains unresolved.",
            "verified": False,
            "error": task.last_error,
        }

    # 3. Check FAILED status
    if task.status == TaskStatus.FAILED.value:
        last_err = task.last_error or ""
        if "synthesis" in last_err.lower() or task.recovery_state == "SYNTHESIS_FAILED":
            return {
                "state": "SYNTHESIS_FAILED",
                "text": f"Could not safely create the missing capability: {last_err or 'Synthesis policy rejected or failed.'}",
                "verified": False,
                "error": last_err,
            }
        failed_step = next((s for s in task.steps if s.status == StepStatus.FAILED.value), None)
        step_err = failed_step.result.get("error", "") if failed_step and failed_step.result else ""
        err_msg = step_err or last_err or "Step execution failed."
        return {
            "state": "FAILED",
            "text": f"Task failed: {err_msg}",
            "verified": False,
            "error": err_msg,
        }

    # 4. Check CANCELLED status
    if task.status == TaskStatus.CANCELLED.value:
        return {
            "state": "CANCELLED",
            "text": f"Task was cancelled: {task.last_error or 'User requested cancellation.'}",
            "verified": False,
        }

    # 5. Check COMPLETED status
    if task.status == TaskStatus.COMPLETED.value:
        # Collect final output from the last executed step
        final_output = None
        for s in reversed(task.steps):
            if s.status == StepStatus.COMPLETED.value:
                res = s.result or {}
                if "output" in res and res["output"] != "":
                    final_output = res["output"]
                    break
                if "data" in res and res["data"]:
                    final_output = res["data"]
                    break
                if "result" in res and res["result"] != "":
                    final_output = res["result"]
                    break

        # Check evidence across steps
        has_verified_postcondition = False
        all_evidence = []
        for s in task.steps:
            for ev in (s.evidence or []):
                all_evidence.append(ev)
                if ev.get("kind") == EvidenceKind.POSTCONDITION.value or ev.get("kind") == "POSTCONDITION":
                    if ev.get("verified") is True:
                        has_verified_postcondition = True

        # Check if any step was mutating
        has_mutating_steps = any(
            str(s.side_effect).lower() in ("mutating", "non_idempotent", "idempotent")
            for s in task.steps
        )

        out_str = f": {final_output}" if final_output is not None else "."
        if isinstance(final_output, (dict, list)):
            out_str = f": {json.dumps(final_output, ensure_ascii=False)}"

        if has_verified_postcondition or not has_mutating_steps:
            # Verified success (either mutating step verified via postcondition or read-only/computational task completed)
            return {
                "state": "COMPLETED_VERIFIED",
                "text": f"Task completed and verified successfully{out_str}",
                "verified": True,
                "output": final_output,
                "evidence_count": len(all_evidence),
            }
        else:
            # Mutating action completed without independent postcondition verification
            return {
                "state": "COMPLETED_INFERRED",
                "text": f"Task completed based on execution report, but independent postcondition verification was not established{out_str}",
                "verified": False,
                "output": final_output,
                "evidence_count": len(all_evidence),
            }

    # Default for in-progress states (RUNNING, PLANNING, READY, CREATED)
    return {
        "state": task.status,
        "text": f"Task is currently {task.status.lower()}.",
        "verified": False,
    }


@dataclass
class DurableTask:
    task_id: str
    session_id: str
    goal: str
    status: str = TaskStatus.CREATED.value
    plan: Dict[str, Any] = field(default_factory=dict)
    current_step_id: str = ""
    attempt: int = 0
    last_error: str = ""
    recovery_state: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    steps: List[DurableStep] = field(default_factory=list)
    confirmation: Optional[Dict[str, Any]] = None
    clarification: Optional[Dict[str, Any]] = None
    created_at: str = field(default_factory=timestamp_now)
    updated_at: str = field(default_factory=timestamp_now)

    @property
    def user_response(self) -> Dict[str, Any]:
        return format_task_user_response(self)

    def to_dict(self) -> dict:
        d = {
            "task_id": self.task_id,
            "session_id": self.session_id,
            "goal": self.goal,
            "status": self.status,
            "status_detail": self.recovery_state if self.recovery_state else self.status,
            "plan": self.plan,
            "current_step_id": self.current_step_id,
            "attempt": self.attempt,
            "last_error": self.last_error,
            "recovery_state": self.recovery_state,
            "metadata": self.metadata,
            "steps": [s.to_dict() for s in self.steps],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.confirmation is not None:
            d["confirmation"] = self.confirmation
        if self.clarification is not None:
            d["clarification"] = self.clarification
        d["user_response"] = format_task_user_response(self)
        return d


@dataclass
class TaskPlan:
    """Explicit compound task plan or clarification proposal."""
    goal: str
    steps: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    status: str = "PLANNED"  # "PLANNED" or "NEEDS_CLARIFICATION"
    questions: List[str] = field(default_factory=list)

    @property
    def needs_clarification(self) -> bool:
        return self.status == "NEEDS_CLARIFICATION"

    def to_dict(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {
            "goal": self.goal,
            "status": self.status,
            "metadata": self.metadata,
        }
        if self.needs_clarification:
            res["questions"] = self.questions
        else:
            res["steps"] = self.steps
        return res


class UnresolvedParameterError(ValueError):
    """Raised when a task step references an unresolved step output or context variable."""
    pass


MAX_PLAN_STEPS = 20
MAX_PLAN_ARGUMENT_BYTES = 64 * 1024
MIN_TIMEOUT_SECONDS = 1.0
MAX_TIMEOUT_SECONDS = 600.0
MIN_MAX_ATTEMPTS = 1
MAX_MAX_ATTEMPTS = 10
MIN_BACKOFF = 0.0
MAX_BACKOFF = 60.0

PROHIBITED_PLAN_TOKENS = (
    "rm -rf",
    "drop table",
    "drop database",
    "format c:",
    "mkfs",
    "__import__",
    "subprocess",
    "os.system",
    "os.popen",
    "eval(",
    "exec(",
    "compile(",
)


class CompoundTaskPlanner:
    """
    Produces explicit executable compound task plans with dependencies,
    timeouts, retry policies, and verification requirements.
    Supports autonomous natural-language plan decomposition via LLM,
    strict deterministic parsing, and safety bounds validation.
    """

    def __init__(self, registry=None):
        self.registry = registry

    @classmethod
    def substitute_parameters(cls, arguments: Any, context: Dict[str, Any]) -> Any:
        """
        Recursively resolves deterministic references like ${step_0.package} or ${step_0.output.field}
        against the execution context.
        Raises UnresolvedParameterError if any referenced step or key is missing.
        """
        import re

        pattern = re.compile(r"\$\{([^}]+)\}")

        def _resolve_token(token: str) -> Any:
            parts = token.strip().split(".")
            if not parts:
                raise UnresolvedParameterError(f"Empty parameter reference: '${{{token}}}'")

            step_key = parts[0]
            if step_key not in context:
                raise UnresolvedParameterError(
                    f"Unresolved step reference '${{{token}}}': step '{step_key}' not found in context"
                )

            curr = context[step_key]
            for part in parts[1:]:
                if isinstance(curr, dict):
                    if part in curr:
                        curr = curr[part]
                    elif part == "result" and "output" in curr:
                        curr = curr["output"]
                    elif part == "output" and "result" in curr:
                        curr = curr["result"]
                    else:
                        raise UnresolvedParameterError(
                            f"Unresolved field '${{{token}}}': key '{part}' not found in {list(curr.keys())}"
                        )
                elif hasattr(curr, part):
                    curr = getattr(curr, part)
                else:
                    raise UnresolvedParameterError(
                        f"Unresolved field '${{{token}}}': cannot access '{part}' on {type(curr).__name__}"
                    )
            return curr

        def _walk(val: Any) -> Any:
            if isinstance(val, dict):
                return {k: _walk(v) for k, v in val.items()}
            elif isinstance(val, list):
                return [_walk(item) for item in val]
            elif isinstance(val, str):
                # Check for exact single token: "${step_0.count}" -> return raw typed value
                m_exact = re.fullmatch(r"\$\{([^}]+)\}", val.strip())
                if m_exact:
                    return _resolve_token(m_exact.group(1))

                # Check for embedded strings: "package=${step_0.pkg}&id=1"
                def _replace(match):
                    res = _resolve_token(match.group(1))
                    return str(res)

                return pattern.sub(_replace, val)
            return val

        return _walk(arguments)

    @classmethod
    def validate_plan(
        cls,
        plan: TaskPlan,
        tools_catalogue: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """
        Validates the compound task plan:
        1. Ensures plan is non-empty and does not exceed MAX_PLAN_STEPS.
        2. Ensures argument payload size does not exceed MAX_PLAN_ARGUMENT_BYTES.
        3. Ensures all step IDs are non-empty, unique, and strings.
        4. Ensures all depends_on IDs refer to known steps in this plan.
        5. Ensures no step depends on itself.
        6. Detects cycles in step dependency DAG via 3-color DFS.
        7. Validates parameter reference syntax and rejects self/forward references.
        8. Enforces timeout, retry policy, and prohibited token bounds.
        """
        import re

        steps = plan.steps if hasattr(plan, "steps") else (plan.get("steps", []) if isinstance(plan, dict) else [])
        if not steps:
            raise ValueError("Plan validation failed: plan cannot be empty")

        if len(steps) > MAX_PLAN_STEPS:
            raise ValueError(
                f"Plan validation failed: plan exceeds maximum step limit of {MAX_PLAN_STEPS} (got {len(steps)})"
            )

        # 1. Check total argument payload size
        total_arg_bytes = 0
        for s in steps:
            args = s.get("arguments", {})
            if not isinstance(args, dict):
                raise ValueError(f"Plan validation failed: step '{s.get('step_id')}' arguments must be a dictionary")
            total_arg_bytes += len(json.dumps(args, ensure_ascii=False))
        if total_arg_bytes > MAX_PLAN_ARGUMENT_BYTES:
            raise ValueError(
                f"Plan validation failed: argument payload size {total_arg_bytes} exceeds limit of {MAX_PLAN_ARGUMENT_BYTES} bytes"
            )

        # 2. Check unique step IDs & build index lookup
        step_ids = set()
        id_to_idx: Dict[str, int] = {}
        name_to_idx: Dict[str, int] = {}
        index_key_to_idx: Dict[str, int] = {}
        adj: Dict[str, List[str]] = {}

        for idx, s in enumerate(steps):
            sid = s.get("step_id")
            if not sid or not isinstance(sid, str) or not sid.strip():
                raise ValueError("Plan validation failed: step is missing 'step_id'")
            sid = sid.strip()
            if sid in step_ids:
                raise ValueError(f"Plan validation failed: duplicate step_id '{sid}'")
            step_ids.add(sid)
            id_to_idx[sid] = idx
            if s.get("name"):
                name_to_idx[s.get("name")] = idx
            index_key_to_idx[f"step_{idx}"] = idx
            adj[sid] = list(s.get("depends_on", []))

        # 3. Check references & self-dependencies
        for sid, deps in adj.items():
            for dep in deps:
                if dep == sid:
                    raise ValueError(f"Plan validation failed: step '{sid}' cannot depend on itself")
                if dep not in step_ids:
                    raise ValueError(f"Plan validation failed: step '{sid}' depends on non-existent step '{dep}'")

        # 4. Cycle detection via 3-color DFS (0=unvisited, 1=visiting, 2=visited)
        state: Dict[str, int] = {sid: 0 for sid in step_ids}

        def _dfs(node: str, path: List[str]) -> None:
            state[node] = 1
            for neighbor in adj.get(node, []):
                if state[neighbor] == 1:
                    cycle_nodes = path + [neighbor]
                    raise ValueError(
                        f"Plan validation failed: cycle detected in task dependencies: {' -> '.join(cycle_nodes)}"
                    )
                if state[neighbor] == 0:
                    _dfs(neighbor, path + [neighbor])
            state[node] = 2

        for sid in step_ids:
            if state[sid] == 0:
                _dfs(sid, [sid])

        # Compute transitive ancestors for each step: ancestors[sid] = set of all steps that sid depends on directly or transitively
        ancestors: Dict[str, Set[str]] = {sid: set() for sid in step_ids}

        def _get_ancestors(node: str) -> Set[str]:
            if ancestors[node]:
                return ancestors[node]
            direct_deps = set(adj.get(node, []))
            res = set(direct_deps)
            for dep in direct_deps:
                res.update(_get_ancestors(dep))
            ancestors[node] = res
            return res

        for sid in step_ids:
            _get_ancestors(sid)

        # 5. Validate parameter template tokens, forward references, self-references, and dependency ancestry
        pattern = re.compile(r"\$\{([^}]+)\}")
        for idx, s in enumerate(steps):
            sid = s.get("step_id")

            def _check_args(val: Any):
                if isinstance(val, dict):
                    for v in val.values():
                        _check_args(v)
                elif isinstance(val, list):
                    for item in val:
                        _check_args(item)
                elif isinstance(val, str):
                    for match in pattern.finditer(val):
                        token = match.group(1).strip()
                        if not token or " " in token:
                            raise ValueError(f"Plan validation failed: malformed parameter template '${{{token}}}'")

                        parts = token.split(".")
                        if len(parts) < 2:
                            raise ValueError(
                                f"Plan validation failed: parameter reference '${{{token}}}' in step '{sid}' must specify a field (e.g. '${{{parts[0]}.output}}')"
                            )
                        ref_target = parts[0]
                        for field_part in parts[1:]:
                            if not field_part or not re.match(r"^[a-zA-Z0-9_]+$", field_part):
                                raise ValueError(
                                    f"Plan validation failed: invalid field identifier '{field_part}' in parameter reference '${{{token}}}' in step '{sid}'"
                                )

                        target_idx = id_to_idx.get(ref_target)
                        target_sid = ref_target if ref_target in id_to_idx else None
                        if target_idx is None:
                            target_idx = name_to_idx.get(ref_target)
                            if target_idx is not None:
                                target_sid = steps[target_idx].get("step_id")
                        if target_idx is None:
                            target_idx = index_key_to_idx.get(ref_target)
                            if target_idx is not None:
                                target_sid = steps[target_idx].get("step_id")

                        if target_idx is None or target_sid is None:
                            raise ValueError(
                                f"Plan validation failed: parameter reference '${{{token}}}' in step '{sid}' references non-existent step '{ref_target}'"
                            )
                        if target_idx == idx:
                            raise ValueError(
                                f"Plan validation failed: self-referential parameter reference '${{{token}}}' in step '{sid}'"
                            )
                        if target_idx > idx:
                            raise ValueError(
                                f"Plan validation failed: forward parameter reference '${{{token}}}' in step '{sid}' references future step '{ref_target}'"
                            )

                        if target_sid not in ancestors.get(sid, set()):
                            raise ValueError(
                                f"Plan validation failed: step '{sid}' references '${{{token}}}' from step '{target_sid}' but does not declare a dependency on it in depends_on"
                            )

            _check_args(s.get("arguments", {}))

        # 6. Validate timeouts, retry policies, and safety checks
        for s in steps:
            sid = s.get("step_id")
            if "timeout_seconds" in s and s["timeout_seconds"] is not None:
                try:
                    t_sec = float(s["timeout_seconds"])
                except (TypeError, ValueError):
                    raise ValueError(f"Plan validation failed: step '{sid}' timeout_seconds must be numeric")
                if t_sec < MIN_TIMEOUT_SECONDS or t_sec > MAX_TIMEOUT_SECONDS:
                    raise ValueError(
                        f"Plan validation failed: step '{sid}' timeout_seconds {t_sec}s out of bounds [{MIN_TIMEOUT_SECONDS}, {MAX_TIMEOUT_SECONDS}]"
                    )

            if "retry_policy" in s and isinstance(s["retry_policy"], dict):
                rp = s["retry_policy"]
                if "max_attempts" in rp:
                    try:
                        ma = int(rp["max_attempts"])
                    except (TypeError, ValueError):
                        raise ValueError(f"Plan validation failed: step '{sid}' retry max_attempts must be an integer")
                    if ma < MIN_MAX_ATTEMPTS or ma > MAX_MAX_ATTEMPTS:
                        raise ValueError(
                            f"Plan validation failed: step '{sid}' retry max_attempts {ma} out of bounds [{MIN_MAX_ATTEMPTS}, {MAX_MAX_ATTEMPTS}]"
                        )
                if "backoff" in rp:
                    try:
                        bo = float(rp["backoff"])
                    except (TypeError, ValueError):
                        raise ValueError(f"Plan validation failed: step '{sid}' retry backoff must be numeric")
                    if bo < MIN_BACKOFF or bo > MAX_BACKOFF:
                        raise ValueError(
                            f"Plan validation failed: step '{sid}' retry backoff {bo}s out of bounds [{MIN_BACKOFF}, {MAX_BACKOFF}]"
                        )

            cap = str(s.get("capability") or "").strip()
            tool_name = str(s.get("tool") or "").strip()
            corpus = f"{cap} {tool_name} {json.dumps(s.get('arguments', {}))}".lower()
            for bad_tok in PROHIBITED_PLAN_TOKENS:
                if bad_tok in corpus:
                    raise ValueError(
                        f"Plan validation failed: prohibited safety token '{bad_tok}' detected in step '{sid}'"
                    )

            if cap and not re.match(r"^[a-zA-Z0-9_.-]+$", cap):
                raise ValueError(f"Plan validation failed: invalid capability format '{cap}' in step '{sid}'")

        # 7. Tool Schema Grounding & Parameter Validation (Slice 1 & Slice 4)
        if tools_catalogue:
            cat_by_name = {t.get("name"): t for t in tools_catalogue if t.get("name")}
            for s in steps:
                sid = s.get("step_id")
                tool_name = s.get("tool")
                if tool_name and tool_name in cat_by_name:
                    tool_info = cat_by_name[tool_name]
                    params_schema = tool_info.get("parameters", {})
                    properties = {}
                    required = []
                    if isinstance(params_schema, dict):
                        if "properties" in params_schema and isinstance(params_schema["properties"], dict):
                            properties = params_schema["properties"]
                            required = params_schema.get("required", [])
                        elif "type" not in params_schema:
                            properties = params_schema
                    args = s.get("arguments", {})

                    # A. Required parameters check
                    for req in required:
                        if req not in args:
                            raise ValueError(
                                f"Plan validation failed: step '{sid}' using tool '{tool_name}' "
                                f"is missing required parameter '{req}'"
                            )

                    # B. Unknown parameters check (if schema specifies closed properties)
                    if properties:
                        for arg_key in args:
                            if arg_key not in properties:
                                raise ValueError(
                                    f"Plan validation failed: step '{sid}' using tool '{tool_name}' "
                                    f"passed unknown parameter '{arg_key}'"
                                )

                    # C. Primitive type validation (skip dynamic parameter references ${...})
                    for arg_key, arg_val in args.items():
                        if isinstance(arg_val, str) and "${" in arg_val:
                            continue  # Dynamic parameter reference evaluated at runtime
                        expected_type = properties.get(arg_key, {}).get("type")
                        if not expected_type:
                            continue
                        if expected_type == "string" and not isinstance(arg_val, str):
                            raise ValueError(
                                f"Plan validation failed: step '{sid}' parameter '{arg_key}' "
                                f"must be string, got {type(arg_val).__name__}"
                            )
                        elif expected_type == "integer":
                            if not isinstance(arg_val, int) or isinstance(arg_val, bool):
                                raise ValueError(
                                    f"Plan validation failed: step '{sid}' parameter '{arg_key}' "
                                    f"must be integer, got {type(arg_val).__name__}"
                                )
                        elif expected_type == "number":
                            if not isinstance(arg_val, (int, float)) or isinstance(arg_val, bool):
                                raise ValueError(
                                    f"Plan validation failed: step '{sid}' parameter '{arg_key}' "
                                    f"must be numeric, got {type(arg_val).__name__}"
                                )
                        elif expected_type == "boolean" and not isinstance(arg_val, bool):
                            raise ValueError(
                                f"Plan validation failed: step '{sid}' parameter '{arg_key}' "
                                f"must be boolean, got {type(arg_val).__name__}"
                            )
                        elif expected_type == "array" and not isinstance(arg_val, list):
                            raise ValueError(
                                f"Plan validation failed: step '{sid}' parameter '{arg_key}' "
                                f"must be list/array, got {type(arg_val).__name__}"
                            )
                        elif expected_type == "object" and not isinstance(arg_val, dict):
                            raise ValueError(
                                f"Plan validation failed: step '{sid}' parameter '{arg_key}' "
                                f"must be dict/object, got {type(arg_val).__name__}"
                            )

                    # D. Authoritative side_effect and risk preservation (Slice 4)
                    auth_side_effect = tool_info.get("side_effect")
                    if auth_side_effect and auth_side_effect.lower() in ("mutating", "dangerous"):
                        if s.get("side_effect", "").lower() in ("read_only", "none"):
                            raise ValueError(
                                f"Plan validation failed: step '{sid}' tool '{tool_name}' has authoritative "
                                f"side_effect '{auth_side_effect}', but step marked side_effect='{s.get('side_effect')}'. "
                                f"Planner cannot downgrade tool risk or side-effects."
                            )

        return True

    @classmethod
    def validate_grounding(
        cls,
        plan: TaskPlan,
        goal: str = "",
        tools_catalogue: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """
        Deterministic semantic grounding validation.
        Enforces producer -> consumer dataflow:
        1. Dangling dependency on pure computational producer: If Step B depends on Step A,
           and Step A is a READ_ONLY or computational producer, Step B must consume
           Step A's output via at least one parameter reference (${Step_A...}).
        2. Chained operation on result: If the user goal explicitly requests
           operating on a result ("the result", "that result", "the output",
           "then <action> it", etc.) or Step B's name/tool explicitly denotes
           operating on a result, Step B must not consume unrelated literal values
           in place of a parameter reference to the producer step.
        """
        import re

        steps = plan.steps if hasattr(plan, "steps") else (plan.get("steps", []) if isinstance(plan, dict) else [])
        if not steps or len(steps) < 2:
            return True

        clean_goal = (goal or (plan.goal if hasattr(plan, "goal") else "")).strip().lower()

        # Build maps for step resolution
        step_map = {s.get("step_id"): s for s in steps}
        id_to_idx = {s.get("step_id"): i for i, s in enumerate(steps)}

        # Helper: extract all referenced step IDs in a step's arguments
        pattern = re.compile(r"\$\{([^}]+)\}")

        def _get_step_refs(step_args: Any) -> Set[str]:
            refs = set()
            def _scan(val: Any):
                if isinstance(val, dict):
                    for v in val.values():
                        _scan(v)
                elif isinstance(val, list):
                    for item in val:
                        _scan(item)
                elif isinstance(val, str):
                    for m in pattern.finditer(val):
                        tok = m.group(1).strip()
                        ref_tgt = tok.split(".")[0]
                        if ref_tgt in step_map:
                            refs.add(ref_tgt)
                        elif ref_tgt.startswith("step_"):
                            try:
                                o_idx = int(ref_tgt.split("_")[1])
                                if o_idx < len(steps):
                                    refs.add(steps[o_idx].get("step_id"))
                            except (ValueError, IndexError):
                                pass
                        else:
                            for s in steps:
                                if s.get("name") == ref_tgt:
                                    refs.add(s.get("step_id"))
                                    break
            _scan(step_args)
            return refs

        step_refs: Dict[str, Set[str]] = {
            s.get("step_id"): _get_step_refs(s.get("arguments", {})) for s in steps
        }

        # Condition 1: Check if the goal explicitly requests result chaining
        has_result_chaining = bool(re.search(
            r"\b(then|after\s+that|and)\s+.*?\b(result|output|that|it)\b|\b(the|that|its)\s+(result|output)\b",
            clean_goal,
            re.IGNORECASE,
        ))

        # Check for ungrounded consumer steps
        for idx in range(1, len(steps)):
            s = steps[idx]
            sid = s.get("step_id")
            s_name = str(s.get("name", "")).lower()
            s_tool = str(s.get("tool", "")).lower()
            deps = s.get("depends_on", [])
            refs = step_refs.get(sid, set())

            # Does this step's name or tool explicitly denote operating on a result?
            name_indicates_result = any(kw in s_name for kw in ("result", "output")) or "result" in s_tool

            # Is this step a mathematical / calculation operation depending on an upstream calculation?
            is_math_chain = False
            for dep in deps:
                dep_step = step_map.get(dep)
                if dep_step:
                    dep_tool = str(dep_step.get("tool", "")).lower()
                    dep_cap = str(dep_step.get("capability", "")).lower()
                    if ("math" in dep_tool or "math" in dep_cap) and (
                        "math" in s_tool or "square" in s_name or "calc" in s_name or "math" in s_name
                    ):
                        is_math_chain = True

            # If the goal chains results, or the step denotes operating on a result,
            # or it's a chained mathematical computation where step B depends on step A:
            if has_result_chaining or name_indicates_result or is_math_chain:
                if len(refs) == 0:
                    prev_id = deps[0] if deps else steps[idx - 1].get("step_id")
                    raise ValueError(
                        f"Plan grounding failed: step '{sid}' ('{s.get('name')}') is expected to operate on the output "
                        f"of preceding step '{prev_id}', but contains no parameter reference '${{{prev_id}.output}}' in its arguments"
                    )

        return True

    @classmethod
    def parse_plan_response(cls, raw_text: str) -> Dict[str, Any]:
        """
        Deterministically parses and extracts a structured task plan JSON from LLM output.
        Handles pure JSON, fenced markdown (```json ... ```), and outer bracket extraction.
        Never uses eval/exec/compile.
        Raises ValueError if the output cannot be parsed or lacks required fields.
        """
        import re

        if not raw_text or not isinstance(raw_text, str) or not raw_text.strip():
            raise ValueError("Planner response is empty")

        text = raw_text.strip()
        data = None

        # 1. Try pure JSON parse
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                data = parsed
        except json.JSONDecodeError:
            pass

        # 2. Try markdown fenced block
        if data is None:
            m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if m:
                try:
                    parsed = json.loads(m.group(1))
                    if isinstance(parsed, dict):
                        data = parsed
                except json.JSONDecodeError:
                    pass

        # 3. Try finding outer braces
        if data is None:
            first_brace = text.find("{")
            last_brace = text.rfind("}")
            if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
                try:
                    parsed = json.loads(text[first_brace : last_brace + 1])
                    if isinstance(parsed, dict):
                        data = parsed
                except json.JSONDecodeError:
                    pass

        if data is None:
            raise ValueError("Failed to parse plan JSON from model response")

        if not isinstance(data, dict):
            raise ValueError("Plan root must be a JSON object")

        status = str(data.get("status") or "PLANNED").strip().upper()
        if status == "NEEDS_CLARIFICATION":
            questions_raw = data.get("questions")
            if not isinstance(questions_raw, list) or not questions_raw:
                raise ValueError("Clarification response must contain a non-empty 'questions' list")
            if len(questions_raw) > 5:
                raise ValueError(f"Clarification response contains too many questions ({len(questions_raw)} > 5)")
            questions = []
            for i, q in enumerate(questions_raw):
                if not isinstance(q, str) or not q.strip():
                    raise ValueError(f"Clarification question at index {i} must be a non-empty string")
                if len(q.strip()) > 500:
                    raise ValueError(f"Clarification question at index {i} exceeds 500 characters")
                questions.append(q.strip())
            return {
                "status": "NEEDS_CLARIFICATION",
                "goal": str(data.get("goal", "")),
                "questions": questions,
                "metadata": data.get("metadata", {}) if isinstance(data.get("metadata"), dict) else {},
            }

        if status != "PLANNED":
            raise ValueError(f"Unknown plan status '{status}' (must be 'PLANNED' or 'NEEDS_CLARIFICATION')")

        steps_raw = data.get("steps")
        if steps_raw is None or not isinstance(steps_raw, list):
            raise ValueError("Plan must contain a 'steps' list")

        if len(steps_raw) == 0:
            raise ValueError("Plan must contain at least one step")

        normalized_steps = []
        for idx, s in enumerate(steps_raw):
            if not isinstance(s, dict):
                raise ValueError(f"Step at index {idx} must be a dictionary")

            sid = str(s.get("step_id") or f"step_{idx}").strip()
            name = str(s.get("name") or s.get("tool") or s.get("tool_name") or f"step_{idx + 1}").strip()
            tool = str(s.get("tool") or s.get("tool_name") or "").strip()
            capability = str(s.get("capability") or "").strip()

            if capability and not re.match(r"^[a-zA-Z0-9_.-]+$", capability):
                capability = re.sub(r"[^a-zA-Z0-9_.-]+", "_", capability).strip("_")

            if not tool and capability:
                tool = capability.replace(".", "_").replace("-", "_").lower()
            elif not capability and tool:
                capability = f"custom.{tool}"

            if not tool and not capability:
                raise ValueError(f"Step '{sid}' must specify either 'tool' or 'capability'")

            args = s.get("arguments") if s.get("arguments") is not None else s.get("args", {})
            if not isinstance(args, dict):
                raise ValueError(f"Step '{sid}' arguments must be a dictionary")

            deps = s.get("depends_on", [])
            if not isinstance(deps, list):
                raise ValueError(f"Step '{sid}' depends_on must be a list")

            normalized_steps.append({
                "step_id": sid,
                "name": name,
                "tool": tool,
                "capability": capability,
                "arguments": args,
                "expected_output": s.get("expected_output", {}) if isinstance(s.get("expected_output"), dict) else {},
                "side_effect": str(s.get("side_effect") or SideEffect.UNKNOWN.value),
                "timeout_seconds": float(s.get("timeout_seconds", 30.0)),
                "retry_policy": s.get("retry_policy") if isinstance(s.get("retry_policy"), dict) else {"max_attempts": 3, "backoff": 1.0},
                "idempotent": bool(s.get("idempotent", False)),
                "depends_on": [str(d).strip() for d in deps if str(d).strip()],
                "success_condition": str(s.get("success_condition") or ""),
                "failure_condition": str(s.get("failure_condition") or ""),
                "recovery_strategy": str(s.get("recovery_strategy") or ""),
                "verification_required": bool(s.get("verification_required", False)),
            })

        return {
            "goal": str(data.get("goal", "")),
            "steps": normalized_steps,
            "metadata": data.get("metadata", {}) if isinstance(data.get("metadata"), dict) else {},
        }

    @classmethod
    def format_tool_catalogue(cls, tools_catalogue: Optional[List[Dict[str, Any]]]) -> str:
        """Formats tools catalogue with parameter schemas, risk, and side-effects for planner prompt."""
        if not tools_catalogue:
            return "No specific pre-registered tools provided."
        cat_lines = []
        for t in tools_catalogue:
            name = t.get("name", "")
            cap = t.get("capability", "")
            desc = t.get("description", "")
            risk = t.get("risk", "")
            side_effect = t.get("side_effect", "")
            verification_supported = t.get("verification_supported", False)
            params = t.get("parameters", {})

            props = {}
            req = []
            if isinstance(params, dict):
                if "properties" in params and isinstance(params["properties"], dict):
                    props = params["properties"]
                    req = params.get("required", [])
                elif "type" not in params:
                    props = params

            param_descs = []
            for p_name, p_info in props.items():
                p_type = p_info.get("type", "any") if isinstance(p_info, dict) else "any"
                is_req = "required" if p_name in req else "optional"
                p_desc = p_info.get("description", "") if isinstance(p_info, dict) else ""
                p_str = f"{p_name} ({p_type}, {is_req})"
                if p_desc:
                    p_str += f": {p_desc}"
                param_descs.append(p_str)

            meta_parts = []
            if cap:
                meta_parts.append(f"capability: '{cap}'")
            if risk:
                meta_parts.append(f"risk: {risk}")
            if side_effect:
                meta_parts.append(f"side_effect: {side_effect}")
            if verification_supported:
                meta_parts.append("supports_verification: true")
            meta_str = f" ({', '.join(meta_parts)})" if meta_parts else ""

            line = f"- Tool '{name}'{meta_str}: {desc}"
            if param_descs:
                line += f"\n  Parameters: {'; '.join(param_descs)}"
            cat_lines.append(line)
        return "\n".join(cat_lines)

    def build_plan_prompt(
        self, goal: str, tools_catalogue: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        """
        Builds the prompt instructing the LLM to decompose a natural-language goal.
        """
        tools_str = self.format_tool_catalogue(tools_catalogue)

        return f"""You are an autonomous compound task planner for AURA 2.0.
Your mission is to decompose the user's high-level goal into a deterministic, multi-step execution plan, or request clarification if the goal is ambiguous.

USER GOAL:
{goal}

AVAILABLE CAPABILITIES / TOOLS IN INVENTORY:
{tools_str}

CRITICAL RULES:
1. Return ONLY a valid JSON object matching the JSON schema below. No conversational prose or reasoning outside the JSON.
2. AMBIGUOUS GOALS: If the user request is ambiguous, underspecified, or missing critical targets (e.g. 'Open the app' without naming the app, 'Send message' without recipient, 'Delete files' without target path), you MUST NOT guess or hallucinate targets. Instead, return a clarification request:
{{
  "status": "NEEDS_CLARIFICATION",
  "questions": [
    "Which application would you like me to open?"
  ]
}}
3. If the goal is clear, return a plan with status "PLANNED":
   - Use existing tools from the inventory whenever suitable. Use exact tool names and exact parameter names.
   - Respect required parameters and parameter types. Do NOT invent parameter names.
   - If a required capability does NOT exist in the inventory, you may propose a clean, domain-specific capability identifier (e.g. "math.fibonacci"). The system will autonomously synthesize missing capabilities at runtime.
4. Parameter Propagation: If a step depends on the output of an earlier step, use deterministic parameter reference templates:
   - Exact typed substitution: "${{step_0.output}}" or "${{step_0.field_name}}"
   - String interpolation: "path=${{step_0.filepath}}"
5. FORWARD REFERENCES ARE STRICTLY FORBIDDEN: A step can only reference steps that precede it.
6. Cyclical dependencies and self-dependencies are strictly forbidden.
7. Maximum allowable steps is {MAX_PLAN_STEPS}. Keep the plan concise and direct.
8. Loops, conditionals (if/else), and dynamic branches are NOT supported. Produce a finite static step DAG.
9. Side-effects and Risk: You are proposing a plan, not authorizing execution. You cannot mark a mutating or dangerous tool as READ_ONLY.
10. Verification: When the user goal explicitly requests verification (e.g. 'confirm', 'verify', 'check that it...'), you MUST set "verification_required": true on the relevant step or include a verification step.

PLAN JSON SCHEMA:
{{
  "status": "PLANNED",
  "goal": "{goal}",
  "steps": [
    {{
      "step_id": "step_0",
      "name": "descriptive_name",
      "tool": "tool_name",
      "capability": "capability_name",
      "arguments": {{}},
      "depends_on": [],
      "timeout_seconds": 30.0,
      "retry_policy": {{"max_attempts": 3, "backoff": 1.0}},
      "idempotent": false,
      "side_effect": "READ_ONLY",
      "verification_required": false
    }}
  ]
}}
"""

    def build_repair_prompt(
        self,
        goal: str,
        rejected_plan: Dict[str, Any],
        failure_reason: str,
        tools_catalogue: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """
        Builds a targeted repair prompt requesting a corrected plan.
        Includes original user goal, the rejected plan JSON, and the exact deterministic failure reason.
        """
        tools_str = self.format_tool_catalogue(tools_catalogue)
        plan_str = json.dumps(rejected_plan, indent=2, ensure_ascii=False)

        return f"""You are an autonomous compound task planner for AURA 2.0.
Your previously proposed execution plan was REJECTED by deterministic validation.

ORIGINAL USER GOAL:
{goal}

AVAILABLE CAPABILITIES / TOOLS IN INVENTORY:
{tools_str}

REJECTED PLAN:
{plan_str}

DETERMINISTIC VALIDATION / GROUNDING ERROR:
{failure_reason}

REPAIR INSTRUCTIONS:
1. Fix the identified error while strictly preserving the user's intended computation:
   - If a step consumes the result of a previous step, you MUST use parameter references (e.g. "${{step_0.output}}") in its arguments.
   - Do NOT pass literal values when the user goal requires operating on the result of an earlier step.
   - Any step referencing "${{step_X.output}}" MUST explicitly include "step_X" in its "depends_on" list.
   - Do NOT reference steps that are not in "depends_on".
   - Ensure all arguments use exact parameter names and types from the tool inventory. Do not pass unknown parameters.
   - Ensure all required parameters are provided.
   - If the request is fundamentally ambiguous and lacks required targets, return a NEEDS_CLARIFICATION response.
2. Return ONLY a valid JSON object matching the JSON schema. No conversational text or markdown explanation outside the JSON.

PLAN JSON SCHEMA:
{{
  "status": "PLANNED",
  "goal": "{goal}",
  "steps": [
    {{
      "step_id": "step_0",
      "name": "descriptive_name",
      "tool": "tool_name",
      "capability": "capability_name",
      "arguments": {{}},
      "depends_on": [],
      "timeout_seconds": 30.0,
      "retry_policy": {{"max_attempts": 3, "backoff": 1.0}},
      "idempotent": false,
      "side_effect": "READ_ONLY",
      "verification_required": false
    }}
  ]
}}
"""

    def plan_from_goal(
        self,
        goal: str,
        llm: Any,
        tools_catalogue: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TaskPlan:
        """
        Decomposes a natural language goal into a validated TaskPlan using an LLM.
        Includes deterministic structural validation, semantic grounding validation,
        and bounded plan repair (up to 2 repair attempts).
        Raises ValueError if planning, parsing, or validation fails after repairs.
        """
        clean_goal = (goal or "").strip()
        if not clean_goal:
            raise ValueError("Cannot plan for an empty goal")

        max_repairs = 2
        last_error = ""
        rejected_plan: Dict[str, Any] = {}

        for attempt in range(max_repairs + 1):
            if attempt == 0:
                prompt = self.build_plan_prompt(clean_goal, tools_catalogue)
            else:
                logger.info(
                    "Retrying plan generation (attempt %d/%d) after validation error: %s",
                    attempt,
                    max_repairs,
                    last_error,
                )
                prompt = self.build_repair_prompt(
                    goal=clean_goal,
                    rejected_plan=rejected_plan,
                    failure_reason=last_error,
                    tools_catalogue=tools_catalogue,
                )

            try:
                raw_response = llm.generate(prompt)
            except Exception as llm_err:
                raise ValueError(f"Planning failed: LLM generation error: {llm_err}") from llm_err

            try:
                parsed = self.parse_plan_response(raw_response)
            except ValueError as parse_err:
                last_error = f"JSON parsing failed: {parse_err}"
                rejected_plan = {"raw_output": raw_response[:500]}
                continue

            # Handle clarification response directly
            if parsed.get("status") == "NEEDS_CLARIFICATION":
                return TaskPlan(
                    goal=clean_goal,
                    steps=[],
                    metadata=metadata or parsed.get("metadata", {}),
                    status="NEEDS_CLARIFICATION",
                    questions=parsed.get("questions", []),
                )

            raw_steps = parsed.get("steps", [])
            candidate_plan = TaskPlan(
                goal=clean_goal,
                steps=raw_steps,
                metadata=metadata or parsed.get("metadata", {}),
                status="PLANNED",
            )

            # 1. Structural and parameter schema validation on plan-local IDs (step_0, step_1, ...)
            try:
                self.validate_plan(candidate_plan, tools_catalogue=tools_catalogue)
            except ValueError as val_err:
                last_error = str(val_err)
                rejected_plan = parsed
                continue

            # 2. Semantic grounding validation
            try:
                self.validate_grounding(candidate_plan, clean_goal, tools_catalogue)
            except ValueError as ground_err:
                last_error = str(ground_err)
                rejected_plan = parsed
                continue

            # Both structural and grounding validation passed!
            # Remap plan-local step IDs (step_0, step_1, ...) to globally unique IDs.
            id_remap: Dict[str, str] = {}
            for idx, s in enumerate(raw_steps):
                local_id = s.get("step_id", f"step_{idx}")
                id_remap[local_id] = new_step_id()

            def _remap_refs(val: Any) -> Any:
                """Recursively remap ${old_id.x} -> ${new_id.x} in argument values."""
                if isinstance(val, str):
                    for old, new in id_remap.items():
                        val = val.replace(f"${{{old}.", f"${{{new}.")
                    return val
                if isinstance(val, dict):
                    return {k: _remap_refs(v) for k, v in val.items()}
                if isinstance(val, list):
                    return [_remap_refs(item) for item in val]
                return val

            for idx, s in enumerate(raw_steps):
                local_id = s.get("step_id", f"step_{idx}")
                s["step_id"] = id_remap[local_id]
                s["depends_on"] = [id_remap.get(d, d) for d in (s.get("depends_on") or [])]
                if "arguments" in s:
                    s["arguments"] = _remap_refs(s["arguments"])

            final_plan = TaskPlan(
                goal=clean_goal,
                steps=raw_steps,
                metadata=candidate_plan.metadata,
                status="PLANNED",
            )

            # Final validation on remapped IDs to ensure integrity after remapping
            self.validate_plan(final_plan, tools_catalogue=tools_catalogue)
            return final_plan

        raise ValueError(
            f"Planning failed: plan rejected after {max_repairs} repair attempts. Last error: {last_error}"
        )

    def plan_sequence(
        self,
        goal: str,
        step_definitions: List[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TaskPlan:
        """
        Builds a structured plan ensuring explicit step IDs, dependencies, and policies.
        Validates the generated DAG for cycle-freedom and reference integrity.
        """
        planned_steps = []
        for idx, s in enumerate(step_definitions):
            step_id = s.get("step_id") or new_step_id()
            depends_on = s.get("depends_on", [])
            if idx > 0 and not depends_on and s.get("sequential", True):
                depends_on = [planned_steps[idx - 1]["step_id"]]

            planned_steps.append({
                "step_id": step_id,
                "name": s.get("name", f"step_{idx + 1}"),
                "tool": s.get("tool", ""),
                "capability": s.get("capability", ""),
                "arguments": s.get("arguments", {}),
                "expected_output": s.get("expected_output", {}),
                "side_effect": s.get("side_effect", SideEffect.UNKNOWN.value),
                "timeout_seconds": float(s.get("timeout_seconds", 30.0)),
                "retry_policy": s.get("retry_policy", {"max_attempts": 3, "backoff": 1.0}),
                "idempotent": bool(s.get("idempotent", False)),
                "depends_on": depends_on,
                "success_condition": s.get("success_condition", ""),
                "failure_condition": s.get("failure_condition", ""),
                "recovery_strategy": s.get("recovery_strategy", ""),
                "verification_required": bool(s.get("verification_required", False)),
            })

        plan = TaskPlan(goal=goal, steps=planned_steps, metadata=metadata or {})
        self.validate_plan(plan)
        return plan


@dataclass
class TaskWorker:
    task_id: str
    thread: threading.Thread
    start_time: float = field(default_factory=time.time)
    error: Optional[str] = None


class TaskWorkerManager:
    """
    Manages active task execution workers and provides a top-level exception guard.
    Prevents thread leakage, tracks worker lifecycle, and records thread panics
    to SQLite to guarantee task state transitions out of RUNNING on crash.
    """

    def __init__(self, runtime: "TaskRuntime"):
        self.runtime = runtime
        self._workers: Dict[str, TaskWorker] = {}
        self._lock = threading.RLock()

    def submit(
        self,
        task_id: str,
        executor: ToolExecutor,
        interactive: Optional[bool] = None,
    ) -> threading.Thread:
        with self._lock:
            if task_id in self._workers and self._workers[task_id].thread.is_alive():
                logger.warning("Task %s worker is already running", task_id)
                return self._workers[task_id].thread

            def _guarded_worker():
                try:
                    self.runtime.execute_compound_task(task_id, executor, interactive=interactive)
                except Exception as ex:
                    import traceback
                    tb = traceback.format_exc()
                    logger.error("Unhandled exception in task worker %s:\n%s", task_id, tb)
                    with self._lock:
                        if task_id in self._workers:
                            self._workers[task_id].error = str(ex)
                    try:
                        self.runtime.update_task_status(
                            task_id,
                            TaskStatus.FAILED.value,
                            error=f"Worker panic: {ex}",
                            recovery_state="PANIC_RECOVERING",
                        )
                        self.runtime._emit_task_event(
                            task_id,
                            "TASK_FAILED",
                            status=TaskStatus.FAILED.value,
                            detail=f"Worker panic: {ex}",
                        )
                    except Exception as db_err:
                        logger.critical("Failed to record worker panic to database for task %s: %s", task_id, db_err)
                finally:
                    with self._lock:
                        self._workers.pop(task_id, None)

            t = threading.Thread(
                target=_guarded_worker,
                name=f"task_worker_{task_id}",
                daemon=True,
            )
            self._workers[task_id] = TaskWorker(task_id=task_id, thread=t)
            t.start()
            return t

    def is_running(self, task_id: str) -> bool:
        with self._lock:
            worker = self._workers.get(task_id)
            return bool(worker and worker.thread.is_alive())

    def wait_for_task(self, task_id: str, timeout: float = 5.0) -> bool:
        with self._lock:
            worker = self._workers.get(task_id)
        if not worker:
            return True
        worker.thread.join(timeout=timeout)
        return not worker.thread.is_alive()

    def get_running_tasks(self) -> List[str]:
        with self._lock:
            return [tid for tid, w in self._workers.items() if w.thread.is_alive()]

    def stop_worker(self, task_id: str) -> bool:
        return self.runtime.cancel_task(task_id, reason="Worker stopped by manager")


class TaskRuntime:
    """
    Manages durable tasks and compound execution backed by SQLite.
    Survives HTTP/WebSocket disconnects, server restarts, and process crashes.
    """

    def __init__(
        self,
        session_factory=None,
        bus=None,
        synthesis_engine=None,
        synthesis_policy=None,
        gap_engine=None,
        interactive_confirmation: bool = False,
    ):
        self._session_factory = session_factory or SessionLocal
        self.bus = bus
        self.planner = CompoundTaskPlanner()
        self.workers = TaskWorkerManager(self)
        self.synthesis_engine = synthesis_engine
        self.synthesis_policy = synthesis_policy
        self.gap_engine = gap_engine
        self.interactive_confirmation = interactive_confirmation
        init_task_tables()

    def _emit_task_event(
        self,
        task_id: str,
        event_type: Any,
        step_id: str = "",
        status: str = "",
        detail: str = "",
        **extra: Any,
    ) -> None:
        if self.bus is not None:
            try:
                from events.types import TaskLifecycleEvent
                if not detail and extra:
                    detail = json.dumps(extra, ensure_ascii=False)
                self.bus.publish(
                    TaskLifecycleEvent(
                        task_id=task_id,
                        event_type=event_type,
                        step_id=step_id,
                        status=status,
                        detail=detail,
                        timestamp=time.time(),
                    )
                )
            except Exception as e:
                logger.debug("Task event publication skipped: %s", e)

    def create_task(
        self,
        goal: str = "",
        session_id: str = "default",
        metadata: Optional[Dict[str, Any]] = None,
        task_id: Optional[str] = None,
        intent: Optional[str] = None,
        steps: Optional[List[Dict[str, Any]]] = None,
        llm: Optional[Any] = None,
        tools_catalogue: Optional[List[Dict[str, Any]]] = None,
    ) -> DurableTask:
        t_id = task_id or new_task_id()
        effective_goal = goal or intent or ""
        now = timestamp_now()

        if not steps and llm is not None and effective_goal:
            plan_obj = self.planner.plan_from_goal(
                goal=effective_goal,
                llm=llm,
                tools_catalogue=tools_catalogue,
                metadata=metadata,
            )
            steps = plan_obj.steps
            metadata = plan_obj.metadata

        task = DurableTask(
            task_id=t_id,
            session_id=session_id,
            goal=effective_goal,
            status=TaskStatus.PENDING.value,
            metadata=metadata or {},
            created_at=now,
            updated_at=now,
        )

        with db_lock:
            with self._session_factory() as session:
                record = DurableTaskRecord(
                    task_id=task.task_id,
                    session_id=task.session_id,
                    goal=task.goal,
                    status=task.status,
                    plan_json=json.dumps(task.plan, ensure_ascii=False),
                    current_step_id=task.current_step_id,
                    attempt=task.attempt,
                    last_error=task.last_error,
                    recovery_state=task.recovery_state,
                    metadata_json=json.dumps(task.metadata, ensure_ascii=False),
                    created_at=task.created_at,
                    updated_at=task.updated_at,
                )
                session.add(record)
                session.commit()

        if steps:
            for idx, s in enumerate(steps):
                self.add_step(
                    task_id=task.task_id,
                    name=s.get("name", s.get("tool", f"step_{idx + 1}")),
                    tool=s.get("tool", ""),
                    arguments=s.get("arguments", {}),
                    side_effect=s.get("side_effect", SideEffect.UNKNOWN.value),
                    step_id=s.get("step_id"),
                    step_index=idx,
                    capability=s.get("capability", ""),
                    timeout_seconds=float(s.get("timeout_seconds", 30.0)),
                    retry_policy=s.get("retry_policy"),
                    idempotent=s.get("idempotent", False),
                    depends_on=s.get("depends_on"),
                    verification_required=s.get("verification_required", False),
                )

        logger.info("Created durable task %s: %s", t_id, effective_goal)
        return self.get_task(t_id) or task

    def create_from_plan(
        self, plan: TaskPlan, session_id: str = "default"
    ) -> DurableTask:
        """Create a durable task from a verified compound TaskPlan."""
        self.planner.validate_plan(plan)
        return self.create_task(
            goal=plan.goal,
            session_id=session_id,
            metadata=plan.metadata,
            steps=plan.steps,
        )

    def get_task(self, task_id: str) -> Optional[DurableTask]:
        with db_lock:
            with self._session_factory() as session:
                record = session.get(DurableTaskRecord, task_id)
                if not record:
                    return None
                task = DurableTask(
                    task_id=record.task_id,
                    session_id=record.session_id,
                    goal=record.goal,
                    status=record.status,
                    plan=json.loads(record.plan_json or "{}"),
                    current_step_id=record.current_step_id,
                    attempt=record.attempt,
                    last_error=record.last_error,
                    recovery_state=record.recovery_state,
                    metadata=json.loads(record.metadata_json or "{}"),
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                )
        task.steps = self.list_steps(task_id)
        conf = self.get_pending_confirmation_for_task(task_id)
        if conf:
            task.confirmation = conf.to_dict()
        clar = self.get_pending_clarification_for_task(task_id)
        if clar:
            task.clarification = clar.to_dict()
        return task

    def list_active_tasks(self) -> List[DurableTask]:
        """List tasks that are active, running, pending or recovering."""
        active_statuses = (
            TaskStatus.CREATED.value,
            TaskStatus.PENDING.value,
            TaskStatus.PLANNING.value,
            TaskStatus.READY.value,
            TaskStatus.RUNNING.value,
            TaskStatus.WAITING.value,
            TaskStatus.VERIFYING.value,
            TaskStatus.RECOVERING.value,
        )
        with db_lock:
            with self._session_factory() as session:
                records = (
                    session.query(DurableTaskRecord)
                    .filter(DurableTaskRecord.status.in_(active_statuses))
                    .all()
                )
                task_ids = [r.task_id for r in records]

        return [t for tid in task_ids if (t := self.get_task(tid)) is not None]

    def update_task_status(
        self,
        task_id: str,
        status: str,
        error: str = "",
        recovery_state: str = "",
        current_step_id: Optional[str] = None,
    ) -> Optional[DurableTask]:
        with db_lock:
            with self._session_factory() as session:
                record = session.get(DurableTaskRecord, task_id)
                if not record:
                    return None
                record.status = status
                if error:
                    record.last_error = error
                if recovery_state:
                    record.recovery_state = recovery_state
                if current_step_id is not None:
                    record.current_step_id = current_step_id
                record.updated_at = timestamp_now()
                session.commit()

        return self.get_task(task_id)

    def add_step(
        self,
        task_id: str,
        name: str,
        tool: str,
        arguments: Optional[Dict[str, Any]] = None,
        side_effect: str = SideEffect.UNKNOWN.value,
        step_index: Optional[int] = None,
        step_id: Optional[str] = None,
        capability: str = "",
        expected_output: Optional[Dict[str, Any]] = None,
        timeout_seconds: float = 30.0,
        retry_policy: Optional[Dict[str, Any]] = None,
        idempotent: bool = False,
        depends_on: Optional[List[str]] = None,
        success_condition: str = "",
        failure_condition: str = "",
        recovery_strategy: str = "",
        verification_required: bool = False,
    ) -> DurableStep:
        s_id = step_id or new_step_id()
        args = arguments or {}
        policy = retry_policy or {"max_attempts": 3, "backoff": 1.0}
        deps = depends_on or []

        # Store extended fields into arguments_json or metadata envelope if schema requires
        payload_args = dict(args)
        payload_meta = {
            "capability": capability,
            "expected_output": expected_output or {},
            "timeout_seconds": timeout_seconds,
            "retry_policy": policy,
            "idempotent": idempotent,
            "depends_on": deps,
            "success_condition": success_condition,
            "failure_condition": failure_condition,
            "recovery_strategy": recovery_strategy,
            "verification_required": verification_required,
        }
        full_args_record = {"args": payload_args, "meta": payload_meta}

        with db_lock:
            with self._session_factory() as session:
                if step_index is None:
                    existing = (
                        session.query(DurableStepRecord)
                        .filter_by(task_id=task_id)
                        .count()
                    )
                    idx = existing
                else:
                    idx = step_index

                now = timestamp_now()
                record = DurableStepRecord(
                    step_id=s_id,
                    task_id=task_id,
                    step_index=idx,
                    name=name,
                    tool=tool,
                    arguments_json=json.dumps(full_args_record, ensure_ascii=False),
                    status=StepStatus.PENDING.value,
                    side_effect=side_effect,
                    attempt=0,
                    result_json="{}",
                    evidence_json="[]",
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
                session.commit()

        return DurableStep(
            step_id=s_id,
            task_id=task_id,
            step_index=idx,
            name=name,
            tool=tool,
            capability=capability,
            arguments=args,
            expected_output=expected_output or {},
            status=StepStatus.PENDING.value,
            side_effect=side_effect,
            timeout_seconds=timeout_seconds,
            retry_policy=policy,
            idempotent=idempotent,
            depends_on=deps,
            success_condition=success_condition,
            failure_condition=failure_condition,
            recovery_strategy=recovery_strategy,
            verification_required=verification_required,
            attempt=0,
            result={},
            evidence=[],
            created_at=now,
            updated_at=now,
        )

    def get_step(self, step_id: str) -> Optional[DurableStep]:
        with db_lock:
            with self._session_factory() as session:
                record = session.get(DurableStepRecord, step_id)
                if not record:
                    return None
                
                raw_args = json.loads(record.arguments_json or "{}")
                if isinstance(raw_args, dict) and "args" in raw_args and "meta" in raw_args:
                    args = raw_args.get("args", {})
                    meta = raw_args.get("meta", {})
                else:
                    args = raw_args if isinstance(raw_args, dict) else {}
                    meta = {}

                return DurableStep(
                    step_id=record.step_id,
                    task_id=record.task_id,
                    step_index=record.step_index,
                    name=record.name,
                    tool=record.tool,
                    capability=meta.get("capability", ""),
                    arguments=args,
                    expected_output=meta.get("expected_output", {}),
                    status=record.status,
                    side_effect=record.side_effect,
                    timeout_seconds=float(meta.get("timeout_seconds", 30.0)),
                    retry_policy=meta.get("retry_policy", {"max_attempts": 3, "backoff": 1.0}),
                    idempotent=bool(meta.get("idempotent", False)),
                    depends_on=list(meta.get("depends_on", [])),
                    success_condition=meta.get("success_condition", ""),
                    failure_condition=meta.get("failure_condition", ""),
                    recovery_strategy=meta.get("recovery_strategy", ""),
                    verification_required=bool(meta.get("verification_required", False)),
                    attempt=record.attempt,
                    result=json.loads(record.result_json or "{}"),
                    evidence=json.loads(record.evidence_json or "[]"),
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                )

    def list_steps(self, task_id: str) -> List[DurableStep]:
        with db_lock:
            with self._session_factory() as session:
                records = (
                    session.query(DurableStepRecord)
                    .filter_by(task_id=task_id)
                    .order_by(DurableStepRecord.step_index.asc())
                    .all()
                )
                step_ids = [r.step_id for r in records]

        return [s for sid in step_ids if (s := self.get_step(sid)) is not None]

    def update_step(
        self,
        step_id: str,
        status: str,
        result: Optional[Dict[str, Any]] = None,
        evidence: Optional[List[Dict[str, Any]]] = None,
        attempt: Optional[int] = None,
    ) -> Optional[DurableStep]:
        with db_lock:
            with self._session_factory() as session:
                record = session.get(DurableStepRecord, step_id)
                if not record:
                    return None
                record.status = status
                if result is not None:
                    record.result_json = json.dumps(result, ensure_ascii=False)
                if evidence is not None:
                    record.evidence_json = json.dumps(evidence, ensure_ascii=False)
                if attempt is not None:
                    record.attempt = attempt
                record.updated_at = timestamp_now()
                session.commit()

        return self.get_step(step_id)

    def get_next_step(self, task_id: str) -> Optional[DurableStep]:
        """Returns the next pending step for a task whose dependencies are satisfied."""
        task = self.get_task(task_id)
        if not task or TaskStatus(task.status).is_terminal:
            return None

        steps = self.list_steps(task_id)
        for step in steps:
            if step.status == StepStatus.PENDING.value:
                if step.depends_on:
                    all_deps_ok = True
                    for dep_id in step.depends_on:
                        dep = self.get_step(dep_id)
                        if not dep or dep.status != StepStatus.COMPLETED.value:
                            all_deps_ok = False
                            break
                    if not all_deps_ok:
                        continue
                return step
        return None

    def record_step_result(
        self,
        task_id: str,
        step_id: str,
        result: Any,
    ) -> Optional[DurableStep]:
        """Records a step execution result and advances task lifecycle."""
        task = self.get_task(task_id)
        if not task:
            raise ValueError(f"Unknown task: {task_id}")

        step = self.get_step(step_id)
        if not step:
            raise ValueError(f"Unknown step: {step_id}")

        res_ok = getattr(result, "ok", False)
        res_output = getattr(result, "output", str(result))
        res_error = getattr(result, "error", "")
        res_status = getattr(result, "status", ToolStatus.SUCCESS.value if res_ok else ToolStatus.FAILED.value)
        res_error_code = getattr(result, "error_code", "")
        res_data = getattr(result, "data", {})

        res_dict = {
            "ok": res_ok,
            "status": res_status,
            "output": res_output,
            "data": res_data if isinstance(res_data, dict) else {},
            "error": res_error,
            "error_code": res_error_code,
        }
        ev_list = [
            {
                "kind": e.kind.value if hasattr(e.kind, "value") else str(e.kind),
                "verified": e.verified,
                "reference": e.reference,
                "detail": e.detail,
            }
            for e in getattr(result, "evidence", ())
        ]

        if res_ok:
            new_step_status = StepStatus.COMPLETED.value
        else:
            is_timeout = res_status == ToolStatus.TIMEOUT.value
            se_val = getattr(result, "side_effect", None) or step.side_effect or SideEffect.UNKNOWN.value
            if isinstance(se_val, str) and se_val.startswith("SideEffect."):
                se_val = se_val.split(".", 1)[1]
            se_enum = (
                SideEffect(se_val)
                if se_val in SideEffect._value2member_map_
                else SideEffect.UNKNOWN
            )
            if is_timeout and not se_enum.repeatable:
                new_step_status = StepStatus.UNKNOWN.value
            else:
                new_step_status = StepStatus.FAILED.value

        updated_step = self.update_step(
            step_id,
            status=new_step_status,
            result=res_dict,
            evidence=ev_list,
        )

        all_steps = self.list_steps(task_id)
        if new_step_status == StepStatus.UNKNOWN.value:
            self.update_task_status(
                task_id,
                TaskStatus.UNKNOWN.value,
                error=f"Step '{step.name}' timed out with status UNKNOWN.",
                current_step_id=step_id,
            )
        elif new_step_status == StepStatus.FAILED.value:
            self.update_task_status(
                task_id,
                TaskStatus.FAILED.value,
                error=f"Step '{step.name}' failed: {res_error}",
                current_step_id=step_id,
            )
        elif all(s.status == StepStatus.COMPLETED.value for s in all_steps):
            self.update_task_status(task_id, TaskStatus.COMPLETED.value)
            self._record_task_experience(task_id, "SUCCESS")
        else:
            self.update_task_status(task_id, TaskStatus.RUNNING.value, current_step_id=step_id)

        return updated_step

    def _record_task_experience(self, task_id: str, outcome: str = "SUCCESS") -> None:
        """Records task execution experience with PII screening for self-learning."""
        try:
            from learning.experience import AuraExperienceStore
            task = self.get_task(task_id)
            if not task:
                return
            steps = self.get_steps(task_id)
            tools = [s.tool for s in steps if s.tool]
            evidence_ids = [s.evidence_id for s in steps if s.evidence_id]
            store = AuraExperienceStore()
            store.record_experience(
                session_id=task.session_id or f"task_session_{task_id}",
                input_text=task.goal,
                model_decision="TOOL_CALL" if tools else "ANSWER",
                task_id=task_id,
                selected_tool=tools[0] if tools else "",
                final_response=f"Task {task_id} completed with {len(steps)} steps.",
                outcome=outcome,
                category="agent_task",
            )
        except Exception as e:
            logger.debug("Task experience recording ignored: %s", e)

    def checkpoint(
        self, task_id: str, plan: Dict[str, Any], current_step_id: str = ""
    ) -> Optional[DurableTask]:
        with db_lock:
            with self._session_factory() as session:
                record = session.get(DurableTaskRecord, task_id)
                if not record:
                    return None
                record.plan_json = json.dumps(plan, ensure_ascii=False)
                if current_step_id:
                    record.current_step_id = current_step_id
                record.updated_at = timestamp_now()
                session.commit()

        return self.get_task(task_id)

    # ------------------------------------------------------------------
    # Confirmation & Clarification (Human-in-the-loop, Phase 5B.7)
    # ------------------------------------------------------------------

    def create_confirmation_request(
        self,
        task_id: str,
        step_id: str,
        tool: str,
        risk: str = "dangerous",
        side_effect: str = "mutating",
        description: str = "",
        arguments: Optional[Dict[str, Any]] = None,
        expires_in_seconds: float = 3600.0,
    ) -> DurableConfirmation:
        arguments = arguments or {}
        cid = new_confirmation_id()
        now = timestamp_now()
        redacted = redact_arguments(arguments)
        fp = compute_invocation_fingerprint(task_id, step_id, tool, arguments)

        with db_lock:
            with self._session_factory() as session:
                record = DurableConfirmationRecord(
                    confirmation_id=cid,
                    task_id=task_id,
                    step_id=step_id,
                    tool=tool,
                    risk=risk,
                    side_effect=side_effect,
                    description=description,
                    arguments_json=json.dumps(arguments, sort_keys=True, ensure_ascii=False),
                    redacted_arguments_json=json.dumps(redacted, sort_keys=True, ensure_ascii=False),
                    status="PENDING",
                    created_at=now,
                    expires_at="",
                    fingerprint=fp,
                )
                session.add(record)
                session.commit()

        conf = DurableConfirmation(
            confirmation_id=cid,
            task_id=task_id,
            step_id=step_id,
            tool=tool,
            risk=risk,
            side_effect=side_effect,
            description=description,
            arguments=arguments,
            redacted_arguments=redacted,
            status="PENDING",
            created_at=now,
            fingerprint=fp,
        )
        self._emit_task_event(
            task_id,
            "TASK_CONFIRMATION_REQUESTED",
            step_id=step_id,
            confirmation_id=cid,
            tool=tool,
            risk=risk,
        )
        return conf

    def get_confirmation(self, confirmation_id: str) -> Optional[DurableConfirmation]:
        with db_lock:
            with self._session_factory() as session:
                rec = session.get(DurableConfirmationRecord, confirmation_id)
                if not rec:
                    return None
                return DurableConfirmation(
                    confirmation_id=rec.confirmation_id,
                    task_id=rec.task_id,
                    step_id=rec.step_id,
                    tool=rec.tool,
                    risk=rec.risk,
                    side_effect=rec.side_effect,
                    description=rec.description,
                    arguments=json.loads(rec.arguments_json or "{}"),
                    redacted_arguments=json.loads(rec.redacted_arguments_json or "{}"),
                    status=rec.status,
                    decision=rec.decision,
                    decision_by=rec.decision_by,
                    decided_at=rec.decided_at,
                    created_at=rec.created_at,
                    expires_at=rec.expires_at,
                    fingerprint=getattr(rec, "fingerprint", "") or "",
                )

    def get_confirmation_for_step(self, step_id: str) -> Optional[DurableConfirmation]:
        with db_lock:
            with self._session_factory() as session:
                rec = (
                    session.query(DurableConfirmationRecord)
                    .filter_by(step_id=step_id)
                    .order_by(DurableConfirmationRecord.created_at.desc())
                    .first()
                )
                if not rec:
                    return None
                return DurableConfirmation(
                    confirmation_id=rec.confirmation_id,
                    task_id=rec.task_id,
                    step_id=rec.step_id,
                    tool=rec.tool,
                    risk=rec.risk,
                    side_effect=rec.side_effect,
                    description=rec.description,
                    arguments=json.loads(rec.arguments_json or "{}"),
                    redacted_arguments=json.loads(rec.redacted_arguments_json or "{}"),
                    status=rec.status,
                    decision=rec.decision,
                    decision_by=rec.decision_by,
                    decided_at=rec.decided_at,
                    created_at=rec.created_at,
                    expires_at=rec.expires_at,
                    fingerprint=getattr(rec, "fingerprint", "") or "",
                )

    def get_pending_confirmation_for_task(self, task_id: str) -> Optional[DurableConfirmation]:
        with db_lock:
            with self._session_factory() as session:
                rec = (
                    session.query(DurableConfirmationRecord)
                    .filter_by(task_id=task_id, status="PENDING")
                    .order_by(DurableConfirmationRecord.created_at.desc())
                    .first()
                )
                if not rec:
                    return None
                return DurableConfirmation(
                    confirmation_id=rec.confirmation_id,
                    task_id=rec.task_id,
                    step_id=rec.step_id,
                    tool=rec.tool,
                    risk=rec.risk,
                    side_effect=rec.side_effect,
                    description=rec.description,
                    arguments=json.loads(rec.arguments_json or "{}"),
                    redacted_arguments=json.loads(rec.redacted_arguments_json or "{}"),
                    status=rec.status,
                    decision=rec.decision,
                    decision_by=rec.decision_by,
                    decided_at=rec.decided_at,
                    created_at=rec.created_at,
                    expires_at=rec.expires_at,
                    fingerprint=getattr(rec, "fingerprint", "") or "",
                )

    def list_confirmations_for_task(self, task_id: str) -> List[DurableConfirmation]:
        with db_lock:
            with self._session_factory() as session:
                recs = (
                    session.query(DurableConfirmationRecord)
                    .filter_by(task_id=task_id)
                    .order_by(DurableConfirmationRecord.created_at.asc())
                    .all()
                )
                return [
                    DurableConfirmation(
                        confirmation_id=r.confirmation_id,
                        task_id=r.task_id,
                        step_id=r.step_id,
                        tool=r.tool,
                        risk=r.risk,
                        side_effect=r.side_effect,
                        description=r.description,
                        arguments=json.loads(r.arguments_json or "{}"),
                        redacted_arguments=json.loads(r.redacted_arguments_json or "{}"),
                        status=r.status,
                        decision=r.decision,
                        decision_by=r.decision_by,
                        decided_at=r.decided_at,
                        created_at=r.created_at,
                        expires_at=r.expires_at,
                        fingerprint=getattr(r, "fingerprint", "") or "",
                    )
                    for r in recs
                ]

    def resolve_confirmation(
        self,
        task_id: str,
        decision: str,
        confirmation_id: Optional[str] = None,
        decision_by: str = "human",
        reason: Optional[str] = None,
    ) -> DurableConfirmation:
        now = timestamp_now()
        decision_upper = decision.strip().upper()
        if decision_upper not in ("APPROVED", "REJECTED"):
            raise ValueError(f"Invalid decision: {decision}. Must be APPROVED or REJECTED.")

        with db_lock:
            with self._session_factory() as session:
                if confirmation_id:
                    target_cid = confirmation_id
                else:
                    rec = (
                        session.query(DurableConfirmationRecord)
                        .filter_by(task_id=task_id)
                        .order_by(DurableConfirmationRecord.created_at.desc())
                        .first()
                    )
                    if not rec:
                        raise ValueError(f"No confirmation found for task {task_id}")
                    target_cid = rec.confirmation_id

                target_status = "APPROVED" if decision_upper == "APPROVED" else "REJECTED"
                target_decision = "APPROVED" if decision_upper == "APPROVED" else (reason or "REJECTED")

                rows_updated = (
                    session.query(DurableConfirmationRecord)
                    .filter(
                        DurableConfirmationRecord.confirmation_id == target_cid,
                        DurableConfirmationRecord.task_id == task_id,
                        DurableConfirmationRecord.status == "PENDING",
                    )
                    .update(
                        {
                            DurableConfirmationRecord.status: target_status,
                            DurableConfirmationRecord.decision: target_decision,
                            DurableConfirmationRecord.decision_by: decision_by,
                            DurableConfirmationRecord.decided_at: now,
                        },
                        synchronize_session="fetch",
                    )
                )
                session.commit()

                if rows_updated == 0:
                    current_rec = session.get(DurableConfirmationRecord, target_cid)
                    if not current_rec or current_rec.task_id != task_id:
                        raise ValueError(f"No confirmation found for task {task_id}")
                    raise ValueError(
                        f"Confirmation {current_rec.confirmation_id} is already {current_rec.status} (cannot be resolved again)"
                    )

                record = session.get(DurableConfirmationRecord, target_cid)
                conf = DurableConfirmation(
                    confirmation_id=record.confirmation_id,
                    task_id=record.task_id,
                    step_id=record.step_id,
                    tool=record.tool,
                    risk=record.risk,
                    side_effect=record.side_effect,
                    description=record.description,
                    arguments=json.loads(record.arguments_json or "{}"),
                    redacted_arguments=json.loads(record.redacted_arguments_json or "{}"),
                    status=record.status,
                    decision=record.decision,
                    decision_by=record.decision_by,
                    decided_at=record.decided_at,
                    created_at=record.created_at,
                    fingerprint=getattr(record, "fingerprint", "") or "",
                )

        if decision_upper == "APPROVED":
            self.update_task_status(task_id, TaskStatus.READY.value, recovery_state="")
            self._emit_task_event(
                task_id,
                "TASK_CONFIRMATION_APPROVED",
                step_id=conf.step_id,
                confirmation_id=conf.confirmation_id,
            )
        else:
            step = self.get_step(conf.step_id)
            if step:
                self.update_step(
                    conf.step_id,
                    StepStatus.FAILED.value,
                    result={
                        "ok": False,
                        "status": ToolStatus.DENIED.value,
                        "error": f"Confirmation rejected by {decision_by}: {reason or 'REJECTED'}",
                        "error_code": "CONFIRMATION_REJECTED",
                    },
                )
            self.update_task_status(
                task_id,
                TaskStatus.FAILED.value,
                recovery_state="CONFIRMATION_REJECTED",
                error=f"Confirmation for step {conf.step_id} ({conf.tool}) was rejected.",
            )
            self._emit_task_event(
                task_id,
                "TASK_CONFIRMATION_REJECTED",
                step_id=conf.step_id,
                confirmation_id=conf.confirmation_id,
                reason=reason,
            )
        return conf

    def _mark_confirmation_consumed(self, confirmation_id: str) -> bool:
        with db_lock:
            with self._session_factory() as session:
                rows_updated = (
                    session.query(DurableConfirmationRecord)
                    .filter(
                        DurableConfirmationRecord.confirmation_id == confirmation_id,
                        DurableConfirmationRecord.status == "APPROVED",
                    )
                    .update(
                        {DurableConfirmationRecord.status: "CONSUMED"},
                        synchronize_session="fetch",
                    )
                )
                session.commit()
                return rows_updated > 0

    def _invalidate_confirmation(self, confirmation_id: str, reason: str = "") -> None:
        with db_lock:
            with self._session_factory() as session:
                record = session.get(DurableConfirmationRecord, confirmation_id)
                if record:
                    record.status = "INVALIDATED"
                    record.decision = f"INVALIDATED: {reason}"
                    session.commit()

    def create_clarification_request(
        self,
        task_id: str,
        goal: str,
        questions: List[str],
    ) -> DurableClarification:
        cid = new_clarification_id()
        now = timestamp_now()
        with db_lock:
            with self._session_factory() as session:
                record = DurableClarificationRecord(
                    clarification_id=cid,
                    task_id=task_id,
                    goal=goal,
                    questions_json=json.dumps(questions, ensure_ascii=False),
                    answers_json="{}",
                    status="PENDING",
                    created_at=now,
                    answered_at="",
                )
                session.add(record)
                session.commit()

        clar = DurableClarification(
            clarification_id=cid,
            task_id=task_id,
            goal=goal,
            questions=questions,
            answers={},
            status="PENDING",
            created_at=now,
        )
        self._emit_task_event(
            task_id,
            "TASK_CLARIFICATION_REQUESTED",
            clarification_id=cid,
            questions=questions,
        )
        return clar

    def get_clarification(self, clarification_id: str) -> Optional[DurableClarification]:
        with db_lock:
            with self._session_factory() as session:
                rec = session.get(DurableClarificationRecord, clarification_id)
                if not rec:
                    return None
                return DurableClarification(
                    clarification_id=rec.clarification_id,
                    task_id=rec.task_id,
                    goal=rec.goal,
                    questions=json.loads(rec.questions_json or "[]"),
                    answers=json.loads(rec.answers_json or "{}"),
                    status=rec.status,
                    created_at=rec.created_at,
                    answered_at=rec.answered_at,
                )

    def get_pending_clarification_for_task(self, task_id: str) -> Optional[DurableClarification]:
        with db_lock:
            with self._session_factory() as session:
                rec = (
                    session.query(DurableClarificationRecord)
                    .filter_by(task_id=task_id, status="PENDING")
                    .order_by(DurableClarificationRecord.created_at.desc())
                    .first()
                )
                if not rec:
                    return None
                return DurableClarification(
                    clarification_id=rec.clarification_id,
                    task_id=rec.task_id,
                    goal=rec.goal,
                    questions=json.loads(rec.questions_json or "[]"),
                    answers=json.loads(rec.answers_json or "{}"),
                    status=rec.status,
                    created_at=rec.created_at,
                    answered_at=rec.answered_at,
                )

    def list_clarifications_for_task(self, task_id: str) -> List[DurableClarification]:
        with db_lock:
            with self._session_factory() as session:
                recs = (
                    session.query(DurableClarificationRecord)
                    .filter_by(task_id=task_id)
                    .order_by(DurableClarificationRecord.created_at.asc())
                    .all()
                )
                return [
                    DurableClarification(
                        clarification_id=rec.clarification_id,
                        task_id=rec.task_id,
                        goal=rec.goal,
                        questions=json.loads(rec.questions_json or "[]"),
                        answers=json.loads(rec.answers_json or "{}"),
                        status=rec.status,
                        created_at=rec.created_at,
                        answered_at=rec.answered_at,
                    )
                    for rec in recs
                ]

    def resolve_clarification(
        self,
        task_id: str,
        clarification_id: Optional[str] = None,
        answers: Optional[Dict[str, Any]] = None,
        clarified_goal: Optional[str] = None,
    ) -> DurableClarification:
        answers = answers or {}
        now = timestamp_now()

        check_text = (clarified_goal or "") + " " + json.dumps(answers)
        check_lower = check_text.lower()
        for tok in PROHIBITED_PLAN_TOKENS:
            if tok in check_lower:
                raise ValueError(f"Disallowed security token detected in clarification: {tok}")

        with db_lock:
            with self._session_factory() as session:
                if clarification_id:
                    target_cid = clarification_id
                else:
                    rec = (
                        session.query(DurableClarificationRecord)
                        .filter_by(task_id=task_id)
                        .order_by(DurableClarificationRecord.created_at.desc())
                        .first()
                    )
                    if not rec:
                        raise ValueError(f"No clarification found for task {task_id}")
                    target_cid = rec.clarification_id

                rows_updated = (
                    session.query(DurableClarificationRecord)
                    .filter(
                        DurableClarificationRecord.clarification_id == target_cid,
                        DurableClarificationRecord.task_id == task_id,
                        DurableClarificationRecord.status == "PENDING",
                    )
                    .update(
                        {
                            DurableClarificationRecord.answers_json: json.dumps(answers, ensure_ascii=False),
                            DurableClarificationRecord.status: "ANSWERED",
                            DurableClarificationRecord.answered_at: now,
                        },
                        synchronize_session="fetch",
                    )
                )
                session.commit()

                if rows_updated == 0:
                    current_rec = session.get(DurableClarificationRecord, target_cid)
                    if not current_rec or current_rec.task_id != task_id:
                        raise ValueError(f"No clarification found for task {task_id}")
                    raise ValueError(
                        f"Clarification {current_rec.clarification_id} is already {current_rec.status} (cannot be answered again)"
                    )

                record = session.get(DurableClarificationRecord, target_cid)
                clar = DurableClarification(
                    clarification_id=record.clarification_id,
                    task_id=record.task_id,
                    goal=record.goal,
                    questions=json.loads(record.questions_json or "[]"),
                    answers=answers,
                    status="ANSWERED",
                    created_at=record.created_at,
                    answered_at=now,
                )
        self._emit_task_event(
            task_id,
            "TASK_CLARIFICATION_ANSWERED",
            clarification_id=clar.clarification_id,
            answers=answers,
        )
        return clar

    def cancel_task(self, task_id: str, reason: str = "") -> bool:
        task = self.get_task(task_id)
        if not task:
            return False
        if TaskStatus(task.status).is_terminal:
            return False

        self.update_task_status(
            task_id, TaskStatus.CANCELLED.value, error=reason or "Task cancelled"
        )
        for step in self.list_steps(task_id):
            if step.status in (StepStatus.PENDING.value, StepStatus.RUNNING.value, StepStatus.WAITING.value):
                self.update_step(step.step_id, StepStatus.SKIPPED.value)

        # Invalidate any pending confirmations or clarifications
        with db_lock:
            with self._session_factory() as session:
                for c in session.query(DurableConfirmationRecord).filter_by(task_id=task_id, status="PENDING").all():
                    c.status = "CANCELLED"
                    c.decision = "CANCELLED: Task was cancelled"
                for cl in session.query(DurableClarificationRecord).filter_by(task_id=task_id, status="PENDING").all():
                    cl.status = "CANCELLED"
                session.commit()

        return True

    def pause_task(self, task_id: str) -> bool:
        """Pauses a running or pending durable task."""
        task = self.get_task(task_id)
        if not task or TaskStatus(task.status).is_terminal:
            return False

        self.update_task_status(task_id, TaskStatus.PAUSED.value)
        self._emit_task_event(task_id, "TASK_PAUSED", status=TaskStatus.PAUSED.value)
        return True

    def list_tasks(
        self, status: Optional[str] = None, limit: int = 50
    ) -> List[DurableTask]:
        """Lists durable tasks with optional status filter."""
        with db_lock:
            with self._session_factory() as session:
                q = session.query(DurableTaskRecord)
                if status:
                    q = q.filter_by(status=status)
                records = (
                    q.order_by(DurableTaskRecord.updated_at.desc())
                    .limit(limit)
                    .all()
                )
                t_ids = [r.task_id for r in records]

        return [t for tid in t_ids if (t := self.get_task(tid)) is not None]

    def settle_ambiguous_step(
        self,
        task_id: str,
        step_id: str,
        ok: bool,
        result: Optional[Dict[str, Any]] = None,
        evidence: Optional[List[Dict[str, Any]]] = None,
        error: str = "",
    ) -> Optional[DurableStep]:
        """
        Explicitly settles a step that is in UNKNOWN/AMBIGUOUS state
        (e.g., after an ambiguous timeout or crash during non-idempotent action)
        using verified late evidence or operator postcondition inspection.
        """
        step = self.get_step(step_id)
        if not step:
            raise ValueError(f"Unknown step: {step_id}")

        if step.status in (StepStatus.COMPLETED.value, StepStatus.CANCELLED.value):
            logger.info("Step %s already in terminal state %s; ignoring duplicate settlement", step_id, step.status)
            return step

        new_status = StepStatus.COMPLETED.value if ok else StepStatus.FAILED.value
        res_dict = dict(result or step.result or {})
        res_dict.update({
            "ok": ok,
            "status": ToolStatus.SUCCESS.value if ok else ToolStatus.FAILED.value,
            "error": error or ("Settled as successful via evidence" if ok else "Settled as failed"),
        })

        updated_step = self.update_step(
            step_id,
            status=new_status,
            result=res_dict,
            evidence=evidence or step.evidence,
        )

        all_steps = self.list_steps(task_id)
        if ok and all(s.status == StepStatus.COMPLETED.value for s in all_steps):
            self.update_task_status(
                task_id,
                TaskStatus.COMPLETED.value,
                recovery_state="SETTLED_SUCCESS",
            )
            self._emit_task_event(task_id, "TASK_COMPLETED", step_id=step_id, status=TaskStatus.COMPLETED.value)
        elif not ok:
            self.update_task_status(
                task_id,
                TaskStatus.FAILED.value,
                error=error or "Step failed upon settlement",
                recovery_state="SETTLED_FAILED",
            )
            self._emit_task_event(task_id, "TASK_STEP_FAILED", step_id=step_id, status=TaskStatus.FAILED.value)
        else:
            self.update_task_status(
                task_id,
                TaskStatus.RUNNING.value,
                recovery_state="SETTLED",
            )

        return updated_step

    def resume_task(self, task_id: str) -> Tuple[Optional[DurableTask], List[DurableStep]]:
        """
        Rehydrates a task after disconnect or restart.
        If the task was PAUSED, resets status to READY so execution can resume.
        Identifies settled steps vs unexecuted/unsettled steps.
        """
        task = self.get_task(task_id)
        if not task:
            return None, []

        if task.status in (TaskStatus.PAUSED.value, TaskStatus.WAITING.value):
            self.update_task_status(task_id, TaskStatus.READY.value)
            self._emit_task_event(task_id, "TASK_RESUMED", status=TaskStatus.READY.value)
            task = self.get_task(task_id)

        steps = self.list_steps(task_id)
        return task, steps

    def execute_compound_task_async(
        self,
        task_id: str,
        executor: ToolExecutor,
        interactive: Optional[bool] = None,
    ) -> threading.Thread:
        """
        Runs execute_compound_task asynchronously in a background daemon thread
        managed by TaskWorkerManager so that thread panics are cleanly caught
        and recorded to SQLite.
        """
        return self.workers.submit(task_id, executor, interactive=interactive)

    def resume_all_active(self, executor: ToolExecutor) -> List[DurableTask]:
        """
        On restart:
        1. Load all active/unfinished tasks from SQLite.
        2. Inspect current steps.
        3. Never blindly replay completed or mutating unknown steps.
        4. Probe postconditions for interrupted mutating steps before declaring UNKNOWN.
        5. Safely continue uncompleted tasks.
        """
        active_tasks = self.list_active_tasks()
        resumed = []

        for task in active_tasks:
            steps = self.list_steps(task.task_id)
            has_ambiguous_mutation = False

            for step in steps:
                if step.status == StepStatus.RUNNING.value:
                    # The server crashed while this step was running!
                    se_val = step.side_effect
                    if se_val.startswith("SideEffect."):
                        se_val = se_val.split(".", 1)[1]
                    se_enum = (
                        SideEffect(se_val)
                        if se_val in SideEffect._value2member_map_
                        else SideEffect.UNKNOWN
                    )

                    if not se_enum.repeatable:
                        # Mutating action was in flight when process crashed!
                        # Check if tool provides a postcondition verify observer probe
                        tool_obj = executor.registry.get(step.tool) if (executor and executor.registry) else None
                        probe_verified = None
                        if tool_obj and hasattr(tool_obj, "verify") and callable(tool_obj.verify):
                            try:
                                v_res = tool_obj.verify(**step.arguments)
                                if isinstance(v_res, bool):
                                    probe_verified = v_res
                                elif hasattr(v_res, "ok"):
                                    probe_verified = bool(v_res.ok)
                            except Exception as v_err:
                                logger.warning("Recovery postcondition probe error: %s", v_err)
                                probe_verified = None

                        if probe_verified is True:
                            logger.info("Interrupted step %s verified as COMPLETED via postcondition probe", step.step_id)
                            res_dict = dict(step.result or {})
                            res_dict.update({
                                "ok": True,
                                "status": ToolStatus.SUCCESS.value,
                                "verified_by_probe": True,
                            })
                            if hasattr(v_res, "data") and isinstance(v_res.data, dict):
                                res_dict["data"] = v_res.data
                            self.update_step(
                                step.step_id,
                                StepStatus.COMPLETED.value,
                                result=res_dict,
                                evidence=[{
                                    "kind": EvidenceKind.POSTCONDITION.value,
                                    "verified": True,
                                    "reference": f"{step.tool}.verify",
                                    "detail": "Verified by postcondition probe during recovery",
                                }],
                            )
                        elif probe_verified is False:
                            logger.info("Interrupted step %s verified as FAILED via postcondition probe", step.step_id)
                            self.update_step(
                                step.step_id,
                                StepStatus.FAILED.value,
                                result={"error": "Postcondition verification confirmed step was not applied."},
                            )
                            has_ambiguous_mutation = True
                        else:
                            # Outcome is genuinely UNKNOWN. Do NOT replay!
                            self.update_step(
                                step.step_id,
                                StepStatus.UNKNOWN.value,
                                result={"error": "Interrupted during execution; outcome unknown."},
                            )
                            has_ambiguous_mutation = True
                    else:
                        # Safe / repeatable action: reset to PENDING to retry safely
                        self.update_step(step.step_id, StepStatus.PENDING.value)
                elif step.status == StepStatus.WAITING.value:
                    # Check if step is waiting for confirmation
                    step_conf = self.get_confirmation_for_step(step.step_id)
                    if step_conf is not None and step_conf.status == "PENDING":
                        # Remains waiting for human confirmation across restart
                        continue
                    elif step_conf is not None and step_conf.status == "APPROVED":
                        # Was approved before crash; transition to PENDING so it can execute
                        self.update_step(step.step_id, StepStatus.PENDING.value)
                        continue
                    elif step_conf is not None and step_conf.status == "REJECTED":
                        self.update_step(step.step_id, StepStatus.FAILED.value)
                        continue

                    # Task was waiting for capability synthesis when process crashed.
                    # Check if the tool was promoted before crash and rehydrated into registry:
                    cap_query = (step.capability or step.tool or step.name or "").strip()
                    target_name = cap_query.replace(".", "_").replace("-", "_").lower()
                    if executor and executor.registry and (executor.registry.has(step.tool) or executor.registry.has(target_name)):
                        if not step.tool or not executor.registry.has(step.tool):
                            step.tool = target_name
                            self._update_step_tool_name(step.step_id, target_name)
                    # Reset step to PENDING so execute_compound_task can continue seamlessly
                    self.update_step(step.step_id, StepStatus.PENDING.value)

            if task.status == TaskStatus.WAITING.value:
                # If task is waiting for a confirmation or clarification, DO NOT transition to READY!
                pending_conf = self.get_pending_confirmation_for_task(task.task_id)
                pending_clar = self.get_pending_clarification_for_task(task.task_id)
                if pending_conf is not None:
                    self.update_task_status(
                        task.task_id,
                        TaskStatus.WAITING.value,
                        recovery_state="WAITING_FOR_CONFIRMATION",
                        current_step_id=pending_conf.step_id,
                    )
                    resumed.append(self.get_task(task.task_id))
                    continue
                elif pending_clar is not None:
                    self.update_task_status(
                        task.task_id,
                        TaskStatus.WAITING.value,
                        recovery_state="WAITING_FOR_CLARIFICATION",
                    )
                    resumed.append(self.get_task(task.task_id))
                    continue
                self.update_task_status(task.task_id, TaskStatus.READY.value, recovery_state="")

            if has_ambiguous_mutation:
                self.update_task_status(
                    task.task_id,
                    TaskStatus.UNKNOWN.value,
                    error="Task was interrupted during a mutating step. Outcome is UNKNOWN; manual verification required.",
                    recovery_state="REQUIRES_MANUAL_VERIFICATION",
                )
                resumed.append(self.get_task(task.task_id))  # type: ignore
            else:
                refreshed_steps = self.list_steps(task.task_id)
                if refreshed_steps and all(s.status == StepStatus.COMPLETED.value for s in refreshed_steps):
                    logger.info("Task %s all steps already completed; reconciling task status to COMPLETED", task.task_id)
                    self.update_task_status(task.task_id, TaskStatus.COMPLETED.value, recovery_state="ALL_STEPS_COMPLETED")
                    self._emit_task_event(task.task_id, "TASK_COMPLETED", status=TaskStatus.COMPLETED.value, detail="All steps already completed")
                    resumed.append(self.get_task(task.task_id))
                    continue

                updated_task = self.execute_compound_task(task.task_id, executor)
                resumed.append(updated_task)

        return resumed

    def _update_step_tool_name(self, step_id: str, tool_name: str) -> None:
        with db_lock:
            with self._session_factory() as session:
                record = session.get(DurableStepRecord, step_id)
                if record:
                    record.tool = tool_name
                    record.updated_at = timestamp_now()
                    session.commit()

    def _maybe_recover_step_capability(
        self,
        task: DurableTask,
        step: DurableStep,
        executor: ToolExecutor,
        resolved_args: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str]:
        """
        Phase 5B.4: Autonomous Capability Gap Recovery for Durable Task Steps.

        When a step's required capability/tool is missing:
        1. Evaluate gap synthesizability via CapabilityGapEngine.
        2. Evaluate deterministic safety & platform eligibility via AutonomousSynthesisPolicy.
        3. If eligible:
           - Checkpoint step.status = WAITING, task.status = WAITING.
           - Execute existing ToolSynthesisEngine.synthesize_and_promote().
           - On promotion: step.status = RUNNING, task.status = RUNNING.
           - Return (True, "") to resume execution of the SAME step via ToolExecutor.
        4. If not eligible or synthesis fails:
           - Return (False, reason) without false success.
        """
        if self.synthesis_engine is None:
            return False, "Synthesis engine not configured"

        cap_query = (step.capability or step.tool or step.name or "").strip()
        if not cap_query:
            return False, "Step specifies no capability, tool, or name"

        # Resolve or configure gap engine with executor's registry
        gap_engine = self.gap_engine
        if gap_engine is None:
            from core.capabilities.discovery import SkillDiscovery
            from core.capabilities.gap import CapabilityGapEngine
            gap_engine = CapabilityGapEngine(discovery=SkillDiscovery(), registry=executor.registry)
        else:
            gap_engine.registry = executor.registry

        try:
            match_state, gap = gap_engine.evaluate_capability(cap_query)
        except Exception as eval_err:
            logger.warning("Capability gap evaluation error for '%s': %s", cap_query, eval_err)
            return False, f"Gap evaluation error: {eval_err}"

        if (gap is None or not gap.is_synthesizable) and step.tool and not executor.registry.has(step.tool):
            import sys
            from core.capabilities.gap import CapabilityGap, CapabilityMatchState, GapStatus, new_gap_id
            gap = CapabilityGap(
                gap_id=new_gap_id(),
                requested_capability=step.capability or f"custom.{step.tool}",
                intent=step.name or step.tool,
                reason=f"Step requires tool '{step.tool}' which is not registered in executor registry",
                match_state=CapabilityMatchState.SYNTHESIZABLE.value,
                platform="win32" if sys.platform == "win32" else "linux",
                status=GapStatus.IDENTIFIED.value,
            )

        if gap is None or not gap.is_synthesizable:
            logger.info("Step %s gap '%s' is not synthesizable (match_state=%s)", step.step_id, cap_query, match_state)
            return False, f"Gap not synthesizable ({match_state})"

        # Propagate step argument specifications into gap requirements so synthesized tool matches step schema
        eff_args = resolved_args if resolved_args is not None else step.arguments
        if eff_args and not gap.required_input:
            def _infer_type(val: Any) -> str:
                if isinstance(val, bool):
                    return "boolean"
                if isinstance(val, int):
                    return "integer"
                if isinstance(val, float):
                    return "number"
                if isinstance(val, dict):
                    return "object"
                if isinstance(val, list):
                    return "array"
                return "string"

            gap.required_input = {
                k: {
                    "type": _infer_type(v),
                    "description": f"Input argument '{k}' required by step",
                }
                for k, v in eff_args.items()
            }

        # Bound maximum dynamic tools per task to prevent runaway recursion
        task_meta = task.metadata or {}
        synth_count = task_meta.get("_synthesis_count", 0)
        max_synths_per_task = 3
        if synth_count >= max_synths_per_task:
            logger.warning("Task %s exceeded maximum synthesis limit (%d)", task.task_id, max_synths_per_task)
            return False, f"Maximum capability synthesis limit reached ({max_synths_per_task} per task)"

        # Check synthesis policy eligibility
        if self.synthesis_policy is not None:
            eligible, reason = self.synthesis_policy.is_eligible(
                gap, depth=0, registry=executor.registry
            )
            if not eligible:
                logger.info("Step %s gap %s not eligible for synthesis: %s", step.step_id, gap.gap_id, reason)
                return False, f"Synthesis policy rejected: {reason}"

        # Persist WAITING state during synthesis
        self.update_step(step.step_id, StepStatus.WAITING.value)
        self.update_task_status(
            task.task_id,
            TaskStatus.WAITING.value,
            recovery_state=f"SYNTHESIZING_CAPABILITY:{gap.requested_capability}",
            current_step_id=step.step_id,
        )
        self._emit_task_event(
            task.task_id,
            "TASK_STEP_WAITING",
            step_id=step.step_id,
            status=StepStatus.WAITING.value,
            detail=f"Synthesizing capability {gap.requested_capability}",
        )

        cap_name = gap.requested_capability
        lock_acquired = True
        wait_event = None
        if self.synthesis_policy is not None and hasattr(self.synthesis_policy, "acquire_synthesis_lock"):
            lock_acquired, wait_event = self.synthesis_policy.acquire_synthesis_lock(cap_name)

        if not lock_acquired and wait_event is not None:
            logger.info("Task %s step %s waiting for concurrent synthesis of %s...", task.task_id, step.step_id, cap_name)
            wait_event.wait(timeout=60.0)
            target_tool_name = cap_name.replace(".", "_").replace("-", "_").lower()
            if executor.registry and (executor.registry.has(target_tool_name) or (step.tool and executor.registry.has(step.tool))):
                if not step.tool or not executor.registry.has(step.tool):
                    step.tool = target_tool_name
                    self._update_step_tool_name(step.step_id, target_tool_name)
                self.update_step(step.step_id, StepStatus.PENDING.value)
                self.update_task_status(task.task_id, TaskStatus.RUNNING.value, recovery_state="", current_step_id=step.step_id)
                self._emit_task_event(task.task_id, "TASK_STEP_RESUMED", step_id=step.step_id, status=StepStatus.PENDING.value)
                return True, ""
            else:
                self.update_step(step.step_id, StepStatus.FAILED.value)
                self.update_task_status(task.task_id, TaskStatus.FAILED.value, error=f"Concurrent synthesis of {cap_name} timed out or failed")
                return False, f"Concurrent synthesis of {cap_name} failed"

        try:
            target_tool_name = cap_name.replace(".", "_").replace("-", "_").lower()
            if executor.registry and (executor.registry.has(target_tool_name) or (step.tool and executor.registry.has(step.tool))):
                logger.info("Tool '%s' already registered in registry; skipping synthesis", target_tool_name)
                if not step.tool or not executor.registry.has(step.tool):
                    step.tool = target_tool_name
                    self._update_step_tool_name(step.step_id, target_tool_name)
                self.update_step(step.step_id, StepStatus.PENDING.value)
                self.update_task_status(task.task_id, TaskStatus.RUNNING.value, recovery_state="", current_step_id=step.step_id)
                self._emit_task_event(task.task_id, "TASK_STEP_RESUMED", step_id=step.step_id, status=StepStatus.PENDING.value)
                return True, ""

            logger.info(
                "Task %s step %s initiating autonomous synthesis for gap %s: capability='%s', intent='%s'",
                task.task_id,
                step.step_id,
                gap.gap_id,
                cap_name,
                cap_query,
            )

            # Ensure builder registers into executor's registry
            if hasattr(self.synthesis_engine, "builder") and self.synthesis_engine.builder:
                self.synthesis_engine.builder.registry = executor.registry

            if not isinstance(task.metadata, dict):
                task.metadata = {}
            task.metadata["_synthesis_count"] = synth_count + 1

            tool, report, synth_res = self.synthesis_engine.synthesize_and_promote(
                gap, approver="autonomous_policy"
            )

            if tool is not None:
                logger.info(
                    "Task %s step %s autonomous synthesis succeeded: promoted tool '%s' (v%s)",
                    task.task_id,
                    step.step_id,
                    tool.name,
                    getattr(tool, "version", 1),
                )
                if not step.tool or not executor.registry.has(step.tool):
                    step.tool = tool.name
                    self._update_step_tool_name(step.step_id, tool.name)

                # Transition back to PENDING:
                self.update_step(step.step_id, StepStatus.PENDING.value)
                self.update_task_status(task.task_id, TaskStatus.RUNNING.value, recovery_state="", current_step_id=step.step_id)
                self._emit_task_event(task.task_id, "TASK_STEP_RESUMED", step_id=step.step_id, status=StepStatus.PENDING.value)
                return True, ""
            else:
                failure_detail = (
                    synth_res.failure_reason
                    if synth_res and hasattr(synth_res, "failure_reason") and synth_res.failure_reason
                    else (str(report.errors) if report and report.errors else "Synthesis failed")
                )
                logger.warning(
                    "Task %s step %s autonomous synthesis failed for '%s': %s",
                    task.task_id,
                    step.step_id,
                    cap_name,
                    failure_detail,
                )
                self.update_step(
                    step.step_id,
                    StepStatus.FAILED.value,
                    result={"error": failure_detail, "error_code": "SYNTHESIS_FAILED"},
                )
                self.update_task_status(
                    task.task_id,
                    TaskStatus.FAILED.value,
                    error=f"Step '{step.name}' capability synthesis failed: {failure_detail}",
                    current_step_id=step.step_id,
                )
                self._emit_task_event(task.task_id, "TASK_STEP_FAILED", step_id=step.step_id, status=TaskStatus.FAILED.value)
                return False, failure_detail
        finally:
            if lock_acquired and self.synthesis_policy is not None and hasattr(self.synthesis_policy, "release_synthesis_lock"):
                self.synthesis_policy.release_synthesis_lock(cap_name)

    def execute_compound_task(
        self,
        task_id: str,
        executor: ToolExecutor,
        interactive: Optional[bool] = None,
    ) -> DurableTask:
        """
        Executes compound multi-step task with checkpointing, context parameter substitution,
        and idempotency-aware retry.
        Survives interruption and never blindly repeats mutating unknowns.
        """
        task = self.get_task(task_id)
        if not task:
            raise ValueError(f"Unknown task: {task_id}")

        if TaskStatus(task.status).is_terminal:
            return task

        self.update_task_status(task_id, TaskStatus.RUNNING.value)
        self._emit_task_event(task_id, "TASK_STARTED", status=TaskStatus.RUNNING.value)
        steps = self.list_steps(task_id)

        # Context accumulator for inter-step parameter substitution: ${step_0.field}
        step_context: Dict[str, Any] = {}
        for s in steps:
            if s.status == StepStatus.COMPLETED.value:
                payload = {}
                if isinstance(s.result.get("data"), dict):
                    payload.update(s.result["data"])
                if isinstance(s.result.get("output"), dict):
                    payload.update(s.result["output"])
                payload.update(s.result)
                if "output" in s.result:
                    payload["output"] = s.result["output"]
                step_context[f"step_{s.step_index}"] = payload
                step_context[s.name] = payload
                step_context[s.step_id] = payload

        for step in steps:
            # Check if task was paused by operator
            current_task = self.get_task(task_id)
            if current_task and current_task.status == TaskStatus.PAUSED.value:
                return current_task

            # Idempotency checkpoint: skip steps that are already completed!
            if step.status == StepStatus.COMPLETED.value:
                continue

            # Check dependencies: if any dependent step failed, skip or fail
            if step.depends_on:
                for dep_id in step.depends_on:
                    dep_step = self.get_step(dep_id)
                    if dep_step and dep_step.status != StepStatus.COMPLETED.value:
                        self.update_step(step.step_id, StepStatus.SKIPPED.value)
                        self.update_task_status(
                            task_id,
                            TaskStatus.FAILED.value,
                            error=f"Dependency '{dep_step.name}' did not complete successfully.",
                        )
                        self._emit_task_event(task_id, "TASK_STEP_FAILED", step_id=step.step_id, status=TaskStatus.FAILED.value)
                        return self.get_task(task_id)  # type: ignore

            self.update_task_status(task_id, TaskStatus.RUNNING.value, current_step_id=step.step_id)
            self._emit_task_event(task_id, "TASK_STEP_STARTED", step_id=step.step_id, status=StepStatus.RUNNING.value)

            # Parameter substitution from prior step outputs
            try:
                resolved_args = CompoundTaskPlanner.substitute_parameters(
                    step.arguments, step_context
                )
            except UnresolvedParameterError as err:
                res_dict = {
                    "ok": False,
                    "status": ToolStatus.FAILED.value,
                    "error": str(err),
                    "error_code": "UNRESOLVED_PARAMETER",
                }
                self.update_step(step.step_id, StepStatus.FAILED.value, result=res_dict)
                self.update_task_status(
                    task_id,
                    TaskStatus.FAILED.value,
                    error=f"Step '{step.name}' failed parameter substitution: {err}",
                    current_step_id=step.step_id,
                )
                self._emit_task_event(task_id, "TASK_STEP_FAILED", step_id=step.step_id, status=TaskStatus.FAILED.value)
                return self.get_task(task_id)  # type: ignore

            # Check if tool is missing before attempting execution
            tool_name = step.tool
            tool_available = bool(
                tool_name and executor and executor.registry and executor.registry.has(tool_name)
            )

            if not tool_available:
                if self.synthesis_engine is not None:
                    recovered, reason = self._maybe_recover_step_capability(
                        task, step, executor, resolved_args=resolved_args
                    )
                    if not recovered:
                        current_task = self.get_task(task_id)
                        if current_task and TaskStatus(current_task.status).is_terminal:
                            return current_task
                        # If policy rejected or not synthesizable, task remains non-terminal
                        # and will fail deterministically at executor.execute below

            # Check confirmation requirements for this step
            tool_obj = executor.registry.get(step.tool) if (executor and executor.registry) else None
            tool_risk = getattr(tool_obj, "risk", None) if tool_obj else None
            is_interactive = (
                interactive
                if interactive is not None
                else (
                    bool(task.metadata.get("interactive_confirmation", False))
                    or getattr(self, "interactive_confirmation", False)
                    or getattr(executor, "interactive_confirmation", False)
                )
            )
            existing_conf = self.get_confirmation_for_step(step.step_id)
            requires_confirmation = False
            if tool_risk is not None and tool_risk not in executor.policy.auto_approve:
                if is_interactive:
                    requires_confirmation = True
            elif existing_conf is not None:
                # If a confirmation was already issued for this step, it must be validated/consumed
                requires_confirmation = True

            if requires_confirmation:
                conf = existing_conf if existing_conf is not None else self.get_confirmation_for_step(step.step_id)
                if conf is None:
                    desc = getattr(tool_obj, "description", "")
                    risk_str = getattr(tool_risk, "value", str(tool_risk))
                    se_str = getattr(getattr(tool_obj, "side_effect", SideEffect.UNKNOWN), "value", "unknown")
                    conf = self.create_confirmation_request(
                        task_id=task_id,
                        step_id=step.step_id,
                        tool=step.tool,
                        risk=risk_str,
                        side_effect=se_str,
                        description=desc,
                        arguments=resolved_args,
                    )
                    self.update_step(step.step_id, StepStatus.WAITING.value)
                    self.update_task_status(
                        task_id,
                        TaskStatus.WAITING.value,
                        current_step_id=step.step_id,
                        error="Waiting for user confirmation",
                        recovery_state="WAITING_FOR_CONFIRMATION",
                    )
                    logger.info("Step %s (%s) requires confirmation; task %s paused in WAITING", step.step_id, step.tool, task_id)
                    return self.get_task(task_id)  # type: ignore

                elif conf.status == "PENDING":
                    self.update_step(step.step_id, StepStatus.WAITING.value)
                    self.update_task_status(
                        task_id,
                        TaskStatus.WAITING.value,
                        current_step_id=step.step_id,
                        error="Waiting for user confirmation",
                        recovery_state="WAITING_FOR_CONFIRMATION",
                    )
                    return self.get_task(task_id)  # type: ignore

                elif conf.status == "REJECTED":
                    res_dict = {
                        "ok": False,
                        "status": ToolStatus.DENIED.value,
                        "error": f"Confirmation rejected: {conf.decision}",
                        "error_code": "CONFIRMATION_REJECTED",
                    }
                    self.update_step(step.step_id, StepStatus.FAILED.value, result=res_dict)
                    self.update_task_status(
                        task_id,
                        TaskStatus.FAILED.value,
                        error=f"Step '{step.name}' confirmation was rejected.",
                        current_step_id=step.step_id,
                    )
                    return self.get_task(task_id)  # type: ignore

                elif conf.status == "APPROVED":
                    current_fp = compute_invocation_fingerprint(task_id, step.step_id, step.tool, resolved_args)
                    expected_fp = conf.fingerprint or compute_invocation_fingerprint(conf.task_id, conf.step_id, conf.tool, conf.arguments)
                    if current_fp != expected_fp:
                        self._invalidate_confirmation(conf.confirmation_id, "Invocation arguments or tool target mismatch")
                        res_dict = {
                            "ok": False,
                            "status": ToolStatus.DENIED.value,
                            "error": "Confirmation invalidated: invocation arguments or tool target do not match confirmed fingerprint.",
                            "error_code": "CONFIRMATION_ARGUMENT_MISMATCH",
                        }
                        self.update_step(step.step_id, StepStatus.FAILED.value, result=res_dict)
                        self.update_task_status(
                            task_id,
                            TaskStatus.FAILED.value,
                            error=f"Step '{step.name}' confirmation invalidated due to argument mismatch.",
                            current_step_id=step.step_id,
                        )
                        return self.get_task(task_id)  # type: ignore

                    consumed = self._mark_confirmation_consumed(conf.confirmation_id)
                    if not consumed:
                        res_dict = {
                            "ok": False,
                            "status": ToolStatus.DENIED.value,
                            "error": "Confirmation is already consumed or cannot be used.",
                            "error_code": "CONFIRMATION_REPLAY_REJECTED",
                        }
                        self.update_step(step.step_id, StepStatus.FAILED.value, result=res_dict)
                        self.update_task_status(
                            task_id,
                            TaskStatus.FAILED.value,
                            error=f"Step '{step.name}' confirmation cannot be consumed again.",
                            current_step_id=step.step_id,
                        )
                        return self.get_task(task_id)  # type: ignore

                elif conf.status in ("CONSUMED", "INVALIDATED", "EXPIRED", "CANCELLED"):
                    res_dict = {
                        "ok": False,
                        "status": ToolStatus.DENIED.value,
                        "error": f"Confirmation is already {conf.status}; cannot be reused.",
                        "error_code": "CONFIRMATION_REPLAY_REJECTED",
                    }
                    self.update_step(step.step_id, StepStatus.FAILED.value, result=res_dict)
                    self.update_task_status(
                        task_id,
                        TaskStatus.FAILED.value,
                        error=f"Step '{step.name}' cannot reuse {conf.status} confirmation.",
                        current_step_id=step.step_id,
                    )
                    return self.get_task(task_id)  # type: ignore

            # Resolve retry policy
            max_attempts = int(step.retry_policy.get("max_attempts", 3))
            backoff_sec = float(step.retry_policy.get("backoff", 1.0))
            success = False
            last_res = None

            while step.attempt < max_attempts and not success:
                new_attempt = step.attempt + 1
                self.update_step(step.step_id, StepStatus.RUNNING.value, attempt=new_attempt)
                step.attempt = new_attempt

                # Check if task was cancelled concurrently
                current_task = self.get_task(task_id)
                if current_task and current_task.status == TaskStatus.CANCELLED.value:
                    self.update_step(step.step_id, StepStatus.CANCELLED.value)
                    self._emit_task_event(task_id, "TASK_CANCELLED", step_id=step.step_id, status=TaskStatus.CANCELLED.value)
                    return current_task

                try:
                    if requires_confirmation:
                        orig_confirm = executor.confirm
                        executor.confirm = lambda t, a: True
                        try:
                            res = executor.execute(step.tool, resolved_args)
                        finally:
                            executor.confirm = orig_confirm
                    else:
                        res = executor.execute(step.tool, resolved_args)
                except Exception as ex:
                    res = ToolResult(
                        ok=False,
                        error=str(ex),
                        tool=step.tool,
                        status=ToolStatus.FAILED.value,
                        error_code="EXECUTION_ERROR",
                    )

                last_res = res
                if res.ok:
                    success = True
                    break

                # Inspect retryability for failures
                se_val = res.side_effect or step.side_effect or SideEffect.UNKNOWN.value
                if se_val.startswith("SideEffect."):
                    se_val = se_val.split(".", 1)[1]
                se_enum = (
                    SideEffect(se_val)
                    if se_val in SideEffect._value2member_map_
                    else SideEffect.UNKNOWN
                )

                retry_state = retryability_of(ToolStatus(res.status), se_enum)
                if not retry_state.may_retry:
                    # Retrying is unsafe (e.g. timeout on mutation, permission denied)
                    break

                if step.attempt < max_attempts:
                    time.sleep(backoff_sec * (2 ** (step.attempt - 1)))

            # Serialize result & evidence
            res = last_res
            ev_list = [
                {
                    "kind": e.kind.value if hasattr(e.kind, "value") else str(e.kind),
                    "verified": e.verified,
                    "reference": e.reference,
                    "detail": e.detail,
                }
                for e in getattr(res, "evidence", ())
            ]
            res_dict = {
                "ok": res.ok if res is not None else False,
                "status": res.status if res is not None else ToolStatus.FAILED.value,
                "error": res.error if res is not None else "Execution failed",
                "output": res.output if res is not None else "",
                "data": getattr(res, "data", {}) if res is not None else {},
                "error_code": res.error_code if res is not None else "UNKNOWN",
                "side_effect": res.side_effect if res is not None else step.side_effect,
            }

            if success and res and res.ok:
                if step.verification_required:
                    self.update_step(step.step_id, StepStatus.VERIFYING.value)
                    self._emit_task_event(task_id, "TASK_STEP_VERIFYING", step_id=step.step_id, status=StepStatus.VERIFYING.value)
                    tool_instance = executor.registry.get(step.tool) if (executor and executor.registry) else None
                    verify_err = ""
                    if tool_instance and hasattr(tool_instance, "verify") and callable(tool_instance.verify):
                        try:
                            v_res = tool_instance.verify(**resolved_args)
                            if isinstance(v_res, bool) and not v_res:
                                success = False
                                verify_err = "Step postcondition verification failed."
                            elif hasattr(v_res, "ok") and not v_res.ok:
                                success = False
                                verify_err = getattr(v_res, "error", "Step postcondition verification failed.")
                            else:
                                ev_list.append({
                                    "kind": EvidenceKind.POSTCONDITION.value,
                                    "verified": True,
                                    "reference": f"{step.tool}.verify",
                                    "detail": "Verified by postcondition probe",
                                    })
                        except Exception as v_err:
                            success = False
                            verify_err = f"Step postcondition verification error: {v_err}"
                    else:
                        has_verified_post = any(
                            e.get("kind") in (EvidenceKind.POSTCONDITION.value, "POSTCONDITION")
                            and e.get("verified") is True
                            for e in ev_list
                        )
                        if not has_verified_post:
                            success = False
                            verify_err = f"Step '{step.name}' required postcondition verification, but tool '{step.tool}' has no verification probe or evidence."

                    if not success:
                        res_dict["ok"] = False
                        res_dict["status"] = ToolStatus.FAILED.value
                        res_dict["error"] = verify_err

            if success and res and res.ok:
                res_dict["ok"] = True
                res_dict["status"] = res.status
                self.update_step(
                    step.step_id,
                    StepStatus.COMPLETED.value,
                    result=res_dict,
                    evidence=ev_list,
                )
                self._emit_task_event(task_id, "TASK_STEP_COMPLETED", step_id=step.step_id, status=StepStatus.COMPLETED.value)
                
                # Propagate output into step_context
                payload = {}
                if res is not None and hasattr(res, "data") and isinstance(res.data, dict):
                    payload.update(res.data)
                if isinstance(res.output, dict):
                    payload.update(res.output)
                payload.update(res_dict)
                if "output" in res_dict:
                    payload["output"] = res_dict["output"]
                # Ensure 'result' is always available for LLM-emitted ${step_N.result}
                if "result" not in payload:
                    # Prefer numeric values from tool data as the canonical "result"
                    numeric_candidates = [
                        v for k, v in payload.items()
                        if isinstance(v, (int, float)) and k not in ("status", "execution_id", "attempt", "ok")
                    ]
                    if numeric_candidates:
                        payload["result"] = numeric_candidates[0]
                    else:
                        payload["result"] = payload.get("output", "")
                # Mirror: if 'output' is missing but 'result' exists, alias it
                if "output" not in payload and "result" in payload:
                    payload["output"] = payload["result"]
                step_context[f"step_{step.step_index}"] = payload
                step_context[step.name] = payload
                step_context[step.step_id] = payload
            else:
                is_timeout = res.status == ToolStatus.TIMEOUT.value if res else False
                se_val = res.side_effect or step.side_effect or SideEffect.UNKNOWN.value
                if se_val.startswith("SideEffect."):
                    se_val = se_val.split(".", 1)[1]
                se_enum = (
                    SideEffect(se_val)
                    if se_val in SideEffect._value2member_map_
                    else SideEffect.UNKNOWN
                )

                if is_timeout and not se_enum.repeatable:
                    self.update_step(
                        step.step_id,
                        StepStatus.UNKNOWN.value,
                        result=res_dict,
                        evidence=ev_list,
                    )
                    self.update_task_status(
                        task_id,
                        TaskStatus.UNKNOWN.value,
                        error=f"Step '{step.name}' timed out with status UNKNOWN. Cannot safely retry non-idempotent mutation.",
                        current_step_id=step.step_id,
                    )
                    self._emit_task_event(task_id, "TASK_UNKNOWN", step_id=step.step_id, status=TaskStatus.UNKNOWN.value)
                    return self.get_task(task_id)  # type: ignore

                self.update_step(
                    step.step_id,
                    StepStatus.FAILED.value,
                    result=res_dict,
                    evidence=ev_list,
                )
                error_detail = res_dict.get("error") or (res.error if res else "Unknown")
                self.update_task_status(
                    task_id,
                    TaskStatus.FAILED.value,
                    error=f"Step '{step.name}' failed: {error_detail}",
                    current_step_id=step.step_id,
                )
                self._emit_task_event(task_id, "TASK_STEP_FAILED", step_id=step.step_id, status=TaskStatus.FAILED.value)
                return self.get_task(task_id)  # type: ignore

        self.update_task_status(task_id, TaskStatus.COMPLETED.value)
        self._emit_task_event(task_id, "TASK_COMPLETED", status=TaskStatus.COMPLETED.value)
        return self.get_task(task_id)  # type: ignore

