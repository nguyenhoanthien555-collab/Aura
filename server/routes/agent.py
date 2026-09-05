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
        "You are Aura's device agent. You complete the user's goal by "
        "calling tools. Before any state-dependent action, ensure a "
        "fresh observation exists (the device sends one each step). "
        "After actions that change the screen, wait_for or verify the "
        "expected state before continuing. Finish only when the goal is "
        "achieved AND verified. Answer questions about current device "
        "state strictly from what your observations returned - never "
        "from assumption - and if nothing observable supports you, say "
        "exactly what blocked you."
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


# ----------------------------------------------------------------------
# Natural-language intents: discovery -> capability gates -> ToolExecutor
# ----------------------------------------------------------------------

_intent_runtime = None


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

    _intent_runtime = AgentRuntime(
        llm=_resolve_llm(),
        executor=executor,
        registry=registry,
        observations=ObservationStore(),
        system_prompt=_system_prompt(),
        deferred=False,
        max_steps=8,
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

    runtime = get_intent_runtime()

    session_id = request.session_id or new_session_id()

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
            directive = runtime.advance(run)
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