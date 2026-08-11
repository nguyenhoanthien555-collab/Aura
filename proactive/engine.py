"""
The proactive engine.

Assembles the context, asks the decision engine, asks the policy, composes
the message, publishes it. The only object in the package that touches the
outside world.

**On triggering.** `tick()` is the entry point and it is pull-driven: it
is called when something asks Aura to consider speaking, and the natural
caller is the notification poll the Android client already makes. That is
a deliberate reuse of transport that exists rather than a second
networking stack, and it has a real consequence worth stating plainly
rather than hiding: *Aura can only consider speaking while a client is
polling.* A message that would have been sent at 03:00 to a phone that
is not polling is not sent late, it is not sent at all.

Closing that gap needs a process that runs when nobody is asking - a
background worker or a scheduled job on the host. Neither exists in this
deployment, and one is not invented here. `docs/DEPLOYMENT.md` records
what would have to be provisioned.

**On delivery.** The engine publishes a `CompanionNotificationEvent` and
stops caring, exactly as the companion engine does. `NotificationOutbox`
already subscribes, holds the message and hands it to the device on its
next poll. Nothing new was built to carry a notification, because
something that carries notifications already existed.

**On honesty.** `tick()` returning a decision with `send=True` means a
message was generated and published to the outbox. It does not mean a
phone displayed it, and nothing in this package may claim otherwise. The
outbox is a queue, not a receipt.
"""

from core.logger import logger
from core.temporal import TemporalClock
from events.types import CompanionNotificationEvent
from proactive.context import PendingTask, ProactiveContext
from proactive.decision import Category, ProactiveDecision, should_proactively_message
from proactive.messages import MessageComposer
from proactive.policy import ProactivePolicy, ProactiveSettings


class ProactiveEngine:
    """
    Considers speaking, and almost always decides not to.

    Nothing here runs on its own. `tick()` must be called, and calling it
    a thousand times in a row changes nothing that the user sees - every
    call runs the full decision and policy path.
    """

    def __init__(
        self,
        policy: ProactivePolicy | None = None,
        composer: MessageComposer | None = None,
        clock: TemporalClock | None = None,
        events=None,
        pending_tasks=None,
        memories=None,
    ):
        self.policy = policy or ProactivePolicy()
        self.composer = composer or MessageComposer()
        self.clock = clock or TemporalClock()
        self.events = events

        # Sources. Both optional, both callables, both returning real
        # data or nothing. `pending_tasks` returning nothing is the
        # normal case and means no task reminders - never a guess.
        self.pending_tasks = pending_tasks
        self.memories = memories

        self._last_user_message_at = None
        self._greeted: dict = {}          # date -> set of parts of day
        self._rotation = 0

    # ------------------------------------------------------------------
    # Facts from outside
    # ------------------------------------------------------------------

    def note_chat(self) -> None:
        """The user just said something. Called by the chat path."""

        self._last_user_message_at = self.clock.now()

    # ------------------------------------------------------------------

    def build_context(self) -> ProactiveContext:
        """
        Gather everything the decision is allowed to see.

        Separate from deciding so the decision itself stays pure and
        testable without a database.
        """

        temporal = self.clock.context()

        greeted = self._greeted.get(temporal.today, set())

        return ProactiveContext(
            temporal=temporal,
            last_user_message_at=self._last_user_message_at,
            last_proactive_at=self.policy.last_sent_at(),
            last_proactive_category="",
            pending_tasks=self._gather_tasks(),
            relevant_memories=self._gather_memories(),
            sent_today=self.policy.sent_today(temporal.now),
            recent_messages=self.policy.recent_messages(),
            greeted_this_part=temporal.part_of_day in greeted,
        )

    def _gather_tasks(self) -> tuple:
        """
        Real pending work, or nothing at all.

        A source that raises is treated as a source with nothing to say.
        A broken task lookup must produce silence, never a reminder
        about work that may not exist.
        """

        if self.pending_tasks is None:
            return ()

        try:
            tasks = self.pending_tasks() or []
        except Exception as error:
            logger.warning("Pending task source failed: %s", error)
            return ()

        return tuple(task for task in tasks if isinstance(task, PendingTask))

    def _gather_memories(self) -> tuple:

        if self.memories is None:
            return ()

        try:
            return tuple(str(line) for line in (self.memories() or []) if line)
        except Exception as error:
            logger.warning("Memory source failed: %s", error)
            return ()

    # ------------------------------------------------------------------
    # The tick
    # ------------------------------------------------------------------

    def tick(self) -> ProactiveDecision:
        """
        Consider speaking. Returns the decision either way.

        Publishes only when the decision engine, the policy and the
        composer all agree. Any one of them can veto.
        """

        context = self.build_context()

        decision = should_proactively_message(context)

        if not decision.send:
            return decision

        message = self.composer.compose(decision, context, rotation=self._rotation)

        if not message:
            # The composer had nothing honest to say - no referent for
            # an appreciation, no known task for a reminder.
            return ProactiveDecision.silent(
                f"{decision.category}: nothing specific enough to say"
            )

        allowed, reason = self.policy.allows(decision.category, message)

        if not allowed:
            return ProactiveDecision.silent(reason)

        self._deliver(decision, message)

        self.policy.note_sent(decision.category, message)
        self._rotation += 1

        if decision.category == Category.GREETING.value:
            self._greeted.setdefault(context.temporal.today, set()).add(
                context.part_of_day
            )

        # Old greeting bookkeeping is not worth keeping; two days is
        # enough to answer "have I greeted them this morning".
        self._prune_greetings(context.temporal.today)

        return ProactiveDecision(
            send=True,
            reason=f"{decision.reason}; {reason}",
            category=decision.category,
            priority=decision.priority,
            detail=message,
        )

    def _deliver(self, decision: ProactiveDecision, message: str) -> None:
        """
        Hand the message to the existing notification transport.

        Publishing is all that happens here. Whether a device ever
        collects it is not something this engine can know.
        """

        if self.events is None:
            return

        self.events.publish(
            CompanionNotificationEvent(
                message=message,
                reason=decision.reason,
                priority=decision.priority.value,
                confidence=1.0,
                source="proactive",
            )
        )

    def _prune_greetings(self, today) -> None:

        for day in list(self._greeted):
            if (today - day).days > 1:
                self._greeted.pop(day, None)


def build_proactive_engine(
    config: dict | None = None,
    events=None,
    pending_tasks=None,
    memories=None,
    clock: TemporalClock | None = None,
) -> ProactiveEngine:
    """
    Composition helper.

    Always returns an engine, even when proactive messaging is disabled -
    a disabled engine ticks, decides nothing and costs a dictionary
    lookup, which is simpler than every caller having to check for None.
    The `enabled` flag lives in the policy, where the other gates are.
    """

    temporal = clock or TemporalClock.from_config(config)

    return ProactiveEngine(
        policy=ProactivePolicy(
            settings=ProactiveSettings.from_config(config),
            clock=temporal.now,
        ),
        clock=temporal,
        events=events,
        pending_tasks=pending_tasks,
        memories=memories,
    )
