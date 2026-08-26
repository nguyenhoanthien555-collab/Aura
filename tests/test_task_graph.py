"""
The seven node states, and what they are derived from.

Section 10 names PENDING, RUNNING, SUCCESS, FAILED, SKIPPED, BLOCKED and
RECOVERING, and requires specifically that "a completed node must not be
repeatedly executed unless explicitly required by recovery".

These tests treat node state as a *derived* quantity. Nothing here writes
a node state anywhere and then reads it back - that would test a store,
and a second store of progress is the bug section 8 forbids. Every test
arranges facts on a `CognitiveState` the way the device would, then asks
the graph what those facts mean.

Two states are worth reading the tests for even if the rest are obvious:

SKIPPED is not "we decided not to bother". It is "the postcondition
already holds", and today that is observable for exactly one step kind -
launching an app that is already in the foreground. It is the one state
that changes behaviour on real hardware right now, because without it a
plan whose app is already open still tells the model to open it.

BLOCKED is about a *later* node. A node that failed on its own account is
FAILED; its successors are BLOCKED, because telling the model to type into
the search box of an app that never opened is worse than telling it
nothing.
"""

import inspect

import pytest

from brain.planner import Plan, PlanStep, StepKind, plan_for
from brain.task_graph import (
    NodeState,
    TaskGraph,
    TaskNode,
    build,
    current_step,
    render_plan,
)
from core.cognitive import CognitiveState

YOUTUBE = "com.google.android.youtube"

REQUEST = "open YouTube and search for lofi music"

PICK = "open YouTube and search for lofi music and pick the first result"


def states(graph) -> list[NodeState]:
    return [node.state for node in graph.nodes]


def launched(state, app: str = YOUTUBE) -> CognitiveState:
    state.succeed_action("open_app", app)

    return state


def searched(state: CognitiveState, app: str = YOUTUBE) -> CognitiveState:
    """A state that has opened an app, typed, and submitted."""

    state.succeed_action("open_app", app)
    state.succeed_action("input_text", "search_box")
    state.succeed_action("submit", "")
    return state


def exhaust(state, kind: str = "open_app", target: str = YOUTUBE) -> CognitiveState:
    """Fail an action until `should_retry` says no more."""

    while state.should_retry(kind, target):
        state.begin_action(kind, target)
        state.fail_action(kind, target, "no such app")

    return state


# ----------------------------------------------------------------------
# The vocabulary
# ----------------------------------------------------------------------

class TestTheVocabulary:
    """Section 10 lists seven states. Not six, and not nine."""

    def test_all_seven_states_exist(self):
        assert {state.name for state in NodeState} == {
            "PENDING",
            "RUNNING",
            "SUCCESS",
            "FAILED",
            "SKIPPED",
            "BLOCKED",
            "RECOVERING",
        }

    def test_there_are_exactly_seven(self):
        assert len(NodeState) == 7

    def test_a_state_is_its_own_wire_value(self):
        # Same convention as ActionState, TaskClass and AuraState: the
        # value is a plain lowercase string so a state can be logged or
        # rendered without a conversion table.
        assert NodeState.BLOCKED == "blocked"
        assert all(state.value == state.name.lower() for state in NodeState)

    def test_success_is_spelled_the_way_the_mandate_spells_it(self):
        # ActionState says SUCCEEDED, section 10 says SUCCESS, and they
        # are not the same vocabulary: an action is one attempt at a
        # device operation, a node is a unit of plan. Matching the
        # mandate's word here keeps the two levels visibly distinct.
        assert NodeState.SUCCESS.value == "success"
        assert not hasattr(NodeState, "SUCCEEDED")


# ----------------------------------------------------------------------
# One state at a time
# ----------------------------------------------------------------------

