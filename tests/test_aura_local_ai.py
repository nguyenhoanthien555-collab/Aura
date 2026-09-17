"""
AURA Local AI, 24/7 Autonomous Daemon & Continuous Self-Learning Test Suite.

Validates the full local-first intelligence architecture:
1. Hardware Profiling & Recommendation
2. Brain Manifest & Atomic Package Manager
3. Deterministic / Local Model Runtime & Provenance
4. Offline Enforcement (Zero external network calls)
5. LocalAuraBrain Provider & Router Integration
6. Experience Store with Privacy Screening & Quality Scoring
7. Self-Learning Candidate Dataset Pipeline
8. Process-Isolated Training Job Execution
9. Evaluation Suite, Rejection of Regressions & Safe Rollback
10. 24/7 Persistent Daemon Lifecycle & Subsystem Health Monitoring
11. FastAPI Endpoints for Brain & Learning Management
"""

import json
import os
import shutil
import tempfile
import pytest
from fastapi.testclient import TestClient

from brain.hardware import detect_hardware, HardwareProfile
from brain.package import BrainManager, BrainManifest, BrainPackage, BrainStatus
from brain.provenance import BrainProvenance
from brain.local_runtime import LocalModelRuntime, DeterministicBackend
from brain.providers.local_aura import LocalAuraBrain
from brain.router import BrainRouter
from daemon.supervisor import AuraDaemon, SubsystemHealth
from learning.experience import AuraExperienceStore, Experience
from learning.pipeline import LearningCandidatePipeline
from learning.evaluation import BrainEvaluator
from learning.training import TrainingJobRunner
from learning.promotion import LearningCoordinator
from server.main import app


# ----------------------------------------------------------------------
# 1. Hardware Profiling
# ----------------------------------------------------------------------

def test_hardware_profiling_and_recommendation():
    """Detects host system resources and provides deterministic local model tier."""
    hw = detect_hardware()
    assert isinstance(hw, HardwareProfile)
    assert hw.cpu_cores >= 1
    assert hw.ram_total_mb > 0
    assert hw.recommended_tier in ("tiny", "small", "medium", "large")
    assert hw.recommended_backend in ("cuda", "rocm", "metal", "cpu", "mock")
    as_dict = hw.to_dict()
    assert "cpu_cores" in as_dict
    assert "ram_total_mb" in as_dict
    assert "gpu_available" in as_dict


# ----------------------------------------------------------------------
# 2. Brain Package Manager & Manifests
# ----------------------------------------------------------------------

def test_brain_manifest_and_packaging(tmp_path):
    """Verifies manifest serialization, artifact validation, and checksum checks."""
    store_dir = tmp_path / "brains"
    mgr = BrainManager(brains_dir=str(store_dir))

    manifest = BrainManifest(
        brain_id="aura-test-v1",
        version="1.0.0",
        model_format="deterministic",
        runtime_backend="cpu",
        capabilities=["chat", "tool_calling"],
        status=BrainStatus.VALIDATING.value,
    )

    weight_data = b"MOCK_MODEL_WEIGHTS_FOR_TESTING_12345"
    pkg = mgr.register_package(manifest, weight_content=weight_data)

    assert pkg.brain_id == "aura-test-v1"
    assert pkg.status == BrainStatus.CANDIDATE.value
    assert pkg.manifest.checksum != ""
    weight_path = os.path.join(pkg.package_dir, "model.bin")
    assert os.path.exists(weight_path)

    # Validate package
    valid = mgr.validate_package(pkg.brain_id)
    assert valid is True

    # Check tamper detection
    with open(weight_path, "wb") as f:
        f.write(b"CORRUPTED_WEIGHTS")
    tampered_valid = mgr.validate_package(pkg.brain_id)
    assert tampered_valid is False


# ----------------------------------------------------------------------
# 3. Local Model Runtime & Deterministic Backend
# ----------------------------------------------------------------------

def test_local_model_runtime_chat_and_tools():
    """Verifies local runtime execution for text and tool selection."""
    runtime = LocalModelRuntime()

    # Direct generation
    resp_text = runtime.generate("Hello who are you?")
    assert resp_text != ""
    prov = runtime.get_provenance()
    assert prov.provider == "local_aura"
    assert prov.backend in ("deterministic", "cpu", "cuda", "rocm", "metal")

    # Tool invocation
    tools = [
        {"name": "android.screenshot", "description": "Take screenshot"},
        {"name": "system.time", "description": "Get current time"},
    ]
    turn = runtime.generate_with_tools(
        system="",
        messages=[{"role": "user", "content": "Please take a screenshot of the app"}],
        tools=tools,
    )
    assert turn.tool_calls is not None
    assert len(turn.tool_calls) > 0
    assert turn.tool_calls[0].name == "android.screenshot"


