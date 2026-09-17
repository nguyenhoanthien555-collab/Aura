"""
Hardware capability and resource profiling for AURA Local Brain.

Inspects available system resources (CPU, RAM, GPU, VRAM, Disk) to dynamically
recommend compatible Brain packages, calculate safe context bounds, and select
appropriate inference backends without hard-coding specific machines.
"""

from dataclasses import dataclass
import os
import platform
import shutil
import subprocess
from typing import Optional


@dataclass
class HardwareProfile:
    """System hardware profile."""

    os_name: str
    os_release: str
    cpu_cores: int
    ram_total_mb: int
    ram_available_mb: int
    gpu_available: bool
    gpu_name: Optional[str] = None
    gpu_vram_total_mb: Optional[int] = None
    gpu_vram_free_mb: Optional[int] = None
    disk_free_mb: int = 0
    recommended_tier: str = "small"  # "tiny", "small", "medium", "large"
    maximum_safe_context: int = 4096
    recommended_backend: str = "cpu"  # "cuda", "rocm", "metal", "cpu", "mock"

    def to_dict(self) -> dict:
        return {
            "os_name": self.os_name,
            "os_release": self.os_release,
            "cpu_cores": self.cpu_cores,
            "ram_total_mb": self.ram_total_mb,
            "ram_available_mb": self.ram_available_mb,
            "gpu_available": self.gpu_available,
            "gpu_name": self.gpu_name,
            "gpu_vram_total_mb": self.gpu_vram_total_mb,
            "gpu_vram_free_mb": self.gpu_vram_free_mb,
            "disk_free_mb": self.disk_free_mb,
            "recommended_tier": self.recommended_tier,
            "maximum_safe_context": self.maximum_safe_context,
            "recommended_backend": self.recommended_backend,
        }


def _get_ram_info() -> tuple[int, int]:
    """Returns (total_ram_mb, avail_ram_mb) across platforms."""
    sys_name = platform.system()
    if sys_name == "Windows":
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return int(stat.ullTotalPhys // (1024 * 1024)), int(stat.ullAvailPhys // (1024 * 1024))
        except Exception:
            pass

    elif sys_name in ("Linux", "Darwin"):
        try:
            total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
            avail = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES")
            return int(total // (1024 * 1024)), int(avail // (1024 * 1024))
        except Exception:
            pass

    # Safe fallback
    return 8192, 4096


def _get_gpu_info() -> tuple[bool, Optional[str], Optional[int], Optional[int]]:
    """Probes GPU via nvidia-smi or system tools."""
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if res.returncode == 0 and res.stdout.strip():
            lines = res.stdout.strip().split("\n")
            first_gpu = lines[0].split(",")
            if len(first_gpu) >= 3:
                name = first_gpu[0].strip()
                total = int(first_gpu[1].strip())
                free = int(first_gpu[2].strip())
                return True, name, total, free
    except Exception:
        pass

    return False, None, None, None


def detect_hardware(target_dir: str = ".") -> HardwareProfile:
    """Inspects the local host and returns a HardwareProfile."""
    os_name = platform.system()
    os_release = platform.release()
    cpu_cores = os.cpu_count() or 4

    ram_total_mb, ram_avail_mb = _get_ram_info()
    gpu_avail, gpu_name, gpu_total_mb, gpu_free_mb = _get_gpu_info()

    disk_free_mb = 0
    try:
        total, used, free = shutil.disk_usage(os.path.abspath(target_dir))
        disk_free_mb = int(free // (1024 * 1024))
    except Exception:
        disk_free_mb = 10240

    # Determine recommended tier and backend
    if gpu_avail and gpu_total_mb and gpu_total_mb >= 12000:
        recommended_tier = "large"
        recommended_backend = "cuda"
        max_context = 16384
    elif gpu_avail and gpu_total_mb and gpu_total_mb >= 6000:
        recommended_tier = "medium"
        recommended_backend = "cuda"
        max_context = 8192
    elif gpu_avail and gpu_total_mb and gpu_total_mb >= 3000:
        recommended_tier = "small"
        recommended_backend = "cuda"
        max_context = 4096
    elif ram_total_mb >= 16000:
        recommended_tier = "medium"
        recommended_backend = "cpu"
        max_context = 4096
    elif ram_total_mb >= 8000:
        recommended_tier = "small"
        recommended_backend = "cpu"
        max_context = 2048
    else:
        recommended_tier = "tiny"
        recommended_backend = "cpu"
        max_context = 1024

    return HardwareProfile(
        os_name=os_name,
        os_release=os_release,
        cpu_cores=cpu_cores,
        ram_total_mb=ram_total_mb,
        ram_available_mb=ram_avail_mb,
        gpu_available=gpu_avail,
        gpu_name=gpu_name,
        gpu_vram_total_mb=gpu_total_mb,
        gpu_vram_free_mb=gpu_free_mb,
        disk_free_mb=disk_free_mb,
        recommended_tier=recommended_tier,
        maximum_safe_context=max_context,
        recommended_backend=recommended_backend,
    )
