"""
What happens when an action does not work.

Everything before this phase records success. `absorb` writes only the
verified branch, `is_done` asks what succeeded, and the seven node states
`brain/task_graph.py` can compute are fed by three. A launch that failed
twice and a launch nobody has tried yet still arrive at the server looking
identical, so the only thing that ever said "stop trying" was the device,
in prose, to the model - which works exactly as well as the model's
willingness to believe it.

Two mandates meet here. Section 11: verification "must not rely only on:
'the command executed without throwing'" - and the wire has to carry that
distinction, or the server cannot make it. Section 12: "never blindly
repeat the same action forever. Add bounded retry policies."

Three things are therefore tested, and they are deliberately separate:

*The bound.* One policy, asked rather than re-derived. `CognitiveState`
already knows how many attempts have happened and already declines to
retry a succeeded action; what it does not have is a per-kind limit,
because `should_retry`'s docstring says the number belongs to the caller.
Phase 7 is that caller.

*The ingest.* A failure has to reach the server as data. The test that
matters most here is idempotence: every tick re-sends the whole history,
so an ingest that counted each replay would exhaust the bound on step two
through repetition of the report alone - the hazard `absorb` already
guards for successes.

*The recovery.* Section 10 allows a completed node to run again only when
recovery explicitly requires it, and until now nothing ever required it.
There is one case the server can actually observe: an app that was
launched, verified, and has since left the foreground. The node says
SUCCESS, the postcondition is false, and without recovery the plan says
DONE forever while the task cannot finish.

Nothing here asserts that a particular function was called or a particular
constant exists. The bound is checked by exhausting it, the ingest by
reading device-shaped lines, and the recovery by asking the graph what
state a node is in afterwards.
"""

import pytest

from brain.agent_mode import absorb, read_action_failures
from brain.planner import StepKind, plan_for
from brain.recovery import (
    DEFAULT_RETRY_LIMIT,
    invalidated,
    limit_for,
    may_retry,
    reconcile,
)
from brain.task_graph import NodeState, build, render_plan
from core.cognitive import ActionState, CognitiveState

YOUTUBE = "com.google.android.youtube"
CHROME = "com.android.chrome"
REQUEST = "open YouTube and search for lofi music"


def states(state: CognitiveState, request: str = REQUEST) -> list[NodeState]:
    return [node.state for node in build(plan_for(request), state).nodes]


def tick(**extra) -> dict:
    """A minimally valid agent tick. `absorb` ignores anything that is not one."""

    base = {
        "agent_tick": True,
        "device": {},
        "accessibility_tree": {},
        "user_request": REQUEST,
    }
    base.update(extra)

    return base


def failing(state: CognitiveState, kind: str, target: str, times: int) -> CognitiveState:
    """Spend `times` attempts on an action, all of them unsuccessful."""

    for _ in range(times):
        state.begin_action(kind, target)
        state.fail_action(kind, target, "no such app")

    return state


def settle(state: CognitiveState, request: str = REQUEST) -> bool:
    """
    Reconcile the way production does, with stuckness actually measured.

    `stuck` is never passed as a literal by any test here. A test that
    asserted recovery while asserting its own precondition would be
    checking that the parameter is honoured, not that recovery happens
    when it should - and the gate is the part most worth getting right,
    because without it a transient overlay relaunches a healthy app.
    """

    plan = plan_for(request)

    return reconcile(plan, state, build(plan, state).is_stuck)


def stranded(state: CognitiveState, app: str = YOUTUBE) -> CognitiveState:
    """
    A launch that worked, an app that is gone, and no way forward left.

    The situation recovery exists for, built the way the device produces
    it: YouTube opened and verified, then the steps inside it spent their
    bounds against whatever is actually on screen. Once the plan is out of
    options and the app the plan named is not in the foreground, "it was
    killed" is the only reading left - which is what makes acting on it
    safe.
    """

    state.succeed_action("open_app", app)
    failing(state, "input_text", "node_3", limit_for("input_text"))
    state.observe(application=CHROME)

    return state


# ======================================================================
# 1. The bound (§12)
# ======================================================================

