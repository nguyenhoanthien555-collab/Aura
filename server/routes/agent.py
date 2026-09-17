"""
Agent endpoints - the device-driven step API.

This is the wire contract of the migrated architecture. The phone no
longer runs the loop and parses action strings; it captures a fresh
observation, hands it here with the results of the previous tool calls,
and receives either structured tool-call directives or a final answer.

    device                                    server
    ------                                    ------
    capture observation ──► POST /api/agent/step
                            (record observation + fold tool reports,
                             one native-FC model round)
    ◄── directive {tool_calls | final}
    execute deterministically
    verify postconditions
    (next step)           ──► POST /api/agent/step with the envelopes

Every request carries ids; every response carries the run snapshot.
A run belongs to exactly one session and one task; nothing from another
run can answer here.

GET /runs/{run_id}  - status for diagnostics.
POST /runs/{run_id}/cancel - owner-initiated stop.
"""

import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from brain.providers.capabilities import (
    CapabilityStatus,
    capabilities_for,
    mark_function_calling_verified,
)
from brain.providers.errors import CapabilityUnavailableError
from core.ids import is_valid_id
from core.logger import logger
from core.observations import Observation, ObservationStore
from server.auth import verify_token

router = APIRouter(prefix="/api/agent", tags=["agent"])


# ----------------------------------------------------------------------
# The runtime singleton
# ----------------------------------------------------------------------

_agent_runtime = None
_device_registry = None


def configure_agent_runtime(runtime) -> None:
    """Install a runtime explicitly (tests, custom deployments)."""

    global _agent_runtime
    _agent_runtime = runtime


def get_device_registry():
    """
    The one Tool Registry that owns the android.* catalogue.

    Shared, not copied: `/api/device/invoke` resolves tools out of this
    exact registry, so the CLI harness and the agent runtime execute the
    same tool objects through the same provider and the same bridge. A
    capability that works from one therefore works from the other by
    construction rather than by two implementations agreeing.
    """

    global _device_registry

    if _device_registry is not None:
        return _device_registry

    from tools.providers.android_bridge import GatewayDeviceBridge
    from tools.providers.android_provider import AndroidProvider
    from tools.registry import ToolRegistry

    registry = ToolRegistry()

    # The gateway bridge, not the declared-only one: in deferred mode the
    # runtime hands tool calls to the phone and never invokes the bridge,
    # but `/api/device/invoke` does - and PART 5 forbids advertising an
    # android tool the real provider cannot execute.
    AndroidProvider(GatewayDeviceBridge()).register_into(registry)

    # Rehydrate active dynamic tools from SQLite
    try:
        from tools.builder.rehydrate import rehydrate_active_tools
        rehydrate_active_tools(registry)
    except Exception as exc:
        logger.warning("Dynamic tool startup rehydration encountered error: %s", exc)

    _device_registry = registry
    return _device_registry


def configure_device_registry(registry) -> None:
    """Install a registry explicitly (tests install a fake bridge)."""

    global _device_registry
    _device_registry = registry


class RouterToolCallingLLM:
    """
    Adapts whatever LLM the server process already owns to the
    generate_with_tools port.

    Selection is capability-first (brain/providers/capabilities.py):
    candidates the registry marks UNSUPPORTED are skipped before any
    request is built, and when nothing capable remains the failure is a
    CapabilityUnavailableError - named for the gap, never silently
    degraded to prose parsing, because a silent fallback would be two
    architectures alive at once.

    A real round trip through a provider whose function calling was only
    UNKNOWN promotes it to VERIFIED on success: evidence, not hope.
    """

    def __init__(self, llm):
        self.llm = llm

    def _capable(self, candidate) -> bool:
        """
        Structurally and registry-wise able to take a tool catalogue.

        The hasattr check is the structural half (the same evidence the
        registry rows were written from); the registry check is the
        declarative half, so a future declaration can rule a provider
        out even where the method exists.
        """

        if not hasattr(candidate, "generate_with_tools"):
            return False

        name = getattr(candidate, "provider_name", type(candidate).__name__)

        return capabilities_for(name).function_calling is not (
            CapabilityStatus.UNSUPPORTED
        )

    def generate_with_tools(self, system: str, messages: list, tools: list):
        candidate = self.llm

        # BrainRouter-style wrappers hold the concrete provider behind
        # them; walk one level of indirection if needed.
        if not self._capable(candidate):
            if hasattr(candidate, "provider"):
                candidate = candidate.provider
            elif hasattr(candidate, "providers"):
                for p in candidate.providers:
                    if self._capable(p):
                        candidate = p
                        break
            else:
                candidate = getattr(candidate, "_provider", None)

        if candidate is None or not self._capable(candidate):
            raise CapabilityUnavailableError(
                "no provider in the configured chain supports native "
                "function calling (needs generate_with_tools and no "
                "UNSUPPORTED declaration)"
            )

        turn = candidate.generate_with_tools(system, messages, tools)

        mark_function_calling_verified(
            getattr(candidate, "provider_name", type(candidate).__name__)
        )

        return turn