class TestNothingHasHappenedYet:

    def test_a_fresh_plan_is_all_pending(self):
        graph = build(plan_for(REQUEST), CognitiveState())

        assert states(graph) == [NodeState.PENDING] * 5

    def test_an_empty_plan_has_no_nodes(self):
        graph = build(Plan(), CognitiveState())

        assert graph.nodes == ()
        assert not graph

    def test_a_plan_with_steps_is_truthy(self):
        assert build(plan_for(REQUEST), CognitiveState())


class TestSuccess:

    def test_a_launched_app_is_a_successful_node(self):
        graph = build(plan_for(REQUEST), launched(CognitiveState()))

        assert graph.nodes[0].state is NodeState.SUCCESS

    def test_the_rest_are_still_pending(self):
        graph = build(plan_for(REQUEST), launched(CognitiveState()))

        assert states(graph)[1:] == [NodeState.PENDING] * 4

    def test_launching_a_different_app_succeeds_nothing(self):
        state = launched(CognitiveState(), "com.android.chrome")

        assert build(plan_for(REQUEST), state).nodes[0].state is NodeState.PENDING


class TestSkipped:
    """
    The state that earns its place on real hardware today.

    Without it, "open YouTube and search Minecraft" issued while YouTube
    is already in the foreground still tells the model to open YouTube.
    """

    def test_an_app_already_in_the_foreground_is_skipped(self):
        state = CognitiveState()
        state.observe(application=YOUTUBE)

        assert build(plan_for(REQUEST), state).nodes[0].state is NodeState.SKIPPED

    def test_a_skipped_node_is_not_what_to_do_next(self):
        state = CognitiveState()
        state.observe(application=YOUTUBE)

        assert current_step(plan_for(REQUEST), state).kind is StepKind.FOCUS_SEARCH

    def test_a_different_foreground_app_skips_nothing(self):
        state = CognitiveState()
        state.observe(application="com.android.chrome")

        assert build(plan_for(REQUEST), state).nodes[0].state is NodeState.PENDING

    def test_nothing_observed_skips_nothing(self):
        assert build(plan_for(REQUEST), CognitiveState()).nodes[0].state is (
            NodeState.PENDING
        )

    def test_having_actually_opened_it_reads_as_success_not_skipped(self):
        # Both facts are true at once after a real launch: the action
        # succeeded and the app is in the foreground. Reporting SKIPPED
        # would say we never did it.
        state = launched(CognitiveState())
        state.observe(application=YOUTUBE)

        assert build(plan_for(REQUEST), state).nodes[0].state is NodeState.SUCCESS

    def test_only_a_launch_can_be_skipped(self):
        # A focused search box and rendered results are not observable
        # from what the tick reports - `focus.screen` arrives empty
        # because the device never fills in the activity name - so no
        # other step may claim its goal already holds.
        state = CognitiveState()
        state.observe(application=YOUTUBE)

        assert states(build(plan_for(REQUEST), state))[1:] == [NodeState.PENDING] * 4


class TestRunning:

    def test_an_action_in_flight_is_running(self):
        state = CognitiveState()
        state.begin_action("open_app", YOUTUBE)

        assert build(plan_for(REQUEST), state).nodes[0].state is NodeState.RUNNING

    def test_a_running_node_is_still_the_current_one(self):
        state = CognitiveState()
        state.begin_action("open_app", YOUTUBE)

        assert current_step(plan_for(REQUEST), state).kind is StepKind.OPEN_APP

    def test_running_carries_the_attempt_count(self):
        state = CognitiveState()
        state.begin_action("open_app", YOUTUBE)

        assert build(plan_for(REQUEST), state).nodes[0].attempts == 1