class TestTheRetryBound:
    """
    Bounded, never open-ended, and one policy rather than several.

    `should_retry(kind, target, limit)` puts the number in the caller's
    hands on purpose - "relaunching an app that may still be starting is
    cheap, re-sending a payment is not". Before this phase every caller
    took the default, which meant the number was a default parameter
    rather than a policy.
    """

    def test_a_fresh_action_may_be_tried(self):
        assert may_retry(CognitiveState(), "open_app", YOUTUBE)

    def test_the_bound_stops_it(self):
        state = failing(CognitiveState(), "open_app", YOUTUBE, limit_for("open_app"))

        assert not may_retry(state, "open_app", YOUTUBE)

    def test_one_attempt_short_of_the_bound_still_allows_another(self):
        limit = limit_for("open_app")
        state = failing(CognitiveState(), "open_app", YOUTUBE, limit - 1)

        assert may_retry(state, "open_app", YOUTUBE)

    def test_a_succeeded_action_is_never_retried(self):
        # Not because the bound was reached - because it worked. This is
        # the invariant that kills the open_app loop, and the policy must
        # not be able to talk over it.
        state = CognitiveState()
        state.succeed_action("open_app", YOUTUBE)

        assert not may_retry(state, "open_app", YOUTUBE)

    def test_the_bound_is_per_action_not_per_kind_alone(self):
        # Two apps are two actions. One app's exhausted attempts must not
        # veto another app's first, which is exactly the bug the device's
        # `"open_app:null"` key produces.
        state = failing(CognitiveState(), "open_app", CHROME, limit_for("open_app"))

        assert not may_retry(state, "open_app", CHROME)
        assert may_retry(state, "open_app", YOUTUBE)

    @pytest.mark.parametrize(
        "kind",
        ["open_app", "click", "input_text", "submit", "focus", "scroll", "back", "home"],
    )
    def test_every_action_the_device_can_send_has_a_bound(self, kind):
        assert limit_for(kind) >= 1

    def test_an_unknown_kind_is_still_bounded(self):
        # The device can send a kind this table has never heard of, and
        # "unknown means unlimited" is precisely the forever-repeat §12
        # forbids. A total function is the only safe shape.
        assert limit_for("something_new_entirely") == DEFAULT_RETRY_LIMIT

    def test_no_bound_is_zero(self):
        # A limit of zero would refuse the first attempt, which is not a
        # retry policy - it is a block, and blocking an action the prompt
        # offers would be an arbitrary restriction on the owner.
        for kind in ("open_app", "click", "input_text", "submit", "unknown"):
            assert limit_for(kind) >= 1


# ======================================================================
# 2. Reading a failure off the wire (§11)
# ======================================================================

class TestReadingFailuresOffTheWire:
    """
    The format is the one that already works, with the device's own words.

    `completed_actions` carries `kind(args) [VERIFIED]`, which
    `read_action_history` parses and `AccessibilityAgentTest` pins.
    Failures use the same signature and the device's other two
    `ExecutionResult` names, so nothing about the taxonomy is invented:
    UNVERIFIED means it executed and the postcondition was not observed,
    FAILED means it could not be executed. That distinction is §11's whole
    point, and it has to survive the wire to be worth anything.

    The count travels with the line rather than the line repeating,
    because the device already keeps the count and a repeated line would
    make the ingest's arithmetic depend on how many ticks happened to have
    passed.
    """

    def test_a_failed_line_is_read(self):
        assert read_action_failures([f"open_app({YOUTUBE}) [FAILED x1]"]) == [
            ("open_app", YOUTUBE, "FAILED", 1)
        ]

    def test_an_unverified_line_is_read(self):
        assert read_action_failures(["click(node_12) [UNVERIFIED x2]"]) == [
            ("click", "node_12", "UNVERIFIED", 2)
        ]

    def test_the_target_is_the_first_argument(self):
        # The same rule as successes: the part that identifies what was
        # acted on. A different rule here would give one action two
        # identities and defeat every "have I already done this?" lookup.
        assert read_action_failures(['input_text(node_3, "lofi") [FAILED x1]']) == [
            ("input_text", "node_3", "FAILED", 1)
        ]

    def test_an_argumentless_action_has_an_empty_target(self):
        assert read_action_failures(["submit() [UNVERIFIED x1]"]) == [
            ("submit", "", "UNVERIFIED", 1)
        ]

    def test_a_verified_line_is_not_a_failure(self):
        assert read_action_failures([f"open_app({YOUTUBE}) [VERIFIED]"]) == []

    def test_an_unreadable_line_is_skipped_not_guessed(self):
        # Half-reading it would put a fact in the cognitive state that no
        # device ever reported.
        assert read_action_failures([
            "open_app(com.x) failed",
            "[FAILED x1]",
            "open_app(com.x) [FAILED]",
            "open_app(com.x) [FAILED xmany]",
            "",
            None,
        ]) == []

    def test_several_lines_come_back_in_order(self):
        assert read_action_failures([
            f"open_app({CHROME}) [FAILED x2]",
            "click(node_9) [UNVERIFIED x1]",
        ]) == [
            ("open_app", CHROME, "FAILED", 2),
            ("click", "node_9", "UNVERIFIED", 1),
        ]

    def test_nothing_in_means_nothing_out(self):
        assert read_action_failures([]) == []
        assert read_action_failures(None) == []