def get_agent_runtime():
    """
    The agent runtime for this process, built once.

    Deferred mode: tools are declared (so schemas reach the model), but
    execution happens on the polling device. The registry is local; the
    bridge is the phone.
    """

    global _agent_runtime

    if _agent_runtime is not None:
        return _agent_runtime

    from agent.runtime import AgentRuntime

    registry = get_device_registry()

    _agent_runtime = AgentRuntime(
        llm=_resolve_llm(),
        registry=registry,
        observations=ObservationStore(),
        system_prompt=_system_prompt(),
        deferred=True,
        max_steps=25,
    )

    logger.info(
        "Agent runtime initialised in deferred mode: %d tools declared",
        len(registry),
    )

    return _agent_runtime


def _resolve_llm():

    try:
        from server.runtime import get_runtime

        services = get_runtime().services
        conversation_llm = services.engine.conversation.llm

    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail=f"server LLM not available yet: {error}",
        )

    return RouterToolCallingLLM(conversation_llm)


def _system_prompt() -> str:
    return (
        "You are Aura's autonomous device agent. You fulfill the user's goals by "
        "calling device tools. You have full vision and control capabilities on the device.\n\n"
        "Guidelines:\n"
        "1. Compound & Multi-Step Actions: For complex requests like 'mở youtube rồi search minecraft', "
        "execute the complete flow: launch the appropriate app, wait for it or inspect UI state, find the search "
        "field/button, tap it, type the requested text, and submit with enter or search button. "
        "You can plan and execute multiple necessary tool calls.\n"
        "2. Vision & Screen Inspection: Use `android.screenshot` whenever you need visual verification "
        "or when UI elements lack clear text in the accessibility tree. The screenshot returns an automated visual "
        "description of everything on screen.\n"
        "3. Verification: Ensure actions take effect before claiming completion. Finish when the requested goal "
        "is achieved.\n"
        "4. Language & Tone: When concluding, answer helpfully in the user's language (e.g. Vietnamese when addressed in Vietnamese) "
        "confirming what was done."
    )



# ----------------------------------------------------------------------
# Wire models
# ----------------------------------------------------------------------

class ObservationIn(BaseModel):
    """One fresh measurement from the device."""

    kind: str
    source: str = "device"
    data: dict = Field(default_factory=dict)
    observation_id: str = ""
    observed_at: float = 0.0
    content_hash: str = ""


class ToolResultIn(BaseModel):
    """The device's structured report for one issued call."""

    tool_call_id: str
    call_id: str = ""
    tool: str
    arguments: dict = Field(default_factory=dict)
    ok: bool
    result: dict = Field(default_factory=dict)
    error: Optional[dict] = None
    postcondition: Optional[dict] = None
    observation_id: str = ""


class AgentStepRequest(BaseModel):

    session_id: str
    goal: str = ""                 # required when starting a run
    run_id: str = ""               # empty -> a new run starts
    observations: list[ObservationIn] = Field(default_factory=list)
    tool_results: list[ToolResultIn] = Field(default_factory=list)


# ----------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------

class IntentRequest(BaseModel):

    session_id: str = ""
    intent: str
    durable: bool = False


# ----------------------------------------------------------------------
# Natural-language intents: discovery -> capability gates -> ToolExecutor
# ----------------------------------------------------------------------

_intent_runtime = None


def configure_intent_runtime(runtime) -> None:
    """Install an intent runtime explicitly (tests, custom deployments)."""
    global _intent_runtime
    _intent_runtime = runtime


