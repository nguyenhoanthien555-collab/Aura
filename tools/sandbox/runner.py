"""
Isolated sandbox runner for candidate tool validation and safe execution.

Ensures that candidate tool code runs in an isolated subprocess with:
1. Scrubbed environment variables (preventing secret exfiltration)
2. Wall-clock timeout enforcement (preventing hangs)
3. Captured stdout, stderr, and return codes
4. Clean directory lifecycle
"""

import os
import re
import sys
import time
import shutil
import tempfile
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

from core.logger import logger

# Patterns that indicate secrets in environment variables
SENSITIVE_PATTERNS = re.compile(
    r"(KEY|SECRET|TOKEN|PASS|AUTH|CREDENTIAL|PRIVATE|SIG|JWT|BEARER)",
    re.IGNORECASE,
)

# Standard essential system variables to preserve on Windows/POSIX
PRESERVED_ENV_KEYS: Set[str] = {
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "OS",
    "PROCESSOR_ARCHITECTURE",
    "NUMBER_OF_PROCESSORS",
}


@dataclass(frozen=True)
class SandboxResult:
    """Outcome of sandboxed execution."""

    ok: bool
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    duration: float = 0.0
    error: str = ""


class SandboxRunner:
    """
    Executes Python snippets or commands in an isolated, scrubbed subprocess.
    """

    def __init__(
        self,
        default_timeout: float = 10.0,
        repo_root: Optional[str] = None,
    ):
        self.default_timeout = default_timeout
        self.repo_root = repo_root or str(Path(__file__).resolve().parents[2])

    def child_environment(self, extra_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """
        Build an environment dict with sensitive keys removed.
        """
        clean_env: Dict[str, str] = {}

        for k, v in os.environ.items():
            upper_k = k.upper()
            # If it's a known essential system variable, keep it
            if upper_k in PRESERVED_ENV_KEYS:
                clean_env[k] = v
            # Otherwise, skip if it matches sensitive patterns
            elif not SENSITIVE_PATTERNS.search(k):
                clean_env[k] = v

        # Set PYTHONPATH to include the project root so imports resolve
        existing_pp = clean_env.get("PYTHONPATH", "")
        if self.repo_root not in existing_pp:
            clean_env["PYTHONPATH"] = (
                f"{self.repo_root}{os.pathsep}{existing_pp}" if existing_pp else self.repo_root
            )

        if extra_env:
            clean_env.update(extra_env)

        return clean_env

    def execute_code(
        self,
        code: str,
        timeout: Optional[float] = None,
        extra_env: Optional[Dict[str, str]] = None,
        args: Optional[List[str]] = None,
    ) -> SandboxResult:
        """
        Write Python code to a temporary file in an isolated directory and execute it.
        """
        effective_timeout = timeout if timeout is not None else self.default_timeout
        env = self.child_environment(extra_env)
        start_time = time.monotonic()

        with tempfile.TemporaryDirectory(prefix="aura_sandbox_") as tmp_dir:
            script_path = os.path.join(tmp_dir, "candidate.py")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(code)

            cmd = [sys.executable, script_path]
            if args:
                cmd.extend(args)

            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=tmp_dir,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )

                try:
                    stdout, stderr = proc.communicate(timeout=effective_timeout)
                    duration = time.monotonic() - start_time
                    return SandboxResult(
                        ok=(proc.returncode == 0),
                        exit_code=proc.returncode,
                        stdout=stdout or "",
                        stderr=stderr or "",
                        timed_out=False,
                        duration=duration,
                        error="" if proc.returncode == 0 else f"Process exited with code {proc.returncode}",
                    )
                except subprocess.TimeoutExpired:
                    proc.kill()
                    stdout, stderr = proc.communicate()
                    duration = time.monotonic() - start_time
                    logger.warning("Sandbox execution timed out after %.2fs", effective_timeout)
                    return SandboxResult(
                        ok=False,
                        exit_code=-1,
                        stdout=stdout or "",
                        stderr=stderr or "",
                        timed_out=True,
                        duration=duration,
                        error=f"Execution timed out after {effective_timeout}s",
                    )

            except Exception as exc:
                duration = time.monotonic() - start_time
                return SandboxResult(
                    ok=False,
                    exit_code=-1,
                    stdout="",
                    stderr=str(exc),
                    timed_out=False,
                    duration=duration,
                    error=str(exc),
                )

    def execute_command(
        self,
        command: List[str],
        timeout: Optional[float] = None,
        cwd: Optional[str] = None,
        extra_env: Optional[Dict[str, str]] = None,
    ) -> SandboxResult:
        """
        Execute an arbitrary command in a scrubbed environment.
        """
        effective_timeout = timeout if timeout is not None else self.default_timeout
        env = self.child_environment(extra_env)
        start_time = time.monotonic()

        try:
            proc = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            try:
                stdout, stderr = proc.communicate(timeout=effective_timeout)
                duration = time.monotonic() - start_time
                return SandboxResult(
                    ok=(proc.returncode == 0),
                    exit_code=proc.returncode,
                    stdout=stdout or "",
                    stderr=stderr or "",
                    timed_out=False,
                    duration=duration,
                    error="" if proc.returncode == 0 else f"Command failed with code {proc.returncode}",
                )
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
                duration = time.monotonic() - start_time
                return SandboxResult(
                    ok=False,
                    exit_code=-1,
                    stdout=stdout or "",
                    stderr=stderr or "",
                    timed_out=True,
                    duration=duration,
                    error=f"Command timed out after {effective_timeout}s",
                )
        except Exception as exc:
            duration = time.monotonic() - start_time
            return SandboxResult(
                ok=False,
                exit_code=-1,
                stdout="",
                stderr=str(exc),
                timed_out=False,
                duration=duration,
                error=str(exc),
            )
