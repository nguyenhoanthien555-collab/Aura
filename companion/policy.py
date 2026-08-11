"""
Notification policy.

The last gate, and the only one that can veto a genuinely relevant
thought. Relevance asks "is this worth saying"; policy asks "is now a
reasonable moment to say it".

They are separate because they fail differently. A wrong relevance call
is a bad remark. A missing policy is a companion that talks over you all
evening.

Every rule here defaults to silence:

    disabled            off until the user turns it on
    quiet hours         nothing at all
    active chat         she is already in the conversation
    cooldown            one thing at a time
    hourly ceiling      survives a stuck relevance score
    duplicate           she already said this
"""

import time
from collections import deque
from dataclasses import dataclass, field
from threading import Lock

from companion.decision import CompanionDecision, Priority
from core.temporal import in_quiet_hours


DEFAULT_COOLDOWN = 300.0
DEFAULT_MAX_PER_HOUR = 6
DEFAULT_THRESHOLD = 0.7
DEFAULT_SUPPRESS_AFTER_CHAT = 120.0

# How long a message counts as "already said".
DUPLICATE_WINDOW = 1800.0


@dataclass
class PolicySettings:
    """
    Tuning for the policy, read from `server.companion` in config.yaml.

    `quiet_hours` is a list of [start_hour, end_hour] pairs in local
    time. A pair may wrap midnight: [23, 7] means 23:00 to 07:00.
    """

    enabled: bool = False
    relevance_threshold: float = DEFAULT_THRESHOLD
    cooldown_seconds: float = DEFAULT_COOLDOWN
    max_per_hour: int = DEFAULT_MAX_PER_HOUR
    quiet_hours: list = field(default_factory=list)
    suppress_after_chat_seconds: float = DEFAULT_SUPPRESS_AFTER_CHAT

    @classmethod
    def from_config(cls, config: dict | None) -> "PolicySettings":
        """
        Build from the `server.companion` section, tolerating a missing
        or partial one.
        """

        settings = config or {}

        return cls(
            enabled=bool(settings.get("enabled", False)),
            relevance_threshold=float(
                settings.get("relevance_threshold", DEFAULT_THRESHOLD)
            ),
            cooldown_seconds=float(
                settings.get("cooldown_seconds", DEFAULT_COOLDOWN)
            ),
            max_per_hour=int(settings.get("max_per_hour", DEFAULT_MAX_PER_HOUR)),
            quiet_hours=list(settings.get("quiet_hours") or []),
            suppress_after_chat_seconds=float(
                settings.get(
                    "suppress_after_chat_seconds", DEFAULT_SUPPRESS_AFTER_CHAT
                )
            ),
        )


class CompanionPolicy:
    """
    Decides whether a relevant thought may be spoken now.

    Thread-safe: screen observations arrive on request threads.

    `clock` is monotonic time for intervals; `local_hour` is wall-clock
    hour for quiet hours. Both are injected so the rules can be tested
    without waiting or without it being 3am.
    """

    def __init__(
        self,
        settings: PolicySettings | None = None,
        clock=time.monotonic,
        local_hour=None,
    ):
        self.settings = settings or PolicySettings()
        self.clock = clock
        self.local_hour = local_hour or (lambda: time.localtime().tm_hour)

        self._lock = Lock()
        self._last_notified: float | None = None
        self._last_chat: float | None = None
        self._recent: deque = deque(maxlen=32)      # (when, message)

    # ------------------------------------------------------------------
    # Facts the policy needs from outside
    # ------------------------------------------------------------------

    def note_chat(self) -> None:
        """The user just sent a message. Called by the chat path."""

        with self._lock:
            self._last_chat = self.clock()

    def note_notified(self, message: str) -> None:
        """A notification went out. Starts the cooldown."""

        now = self.clock()

        with self._lock:
            self._last_notified = now
            self._recent.append((now, self._normalise(message)))

    def reset(self) -> None:
        with self._lock:
            self._last_notified = None
            self._last_chat = None
            self._recent.clear()

    # ------------------------------------------------------------------
    # The gate
    # ------------------------------------------------------------------

    def allows(self, score: float, message: str) -> CompanionDecision:
        """
        A silent CompanionDecision explaining the refusal, or a
        `should_notify=True` one carrying `message`.

        Order matters: the cheapest and most absolute rules run first, so
        the reason a user reads is the most fundamental one that applied.
        """

        settings = self.settings

        if not settings.enabled:
            return CompanionDecision.silent("companion notifications are off")

        if not message.strip():
            return CompanionDecision.silent("nothing to say", confidence=score)

        if score < settings.relevance_threshold:
            return CompanionDecision.silent(
                f"below relevance threshold "
                f"({score:.2f} < {settings.relevance_threshold:.2f})",
                confidence=score,
            )

        if self._in_quiet_hours():
            return CompanionDecision.silent("quiet hours", confidence=score)

        now = self.clock()

        with self._lock:
            last_chat = self._last_chat
            last_notified = self._last_notified
            recent = list(self._recent)

        if (
            last_chat is not None
            and settings.suppress_after_chat_seconds > 0
            and now - last_chat < settings.suppress_after_chat_seconds
        ):
            return CompanionDecision.silent(
                "user is mid-conversation", confidence=score
            )

        if last_notified is not None:
            elapsed = now - last_notified

            if elapsed < settings.cooldown_seconds:
                return CompanionDecision.silent(
                    f"cooling down "
                    f"({settings.cooldown_seconds - elapsed:.0f}s left)",
                    confidence=score,
                )

        if self._is_duplicate(message, recent, now):
            return CompanionDecision.silent(
                "already said this recently", confidence=score
            )

        if self._hourly_ceiling_reached(recent, now):
            return CompanionDecision.silent(
                f"hourly limit reached ({settings.max_per_hour})",
                confidence=score,
            )

        return CompanionDecision(
            should_notify=True,
            reason="relevant, and a reasonable moment",
            priority=self._priority(score),
            message=message.strip(),
            confidence=score,
            cooldown=settings.cooldown_seconds,
        )

    # ------------------------------------------------------------------
    # Rules
    # ------------------------------------------------------------------

    def _in_quiet_hours(self) -> bool:
        """
        Delegated to `core.temporal`, which the proactive scheduler reads
        too. "Quiet" has to mean the same thing to everything in Aura
        that can speak unprompted, and two copies of a midnight-wrapping
        range check would eventually disagree.
        """

        return in_quiet_hours(self.local_hour(), self.settings.quiet_hours)

    @staticmethod
    def _normalise(message: str) -> str:
        return " ".join(message.lower().split())

    def _is_duplicate(self, message: str, recent, now: float) -> bool:

        candidate = self._normalise(message)

        return any(
            said == candidate and now - when < DUPLICATE_WINDOW
            for when, said in recent
        )

    def _hourly_ceiling_reached(self, recent, now: float) -> bool:

        if self.settings.max_per_hour <= 0:
            return True

        in_last_hour = sum(1 for when, _ in recent if now - when < 3600.0)

        return in_last_hour >= self.settings.max_per_hour

    @staticmethod
    def _priority(score: float) -> Priority:

        if score >= 0.9:
            return Priority.HIGH

        if score >= 0.75:
            return Priority.NORMAL

        return Priority.LOW
