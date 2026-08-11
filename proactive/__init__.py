"""
Proactive messaging.

Aura saying something without being asked first. The hard part is not
generating a message; it is deciding not to, correctly, almost every
time.

The pipeline, one module per stage:

    context     what is true right now - the time, when the user was
                last here, what is actually pending
    decision    should_proactively_message(context) -> ProactiveDecision
    policy      the anti-spam gate: cooldowns, quiet hours, daily cap,
                duplicate and similarity suppression
    messages    what to say, per category
    engine      the assembly, and the only thing that publishes

Three rules the whole package is built around.

**Nothing fires on a timer alone.** A trigger is an invitation to
*consider* speaking, and every consideration runs the full decision and
policy path. `tick()` can be called every second without changing what
the user experiences.

**Every decision explains itself.** Silence is the common case, so a
decision that only carries a reason when it fires cannot be tuned or
debugged. `reason` is populated on both answers.

**Nothing is invented.** A pending-task reminder requires a real, known
piece of pending work from a real source. With no source there are no
task reminders - not a plausible guess about what the user might be
working on.
"""

from proactive.context import PendingTask, ProactiveContext
from proactive.decision import Category, ProactiveDecision, should_proactively_message
from proactive.engine import ProactiveEngine, build_proactive_engine
from proactive.messages import MessageComposer
from proactive.policy import ProactivePolicy, ProactiveSettings, similarity
from proactive.tasks import EpisodicTaskSource

__all__ = [
    "Category",
    "EpisodicTaskSource",
    "MessageComposer",
    "PendingTask",
    "ProactiveContext",
    "ProactiveDecision",
    "ProactiveEngine",
    "ProactivePolicy",
    "ProactiveSettings",
    "build_proactive_engine",
    "should_proactively_message",
    "similarity",
]
