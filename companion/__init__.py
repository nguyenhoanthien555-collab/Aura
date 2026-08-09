"""
Companion decision engine.

Aura may say something the user did not ask for. This package decides
whether she should, and the default answer is no.

    ScreenObservation
        -> ChangeDetector      is this a different screen?
        -> RelevanceEvaluator  is the difference worth a word?
        -> CompanionPolicy     is now a reasonable moment?
        -> CompanionDecision   should_notify + why
        -> CompanionNotificationEvent

Not to be confused with `memory/companion.py`, which stores what Aura
knows about the user. That is memory; this is manners.

Nothing here imports brain, and brain imports nothing here. The engine
publishes an event and the transport - a WebSocket, a push notification,
a desktop toast - decides what to do with it.
"""

from companion.decision import CompanionDecision, Priority
from companion.detector import ChangeDetector, ScreenChange
from companion.evaluator import RelevanceEvaluator, Relevance
from companion.policy import CompanionPolicy, PolicySettings
from companion.engine import CompanionEngine

__all__ = [
    "ChangeDetector",
    "CompanionDecision",
    "CompanionEngine",
    "CompanionPolicy",
    "PolicySettings",
    "Priority",
    "Relevance",
    "RelevanceEvaluator",
    "ScreenChange",
]
