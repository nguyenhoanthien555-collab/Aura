"""
Host hardware & system environment probe.

Probes the machine running Aura (hostname, manufacturer, model, OS, CPU, RAM,
GPU, disk storage, machine UUID, network interfaces, and current user) and
persists facts into SQLite ProfileStore under category="system".
"""

import getpass
import os
import platform
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

try:
    import psutil
except ImportError:
    psutil = None

from core.logger import logger


@dataclass
class HostEnvironment:
    """Detailed host hardware and environment specifications."""

    hostname: str = ""
    manufacturer: str = ""
    model: str = ""
    os_name: str = ""
    os_version: str = ""
    cpu_name: str = ""
    cpu_cores_logical: int = 0
    cpu_cores_physical: int = 0
    ram_total_gb: float = 0.0
    ram_available_gb: float = 0.0
    gpus: list[str] = field(default_factory=list)
    primary_storage_total_gb: float = 0.0
    primary_storage_free_gb: float = 0.0
    machine_uuid: str = ""
    network_interfaces: list[str] = field(default_factory=list)
    username: str = ""

    def to_facts(self) -> dict[str, str]:
        """Convert environment properties into stable slug-value pairs for ProfileStore."""
        facts = {
            "system_hostname": self.hostname or "Unknown",
            "system_os": f"{self.os_name} {self.os_version}".strip(),
            "system_cpu": f"{self.cpu_name} ({self.cpu_cores_logical} threads, {self.cpu_cores_physical} cores)".strip(),
            "system_ram": f"{self.ram_total_gb} GB total ({self.ram_available_gb} GB free)",
            "system_storage": f"{self.primary_storage_free_gb} GB free of {self.primary_storage_total_gb} GB",
            "system_username": self.username or "user",
        }
        if self.manufacturer or self.model:
            facts["system_model"] = f"{self.manufacturer} {self.model}".strip()
        if self.gpus:
            facts["system_gpu"] = ", ".join(self.gpus)
        if self.machine_uuid:
            facts["system_uuid"] = self.machine_uuid
        if self.network_interfaces:
            facts["system_network"] = ", ".join(self.network_interfaces[:5])
        return facts

    def summary_lines(self) -> list[str]:
        """Render prompt-ready lines for HOST ENVIRONMENT prompt section."""
        lines = [
            f"Machine: {self.hostname} ({self.manufacturer} {self.model})".strip(),
            f"OS: {self.os_name} {self.os_version}",
            f"CPU: {self.cpu_name} ({self.cpu_cores_logical} vCPUs, {self.cpu_cores_physical} physical)",
            f"RAM: {self.ram_total_gb} GB total ({self.ram_available_gb} GB available)",
            f"GPU: {', '.join(self.gpus) if self.gpus else 'Integrated/None'}",
            f"Storage: {self.primary_storage_free_gb} GB free / {self.primary_storage_total_gb} GB total",
            f"Machine UUID: {self.machine_uuid}",
            f"Current User: {self.username}",
        ]
        return [line for line in lines if line]


def _run_powershell_cmd(cmd: str, timeout: float = 5.0) -> list[str]:
    """Execute a PowerShell command safely returning trimmed output lines."""
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", cmd],
            text=True,
            timeout=timeout,
            stderr=subprocess.DEVNULL,
        )
        return [line.strip() for line in out.strip().splitlines() if line.strip()]
    except Exception as exc:
        logger.debug("PowerShell command failed: %s (%s)", cmd, exc)
        return []


