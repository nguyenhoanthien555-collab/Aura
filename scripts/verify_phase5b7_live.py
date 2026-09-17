"""
AURA 2.0 — Phase 5B.7 Live Laptop End-to-End Verification Script.

Interactive Confirmation + Clarification + Durable Human-in-the-Loop.

Verifies against live Gemini provider and real SQLite database:
LIVE-1: Normal Schema-Grounded Task (Fibonacci 10 -> 55)
LIVE-2: Dangerous Action Pauses in WAITING with Durable Confirmation Record
LIVE-3: Sensitive Parameter Redaction in SQLite Confirmation Record
LIVE-4: Human Approval via API Resumes and Executes Dangerous Step to Completion
LIVE-5: Single-Use Confirmation Consumption (CONSUMED status, Replay Conflict)
LIVE-6: Human Rejection via API Rejects Step and Fails Task Without Tool Execution
LIVE-7: Argument Tampering Detection (Argument Mismatch Invalidates Confirmation)
LIVE-8: Ambiguous Goal with Durable Clarification Creates WAITING Task and Clarification Record
LIVE-9: Human Clarification via API Resumes Planning and Compound Execution
LIVE-10: Crash / Restart Recovery Across Confirmation Boundary (No Blind Replay)
"""

import json
import os
from pathlib import Path
import sys
import tempfile
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
load_dotenv(REPO_ROOT / ".env")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

from agent.task_runtime import (
    DurableConfirmation,
    DurableClarification,
    DurableStep,
    DurableTask,
    StepStatus,
    TaskPlan,
    TaskRuntime,
    TaskStatus,
)
from brain.router import BrainRouter
from core.capabilities import registry as global_cap_registry, Capability
from core.capabilities.discovery import SkillDiscovery
from core.capabilities.gap import CapabilityGapEngine
from memory.models import (
    DurableConfirmationRecord,
    DurableClarificationRecord,
    DurableStepRecord,
    DurableTaskRecord,
)
from memory.sqlite import init_task_tables
from server.main import app
from server.routes.agent import configure_device_registry, configure_task_runtime
from tools.base import Parameter, Tool, ToolResult, ToolRisk
from tools.builtins.clock import CurrentTimeTool
from tools.builder.builder import ToolBuilder
from tools.builder.policy import AutonomousSynthesisPolicy
from tools.builder.synthesis import ToolSynthesisEngine
from tools.executor import ToolExecutor, ToolPolicy
from tools.outcome import Evidence, EvidenceKind, SideEffect, ToolStatus as OutcomeToolStatus
from tools.registry import ToolRegistry


def ensure_test_capabilities():
    for cid, cname in [
        ("system.wipe", "System Wipe Tool"),
        ("test.echo", "Echo Tool"),
        ("test.dangerous_wipe", "Dangerous Wipe Tool"),
    ]:
        if not global_cap_registry.get(cid):
            global_cap_registry.register(Capability(capability_id=cid, name=cname, description=cname, category="test"))

ensure_test_capabilities()


def print_banner(text: str):
    print("\n" + "=" * 70, flush=True)
    print(f"  {text}", flush=True)
    print("=" * 70, flush=True)


class LiveDangerousWipeTool(Tool):
    name = "system_wipe_cache"
    capability = "system.wipe"
    risk = ToolRisk.DANGEROUS
    side_effect = SideEffect.NON_IDEMPOTENT
    parameters = (
        Parameter(name="target_dir", type="string", required=True),
        Parameter(name="auth_token", type="string", required=False),
        Parameter(name="secret_key", type="string", required=False),
    )

    def __init__(self):
        super().__init__()
        self.call_count = 0
        self.last_target = ""

    def execute(self, **arguments) -> ToolResult:
        self.call_count += 1
        self.last_target = arguments.get("target_dir", "/tmp")
        return ToolResult(
            ok=True,
            output=f"Wiped cache at {self.last_target}",
            data={"target_dir": self.last_target, "wiped": True},
            status=OutcomeToolStatus.SUCCESS.value,
            side_effect=SideEffect.NON_IDEMPOTENT.value,
        )

    def verify(self, **arguments) -> ToolResult:
        target = arguments.get("target_dir", "")
        return ToolResult(
            ok=True,
            data={"verified_wiped": True, "target": target},
            status=OutcomeToolStatus.SUCCESS.value,
        )


