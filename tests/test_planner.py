"""
The planner: a request becomes an ordered plan, once.

The defect this exists to fix is visible in the agent loop. Every step
sends the screen, the request, and a flat list of completed actions, and
asks for one action - so the model re-derives the whole task from scratch
each time: what was asked, how far it got, what comes next. Ten steps
means ten rediscoveries of the same five-step task, and each is a fresh
chance to answer differently. `runAgentSteps` carries nine mutable locals
across steps and not one of them looks forward.

The plan is a pure function of the request, which is what makes it safe to
recompute every tick rather than serialise: the request does not change
mid-task, so two calls cannot disagree. Position within the plan comes
from the cognitive state - what has actually succeeded - never from a step
counter, because a counter would be a second record of progress and the
two would part company the first time an action was retried.

Two boundaries, both deliberate.

*Against the device.* `shouldAutoComplete`, `isSearchTaskComplete` and
`isSelectionTaskComplete` in AuraAccessibilityService encode an implicit
version of this plan as keyword heuristics. Phase 17 named the boundary
rather than merging them: this planner and the graph own "is the goal
met", the model ends a task by saying `complete`, and the device's
heuristics own only "may I stop without asking" - an optimisation over an
obviously single-step request. The device is therefore allowed to be
wrong only in the direction that costs a round trip. It was wrong the
other way, and `test_no_multi_step_request_satisfies_the_device_early_exit`
in tests/test_agent_protocol.py is what now prevents it.

*Against later phases.* No node states beyond done/not-done (phase 6 owns
PENDING/RUNNING/BLOCKED/RECOVERING), no retry or recovery policy (phase
7), and no LLM. This planner never calls a model: a plan produced by the
thing being planned for cannot be the fixed point the model is steered
against. A request it does not recognise gets an empty plan and the loop
behaves exactly as it does today, which is the bargain `read_intent`
already makes - when unsure, the option that costs least when wrong.
"""

import pytest

from brain.planner import Plan, StepKind, plan_for, same_app, search_query
from core.cognitive import CognitiveState

YOUTUBE = "com.google.android.youtube"


def kinds(plan: Plan) -> list[StepKind]:
    return [step.kind for step in plan.steps]


def searched(state: CognitiveState, app: str = YOUTUBE) -> CognitiveState:
    """A state that has opened an app, typed, and submitted."""

    state.succeed_action("open_app", app)
    state.succeed_action("input_text", "search_box")
    state.succeed_action("submit", "")
    return state


# ======================================================================
# 1. The query, which section 23 names explicitly
# ======================================================================

class TestTheQuery:
    """
    Section 23: the query must be `Minecraft`, not `search for Minecraft`.

    Until now that rule lived in two places, neither of which is a
    function the server can call: prose in the AGENT RULES prompt, and
    `AuraActionExecutor.sanitizeSearchQuery` cleaning up whatever the
    model produced after the fact.
    """

    @pytest.mark.parametrize(
        "request_text, expected",
        [
            ("open YouTube and search Minecraft", "Minecraft"),
            ("open YouTube and search for Minecraft", "Minecraft"),
            ("search for lofi music", "lofi music"),
            ("mở YouTube và tìm Minecraft", "Minecraft"),
            ("tìm kiếm nhạc lofi", "nhạc lofi"),
        ],
    )
    def test_the_verb_never_survives_into_the_query(self, request_text, expected):
        assert search_query(request_text) == expected

    def test_case_is_preserved_in_the_query_itself(self):
        # The prefix match is case insensitive; the query is not. A search
        # box is given what the owner wrote.
        assert search_query("Open YouTube And Search Minecraft") == "Minecraft"

    def test_a_request_with_no_search_verb_has_no_query(self):
        assert search_query("open YouTube") == ""
        assert search_query("") == ""
        assert search_query(None) == ""

    def test_a_trailing_follow_up_is_not_part_of_the_query(self):
        # "and pick the first result" is a second step, not three more
        # words to type into the search box.
        assert search_query(
            "open YouTube and search for lofi music and pick the first result"
        ) == "lofi music"
        assert search_query(
            "mở YouTube và tìm nhạc lofi và chọn kết quả đầu tiên"
        ) == "nhạc lofi"


