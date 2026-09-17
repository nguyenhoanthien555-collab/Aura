"""
Autonomous Synthesis Policy for AURA 2.0 (Phase 5B.3).

Enforces deterministic boundaries for autonomous self-extension:
1. Synthesizability state verification (only SYNTHESIZABLE gaps qualify).
2. Platform restriction (local host execution only; no Android code generation).
3. Risk boundary (strictly SAFE tools; sensitive/dangerous prohibited from auto-synthesis).
4. Safety & security keyword filtering (blocks dangerous/mutating/shell/network commands).
5. Recursion depth bounding (synthesized tools cannot invoke self-extension).
6. Concurrency deduplication (in-flight synthesis locks prevent duplicate synthesis).
"""

import re
import threading
from typing import Dict, List, Optional, Set, Tuple

from core.capabilities.gap import CapabilityGap, CapabilityMatchState
from core.logger import logger
from tools.base import ToolRisk
from tools.registry import ToolRegistry


SAFE_PLATFORMS: frozenset[str] = frozenset({
    "local",
    "python",
    "win32",
    "linux",
    "darwin",
})

SAFE_RISKS: frozenset[str] = frozenset({
    "safe",
    ToolRisk.SAFE.value,
})

PROHIBITED_INTENT_TOKENS: frozenset[str] = frozenset({
    "subprocess",
    "rm",
    "delete",
    "remove",
    "kill",
    "shell",
    "exec",
    "eval",
    "compile",
    "token",
    "password",
    "secret",
    "key",
    "sms",
    "email",
    "phone",
    "network",
    "http",
    "download",
    "curl",
    "wget",
    "format",
    "shutil",
    "os.system",
    "os.popen",
    "socket",
    "urllib",
    "requests",
    "httpx",
    "drop",
    "wipe",
})


class AutonomousSynthesisPolicy:
    """
    Deterministic gatekeeper for autonomous capability gap synthesis.
    """

    def __init__(
        self,
        allowed_platforms: Optional[Set[str]] = None,
        allowed_risks: Optional[Set[str]] = None,
        prohibited_tokens: Optional[Set[str]] = None,
        max_depth: int = 1,
    ):
        self.allowed_platforms = set(allowed_platforms) if allowed_platforms else set(SAFE_PLATFORMS)
        self.allowed_risks = set(allowed_risks) if allowed_risks else set(SAFE_RISKS)
        self.prohibited_tokens = set(prohibited_tokens) if prohibited_tokens else set(PROHIBITED_INTENT_TOKENS)
        self.max_depth = max_depth

        self._in_flight: Dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def is_eligible(
        self,
        gap: CapabilityGap,
        depth: int = 0,
        registry: Optional[ToolRegistry] = None,
    ) -> Tuple[bool, str]:
        """
        Deterministically evaluates whether a CapabilityGap is permitted to be
        autonomously synthesized and promoted.
        """
        # 1. Bounded recursion depth limit
        if depth >= self.max_depth:
            return False, f"Recursion depth {depth} reaches or exceeds max_depth {self.max_depth}"

        # 2. Check if tool or capability already exists
        target_name = (
            gap.requested_capability.replace(".", "_").replace("-", "_").lower()
        )
        if registry is not None and registry.has(target_name):
            return False, f"Tool '{target_name}' already exists in registry"

        # 3. Gap synthesizability check
        is_synth = (
            gap.is_synthesizable
            or gap.match_state in (
                CapabilityMatchState.SYNTHESIZABLE.value,
                CapabilityMatchState.SYNTHESIZABLE,
                "SYNTHESIZABLE",
                "NO_CAPABILITY_EXISTS",
            )
        )
        if not is_synth:
            return False, f"Gap match state '{gap.match_state}' is not synthesizable"

        # 4. Platform check (strictly local / host Python)
        plat = (gap.platform or "local").lower()
        if plat not in self.allowed_platforms:
            return (
                False,
                f"Platform '{gap.platform}' not eligible for autonomous synthesis (local Python only)",
            )

        # 5. Risk level check (strictly safe)
        risk = (gap.risk or "safe").lower()
        if risk not in self.allowed_risks:
            return (
                False,
                f"Risk '{gap.risk}' not eligible for autonomous synthesis (strictly safe tools only)",
            )

        # 6. Prohibited tokens check (intent, capability name, reason)
        text_corpus = f"{gap.intent} {gap.requested_capability} {gap.reason}".lower()
        for tok in self.prohibited_tokens:
            pattern = rf"\b{re.escape(tok)}\b" if tok.isalpha() else re.escape(tok)
            if re.search(pattern, text_corpus):
                return False, f"Prohibited safety/security token '{tok}' detected in request"

        return True, ""

    def acquire_synthesis_lock(
        self, capability_name: str
    ) -> Tuple[bool, Optional[threading.Event]]:
        """
        Attempts to acquire the synthesis lock for a capability.
        Returns:
            (True, event) if this caller won the lock and must execute synthesis.
            (False, event) if synthesis is already in flight; caller should wait on event.
        """
        normalized = capability_name.strip().lower()
        with self._lock:
            if normalized in self._in_flight:
                return False, self._in_flight[normalized]
            ev = threading.Event()
            self._in_flight[normalized] = ev
            return True, ev

    def release_synthesis_lock(self, capability_name: str) -> None:
        """
        Releases the synthesis lock and unblocks any waiting concurrent threads.
        """
        normalized = capability_name.strip().lower()
        with self._lock:
            ev = self._in_flight.pop(normalized, None)
            if ev is not None:
                ev.set()
