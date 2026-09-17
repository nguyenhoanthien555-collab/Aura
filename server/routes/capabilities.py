from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from server.auth import verify_token
from core.capabilities.introspection import get_introspection_service

router = APIRouter(prefix="/api/capabilities", tags=["capabilities"])

class SynthesizeCapabilityRequest(BaseModel):
    intent: str
    required_capability: Optional[str] = None
    threshold: float = 0.5
    approver: str = "operator"
    auto_promote: bool = True


@router.get("")
async def list_capabilities(token: str = Depends(verify_token)):
    """
    Returns the live capability inventory.
    """
    service = get_introspection_service()
    return service.get_inventory()


@router.get("/gap")
async def evaluate_capability_gap(
    intent: str,
    threshold: float = 0.5,
    token: str = Depends(verify_token),
):
    """
    Evaluates an intent against the capability registry to detect gaps,
    partial matches, or blocked dependencies.
    """
    from core.capabilities.gap import CapabilityGapEngine
    engine = CapabilityGapEngine()
    match_state, gap = engine.evaluate_capability(intent, threshold=threshold)
    return {
        "intent": intent,
        "match_state": match_state.value if hasattr(match_state, "value") else str(match_state),
        "gap": gap.to_dict() if gap else None,
    }


@router.post("/synthesize")
async def synthesize_capability_endpoint(
    request: SynthesizeCapabilityRequest,
    token: str = Depends(verify_token),
):
    """
    Synthesizes a missing capability end-to-end:
    Intent -> CapabilityGap -> LLM Synthesis -> AST Validation -> Sandbox -> Promotion.
    """
    from core.capabilities.gap import CapabilityGapEngine
    from server.routes.agent import get_device_registry
    from tools.builder.builder import ToolBuilder
    from tools.builder.synthesis import ToolSynthesisEngine, ToolSynthesisRequest

    gap_engine = CapabilityGapEngine()
    gap = gap_engine.detect_gap(
        request.intent,
        threshold=request.threshold,
        required_capability=request.required_capability,
    )
    if not gap:
        return {
            "synthesized": False,
            "status": "NO_GAP",
            "message": "Capability already exists or request is ambiguous",
        }

    # Resolve active LLM
    try:
        from server.runtime import get_runtime
        runtime = get_runtime()
        llm = runtime.services.engine.conversation.llm
    except Exception:
        from brain.router import BrainRouter
        llm = BrainRouter()

    registry = get_device_registry()
    builder = ToolBuilder(registry=registry)
    synthesis_engine = ToolSynthesisEngine(llm=llm, builder=builder)

    eligible, reason = synthesis_engine.is_eligible_for_synthesis(gap)
    if not eligible:
        return {
            "synthesized": False,
            "status": "INELIGIBLE",
            "reason": reason,
            "gap": gap.to_dict(),
        }

    tool_inst, report, synth_res = synthesis_engine.synthesize_and_promote(
        gap, approver=request.approver
    )

    if not synth_res.success or not tool_inst:
        return {
            "synthesized": False,
            "status": "REJECTED",
            "errors": report.errors if report else [synth_res.failure_reason],
            "gap": gap.to_dict(),
            "synthesis": synth_res.to_dict(),
        }

    return {
        "synthesized": True,
        "status": "ACTIVE",
        "tool_name": tool_inst.name,
        "version": getattr(tool_inst, "version", 1),
        "digest": getattr(synth_res.manifest, "source_digest", ""),
        "capability": gap.requested_capability,
        "validation": report.to_dict(),
        "gap": gap.to_dict(),
    }