# ======================================================================
# 2. The plan
# ======================================================================

class TestPlanningASearch:

    def test_the_mandated_scenario_becomes_five_ordered_steps(self):
        plan = plan_for("open YouTube and search Minecraft")

        assert kinds(plan) == [
            StepKind.OPEN_APP,
            StepKind.FOCUS_SEARCH,
            StepKind.ENTER_QUERY,
            StepKind.SUBMIT_SEARCH,
            StepKind.AWAIT_RESULTS,
        ]

    def test_the_steps_carry_what_they_need(self):
        plan = plan_for("open YouTube and search Minecraft")
        by_kind = {step.kind: step.detail for step in plan.steps}

        assert by_kind[StepKind.OPEN_APP] == "YouTube"
        assert by_kind[StepKind.ENTER_QUERY] == "Minecraft"

    def test_the_goal_is_the_request_verbatim(self):
        # Not the parsed pieces. If the parse was wrong the model still has
        # the original sentence, which is the same reason `set_goal` keeps
        # the owner's words rather than a paraphrase.
        request = "open YouTube and search Minecraft"
        assert plan_for(request).goal == request

    def test_submit_is_its_own_step(self):
        # Section 23 lists submit as a step of the flow, and rule 3 of the
        # prompt says entering text is not search completion. A plan that
        # folded submit into "enter the query" would encode the exact
        # mistake the rule warns against - and the parser refusing submit
        # was a live defect a week ago.
        plan = plan_for("open YouTube and search Minecraft")
        assert StepKind.SUBMIT_SEARCH in kinds(plan)

    def test_opening_without_searching_is_one_step(self):
        plan = plan_for("open YouTube")
        assert kinds(plan) == [StepKind.OPEN_APP]
        assert plan.steps[0].detail == "YouTube"

    def test_searching_without_naming_an_app_skips_the_launch(self):
        plan = plan_for("search for lofi music")
        assert StepKind.OPEN_APP not in kinds(plan)
        assert kinds(plan)[0] == StepKind.FOCUS_SEARCH

    def test_picking_a_result_adds_a_step_after_the_results(self):
        plan = plan_for(
            "open YouTube and search for lofi music and pick the first result"
        )
        assert kinds(plan)[-1] == StepKind.SELECT_RESULT
        assert kinds(plan).index(StepKind.AWAIT_RESULTS) == len(kinds(plan)) - 2

    def test_a_search_only_request_does_not_get_a_select_step(self):
        # Rule 4: if the owner only asked to search, stop when results are
        # visible. A plan ending in "select" would authorise in structure
        # the click that rule forbids in prose.
        plan = plan_for("open YouTube and search Minecraft")
        assert StepKind.SELECT_RESULT not in kinds(plan)

    def test_the_non_ad_request_is_still_one_select_step(self):
        # *Which* result to avoid is the selection rules' business, not the
        # plan's shape. The plan says "select one"; rule 8 says not an ad.
        # Encoding ad-avoidance as a step would put the same rule in two
        # places with no way to keep them agreeing.
        plan = plan_for(
            "open YouTube and search for lofi music and pick the first non-ad result"
        )
        assert kinds(plan)[-1] == StepKind.SELECT_RESULT
        assert kinds(plan).count(StepKind.SELECT_RESULT) == 1

    def test_vietnamese_plans_identically(self):
        # Section 13's default language. The plan is structure, and
        # structure is not language specific.
        assert kinds(plan_for("mở YouTube và tìm Minecraft")) == kinds(
            plan_for("open YouTube and search Minecraft")
        )

    def test_the_app_name_is_not_resolved_to_a_package(self):
        # The planner has no package database and inventing one would be
        # inventing device state. `open_app` already maps a name to a
        # package on the device, where the package manager actually is.
        assert plan_for("open YouTube").steps[0].detail == "YouTube"


