"""
One place that knows what Aura is doing.

Before this module the answer to "what is Aura in the middle of?" was
spread across an untyped `context: dict` travelling over HTTP, a `_Turn`
dataclass living for one request, a `ProactiveContext` assembled for one
decision, and the Android service's own fields. Four partial answers, no
authority, and no way to ask the question that matters most:

    have I already done this?

That question is the whole point. The reported failure it exists to kill is
an agent that opens YouTube, verifies it, and then opens YouTube again,
because nothing between one model call and the next remembered the first
one succeeding. A model asked to re-derive the situation from a screenshot
every tick will sometimes re-derive it wrongly; a recorded fact will not.

So this file pins four properties:

  * Time is never stored here. `core.temporal` owns "now", and a second
    copy of it would eventually disagree with the first.

  * An action is identified by what it did and to what, so "open_app
    com.google.android.youtube succeeded" is answerable without a model.

  * A succeeded action cannot quietly become pending again. Repetition has
    to be asked for by name, through recovery, with a bounded count.

  * What comes out is frozen. A decision made from a snapshot is
    reproducible, which is the same bargain `ProactiveContext` already
    makes.

Nothing here talks to a model, a device, or a socket.
"""

from dataclasses import FrozenInstanceError, is_dataclass
from datetime import datetime, timedelta

import pytest

from core.cognitive import (
    ActionRecord,
    ActionState,
    CognitiveSnapshot,
    CognitiveState,
    CognitiveStore,
    Focus,
)
from core.temporal import TemporalClock, TemporalContext


YOUTUBE = "com.google.android.youtube"


def moving_clock(start: datetime | None = None):
    """
    A clock a test can push forward.

    Returns the clock and a function that advances it, so a test can prove
    that something reads the time rather than remembering it.
    """

    current = [start or datetime(2026, 8, 24, 9, 0, 0)]

    def advance(seconds: int):
        current[0] += timedelta(seconds=seconds)

    return TemporalClock(now=lambda: current[0]), advance


def state(**kwargs) -> CognitiveState:
    clock, _ = moving_clock()
    kwargs.setdefault("clock", clock)
    return CognitiveState(**kwargs)


# ======================================================================
# 1. Time is borrowed, never kept
# ======================================================================

class TestTimeIsNotDuplicated:

    def test_now_comes_from_the_clock_every_time_it_is_asked(self):
        # The failure this prevents: a timestamp captured when the state
        # object was built, reported for the rest of the process, and
        # slowly becoming a lie. Section 16 gives "now" one owner.
        clock, advance = moving_clock()
        cognitive = CognitiveState(clock=clock)

        first = cognitive.now
        advance(30)
        second = cognitive.now

        assert second > first

    def test_the_temporal_context_is_the_shared_one(self):
        cognitive = state()

        assert isinstance(cognitive.temporal, TemporalContext)

    def test_there_is_no_timestamp_field_to_go_stale(self):
        # Structural, not behavioural: if a `now` or `timestamp`
        # attribute is ever added here, two things will answer the same
        # question and one of them will be wrong.
        cognitive = state()

        assert "now" not in vars(cognitive)
        assert "timestamp" not in vars(cognitive)

    def test_a_default_state_still_knows_the_time(self):
        # No clock passed. It must not become the one object in the
        # process with no idea what time it is.
        assert isinstance(CognitiveState().now, datetime)


# ======================================================================
# 2. Who and where: session, owner, conversation
# ======================================================================

