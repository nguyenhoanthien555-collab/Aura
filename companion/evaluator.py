"""
Relevance evaluation.

The second gate: the screen changed, but is the change worth a word?

Two evaluators, one interface:

    HeuristicEvaluator  free, instant, no model. Scores a change on
                        signals that do not need understanding.

    LLMRelevanceEvaluator  asks Aura's own brain, through the LLM port
                        that already exists. Costs a call, so it only
                        runs on changes the heuristic already liked.

The default engine uses the heuristic as a pre-filter and the LLM as the
decider, which keeps the common case (the user scrolled) free.
"""

import json
import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from core.logger import logger
from companion.detector import ScreenChange
from vision.remote import ScreenObservation


# Signals that a screen is worth a companion's attention. Deliberately
# small and boring: this is a pre-filter, not an understanding.
INTERESTING = (
    "error", "exception", "failed", "failure", "traceback",
    "warning", "denied", "timeout", "crash", "cannot", "unable",
    "deadline", "due today", "overdue", "expired",
    "merge conflict", "build failed", "test failed",
)

# Screens a companion should stay out of, whatever they contain.
SENSITIVE = (
    "password", "passcode", "credit card", "card number", "cvv",
    "bank", "banking", "otp", "one-time code", "verification code",
    "seed phrase", "private key", "social security",
)

SENSITIVE_PACKAGES = (
    "com.android.settings",
    "com.google.android.gms",
    "keepass", "bitwarden", "1password", "lastpass", "authenticator",
)


@dataclass(frozen=True)
class Relevance:
    """How interesting a change is, and what to say about it."""

    score: float = 0.0
    reason: str = ""
    message: str = ""
    sensitive: bool = False


@runtime_checkable
class RelevanceEvaluator(Protocol):

    def evaluate(
        self,
        observation: ScreenObservation,
        change: ScreenChange,
    ) -> Relevance:
        ...


def looks_sensitive(observation: ScreenObservation) -> bool:
    """
    True for screens Aura must not comment on.

    Checked before anything else, and it is not a score - a login screen
    is not "low relevance", it is off limits. Erring toward silence here
    costs a missed remark; erring the other way puts a password field in
    a prompt.
    """

    package = (observation.package or "").lower()

    if any(name in package for name in SENSITIVE_PACKAGES):
        return True

    haystack = " ".join((
        observation.application,
        observation.screen_text,
        observation.accessibility_context,
    )).lower()

    return any(term in haystack for term in SENSITIVE)


class HeuristicEvaluator:
    """
    Scores a change without a model.

    The score is a rough prior, not a judgement. It exists to keep the
    LLM out of the 95% of screen changes that are someone scrolling.
    """

    def __init__(self, app_switch_score: float = 0.45):
        self.app_switch_score = app_switch_score

    def evaluate(
        self,
        observation: ScreenObservation,
        change: ScreenChange,
    ) -> Relevance:

        if looks_sensitive(observation):
            return Relevance(
                score=0.0,
                reason="sensitive screen",
                sensitive=True,
            )

        haystack = " ".join((
            observation.application,
            observation.screen_text,
            observation.accessibility_context,
        )).lower()

        hits = [term for term in INTERESTING if term in haystack]

        if hits:
            return Relevance(
                score=min(0.6 + 0.1 * len(hits), 0.95),
                reason=f"screen mentions {', '.join(hits[:3])}",
            )

        if change.app_switched:
            return Relevance(
                score=self.app_switch_score,
                reason=f"switched to {change.current_app}",
            )

        # A big change with nothing notable in it. Real, but not a reason
        # to speak.
        return Relevance(
            score=max(0.0, 0.3 * (1.0 - change.similarity)),
            reason="ordinary screen change",
        )


PROMPT = """\
You are deciding whether a companion should interrupt.

The user's phone screen changed. Decide whether saying something now
would genuinely help them, or whether it would be noise.

Default to staying silent. Interrupt only for something the user would
thank you for: an error they may not have noticed, a deadline, something
that blocks what they are doing. Never interrupt to describe what they
can already see, to be friendly, or to check in.

App: {app}
What changed: {change}
On screen:
{text}

Reply with JSON and nothing else:
{{"relevant": true or false, "confidence": 0.0 to 1.0, "reason": "short", "message": "what to say, one sentence, empty if not relevant"}}
"""


class LLMRelevanceEvaluator:
    """
    Asks the model whether this is worth interrupting for.

    Takes the same `LLM` port the brain uses (`brain.ports.LLM`), so it
    runs on whatever provider is configured, with no separate client, key
    or model of its own.

    A failure - provider down, unparseable reply - scores zero. When in
    doubt, Aura stays quiet.
    """

    def __init__(self, llm, max_text: int = 1200):
        self.llm = llm
        self.max_text = max_text

    def evaluate(
        self,
        observation: ScreenObservation,
        change: ScreenChange,
    ) -> Relevance:

        if looks_sensitive(observation):
            return Relevance(
                score=0.0,
                reason="sensitive screen",
                sensitive=True,
            )

        prompt = PROMPT.format(
            app=observation.application or observation.package or "unknown",
            change=change.reason,
            text=" ".join(
                " ".join((
                    observation.screen_text,
                    observation.accessibility_context,
                )).split()
            )[: self.max_text],
        )

        try:
            raw = self.llm.generate(prompt)
        except Exception as error:
            logger.debug("Companion relevance call failed: %s", error)
            return Relevance(score=0.0, reason="evaluator unavailable")

        return self._parse(raw)

    @staticmethod
    def _parse(raw: str) -> Relevance:

        match = re.search(r"\{.*\}", raw or "", re.DOTALL)

        if not match:
            return Relevance(score=0.0, reason="unparseable evaluation")

        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return Relevance(score=0.0, reason="unparseable evaluation")

        if not isinstance(data, dict):
            return Relevance(score=0.0, reason="unparseable evaluation")

        relevant = bool(data.get("relevant"))

        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0

        confidence = max(0.0, min(1.0, confidence))

        message = str(data.get("message") or "").strip()

        # "Relevant, but I have nothing to say" is not relevant.
        if not relevant or not message:
            return Relevance(
                score=0.0,
                reason=str(data.get("reason") or "not worth interrupting"),
            )

        return Relevance(
            score=confidence,
            reason=str(data.get("reason") or "model judged this relevant"),
            message=message,
        )
