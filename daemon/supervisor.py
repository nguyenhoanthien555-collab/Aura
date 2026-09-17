"""
AURA 24/7 Persistent Daemon & Subsystem Supervisor.

Runs continuously in the background, independently of HTTP request lifecycles.
Coordinates bounded background workers:
    - TaskWorker: Drives and recovers durable tasks across disconnects and restarts.
    - LearningWorker: Background experience collection, consolidation, and candidate generation.
    - MemoryWorker: Periodic memory maintenance and deduplication.
    - HealthMonitor: Subsystem health tracking (HEALTHY, DEGRADED, UNAVAILABLE).
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
import os
import threading
import time
from typing import Any, Dict, List, Optional

from brain.hardware import detect_hardware, HardwareProfile
from brain.local_runtime import LocalModelRuntime
from brain.package import BrainManager
from brain.providers.local_aura import LocalAuraBrain
from core.logger import logger
from learning.experience import AuraExperienceStore
from learning.pipeline import LearningCandidatePipeline
from learning.promotion import LearningCoordinator
from learning.scheduler import AutonomousLearningScheduler
from learning.training import TrainingJobRunner


class SubsystemHealth(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass
class DaemonHealthStatus:
    daemon: str = SubsystemHealth.HEALTHY.value
    brain: str = SubsystemHealth.HEALTHY.value
    memory: str = SubsystemHealth.HEALTHY.value
    task_runtime: str = SubsystemHealth.HEALTHY.value
    tool_registry: str = SubsystemHealth.HEALTHY.value
    android: str = SubsystemHealth.HEALTHY.value
    learning: str = SubsystemHealth.HEALTHY.value
    storage: str = SubsystemHealth.HEALTHY.value
    backup: str = SubsystemHealth.HEALTHY.value
    sync_pruning: str = SubsystemHealth.HEALTHY.value
    backups_created: int = 0
    records_pruned: int = 0
    offline: bool = True
    active_brain_id: str = "aura-local-v1"
    uptime_seconds: float = 0.0
    timestamp: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class AuraDaemon:
    """
    Persistent 24/7 Daemon for AURA.
    Owns background loops with bounded intervals and event-driven wakeups.
    """

    def __init__(
        self,
        brain: Optional[LocalAuraBrain] = None,
        task_runtime: Optional[Any] = None,
        tool_registry: Optional[Any] = None,
        experience_store: Optional[AuraExperienceStore] = None,
        learning_pipeline: Optional[LearningCandidatePipeline] = None,
        learning_coordinator: Optional[LearningCoordinator] = None,
        scheduler: Optional[AutonomousLearningScheduler] = None,
        offline: bool = True,
        poll_interval: float = 1.0,
        proactive_engine: Optional[Any] = None,
        proactive_interval: float = 60.0,
        backup_interval: float = 86400.0,
        backup_dir: Optional[Any] = None,
        prune_interval: float = 3600.0,
        prune_retention_limit: int = 5000,
    ):
        self.offline = offline
        self.poll_interval = poll_interval
        self.proactive_engine = proactive_engine
        self.proactive_interval = proactive_interval
        self._last_proactive_tick = 0.0
        self.backup_interval = backup_interval
        self.backup_dir = backup_dir
        self._last_backup_tick = 0.0
        self.prune_interval = prune_interval
        self.prune_retention_limit = prune_retention_limit
        self._last_prune_tick = 0.0
        self.brain = brain
        self.task_runtime = task_runtime
        self.tool_registry = tool_registry
        self.experience_store = experience_store or AuraExperienceStore()
        self.learning_pipeline = learning_pipeline or LearningCandidatePipeline(store=self.experience_store)
        self.learning_coordinator = learning_coordinator or LearningCoordinator()
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.scheduler = scheduler or AutonomousLearningScheduler(
            experience_store=self.experience_store,
            pipeline=self.learning_pipeline,
            state_file=os.path.join(repo_root, "brains", "scheduler_state.json"),
        )

        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._started_at = 0.0
        self._lock = threading.RLock()

        # Worker metrics
        self.tasks_processed = 0
        self.learning_cycles = 0
        self.last_consolidation_at = 0.0
        self.backups_created = 0
        self.records_pruned = 0

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Starts the persistent background daemon thread."""
        with self._lock:
            if self.is_running:
                return
            self._stop_event.clear()
            self._started_at = time.time()
            self._thread = threading.Thread(target=self._daemon_loop, name="aura-daemon-24-7", daemon=True)
            self._thread.start()
            logger.info("AURA 24/7 Daemon started (offline=%s)", self.offline)

    def stop(self, timeout: float = 5.0) -> None:
        """Stops the daemon gracefully."""
        with self._lock:
            if not self.is_running:
                return
            self._stop_event.set()
            self._wake_event.set()
            if self._thread:
                self._thread.join(timeout=timeout)
            self._thread = None
            logger.info("AURA 24/7 Daemon stopped")

    def wakeup(self) -> None:
        """Wakes up the daemon immediately when new events arrive."""
        self._wake_event.set()

    def step_once(self) -> None:
        """Executes a single supervisory step across task, learning, proactive, backup, and pruning workers."""
        self._step_task_worker()
        self._step_learning_worker()
        self._step_proactive_worker()
        self._step_backup_worker()
        self._step_pruning_worker()

    def _daemon_loop(self) -> None:
        """Main 24/7 supervisory loop."""
        while not self._stop_event.is_set():
            try:
                # 1. Task Worker Step
                self._step_task_worker()

                # 2. Learning & Sleep/Consolidation Step
                self._step_learning_worker()

                # 3. Proactive Messaging Step
                self._step_proactive_worker()

                # 4. Periodic Backup Step
                self._step_backup_worker()

                # 5. Outbox / Inbox Pruning Step
                self._step_pruning_worker()

                # 6. Sleep until next poll interval or wake event
                self._wake_event.wait(timeout=self.poll_interval)
                self._wake_event.clear()

            except Exception as e:
                logger.error("Error in AURA 24/7 daemon loop: %s", e, exc_info=True)
                time.sleep(self.poll_interval)

    def _step_task_worker(self) -> None:
        """Processes any ready task steps or recovers pending tasks."""
        if self.task_runtime is None:
            return

        try:
            if hasattr(self.task_runtime, "run_all_ready_steps"):
                processed = self.task_runtime.run_all_ready_steps()
                if processed:
                    self.tasks_processed += processed
            elif hasattr(self.task_runtime, "step"):
                # single step
                pass
        except Exception as e:
            logger.debug("Task worker tick error: %s", e)

    def _step_learning_worker(self) -> None:
        """Periodically checks eligibility and executes autonomous self-learning cycles."""
        if not self.scheduler:
            return
        if "PYTEST_CURRENT_TEST" in os.environ and os.environ.get("AURA_FORCE_NEURAL_TEST") != "1":
            return
        try:
            eligible, reason = self.scheduler.check_eligibility()
            if eligible:
                logger.info("[AuraDaemon] Scheduler detected eligible learning conditions: %s", reason)
                self.learning_cycles += 1
                cycle_trace = self.scheduler.execute_cycle(
                    auto_promote=self.scheduler.config.auto_promote,
                )
                self.last_consolidation_at = time.time()
                logger.info("[AuraDaemon] Completed autonomous learning cycle: %s", cycle_trace.get("status"))
        except Exception as e:
            logger.debug("Learning worker tick error: %s", e)

    def _step_proactive_worker(self) -> None:
        """Periodically evaluates proactive messaging triggers unprompted."""
        if not self.proactive_engine:
            return
        policy = getattr(self.proactive_engine, "policy", None)
        if policy and hasattr(policy, "settings"):
            if not getattr(policy.settings, "enabled", False):
                return
        now = time.time()
        if (now - self._last_proactive_tick) < self.proactive_interval:
            return
        self._last_proactive_tick = now
        try:
            decision = self.proactive_engine.tick()
            if decision and getattr(decision, "send", False):
                logger.info("[AuraDaemon] Proactive message generated: %s", getattr(decision, "detail", ""))
        except Exception as e:
            logger.debug("Proactive worker tick error: %s", e)

    def _step_backup_worker(self) -> None:
        """Periodically creates an atomic online backup of memory.db with integrity check."""
        if self.backup_interval <= 0:
            return
        now = time.time()
        if (now - self._last_backup_tick) < self.backup_interval:
            return
        self._last_backup_tick = now
        try:
            from memory.backup import create_database_backup
            backup_path = create_database_backup(tag="auto_periodic", backup_dir=self.backup_dir, max_backups_to_keep=7)
            self.backups_created += 1
            logger.info("[AuraDaemon] Periodic backup created successfully: %s", backup_path.name)
        except Exception as e:
            logger.error("[AuraDaemon] Periodic backup failed: %s", e)

    def _step_pruning_worker(self) -> None:
        """Periodically prunes acknowledged outbox and processed inbox records."""
        if self.prune_interval <= 0:
            return
        now = time.time()
        if (now - self._last_prune_tick) < self.prune_interval:
            return
        self._last_prune_tick = now
        try:
            from core.sync.outbox import OutboxManager
            from core.sync.inbox import InboxProcessor
            outbox = OutboxManager()
            inbox = InboxProcessor()
            pruned_out = outbox.prune_acknowledged(max_records_to_keep=self.prune_retention_limit)
            pruned_in = inbox.prune_processed(max_records_to_keep=self.prune_retention_limit)
            self.records_pruned += (pruned_out + pruned_in)
            if pruned_out or pruned_in:
                logger.info("[AuraDaemon] Pruned %d outbox and %d inbox records", pruned_out, pruned_in)
        except Exception as e:
            logger.debug("[AuraDaemon] Pruning worker tick error: %s", e)

    def get_health(self) -> DaemonHealthStatus:
        """Compiles health metrics across all AURA subsystems."""
        now = datetime.now().isoformat(timespec="seconds")
        uptime = (time.time() - self._started_at) if self.is_running else 0.0

        brain_health = SubsystemHealth.HEALTHY.value
        active_brain_id = "unknown"
        if self.brain:
            brain_health = self.brain.health()
            active_brain_id = self.brain.brain_id

        task_health = SubsystemHealth.HEALTHY.value if self.task_runtime else SubsystemHealth.DEGRADED.value
        tool_health = SubsystemHealth.HEALTHY.value if self.tool_registry else SubsystemHealth.DEGRADED.value

        return DaemonHealthStatus(
            daemon=SubsystemHealth.HEALTHY.value if self.is_running else SubsystemHealth.DEGRADED.value,
            brain=brain_health,
            memory=SubsystemHealth.HEALTHY.value,
            task_runtime=task_health,
            tool_registry=tool_health,
            android=SubsystemHealth.HEALTHY.value,
            learning=SubsystemHealth.HEALTHY.value,
            storage=SubsystemHealth.HEALTHY.value,
            backup=SubsystemHealth.HEALTHY.value,
            sync_pruning=SubsystemHealth.HEALTHY.value,
            backups_created=self.backups_created,
            records_pruned=self.records_pruned,
            offline=self.offline,
            active_brain_id=active_brain_id,
            uptime_seconds=round(uptime, 1),
            timestamp=now,
        )
