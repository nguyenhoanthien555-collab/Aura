# AURA 2.0 — 24/7 Daemon & Runtime Supervisor Specification
**Status:** IMPLEMENTED | TESTED | LIVE VERIFIED
**Document Version:** 1.0.0
**Classification:** Core System Architecture

---

## 1. Executive Summary

AURA 2.0 operates as an autonomous, 24/7 personal runtime daemon rather than an ephemeral request/response web application. The core intelligence and task orchestration loops persist independently of whether an HTTP client, UI frontend, or CLI session is connected.

The daemon supervisor maintains active vigilance over durable tasks, handles background self-learning cycles, recovers gracefully from system reboots or crashes, and enforces strict idempotency so that side-effecting operations are never replayed upon recovery.

---

## 2. Core 24/7 Daemon Architecture

Located in [daemon/supervisor.py](file:///D:/AURA/daemon/supervisor.py), the AuraDaemon coordinates continuous operations across multiple subsystems.

`	ext
┌─────────────────────────────────────────────────────────────────────────┐
│                           AURA 24/7 DAEMON                              │
│                                                                         │
│   ┌─────────────────────┐   ┌─────────────────┐   ┌─────────────────┐   │
│   │    TASK WORKER      │   │ LEARNING WORKER │   │ HEALTH MONITOR  │   │
│   │ (Durable Execution) │   │ (Curation/Eval) │   │  (Heartbeats)   │   │
│   └──────────┬──────────┘   └────────┬────────┘   └────────┬────────┘   │
└──────────────┼───────────────────────┼─────────────────────┼────────────┘
               ▼                       ▼                     ▼
      ┌─────────────────┐     ┌─────────────────┐   ┌─────────────────┐
      │  TaskRuntime /  │     │ ExperienceStore │   │ SubsystemHealth │
      │  ToolExecutor   │     │  & Coordinator  │   │     Tracker     │
      └─────────────────┘     └─────────────────┘   └─────────────────┘
`

### Key Properties
- **Decoupled Lifecycle:** Starts as a dedicated daemon thread on application initialization and runs uninterrupted.
- **Event-Driven & Polled Wakeup:** Background workers sleep on configurable intervals (default: 1.0s, testable down to 0.05s) with event triggers for immediate wakeup on task submission.
- **Graceful Teardown:** Handles SIGINT / SIGTERM cleanly, allowing currently executing non-interruptible steps to complete before saving state and terminating.

---

## 3. Bounded Background Workers

### 1. Task Execution Worker
- Scans TaskRuntime for active tasks in PENDING or RUNNING status.
- Executes steps sequentially via ToolExecutor, respecting permission, capability, and timeout gates.
- Updates step status and captures Evidence at each milestone.
- Reconciles task status upon step completion or failure.

### 2. Continuous Learning Worker
- Periodically queries AuraExperienceStore for newly accumulated eligible experiences.
- When uncurated experience count exceeds a threshold (e.g. 50 eligible turns), triggers dataset manifest creation.
- Dispatches background smoke-training jobs to TrainingJobRunner and invokes LearningCoordinator to evaluate candidate models without disrupting foreground tasks.

---

## 4. Crash Recovery & Idempotency Guarantees

A central hazard of autonomous agent runtimes is repeated execution of mutations following a crash or process restart (e.g. duplicate purchases, repeated messages, or repeated device inputs).

AURA guarantees idempotency and crash resilience through:

1. **Step-Level Evidence Ledger:**
   Every step execution records its resulting Evidence directly to durable SQLite storage upon completion.
2. **Crash Recovery Scan:**
   Upon daemon startup or server restart, TaskRuntime.resume_all_active() loads all unfinished tasks.
3. **Never Replay Completed Steps:**
   Before invoking ToolExecutor, the runtime verifies whether a step already holds status COMPLETED and confirming Evidence. Completed steps are skipped, and execution resumes exclusively from the first unverified step.
4. **Reconciliation:**
   If all steps in an interrupted task were completed prior to the crash, the task is immediately marked COMPLETED without re-running any tools.

---

## 5. Subsystem Health Monitoring

The supervisor aggregates real-time health checks across 5 core subsystems:

`python
@dataclass
class DaemonHealthStatus:
    daemon: str          # HEALTHY | DEGRADED | UNHEALTHY
    brain: str           # Status of active Local Brain
    memory: str          # Status of SQLite experience & task databases
    task_runtime: str    # Status of durable task engine
    capabilities: str    # Capability registry & provider readiness
    learning: str        # Status of learning pipeline & active training
    timestamp: str       # ISO-8601 UTC timestamp
    active_tasks: int    # Number of active tasks currently executing
`

If an individual subsystem fails (e.g. an inference server disconnection), the daemon marks the specific subsystem as DEGRADED or UNHEALTHY while continuing to supervise healthy components.

---

## 6. Server Lifecycle Integration

Located in [server/runtime.py](file:///D:/AURA/server/runtime.py), ServerRuntime binds daemon lifecycle to the FastAPI application:
- On startup: Initializes database schemas, registers capabilities, discovers hardware, and calls daemon.start().
- On shutdown: Calls daemon.stop() for graceful thread termination.
- **API Contract Preservation:** Standard health check (/api/health) strictly maintains the 10 documented runtime keys required by legacy API clients. Comprehensive daemon metrics are served via /api/brain/health.