class TestWhatIsDeliberatelyNotPlanned:

    @pytest.mark.parametrize(
        "request_text",
        [
            "what's the weather like",
            "kể chuyện cười đi",
            "",
            "   ",
            None,
        ],
    )
    def test_an_unrecognised_request_gets_no_plan(self, request_text):
        # Not a guess. An invented plan is worse than none: it would tell
        # the model to open an app nobody named. With no steps the prompt
        # gets no PLAN section, so the tick is byte for byte what it is
        # today and an unrecognised request cannot be a regression.
        plan = plan_for(request_text)
        assert plan.steps == ()
        assert not plan

    def test_a_plan_with_steps_is_truthy(self):
        assert plan_for("open YouTube")

    def test_a_plan_is_immutable(self):
        # The plan is derived, never edited. Progress lives in the
        # cognitive state; a mutable plan would invite a second copy of it,
        # which is what section 8 forbids.
        plan = plan_for("open YouTube")
        with pytest.raises(Exception):
            plan.steps = ()
        with pytest.raises(Exception):
            plan.steps[0].detail = "Chrome"

    def test_planning_is_pure(self):
        # Same request, same plan, no hidden state between calls. This is
        # what licenses recomputing the plan every tick instead of storing
        # its structure, and it is why there is no second source of truth
        # to drift.
        first = plan_for("open YouTube and search Minecraft")
        second = plan_for("open YouTube and search Minecraft")
        assert first == second

    def test_the_planner_never_calls_a_model(self):
        # `plan_for` takes a string and nothing else. If it grew a
        # provider argument, a plan would depend on which model answered -
        # and section 7 says a model switch must not change behaviour.
        import inspect

        assert list(inspect.signature(plan_for).parameters) == ["request"]


# ======================================================================
# same_app - the launches the narrow match cannot see
#
# `same_app` is deliberately narrow: `com.google.android.youtube`
# contains "google", so a loose reading would let a YouTube launch
# satisfy "open Google Chrome", and advancing a plan past a step that
# never happened is the failure phase 5 exists to stop.
#
# The cost of that narrowness is the opposite error, and it is not
# hypothetical. When an app's display name and its package share no
# substring, a launch that genuinely succeeded is never marked done - so
# `is_done` stays False, the plan re-issues OPEN_APP, and the device does
# `open_app open_app open_app` forever. That is the exact behaviour
# section 10 names as the thing to prevent, reached from the other side:
# not a missing verification, but a verification that cannot recognise
# its own success.
# ======================================================================