def probe_host_environment() -> HostEnvironment:
    """
    Probe the local host environment with deep Windows WMI/CIM inspection
    and graceful cross-platform / Linux fallback.
    """
    hostname = platform.node()
    os_name = platform.system()
    os_version = f"{platform.release()} (Build {platform.version()})"
    username = getpass.getuser()

    # CPU info
    if psutil is not None:
        cpu_cores_logical = psutil.cpu_count(logical=True) or 1
        cpu_cores_physical = psutil.cpu_count(logical=False) or 1
    else:
        cpu_cores_logical = os.cpu_count() or 1
        cpu_cores_physical = 1
    cpu_name = platform.processor() or "Unknown CPU"

    # Memory info
    if psutil is not None:
        mem = psutil.virtual_memory()
        ram_total_gb = round(mem.total / (1024**3), 1)
        ram_available_gb = round(mem.available / (1024**3), 1)
    else:
        ram_total_gb = 0.0
        ram_available_gb = 0.0

    # Storage info
    primary_drive = "C:\\" if os.name == "nt" else "/"
    if psutil is not None:
        try:
            disk = psutil.disk_usage(primary_drive)
            storage_total_gb = round(disk.total / (1024**3), 1)
            storage_free_gb = round(disk.free / (1024**3), 1)
        except Exception:
            storage_total_gb = 0.0
            storage_free_gb = 0.0
    else:
        storage_total_gb = 0.0
        storage_free_gb = 0.0

    # Network adapters
    active_nets = []
    if psutil is not None:
        try:
            net_addrs = psutil.net_if_addrs()
            net_stats = psutil.net_if_stats()
            active_nets = [
                nic for nic, stat in net_stats.items()
                if stat.isup and nic in net_addrs and not nic.startswith("Loopback")
            ]
        except Exception:
            active_nets = []

    manufacturer = ""
    model = ""
    machine_uuid = ""
    gpus: list[str] = []

    if os.name == "nt":
        # Deep Windows probe via PowerShell CIM instances
        cpu_lines = _run_powershell_cmd("(Get-CimInstance Win32_Processor).Name")
        if cpu_lines:
            cpu_name = cpu_lines[0]

        mfg_lines = _run_powershell_cmd("(Get-CimInstance Win32_ComputerSystem).Manufacturer")
        if mfg_lines:
            manufacturer = mfg_lines[0]

        model_lines = _run_powershell_cmd("(Get-CimInstance Win32_ComputerSystem).Model")
        if model_lines:
            model = model_lines[0]

        gpu_lines = _run_powershell_cmd("(Get-CimInstance Win32_VideoController).Caption")
        if gpu_lines:
            gpus = [g for g in gpu_lines if g]

        uuid_lines = _run_powershell_cmd("(Get-CimInstance Win32_ComputerSystemProduct).UUID")
        if uuid_lines:
            machine_uuid = uuid_lines[0]

    # Fallback for UUID if WMI missing or on non-Windows
    if not machine_uuid or machine_uuid.lower() == "none":
        try:
            node_id = uuid.getnode()
            machine_uuid = str(uuid.UUID(int=node_id))
        except Exception:
            machine_uuid = "unknown-uuid"

    return HostEnvironment(
        hostname=hostname,
        manufacturer=manufacturer,
        model=model,
        os_name=os_name,
        os_version=os_version,
        cpu_name=cpu_name,
        cpu_cores_logical=cpu_cores_logical,
        cpu_cores_physical=cpu_cores_physical,
        ram_total_gb=ram_total_gb,
        ram_available_gb=ram_available_gb,
        gpus=gpus,
        primary_storage_total_gb=storage_total_gb,
        primary_storage_free_gb=storage_free_gb,
        machine_uuid=machine_uuid,
        network_interfaces=active_nets,
        username=username,
    )


def probe_and_persist(
    profile_store,
    force: bool = False,
    show_ui: bool = True,
) -> HostEnvironment:
    """
    Probe the host environment and persist into ProfileStore if not already present
    or if force is True.
    """
    if profile_store is None:
        return probe_host_environment()

    existing_facts = profile_store.by_category("system")
    if existing_facts and not force:
        logger.debug("Host hardware facts already present in memory (%d facts)", len(existing_facts))
        return probe_host_environment()

    if show_ui:
        try:
            from rich.console import Console
            from rich.panel import Panel
            from rich.table import Table

            console = Console()
            console.print(
                "[bold cyan]⚡ AURA HARDWARE PROBE[/bold cyan] Scanning host environment and machine specifications..."
            )
        except Exception:
            show_ui = False

    env = probe_host_environment()
    facts = env.to_facts()

    for key, val in facts.items():
        profile_store.remember(
            key=key,
            value=val,
            category="system",
            source="system_probe",
        )

    logger.info("Persisted %d host environment facts to ProfileStore", len(facts))

    if show_ui:
        try:
            from rich.console import Console
            from rich.table import Table

            console = Console()
            table = Table(title="[bold green]✓ Host Hardware Memory Initialized[/bold green]", box=None)
            table.add_column("Property", style="cyan", no_wrap=True)
            table.add_column("Value", style="white")

            table.add_row("Hostname", env.hostname)
            if env.manufacturer or env.model:
                table.add_row("Model", f"{env.manufacturer} {env.model}".strip())
            table.add_row("OS", f"{env.os_name} {env.os_version}")
            table.add_row("CPU", env.cpu_name)
            table.add_row("RAM", f"{env.ram_total_gb} GB")
            if env.gpus:
                table.add_row("GPU", ", ".join(env.gpus))
            table.add_row("Storage", f"{env.primary_storage_free_gb} GB free / {env.primary_storage_total_gb} GB")
            table.add_row("Machine UUID", env.machine_uuid)
            table.add_row("User", env.username)

            console.print(table)
        except Exception:
            pass

    return env