def get_intent_runtime():
    """
    The inline autonomous runtime for one natural-language intent.

    It uses the SAME tool registry, AndroidProvider and GatewayDeviceBridge
    as everything else in this process - there is no second execution
    path. Only the loop's site differs: inline, so skill discovery,
    permission/health refusals, postcondition verification and the final
    grounded reply all happen here, bounded by a low round ceiling.
    """

    global _intent_runtime

    if _intent_runtime is not None:
        return _intent_runtime

    from agent.runtime import AgentRuntime
    from tools.base import ToolRisk
    from tools.executor import ToolExecutor, ToolPolicy

    registry = get_device_registry()

    # Mirrors the device-invoke policy: authenticated use of these narrow
    # Android endpoints is the approval; capability permission, health and
    # heartbeat still gate every single call before any bridge work.
    executor = ToolExecutor(
        registry=registry,
        policy=ToolPolicy(
            enabled=True,
            allowed=frozenset(registry.names()),
            auto_approve=frozenset({
                ToolRisk.SAFE, ToolRisk.SENSITIVE, ToolRisk.DANGEROUS,
            }),
        ),
    )

    synthesis_engine = None
    synthesis_policy = None
    try:
        from brain.router import BrainRouter
        from tools.builder.builder import ToolBuilder
        from tools.builder.policy import AutonomousSynthesisPolicy
        from tools.builder.synthesis import ToolSynthesisEngine

        synthesis_llm = BrainRouter()
        builder = ToolBuilder(registry=registry)
        synthesis_engine = ToolSynthesisEngine(llm=synthesis_llm, builder=builder)
        synthesis_policy = AutonomousSynthesisPolicy()
    except Exception as exc:
        logger.warning("Autonomous synthesis initialization skipped: %s", exc)

    _intent_runtime = AgentRuntime(
        llm=_resolve_llm(),
        executor=executor,
        registry=registry,
        observations=ObservationStore(),
        system_prompt=_system_prompt(),
        deferred=False,
        max_steps=8,
        synthesis_engine=synthesis_engine,
        synthesis_policy=synthesis_policy,
    )

    logger.info(
        "Intent runtime initialised inline: %d tools declared",
        len(registry),
    )

    return _intent_runtime


@router.post("/intent")
async def agent_intent(
    request: IntentRequest,
    token: str = Depends(verify_token),
):
    """
    One natural-language intent -> discovery -> execution -> grounded
    answer.

    Nothing here trusts the model's wording: skills come from discovery
    ranked against LIVE capability state, unavailable or unauthorized
    candidates are filtered out by the registry, ToolExecutor remains the
    only thing that runs anything, and mutating actions need verified
    postconditions before completion counts. `grounded` reports whether
    the verdict rests on real executed evidence rather than prose.
    """

    from agent.runtime import RunStatus, StopReason
    from core.ids import new_session_id

    session_id = request.session_id or new_session_id()

    if request.durable:
        from agent.task_runtime import TaskStatus
        task_req = CreateTaskRequest(
            goal=request.intent,
            session_id=session_id,
            steps=None,
            run_async=False,
        )
        task_dict = await create_durable_task(task_req, token=token)
        u_resp = task_dict.get("user_response") or {}
        return {
            "session_id": session_id,
            "run_id": task_dict.get("task_id", ""),
            "task_id": task_dict.get("task_id", ""),
            "status": task_dict.get("status", ""),
            "status_detail": task_dict.get("status_detail", ""),
            "grounded": task_dict.get("status") == TaskStatus.COMPLETED.value and u_resp.get("verified", False),
            "reply": u_resp.get("text", ""),
            "user_response": u_resp,
            "task": task_dict,
        }

    runtime = get_intent_runtime()

    run = await run_in_threadpool(
        lambda: runtime.run_to_completion(
            runtime.start_run(request.intent, session_id)
        )
    )

    reply = ""
    for message in reversed(run.messages):
        content = message.get("content")
        if (
            message.get("role") == "assistant"
            and not message.get("tool_calls")
            and isinstance(content, str)
            and content.strip()
        ):
            reply = content.strip()
            break

    reply, verifier_summary = _verify_run_reply(reply, run)

    return {
        "session_id": session_id,
        "run_id": run.run_id,
        "task_id": run.task_id,
        "status": run.status.value,
        "stop_reason": run.stop_reason.value if run.stop_reason else None,
        "grounded": (
            run.status is RunStatus.COMPLETED
            and run.stop_reason is StopReason.GOAL_VERIFIED
        ),
        "reply": reply,
        "verifier": verifier_summary,
        "rounds": run.rounds,
        "tool_calls": run.tool_call_count,
        "unverified_count": len(run.unverified),
    }


