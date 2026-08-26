"""
When to try again, and when finished work stops being finished.

Everything up to here records success. `absorb` writes only the verified
branch, `is_done` asks what succeeded, and of the seven node states
`brain/task_graph.py` can compute, three had no producer at all. A launch
that failed twice and a launch nobody has tried arrived at the server
looking identical, so the only thing that ever said "stop trying" was the
device, in prose, to the model.

Two mandates land here.

*Section 12.* "Never blindly repeat the same action forever. Add bounded
retry policies." The counting already exists - `CognitiveState` knows how
many attempts an action has had and already refuses to retry one that
worked. What did not exist is the number. `should_retry` takes `limit` as
an argument on purpose, because "the right number depends on the action",
and every caller so far took the default - which makes the bound a default
parameter rather than a policy. This module is where the number lives.

*Section 11.* Verification "must not rely only on: 'the command executed
without throwing'", and a postcondition is a condition rather than an
event. `expected package == foreground package` is checked once at launch
and then believed forever. When the app leaves the foreground the node
still reads SUCCESS, the plan still reads DONE, and the task can never
finish. `invalidated` is that condition re-asked on every tick.

Three deliberate limits.

*Only launches are re-checked.* The same evidence restriction that governs
SKIPPED in `task_graph`: a focused search field or rendered results would
have to be read off `focus.screen`, which arrives permanently empty because
the device never fills in the activity name. A postcondition asserted on
absent evidence is worse than one not asserted at all.

*The bound is not raised above what the device will actually do.* The
service refuses to execute an action whose failure count has reached
`MAX_ACTION_ATTEMPTS`, so a server limit above that floor would be a policy
nothing enforces - it would read as permission while the phone declined.
Below the floor is enforceable, because the server declining to ask is the
whole mechanism. `tests/test_agent_protocol.py` pins the two together.

*No verification is rebuilt here.* The device already does the real thing,
per kind, with bounded polls - `waitForForegroundPackage` for a launch,
`waitForContentChange` for a submit, a fingerprint comparison otherwise.
Reimplementing that on the server would be a second verification system
whose disagreements with the first would be invisible. What was missing was
never the checking; it was that the *result* of the check never crossed the
wire in a form the server could read. That gap is closed in
`brain/agent_mode.py`, where the wire is already parsed.
"""

from brain.planner import Plan, PlanStep, StepKind, same_app
from core.cognitive import ActionRecord, ActionState, CognitiveState


# How many attempts one action gets, when nothing more specific applies.
#
# Two, matching the floor the device already enforces: the service stops
# executing an action once its failure count reaches that number, so this
# is the largest value that means anything. It is not a coincidence to be
# maintained by hope - `test_agent_protocol.py` reads the Kotlin constant
# and fails if the two part company.
DEFAULT_RETRY_LIMIT = 2

# Kinds whose number differs, deliberately empty.
#
# The seam exists because `should_retry`'s own docstring names the case it
# is for - "relaunching an app that may still be starting is cheap,
# re-sending a payment is not" - and phases 18 and 22 bring actions that
# are not cheap: a shell command, a file write, a tool with a side effect
# outside the phone. None of those exist yet.
#
# It is empty rather than pre-filled with plausible numbers because every
# entry has to be justified against something. Today every action the
# device can perform is a UI gesture whose retry costs a second and whose
# ceiling is set by the service, so inventing variety would be inventing
# behaviour: numbers that look like policy, derived from nothing, that a
# later reader would reasonably assume someone had measured.
RETRY_LIMITS: dict[str, int] = {}


# What the device's two unsuccessful verdicts mean, in words that reach the
# model through the rendered plan.
#
# The vocabulary is the device's own `ExecutionResult` names rather than a
# taxonomy invented here, which is the point: section 11's distinction is
# between an action that could not be performed and one that was performed
# without its postcondition being observed, and those are exactly the two
# cases the service already tells apart. A single "it failed" would throw
# away the more useful half - a click that landed on nothing needs a
# different target, while a submit whose results never rendered may only
# need another look.
FAILURE_DETAIL = {
    "FAILED": "could not execute",
    "UNVERIFIED": "executed but not verified",
}