class TestFailed:

    def test_one_failure_is_not_yet_a_failed_node(self):
        # Still retryable, so the honest report is "not done", not
        # "cannot be done". Reporting FAILED here would strand a plan
        # that a second attempt would finish.
        state = CognitiveState()
        state.begin_action("open_app", YOUTUBE)
        state.fail_action("open_app", YOUTUBE, "no such app")

        assert build(plan_for(REQUEST), state).nodes[0].state is NodeState.PENDING

    def test_an_exhausted_action_is_a_failed_node(self):
        graph = build(plan_for(REQUEST), exhaust(CognitiveState()))

        assert graph.nodes[0].state is NodeState.FAILED

    def test_a_failed_node_keeps_the_reason(self):
        graph = build(plan_for(REQUEST), exhaust(CognitiveState()))

        assert graph.nodes[0].detail == "no such app"

    def test_the_node_reports_the_state_s_own_attempt_count(self):
        # Not a second tally kept here. `attempts_for` owns the count and
        # `should_retry` owns the bound; the graph reports what they say.
        # Two retry accountings that disagree is a live problem on the
        # Android side already, and one is enough.
        state = exhaust(CognitiveState())

        assert build(plan_for(REQUEST), state).nodes[0].attempts == (
            state.attempts_for("open_app", YOUTUBE)
        )

    def test_a_failure_still_short_of_the_bound_stays_available(self):
        state = CognitiveState()
        state.begin_action("open_app", YOUTUBE)
        state.fail_action("open_app", YOUTUBE, "no such app")

        graph = build(plan_for(REQUEST), state)

        assert state.should_retry("open_app", YOUTUBE)
        assert graph.current.step.kind is StepKind.OPEN_APP


class TestBlocked:

    def test_a_node_after_a_failure_is_blocked(self):
        graph = build(plan_for(REQUEST), exhaust(CognitiveState()))

        assert states(graph)[1:] == [NodeState.BLOCKED] * 4

    def test_a_blocked_plan_has_nothing_to_do_next(self):
        assert current_step(plan_for(REQUEST), exhaust(CognitiveState())) is None

    def test_blocked_is_not_the_same_as_finished(self):
        graph = build(plan_for(REQUEST), exhaust(CognitiveState()))

        assert graph.is_stuck
        assert not graph.is_finished

    def test_a_finished_plan_is_not_stuck(self):
        state = launched(CognitiveState())
        state.succeed_action("input_text", "3")
        state.succeed_action("submit", "3")

        graph = build(plan_for(REQUEST), state)

        assert graph.is_finished
        assert not graph.is_stuck

    def test_a_fresh_plan_is_neither(self):
        graph = build(plan_for(REQUEST), CognitiveState())

        assert not graph.is_finished
        assert not graph.is_stuck

    def test_a_node_already_succeeded_is_not_retroactively_blocked(self):
        # Order matters, not membership: a launch that worked stays
        # SUCCESS even when a later step has exhausted itself.
        state = launched(CognitiveState())
        exhaust(state, "input_text", "3")

        assert build(plan_for(REQUEST), state).nodes[0].state is NodeState.SUCCESS

    def test_an_empty_plan_is_neither_finished_nor_stuck(self):
        # Nothing was asked, so there is nothing to be done or blocked
        # on. "Finished" would read as an accomplishment.
        graph = build(Plan(), CognitiveState())

        assert not graph.is_finished
        assert not graph.is_stuck