def _verify_run_reply(reply: str, run) -> tuple[str, dict | None]:
    """
    Phase 4.5: the claim->evidence boundary for the intent reply.

    This route's `reply` is user-visible prose produced by the model over
    a transcript the server owns - so it gets the same verification the
    conversation path gets. The ledger is built from the run's own
    structured tool envelopes (agent/verify transcript), never from the
    reply text; the model's wording cannot ground itself here either.

    Behaviour is config-gated (`response.verify.enabled`, default on;
    `repair` off means observe-only) and never fails a run: any internal
    problem returns the reply unchanged with no summary, because a
    grounding outage must not cost the task its answer.
    """

    if not reply.strip():
        return reply, None

    try:
        from core.config import load_config

        settings = (
            ((load_config() or {}).get("response") or {}).get("verify")
            or {}
        )
    except Exception:  # noqa: BLE001 - config trouble must not break a reply
        settings = {}

    if not settings.get("enabled", True):
        return reply, None

    try:
        from brain.verify import ResponseVerifier, verify_run_reply

        result = verify_run_reply(
            reply,
            run.messages,
            request_id=run.run_id,
            verifier=ResponseVerifier(
                repair=bool(settings.get("repair", True))
            ),
        )

        summary = {
            "decision": result.decision.value,
            "claims": int(result.counts.get("claims", 0)),
            "contradicted": int(result.counts.get("contradicted", 0)),
            "unsupported": int(result.counts.get("unsupported", 0)),
            "repairs": len(result.repairs),
        }

        if result.changed:
            return result.repaired_text, summary

        return reply, summary

    except Exception as error:  # noqa: BLE001
        logger.debug("Intent reply verification skipped: %s", error)
        return reply, None


@router.post("/step")
async def agent_step(
    request: AgentStepRequest,
    token: str = Depends(verify_token),
):
    """
    One round of the loop, driven by the device.

    Accepts the fresh observation(s) and the previous calls' structured
    results; returns the next directive plus the run snapshot.
    """

    runtime = get_agent_runtime()

    if request.run_id:
        if not is_valid_id(request.run_id):
            raise HTTPException(422, "run_id is not a valid identifier")

        run = runtime.get_run(request.run_id)

        if run is None:
            raise HTTPException(404, f"unknown run {request.run_id}")

        if run.session_id != request.session_id:
            # A session may only drive its own runs - this check is what
            # makes cross-task replay structurally impossible rather
            # than merely discouraged.
            raise HTTPException(403, "run belongs to another session")

    else:
        if not request.goal.strip():
            raise HTTPException(422, "goal is required to start a run")

        run = runtime.start_run(
            goal=request.goal.strip(),
            session_id=request.session_id,
        )

    # Record the fresh observations under this run's identity first -
    # they are the state every decision this round may depend on.
    for incoming in request.observations:

        stored = Observation(
            observation_id=incoming.observation_id,
            kind=incoming.kind,
            source=incoming.source or "device",
            observed_at=incoming.observed_at or time.time(),
            content_hash=incoming.content_hash,
            data=incoming.data,
        ).with_scope(
            task_id=run.task_id,
            run_id=run.run_id,
            session_id=run.session_id,
        )

        try:
            runtime.observations.record(stored)
        except ValueError as error:
            raise HTTPException(422, str(error))

    # Fold the device's execution reports into the transcript before the
    # model sees anything - results precede reasoning.
    if request.tool_results:
        if not run.messages[-1].get("tool_calls"):
            raise HTTPException(
                409,
                "tool_results sent but the run has no pending calls",
            )
        runtime.fold_tool_reports(
            run, [result.model_dump() for result in request.tool_results]
        )

    directive_dict = None

    if run.status.value == "running":
        try:
            directive = await run_in_threadpool(runtime.advance, run)
            directive_dict = directive.to_dict()
        except RuntimeError:
            pass          # stopped mid-round by cancel; report as-is
        except Exception as error:
            logger.warning("agent step failed: %s", error)
            raise HTTPException(502, f"model round failed: {error}")

    snapshot = run.snapshot()

    if directive_dict is not None:
        snapshot["directive"] = directive_dict

    return snapshot


@router.get("/runs/{run_id}")
async def get_run(run_id: str, token: str = Depends(verify_token)):

    runtime = get_agent_runtime()
    run = runtime.get_run(run_id)

    if run is None:
        raise HTTPException(404, f"unknown run {run_id}")

    return {
        **run.snapshot(),
        "rounds": run.rounds,
        "messages": len(run.messages),
    }


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, token: str = Depends(verify_token)):

    runtime = get_agent_runtime()

    if not runtime.cancel(run_id):
        raise HTTPException(409, "run not running or unknown")

    # Whatever this run had queued for the phone dies with it. Without
    # this the run is cancelled while its invocations stay pending, and
    # the handset executes an action for a task nobody is waiting on -
    # the orphaned-operation failure TEST I looks for.
    from server.device_gateway import get_device_gateway

    orphaned = get_device_gateway().cancel_run(run_id)

    return {
        "cancelled": True,
        "run_id": run_id,
        "device_invocations_cancelled": orphaned,
    }


# ----------------------------------------------------------------------
# Phase 5B: Durable Tasks & Dynamic Tool APIs
# ----------------------------------------------------------------------

class CreateTaskRequest(BaseModel):
    goal: str
    session_id: str = "default"
    steps: Optional[list[dict]] = None
    metadata: dict = Field(default_factory=dict)
    run_async: bool = True
    durable_clarification: bool = False