# ======================================================================
# 3. Absorbing it
# ======================================================================

class TestAbsorbingFailures:

    def test_a_reported_failure_lands_as_failed(self):
        state = CognitiveState()
        absorb(state, tick(failed_actions=[f"open_app({YOUTUBE}) [FAILED x1]"]))

        record = state.action_for("open_app", YOUTUBE)

        assert record is not None
        assert record.state is ActionState.FAILED

    def test_the_attempt_count_matches_what_the_device_reported(self):
        state = CognitiveState()
        absorb(state, tick(failed_actions=[f"open_app({YOUTUBE}) [FAILED x2]"]))

        assert state.attempts_for("open_app", YOUTUBE) == 2

    def test_re_absorbing_the_same_tick_does_not_inflate_the_count(self):
        # The one that matters. Every tick re-sends the whole history, so
        # an ingest that counted each replay would exhaust the bound on
        # step two through repetition of the report alone - which is the
        # hazard `absorb` already guards for successes, and the reason the
        # count travels on the line instead of the line repeating.
        state = CognitiveState()
        report = tick(failed_actions=[f"open_app({YOUTUBE}) [FAILED x1]"])

        for _ in range(5):
            absorb(state, report)

        assert state.attempts_for("open_app", YOUTUBE) == 1
        assert may_retry(state, "open_app", YOUTUBE)

    def test_a_rising_count_is_followed(self):
        state = CognitiveState()
        absorb(state, tick(failed_actions=[f"open_app({YOUTUBE}) [FAILED x1]"]))
        absorb(state, tick(failed_actions=[f"open_app({YOUTUBE}) [FAILED x2]"]))

        assert state.attempts_for("open_app", YOUTUBE) == 2

    def test_a_count_beyond_the_bound_neither_hangs_nor_overshoots(self):
        # A malformed or wildly large count must not spin the ingest. Past
        # the bound nothing about behaviour changes anyway, so clamping
        # there is free.
        state = CognitiveState()
        absorb(state, tick(failed_actions=[f"open_app({YOUTUBE}) [FAILED x9999]"]))

        assert state.attempts_for("open_app", YOUTUBE) == limit_for("open_app")
        assert not may_retry(state, "open_app", YOUTUBE)

    def test_a_changed_verdict_updates_the_reason_without_spending_an_attempt(self):
        # A click that was executed-but-unverified can become un-executable
        # on the next attempt. The count has not moved, but what the model
        # is told about it should - the reason is the actionable half.
        state = CognitiveState()
        absorb(state, tick(failed_actions=["click(node_4) [UNVERIFIED x1]"]))
        first = state.action_for("click", "node_4").detail

        absorb(state, tick(failed_actions=["click(node_4) [FAILED x1]"]))
        second = state.action_for("click", "node_4").detail

        assert first and second and first != second
        assert state.attempts_for("click", "node_4") == 1

    def test_a_succeeded_action_is_not_resurrected_by_a_stale_failure(self):
        # The device clears its failure count on a verified success, but a
        # tick in flight can still carry the old line. Success is the later
        # fact and must win.
        state = CognitiveState()
        absorb(state, tick(completed_actions=[f"open_app({YOUTUBE}) [VERIFIED]"]))
        absorb(state, tick(
            completed_actions=[f"open_app({YOUTUBE}) [VERIFIED]"],
            failed_actions=[f"open_app({YOUTUBE}) [FAILED x2]"],
        ))

        record = state.action_for("open_app", YOUTUBE)

        assert record.state is ActionState.SUCCEEDED

    def test_absorbing_a_failure_counts_as_a_change(self):
        state = CognitiveState()

        assert absorb(state, tick(failed_actions=["click(node_1) [UNVERIFIED x1]"]))

    def test_re_absorbing_it_is_not_a_change(self):
        state = CognitiveState()
        report = tick(failed_actions=["click(node_1) [UNVERIFIED x1]"])
        absorb(state, report)

        # The goal is not the boolean itself; it is that a tick reporting
        # nothing new is indistinguishable from one that changed nothing.
        assert not absorb(state, dict(report, user_request=REQUEST))

    def test_a_tick_without_the_field_behaves_exactly_as_before(self):
        # Backward compatibility is not optional: an installed APK that
        # predates this phase sends no `failed_actions` at all, and it must
        # keep working rather than being treated as reporting nothing.
        with_field = CognitiveState()
        without = CognitiveState()

        absorb(with_field, tick(
            completed_actions=[f"open_app({YOUTUBE}) [VERIFIED]"],
            failed_actions=[],
        ))
        absorb(without, tick(completed_actions=[f"open_app({YOUTUBE}) [VERIFIED]"]))

        def recorded(state):
            return [(r.key, r.state, r.attempts) for r in state.actions]

        assert recorded(with_field) == recorded(without)
        assert states(with_field) == states(without)

    def test_a_non_tick_is_still_left_alone(self):
        state = CognitiveState()

        assert not absorb(state, {"failed_actions": ["click(n) [FAILED x1]"]})
        assert state.actions == ()


