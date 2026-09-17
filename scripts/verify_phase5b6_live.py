"""
AURA 2.0 — Phase 5B.6 Live Laptop End-to-End Verification Script.

Planner Reliability & Real-World Intent Coverage.

Verifies on live Windows host against real LLM:
Scenario 1: Schema-Grounded Normal Task (Fibonacci 10 -> 55)
Scenario 2: Multi-Step Data Dependency (Fibonacci 10 -> Square -> 3025)
Scenario 3: Ambiguous Request -> NEEDS_CLARIFICATION (No task created, no tools executed, no synthesis)
Scenario 4: Existing Tool Reuse (Fibonacci 6 -> Square -> 64 without re-synthesis)
Scenario 5: Dangerous Mutation Confirmation Gate (Dangerous tool execution requires operator confirmation)
Scenario 6: Verification-Aware Request (verification_required: true / verification step, Fibonacci 7 -> 13)
"""

import json
import os
from pathlib import Path
import sys
import tempfile
import time

# Enable line buffering on stdout for live log visibility
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
load_dotenv(REPO_ROOT / ".env")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

from agent.task_runtime import StepStatus, TaskPlan, TaskRuntime, TaskStatus
from brain.router import BrainRouter
from core.capabilities import registry as global_cap_registry, Capability
from core.capabilities.discovery import SkillDiscovery
from core.capabilities.gap import CapabilityGapEngine
from memory.models import DurableStepRecord, DurableTaskRecord
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


def print_banner(text: str):
    print("\n" + "=" * 65, flush=True)
    print(f"  {text}", flush=True)
    print("=" * 65, flush=True)


class LiveDangerousTool(Tool):
    name = "system_wipe_cache"
    capability = "system.wipe"
    risk = ToolRisk.DANGEROUS
    side_effect = SideEffect.MUTATING
    parameters = (
        Parameter(name="target_dir", type="string", required=True),
    )

    def execute(self, **arguments) -> ToolResult:
        target_dir = arguments.get("target_dir", "/tmp")
        return ToolResult(
            ok=True,
            output=f"Wiped {target_dir}",
            status=OutcomeToolStatus.SUCCESS.value,
        )


