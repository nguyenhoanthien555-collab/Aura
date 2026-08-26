"""
Each step of a plan, in one of section 10's seven states.

The plan from `brain/planner.py` answers "what has to happen". It
deliberately stops there: a step is done or it is not. That is enough to
mark progress in a prompt, and not enough to say anything useful when
something goes wrong. A launch that failed twice and a launch nobody has
tried yet are both "not done", so both render identically, and the model
is invited to keep trying the first one forever.

This module is the missing distinction. `build` assigns every step one of
PENDING, RUNNING, SUCCESS, FAILED, SKIPPED, BLOCKED or RECOVERING, and
`current` names the one node worth working on now.

*Nothing here is stored.* Every state is derived, on demand, from the
`CognitiveState` the device's ticks have been writing into and from the
plan itself. A node state written down somewhere would be a second record
of progress, and section 8 is explicit that this state must not be
duplicated across modules - the two copies would disagree the first time
an action was retried, and then there would be no way to tell which was
lying. So `set_plan` and `enter_node` are left exactly as they are: a flat
tuple of step kinds and the name of the current one. Neither needs to
grow a per-node field, because the states are computed from facts already
recorded rather than remembered alongside them.

Precedence between the seven, and why:

RECOVERING beats SUCCESS. That is the whole content of section 10's
exception - a completed node runs again only when recovery explicitly
requires it - and if SUCCESS won, `enter_recovery` could never reopen
anything and the exception would be decorative.

SUCCESS beats SKIPPED. After a real launch both facts hold at once: the
action succeeded, and the app is in the foreground. Reporting SKIPPED
would say we never did it.

A node's own FAILED beats an inherited BLOCKED. "This exact thing broke,
and here is what the device said" is more use to the model than "something
upstream broke", when both are true.

Two states are worth singling out.

SKIPPED is not a decision to cut a corner; it is "the postcondition
already holds". Today exactly one step kind can observe that: launching an
app that is already in the foreground, because `absorb` records the
foreground package on every tick. The others cannot, and must not pretend
to - a focused search field and rendered results would be read off
`focus.screen`, which arrives permanently empty because the device never
fills in the activity name. Claiming a step was skipped on evidence that
does not exist would advance a plan past work nobody did, which is the
failure this whole area exists to stop. SKIPPED is also the one state that
changes behaviour on hardware today: without it, asking to open an app
that is already open still produces an open_app.

BLOCKED is about a node's successors, never itself. A step whose own
action is exhausted is FAILED; the steps after it are BLOCKED, because
telling the model to type into the search box of an app that never opened
is worse than telling it nothing. `current` therefore returns None for a
blocked plan, and `is_stuck` exists so that "nothing to do" can be told
apart from "finished" - a distinction the caller cannot make from a null
current node alone.

What is *not* here: an edge list. A plan produced by `plan_for` is a
chain, so "an earlier node" means exactly the nodes before this one, and a
`depends_on` field that always held `(index - 1,)` would be a structure
pretending to carry information it does not. When plans branch, that field
is where the dependency goes and `_blocking` is the only function that has
to change.

Honest about reach: of the seven states, PENDING, SUCCESS and SKIPPED
occur in production today. RUNNING needs `begin_action`, and FAILED,
BLOCKED and RECOVERING need `fail_action` and `enter_recovery`, none of
which the tick calls yet - `absorb` records only the verified branch,
because the device's error strings are free prose in five shapes and
deriving a `(kind, target)` from them would be inventing a format rather
than reading one. Phase 7 owns those producers. The projection is complete
and tested for all seven; three of its inputs arrive later.
"""

from dataclasses import dataclass, field
from enum import Enum

from brain.planner import (
    SATISFIED_BY,
    Plan,
    PlanStep,
    StepKind,
    describe,
    is_done,
    same_app,
)
from brain.recovery import may_retry
from core.cognitive import ActionRecord, ActionState, CognitiveState