class ConfirmTaskRequest(BaseModel):
    confirmation_id: Optional[str] = None
    decision: str = "APPROVED"  # APPROVED or REJECTED
    decision_by: str = "human_operator"
    reason: Optional[str] = None


class ClarifyTaskRequest(BaseModel):
    clarification_id: Optional[str] = None
    answers: dict = Field(default_factory=dict)
    clarified_goal: Optional[str] = None


class SettleStepRequest(BaseModel):
    step_id: str
    ok: bool
    evidence: list[dict] = Field(default_factory=list)
    result: dict = Field(default_factory=dict)
    error: str = ""


_task_runtime_instance = None


def configure_task_runtime(runtime) -> None:
    """Install a TaskRuntime explicitly (tests, custom deployments)."""
    global _task_runtime_instance
    _task_runtime_instance = runtime


def get_task_runtime():
    global _task_runtime_instance
    if _task_runtime_instance is None:
        from agent.task_runtime import TaskRuntime
        try:
            from server.runtime import get_runtime
            bus = get_runtime().bus
        except Exception:
            bus = None

        synthesis_engine = None
        synthesis_policy = None
        try:
            from brain.router import BrainRouter
            from tools.builder.builder import ToolBuilder
            from tools.builder.policy import AutonomousSynthesisPolicy
            from tools.builder.synthesis import ToolSynthesisEngine

            registry = get_device_registry()
            synthesis_llm = BrainRouter()
            builder = ToolBuilder(registry=registry)
            synthesis_engine = ToolSynthesisEngine(llm=synthesis_llm, builder=builder)
            synthesis_policy = AutonomousSynthesisPolicy()
        except Exception as exc:
            logger.warning("TaskRuntime synthesis initialization skipped: %s", exc)

        _task_runtime_instance = TaskRuntime(
            bus=bus,
            synthesis_engine=synthesis_engine,
            synthesis_policy=synthesis_policy,
        )
    return _task_runtime_instance


@router.post("/tasks")
async def create_durable_task(
    request: CreateTaskRequest,
    token: str = Depends(verify_token),
):
    """
    Creates and optionally triggers a compound durable task.
    When run_async=True, runs in background and survives connection drop.
    """
    if not request.goal.strip():
        raise HTTPException(422, "goal is required")

    task_runtime = get_task_runtime()
    steps = list(request.steps) if request.steps else []

    if request.steps is None:
        try:
            from brain.router import BrainRouter
            from tools.schema import to_json_schema
            llm = BrainRouter()
            registry = get_device_registry()
            catalogue = []
            for t in registry.all():
                if not registry.is_active(getattr(t, "name", "")):
                    continue
                try:
                    p_schema = to_json_schema(t)
                except Exception:
                    p_schema = getattr(t, "parameters", {})
                    if not isinstance(p_schema, dict):
                        p_schema = {}
                risk_val = getattr(t, "risk", "safe")
                if hasattr(risk_val, "value"):
                    risk_val = risk_val.value
                side_val = getattr(t, "side_effect", "unknown")
                if hasattr(side_val, "value"):
                    side_val = side_val.value
                catalogue.append({
                    "name": getattr(t, "name", ""),
                    "description": getattr(t, "description", ""),
                    "capability": getattr(t, "capability", getattr(t, "name", "")),
                    "parameters": p_schema,
                    "risk": str(risk_val).lower(),
                    "side_effect": str(side_val).lower(),
                    "verification_supported": bool(hasattr(t, "verify") and callable(t.verify)),
                })
            plan = task_runtime.planner.plan_from_goal(
                goal=request.goal.strip(),
                llm=llm,
                tools_catalogue=catalogue,
                metadata=request.metadata or {},
            )
            if getattr(plan, "needs_clarification", False) or getattr(plan, "status", "") == "NEEDS_CLARIFICATION":
                is_durable = request.durable_clarification or bool(
                    request.metadata.get("durable_clarification", False)
                )
                if not is_durable:
                    return {
                        "status": "NEEDS_CLARIFICATION",
                        "status_detail": "WAITING_FOR_CLARIFICATION",
                        "goal": request.goal.strip(),
                        "questions": plan.questions,
                        "user_response": {
                            "state": "WAITING_FOR_CLARIFICATION",
                            "text": f"Clarification needed: {' '.join(plan.questions)}",
                            "verified": False,
                            "questions": plan.questions,
                        },
                    }
                else:
                    from agent.task_runtime import TaskStatus
                    task = task_runtime.create_task(
                        goal=request.goal.strip(),
                        session_id=request.session_id,
                        metadata=request.metadata,
                        steps=[],
                    )
                    task_runtime.update_task_status(
                        task.task_id,
                        TaskStatus.WAITING.value,
                        error="Waiting for clarification",
                        recovery_state="WAITING_FOR_CLARIFICATION",
                    )
                    clar_req = task_runtime.create_clarification_request(
                        task_id=task.task_id,
                        goal=request.goal.strip(),
                        questions=plan.questions,
                    )
                    t_dict = task_runtime.get_task(task.task_id).to_dict()
                    t_dict.update({
                        "status_detail": "WAITING_FOR_CLARIFICATION",
                        "clarification_id": clar_req.clarification_id,
                        "questions": plan.questions,
                    })
                    return t_dict
            steps = plan.steps
        except ValueError as e:
            raise HTTPException(400, f"Plan decomposition/validation failed: {e}")
        except Exception as e:
            logger.error("Planner failed for goal '%s': %s", request.goal, e)
            raise HTTPException(500, f"Plan decomposition failed: {e}")

    task = task_runtime.create_task(
        goal=request.goal.strip(),
        session_id=request.session_id,
        metadata=request.metadata,
        steps=steps,
    )

    if steps:
        from tools.base import ToolRisk
        from tools.executor import ToolExecutor, ToolPolicy
        registry = get_device_registry()
        executor = ToolExecutor(
            registry=registry,
            policy=ToolPolicy(
                enabled=True,
                allowed=frozenset(registry.names()),
                auto_approve=frozenset({
                    ToolRisk.SAFE, ToolRisk.SENSITIVE,
                }),
            ),
        )
        if request.run_async:
            task_runtime.execute_compound_task_async(task.task_id, executor)
        else:
            task = await run_in_threadpool(
                lambda: task_runtime.execute_compound_task(task.task_id, executor)
            )

    return task.to_dict()