UNKNOWN_FAILURE = "did not complete"


def limit_for(kind: str) -> int:
    """
    How many attempts this kind of action gets. Total, never zero.

    Total because the device can send a kind this table has never heard of
    - a new action, a renamed one, a typo in a model's JSON - and "unknown
    means unlimited" is precisely the forever-repeat section 12 forbids.
    The fallback has to be a bound, not an exemption.

    Never zero because a limit of zero would refuse the first attempt,
    which is not a retry policy but a block. Blocking an action the prompt
    openly offers would be exactly the arbitrary restriction section 2
    rules out - a safety decision belongs in `SafetyGuard`, where it is
    visible, not smuggled in as a retry count of nothing.
    """

    return max(1, int(RETRY_LIMITS.get(str(kind), DEFAULT_RETRY_LIMIT)))


def detail_for(verdict: str) -> str:
    """The human-readable reason behind one of the device's verdicts."""

    return FAILURE_DETAIL.get(str(verdict).upper(), UNKNOWN_FAILURE)


def may_retry(state: CognitiveState, kind: str, target: str = "") -> bool:
    """
    Whether this action may be attempted again.

    A thin question with one job: put `limit_for`'s number in front of the
    counting that already exists, so there is one retry policy rather than
    a bound per caller. `should_retry` still owns the arithmetic, including
    the part that matters most - an action that succeeded is never retried,
    whatever the count says.
    """

    return state.should_retry(kind, target, limit_for(kind))


def _launch_record(step: PlanStep, state: CognitiveState) -> ActionRecord | None:
    """The succeeded launch that speaks for this step, if there is one."""

    if step.kind is not StepKind.OPEN_APP:
        return None

    for record in state.succeeded:
        if record.kind == "open_app" and same_app(step.detail, record.target):
            return record

    return None


def invalidated(plan: Plan, state: CognitiveState) -> tuple[PlanStep, ...]:
    """
    Steps that were done and whose postcondition has since become false.

    Section 11's launch check, re-asked: expected package == foreground
    package. `absorb` records the foreground package on every tick, so this
    costs nothing and needs no new evidence.

    An empty `focus.application` returns nothing rather than everything. A
    tick that never reported a package is Aura not knowing what is on
    screen, and reading that as "the app is gone" would invalidate every
    launch on the first tick of every task - the same reason `Focus`
    defaults to empty instead of to a guess.
    """

    foreground = state.focus.application

    if not foreground:
        return ()

    return tuple(
        step
        for step in plan.steps
        if _launch_record(step, state) is not None
        and not same_app(step.detail, foreground)
    )


def _owns(record: ActionRecord, plan: Plan, state: CognitiveState) -> bool:
    """Whether an active recovery is one this function could have started."""

    if record.kind != "open_app":
        return False

    return any(
        step.kind is StepKind.OPEN_APP and same_app(step.detail, record.target)
        for step in plan.steps
    )


