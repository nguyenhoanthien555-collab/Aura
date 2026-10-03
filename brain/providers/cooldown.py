"""
Provider Cooldown State Machine for Aura Brain.

Maintains cooldown states for providers that fail with credentials/auth errors
or rate limit/quota exhaustion, preventing request storms across fallback chains.
Automatically clears cooldown when provider credentials change.
"""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable, Optional

from brain.providers.errors import (
    ProviderAuthError,
    ProviderRateLimitError,
)

AUTH_COOLDOWN_S: float = 1800.0          # 30 min for auth failures
RATE_LIMIT_DEFAULT_S: float = 60.0       # 60s for generic rate limits
ACCOUNT_LIMIT_COOLDOWN_S: float = 3600.0 # 60 min for account quota exhaustion


@dataclass
class CooldownEntry:
    until: float          # monotonic seconds
    reason: str           # e.g. "AUTH_FORBIDDEN", "RATE_LIMITED", "QUOTA_EXHAUSTED"
    fingerprint: str      # credential fingerprint at time of failure ("" if unknown)
    error: Exception      # original error, re-raised when skipped / all down


class ProviderCooldowns:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self._entries: dict[str, CooldownEntry] = {}

    def check(self, name: str, fingerprint: str = "") -> Optional[CooldownEntry]:
        """
        Returns active entry if provider is in cooldown.
        If credential fingerprint changed since failure, clears cooldown immediately and returns None.
        If cooldown period has expired, clears cooldown and returns None.
        """
        entry = self._entries.get(name)
        if not entry:
            return None

        # Recovery on credential update: if a credential fingerprint is present and differs,
        # invalidate cooldown immediately!
        if entry.fingerprint and fingerprint and entry.fingerprint != fingerprint:
            del self._entries[name]
            return None

        now = self.clock()
        if now >= entry.until:
            del self._entries[name]
            return None

        return entry

    def record(self, name: str, error: Exception, fingerprint: str = "") -> Optional[CooldownEntry]:
        """
        Records a cooldown for typed auth or rate-limit failures.
        Returns the recorded CooldownEntry, or None if the error does not trigger cooldown.
        """
        now = self.clock()
        duration: Optional[float] = None
        reason: str = "ERROR"

        if isinstance(error, ProviderAuthError):
            duration = AUTH_COOLDOWN_S
            reason = getattr(error, "reason", "AUTH_ERROR") or "AUTH_ERROR"

        elif isinstance(error, ProviderRateLimitError):
            retry_after = getattr(error, "retry_after", None)
            if retry_after is not None:
                # Clamp between 5s and 6h (21600s)
                duration = max(5.0, min(float(retry_after), 21600.0))
                reason = "RATE_LIMITED"
            elif getattr(error, "is_account_limit", False):
                duration = ACCOUNT_LIMIT_COOLDOWN_S
                reason = "QUOTA_EXHAUSTED"
            else:
                duration = RATE_LIMIT_DEFAULT_S
                reason = "RATE_LIMITED"

        if duration is not None:
            entry = CooldownEntry(
                until=now + duration,
                reason=reason,
                fingerprint=fingerprint or "",
                error=error,
            )
            self._entries[name] = entry
            return entry

        return None

    def clear(self, name: Optional[str] = None) -> None:
        """Clears cooldown for a specific provider, or all providers if name is None."""
        if name is None:
            self._entries.clear()
        else:
            self._entries.pop(name, None)

    def snapshot(self) -> dict[str, dict]:
        """Snapshot of active cooldowns for health API."""
        now = self.clock()
        out: dict[str, dict] = {}
        for name, entry in list(self._entries.items()):
            remaining = entry.until - now
            if remaining <= 0:
                del self._entries[name]
            else:
                out[name] = {
                    "reason": entry.reason,
                    "remaining_s": int(remaining),
                    "fingerprint": entry.fingerprint,
                }
        return out
