"""
A request, decomposed once, so the model is not asked to rediscover it.

The agent loop currently sends the screen, the request and a flat list of
completed actions, then asks for one action. Every step is therefore a
fresh derivation of the same task: what was asked, how far it got, what
comes next. Ten steps is ten rediscoveries, and each one is an
opportunity to answer differently - which is how a verified launch ends
up followed by another launch.

This module supplies the missing half. `plan_for` turns a request into an
ordered list of steps and `is_done` says whether one has already happened,
by reading what actually succeeded rather than asking the model to judge.
`brain/task_graph.py` builds on both: it gives every step one of section
10's seven node states, names the step to take now, and renders the result
into the prompt. The model stops being asked to infer progress and starts
being told it.

Three properties make this safe to recompute on every tick rather than
serialise:

*It is pure.* No clock, no configuration, no model. A plan produced by
the thing being planned for could not be the fixed point the model is
steered against, and section 7 requires that swapping models does not
change behaviour - so a plan must not depend on which model answered.

*It holds no progress.* Position comes from `CognitiveState`, which is
the one place an action's outcome is recorded. A step counter here would
be a second record of the same fact, and the two would part company the
first time an action was retried. Section 8, exactly.

*It declines to guess.* An unrecognised request yields an empty plan, the
prompt gains no section, and the tick is byte for byte what it is today.
An invented plan would be worse than none: it would name an app the
owner never mentioned.

How this relates to the device's completion heuristics, settled in
phase 17: this module and `task_graph.build` own "is the goal met", and
the model ends a task by saying `complete`. `shouldAutoComplete`,
`isSearchTaskComplete` and `isSelectionTaskComplete` on the device own
something narrower - whether the loop may stop *without asking*, which
is a latency optimisation over an obviously single-step request. So the
device is allowed to be wrong only in the direction that costs a round
trip, never in the direction that ends a task early, and
`test_no_multi_step_request_satisfies_the_device_early_exit` holds it
to that.
"""

from dataclasses import dataclass, field
from enum import Enum

from core.cognitive import CognitiveState

# Longest first, so "search for lofi" is not read as the verb "search"
# followed by a query beginning "for", and "tim kiem" beats "tim".
#
# This vocabulary is shared with `AuraActionExecutor.sanitizeSearchQuery`
# on the device, which strips the same words off whatever the model
# produced. Two lists in two languages meaning the same thing is how the
# `submit` drift happened, so a cross-language test pins them together
# rather than a comment asking the next editor to remember.
SEARCH_VERBS = ("search for", "search", "tìm kiếm", "tìm")

# Only what the mandated scenarios actually use. "launch", "start" and
# friends would be inventing vocabulary the prompt never offers and no
# test exercises, and an unrecognised launch verb costs an empty plan -
# today's behaviour - rather than a wrong one.
LAUNCH_VERBS = ("open", "mở")

# Where one clause ends and the next begins. The device asks the same
# question in `shouldAutoComplete`'s `multiStepKeywords`, and this comment
# used to claim the two sets were identical. They are not: the device also
# reads " tiếp " and " to ", and bare "," and ";" where this wants a
# following space. Every difference makes the device call a request
# multi-step where this calls it one, which is the safe direction - the
# device asks the server instead of stopping.
#
# What has to hold is containment, not equality, and it is asserted rather
# than described: `test_the_device_conjunctions_cover_the_planners`.
#
# Splitting on a conjunction is only half of "more than one thing" - a
# launch verb and a search verb together mean two jobs with no separator
# at all ("mở YouTube tìm nhạc"). The device missed that half until
# phase 17.
CONJUNCTIONS = (
    " and ",
    " then ",
    " after ",
    " và ",
    " rồi ",
    " sau đó ",
    ", ",
    "; ",
)

# Read only in a trailing clause, never in the whole request - "open" is
# both a launch verb and a selection cue, and "open YouTube" must not be
# read as asking for a result to be picked.
SELECTION_CUES = (
    "pick",
    "select",
    "play",
    "click",
    "tap",
    "open",
    "chọn",
    "phát",
    "chơi",
    "mở",
    "bấm",
)


class StepKind(str, Enum):
    """
    The kinds of step a plan can contain.

    `str`-valued for the same reason `TaskClass` is: a step kind is
    rendered into a prompt and written into `CognitiveState.enter_node`
    as a plain string, and neither should need a conversion table.

    This is not a task-graph node type. There are no PENDING / RUNNING /
    BLOCKED / RECOVERING states here - phase 6 owns those. A step is done
    or it is not, and "done" is a question only the cognitive state can
    answer.
    """

    OPEN_APP = "open_app"
    FOCUS_SEARCH = "focus_search"
    ENTER_QUERY = "enter_query"
    SUBMIT_SEARCH = "submit_search"
    AWAIT_RESULTS = "await_results"
    SELECT_RESULT = "select_result"