def reconcile(plan: Plan, state: CognitiveState, stuck: bool) -> bool:
    """
    Open or close recovery for this plan. True when something moved.

    Section 10 permits a completed node to run again "only when explicitly
    required by recovery", and until now nothing ever required it - so the
    exception existed with no way to reach it, and a launch that stopped
    holding was unrecoverable by construction.

    `stuck` is the caller's answer to "has this plan run out of ways
    forward", and recovery only *opens* when it is true. That gate is the
    most important line in this module, and it is there because a single
    package read cannot tell the two interesting cases apart. An app that
    was killed and an app sitting behind a permission dialog, a share
    sheet, or a sub-activity in another package all look identical: the
    foreground package is not the one the plan named. Acting on that
    directly would relaunch a perfectly healthy app, throw away a search
    already half typed, and do it again on the next tick - which is
    open_app, open_app, open_app, the exact behaviour section 10 names as
    the thing to prevent. A recovery engine that manufactures the loop it
    exists to stop is worse than none.

    Waiting until the plan is stuck costs a few actions that were going to
    fail anyway, and buys an unambiguous reading: every remaining step has
    spent its bound against an app that is not there. A transient overlay
    does not produce that. At that point there is also nothing left to
    lose - the task cannot complete - so reopening the launch is the only
    alternative to giving up, which is what recovery is for.

    Closing is not gated, because it has to happen on the tick the app
    comes back, and a plan in recovery is never stuck by construction: the
    RECOVERING node is workable, so `current` returns it.

    Two things it will not do. It will not touch a recovery scoped to
    another action, because recovery is deliberately one `(kind, target)`
    rather than a mode - a reconciler that reassigned it would decide what
    everything else is allowed to repeat. And it will not open recovery on
    an action already at its bound, which would otherwise be section 12's
    forever-loop wearing a different name: `_state_of` checks RECOVERING
    before FAILED, so an unbounded recovery would keep a node workable
    forever while an app was repeatedly killed.

    The bound here is the attempt count directly rather than `may_retry`,
    and the difference is the whole reason recovery exists. `may_retry`
    refuses a succeeded action outright - correctly, since that refusal is
    what stops the open_app loop - but the action being recovered is
    succeeded by definition. Asking `may_retry` would mean recovery could
    never start at all.
    """

    active = state.recovering_from
    stale = invalidated(plan, state)
    stale_keys = {
        record.key
        for record in (_launch_record(step, state) for step in stale)
        if record is not None
    }

    if active is not None:
        if not _owns(active, plan, state):
            return False

        if active.key in stale_keys:
            return False

        state.leave_recovery()

        return True

    if not stuck:
        return False

    for step in stale:
        record = _launch_record(step, state)

        if record is None:
            continue

        if state.attempts_for(record.kind, record.target) >= limit_for(record.kind):
            continue

        # One action, the first that needs it. A second would overwrite
        # the first and leave the plan claiming to recover something it
        # had already stopped recovering.
        state.enter_recovery(record.kind, record.target)

        return True

    return False


def absorbed_failure(
    state: CognitiveState, kind: str, target: str, verdict: str, count: int
) -> bool:
    """
    Bring one action's attempt count up to what the device reported.

    Lives here rather than in `agent_mode` because the clamp is a policy
    decision: past the bound nothing about behaviour changes, so a count of
    nine thousand and a count of two mean the same thing, and clamping is
    what stops an absurd or malformed number from spinning the loop.

    The loop is bounded by arithmetic rather than by a condition on the
    state, and that is not stylistic. `begin_action` hands back a finished
    record without incrementing anything when the action already succeeded,
    so `while attempts < count: begin; fail` would never advance and never
    end. The `has_succeeded` guard below is the same hazard's other half.
    """

    if state.has_succeeded(kind, target):
        return False

    wanted = min(max(0, int(count)), limit_for(kind))
    already = state.attempts_for(kind, target)
    detail = detail_for(verdict)

    if already >= wanted:
        # Nothing new reported. The usual case by a wide margin: every tick
        # re-sends the whole list, so most lines have already been read.
        # Re-recording would inflate the count until the bound was spent on
        # repetition of the report alone.
        #
        # The verdict can still change without the count moving - a click
        # that was executed-but-unverified can become un-executable on the
        # next attempt - and that is worth following, because the reason is
        # what reaches the model through the rendered plan.
        record = state.action_for(kind, target)

        if record is None or record.state is not ActionState.FAILED:
            return False

        if record.detail == detail:
            return False

        state.fail_action(kind, target, detail)

        return True

    for _ in range(wanted - already):
        state.begin_action(kind, target)
        state.fail_action(kind, target, detail)

    return True