class TestIdentityAndScope:

    def test_a_session_id_exists_without_being_asked_for(self):
        assert state().session_id

    def test_a_new_session_replaces_the_id(self):
        cognitive = state()
        before = cognitive.session_id

        cognitive.new_session()

        assert cognitive.session_id != before

    def test_the_owner_survives_a_new_conversation(self):
        # Section 38: nothing the owner has told Aura about themselves
        # should need saying twice because a conversation started.
        cognitive = state(user="Ember")

        cognitive.begin_conversation("conv-2")

        assert cognitive.user == "Ember"

    def test_the_owner_survives_a_new_session(self):
        cognitive = state(user="Ember")

        cognitive.new_session()

        assert cognitive.user == "Ember"

    def test_beginning_a_conversation_records_it(self):
        cognitive = state()

        cognitive.begin_conversation("conv-1")

        assert cognitive.conversation_id == "conv-1"

    def test_a_conversation_switch_clears_what_belonged_to_the_old_one(self):
        # Intent, plan and action history are all statements about *this*
        # conversation. Carrying them into the next one would let Aura
        # believe it had already opened an app for a request nobody made.
        cognitive = state()
        cognitive.begin_conversation("conv-1")
        cognitive.set_intent("action")
        cognitive.set_goal("open youtube and search minecraft")
        cognitive.set_plan(("open_app", "input_text"))
        cognitive.enter_node("open_app")
        cognitive.begin_action("open_app", YOUTUBE)
        cognitive.succeed_action("open_app", YOUTUBE)

        cognitive.begin_conversation("conv-2")

        assert cognitive.intent == ""
        assert cognitive.goal == ""
        assert cognitive.plan == ()
        assert cognitive.task_node == ""
        assert cognitive.actions == ()
        assert not cognitive.has_succeeded("open_app", YOUTUBE)

    def test_reopening_the_same_conversation_is_not_a_switch(self):
        # The phone re-sends its conversation id on every tick. Treating
        # that as a switch would erase the task mid-execution, which is
        # the exact loop this module exists to stop.
        cognitive = state()
        cognitive.begin_conversation("conv-1")
        cognitive.succeed_action("open_app", YOUTUBE)

        cognitive.begin_conversation("conv-1")

        assert cognitive.has_succeeded("open_app", YOUTUBE)


# ======================================================================
# 3. Intent, goal, plan, node
# ======================================================================

class TestIntentAndPlan:

    def test_intent_is_recorded_and_readable(self):
        cognitive = state()

        cognitive.set_intent("action")

        assert cognitive.intent == "action"

    def test_a_plan_is_a_frozen_sequence_of_steps(self):
        cognitive = state()

        cognitive.set_plan(["open_app", "find_search", "input_text"])

        assert cognitive.plan == ("open_app", "find_search", "input_text")

    def test_the_current_node_is_one_of_the_planned_steps(self):
        cognitive = state()
        cognitive.set_plan(("open_app", "input_text"))

        cognitive.enter_node("input_text")

        assert cognitive.task_node == "input_text"

    def test_a_goal_is_the_owners_words_not_a_paraphrase(self):
        # Stored verbatim so that section 23's rule - the query is
        # `Minecraft`, not `search for Minecraft` - is decided by whoever
        # parses the goal, once, rather than re-guessed per tick.
        cognitive = state()

        cognitive.set_goal("mở youtube tìm minecraft")

        assert cognitive.goal == "mở youtube tìm minecraft"

    def test_clearing_the_task_leaves_the_conversation_alone(self):
        cognitive = state()
        cognitive.begin_conversation("conv-1")
        cognitive.set_goal("open youtube")
        cognitive.succeed_action("open_app", YOUTUBE)

        cognitive.clear_task()

        assert cognitive.goal == ""
        assert cognitive.actions == ()
        assert cognitive.conversation_id == "conv-1"


# ======================================================================
# 4. What is in front of the user
# ======================================================================

class TestFocus:

    def test_focus_starts_empty_rather_than_guessed(self):
        cognitive = state()

        assert cognitive.focus == Focus()

    def test_observing_a_screen_records_the_application(self):
        cognitive = state()

        cognitive.observe(application=YOUTUBE, screen="home")

        assert cognitive.focus.application == YOUTUBE
        assert cognitive.focus.screen == "home"

    def test_observing_a_change_reports_that_it_changed(self):
        cognitive = state()
        cognitive.observe(application=YOUTUBE, screen="home")

        assert cognitive.observe(application=YOUTUBE, screen="results") is True

    def test_observing_the_same_screen_reports_no_change(self):
        # A tick that changed nothing must be distinguishable from one
        # that did, or a verification loop cannot tell "still loading"
        # from "arrived".
        cognitive = state()
        cognitive.observe(application=YOUTUBE, screen="home")

        assert cognitive.observe(application=YOUTUBE, screen="home") is False

    def test_observing_only_the_application_leaves_the_screen_alone(self):
        cognitive = state()
        cognitive.observe(application=YOUTUBE, screen="results")

        cognitive.observe(application=YOUTUBE)

        assert cognitive.focus.screen == "results"

    def test_focus_is_frozen(self):
        cognitive = state()
        cognitive.observe(application=YOUTUBE)

        with pytest.raises(FrozenInstanceError):
            cognitive.focus.application = "com.android.chrome"