@dataclass(frozen=True)
class PlanStep:
    """
    One step, and the only thing about it that varies.

    `detail` is the app name for OPEN_APP, the query for ENTER_QUERY, the
    owner's own words for SELECT_RESULT, and empty for the rest. It is
    never a node id: the planner runs on the server before the screen has
    been seen, so a node id here would be a guess about device state.
    """

    kind: StepKind
    detail: str = ""


@dataclass(frozen=True)
class Plan:
    """
    An ordered decomposition of one request.

    `goal` is the request verbatim, not the parsed pieces. If the parse
    was wrong the model still has the original sentence to fall back on -
    the same reason `set_goal` stores the owner's words rather than a
    paraphrase.

    Frozen because a plan is derived, never edited. Progress belongs to
    `CognitiveState`; a mutable plan would invite a second copy of it.
    """

    goal: str = ""
    steps: tuple[PlanStep, ...] = field(default_factory=tuple)

    def __bool__(self) -> bool:
        return bool(self.steps)


# ----------------------------------------------------------------------
# Reading the request
# ----------------------------------------------------------------------

def _split_clause(text: str) -> tuple[str, str]:
    """
    The first clause, and everything after it.

    The conjunction itself belongs to neither, so it is dropped: a
    follow-up rendered as "and pick the first result" would read oddly
    after "Then:", and the word carries no information the position does
    not already give.
    """

    lower = text.lower()
    cut, width = len(text), 0

    for conjunction in CONJUNCTIONS:
        index = lower.find(conjunction)
        if index != -1 and index < cut:
            cut, width = index, len(conjunction)

    return text[:cut].strip(), text[cut + width:].strip()


def _parse(request) -> tuple[str, str, str]:
    """The app to launch, the query to type, and the trailing clause."""

    text = (request or "").strip()
    if not text:
        return ("", "", "")

    lower = text.lower()

    app = ""
    for verb in LAUNCH_VERBS:
        if lower.startswith(verb + " "):
            app, _ = _split_clause(text[len(verb) + 1:])
            break

    for verb in SEARCH_VERBS:
        index = lower.find(verb + " ")
        if index == -1:
            continue
        query, follow_up = _split_clause(text[index + len(verb) + 1:])
        return (app, query, follow_up)

    return (app, "", "")


def search_query(request) -> str:
    """
    What the owner actually wants typed into a search box.

    Section 23 names this explicitly: the query is `Minecraft`, not
    `search for Minecraft`. Until now that rule existed as prose in the
    prompt and as after-the-fact cleanup on the device; this is the first
    time the server can answer the question itself.
    """

    return _parse(request)[1]


def _wants_selection(follow_up: str) -> bool:
    lower = follow_up.lower()

    return any(cue in lower for cue in SELECTION_CUES)


def plan_for(request) -> Plan:
    """
    The steps that satisfy a request, or none if it is not understood.

    Takes a string and nothing else. No provider, no state, no clock -
    see the module docstring for why each of those absences is load
    bearing.
    """

    app, query, follow_up = _parse(request)
    steps: list[PlanStep] = []

    if app:
        steps.append(PlanStep(StepKind.OPEN_APP, app))

    if query:
        steps.extend([
            PlanStep(StepKind.FOCUS_SEARCH),
            PlanStep(StepKind.ENTER_QUERY, query),
            PlanStep(StepKind.SUBMIT_SEARCH),
            PlanStep(StepKind.AWAIT_RESULTS),
        ])

        # Only when the owner asked. Rule 4 forbids clicking a result on a
        # search-only request, and a plan whose last step was "select"
        # would authorise in structure what the rule forbids in prose.
        if _wants_selection(follow_up):
            steps.append(PlanStep(StepKind.SELECT_RESULT, follow_up))

    return Plan(goal=(request or "").strip(), steps=tuple(steps))


# ----------------------------------------------------------------------
# Reading how far we got
# ----------------------------------------------------------------------