# ----------------------------------------------------------------------
# 4. Strict Offline Mode Enforcement (Zero External Network Calls)
# ----------------------------------------------------------------------

def test_strict_offline_mode_enforcement(monkeypatch):
    """Verifies that offline=True forces local_aura and forbids external network calls."""
    import socket

    # Disallow socket connect to external addresses
    orig_connect = socket.socket.connect

    def forbidden_connect(self, address):
        host, port = address[0], address[1]
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ConnectionRefusedError(f"Zero network rule: outbound connection to {host} forbidden in offline mode")
        return orig_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", forbidden_connect)
    monkeypatch.setenv("AURA_OFFLINE", "1")

    router = BrainRouter(provider_name="gemini")

    # Provider must be resolved to local_aura because offline is True
    assert router.active_chain() == "local_aura"
    prov = router.get_provenance()
    assert prov.provider == "local_aura"

    # Chat execution must succeed without external network
    res = router.generate("What is your mission?")
    assert isinstance(res, str)
    assert "aura" in res.lower()


# ----------------------------------------------------------------------
# 5. Experience Store, Privacy Screening & Quality Scoring
# ----------------------------------------------------------------------

def test_experience_store_and_privacy_screening():
    """Verifies recording, quality scoring, and secret redaction/exclusion."""
    store = AuraExperienceStore()

    # 1. Normal successful experience
    exp_normal = store.record_experience(
        session_id="sess-001",
        input_text="Check battery level",
        model_decision="TOOL_CALL",
        selected_tool="system.battery",
        arguments={"detail": True},
        outcome="SUCCESS",
        evidence=[{"source": "battery", "level": 85}],
        verifier_result="VERIFIED",
        user_feedback="CORRECT",
        category="system",
    )
    assert exp_normal.learning_eligible is True
    assert exp_normal.privacy_class == "INTERNAL"
    assert exp_normal.quality_score >= 0.9

    # 2. Sensitive experience with leaked API key / password
    exp_sensitive = store.record_experience(
        session_id="sess-002",
        input_text="My secret API key is sk-1234567890abcdef1234567890 please save it",
        model_decision="ANSWER",
        arguments={"token": "bearer secret_tok_123"},
        outcome="SUCCESS",
        category="secret",
    )
    assert exp_sensitive.learning_eligible is False
    assert exp_sensitive.privacy_class == "SENSITIVE"

    # Verify query excludes sensitive records
    eligible = store.list_eligible_experiences(min_quality=0.5, limit=50)
    eligible_ids = [e.experience_id for e in eligible]
    assert exp_normal.experience_id in eligible_ids
    assert exp_sensitive.experience_id not in eligible_ids


# ----------------------------------------------------------------------
# 6. Candidate Dataset Pipeline & Training Runner
# ----------------------------------------------------------------------

def test_learning_pipeline_and_training_runner(tmp_path):
    """Verifies dataset extraction, formatting, manifest generation, and training job execution."""
    store = AuraExperienceStore()
    pipeline = LearningCandidatePipeline(store=store, data_dir=str(tmp_path / "data"))

    # Populate verified experience
    store.record_experience(
        session_id="sess-pipeline-1",
        input_text="Launch Settings app",
        model_decision="TOOL_CALL",
        selected_tool="android.launch_app",
        arguments={"package_name": "com.android.settings"},
        outcome="SUCCESS",
        evidence=[{"app": "com.android.settings"}],
        verifier_result="VERIFIED",
        category="android",
    )

    manifest = pipeline.generate_candidate_dataset(min_quality=0.5, dataset_name="test_candidate_dataset")
    assert manifest is not None
    assert manifest.num_examples >= 1
    assert os.path.exists(manifest.file_path)
    assert manifest.checksum != ""

    # Setup manager with base package
    mgr = BrainManager(brains_dir=str(tmp_path / "brains"))
    base_manifest = BrainManifest(
        brain_id="aura-local-v1",
        version="1.0.0",
        model_format="deterministic",
        status=BrainStatus.ACTIVE.value,
    )
    mgr.register_package(base_manifest, weight_content=b"BASE_WEIGHTS")
    mgr.promote_candidate("aura-local-v1")

    runner = TrainingJobRunner(manager=mgr)
    job = runner.create_job(
        base_brain_id="aura-local-v1",
        dataset_path=manifest.file_path,
        output_brain_id="aura-candidate-v2",
        output_version="1.2.0",
    )
    assert job.status == "PENDING"

    completed = runner.run_job(job.job_id)
    assert completed.status == "COMPLETED"
    assert completed.metrics.get("examples_trained", 0) >= 1

    candidate_pkg = mgr.get_package("aura-candidate-v2")
    assert candidate_pkg is not None
    assert candidate_pkg.status == BrainStatus.CANDIDATE.value


