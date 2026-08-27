"""
Device endpoints - the polling transport of the tool protocol.

Three ends of one queue (see server/device_gateway.py):

    POST /api/device/invoke   caller-side: queue an invocation, wait for
                              the device's structured report
    POST /api/device/poll     device-side: fetch the next queued request
    POST /api/device/results  device-side: deliver a structured report

Authentication is the existing bearer token on every route - the same
token chat uses - so an arbitrary HTTP caller cannot drive the phone.
Tokens are never logged.
"""

import threading
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from server.auth import verify_token
from server.device_gateway import (
    configure_device_gateway,
    get_device_gateway,
)

router = APIRouter(prefix="/api/device", tags=["device"])

_device_executor = None


def get_device_executor():
    """The only server-side executor allowed to submit Android work."""
    global _device_executor
    if _device_executor is None:
        from tools.base import ToolRisk
        from tools.executor import ToolExecutor, ToolPolicy
        from server.routes.agent import get_device_registry

        registry = get_device_registry()
        _device_executor = ToolExecutor(
            registry=registry,
            policy=ToolPolicy(
                enabled=True,
                allowed=frozenset(registry.names()),
                # An authenticated device invocation is the explicit
                # approval for this narrow, non-shell Android tool set.
                # Capability permission and runtime health still gate every
                # call before the bridge is touched.
                auto_approve=frozenset({
                    ToolRisk.SAFE, ToolRisk.SENSITIVE, ToolRisk.DANGEROUS,
                }),
            ),
        )
    return _device_executor


class InvokeRequest(BaseModel):

    run_id: str = ""
    tool_call_id: str = ""
    tool: str
    arguments: dict = Field(default_factory=dict)
    timeout_s: float = Field(default=30.0, gt=0, le=120)


class PollRequest(BaseModel):
    """The device identifies itself so logs can distinguish handsets."""

    device_id: str = ""
    timeout_s: float = Field(default=0.0, ge=0.0, le=30.0)
    capabilities: dict = Field(default_factory=dict)


class ResultReport(BaseModel):

    invocation_id: str
    ok: bool = False
    result: Optional[dict] = None
    error: Optional[dict] = None
    postcondition: Optional[dict] = None
    # Device failures are allowed to have no observation.  The Android
    # protocol deliberately leaves this null when validation fails before a
    # screen read; rejecting that report would strand the invocation in the
    # poll queue and cause an endless retry loop.
    observation_id: Optional[str] = None
    observation: Optional[dict] = None


class ResultSubmission(BaseModel):
    """The body the Android app posts after executing an invocation."""

    device_id: str = ""
    reports: list[ResultReport] = Field(default_factory=list)


@router.post("/invoke")
async def invoke(
    request: InvokeRequest,
    token: str = Depends(verify_token),
):
    """
    Execute one android.* tool on the connected device.

    The call is resolved out of the shared Tool Registry and executed
    through the AndroidProvider - the same tool object, provider and
    bridge the agent runtime uses - so this endpoint is a transport for
    the one execution path rather than a second implementation of it.

    Blocks up to `timeout_s` while the polling handset picks the
    invocation up and answers. The body is always the structured
    envelope: success, a tool-level failure, or a gateway-authored
    TIMEOUT. Never prose, and never an exception crossing the boundary.
    """

    from core.ids import is_valid_id
    from server.routes.agent import get_device_registry

    if request.run_id and not is_valid_id(request.run_id):
        raise HTTPException(422, "run_id is not a valid identifier")

    if request.tool_call_id and not is_valid_id(request.tool_call_id):
        raise HTTPException(422, "tool_call_id is not a valid identifier")

    if not request.tool.startswith("android."):
        raise HTTPException(
            422, "only android.* tools are executable on a device"
        )

    envelope = {
        "run_id": request.run_id,
        "tool_call_id": request.tool_call_id,
        "tool": request.tool,
    }

    # A cancelled or finished run may not reach the device. Its answer
    # would be folded into a transcript nobody is advancing, which is the
    # stale-run half of the state-isolation rule.
    stale = _stale_run_reason(request.run_id)

    if stale is not None:
        return JSONResponse(status_code=409, content={
            **envelope, "ok": False,
            "error": {"code": "STALE_RUN", "message": stale},
        })

    registry = get_device_registry()
    tool = registry.get(request.tool)

    if tool is None:
        return JSONResponse(status_code=404, content={
            **envelope, "ok": False,
            "error": {
                "code": "TOOL_NOT_FOUND",
                "message": f"{request.tool} is not a registered device tool",
            },
        })

    report = await run_in_threadpool(
        _execute_scoped,
        get_device_executor(),
        request.tool,
        dict(request.arguments),
        request.run_id,
        request.tool_call_id,
        request.timeout_s,
    )

    body = {**envelope, **report, "tool": request.tool}

    # A device timeout is a 504 so infrastructure (proxies, retries) sees
    # the failure class; every structured tool-level outcome - success or
    # failure - is a 200 whose envelope carries the verdict.
    code = (body.get("error") or {}).get("code")

    if code == "TIMEOUT":
        return JSONResponse(status_code=504, content=body)

    if code == "CANCELLED":
        return JSONResponse(status_code=409, content=body)

    return body