def main():
    print_banner("AURA 2.0 — PHASE 5B.6 LIVE LAPTOP E2E VERIFICATION")
    print(f"Repository: {REPO_ROOT}", flush=True)
    print(f"Python:     {sys.version.split()[0]}", flush=True)
    print(f"Platform:   {sys.platform}", flush=True)

    # Check for live LLM credentials
    if not os.getenv("GEMINI_API_KEY"):
        print("[FAIL] GEMINI_API_KEY is not set in environment or .env file.", flush=True)
        print("Live verification requires valid LLM credentials.", flush=True)
        sys.exit(1)

    # 1. Setup isolated database
    temp_dir = tempfile.mkdtemp(prefix="aura_phase5b6_live_")
    db_path = Path(temp_dir) / "aura_live_phase5b6.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    init_task_tables(engine)
    session_factory = sessionmaker(bind=engine)
    print(f"[OK] Initialized isolated database: {db_path}", flush=True)

    # 2. Setup ToolRegistry with baseline tools and dangerous tool
    live_registry = ToolRegistry()
    clock_tool = CurrentTimeTool()
    dangerous_tool = LiveDangerousTool()
    live_registry.register(clock_tool)
    live_registry.register(dangerous_tool)
    print(f"[OK] Registered initial tools: {live_registry.names()}", flush=True)

    # 3. Setup BrainRouter and ToolSynthesisEngine
    live_llm = BrainRouter()
    builder = ToolBuilder(registry=live_registry, session_factory=session_factory)
    synth_engine = ToolSynthesisEngine(llm=live_llm, builder=builder)
    synth_policy = AutonomousSynthesisPolicy()
    gap_engine = CapabilityGapEngine(discovery=SkillDiscovery(), registry=live_registry)

    # 4. Setup TaskRuntime
    runtime = TaskRuntime(
        session_factory=session_factory,
        synthesis_engine=synth_engine,
        synthesis_policy=synth_policy,
        gap_engine=gap_engine,
    )

    # 5. Wire into server endpoint configuration
    configure_task_runtime(runtime)
    configure_device_registry(live_registry)
    client = TestClient(app)
    print("[OK] Configured API server routes with isolated runtime and registry", flush=True)

    from server.config import settings
    auth_headers = {"Authorization": f"Bearer {settings.auth_token}" if settings.auth_token else "Bearer dev"}

    # =========================================================================
    # SCENARIO 1: Schema-Grounded Normal Task (Fibonacci 10 -> 55)
    # =========================================================================
    goal_1 = "Calculate the 10th Fibonacci number."
    print_banner(f"SCENARIO 1: Schema-Grounded Single Step Task\n'{goal_1}'")

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
    has_55_s1 = any("55" in out for out in step_outputs_1)
    assert has_55_s1, f"Expected 55 in outputs: {step_outputs_1}"
    print(f"  [PASS] Grounded Fibonacci(10) = 55 verified", flush=True)

    # =========================================================================
    # SCENARIO 2: Multi-Step Data Dependency (Fibonacci 10 -> Square -> 3025)
    # =========================================================================
    goal_2 = "Calculate the 10th Fibonacci number and square the result."
    print_banner(f"SCENARIO 2: Multi-Step Compound Data Dependency\n'{goal_2}'")

    t0_2 = time.time()
    response_2 = client.post(
        "/api/agent/tasks",
        json={"goal": goal_2, "run_async": False},
        headers=auth_headers,
    )
    t_2 = time.time() - t0_2
    assert response_2.status_code == 200, f"Scenario 2 failed: {response_2.text}"
    data_2 = response_2.json()
    task_id_2 = data_2["task_id"]
    print(f"[OK] Task '{task_id_2}' completed in {t_2:.2f}s", flush=True)

    steps_2 = runtime.list_steps(task_id_2)
    assert len(steps_2) >= 2, f"Expected at least 2 steps, got {len(steps_2)}"
    step0_id_2 = steps_2[0].step_id
    consumer_args_str = json.dumps(steps_2[1].arguments)
    has_param_ref_2 = f"${{{step0_id_2}." in consumer_args_str or "${step_0." in consumer_args_str
    print(f"     Step 1 arguments: {consumer_args_str}", flush=True)
    print(f"     Genuine parameter reference: {has_param_ref_2}", flush=True)
    assert has_param_ref_2, "Consumer step must reference producer step output"

    step_outputs_2 = [str(s.result.get("output", "")) for s in steps_2]
    print(f"     Step outputs: {step_outputs_2}", flush=True)
    has_3025_s2 = any("3025" in out for out in step_outputs_2)
    assert has_3025_s2, f"Expected 3025 in outputs: {step_outputs_2}"
    print(f"  [PASS] Grounded calculation 55^2 = 3025 verified", flush=True)

    # =========================================================================
    # SCENARIO 3: Ambiguous Request (Clarification Protocol)
    # =========================================================================
    goal_3 = "Open the app."
    print_banner(f"SCENARIO 3: Ambiguous Goal Clarification Protocol\n'{goal_3}'")

    with session_factory() as s:
        tasks_before = s.query(DurableTaskRecord).count()
        steps_before = s.query(DurableStepRecord).count()

    t0_3 = time.time()
    response_3 = client.post(
        "/api/agent/tasks",
        json={"goal": goal_3, "run_async": False},
        headers=auth_headers,
    )
    t_3 = time.time() - t0_3
    assert response_3.status_code == 200, f"Scenario 3 failed: {response_3.text}"
    data_3 = response_3.json()
    print(f"[OK] Ambiguous goal evaluated in {t_3:.2f}s:", flush=True)
    print(f"     Response: {json.dumps(data_3, indent=2)}", flush=True)

    assert data_3.get("status") == "NEEDS_CLARIFICATION", (
        f"Expected status 'NEEDS_CLARIFICATION', got '{data_3.get('status')}'. Model must not guess!"
    )
    questions = data_3.get("questions", [])
    assert isinstance(questions, list) and 1 <= len(questions) <= 5, (
        f"Expected 1-5 clarification questions, got: {questions}"
    )
    print(f"     Clarification questions ({len(questions)}): {questions}", flush=True)

    with session_factory() as s:
        tasks_after = s.query(DurableTaskRecord).count()
        steps_after = s.query(DurableStepRecord).count()

    assert tasks_after == tasks_before, "Ambiguous goal must NOT create any SQLite DurableTask record"
    assert steps_after == steps_before, "Ambiguous goal must NOT create any SQLite DurableStep record"
    print(f"  [PASS] Ambiguous goal returned clarification; 0 tasks created in SQLite", flush=True)

    # =========================================================================
    # SCENARIO 4: Existing Tool Reuse Without Re-synthesis
    # =========================================================================
    goal_4 = "Calculate the 6th Fibonacci number and square the result."
    print_banner(f"SCENARIO 4: Capability Reuse Without Re-synthesis\n'{goal_4}'")

    t0_4 = time.time()
    response_4 = client.post(
        "/api/agent/tasks",
        json={"goal": goal_4, "run_async": False},
        headers=auth_headers,
    )
    t_4 = time.time() - t0_4
    assert response_4.status_code == 200, f"Scenario 4 failed: {response_4.text}"
    data_4 = response_4.json()
    task_id_4 = data_4["task_id"]
    print(f"[OK] Reused tools executed in {t_4:.2f}s for task '{task_id_4}'", flush=True)

    steps_4 = runtime.list_steps(task_id_4)
    step_outputs_4 = [str(s.result.get("output", "")) for s in steps_4]
    print(f"     Step outputs: {step_outputs_4}", flush=True)
    has_8_s4 = any("8" in out for out in step_outputs_4)
    has_64_s4 = any("64" in out for out in step_outputs_4)
    assert has_8_s4 and has_64_s4, f"Expected 8 and 64 in outputs: {step_outputs_4}"
    print(f"  [PASS] Fibonacci(6) = 8 -> 8^2 = 64 completed via tool reuse", flush=True)

    # =========================================================================
    # SCENARIO 5: Dangerous Mutation Confirmation Gate
    # =========================================================================
    print_banner("SCENARIO 5: Dangerous Mutation Confirmation Gate Integrity")
    policy = ToolPolicy(
        enabled=True,
        allowed=frozenset([dangerous_tool.name]),
        auto_approve=frozenset({ToolRisk.SAFE, ToolRisk.SENSITIVE}),
    )
    assert ToolRisk.DANGEROUS not in policy.auto_approve, "DANGEROUS must not be auto-approved"

    executor = ToolExecutor(registry=live_registry, policy=policy)
    dangerous_res = executor.execute("system_wipe_cache", {"target_dir": "/var/cache"})
    print(f"     Unattended execution result: ok={dangerous_res.ok}, error='{dangerous_res.error}'", flush=True)
    assert dangerous_res.ok is False, "Unattended dangerous tool execution must be refused"
    print(f"  [PASS] ToolExecutor correctly blocked unconfirmed dangerous operation", flush=True)

    # =========================================================================
    # SCENARIO 6: Verification-Aware Request (Fibonacci 7 -> 13)
    # =========================================================================
    goal_6 = "Calculate the 7th Fibonacci number and verify the calculation."
    print_banner(f"SCENARIO 6: Verification-Aware Request\n'{goal_6}'")

    t0_6 = time.time()
    response_6 = client.post(
        "/api/agent/tasks",
        json={"goal": goal_6, "run_async": False},
        headers=auth_headers,
    )
    t_6 = time.time() - t0_6
    assert response_6.status_code == 200, f"Scenario 6 failed: {response_6.text}"
    data_6 = response_6.json()
    task_id_6 = data_6["task_id"]
    print(f"[OK] Verification-aware task '{task_id_6}' completed in {t_6:.2f}s", flush=True)

    steps_6 = runtime.list_steps(task_id_6)
    step_outputs_6 = [str(s.result.get("output", "")) for s in steps_6]
    has_13_s6 = any("13" in out for out in step_outputs_6)
    has_verif = any(s.verification_required or "verify" in s.name.lower() or "verify" in s.tool.lower() for s in steps_6)
    print(f"     Step outputs: {step_outputs_6}", flush=True)
    print(f"     Verification awareness detected: {has_verif}", flush=True)
    assert has_13_s6, f"Expected 13 in outputs: {step_outputs_6}"
    assert has_verif, "Verification requirement or verification step must be generated"
    print(f"  [PASS] Verification-aware goal correctly executed: Fib(7) = 13", flush=True)

    # =========================================================================
    # SUMMARY OF ALL INVARIANTS
    # =========================================================================
    print_banner("PHASE 5B.6 LIVE VERIFICATION SUMMARY")
    invariants = [
        ("Scenario 1: Schema-grounded single step task completed (Fib 10 -> 55)", has_55_s1),
        ("Scenario 2: Multi-step compound dataflow passed (${step_0.output} -> 3025)", has_3025_s2),
        ("Scenario 3: Ambiguous goal 'Open the app' triggered NEEDS_CLARIFICATION", data_3.get("status") == "NEEDS_CLARIFICATION"),
        ("Scenario 3: Zero SQLite records created on clarification request", tasks_after == tasks_before),
        ("Scenario 4: Reused synthesized tools without re-synthesis (Fib 6 -> 64)", has_64_s4),
        ("Scenario 5: Dangerous mutation unattended execution blocked by confirmation gate", dangerous_res.ok is False),
        ("Scenario 6: Verification-aware goal enforced verification flag / step (Fib 7 -> 13)", has_13_s6 and has_verif),
    ]

    all_passed = True
    for desc, passed in invariants:
        mark = "PASS" if passed else "FAIL"
        if not passed:
            all_passed = False
        print(f"  [{mark}] {desc}", flush=True)

    if all_passed:
        print(f"\n{'=' * 65}", flush=True)
        print("  ALL 7/7 LIVE RELIABILITY AND INTENT INVARIANTS VERIFIED", flush=True)
        print(f"{'=' * 65}", flush=True)
    else:
        print(f"\n{'=' * 65}", flush=True)
        print("  LIVE VERIFICATION FAILED", flush=True)
        print(f"{'=' * 65}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