# ======================================================================
# 5. Actions: the anti-repetition invariant
# ======================================================================

class TestActionsAreRecordedOnce:

    def test_an_action_begins_pending(self):
        cognitive = state()

        record = cognitive.begin_action("open_app", YOUTUBE)

        assert record.state is ActionState.PENDING
        assert record.kind == "open_app"
        assert record.target == YOUTUBE

    def test_a_succeeded_action_is_no_longer_pending(self):
        cognitive = state()
        cognitive.begin_action("open_app", YOUTUBE)

        cognitive.succeed_action("open_app", YOUTUBE)

        assert cognitive.pending == ()
        assert cognitive.has_succeeded("open_app", YOUTUBE)

    def test_the_same_action_is_one_record_not_two(self):
        # Identity is (kind, target). Two records for one intent would
        # make "have I done this?" unanswerable, which is how the
        # open_app loop survived.
        cognitive = state()

        cognitive.begin_action("open_app", YOUTUBE)
        cognitive.begin_action("open_app", YOUTUBE)

        assert len(cognitive.actions) == 1

    def test_beginning_a_succeeded_action_again_does_not_reopen_it(self):
        # The invariant that kills the loop. A caller that asks to redo
        # finished work is told, by the state itself, that it is finished.
        cognitive = state()
        cognitive.succeed_action("open_app", YOUTUBE)

        cognitive.begin_action("open_app", YOUTUBE)

        assert cognitive.has_succeeded("open_app", YOUTUBE)
        assert cognitive.pending == ()

    def test_a_different_target_is_a_different_action(self):
        cognitive = state()
        cognitive.succeed_action("open_app", YOUTUBE)

        assert not cognitive.has_succeeded("open_app", "com.android.chrome")

    def test_a_failure_is_not_a_success(self):
        cognitive = state()

        cognitive.fail_action("open_app", YOUTUBE, detail="package not found")

        assert not cognitive.has_succeeded("open_app", YOUTUBE)
        assert cognitive.failed[0].detail == "package not found"

    def test_success_after_failure_is_success(self):
        cognitive = state()
        cognitive.fail_action("open_app", YOUTUBE, detail="not foreground yet")

        cognitive.succeed_action("open_app", YOUTUBE)

        assert cognitive.has_succeeded("open_app", YOUTUBE)
        assert cognitive.failed == ()

    def test_the_completed_actions_read_back_in_the_order_they_happened(self):
        # This is what gets rendered into the prompt's COMPLETED ACTIONS
        # section, so the order has to be the order of events.
        cognitive = state()
        cognitive.succeed_action("open_app", YOUTUBE)
        cognitive.succeed_action("input_text", "Minecraft")

        assert [a.kind for a in cognitive.succeeded] == [
            "open_app", "input_text"
        ]

    def test_an_action_record_is_frozen(self):
        cognitive = state()
        record = cognitive.begin_action("open_app", YOUTUBE)

        with pytest.raises(FrozenInstanceError):
            record.state = ActionState.SUCCEEDED

    def test_an_action_carries_when_it_last_moved(self):
        clock, advance = moving_clock()
        cognitive = CognitiveState(clock=clock)
        cognitive.begin_action("open_app", YOUTUBE)
        advance(5)

        cognitive.succeed_action("open_app", YOUTUBE)

        record = cognitive.action_for("open_app", YOUTUBE)
        assert record.at is not None
        assert record.at == cognitive.now


# ======================================================================
# 6. Bounded retry
# ======================================================================