def main():
    print_banner("AURA 2.0 — PHASE 5B.7 LIVE LAPTOP E2E VERIFICATION")
    print(f"Repository: {REPO_ROOT}", flush=True)
    print(f"Python:     {sys.version.split()[0]}", flush=True)
    print(f"Platform:   {sys.platform}", flush=True)

    if not os.getenv("GEMINI_API_KEY"):
        print("[FAIL] GEMINI_API_KEY is not set in environment or .env file.", flush=True)
        print("Live verification requires valid LLM credentials.", flush=True)
        sys.exit(1)

    # 1. Setup isolated database
    temp_dir = tempfile.mkdtemp(prefix="aura_phase5b7_live_")
    db_path = Path(temp_dir) / "aura_live_phase5b7.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    init_task_tables(engine)
    session_factory = sessionmaker(bind=engine)
    print(f"[OK] Initialized isolated database: {db_path}", flush=True)

    # 2. Setup ToolRegistry with baseline tools and dangerous tool
    live_registry = ToolRegistry()
    clock_tool = CurrentTimeTool()
    dangerous_tool = LiveDangerousWipeTool()
    live_registry.register(clock_tool)
    live_registry.register(dangerous_tool)
    print(f"[OK] Registered initial tools: {live_registry.names()}", flush=True)

    # 3. Setup BrainRouter and ToolSynthesisEngine
    live_llm = BrainRouter()
    builder = ToolBuilder(registry=live_registry, session_factory=session_factory)
    synth_engine = ToolSynthesisEngine(llm=live_llm, builder=builder)
    synth_policy = AutonomousSynthesisPolicy()
    gap_engine = CapabilityGapEngine(discovery=SkillDiscovery(), registry=live_registry)

    # 4. Setup TaskRuntime with interactive confirmation enabled
    runtime = TaskRuntime(
        session_factory=session_factory,
        synthesis_engine=synth_engine,
        synthesis_policy=synth_policy,
        gap_engine=gap_engine,
        interactive_confirmation=True,
    )

    # 5. Wire into server endpoint configuration
    configure_task_runtime(runtime)
    configure_device_registry(live_registry)
    client = TestClient(app)
    print("[OK] Configured API server routes with isolated runtime and registry", flush=True)

    from server.config import settings
    auth_headers = {"Authorization": f"Bearer {settings.auth_token}" if settings.auth_token else "Bearer dev"}

    passed_invariants = 0
    total_invariants = 10

    # =========================================================================
    # LIVE-1: Normal Schema-Grounded Task (Fibonacci 10 -> 55)
    # =========================================================================
    goal_1 = "Calculate the 10th Fibonacci number."
    print_banner(f"LIVE-1: Schema-Grounded Normal Task\n'{goal_1}'")

    t0_1 = time.time()
    response_1 = client.post(
        "/api/agent/tasks",
        json={"goal": goal_1, "run_async": False},
        headers=auth_headers,
    )
    t_1 = time.time() - t0_1
    assert response_1.status_code == 200, f"Scenario 1 failed: {response_1.text}"
    data_1 = response_1.json()
    task_id_1 = data_1["task_id"]
    print(f"[OK] Task '{task_id_1}' completed in {t_1:.2f}s", flush=True)

    steps_1 = runtime.list_steps(task_id_1)
    step_outputs_1 = [str(s.result.get("output", "")) for s in steps_1]
    print(f"     Step outputs: {step_outputs_1}", flush=True)
    has_55 = any("55" in out for out in step_outputs_1)
    assert has_55, f"Expected 55 in outputs: {step_outputs_1}"
    print(f"  [PASS] LIVE-1: Normal task executed to completion (Fibonacci(10) = 55)", flush=True)
    passed_invariants += 1

    # =========================================================================
    # LIVE-2: Dangerous Action Pauses in WAITING with Durable Confirmation Record
    # =========================================================================
    goal_2 = "Wipe system cache directory /var/cache/app"
    print_banner(f"LIVE-2: Dangerous Action Triggers Confirmation Request\n'{goal_2}'")

    dangerous_task = runtime.create_task(
        goal=goal_2,
        steps=[
            {
                "step_id": "step_danger_live_2",
                "name": "wipe_cache",
                "tool": "system_wipe_cache",
                "capability": "system.wipe",
                "arguments": {
                    "target_dir": "/var/cache/app",
                    "auth_token": "secret_token_live_12345",
                    "secret_key": "private_key_xyz_live",
                },
                "verification_required": True,
            }
        ],
    )
    executor = ToolExecutor(
        registry=live_registry,
        policy=ToolPolicy(
            enabled=True,
            allowed=frozenset(live_registry.names()),
            auto_approve=frozenset({ToolRisk.SAFE, ToolRisk.SENSITIVE}),
        ),
    )

    # Execute compound task - must pause in WAITING
    t_res_2 = runtime.execute_compound_task(dangerous_task.task_id, executor)
    assert t_res_2.status == TaskStatus.WAITING.value, f"Expected WAITING, got {t_res_2.status}"
    steps_2 = runtime.list_steps(dangerous_task.task_id)
    assert steps_2[0].status == StepStatus.WAITING.value
    assert dangerous_tool.call_count == 0, "Dangerous tool executed without confirmation!"

    conf_2 = runtime.get_confirmation_for_step("step_danger_live_2")
    assert conf_2 is not None
    assert conf_2.status == "PENDING"
    assert conf_2.tool == "system_wipe_cache"
    assert conf_2.risk == "dangerous"
    print(f"  [PASS] LIVE-2: Dangerous task paused in WAITING; confirmation {conf_2.confirmation_id} created", flush=True)
    passed_invariants += 1

    # =========================================================================
    # LIVE-3: Sensitive Parameter Redaction in SQLite Confirmation Record
    # =========================================================================
    print_banner("LIVE-3: Sensitive Parameter Redaction")
    with session_factory() as s:
        rec_3 = s.query(DurableConfirmationRecord).filter_by(step_id="step_danger_live_2").first()
        assert rec_3 is not None
        redacted_dict = json.loads(rec_3.redacted_arguments_json)
        assert redacted_dict["auth_token"] == "[REDACTED]"
        assert redacted_dict["secret_key"] == "[REDACTED]"
        assert redacted_dict["target_dir"] == "/var/cache/app"

    # API exposure check
    api_confs = client.get(f"/api/agent/tasks/{dangerous_task.task_id}/confirmations", headers=auth_headers).json()
    assert len(api_confs) >= 1
    assert api_confs[0]["arguments"]["auth_token"] == "[REDACTED]"
    print(f"  [PASS] LIVE-3: Credentials redacted in SQLite record and API responses", flush=True)
    passed_invariants += 1

    # =========================================================================
    # LIVE-4: Human Approval via API Resumes and Executes Dangerous Step
    # =========================================================================
    print_banner("LIVE-4: Human Approval via API Resumes Execution")
    confirm_resp = client.post(
        f"/api/agent/tasks/{dangerous_task.task_id}/confirm",
        json={"decision": "APPROVED", "decision_by": "operator_bob"},
        headers=auth_headers,
    )
    assert confirm_resp.status_code == 200
    assert confirm_resp.json()["status"] == "RESUMING"

    # Wait for async background worker to complete
    for _ in range(50):
        t_check = runtime.get_task(dangerous_task.task_id)
        if t_check.status in (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value):
            break
        time.sleep(0.1)

    t_after_approve = runtime.get_task(dangerous_task.task_id)
    print(f"     Task after approve: status={t_after_approve.status}, error={t_after_approve.last_error}", flush=True)
    steps_after_approve = runtime.list_steps(dangerous_task.task_id)
    print(f"     Step after approve: status={steps_after_approve[0].status}, result={steps_after_approve[0].result}", flush=True)
    assert t_after_approve.status == TaskStatus.COMPLETED.value
    assert steps_after_approve[0].status == StepStatus.COMPLETED.value
    assert dangerous_tool.call_count == 1
    assert dangerous_tool.last_target == "/var/cache/app"
    print(f"  [PASS] LIVE-4: Approved step executed to completion with postcondition verification", flush=True)
    passed_invariants += 1

    # =========================================================================
    # LIVE-5: Single-Use Confirmation Consumption
    # =========================================================================
    print_banner("LIVE-5: Single-Use Confirmation Consumption & Replay Conflict")
    conf_5 = runtime.get_confirmation_for_step("step_danger_live_2")
    assert conf_5.status == "CONSUMED"

    # Attempt replay via API
    replay_resp = client.post(
        f"/api/agent/tasks/{dangerous_task.task_id}/confirm",
        json={"decision": "APPROVED"},
        headers=auth_headers,
    )
    assert replay_resp.status_code == 409, f"Expected 409 Conflict, got {replay_resp.status_code}"
    print(f"  [PASS] LIVE-5: Confirmation consumed; replay returned 409 Conflict", flush=True)
    passed_invariants += 1

    # =========================================================================
    # LIVE-6: Human Rejection via API Rejects Step Without Tool Execution
    # =========================================================================
    print_banner("LIVE-6: Human Rejection via API")
    task_reject = runtime.create_task(
        goal="Wipe database",
        steps=[
            {
                "step_id": "step_danger_live_6",
                "name": "wipe_db",
                "tool": "system_wipe_cache",
                "arguments": {"target_dir": "/var/lib/db"},
            }
        ],
    )
    runtime.execute_compound_task(task_reject.task_id, executor)
    call_count_before = dangerous_tool.call_count

    # Reject via API
    reject_resp = client.post(
        f"/api/agent/tasks/{task_reject.task_id}/confirm",
        json={"decision": "REJECTED", "reason": "Operation unsafe in production"},
        headers=auth_headers,
    )
    assert reject_resp.status_code == 200
    assert reject_resp.json()["status"] == "REJECTED"

    task_reject_final = runtime.get_task(task_reject.task_id)
    assert task_reject_final.status == TaskStatus.FAILED.value
    steps_reject = runtime.list_steps(task_reject.task_id)
    assert steps_reject[0].status == StepStatus.FAILED.value
    assert dangerous_tool.call_count == call_count_before, "Tool executed on rejection!"
    print(f"  [PASS] LIVE-6: Rejection marked task FAILED; tool was NOT executed", flush=True)
    passed_invariants += 1

    # =========================================================================
    # LIVE-7: Argument Tampering Detection
    # =========================================================================
    print_banner("LIVE-7: Argument Tampering Detection (Argument Mismatch)")
    task_tamper = runtime.create_task(
        goal="Wipe staging",
        steps=[
            {
                "step_id": "step_danger_live_7",
                "name": "wipe_staging",
                "tool": "system_wipe_cache",
                "arguments": {"target_dir": "/staging/cache"},
            }
        ],
    )
    runtime.execute_compound_task(task_tamper.task_id, executor)
    # Approve for /staging/cache
    runtime.resolve_confirmation(task_tamper.task_id, decision="APPROVED")

    # Simulate malicious argument substitution in DB
    with session_factory() as s:
        rec_step = s.get(DurableStepRecord, "step_danger_live_7")
        rec_step.arguments_json = json.dumps({"target_dir": "/production/critical_data"})
        s.commit()
    runtime.update_step("step_danger_live_7", StepStatus.PENDING.value)

    # Resume execution - tampering must be caught!
    call_count_before_tamper = dangerous_tool.call_count
    t_tamper_res = runtime.execute_compound_task(task_tamper.task_id, executor)
    assert t_tamper_res.status == TaskStatus.FAILED.value
    assert dangerous_tool.call_count == call_count_before_tamper, "Tool executed with tampered arguments!"

    conf_tamper = runtime.get_confirmation_for_step("step_danger_live_7")
    assert conf_tamper.status == "INVALIDATED"
    print(f"  [PASS] LIVE-7: Tampered arguments detected; confirmation INVALIDATED, tool blocked", flush=True)
    passed_invariants += 1

    # =========================================================================
    # LIVE-8: Ambiguous Goal with Durable Clarification
    # =========================================================================
    print_banner("LIVE-8: Ambiguous Goal Persists Clarification in SQLite")
    ambiguous_goal = "Open the app"
    clar_resp = client.post(
        "/api/agent/tasks",
        json={"goal": ambiguous_goal, "durable_clarification": True},
        headers=auth_headers,
    )
    assert clar_resp.status_code == 200
    clar_data = clar_resp.json()
    assert clar_data["status"] == "WAITING_FOR_CLARIFICATION"
    clar_task_id = clar_data["task_id"]
    clar_id = clar_data["clarification_id"]
    assert len(clar_data["questions"]) >= 1

    with session_factory() as s:
        t_rec = s.get(DurableTaskRecord, clar_task_id)
        assert t_rec.status == TaskStatus.WAITING.value
        c_rec = s.get(DurableClarificationRecord, clar_id)
        assert c_rec.status == "PENDING"
        assert c_rec.task_id == clar_task_id
    print(f"  [PASS] LIVE-8: Durable clarification {clar_id} persisted for task {clar_task_id}", flush=True)
    passed_invariants += 1

    # =========================================================================
    # LIVE-9: Human Clarification via API Resumes Planning and Compound Execution
    # =========================================================================
    print_banner("LIVE-9: Human Clarification via API Resumes Planning")
    clar_answer_resp = client.post(
        f"/api/agent/tasks/{clar_task_id}/clarify",
        json={
            "clarification_id": clar_id,
            "clarified_goal": "Calculate the 6th Fibonacci number.",
        },
        headers=auth_headers,
    )
    assert clar_answer_resp.status_code == 200
    assert clar_answer_resp.json()["clarified"] is True

    # Run execution of clarified task
    t_clar_exec = runtime.execute_compound_task(clar_task_id, executor)
    assert t_clar_exec.status == TaskStatus.COMPLETED.value
    steps_clar = runtime.list_steps(clar_task_id)
    step_outputs_clar = [str(s.result.get("output", "")) for s in steps_clar]
    has_8 = any("8" in out for out in step_outputs_clar)
    assert has_8, f"Expected 8 in outputs: {step_outputs_clar}"
    print(f"  [PASS] LIVE-9: Clarification resumed planning and completed task (Fibonacci(6) = 8)", flush=True)
    passed_invariants += 1

    # =========================================================================
    # LIVE-10: Crash & Restart Recovery Across Confirmation Boundary
    # =========================================================================
    print_banner("LIVE-10: Crash & Restart Recovery Across Confirmation Boundary")
    task_crash = runtime.create_task(
        goal="Crash recovery task",
        steps=[
            {
                "step_id": "step_danger_live_10",
                "name": "wipe_tmp",
                "tool": "system_wipe_cache",
                "arguments": {"target_dir": "/tmp/crash_test"},
            }
        ],
    )
    runtime.execute_compound_task(task_crash.task_id, executor)
    call_count_before_restart = dangerous_tool.call_count

    # Simulate process crash by creating a fresh TaskRuntime over same SQLite database
    runtime_after_restart = TaskRuntime(
        session_factory=session_factory,
        interactive_confirmation=True,
    )
    resumed_tasks = runtime_after_restart.resume_all_active(executor)

    # Task and step MUST remain WAITING
    task_recovered = runtime_after_restart.get_task(task_crash.task_id)
    assert task_recovered.status == TaskStatus.WAITING.value
    steps_recovered = runtime_after_restart.list_steps(task_crash.task_id)
    assert steps_recovered[0].status == StepStatus.WAITING.value
    assert dangerous_tool.call_count == call_count_before_restart, "Dangerous tool executed across crash restart!"
    print(f"  [PASS] LIVE-10: Process restart preserved WAITING state without mutation replay", flush=True)
    passed_invariants += 1

    # =========================================================================
    # SUMMARY
    # =========================================================================
    print_banner("PHASE 5B.7 LIVE VERIFICATION SUMMARY")
    print(f"Invariants Passed: {passed_invariants}/{total_invariants}", flush=True)
    if passed_invariants == total_invariants:
        print("\n*** ALL 10/10 PHASE 5B.7 LIVE INVARIANTS PASSED ***", flush=True)
    else:
        print(f"\n[FAIL] Only {passed_invariants}/{total_invariants} invariants passed.", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