class NodeState(str, Enum):
    """
    Where one node of a plan stands.

    Plain lowercase strings, like `ActionState`, `TaskClass` and
    `AuraState`, so a state can be logged or rendered without a
    conversion table.

    Seven, because section 10 names seven. The spelling follows the
    mandate too: SUCCESS here where `ActionState` says SUCCEEDED. They are
    not the same vocabulary and should not look like it - an action is one
    attempt at a device operation, keyed by what it targeted; a node is a
    unit of plan, and can be BLOCKED or SKIPPED, neither of which any
    single attempt could ever be.
    """

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    RECOVERING = "recovering"


# The states in which there is nothing left to do, for good reasons.
_SETTLED = (NodeState.SUCCESS, NodeState.SKIPPED)

# The states in which there is nothing left to do, for bad ones.
_STOPPED = (NodeState.FAILED, NodeState.BLOCKED)


@dataclass(frozen=True)
class TaskNode:
    """
    One step, plus what has become of it.

    `attempts` and `detail` are copied off the action record that would
    satisfy this step rather than counted here. A node that kept its own
    tally would be a second retry accounting, and two that disagree is
    already a live problem between Python's `(kind, target)` key and the
    device's `"${action.action}:${action.nodeId}"`, which collides for
    open_app.

    Frozen because it is a reading, not a record. Mutating a node would
    change a derivation without changing what it was derived from.
    """

    step: PlanStep
    state: NodeState = NodeState.PENDING
    attempts: int = 0
    detail: str = ""


@dataclass(frozen=True)
class TaskGraph:
    """
    A whole plan, read against what has happened to it.

    `goal` is carried through from the plan, which carries it verbatim
    from the owner, so a wrong parse still leaves the original sentence
    reachable.
    """

    goal: str = ""
    nodes: tuple[TaskNode, ...] = field(default_factory=tuple)

    def __bool__(self) -> bool:
        return bool(self.nodes)

    @property
    def current(self) -> TaskNode | None:
        """
        The one node worth working on, or None if there is not one.

        None has two meanings and the caller usually needs to know which:
        `is_finished` and `is_stuck` separate them.
        """

        for node in self.nodes:
            if node.state not in _SETTLED and node.state not in _STOPPED:
                return node

        return None

    @property
    def is_finished(self) -> bool:
        """Every node settled. An empty graph is not finished; it is empty."""

        return bool(self.nodes) and all(
            node.state in _SETTLED for node in self.nodes
        )

    @property
    def is_stuck(self) -> bool:
        """Nothing to do, and not because the work is done."""

        return bool(self.nodes) and self.current is None and not self.is_finished


# ----------------------------------------------------------------------
# Deriving the states
# ----------------------------------------------------------------------

def _satisfies(record: ActionRecord, step: PlanStep) -> bool:
    """Whether this action record is the one that speaks for this step."""

    if step.kind is StepKind.OPEN_APP:
        return record.kind == "open_app" and same_app(step.detail, record.target)

    return record.kind in SATISFIED_BY[step.kind]


def _record_for(step: PlanStep, state: CognitiveState) -> ActionRecord | None:
    for record in state.actions:
        if _satisfies(record, step):
            return record

    return None


def _recovering(step: PlanStep, state: CognitiveState) -> bool:
    record = state.recovering_from

    return record is not None and _satisfies(record, step)


def _already_holds(step: PlanStep, state: CognitiveState) -> bool:
    """
    Whether this step's goal is true without anyone having done it.

    Launches only - see the module docstring for why no other step kind is
    allowed to claim this.
    """

    if step.kind is not StepKind.OPEN_APP:
        return False

    return same_app(step.detail, state.focus.application)