class TestRetryIsBounded:

    def test_attempts_count_from_zero(self):
        assert state().attempts_for("open_app", YOUTUBE) == 0

    def test_each_beginning_counts_as_an_attempt(self):
        cognitive = state()

        cognitive.begin_action("open_app", YOUTUBE)
        cognitive.fail_action("open_app", YOUTUBE)
        cognitive.begin_action("open_app", YOUTUBE)

        assert cognitive.attempts_for("open_app", YOUTUBE) == 2

    def test_a_fresh_action_may_be_retried(self):
        assert state().should_retry("open_app", YOUTUBE, limit=3) is True

    def test_retrying_stops_at_the_limit(self):
        # Section 12: never blindly repeat the same action forever. The
        # bound lives here because here is the only place that knows how
        # many times it has already happened.
        cognitive = state()
        for _ in range(3):
            cognitive.begin_action("open_app", YOUTUBE)
            cognitive.fail_action("open_app", YOUTUBE)

        assert cognitive.should_retry("open_app", YOUTUBE, limit=3) is False

    def test_a_succeeded_action_is_never_retried(self):
        cognitive = state()
        cognitive.succeed_action("open_app", YOUTUBE)

        assert cognitive.should_retry("open_app", YOUTUBE, limit=3) is False

    def test_the_limit_is_the_callers_to_choose(self):
        cognitive = state()
        cognitive.begin_action("open_app", YOUTUBE)
        cognitive.fail_action("open_app", YOUTUBE)

        assert cognitive.should_retry("open_app", YOUTUBE, limit=1) is False
        assert cognitive.should_retry("open_app", YOUTUBE, limit=2) is True


# ======================================================================
# 7. Recovery
# ======================================================================

class TestRecovery:

    def test_nothing_is_being_recovered_by_default(self):
        assert state().recovering_from is None

    def test_recovery_names_the_action_it_is_recovering(self):
        cognitive = state()
        cognitive.fail_action("input_text", "Minecraft", detail="no focus")

        cognitive.enter_recovery("input_text", "Minecraft")

        assert cognitive.recovering_from.kind == "input_text"
        assert cognitive.recovering_from.detail == "no focus"

    def test_leaving_recovery_clears_it(self):
        cognitive = state()
        cognitive.fail_action("input_text", "Minecraft")
        cognitive.enter_recovery("input_text", "Minecraft")

        cognitive.leave_recovery()

        assert cognitive.recovering_from is None

    def test_recovery_can_reopen_a_succeeded_action_deliberately(self):
        # Section 10 allows exactly one way for finished work to run
        # again: recovery asking for it by name. The plain path still
        # cannot, which is what makes this safe.
        cognitive = state()
        cognitive.succeed_action("open_app", YOUTUBE)

        cognitive.enter_recovery("open_app", YOUTUBE)
        cognitive.begin_action("open_app", YOUTUBE)

        assert cognitive.action_for("open_app", YOUTUBE).state is (
            ActionState.PENDING
        )

    def test_recovery_does_not_reopen_an_unrelated_action(self):
        cognitive = state()
        cognitive.succeed_action("open_app", YOUTUBE)
        cognitive.succeed_action("input_text", "Minecraft")

        cognitive.enter_recovery("input_text", "Minecraft")
        cognitive.begin_action("open_app", YOUTUBE)

        assert cognitive.has_succeeded("open_app", YOUTUBE)


# ======================================================================
# 8. Tools in flight
# ======================================================================

class TestActiveTools:

    def test_no_tool_is_active_at_rest(self):
        assert state().active_tools == ()

    def test_a_started_tool_is_active(self):
        cognitive = state()

        cognitive.tool_started("open_app")

        assert cognitive.active_tools == ("open_app",)

    def test_a_finished_tool_is_not_active(self):
        cognitive = state()
        cognitive.tool_started("open_app")

        cognitive.tool_finished("open_app")

        assert cognitive.active_tools == ()

    def test_finishing_a_tool_that_never_started_is_not_an_error(self):
        # A crash between start and finish must not make the next
        # finish() raise, or one lost event poisons the whole session.
        state().tool_finished("open_app")

    def test_the_same_tool_twice_is_listed_once(self):
        cognitive = state()

        cognitive.tool_started("open_app")
        cognitive.tool_started("open_app")

        assert cognitive.active_tools == ("open_app",)


# ======================================================================
# 9. Snapshots are frozen and complete
# ======================================================================