@router.get("/tasks")
async def list_durable_tasks(
    status: Optional[str] = None,
    limit: int = 50,
    token: str = Depends(verify_token),
):
    """Lists durable tasks."""
    task_runtime = get_task_runtime()
    tasks = task_runtime.list_tasks(status=status, limit=limit)
    return [t.to_dict() for t in tasks]


@router.get("/tasks/{task_id}")
async def get_durable_task(
    task_id: str,
    token: str = Depends(verify_token),
):
    """Inspects a durable task, its steps, and its recovery state."""
    task_runtime = get_task_runtime()
    task = task_runtime.get_task(task_id)
    if not task:
        raise HTTPException(404, f"unknown task {task_id}")
    return task.to_dict()


@router.post("/tasks/{task_id}/pause")
async def pause_durable_task(
    task_id: str,
    token: str = Depends(verify_token),
):
    task_runtime = get_task_runtime()
    if not task_runtime.pause_task(task_id):
        raise HTTPException(409, f"task {task_id} not running or cannot be paused")
    return {"paused": True, "task_id": task_id}


@router.post("/tasks/{task_id}/resume")
async def resume_durable_task(
    task_id: str,
    token: str = Depends(verify_token),
):
    task_runtime = get_task_runtime()
    task, steps = task_runtime.resume_task(task_id)
    if not task:
        raise HTTPException(404, f"unknown task {task_id}")

    from tools.base import ToolRisk
    from tools.executor import ToolExecutor, ToolPolicy
    registry = get_device_registry()
    executor = ToolExecutor(
        registry=registry,
        policy=ToolPolicy(
            enabled=True,
            allowed=frozenset(registry.names()),
            auto_approve=frozenset({
                ToolRisk.SAFE, ToolRisk.SENSITIVE,
            }),
        ),
    )
    task_runtime.execute_compound_task_async(task_id, executor)
    return {"resumed": True, "task_id": task_id}


@router.post("/tasks/{task_id}/cancel")
async def cancel_durable_task(
    task_id: str,
    reason: str = "User requested cancellation",
    token: str = Depends(verify_token),
):
    task_runtime = get_task_runtime()
    if not task_runtime.cancel_task(task_id, reason=reason):
        raise HTTPException(409, f"task {task_id} already terminal or unknown")
    return {"cancelled": True, "task_id": task_id}


@router.post("/tasks/{task_id}/settle")
async def settle_durable_step(
    task_id: str,
    request: SettleStepRequest,
    token: str = Depends(verify_token),
):
    """
    Settle an ambiguous mutation step using verified late evidence or postcondition.
    """
    task_runtime = get_task_runtime()
    step = task_runtime.settle_ambiguous_step(
        task_id=task_id,
        step_id=request.step_id,
        ok=request.ok,
        result=request.result,
        evidence=request.evidence,
        error=request.error,
    )
    if not step:
        raise HTTPException(404, f"unknown step {request.step_id}")
    return step.to_dict()