def _state_of(
    step: PlanStep,
    state: CognitiveState,
    record: ActionRecord | None,
    blocked: bool,
) -> NodeState:
    if _recovering(step, state):
        return NodeState.RECOVERING

    if is_done(step, state):
        return NodeState.SUCCESS

    if _already_holds(step, state):
        return NodeState.SKIPPED

    if record is not None:
        if record.state is ActionState.PENDING:
            return NodeState.RUNNING

        # Exhausted, not merely unsuccessful. Asked rather than compared
        # here, so that one policy answers it everywhere: `may_retry` holds
        # the bound, `CognitiveState` holds the count, and this function
        # holds neither. A limit spelled out at this call site would be a
        # third opinion about a number two other places already have.
        if record.state is ActionState.FAILED and not may_retry(
            state, record.kind, record.target
        ):
            return NodeState.FAILED

    return NodeState.BLOCKED if blocked else NodeState.PENDING


def build(plan: Plan, state: CognitiveState) -> TaskGraph:
    """
    Read a plan against a cognitive state.

    Pure, and takes nothing else: no provider, no clock, no configuration.
    Section 7 requires that swapping models leaves behaviour unchanged, so
    nothing about which model answered may reach a derivation the model is
    then steered by.

    Reads the state and never writes to it. Asking where a task stands
    must not look like doing something, or asking twice would spend a
    retry.
    """

    nodes: list[TaskNode] = []
    blocked = False

    for step in plan.steps:
        record = _record_for(step, state)
        node_state = _state_of(step, state, record, blocked)

        nodes.append(TaskNode(
            step=step,
            state=node_state,
            attempts=record.attempts if record else 0,
            detail="" if node_state in _SETTLED else (
                record.detail if record else ""
            ),
        ))

        blocked = blocked or node_state in _STOPPED

    return TaskGraph(goal=plan.goal, nodes=tuple(nodes))


def current_step(plan: Plan, state: CognitiveState) -> PlanStep | None:
    """
    The step to take now, or None if there is not one.

    Returns the step rather than the node so that callers wanting only
    "what next" - the prompt, and `enter_node` - do not have to know this
    module's vocabulary. Anything that needs the state asks `build`.
    """

    node = build(plan, state).current

    return node.step if node else None


# ----------------------------------------------------------------------
# Telling the model
# ----------------------------------------------------------------------

# What each state looks like in a prompt. FAILED is absent because its
# line carries the device's reason, and RECOVERING and RUNNING appear here
# only for the case where they are somehow not the current node - normally
# both are, and both render as `<- NOW`.
_MARKS = {
    NodeState.PENDING: "",
    NodeState.SUCCESS: "  [DONE]",
    NodeState.SKIPPED: "  [SKIPPED]",
    NodeState.BLOCKED: "  [BLOCKED]",
    NodeState.RUNNING: "  [RUNNING]",
    NodeState.RECOVERING: "  [RECOVERING]",
}


def _mark(node: TaskNode, here: TaskNode | None) -> str:
    if node is here:
        return "  <- NOW"

    if node.state is NodeState.FAILED:
        return f"  [FAILED: {node.detail}]" if node.detail else "  [FAILED]"

    return _MARKS[node.state]


def render(graph: TaskGraph) -> list[str]:
    """
    The graph as prompt lines.

    An empty graph renders nothing at all rather than an empty heading, so
    a request the planner does not understand leaves the prompt exactly as
    it was before any of this existed.

    A plan whose every line reads DONE or SKIPPED, with no NOW anywhere,
    is the clearest available cue that the task is finished. A plan whose
    lines read FAILED and BLOCKED is the cue that it cannot be, which
    until now had no way of being said at all.
    """

    if not graph:
        return []

    here = graph.current

    return [
        f"{index}. {describe(node.step)}{_mark(node, here)}"
        for index, node in enumerate(graph.nodes, start=1)
    ]


def render_plan(plan: Plan, state: CognitiveState) -> list[str]:
    """The plan as prompt lines, read against what has happened to it."""

    return render(build(plan, state))