class TestSnapshot:

    def test_a_snapshot_is_a_frozen_dataclass(self):
        snapshot = state().snapshot()

        assert is_dataclass(snapshot)
        assert isinstance(snapshot, CognitiveSnapshot)

        with pytest.raises(FrozenInstanceError):
            snapshot.intent = "action"

    def test_a_snapshot_does_not_change_when_the_state_moves_on(self):
        # The bargain ProactiveContext already makes: given this exact
        # snapshot, a decision is always the same one.
        cognitive = state()
        cognitive.set_intent("conversation")
        snapshot = cognitive.snapshot()

        cognitive.set_intent("action")
        cognitive.succeed_action("open_app", YOUTUBE)

        assert snapshot.intent == "conversation"
        assert snapshot.actions == ()

    def test_a_snapshot_carries_every_tracked_concept(self):
        # Section 8 lists what has to be trackable. If a field is added
        # to the state and not to the snapshot, consumers go back to
        # reaching into the live object, which is how the duplication
        # started.
        cognitive = state(user="Ember")
        cognitive.begin_conversation("conv-1")
        cognitive.set_intent("action")
        cognitive.set_goal("open youtube")
        cognitive.set_plan(("open_app",))
        cognitive.enter_node("open_app")
        cognitive.observe(application=YOUTUBE, screen="home")
        cognitive.tool_started("open_app")
        cognitive.succeed_action("open_app", YOUTUBE)

        snapshot = cognitive.snapshot()

        assert snapshot.session_id == cognitive.session_id
        assert snapshot.conversation_id == "conv-1"
        assert snapshot.user == "Ember"
        assert snapshot.intent == "action"
        assert snapshot.goal == "open youtube"
        assert snapshot.plan == ("open_app",)
        assert snapshot.task_node == "open_app"
        assert snapshot.focus.application == YOUTUBE
        assert snapshot.active_tools == ("open_app",)
        assert snapshot.succeeded[0].kind == "open_app"
        assert isinstance(snapshot.temporal, TemporalContext)
        assert snapshot.recovering_from is None

    def test_a_snapshot_renders_without_secrets_or_objects(self):
        # It ends up in logs and diagnostics, so everything in it must be
        # primitive. Sections 28 and 30: nothing here may carry a key, a
        # socket or a live handle.
        cognitive = state(user="Ember")
        cognitive.observe(application=YOUTUBE)

        rendered = cognitive.snapshot().as_dict()

        assert rendered["user"] == "Ember"
        assert rendered["focus"]["application"] == YOUTUBE

        def primitive(value) -> bool:
            if isinstance(value, dict):
                return all(primitive(v) for v in value.values())
            if isinstance(value, (list, tuple)):
                return all(primitive(v) for v in value)
            return isinstance(value, (str, int, float, bool, type(None)))

        assert primitive(rendered)


# ======================================================================
# 10. Observability
# ======================================================================

class TestRevision:

    def test_a_change_bumps_the_revision(self):
        cognitive = state()
        before = cognitive.revision

        cognitive.set_intent("action")

        assert cognitive.revision > before

    def test_an_unchanged_observation_does_not_bump_it(self):
        # So that "has anything actually happened since the last tick?"
        # is one integer comparison rather than a diff of the whole
        # object.
        cognitive = state()
        cognitive.observe(application=YOUTUBE, screen="home")
        before = cognitive.revision

        cognitive.observe(application=YOUTUBE, screen="home")

        assert cognitive.revision == before

    def test_setting_the_same_intent_twice_does_not_bump_it(self):
        cognitive = state()
        cognitive.set_intent("action")
        before = cognitive.revision

        cognitive.set_intent("action")

        assert cognitive.revision == before


# ======================================================================
# The store: one home, not a field on a shared object
# ======================================================================