# ======================================================================
# 4. The graph now sees failure
# ======================================================================

class TestTheGraphNowSeesFailure:
    """
    The point of the phase, checked where it shows.

    Phase 6 could compute FAILED and BLOCKED but nothing produced them.
    These are the first tests in the repository where a device-shaped tick
    alone puts a node into either.
    """

    def test_an_exhausted_launch_reads_failed_and_blocks_what_follows(self):
        state = CognitiveState()
        limit = limit_for("open_app")
        absorb(state, tick(failed_actions=[f"open_app({YOUTUBE}) [FAILED x{limit}]"]))

        assert states(state)[:2] == [NodeState.FAILED, NodeState.BLOCKED]

    def test_the_whole_tail_is_blocked_not_just_the_next_step(self):
        state = CognitiveState()
        limit = limit_for("open_app")
        absorb(state, tick(failed_actions=[f"open_app({YOUTUBE}) [FAILED x{limit}]"]))

        assert set(states(state)[1:]) == {NodeState.BLOCKED}

    def test_a_failure_short_of_the_bound_leaves_the_step_workable(self):
        state = CognitiveState()
        absorb(state, tick(failed_actions=[f"open_app({YOUTUBE}) [FAILED x1]"]))
        graph = build(plan_for(REQUEST), state)

        assert graph.current is not None
        assert graph.current.step.kind is StepKind.OPEN_APP
        assert graph.current.state is not NodeState.FAILED

    def test_an_exhausted_plan_is_stuck_and_not_finished(self):
        state = CognitiveState()
        limit = limit_for("open_app")
        absorb(state, tick(failed_actions=[f"open_app({YOUTUBE}) [FAILED x{limit}]"]))
        graph = build(plan_for(REQUEST), state)

        assert graph.is_stuck
        assert not graph.is_finished

    def test_the_reason_reaches_the_rendered_plan(self):
        # A node that says only FAILED tells the model nothing it can act
        # on. What the device distinguished has to survive to the prompt.
        state = CognitiveState()
        limit = limit_for("open_app")
        absorb(state, tick(failed_actions=[f"open_app({YOUTUBE}) [FAILED x{limit}]"]))

        first = render_plan(plan_for(REQUEST), state)[0]

        assert "FAILED" in first
        assert first.rstrip().endswith("]")
        assert "[FAILED]" not in first