class TestRecovering:
    """Section 10's one sanctioned way for a completed node to run again."""

    def test_a_recovered_node_reads_as_recovering(self):
        state = launched(CognitiveState())
        state.enter_recovery("open_app", YOUTUBE)

        assert build(plan_for(REQUEST), state).nodes[0].state is NodeState.RECOVERING

    def test_recovery_beats_success(self):
        # The whole point: recovery reopens something already done. If
        # SUCCESS won, the node could never be revisited and the mandate's
        # exception would have no effect.
        state = launched(CognitiveState())
        state.enter_recovery("open_app", YOUTUBE)

        assert build(plan_for(REQUEST), state).nodes[0].state is not NodeState.SUCCESS

    def test_a_recovering_node_becomes_the_current_one_again(self):
        state = launched(CognitiveState())
        state.enter_recovery("open_app", YOUTUBE)

        assert current_step(plan_for(REQUEST), state).kind is StepKind.OPEN_APP

    def test_leaving_recovery_restores_success(self):
        state = launched(CognitiveState())
        state.enter_recovery("open_app", YOUTUBE)
        state.leave_recovery()

        assert build(plan_for(REQUEST), state).nodes[0].state is NodeState.SUCCESS

    def test_recovering_a_different_action_leaves_this_node_alone(self):
        # Chrome is launched too, so the record recovery names really
        # exists - otherwise `recovering_from` is None and this passes
        # without testing anything.
        state = launched(CognitiveState())
        launched(state, "com.android.chrome")
        state.enter_recovery("open_app", "com.android.chrome")

        assert build(plan_for(REQUEST), state).nodes[0].state is NodeState.SUCCESS

    def test_recovery_unblocks_what_the_failure_blocked(self):
        # A plan whose launch is being retried is not a plan whose later
        # steps are permanently unreachable.
        state = exhaust(CognitiveState())
        state.enter_recovery("open_app", YOUTUBE)

        graph = build(plan_for(REQUEST), state)

        assert graph.nodes[0].state is NodeState.RECOVERING
        assert not graph.is_stuck


# ----------------------------------------------------------------------
# What to do next
# ----------------------------------------------------------------------

class TestCurrent:

    def test_the_current_node_is_the_first_unfinished_one(self):
        graph = build(plan_for(REQUEST), launched(CognitiveState()))

        assert graph.current.step.kind is StepKind.FOCUS_SEARCH

    def test_a_finished_plan_has_no_current_node(self):
        state = launched(CognitiveState())
        state.succeed_action("input_text", "3")
        state.succeed_action("submit", "3")

        assert build(plan_for(REQUEST), state).current is None

    def test_current_step_returns_the_step_not_the_node(self):
        # `conversation` and the prompt want the step; the node's state is
        # the graph's business. Keeping the old signature means phase 5's
        # callers did not have to learn a new type.
        step = current_step(plan_for(REQUEST), launched(CognitiveState()))

        assert isinstance(step, PlanStep)

    def test_an_empty_plan_has_no_current_step(self):
        assert current_step(Plan(), CognitiveState()) is None

    def test_asking_does_not_spend_an_attempt(self):
        state = CognitiveState()
        before = state.revision

        build(plan_for(PICK), state)
        current_step(plan_for(PICK), state)
        render_plan(plan_for(PICK), state)

        assert state.revision == before
        assert state.actions == ()


# ----------------------------------------------------------------------
# What the model is shown
# ----------------------------------------------------------------------

class TestRenderingTheStates:

    def test_a_skipped_step_says_so(self):
        state = CognitiveState()
        state.observe(application=YOUTUBE)

        assert "1. Open YouTube  [SKIPPED]" in render_plan(plan_for(REQUEST), state)

    def test_a_failed_step_says_why(self):
        lines = render_plan(plan_for(REQUEST), exhaust(CognitiveState()))

        assert lines[0] == "1. Open YouTube  [FAILED: no such app]"

    def test_a_blocked_step_says_so(self):
        lines = render_plan(plan_for(REQUEST), exhaust(CognitiveState()))

        assert lines[1].endswith("[BLOCKED]")

    def test_a_stuck_plan_has_no_next_step_marked(self):
        lines = render_plan(plan_for(REQUEST), exhaust(CognitiveState()))

        assert not any("<- NOW" in line for line in lines)

    def test_a_recovering_step_is_the_one_to_do_now(self):
        state = launched(CognitiveState())
        state.enter_recovery("open_app", YOUTUBE)

        lines = render_plan(plan_for(REQUEST), state)

        assert lines[0] == "1. Open YouTube  <- NOW"

    def test_exactly_one_step_is_marked_now(self):
        state = CognitiveState()
        state.observe(application=YOUTUBE)

        lines = render_plan(plan_for(PICK), state)

        assert sum("<- NOW" in line for line in lines) == 1

    def test_a_failure_with_no_reason_still_renders(self):
        state = CognitiveState()

        while state.should_retry("open_app", YOUTUBE):
            state.begin_action("open_app", YOUTUBE)
            state.fail_action("open_app", YOUTUBE, "")

        assert render_plan(plan_for(REQUEST), state)[0] == "1. Open YouTube  [FAILED]"