class TestOneStatePerSession:
    """
    Where a `CognitiveState` is allowed to live.

    Not on `ConversationManager`. `brain/conversation.py` says why, in
    the comment above `_Turn`: one engine serves every session on the
    server, so a per-turn field on it "is a race, not a cache". The same
    applies here with worse consequences - two owners sharing one record
    of what has already been done would make Aura skip a step for one of
    them.

    So the state is keyed by session, exactly the way `SessionManager`
    already keys its metadata, and the store is the only way to reach it.
    """

    def test_the_same_session_gets_the_same_state_back(self):
        # The whole point. Two ticks of one task must see one record, or
        # the second one cannot know what the first one did.
        store = CognitiveStore()

        first = store.for_session("s1")
        first.succeed_action("open_app", YOUTUBE)

        assert store.for_session("s1").has_succeeded("open_app", YOUTUBE)

    def test_different_sessions_do_not_share_a_state(self):
        store = CognitiveStore()

        store.for_session("s1").succeed_action("open_app", YOUTUBE)

        assert not store.for_session("s2").has_succeeded("open_app", YOUTUBE)

    def test_a_state_reads_the_clock_the_store_was_given(self):
        # Section 16, and survey item 6: this must not become a seventh
        # source of "now". The store hands its own clock down rather than
        # letting each state build one.
        clock, advance = moving_clock()
        store = CognitiveStore(clock=clock)

        advance(90)

        assert store.for_session("s1").now == clock.now()

    def test_the_store_reports_how_many_sessions_it_holds(self):
        store = CognitiveStore()

        store.for_session("s1")
        store.for_session("s2")
        store.for_session("s1")

        assert len(store) == 2

    def test_forgetting_a_session_drops_its_state(self):
        store = CognitiveStore()
        store.for_session("s1").succeed_action("open_app", YOUTUBE)

        assert store.forget("s1") is True
        assert not store.for_session("s1").has_succeeded("open_app", YOUTUBE)

    def test_forgetting_an_unknown_session_is_not_an_error(self):
        # Callers arrive from HTTP with ids they invented. A miss is a
        # normal answer, not an exception to handle at every call site.
        store = CognitiveStore()

        assert store.forget("never-existed") is False

    def test_clearing_drops_everything(self):
        store = CognitiveStore()
        store.for_session("s1")
        store.for_session("s2")

        store.clear()

        assert len(store) == 0

    def test_an_absent_session_can_be_read_without_creating_one(self):
        # Diagnostics has to be able to look without leaving a mark, or
        # the act of inspecting the store changes what it holds.
        store = CognitiveStore()

        assert store.peek("s1") is None
        assert len(store) == 0


class TestTheStoreDoesNotLeak:
    """
    Ids arrive from the client, so the dict is unbounded unless something
    removes entries.

    `server/session.py` records this exact bug being fixed once already
    (AURA-P1-006): `cleanup_old` existed with no caller, and on a
    long-lived server that is a slow leak no request ever pays for. The
    same shape of state gets the same discipline - swept on the access
    path, throttled, no background thread.
    """

    def test_an_idle_session_is_dropped(self):
        clock, advance = moving_clock()
        store = CognitiveStore(clock=clock, max_idle_seconds=60, sweep_interval_seconds=0)

        store.for_session("stale")
        advance(600)

        store.for_session("fresh")

        assert store.peek("stale") is None
        assert store.peek("fresh") is not None

    def test_a_session_in_use_survives_a_sweep_of_its_own(self):
        # Touch before sweep, and that ordering is load bearing. A task
        # mid-flight ticks every few seconds; if the sweep ran first it
        # could reap the very state the caller came to read, and the
        # agent would forget it had already opened the app.
        clock, advance = moving_clock()
        store = CognitiveStore(clock=clock, max_idle_seconds=60, sweep_interval_seconds=0)

        store.for_session("busy").succeed_action("open_app", YOUTUBE)
        advance(600)

        assert store.for_session("busy").has_succeeded("open_app", YOUTUBE)

    def test_sweeping_is_throttled(self):
        # The scan is O(n) and the common case is a live session. It must
        # not run on every access.
        clock, advance = moving_clock()
        store = CognitiveStore(
            clock=clock, max_idle_seconds=60, sweep_interval_seconds=300
        )

        store.for_session("stale")
        advance(120)

        store.for_session("other")

        # Idle past its age, but the interval has not elapsed yet.
        assert store.peek("stale") is not None

        advance(300)
        store.for_session("later")

        assert store.peek("stale") is None

    def test_expiry_can_be_asked_for_directly_and_reports_a_count(self):
        # Same reason `SessionManager.cleanup_old` stayed public: an
        # operator endpoint or a test wants the number now, not at the
        # next interval.
        clock, advance = moving_clock()
        store = CognitiveStore(clock=clock, max_idle_seconds=60)

        store.for_session("a")
        store.for_session("b")
        advance(600)

        assert store.cleanup_idle() == 2
        assert len(store) == 0