# ----------------------------------------------------------------------
# 7. Candidate Evaluation, Rejection of Regression & Promotion/Rollback
# ----------------------------------------------------------------------

def test_candidate_promotion_and_regression_rejection(tmp_path):
    """Verifies evaluator catches regressed candidates, promotes good candidates, and supports rollback."""
    mgr = BrainManager(brains_dir=str(tmp_path / "brains"))
    base_manifest = BrainManifest(
        brain_id="aura-local-v1",
        version="1.0.0",
        model_format="deterministic",
        status=BrainStatus.ACTIVE.value,
    )
    mgr.register_package(base_manifest, weight_content=b"BASE_WEIGHTS")
    mgr.promote_candidate("aura-local-v1")

    coord = LearningCoordinator(manager=mgr)
    active_before = mgr.get_active_package()
    assert active_before is not None

    # 1. Reject bad candidate with injected regression
    runner = TrainingJobRunner(manager=mgr)
    dataset_file = tmp_path / "dummy.jsonl"
    dataset_file.write_text('{"messages": [{"role": "user", "content": "hi"}]}\n', encoding="utf-8")

    regressed_job = runner.create_job(
        base_brain_id=active_before.brain_id,
        dataset_path=str(dataset_file),
        output_brain_id="aura-bad-candidate",
    )
    runner.run_job(regressed_job.job_id, inject_regression=True)

    ok, comp = coord.evaluate_and_promote(candidate_brain_id="aura-bad-candidate")
    assert ok is False
    assert comp.is_promotable is False
    assert len(comp.rejection_reasons) > 0
    bad_pkg = mgr.get_package("aura-bad-candidate")
    assert bad_pkg.status == BrainStatus.REJECTED.value

    # 2. Promote good candidate
    good_job = runner.create_job(
        base_brain_id=active_before.brain_id,
        dataset_path=str(dataset_file),
        output_brain_id="aura-good-candidate",
        output_version="1.2.0",
    )
    runner.run_job(good_job.job_id, inject_regression=False)

    ok_good, comp_good = coord.evaluate_and_promote(candidate_brain_id="aura-good-candidate")
    assert ok_good is True
    assert comp_good.is_promotable is True
    promoted_pkg = mgr.get_package("aura-good-candidate")
    assert promoted_pkg.status == BrainStatus.ACTIVE.value
    assert mgr.state.get("active_brain_id") == "aura-good-candidate"
    assert mgr.state.get("previous_brain_id") == active_before.brain_id

    # 3. Rollback
    rolled_back = coord.rollback(reason="Test rollback verification")
    assert rolled_back is True
    assert mgr.state.get("active_brain_id") == active_before.brain_id
    reverted_pkg = mgr.get_package("aura-good-candidate")
    assert reverted_pkg.status == BrainStatus.ROLLED_BACK.value


# ----------------------------------------------------------------------
# 8. 24/7 Persistent Daemon Lifecycle & Health
# ----------------------------------------------------------------------

def test_daemon_lifecycle_and_subsystem_health():
    """Verifies that AuraDaemon monitors all subsystems and responds to event wakeups."""
    daemon = AuraDaemon(offline=True, poll_interval=0.1)
    health = daemon.get_health()

    # Prior to start, daemon status is DEGRADED (idle)
    assert health.daemon == SubsystemHealth.DEGRADED.value
    assert health.brain in (SubsystemHealth.HEALTHY.value, SubsystemHealth.DEGRADED.value)
    assert health.offline is True

    daemon.start()
    assert daemon.is_running is True
    assert daemon.get_health().daemon == SubsystemHealth.HEALTHY.value

    # Trigger wake event
    daemon.wakeup()
    daemon.step_once()

    daemon.stop()
    assert daemon.is_running is False


# ----------------------------------------------------------------------
# 9. Server Routes Integration (FastAPI Endpoints)
# ----------------------------------------------------------------------

def test_brain_and_learning_api_endpoints():
    """Verifies GET /api/brain, GET /api/brain/health, GET /api/learning/status."""
    client = TestClient(app)

    # 1. Brain Info
    resp = client.get("/api/brain")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "online"
    assert "hardware" in data
    assert "active_brain" in data

    # 2. Brain Health
    health_resp = client.get("/api/brain/health")
    assert health_resp.status_code == 200
    health_data = health_resp.json()
    assert health_data["status"] in ("HEALTHY", "DEGRADED")

    # 3. Learning Status
    learning_resp = client.get("/api/learning/status")
    assert learning_resp.status_code == 200
    learning_data = learning_resp.json()
    assert learning_data["status"] == "active"
    assert "total_experiences" in learning_data
    assert "eligible_experiences" in learning_data
