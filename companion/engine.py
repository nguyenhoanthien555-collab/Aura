"""
The engine that runs the pipeline.

One method matters:

    observe(observation) -> CompanionDecision

It walks the gates in order, stops at the first one that says no, and
returns a decision that explains itself either way. When the answer is
yes it publishes a `CompanionNotificationEvent` and starts the cooldown.

The engine holds no transport. Delivering the notification to a phone,
a desktop toast or a log line is a subscriber's job.
"""

import time
from threading import Lock

from core.logger import logger
from events.types import CompanionNotificationEvent
from companion.decision import CompanionDecision
from companion.detector import ChangeDetector
from companion.evaluator import (
    HeuristicEvaluator,
    LLMRelevanceEvaluator,
    Relevance,
)
from companion.policy import CompanionPolicy, PolicySettings
from vision.remote import ScreenObservation


# Heuristic score a change must reach before the LLM is consulted. Below
# this the answer is no and no call is made.
DEFAULT_PREFILTER = 0.4


class CompanionEngine:
    """
    ScreenObservation in, CompanionDecision out.

    `llm` is optional. Without one the heuristic evaluator decides alone,
    which is a strictly quieter companion - the heuristic proposes no
    message of its own, so it can only ever fire on the wording built
    from its own signals.
    """

    def __init__(
        self,
        policy: CompanionPolicy | None = None,
        detector: ChangeDetector | None = None,
        heuristic=None,
        evaluator=None,
        events=None,
        prefilter: float = DEFAULT_PREFILTER,
        clock=time.monotonic,
    ):
        self.policy = policy or CompanionPolicy()
        self.detector = detector or ChangeDetector()
        self.heuristic = heuristic or HeuristicEvaluator()
        self.evaluator = evaluator
        self.events = events
        self.prefilter = prefilter
        self.clock = clock

        self._lock = Lock()
        self._last_decision: CompanionDecision | None = None
        self.observations = 0
        self.notifications = 0

    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self.policy.settings.enabled

    @property
    def last_decision(self) -> CompanionDecision | None:
        with self._lock:
            return self._last_decision

    def note_chat(self) -> None:
        """The user just spoke to Aura directly."""
        self.policy.note_chat()

    # ------------------------------------------------------------------

    def observe(self, observation: ScreenObservation) -> CompanionDecision:
        """
        Run one observation through the pipeline.

        Never raises: an observation is an enhancement, and a companion
        that crashes the screen endpoint because it could not decide
        whether to speak is worse than one that stays quiet.
        """

        with self._lock:
            self.observations += 1

        try:
            decision = self._decide(observation)
        except Exception as error:
            logger.debug("Companion decision failed: %s", error)
            decision = CompanionDecision.silent("decision failed")

        with self._lock:
            self._last_decision = decision

        if decision.should_notify:
            self._notify(decision, observation)

        return decision

    # ------------------------------------------------------------------

    def _decide(self, observation: ScreenObservation) -> CompanionDecision:

        # Gate 0: is this feature even on? Checked before the detector so
        # a disabled companion builds no state to be surprised by later.
        if not self.policy.settings.enabled:
            return CompanionDecision.silent("companion notifications are off")

        if observation.is_empty():
            return CompanionDecision.silent("nothing on screen")

        # Gate 1: did anything change?
        change = self.detector.check(observation)

        if not change.changed:
            return CompanionDecision.silent(change.reason)

        # Gate 2: cheap relevance, and the sensitive-screen veto.
        prior = self.heuristic.evaluate(observation, change)

        if prior.sensitive:
            return CompanionDecision.silent("sensitive screen")

        if prior.score < self.prefilter:
            return CompanionDecision.silent(prior.reason, confidence=prior.score)

        # Gate 3: the expensive opinion, when there is one to ask.
        relevance = self._evaluate(observation, change, prior)

        if relevance.sensitive:
            return CompanionDecision.silent("sensitive screen")

        # Gate 4: manners.
        return self.policy.allows(relevance.score, relevance.message)

    def _evaluate(self, observation, change, prior: Relevance) -> Relevance:

        if self.evaluator is None:
            return prior

        relevance = self.evaluator.evaluate(observation, change)

        # The model's confidence decides, but it never gets to be more
        # certain than the cheap signals were interested.
        return Relevance(
            score=min(relevance.score, max(prior.score, relevance.score)),
            reason=relevance.reason or prior.reason,
            message=relevance.message,
            sensitive=relevance.sensitive or prior.sensitive,
        )

    def _notify(self, decision: CompanionDecision, observation) -> None:

        self.policy.note_notified(decision.message)

        with self._lock:
            self.notifications += 1

        if self.events is None:
            return

        try:
            self.events.publish(
                CompanionNotificationEvent(
                    message=decision.message,
                    reason=decision.reason,
                    priority=decision.priority.value,
                    confidence=decision.confidence,
                    source=observation.application
                    or observation.package
                    or "phone",
                    device_id=observation.device_id,
                )
            )
        except Exception as error:
            logger.debug("Companion event publish failed: %s", error)


def build_companion_engine(
    config: dict | None,
    events=None,
    llm=None,
    ledger=None,
    last_user_message=None,
):
    """
    Build the engine from the `server.companion` section of config.

    Returns None when the section is absent, which keeps a caller from
    having to know whether the feature exists.

    `ledger` and `last_user_message` are what make section 20's ceiling and
    section 21's "do not talk over the owner" survive a restart, and both
    stay None here. A builder that reached for a real file and a real
    database on its own would make every test that calls it either write to
    `data/` or need a fixture to stop it; the server supplies them in
    `ServerRuntime._build_companion`, which is the only caller that has
    them to give.
    """

    server = (config or {}).get("server") or {}

    settings = PolicySettings.from_config(server.get("companion"))

    evaluator = LLMRelevanceEvaluator(llm) if llm is not None else None

    return CompanionEngine(
        policy=CompanionPolicy(
            settings=settings,
            ledger=ledger,
            last_user_message=last_user_message,
        ),
        evaluator=evaluator,
        events=events,
    )