# ======================================================================
# 5. Postconditions that stop holding (§11)
# ======================================================================

class TestPostconditionsThatStopHolding:
    """
    Section 11 names the launch check exactly: expected package ==
    foreground package. That is a condition, not an event, and the
    difference is the bug: it is checked once at launch and then assumed
    forever. When the app leaves the foreground the node still says
    SUCCESS, the plan still says DONE, and the task can never finish.

    Only launches are checked, and for the same reason only launches may
    be SKIPPED: `focus.screen` is permanently empty in production because
    the device never fills in `AppInfo.activity`, so a claim about a
    focused field or rendered results would rest on nothing.
    """

    def test_an_app_that_left_the_foreground_invalidates_its_launch(self):
        state = CognitiveState()
        state.succeed_action("open_app", YOUTUBE)
        state.observe(application=CHROME)

        assert [step.detail for step in invalidated(plan_for(REQUEST), state)] == [
            "YouTube"
        ]

    def test_an_app_still_in_the_foreground_is_fine(self):
        state = CognitiveState()
        state.succeed_action("open_app", YOUTUBE)
        state.observe(application=YOUTUBE)

        assert invalidated(plan_for(REQUEST), state) == ()

    def test_an_unobserved_foreground_is_not_evidence_of_absence(self):
        # A tick that never reported a package leaves `focus.application`
        # empty. Reading that as "the app is gone" would invalidate every
        # launch on the first tick of every task.
        state = CognitiveState()
        state.succeed_action("open_app", YOUTUBE)

        assert invalidated(plan_for(REQUEST), state) == ()

    def test_a_launch_that_never_happened_is_not_invalidated(self):
        # It is simply not done. Invalidation is about completed work whose
        # postcondition has since become false.
        state = CognitiveState()
        state.observe(application=CHROME)

        assert invalidated(plan_for(REQUEST), state) == ()

    def test_no_other_step_kind_can_be_invalidated(self):
        # Every non-launch step of a finished search, with the app gone.
        # Only the launch may be reported, because only the launch has
        # evidence.
        state = CognitiveState()
        state.succeed_action("open_app", YOUTUBE)
        state.succeed_action("input_text", "node_3")
        state.succeed_action("submit", "")
        state.observe(application=CHROME)

        kinds = [step.kind for step in invalidated(plan_for(REQUEST), state)]

        assert kinds == [StepKind.OPEN_APP]

    def test_an_empty_plan_invalidates_nothing(self):
        state = CognitiveState()
        state.observe(application=CHROME)

        assert invalidated(plan_for("what's the weather"), state) == ()


# ======================================================================
# 6. Recovery (§10's exception, §12's engine)
# ======================================================================