class TestAppsWhoseNameIsNotInTheirPackage:

    def test_messenger_is_recognised(self):
        # com.facebook.orca. Facebook's messenger app has been "orca"
        # internally since before it was split out, and nothing in the
        # package spells "messenger".
        assert same_app("Messenger", "com.facebook.orca")

    def test_x_is_recognised(self):
        # The rename left the package alone, so the display name and the
        # package now have nothing whatsoever in common.
        assert same_app("X", "com.twitter.android")

    def test_the_old_name_still_works_too(self):
        # Aliases add readings, they never remove one. An owner who still
        # says "Twitter" is not wrong, and the heuristic already handles
        # them - this pins that the table did not displace it.
        assert same_app("Twitter", "com.twitter.android")

    def test_an_alias_does_not_make_matching_loose(self):
        # The whole risk of an alias table: one entry that is too generous
        # undoes the narrowness the function was written for. A messenger
        # alias must not let any Facebook package satisfy it.
        assert not same_app("Messenger", "com.facebook.katana")

    def test_an_alias_is_not_a_substring_rule(self):
        # "X" is one character. If it were matched as a substring it would
        # satisfy almost every package on the device; it is a whole-name
        # key and nothing else.
        assert not same_app("X", "com.android.chrome")
        assert not same_app("Xbox", "com.twitter.android")

    def test_case_and_spacing_do_not_matter(self):
        # The owner types what they type. `same_app` already squashes
        # whitespace and lowercases for its heuristic; the table is
        # consulted on the same normalised form rather than on the raw
        # string, or "messenger" would work and "Messenger " would not.
        assert same_app("  messenger ", "com.facebook.orca")
        assert same_app("MESSENGER", "com.facebook.orca")

    def test_the_narrow_match_is_still_narrow(self):
        # The regression the docstring warns about, pinned here so the
        # table cannot quietly reintroduce it.
        assert not same_app("Google Chrome", "com.google.android.youtube")

    @pytest.mark.parametrize(
        "name, package",
        [
            ("Messenger", "com.facebook.orca"),
            ("X", "com.twitter.android"),
            ("Gmail", "com.google.android.gm"),
            ("Play Store", "com.android.vending"),
            ("Phone", "com.google.android.dialer"),
            ("TikTok", "com.zhiliaoapp.musically"),
            ("Messages", "com.google.android.apps.messaging"),
        ],
    )
    def test_every_alias_resolves(self, name, package):
        # Each of these was checked against `same_app` before being added
        # and genuinely missed, so this is the list of launches that used
        # to loop rather than a list of names that happened to be handy.
        assert same_app(name, package)

    @pytest.mark.parametrize(
        "name, package",
        [
            # An alias must not answer for a different app by the same
            # publisher, which is the loose reading that would undo the
            # narrowness the function exists for.
            ("Messenger", "com.facebook.katana"),
            ("Gmail", "com.google.android.apps.messaging"),
            ("Messages", "com.google.android.gm"),
            ("Phone", "com.android.vending"),
            ("TikTok", "com.facebook.orca"),
        ],
    )
    def test_no_alias_answers_for_the_wrong_app(self, name, package):
        # The dangerous direction. A false negative here costs a repeated
        # launch; a false positive advances the plan past a step that
        # never happened, which is unrecoverable within the turn.
        assert not same_app(name, package)

    def test_a_name_with_no_alias_is_no_worse_off(self):
        # The table adds readings and removes none, so an app that was
        # already recognised still is, and one that never was still falls
        # through to the heuristic rather than being rejected outright.
        assert same_app("Zalo", "com.zing.zalo")
        assert same_app("Instagram", "com.instagram.android")
        assert not same_app("Notion", "com.some.unrelated.package")

    def test_a_messenger_launch_stops_asking_to_be_launched(self):
        """
        The defect, as behaviour rather than as a string comparison.

        A plan for "open Messenger" whose OPEN_APP step is already
        satisfied by a verified launch must report that step done. Before
        the alias table `is_done` returned False here, so the next tick
        re-issued OPEN_APP against an app that was already in the
        foreground - `open_app open_app open_app`, which is the loop
        section 10 exists to prevent.

        This goes through `is_done` and not `same_app`, because `is_done`
        is what the tick actually asks, and because the six call sites of
        `same_app` across the planner, the recovery engine and the task
        graph all inherit the fix from one place.
        """

        from brain.planner import is_done

        plan = plan_for("open Messenger")

        launch = plan.steps[0]
        assert launch.kind is StepKind.OPEN_APP

        state = CognitiveState()
        state.succeed_action("open_app", "com.facebook.orca")

        assert is_done(launch, state)

    def test_an_unlaunched_app_is_still_not_done(self):
        """
        The other direction, so the test above cannot be satisfied by a
        function that returns True. A different app in the foreground must
        not satisfy the step.
        """

        from brain.planner import is_done

        plan = plan_for("open Messenger")

        state = CognitiveState()
        state.succeed_action("open_app", "com.facebook.katana")

        assert not is_done(plan.steps[0], state)