@router.post("/tasks/{task_id}/confirm")
async def confirm_durable_task(
    task_id: str,
    request: ConfirmTaskRequest,
    token: str = Depends(verify_token),
):
    """
    Approves or rejects a pending human confirmation for a dangerous/sensitive step.
    If approved, resumes task execution from the confirmed step.
    If rejected, marks step and task as failed/rejected without executing the tool.
    """
    task_runtime = get_task_runtime()
    task = task_runtime.get_task(task_id)
    if not task:
        raise HTTPException(404, f"unknown task {task_id}")

    try:
        conf = task_runtime.resolve_confirmation(
            task_id=task_id,
            decision=request.decision,
            confirmation_id=request.confirmation_id,
            decision_by=request.decision_by,
            reason=request.reason,
        )
    except ValueError as val_err:
        raise HTTPException(409, str(val_err))

    if conf.status == "APPROVED":
        from tools.base import ToolRisk
        from tools.executor import ToolExecutor, ToolPolicy
        registry = get_device_registry()
        executor = ToolExecutor(
            registry=registry,
            policy=ToolPolicy(
                enabled=True,
                allowed=frozenset(registry.names()),
                auto_approve=frozenset({
                    ToolRisk.SAFE, ToolRisk.SENSITIVE,
                }),
            ),
        )
        task_runtime.execute_compound_task_async(task_id, executor)
        return {
            "confirmed": True,
            "task_id": task_id,
            "confirmation_id": conf.confirmation_id,
            "decision": "APPROVED",
            "status": "RESUMING",
        }
    else:
        return {
            "confirmed": False,
            "task_id": task_id,
            "confirmation_id": conf.confirmation_id,
            "decision": conf.decision,
            "status": "REJECTED",
        }