# ----------------------------------------------------------------------
# It is a projection, not a store
# ----------------------------------------------------------------------

class TestItIsAProjection:

    def test_build_returns_a_graph_of_nodes(self):
        graph = build(plan_for(REQUEST), CognitiveState())

        assert isinstance(graph, TaskGraph)
        assert all(isinstance(node, TaskNode) for node in graph.nodes)

    def test_a_node_is_frozen(self):
        node = build(plan_for(REQUEST), CognitiveState()).nodes[0]

        with pytest.raises(Exception):
            node.state = NodeState.SUCCESS

    def test_a_graph_is_frozen(self):
        graph = build(plan_for(REQUEST), CognitiveState())

        with pytest.raises(Exception):
            graph.nodes = ()

    def test_building_twice_from_the_same_facts_agrees(self):
        state = launched(CognitiveState())

        assert build(plan_for(REQUEST), state) == build(plan_for(REQUEST), state)

    def test_the_graph_keeps_the_goal_verbatim(self):
        assert build(plan_for(REQUEST), CognitiveState()).goal == REQUEST

    def test_build_takes_only_a_plan_and_a_state(self):
        # No provider, no clock, no configuration. Section 7: swapping
        # models must not change behaviour, so nothing about which model
        # answered may reach this.
        assert list(inspect.signature(build).parameters) == ["plan", "state"]

    def test_a_selection_plan_gets_a_node_per_step(self):
        graph = build(plan_for(PICK), CognitiveState())

        assert len(graph.nodes) == len(plan_for(PICK).steps) == 6

    @pytest.mark.parametrize("wanted", list(NodeState))
    def test_every_state_is_reachable_by_some_arrangement(self, wanted):
        # A state nothing can produce is vocabulary, not behaviour. Each
        # of the seven is produced by arranging facts the way the device
        # or a recovery would.
        assert wanted in reachable_states()


def reachable_states() -> set:
    plan = plan_for(REQUEST)
    found = set()

    found.update(states(build(plan, CognitiveState())))

    foreground = CognitiveState()
    foreground.observe(application=YOUTUBE)
    found.update(states(build(plan, foreground)))

    running = CognitiveState()
    running.begin_action("open_app", YOUTUBE)
    found.update(states(build(plan, running)))

    found.update(states(build(plan, launched(CognitiveState()))))
    found.update(states(build(plan, exhaust(CognitiveState()))))

    recovering = launched(CognitiveState())
    recovering.enter_recovery("open_app", YOUTUBE)
    found.update(states(build(plan, recovering)))

    return found


# ----------------------------------------------------------------------
# Position and rendering over planner-produced plans
#
# These moved here with `current_step` and `render_plan` when phase 6 took
# ownership of both. They are the three-state view - done, not done, which
# one is next - and every one of them still has to hold now that seven
# states are computed instead of two.
# ----------------------------------------------------------------------

