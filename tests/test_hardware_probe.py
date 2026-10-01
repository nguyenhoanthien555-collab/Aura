"""
Unit tests for host hardware probe and persistence into ProfileStore.
"""

from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.hardware_probe import (
    HostEnvironment,
    probe_and_persist,
    probe_host_environment,
)
from memory.models import Base
from memory.profile import ProfileStore


@pytest.fixture
def session():
    """Isolated in-memory database."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sess = sessionmaker(bind=engine)()
    yield sess
    sess.close()


@pytest.fixture
def profile_store(session):
    return ProfileStore(session=session)


def test_probe_host_environment_live():
    """Probe should discover current system specs without throwing."""
    env = probe_host_environment()
    assert isinstance(env, HostEnvironment)
    assert env.hostname != ""
    assert env.os_name != ""
    assert env.cpu_cores_logical >= 1
    assert env.ram_total_gb > 0
    assert env.machine_uuid != ""


def test_host_environment_to_facts():
    """Environment dataclass converts cleanly to ProfileStore slugs."""
    env = HostEnvironment(
        hostname="TestRig",
        manufacturer="Acme Corp",
        model="SuperServer 9000",
        os_name="Windows",
        os_version="11 Pro",
        cpu_name="FastCPU 16-Core",
        cpu_cores_logical=16,
        cpu_cores_physical=8,
        ram_total_gb=32.0,
        ram_available_gb=20.5,
        gpus=["NVIDIA GeForce RTX 4090"],
        primary_storage_total_gb=1000.0,
        primary_storage_free_gb=500.0,
        machine_uuid="12345678-ABCD-EF01-2345-6789ABCDEF01",
        network_interfaces=["Ethernet", "Wi-Fi"],
        username="developer",
    )
    facts = env.to_facts()
    assert facts["system_hostname"] == "TestRig"
    assert "SuperServer 9000" in facts["system_model"]
    assert "Windows 11 Pro" in facts["system_os"]
    assert "FastCPU" in facts["system_cpu"]
    assert "32.0 GB total" in facts["system_ram"]
    assert "RTX 4090" in facts["system_gpu"]
    assert facts["system_uuid"] == "12345678-ABCD-EF01-2345-6789ABCDEF01"
    assert facts["system_username"] == "developer"


def test_host_environment_summary_lines():
    """Summary lines format appropriately for system prompt injection."""
    env = HostEnvironment(
        hostname="LaptopAlpha",
        manufacturer="Dell",
        model="XPS 15",
        os_name="Windows",
        os_version="11",
        cpu_name="Core i9",
        cpu_cores_logical=14,
        cpu_cores_physical=10,
        ram_total_gb=16.0,
        ram_available_gb=8.0,
        gpus=["RTX 4070"],
        primary_storage_total_gb=512.0,
        primary_storage_free_gb=200.0,
        machine_uuid="XYZ-123",
        username="alice",
    )
    lines = env.summary_lines()
    assert any("Machine: LaptopAlpha" in line for line in lines)
    assert any("Dell XPS 15" in line for line in lines)
    assert any("RTX 4070" in line for line in lines)
    assert any("XYZ-123" in line for line in lines)


def test_probe_and_persist_lifecycle(profile_store):
    """probe_and_persist should store facts on first run and skip on subsequent runs unless forced."""
    mock_env = HostEnvironment(
        hostname="MockHost",
        manufacturer="MockVendor",
        model="MockModel",
        os_name="Linux",
        os_version="6.5",
        cpu_name="MockCPU",
        cpu_cores_logical=8,
        cpu_cores_physical=4,
        ram_total_gb=16.0,
        ram_available_gb=12.0,
        gpus=["MockGPU"],
        primary_storage_total_gb=256.0,
        primary_storage_free_gb=128.0,
        machine_uuid="MOCK-UUID-001",
        network_interfaces=["eth0"],
        username="tester",
    )

    with patch("core.hardware_probe.probe_host_environment", return_value=mock_env) as mock_probe:
        # First run: no system facts, should probe and persist
        env1 = probe_and_persist(profile_store, force=False, show_ui=False)
        assert mock_probe.call_count == 1
        facts = profile_store.by_category("system")
        assert len(facts) >= 6
        assert profile_store.get("system_hostname") == "MockHost"
        assert profile_store.get("system_uuid") == "MOCK-UUID-001"

        # Second run: facts present, should NOT re-persist
        mock_probe.reset_mock()
        env2 = probe_and_persist(profile_store, force=False, show_ui=False)
        assert mock_probe.call_count == 1  # probe_host_environment called to return env, but no re-persistence
        # facts still present
        assert profile_store.get("system_hostname") == "MockHost"

        # Third run: force=True, should re-persist
        mock_probe.reset_mock()
        mock_env.hostname = "UpdatedMockHost"
        env3 = probe_and_persist(profile_store, force=True, show_ui=False)
        assert mock_probe.call_count == 1
        assert profile_store.get("system_hostname") == "UpdatedMockHost"


def test_probe_and_persist_handles_none_store():
    """probe_and_persist should not crash if profile_store is None."""
    env = probe_and_persist(None, force=False, show_ui=False)
    assert isinstance(env, HostEnvironment)