class TestRecovery:
    """
    Section 10's exception, which until now had no way of being reached.

    A completed node may run again "only when explicitly required by
    recovery", and nothing ever required it - so RECOVERING was a state the
    graph could render and nothing could produce.

    The gate matters more than the mechanism. One package read cannot tell
    a killed app from one behind a permission dialog or a share sheet, so
    recovery waits until the plan has demonstrably run out of ways forward.
    A few doomed actions is the price; the alternative is relaunching
    healthy apps on a transient overlay, which is open_app, open_app,
    open_app - the loop this whole area exists to stop.
    """

    def test_a_stranded_launch_enters_recovery(self):
        state = stranded(CognitiveState())

        assert settle(state)

        record = state.recovering_from

        assert record is not None
        assert record.key == ("open_app", YOUTUBE)

    def test_the_node_then_reads_recovering(self):
        # Which is what makes the launch workable again. Without it the
        # node reads SUCCESS forever and `begin_action` declines to redo
        # it, so nothing can reopen the task.
        state = stranded(CognitiveState())
        settle(state)

        assert states(state)[0] is NodeState.RECOVERING

    def test_recovery_lets_the_launch_be_attempted_again(self):
        state = stranded(CognitiveState())
        settle(state)

        assert state.begin_action("open_app", YOUTUBE).state is ActionState.PENDING

    def test_a_stuck_plan_becomes_workable_again(self):
        # The whole point, stated once: before recovery there is nothing to
        # do and the task cannot finish; after it there is.
        state = stranded(CognitiveState())
        before = build(plan_for(REQUEST), state)
        settle(state)
        after = build(plan_for(REQUEST), state)

        assert before.is_stuck and before.current is None
        assert not after.is_stuck and after.current is not None

    def test_an_app_that_left_the_foreground_is_not_enough_on_its_own(self):
        # The gate. A launch whose app is not in front, with the plan still
        # making progress, is far more likely an overlay than a death - and
        # relaunching would destroy work in flight.
        state = CognitiveState()
        state.succeed_action("open_app", YOUTUBE)
        state.observe(application=CHROME)

        assert not settle(state)
        assert state.recovering_from is None
        assert states(state)[0] is NodeState.SUCCESS

    def test_a_stuck_plan_whose_app_is_present_is_left_stuck(self):
        # Stuck for some other reason - a search box that cannot be typed
        # into. Recovery has nothing to offer, and pretending otherwise
        # would relaunch an app that is already right there.
        state = CognitiveState()
        state.succeed_action("open_app", YOUTUBE)
        failing(state, "input_text", "node_3", limit_for("input_text"))
        state.observe(application=YOUTUBE)

        assert not settle(state)
        assert state.recovering_from is None
        assert build(plan_for(REQUEST), state).is_stuck

    def test_recovery_ends_when_the_postcondition_holds_again(self):
        state = stranded(CognitiveState())
        settle(state)

        state.observe(application=YOUTUBE)

        assert settle(state)
        assert state.recovering_from is None

    def test_reconciling_twice_changes_nothing_the_second_time(self):
        # Called on every tick, so it has to be idempotent or it would
        # report a change forever and any caller watching for one would
        # never settle.
        state = stranded(CognitiveState())

        assert settle(state)
        assert not settle(state)

    def test_recovery_scoped_elsewhere_is_not_stolen(self):
        # Recovery is scoped to exactly one action rather than being a
        # mode, and that is the whole safety property: a reconciler that
        # reassigned it would let this one decide what everything else is
        # allowed to repeat.
        state = stranded(CognitiveState())
        state.succeed_action("click", "node_7")
        state.enter_recovery("click", "node_7")

        settle(state)

        assert state.recovering_from.key == ("click", "node_7")

    def test_recovery_is_bounded_like_everything_else(self):
        # Otherwise this is §12's forever-loop wearing a different name. A
        # node in recovery is workable by definition - `_state_of` checks
        # RECOVERING before FAILED - so an app being killed over and over
        # would be relaunched over and over with nothing to stop it.
        state = CognitiveState()
        failing(state, "open_app", YOUTUBE, limit_for("open_app"))
        stranded(state)

        assert not settle(state)
        assert state.recovering_from is None

    def test_an_exhausted_launch_that_left_the_foreground_reads_failed(self):
        # And the plan says so rather than saying DONE, which is the honest
        # answer when the app is gone and there are no attempts left.
        state = CognitiveState()
        failing(state, "open_app", YOUTUBE, limit_for("open_app"))
        stranded(state)
        settle(state)

        assert states(state)[0] is not NodeState.RECOVERING

    def test_a_plan_with_no_steps_leaves_another_plan_s_recovery_alone(self):
        # Sessions are long lived and requests are not. A conversational
        # turn arriving mid-task must not clear the recovery the previous
        # request opened, which it would if recovery were a mode rather
        # than one named action.
        state = stranded(CognitiveState())
        settle(state)

        assert not settle(state, "tell me a joke")
        assert state.recovering_from is not None