class TestPosition:
    """
    Position is read from the cognitive state, never from a step counter.

    A counter would be a second record of progress, and the two would
    disagree the first time an action was retried - the class of bug that
    produced open_app open_app open_app after a successful launch.
    """

    def test_a_fresh_task_is_on_the_first_step(self):
        plan = plan_for("open YouTube and search Minecraft")

        assert current_step(plan, CognitiveState()).kind is StepKind.OPEN_APP

    def test_a_launched_app_moves_to_the_search_field(self):
        state = CognitiveState()
        state.succeed_action("open_app", YOUTUBE)

        plan = plan_for("open YouTube and search Minecraft")
        assert current_step(plan, state).kind is StepKind.FOCUS_SEARCH

    def test_typing_satisfies_focusing_too(self):
        # Text cannot be entered into a field that was never focused, so a
        # succeeded input_text is proof of both. Insisting on a separate
        # focus record would strand the plan on a step already passed, and
        # the model would be told to focus a box it had already typed into.
        state = CognitiveState()
        state.succeed_action("open_app", YOUTUBE)
        state.succeed_action("input_text", "search_box")

        plan = plan_for("open YouTube and search Minecraft")
        assert current_step(plan, state).kind is StepKind.SUBMIT_SEARCH

    def test_a_verified_submit_finishes_a_search_only_plan(self):
        # AWAIT_RESULTS shares its evidence with SUBMIT_SEARCH, and that is
        # not a shortcut: the device verifies a submit by polling the screen
        # until the content changes, so a submit that succeeded IS results
        # having rendered. Inventing an `await_results` action to satisfy
        # the step separately would mean inventing a kind nothing emits,
        # and the step would then never be passed in production.
        state = searched(CognitiveState())

        plan = plan_for("open YouTube and search Minecraft")
        assert current_step(plan, state) is None

    def test_the_model_is_told_it_is_finished_rather_than_left_guessing(self):
        # The payoff of the line above. Every step DONE and no NOW is the
        # clearest possible cue for rule 10 - stop and complete - which is
        # the behaviour the auto-complete heuristics on the device are
        # currently left to infer from keywords alone.
        state = searched(CognitiveState())

        lines = render_plan(plan_for("open YouTube and search Minecraft"), state)
        assert all("DONE" in line for line in lines)

    def test_position_ignores_node_ids(self):
        # The planner cannot know a node id in advance, so satisfaction of
        # the middle steps is by action kind alone. A planner that guessed
        # node ids would be inventing device state.
        state = CognitiveState()
        state.succeed_action("open_app", YOUTUBE)
        state.succeed_action("input_text", "some_box_we_could_not_predict")

        plan = plan_for("open YouTube and search Minecraft")
        assert current_step(plan, state).kind is StepKind.SUBMIT_SEARCH

    def test_a_failed_action_does_not_advance_the_plan(self):
        state = CognitiveState()
        state.fail_action("open_app", YOUTUBE, "activity not found")

        plan = plan_for("open YouTube and search Minecraft")
        assert current_step(plan, state).kind is StepKind.OPEN_APP

    def test_the_wrong_app_does_not_count_as_the_launch(self):
        # open_app is the one step whose target the planner does know, so
        # it is matched exactly. Chrome coming up does not satisfy "open
        # YouTube", and reporting otherwise would advance the plan past a
        # step that never happened.
        state = CognitiveState()
        state.succeed_action("open_app", "com.android.chrome")

        plan = plan_for("open YouTube and search Minecraft")
        assert current_step(plan, state).kind is StepKind.OPEN_APP

    def test_a_package_that_contains_the_app_name_counts(self):
        # The plan step holds a display name ("YouTube") and the device
        # reports a package ("com.google.android.youtube"). Matching has to
        # bridge that, or the launch step could never be satisfied.
        state = CognitiveState()
        state.succeed_action("open_app", YOUTUBE)

        plan = plan_for("open YouTube")
        assert current_step(plan, state) is None

    def test_a_selection_plan_is_not_finished_by_the_search(self):
        # The regression this guards: if reaching results ended a "search
        # and pick the first result" task, the plan would tell the model to
        # stop one step early and rule 5 would be violated by structure.
        state = searched(CognitiveState())

        plan = plan_for("open YouTube and search lofi and pick the first result")
        assert current_step(plan, state).kind is StepKind.SELECT_RESULT

    def test_a_tap_completes_a_selection_plan(self):
        state = searched(CognitiveState())
        state.succeed_action("click", "result_0")

        plan = plan_for("open YouTube and search lofi and pick the first result")
        assert current_step(plan, state) is None

    def test_a_tap_is_not_how_a_field_gets_focused(self):
        # Deliberate: `click` satisfies SELECT_RESULT and nothing else. If a
        # tap also counted as focusing, then tapping the search box would
        # satisfy the last step of a selection plan, and a task would report
        # itself finished having only opened the keyboard. The prompt offers
        # a dedicated `focus` action, and typing proves focus anyway, so
        # nothing is lost by refusing the ambiguous reading.
        state = CognitiveState()
        state.succeed_action("open_app", YOUTUBE)
        state.succeed_action("click", "search_box")

        plan = plan_for("open YouTube and search Minecraft")
        assert current_step(plan, state).kind is StepKind.FOCUS_SEARCH

    def test_an_empty_plan_has_no_current_step(self):
        assert current_step(plan_for("tell me a joke"), CognitiveState()) is None

    def test_position_does_not_mutate_the_state(self):
        # Reading where we are must not look like doing something. If
        # `current_step` began an action, asking twice would spend a retry.
        state = CognitiveState()
        state.succeed_action("open_app", YOUTUBE)
        before = state.revision

        plan = plan_for("open YouTube and search Minecraft")
        current_step(plan, state)
        current_step(plan, state)

        assert state.revision == before