# Which succeeded action kinds count as having done a step.
#
# FOCUS_SEARCH accepts `input_text` because text cannot be entered into a
# field that was never focused - demanding a separate focus record would
# strand the plan on a step already passed, and the model would be told
# to focus a box it had already typed into.
#
# AWAIT_RESULTS shares `submit` with SUBMIT_SEARCH, and that is not a
# shortcut. The device verifies a submit by polling the screen until its
# content changes, so a *verified* submit is precisely "results
# rendered". The alternative would be an `await_results` action, which no
# part of the system emits, leaving the final step permanently unmet.
#
# SELECT_RESULT accepts `click` and FOCUS_SEARCH deliberately does not.
# If a tap also counted as focusing, tapping the search box would satisfy
# the last step of a selection plan and the task would report itself
# finished having only opened the keyboard.
SATISFIED_BY = {
    StepKind.FOCUS_SEARCH: ("focus", "input_text"),
    StepKind.ENTER_QUERY: ("input_text",),
    StepKind.SUBMIT_SEARCH: ("submit",),
    StepKind.AWAIT_RESULTS: ("submit",),
    StepKind.SELECT_RESULT: ("click",),
}


# Display names that share no substring with their package.
#
# `same_app` below matches narrowly on purpose, and the cost of that is
# the opposite error: an app whose name appears nowhere in its package can
# never be recognised, so a launch that genuinely succeeded is never
# marked done, the plan re-issues OPEN_APP, and the device repeats
# `open_app` forever. That is the behaviour section 10 names as the thing
# to prevent, reached from the other side - not a missing verification,
# but a verification that cannot recognise its own success.
#
# Every entry is a case where no heuristic could work, verified against
# `same_app` before being added rather than guessed at: Facebook's
# messenger has been "orca" internally since before it was split out, the
# Twitter rename left the package untouched, and TikTok ships as the app
# it was built from. These are among the most common apps a Vietnamese
# owner has installed, which is why the gap mattered enough to close.
#
# Exact packages in a set, never substrings, and keyed on the whole
# normalised name. Both halves are load bearing. A substring reading of
# "x" would satisfy nearly every package on the device, and a loose
# `messenger` entry would let `com.facebook.katana` through - which is the
# dangerous direction, because a false positive advances a plan past a
# step that never happened, while a missing entry merely falls back to the
# heuristic. A name absent here is no worse off than before.
APP_ALIASES: dict[str, frozenset[str]] = {
    "messenger": frozenset({"com.facebook.orca"}),
    "x": frozenset({"com.twitter.android"}),
    "gmail": frozenset({"com.google.android.gm"}),
    "playstore": frozenset({"com.android.vending"}),
    "phone": frozenset({"com.google.android.dialer"}),
    "tiktok": frozenset({"com.zhiliaoapp.musically"}),
    "messages": frozenset({"com.google.android.apps.messaging"}),
}


def same_app(name: str, package: str) -> bool:
    """
    Whether a launched package is the app the plan named.

    The plan holds a display name because that is what the owner said and
    what `open_app` accepts; the device reports a package because that is
    what a package manager deals in. Something has to bridge the two, or
    the launch step could never be marked done.

    Matching is narrow on purpose. `com.google.android.youtube` contains
    "google", so a loose reading would let a YouTube launch satisfy "open
    Google Chrome" - and advancing a plan past a step that never happened
    is the failure this whole phase exists to stop.
    """

    squashed = "".join(name.lower().split())
    target = package.lower()
    if not squashed or not target:
        return False

    # Known aliases first: an exact package for a whole name, for the apps
    # no heuristic can reach. Cheapest check and the least ambiguous, and
    # it adds readings without removing any - an owner who still says
    # "Twitter" is served by the substring rule below exactly as before.
    if target in APP_ALIASES.get(squashed, frozenset()):
        return True

    if squashed in target.replace(".", "").replace("_", ""):
        return True

    return target.rsplit(".", 1)[-1] in name.lower().split()


def is_done(step: PlanStep, state: CognitiveState) -> bool:
    if step.kind is StepKind.OPEN_APP:
        return any(
            same_app(step.detail, record.target)
            for record in state.succeeded
            if record.kind == "open_app"
        )

    return any(
        state.has_succeeded_kind(kind) for kind in SATISFIED_BY[step.kind]
    )


# ----------------------------------------------------------------------
# Telling the model
# ----------------------------------------------------------------------

_DESCRIPTIONS = {
    StepKind.FOCUS_SEARCH: "Focus the search box",
    StepKind.SUBMIT_SEARCH: "Submit the search",
    StepKind.AWAIT_RESULTS: "Search results are on screen",
}


def describe(step: PlanStep) -> str:
    if step.kind is StepKind.OPEN_APP:
        return f"Open {step.detail}"

    if step.kind is StepKind.ENTER_QUERY:
        # "only" because this line is the model's most concrete
        # instruction about what to type, and rule 1 exists because
        # models type the verb along with the query.
        return f'Type only "{step.detail}" into the search box'

    if step.kind is StepKind.SELECT_RESULT:
        return f"Then: {step.detail}" if step.detail else "Select a result"

    return _DESCRIPTIONS[step.kind]