@router.post("/tasks/{task_id}/clarify")
async def clarify_durable_task(
    task_id: str,
    request: ClarifyTaskRequest,
    token: str = Depends(verify_token),
):
    """
    Answers an ambiguous goal clarification request, updating the durable task
    and resuming planning / execution with the provided answers.
    """
    task_runtime = get_task_runtime()
    task = task_runtime.get_task(task_id)
    if not task:
        raise HTTPException(404, f"unknown task {task_id}")

    try:
        resolved_clar = task_runtime.resolve_clarification(
            task_id=task_id,
            clarification_id=request.clarification_id,
            answers=request.answers,
            clarified_goal=request.clarified_goal,
        )
    except ValueError as val_err:
        raise HTTPException(409, str(val_err))

    from brain.router import BrainRouter
    from tools.schema import to_json_schema
    from tools.base import ToolRisk
    from tools.executor import ToolExecutor, ToolPolicy
    from tools.outcome import SideEffect
    from agent.task_runtime import TaskStatus

    llm = BrainRouter()
    registry = get_device_registry()
    catalogue = []
    for t in registry.all():
        if not registry.is_active(getattr(t, "name", "")):
            continue
        try:
            p_schema = to_json_schema(t)
        except Exception:
            p_schema = getattr(t, "parameters", {})
            if not isinstance(p_schema, dict):
                p_schema = {}
        risk_val = getattr(t, "risk", "safe")
        if hasattr(risk_val, "value"):
            risk_val = risk_val.value
        side_val = getattr(t, "side_effect", "unknown")
        if hasattr(side_val, "value"):
            side_val = side_val.value
        catalogue.append({
            "name": getattr(t, "name", ""),
            "description": getattr(t, "description", ""),
            "capability": getattr(t, "capability", getattr(t, "name", "")),
            "parameters": p_schema,
            "risk": str(risk_val).lower(),
            "side_effect": str(side_val).lower(),
            "verification_supported": bool(hasattr(t, "verify") and callable(t.verify)),
        })

    if request.clarified_goal:
        combined_goal = request.clarified_goal.strip()
    else:
        ans_parts = [f"{k}: {v}" for k, v in request.answers.items()]
        combined_goal = f"{task.goal} ({'; '.join(ans_parts)})"

    plan = task_runtime.planner.plan_from_goal(
        goal=combined_goal,
        llm=llm,
        tools_catalogue=catalogue,
        metadata=task.metadata,
    )

    if getattr(plan, "needs_clarification", False) or getattr(plan, "status", "") == "NEEDS_CLARIFICATION":
        new_clar = task_runtime.create_clarification_request(
            task_id=task_id,
            goal=combined_goal,
            questions=plan.questions,
        )
        task_runtime.update_task_status(task_id, TaskStatus.WAITING.value, error="Further clarification required")
        return {
            "task_id": task_id,
            "status": "WAITING_FOR_CLARIFICATION",
            "goal": combined_goal,
            "clarification_id": new_clar.clarification_id,
            "questions": plan.questions,
        }

    for idx, s in enumerate(plan.steps):
        task_runtime.add_step(
            task_id=task_id,
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

    task_runtime.update_task_status(task_id, TaskStatus.READY.value)
    executor = ToolExecutor(
        registry=registry,
        policy=ToolPolicy(
            enabled=True,
            allowed=frozenset(registry.names()),
            auto_approve=frozenset({
                ToolRisk.SAFE, ToolRisk.SENSITIVE,
            }),
        ),
    )
    task_runtime.execute_compound_task_async(task_id, executor)
    return {
        "clarified": True,
        "task_id": task_id,
        "clarification_id": resolved_clar.clarification_id,
        "status": "READY",
        "steps_count": len(plan.steps),
    }


@router.get("/tasks/{task_id}/confirmations")
async def list_task_confirmations(
    task_id: str,
    token: str = Depends(verify_token),
):
    task_runtime = get_task_runtime()
    task = task_runtime.get_task(task_id)
    if not task:
        raise HTTPException(404, f"unknown task {task_id}")
    confs = task_runtime.list_confirmations_for_task(task_id)
    return [c.to_dict() for c in confs]


@router.get("/tasks/{task_id}/clarifications")
async def list_task_clarifications(
    task_id: str,
    token: str = Depends(verify_token),
):
    task_runtime = get_task_runtime()
    task = task_runtime.get_task(task_id)
    if not task:
        raise HTTPException(404, f"unknown task {task_id}")
    clars = task_runtime.list_clarifications_for_task(task_id)
    return [c.to_dict() for c in clars]


@router.get("/tasks/{task_id}/steps")
async def list_task_steps(
    task_id: str,
    token: str = Depends(verify_token),
):
    task_runtime = get_task_runtime()
    task = task_runtime.get_task(task_id)
    if not task:
        raise HTTPException(404, f"unknown task {task_id}")
    steps = task_runtime.list_steps(task_id)
    return [s.to_dict() for s in steps]


# --- Dynamic Tools Endpoints ---

@router.get("/tools/dynamic")
async def list_dynamic_tools_endpoint(
    token: str = Depends(verify_token),
):
    registry = get_device_registry()
    tools = []
    for name in registry.names():
        tool = registry.get(name)
        provenance = registry.get_provenance(name) or {}
        if provenance or getattr(tool, "is_dynamic", False):
            tools.append({
                "name": name,
                "version": registry.get_version(name),
                "is_active": registry.is_active(name),
                "is_revoked": registry.is_revoked(name),
                "provenance": provenance,
            })
    return {"tools": tools}


@router.get("/tools/dynamic/{tool_name}")
async def get_dynamic_tool_endpoint(
    tool_name: str,
    token: str = Depends(verify_token),
):
    registry = get_device_registry()
    tool = registry.get(tool_name)
    if not tool:
        raise HTTPException(404, f"unknown tool {tool_name}")
    return {
        "name": tool_name,
        "version": registry.get_version(tool_name),
        "is_active": registry.is_active(tool_name),
        "is_revoked": registry.is_revoked(tool_name),
        "provenance": registry.get_provenance(tool_name) or {},
        "risk": getattr(tool, "risk", "safe"),
    }


@router.post("/tools/dynamic/{tool_name}/approve")
async def approve_dynamic_tool_endpoint(
    tool_name: str,
    approver: str = "operator",
    token: str = Depends(verify_token),
):
    registry = get_device_registry()
    tool = registry.get(tool_name)
    if not tool:
        raise HTTPException(404, f"unknown tool {tool_name}")
    provenance = registry.get_provenance(tool_name) or {}
    provenance["approved_by"] = approver
    provenance["approved_at"] = time.time()
    registry.activate(tool_name)
    return {"approved": True, "tool_name": tool_name, "approver": approver}


@router.post("/tools/dynamic/{tool_name}/revoke")
async def revoke_dynamic_tool_endpoint(
    tool_name: str,
    reason: str = "Revoked by operator",
    token: str = Depends(verify_token),
):
    registry = get_device_registry()
    if not registry.revoke(tool_name, reason=reason):
        raise HTTPException(404, f"unknown tool {tool_name}")
    return {"revoked": True, "tool_name": tool_name, "reason": reason}


@router.post("/tools/dynamic/{tool_name}/rollback")
async def rollback_dynamic_tool_endpoint(
    tool_name: str,
    target_version: int = 1,
    token: str = Depends(verify_token),
):
    registry = get_device_registry()
    if not registry.rollback(tool_name):
        raise HTTPException(409, f"cannot rollback tool {tool_name}")
    return {"rolled_back": True, "tool_name": tool_name, "current_version": registry.get_version(tool_name)}