# ======================================================================
# 4. What the model is shown
# ======================================================================

class TestRendering:

    def test_an_empty_plan_renders_nothing(self):
        # No steps means no PLAN section, so the prompt is byte for byte
        # what it is today. That is what makes an unrecognised request a
        # no-op rather than a regression.
        assert render_plan(plan_for("tell me a joke"), CognitiveState()) == []

    def test_every_step_is_listed(self):
        lines = render_plan(
            plan_for("open YouTube and search Minecraft"), CognitiveState()
        )
        assert len(lines) == 5

    def test_finished_steps_are_marked_finished(self):
        state = CognitiveState()
        state.succeed_action("open_app", YOUTUBE)

        lines = render_plan(plan_for("open YouTube and search Minecraft"), state)
        assert "DONE" in lines[0]
        assert "DONE" not in lines[1]

    def test_exactly_one_step_is_marked_current(self):
        # The whole point of the phase. The model is told where it is
        # instead of being asked to work it out from the action history on
        # every one of ten steps.
        state = CognitiveState()
        state.succeed_action("open_app", YOUTUBE)

        lines = render_plan(plan_for("open YouTube and search Minecraft"), state)
        assert sum(1 for line in lines if "NOW" in line) == 1
        assert "NOW" in lines[1]

    def test_the_query_appears_in_the_step_that_types_it(self):
        lines = render_plan(
            plan_for("open YouTube and search Minecraft"), CognitiveState()
        )
        typing = next(line for line in lines if "Minecraft" in line)

        # And the verb must not have come along for the ride, because this
        # line is the model's most concrete instruction about what to type.
        assert "search Minecraft" not in typing
        assert "search for" not in typing

    def test_rendering_survives_a_finished_plan(self):
        state = searched(CognitiveState())

        lines = render_plan(plan_for("open YouTube and search Minecraft"), state)
        assert all("NOW" not in line for line in lines)
        assert all("DONE" in line for line in lines)

    def test_the_lines_are_numbered_in_order(self):
        lines = render_plan(
            plan_for("open YouTube and search Minecraft"), CognitiveState()
        )
        for index, line in enumerate(lines, start=1):
            assert line.lstrip().startswith(str(index))

    def test_rendering_does_not_mutate_the_state(self):
        state = CognitiveState()
        state.succeed_action("open_app", YOUTUBE)
        before = state.revision

        render_plan(plan_for("open YouTube and search Minecraft"), state)

        assert state.revision == before
