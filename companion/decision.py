"""
The decision itself.

A frozen record of one judgement: whether to speak, and everything
needed to explain the answer afterwards. Every field is filled in on a
"no" as well as on a "yes" - a decision that only explains itself when it
fires is impossible to tune.
"""

from dataclasses import dataclass
from enum import Enum


class Priority(str, Enum):
    """
    How loudly a notification should arrive.

    Lowercase values so they can be written to a log, sent as JSON and
    mapped onto an Android channel importance without conversion.
    """

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


@dataclass(frozen=True)
class CompanionDecision:
    """
    Whether Aura should say something unprompted, and why.

    should_notify   the answer
    reason          why, in words, for logs and for the user's own
                    "why did you say that" - always populated
    priority        how loudly, when notifying
    message         what to say, empty when not notifying
    confidence      0.0 to 1.0, how sure the evaluator was
    cooldown        seconds before the next notification may fire
    """

    should_notify: bool = False
    reason: str = ""
    priority: Priority = Priority.NORMAL
    message: str = ""
    confidence: float = 0.0
    cooldown: float = 0.0

    @classmethod
    def silent(cls, reason: str, confidence: float = 0.0) -> "CompanionDecision":
        """A decision not to speak. The common case."""

        return cls(
            should_notify=False,
            reason=reason,
            confidence=confidence,
        )

    def as_dict(self) -> dict:
        """
        JSON-safe form for an API response.

        `message` is included on a silence too, and is empty there by
        construction - a client that renders `message` unconditionally
        shows nothing rather than a stale line.
        """

        return {
            "should_notify": self.should_notify,
            "reason": self.reason,
            "priority": self.priority.value,
            "message": self.message,
            "confidence": round(self.confidence, 3),
            "cooldown": round(self.cooldown, 1),
        }