def _stale_run_reason(run_id: str) -> Optional[str]:
    """
    Why this run may not drive the device, or None if it may.

    An unknown run id is deliberately allowed: the CLI harness drives the
    device with no run at all, and refusing that would make the tool
    protocol reachable only from inside an agent run.
    """

    if not run_id:
        return None

    try:
        from server.routes.agent import get_agent_runtime

        run = get_agent_runtime().get_run(run_id)
    except Exception:            # no runtime configured in this process
        return None

    if run is None:
        return None

    status = getattr(run.status, "value", str(run.status))

    if status != "running":
        return f"run {run_id} is {status}"

    return None


def _execute_scoped(
    executor,
    tool_name: str,
    arguments: dict,
    run_id: str,
    tool_call_id: str,
    timeout_s: float,
) -> dict:
    """
    Run the tool with the device scope bound, in a worker thread.

    The scope is what lets the gateway attribute a queued invocation to
    its run, so cancelling that run resolves it instead of orphaning it.
    It is reset afterwards because this thread is reused.
    """

    from tools.providers.android_bridge import device_call_scope

    token = device_call_scope.set((run_id, tool_call_id, timeout_s))

    try:
        result = executor.execute(tool_name, arguments)
    finally:
        device_call_scope.reset(token)

    # The AndroidProvider keeps the whole structured report in `data`;
    # that report - not the rendered output - is what the caller gets.
    report = getattr(result, "data", None)

    if isinstance(report, dict) and ("ok" in report):
        return report

    if getattr(result, "ok", False):
        return {"ok": True, "result": {"output": getattr(result, "output", "")}}

    error_code = "EXECUTION_FAILED"
    if getattr(result, "capability", "") and getattr(result, "authorization", "") == "missing":
        error_code = "BLOCKED_PERMISSION"
    elif getattr(result, "execution", "") == "not_attempted":
        error_code = "CAPABILITY_UNAVAILABLE"

    return {
        "ok": False,
        "error": {
            "code": error_code,
            "message": str(getattr(result, "error", "") or "tool failed"),
        },
    }


@router.post("/poll")
async def poll(
    request: PollRequest,
    token: str = Depends(verify_token),
):
    """
    The device asks for work. One invocation or none.

    Supports long polling: when timeout_s > 0, the server waits up to
    timeout_s on the gateway condition rather than answering empty
    immediately, eliminating poll interval latency when a tool is queued.
    """

    gateway = get_device_gateway()
    gateway.heartbeat(request.device_id, request.capabilities)
    pending = await run_in_threadpool(gateway.poll, request.timeout_s)

    if pending is None:
        return {"invocations": []}

    return {"invocations": [pending.to_dict()]}


@router.post("/results")
async def results(
    request: ResultSubmission,
    token: str = Depends(verify_token),
):
    """
    The device answers invocations. Each report is correlated by its
    invocation id; unknown ids are refused rather than absorbed.
    """

    gateway = get_device_gateway()

    accepted, rejected = 0, []

    for report in request.reports:
        payload = {
            "ok": report.ok,
            "result": report.result or {},
            "error": report.error,
            "postcondition": report.postcondition,
            "observation_id": report.observation_id,
            "observation": report.observation,
        }

        if gateway.complete(report.invocation_id, payload):
            accepted += 1
        else:
            rejected.append(report.invocation_id)

    return {"accepted": accepted, "rejected": rejected}
