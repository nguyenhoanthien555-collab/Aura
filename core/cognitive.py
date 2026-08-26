"""
What Aura is doing right now.

One object, one owner, one answer. Before this file the answer was spread
across four places that each knew part of it and none of which could be
asked:

    context: dict           the Android screen, travelling over HTTP with
                            untyped keys
    _Turn                   one request's worth of state, gone when the
                            response is written
    ProactiveContext        a frozen read model for one decision
    runAgentSteps locals    on the stack of one suspend function, invisible
                            to everything outside it

Four partial answers is not four times the knowledge. It is no authority at
all, and it left the one question that matters unanswerable:

    have I already done this?

That question is why this file exists. The reported failure it kills is an
agent that opens YouTube, verifies it opened, and then opens YouTube again -
because nothing between one model call and the next remembered the first one
working. A model re-deriving the situation from a fresh screenshot every
tick will sometimes re-derive it wrongly. A recorded fact will not.


WHAT IS HERE AND WHAT IS DELIBERATELY NOT
-----------------------------------------
Here: the identity of the session and the owner, the conversation in
progress, the intent behind it, the goal in the owner's own words, the plan
and which step of it is current, what is on screen, which tools are in
flight, and every action attempted with its outcome and its attempt count.

Not here, on purpose:

    the time          `core.temporal` owns "now". A second copy would
                      eventually disagree with the first, and then two
                      parts of Aura would believe different things about
                      when something happened. This class holds a clock
                      and asks it.

    the avatar's state  `events.AuraState` is what the face is doing.
                        Presentational, three axes deep, and none of the
                        three is cognition. They must not be conflated
                        just because both are called "state".

    the task graph     phase 6. This tracks which node is current; it does
                       not own the graph's shape. The distinction keeps
                       the graph free to be persisted or rebuilt without
                       this object caring.

    persistence        nothing here writes to disk. `memory/` is the
                       durable layer and knows how; a cache in front of it
                       that could drift is worse than no cache.


THE SHAPE: A MUTABLE OWNER, FROZEN VIEWS
----------------------------------------
`CognitiveState` is mutable and authoritative. `snapshot()` hands out a
frozen `CognitiveSnapshot`. That is the same bargain `ProactiveContext`
already makes and for the same reason: given one exact snapshot, a decision
is always the same decision, which is what makes it testable without
waiting for real time to pass.

`revision` exists so "has anything happened since the last tick?" is an
integer comparison rather than a diff. It increments only on a real change -
observing the same screen twice does not move it, because a verification
loop has to be able to tell "still loading" from "arrived".


ACTION IDENTITY, AND THE ONE WAY TO REPEAT WORK
-----------------------------------------------
An action is identified by what it did and to what: `(kind, target)`.
`open_app com.google.android.youtube` is one fact whether it is attempted
once or four times, which is precisely what makes "have I already done
this?" answerable without a model call.

A succeeded action cannot quietly become pending again. `begin_action` on
finished work returns the finished record and changes nothing. There is
exactly one way to make it run again - `enter_recovery` naming it - because
section 12 requires repetition to be deliberate and bounded rather than the
default behaviour of a loop that forgot.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from threading import RLock
from uuid import uuid4

from core.temporal import TemporalClock, TemporalContext


class ActionState(str, Enum):
    """
    Where one attempted action got to.

    Plain lowercase strings so a state can be logged, rendered into a
    prompt or compared against a wire value without a conversion table -
    the same reason `AuraState` and `ToolRisk` are spelled this way.

    Three states, not four. There is no CANCELLED: an action nobody
    finished is PENDING, and inventing a fourth state to mean "we stopped
    caring" would put two different things in the same bucket.
    """

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class ActionRecord:
    """
    One thing Aura tried to do, and how it went.

    `kind` and `target` together are the identity - see the module
    docstring. `target` is whatever makes the action specific: a package
    name for `open_app`, the typed string for `input_text`, a node id for
    `click`. It is a plain string rather than a union because the only
    thing this file does with it is compare it.

    `attempts` counts beginnings, not failures. A first attempt that
    failed and a second that has not finished are two attempts and one
    failure, and a retry bound has to be built on the former.

    `detail` is why it failed, in words a person can read. It is the
    payload that reaches the model as the last action error, so it must
    never carry a stack trace, a key, or anything else section 30
    forbids leaving in a log.
    """

    kind: str
    target: str = ""
    state: ActionState = ActionState.PENDING
    attempts: int = 0
    detail: str = ""
    at: datetime | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.kind, self.target)

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "target": self.target,
            "state": self.state.value,
            "attempts": self.attempts,
            "detail": self.detail,
            "at": self.at.isoformat(timespec="seconds") if self.at else "",
        }


@dataclass(frozen=True)
class Focus:
    """
    What is in front of the owner.

    Two fields because Android gives two and the desktop gives the same
    two under different names: the application, and which part of it.
    `application` is a package name on a phone and a window title or
    process name on a PC; `screen` is whatever identifies the view - an
    activity, a route, a content signature.

    Both default to empty rather than to a guess. "I do not know what is
    on screen" is a real and frequent answer, and a placeholder would
    make a verification step compare against fiction.
    """

    application: str = ""
    screen: str = ""

    def as_dict(self) -> dict:
        return {"application": self.application, "screen": self.screen}


@dataclass(frozen=True)
class CognitiveSnapshot:
    """
    Everything above, frozen at one instant.

    What consumers are given. They get this rather than the live object so
    that a decision cannot change under them halfway through being made,
    and so that adding a field to `CognitiveState` forces a decision about
    whether consumers should see it - which is the check that stops the
    duplication this module exists to end from starting again.
    """

    temporal: TemporalContext
    session_id: str = ""
    conversation_id: str = ""
    user: str = ""
    intent: str = ""
    goal: str = ""
    plan: tuple[str, ...] = ()
    task_node: str = ""
    focus: Focus = field(default_factory=Focus)
    active_tools: tuple[str, ...] = ()
    actions: tuple[ActionRecord, ...] = ()
    recovering_from: ActionRecord | None = None
    revision: int = 0

    @property
    def pending(self) -> tuple[ActionRecord, ...]:
        return self._in(ActionState.PENDING)

    @property
    def succeeded(self) -> tuple[ActionRecord, ...]:
        return self._in(ActionState.SUCCEEDED)

    @property
    def failed(self) -> tuple[ActionRecord, ...]:
        return self._in(ActionState.FAILED)

    def _in(self, state: ActionState) -> tuple[ActionRecord, ...]:
        return tuple(a for a in self.actions if a.state is state)

    def as_dict(self) -> dict:
        """
        Primitive all the way down, for logs and diagnostics.

        Sections 28 and 30: this ends up in places a person reads, so
        nothing in it may be a live handle and nothing in it may be a
        secret. Every value below is a string, a number, a bool or a
        container of those.
        """

        return {
            "temporal": self.temporal.as_dict(),
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "user": self.user,
            "intent": self.intent,
            "goal": self.goal,
            "plan": list(self.plan),
            "task_node": self.task_node,
            "focus": self.focus.as_dict(),
            "active_tools": list(self.active_tools),
            "actions": [a.as_dict() for a in self.actions],
            "recovering_from": (
                self.recovering_from.as_dict() if self.recovering_from else None
            ),
            "revision": self.revision,
        }


class CognitiveState:
    """
    The live one. Mutable, single-owner, not thread-safe.

    Not thread-safe on purpose, and the same call as `ObservationThrottle`
    makes: everything that touches this runs on one request's thread or on
    the agent loop, and a lock would buy nothing but the impression that
    concurrent mutation is expected. If that changes, the lock goes here,
    around these methods, rather than being scattered through callers.

    Every mutator is a no-op when it would not change anything, which is
    what makes `revision` mean "something actually happened".
    """

    def __init__(
        self,
        clock: TemporalClock | None = None,
        session_id: str = "",
        user: str = "",
    ):
        # Held, not read. The one place "now" comes from.
        self._clock = clock or TemporalClock()

        self._session_id = session_id or uuid4().hex
        self._user = user
        self._conversation_id = ""

        self._intent = ""
        self._goal = ""
        self._plan: tuple[str, ...] = ()
        self._task_node = ""

        self._focus = Focus()
        self._tools: list[str] = []

        # Insertion-ordered by (kind, target), so `succeeded` reads back in
        # the order things happened - which is the order the prompt's
        # completed-actions section has to show them in.
        self._actions: dict[tuple[str, str], ActionRecord] = {}

        # The one key allowed to reopen finished work.
        self._recovering: tuple[str, str] | None = None

        self._revision = 0

    # ------------------------------------------------------------------
    # Time: borrowed, never kept
    # ------------------------------------------------------------------

    @property
    def now(self) -> datetime:
        return self._clock.now()

    @property
    def temporal(self) -> TemporalContext:
        return self._clock.context()

    # ------------------------------------------------------------------
    # Identity and scope
    # ------------------------------------------------------------------

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def user(self) -> str:
        return self._user

    @property
    def conversation_id(self) -> str:
        return self._conversation_id

    def set_user(self, name: str) -> None:
        self._assign("_user", str(name or ""))

    def new_session(self) -> str:
        """
        A fresh session, keeping the owner.

        The owner outlives sessions and conversations both. Section 38: no
        part of Aura may make the owner introduce themselves again because
        something restarted underneath them.
        """

        self._session_id = uuid4().hex
        self.clear_task()
        self._bump()

        return self._session_id

    def begin_conversation(self, conversation_id: str) -> None:
        """
        Switch to a conversation, or re-enter the current one.

        Re-entering is the common case and must be free: the phone
        re-sends its conversation id on every agent tick, and treating
        that as a switch would erase the task mid-execution - which is
        the loop this module exists to stop, arriving by a different
        door.
        """

        conversation_id = str(conversation_id or "")

        if conversation_id == self._conversation_id:
            return

        self._conversation_id = conversation_id
        self.clear_task()
        self._bump()

    # ------------------------------------------------------------------
    # Intent, goal, plan, node
    # ------------------------------------------------------------------

    @property
    def intent(self) -> str:
        return self._intent

    @property
    def goal(self) -> str:
        return self._goal

    @property
    def plan(self) -> tuple[str, ...]:
        return self._plan

    @property
    def task_node(self) -> str:
        return self._task_node

    def set_intent(self, intent: str) -> None:
        self._assign("_intent", str(intent or ""))

    def set_goal(self, goal: str) -> None:
        """
        The request, in the owner's own words.

        Verbatim, and never a paraphrase. Section 23's rule - the search
        query is `Minecraft`, not `search for Minecraft` - is a decision
        for whoever parses this, made once. Storing a cleaned-up version
        would mean the cleaning happened somewhere unrecorded and cannot
        be reviewed.
        """

        self._assign("_goal", str(goal or ""))

    def set_plan(self, steps) -> None:
        self._assign("_plan", tuple(str(step) for step in (steps or ())))

    def enter_node(self, node: str) -> None:
        self._assign("_task_node", str(node or ""))

    def clear_task(self) -> None:
        """
        Forget the task, keep the conversation.

        Everything cleared here is a statement about one piece of work:
        the intent behind it, the plan for it, which step is current, what
        has been attempted, and what is being recovered. The conversation,
        the session and the owner are not statements about the work and
        stay exactly as they were.
        """

        changed = any((
            self._intent, self._goal, self._plan, self._task_node,
            self._actions, self._recovering,
        ))

        self._intent = ""
        self._goal = ""
        self._plan = ()
        self._task_node = ""
        self._actions = {}
        self._recovering = None

        if changed:
            self._bump()

    # ------------------------------------------------------------------
    # What is on screen
    # ------------------------------------------------------------------

    @property
    def focus(self) -> Focus:
        return self._focus

    def observe(
        self, application: str | None = None, screen: str | None = None
    ) -> bool:
        """
        Record what is in front of the owner. True when it changed.

        Each field is updated only when given, so a caller that knows the
        package but not the screen does not blank the screen by omission -
        `None` means "no news", which is different from `""` meaning
        "nothing there".

        The return value is the point. A tick that changed nothing has to
        be distinguishable from one that did, or a verification step
        cannot tell a screen still loading from a screen that arrived.
        """

        updated = Focus(
            application=(
                self._focus.application if application is None
                else str(application)
            ),
            screen=self._focus.screen if screen is None else str(screen),
        )

        if updated == self._focus:
            return False

        self._focus = updated
        self._bump()

        return True

    # ------------------------------------------------------------------
    # Tools in flight
    # ------------------------------------------------------------------

    @property
    def active_tools(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def tool_started(self, name: str) -> None:
        if name in self._tools:
            return

        self._tools.append(name)
        self._bump()

    def tool_finished(self, name: str) -> None:
        """
        Forgiving on purpose.

        A crash between start and finish must not make the next finish
        raise: one lost event would then poison every later reading of
        what is running. Finishing something that never started is
        simply nothing to do.
        """

        if name not in self._tools:
            return

        self._tools.remove(name)
        self._bump()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    @property
    def actions(self) -> tuple[ActionRecord, ...]:
        return tuple(self._actions.values())

    @property
    def pending(self) -> tuple[ActionRecord, ...]:
        return self._in(ActionState.PENDING)

    @property
    def succeeded(self) -> tuple[ActionRecord, ...]:
        return self._in(ActionState.SUCCEEDED)

    @property
    def failed(self) -> tuple[ActionRecord, ...]:
        return self._in(ActionState.FAILED)

    def action_for(self, kind: str, target: str = "") -> ActionRecord | None:
        return self._actions.get((kind, target))

    def has_succeeded(self, kind: str, target: str = "") -> bool:
        record = self._actions.get((kind, target))

        return record is not None and record.state is ActionState.SUCCEEDED

    def has_succeeded_kind(self, kind: str) -> bool:
        """
        Whether any action of this kind succeeded, whatever its target.

        The sibling of `has_succeeded`, for the callers that cannot know a
        target in advance. The planner is the first: it decides on the
        server that a search field must be filled, long before the screen
        has been seen, so the node id that `input_text` will eventually
        carry is unknowable to it. Asking "did anything get typed" is the
        honest question; inventing a node id to ask the exact one would be
        guessing at device state.

        `open_app` is exactly the case that still wants `has_succeeded` -
        its target is a package name the caller does know, and "some app
        opened" is not evidence that the requested one did.
        """

        return any(record.kind == kind for record in self.succeeded)

    def attempts_for(self, kind: str, target: str = "") -> int:
        record = self._actions.get((kind, target))

        return record.attempts if record else 0

    def begin_action(self, kind: str, target: str = "") -> ActionRecord:
        """
        Start an action, or decline to start a finished one.

        The invariant that kills the open_app loop. A caller asking to
        redo finished work is handed the finished record and nothing
        moves - the state itself is what says "already done", so no
        caller has to remember to check.

        Recovery is the single exception, and it has to name this exact
        action to get it: `enter_recovery(kind, target)` first. Section
        10 allows a completed node to run again only when recovery
        explicitly requires it.
        """

        key = (kind, target)
        existing = self._actions.get(key)

        if (
            existing is not None
            and existing.state is ActionState.SUCCEEDED
            and self._recovering != key
        ):
            return existing

        record = ActionRecord(
            kind=kind,
            target=target,
            state=ActionState.PENDING,
            attempts=(existing.attempts if existing else 0) + 1,
            detail="",
            at=self.now,
        )

        self._actions[key] = record
        self._bump()

        return record

    def succeed_action(self, kind: str, target: str = "") -> ActionRecord:
        """
        Record that it worked.

        Clears `detail`: a record that succeeded has no failure reason,
        and leaving the old one there would send a stale error to the
        model on the next tick.
        """

        return self._settle(kind, target, ActionState.SUCCEEDED, "")

    def fail_action(
        self, kind: str, target: str = "", detail: str = ""
    ) -> ActionRecord:
        """
        Record that it did not work, and why.

        Does not increment `attempts`. One beginning is one attempt
        whether it ends in success or failure; counting the failure too
        would halve any retry bound built on it.
        """

        return self._settle(kind, target, ActionState.FAILED, detail)

    def should_retry(self, kind: str, target: str = "", limit: int = 2) -> bool:
        """
        Whether trying again is allowed. Bounded, never open-ended.

        Section 12: never blindly repeat the same action forever. The
        bound lives here because here is the only place that knows how
        many times it has already happened - a caller counting its own
        attempts is a caller whose count resets when it does.

        `limit` belongs to the caller because the right number depends on
        the action: relaunching an app that may still be starting is
        cheap, re-sending a payment is not.
        """

        record = self._actions.get((kind, target))

        if record is not None and record.state is ActionState.SUCCEEDED:
            return False

        return self.attempts_for(kind, target) < max(0, int(limit))

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------

    @property
    def recovering_from(self) -> ActionRecord | None:
        if self._recovering is None:
            return None

        return self._actions.get(self._recovering)

    def enter_recovery(self, kind: str, target: str = "") -> None:
        """
        Declare that this one action is being recovered.

        Scoped to exactly one `(kind, target)` rather than being a mode.
        A global "recovering" flag would let any action be repeated while
        it was set, which is the open door this module closes.
        """

        self._assign("_recovering", (kind, target))

    def leave_recovery(self) -> None:
        self._assign("_recovering", None)

    # ------------------------------------------------------------------
    # Observation
    # ------------------------------------------------------------------

    @property
    def revision(self) -> int:
        return self._revision

    def snapshot(self) -> CognitiveSnapshot:
        return CognitiveSnapshot(
            temporal=self.temporal,
            session_id=self._session_id,
            conversation_id=self._conversation_id,
            user=self._user,
            intent=self._intent,
            goal=self._goal,
            plan=self._plan,
            task_node=self._task_node,
            focus=self._focus,
            active_tools=tuple(self._tools),
            actions=tuple(self._actions.values()),
            recovering_from=self.recovering_from,
            revision=self._revision,
        )

    def __repr__(self) -> str:
        return (
            f"CognitiveState(session={self._session_id[:8]}, "
            f"conversation={self._conversation_id!r}, "
            f"node={self._task_node!r}, actions={len(self._actions)}, "
            f"revision={self._revision})"
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _in(self, state: ActionState) -> tuple[ActionRecord, ...]:
        return tuple(a for a in self._actions.values() if a.state is state)

    def _settle(
        self, kind: str, target: str, state: ActionState, detail: str
    ) -> ActionRecord:
        """
        Move an action to a terminal state, creating it if it is new.

        Created rather than rejected because both callers are legitimate:
        the agent loop begins an action and then settles it, but a tool
        that already ran - or a step recovered from a previous session -
        arrives here having never begun. Refusing that would mean the
        record was silently dropped, which is worse than an attempt count
        of one on something nobody watched start.
        """

        key = (kind, target)
        existing = self._actions.get(key)

        record = ActionRecord(
            kind=kind,
            target=target,
            state=state,
            attempts=max(1, existing.attempts if existing else 0),
            detail=detail,
            at=self.now,
        )

        if existing == record:
            return existing

        self._actions[key] = record
        self._bump()

        return record

    def _assign(self, attribute: str, value) -> None:
        """Set a field, and count it only if it moved."""

        if getattr(self, attribute) == value:
            return

        setattr(self, attribute, value)
        self._bump()

    def _bump(self) -> None:
        self._revision += 1


# ----------------------------------------------------------------------
# Where one of these is allowed to live
# ----------------------------------------------------------------------

# An hour of silence ends a task. Long enough that a person can put the
# phone down mid-task, answer the door and come back to an agent that
# still knows what it was doing.
DEFAULT_MAX_IDLE_SECONDS = 3600.0

# How often the access path may pay for the O(n) scan.
DEFAULT_SWEEP_INTERVAL_SECONDS = 300.0


class CognitiveStore:
    """
    One `CognitiveState` per session, and the only way to reach one.

    A store rather than a field on the engine, because `ConversationManager`
    says why in the comment above `_Turn`: one engine serves every session,
    so per-turn state kept on it "is a race, not a cache". Here the
    consequence would be worse than a race - two owners sharing one record
    of what has already been done means Aura skips a step for one of them.

    `SessionManager` already solved the same problem for session metadata,
    so this follows it: a dict keyed by session id behind a lock, swept on
    the access path with a throttle, no background thread. The one
    deliberate divergence is that `SessionManager` sweeps only when a
    session is created, while this sweeps on every access. Both bound the
    scan to once per interval; sweeping on access only changes which call
    pays for it. It matters here because a phone running an agent task
    holds one session and ticks it for hours - on the create-only rule
    that session's stale neighbours would never be reaped at all, and the
    entries here are action records and plans rather than four floats.

    Touching happens before sweeping, and that ordering is load bearing.
    See `for_session`.
    """

    def __init__(
        self,
        clock: TemporalClock | None = None,
        max_idle_seconds: float = DEFAULT_MAX_IDLE_SECONDS,
        sweep_interval_seconds: float = DEFAULT_SWEEP_INTERVAL_SECONDS,
    ):
        # Handed down to every state this store makes, so there is still
        # exactly one answer to "what time is it" in the process.
        self._clock = clock or TemporalClock()

        self._states: dict[str, CognitiveState] = {}
        self._touched: dict[str, datetime] = {}
        self._lock = RLock()

        self.max_idle_seconds = max_idle_seconds
        self.sweep_interval_seconds = sweep_interval_seconds
        self._last_sweep = self._clock.now()

    def for_session(self, session_id: str) -> CognitiveState:
        """
        The state for this session, created on first ask.

        The touch is recorded before the sweep runs, and that order is the
        thing that makes this safe. An agent task ticks every few seconds
        and reads its state on every tick; if the sweep went first it
        could reap the very entry the caller came for - and the agent
        would then re-open an app it had already opened, which is the
        exact failure this module exists to prevent. Touch first and a
        session in use can never expire underneath its own reader.
        """

        key = str(session_id or "default")

        with self._lock:
            state = self._states.get(key)

            if state is None:
                state = CognitiveState(clock=self._clock, session_id=key)
                self._states[key] = state

            now = self._clock.now()
            self._touched[key] = now

            self._maybe_sweep(now)

            return state

    def peek(self, session_id: str) -> CognitiveState | None:
        """
        Look without creating, and without counting as use.

        Diagnostics has to be able to inspect the store without changing
        what it holds - a reader that created entries would make the act
        of looking a source of the leak it was checking for.
        """

        with self._lock:
            return self._states.get(str(session_id or "default"))

    def sessions(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._states)

    def forget(self, session_id: str) -> bool:
        """
        Drop one session's state. False when there was none.

        A miss is a normal answer rather than an exception, because ids
        arrive from clients that invent them and every call site would
        otherwise have to handle the same non-event.
        """

        key = str(session_id or "default")

        with self._lock:
            self._touched.pop(key, None)

            return self._states.pop(key, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._states.clear()
            self._touched.clear()

    def cleanup_idle(self, max_idle_seconds: float | None = None) -> int:
        """
        Expire now, and report how many went.

        Public for the same reason `SessionManager.cleanup_old` is: an
        operator endpoint or a test wants the count immediately rather
        than at the next sweep interval.
        """

        idle = (
            self.max_idle_seconds
            if max_idle_seconds is None
            else max_idle_seconds
        )

        with self._lock:
            return self._expire(self._clock.now(), idle)

    def __len__(self) -> int:
        with self._lock:
            return len(self._states)

    def __repr__(self) -> str:
        return f"CognitiveStore(sessions={len(self)})"

    # -- internals: both assume the caller holds the lock. It is
    # -- reentrant, but neither needs to reacquire it.

    def _maybe_sweep(self, now: datetime) -> int:

        if self._elapsed(self._last_sweep, now) < self.sweep_interval_seconds:
            return 0

        self._last_sweep = now

        return self._expire(now, self.max_idle_seconds)

    def _expire(self, now: datetime, max_idle_seconds: float) -> int:

        stale = [
            key for key in self._states
            if self._elapsed(self._touched.get(key), now) > max_idle_seconds
        ]

        for key in stale:
            del self._states[key]
            self._touched.pop(key, None)

        return len(stale)

    @staticmethod
    def _elapsed(then: datetime | None, now: datetime) -> float:
        """
        Seconds between two readings of the injected clock.

        Wall clock, so it can step backwards - DST, or an NTP correction.
        A backwards step makes this negative, which reads as "no idle time
        has passed" and keeps the entry. That is the safe direction: this
        would rather hold a dead session for an extra hour than forget a
        live task mid-step. A forwards step could expire an idle entry
        early, which costs nothing, and cannot touch a live one because
        `for_session` touches before it sweeps.
        """

        if then is None:
            return 0.0

        return (now - then).total_seconds()
