"""
The tool outcome contract.

Phase 3's single source of truth for *what happened* when a tool ran, as
data rather than prose. Before this module the answer was a bool and two
free-form strings invented at each call site, which meant the reasoning
layer could not tell "the phone is not connected" from "the tool
crashed", or "we stopped waiting" from "it definitely did not happen".
Those are different sentences to a user and different decisions to a
runtime, so they are different values here.

Four vocabularies live here, and they are deliberately separate:

    ToolStatus     did it happen              (nine values, one meaning each)
    ToolError      why it did not, machine-readably
    Evidence       how we know it happened
    SideEffect     what re-running it would cost

Keeping them apart is the whole point. `ToolRisk` in tools/base.py
answers "how much damage could this do", which is a permission question;
`SideEffect` answers "is it safe to run twice", which is a retry
question. Conflating them is why a read-only file read was previously
treated as a mutation needing verification.

Two invariants are enforced mechanically rather than documented and
hoped for:

    UNKNOWN is never success. `ToolStatus.UNKNOWN.ok` is False, and
    ToolResult reconciles `ok` from `status`, so no construction can
    claim otherwise. An assistant must never tell someone a message was
    sent when the runtime only knows that it stopped waiting.

    Retryability is derived, never asserted. `retryability_of` reads
    (status, side_effect) and returns a tri-state; nothing may hand-set
    a side-effecting UNKNOWN to "safe to retry", because that is how a
    text message gets sent twice.
"""

from dataclasses import dataclass
from enum import Enum


class ToolStatus(str, Enum):
    """
    The canonical outcome of one tool execution.

    A str Enum so it serialises to its own name with no adapter - the
    same choice ToolRisk and CapabilityState already made.

    SUCCESS
        Execution completed and nothing contradicts success. Note the
        wording: this status alone is not proof of a world change - that
        is what `Evidence` is for. A SUCCESS with no evidence means "the
        call returned", which is exactly the sentence the contract
        forbids treating as verification.

    FAILED
        Execution was attempted and failed. The tool ran; it did not
        work. Whether some of it landed is what `PARTIAL` says when the
        tool can tell, and what `retryability_of` treats conservatively
        when it cannot.

    PARTIAL
        Some requested work completed, the whole operation did not. Not
        a success: a caller reading `ok` as "the operation happened"
        would be wrong, so `ok` is False and the verified portion is the
        only thing that may be described.

    DENIED
        Policy, permission, or a missing human confirmation prevented
        execution. Nothing ran. Retrying the identical call cannot help,
        because nothing about the call is what was refused.

    UNAVAILABLE
        The tool, capability, provider or device is not there right now.
        Nothing ran, and nothing about the request was wrong - which is
        what separates it from DENIED and INVALID_ARGUMENTS, and why it
        is the one non-success status that is always safe to retry.

    INVALID_ARGUMENTS
        The request failed validation before execution. Nothing ran.

    TIMEOUT
        Execution exceeded its allowed time. The critical case: this
        says we stopped waiting, NOT that the tool stopped working. The
        call may still be in flight (see tools/timeout.py - the wait is
        bounded, the tool is not), so the outcome is genuinely unknown
        and a non-idempotent retry could duplicate a real side effect.

    CANCELLED
        Execution was cancelled. Like TIMEOUT, whether anything landed
        before the cancellation is not established.

    UNKNOWN
        The outcome cannot be established safely. The honest answer when
        a tool returned something unreadable, or when its declared output
        schema rejected what it produced: the call happened, and what it
        did is not knowable from here.
    """

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"
    DENIED = "DENIED"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"

    @property
    def ok(self) -> bool:
        """
        Whether this status may be read as "the operation happened".

        Exactly one status qualifies. This property is the mechanical
        half of the contract's central rule - UNKNOWN must not become
        SUCCESS - and ToolResult derives its `ok` field from it, so the
        rule cannot be broken by forgetting it.
        """

        return self is ToolStatus.SUCCESS

    @property
    def attempted(self) -> bool:
        """
        Whether the tool's own code was entered.

        The gate statuses refuse before execution; everything else got
        as far as the call. This is what separates "no side effect is
        possible" from "a side effect may exist that nobody has
        confirmed", and `retryability_of` reads it rather than
        re-deriving the same list.
        """

        return self not in (
            ToolStatus.DENIED,
            ToolStatus.UNAVAILABLE,
            ToolStatus.INVALID_ARGUMENTS,
        )

    @property
    def established(self) -> bool:
        """
        Whether the outcome is known at all, success or failure.

        False for the three statuses that describe our own ignorance
        rather than the tool's behaviour. A runtime must not report an
        unestablished outcome as either done or not done.
        """

        return self not in (
            ToolStatus.TIMEOUT,
            ToolStatus.CANCELLED,
            ToolStatus.UNKNOWN,
        )


class SideEffect(str, Enum):
    """
    What running this tool a second time would cost.

    A retry question, not a permission question - `ToolRisk` already
    answers the latter and the two must not be merged. Reading a private
    file is SENSITIVE risk but READ_ONLY effect; pressing the phone's
    Back key is DANGEROUS risk but harmlessly repeatable.

    READ_ONLY        observes; running it twice changes nothing
    IDEMPOTENT       converges on a state; twice is the same as once
                     (navigate home, set volume to 30)
    NON_IDEMPOTENT   each run is a new effect on the world
                     (send a message, create an event, tap a button)
    UNKNOWN          undeclared

    UNKNOWN is the default, and it is treated as unsafe to repeat. That
    follows the rule tools/base.py already set for `risk`: an unlabelled
    tool gets the strictest reading that still runs, so a tool author
    cannot opt out of retry safety by omission.
    """

    READ_ONLY = "READ_ONLY"
    IDEMPOTENT = "IDEMPOTENT"
    NON_IDEMPOTENT = "NON_IDEMPOTENT"
    UNKNOWN = "UNKNOWN"

    @property
    def repeatable(self) -> bool:
        """Whether a second identical run is harmless."""

        return self in (SideEffect.READ_ONLY, SideEffect.IDEMPOTENT)

    @property
    def mutates(self) -> bool:
        """
        Whether this tool can change state outside Aura.

        UNKNOWN counts as mutating, for the same reason it counts as
        non-repeatable: the honest default for an undeclared tool is the
        one that keeps a side effect from happening twice unnoticed.
        """

        return self is not SideEffect.READ_ONLY


class Retryability(str, Enum):
    """
    Whether the runtime may re-issue this exact call.

    Tri-state on purpose. "Unsafe" and "we cannot tell" are different
    findings, and collapsing them would either hide a real duplicate-send
    risk or forbid retries that are plainly fine. `may_retry` below is
    where the safety decision lives: only SAFE is a yes, so an honest
    UNKNOWN behaves conservatively without having to lie about being
    UNSAFE.
    """

    SAFE = "SAFE"
    UNSAFE = "UNSAFE"
    UNKNOWN = "UNKNOWN"

    @property
    def may_retry(self) -> bool:
        """Only an affirmative SAFE authorises an automatic retry."""

        return self is Retryability.SAFE


def retryability_of(
    status: ToolStatus, side_effect: SideEffect
) -> Retryability:
    """
    Whether re-issuing this call is safe, derived from the two facts that
    decide it. Never hand-set.

    The reasoning, in the order the checks run:

    1. DENIED and INVALID_ARGUMENTS are UNSAFE despite nothing having
       run. Not because a repeat would damage anything - it cannot - but
       because the identical call is guaranteed to be refused again, and
       a runtime reading "safe to retry" as "worth retrying" would spin.
       Something must change first: the policy, the confirmation, or the
       arguments.

    2. UNAVAILABLE is SAFE for every side-effect class, including
       NON_IDEMPOTENT. The gate refused before the tool was entered, so
       there is no effect to duplicate, and the condition is exactly the
       kind that clears on its own when a device reconnects.

    3. Otherwise the tool was entered, so the question becomes whether a
       second run would add a second effect. A repeatable tool is SAFE
       whatever happened; a NON_IDEMPOTENT one is UNSAFE unless the
       outcome is established - a send that definitely failed may be
       reconsidered, a send that timed out may not, because the message
       may already be gone.

    4. An undeclared side effect on an established failure is UNKNOWN,
       not SAFE. The call may have mutated something; nobody said. That
       is a real absence of knowledge, and the tri-state exists to carry
       it rather than round it off in either direction.
    """

    if status in (ToolStatus.DENIED, ToolStatus.INVALID_ARGUMENTS):
        return Retryability.UNSAFE

    if status is ToolStatus.UNAVAILABLE:
        return Retryability.SAFE

    if side_effect.repeatable:
        return Retryability.SAFE

    if not status.established:
        # TIMEOUT, CANCELLED, UNKNOWN over a tool that may have changed
        # the world. This single branch is the reason a message send
        # cannot be retried after a timeout.
        return Retryability.UNSAFE

    if side_effect is SideEffect.NON_IDEMPOTENT:
        # Declared mutating, and we know what happened. A SUCCESS must
        # not be repeated; an established FAILED or PARTIAL leaves the
        # decision to the caller, which is a judgement about intent
        # rather than about safety, so it is not called SAFE here.
        return Retryability.UNSAFE if status.ok else Retryability.UNKNOWN

    return Retryability.UNKNOWN


class ToolErrorCategory(str, Enum):
    """
    The kind of thing that went wrong, for callers deciding what to do
    next rather than what to say.

    Coarse on purpose - ten buckets, each mapping to a different
    recovery. `code` carries the specific fact; this carries the shape.

    VALIDATION   the request was malformed
    PERMISSION   an OS or device permission is missing
    POLICY       Aura's own policy, or a human, refused
    CAPABILITY   the capability is absent, blocked or unhealthy
    PROVIDER     the far side answered, and answered badly
    NETWORK      the far side could not be reached
    TIMEOUT      we stopped waiting
    EXECUTION    the tool ran and failed on its own terms
    INTERNAL     Aura is broken, not the request
    UNKNOWN      unclassified

    UNKNOWN is a real value, not a placeholder to avoid. A device
    returning a code this table has never seen is unclassified, and
    saying so beats guessing PROVIDER - the mistake `_category_of` in
    brain/providers/fallback.py was fixed for in Phase 1.
    """

    VALIDATION = "VALIDATION"
    PERMISSION = "PERMISSION"
    POLICY = "POLICY"
    CAPABILITY = "CAPABILITY"
    PROVIDER = "PROVIDER"
    NETWORK = "NETWORK"
    TIMEOUT = "TIMEOUT"
    EXECUTION = "EXECUTION"
    INTERNAL = "INTERNAL"
    UNKNOWN = "UNKNOWN"


# Canonical error codes. Module constants rather than an enum member per
# code, because codes also arrive from the Android device
# (NODE_NOT_FOUND, BRIDGE_ERROR) and will arrive from future providers: a
# closed enum would force every foreign code through a lossy conversion,
# and a code's whole job is to be specific. The category is what the
# runtime branches on, and `category_for_code` maps both families into it.
CODE_TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
CODE_TOOLS_DISABLED = "TOOLS_DISABLED"
CODE_NOT_ALLOWED = "NOT_ALLOWED"
CODE_INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
CODE_PERMISSION_DENIED = "PERMISSION_DENIED"
CODE_CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
CODE_BLOCKED_PERMISSION = "BLOCKED_PERMISSION"
CODE_CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
CODE_CAPABILITY_UNHEALTHY = "CAPABILITY_UNHEALTHY"
CODE_CAPABILITY_UNKNOWN = "CAPABILITY_UNKNOWN"
CODE_CAPABILITY_DISABLED = "CAPABILITY_DISABLED"
CODE_BLOCKED_DEPENDENCY = "BLOCKED_DEPENDENCY"
CODE_BLOCKED_PLATFORM = "BLOCKED_PLATFORM"
CODE_BLOCKED_CONFIGURATION = "BLOCKED_CONFIGURATION"
CODE_NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
CODE_TIMEOUT = "TIMEOUT"
CODE_CANCELLED = "CANCELLED"
CODE_TOOL_ERROR = "TOOL_ERROR"
CODE_OUTPUT_SCHEMA = "OUTPUT_SCHEMA_MISMATCH"
CODE_UNVERIFIED = "UNVERIFIED"
CODE_INTERNAL = "INTERNAL_ERROR"
CODE_UNKNOWN = "UNKNOWN"


_CATEGORY_BY_CODE: dict[str, ToolErrorCategory] = {
    CODE_TOOL_NOT_FOUND: ToolErrorCategory.VALIDATION,
    CODE_INVALID_ARGUMENTS: ToolErrorCategory.VALIDATION,
    CODE_TOOLS_DISABLED: ToolErrorCategory.POLICY,
    CODE_NOT_ALLOWED: ToolErrorCategory.POLICY,
    CODE_CONFIRMATION_REQUIRED: ToolErrorCategory.POLICY,
    CODE_PERMISSION_DENIED: ToolErrorCategory.PERMISSION,
    CODE_BLOCKED_PERMISSION: ToolErrorCategory.PERMISSION,
    CODE_CAPABILITY_UNAVAILABLE: ToolErrorCategory.CAPABILITY,
    CODE_CAPABILITY_UNHEALTHY: ToolErrorCategory.CAPABILITY,
    CODE_CAPABILITY_UNKNOWN: ToolErrorCategory.CAPABILITY,
    CODE_CAPABILITY_DISABLED: ToolErrorCategory.CAPABILITY,
    CODE_BLOCKED_DEPENDENCY: ToolErrorCategory.CAPABILITY,
    CODE_BLOCKED_PLATFORM: ToolErrorCategory.CAPABILITY,
    CODE_BLOCKED_CONFIGURATION: ToolErrorCategory.CAPABILITY,
    CODE_NOT_IMPLEMENTED: ToolErrorCategory.CAPABILITY,
    CODE_TIMEOUT: ToolErrorCategory.TIMEOUT,
    CODE_CANCELLED: ToolErrorCategory.EXECUTION,
    CODE_TOOL_ERROR: ToolErrorCategory.EXECUTION,
    CODE_OUTPUT_SCHEMA: ToolErrorCategory.EXECUTION,
    CODE_UNVERIFIED: ToolErrorCategory.EXECUTION,
    CODE_INTERNAL: ToolErrorCategory.INTERNAL,
    CODE_UNKNOWN: ToolErrorCategory.UNKNOWN,

    # Codes the Android bridge already emits, mapped rather than
    # rewritten. They are part of the device contract this phase must not
    # break, and they carry real information the model branches on.
    "NODE_NOT_FOUND": ToolErrorCategory.EXECUTION,
    "BRIDGE_ERROR": ToolErrorCategory.NETWORK,
    "DEVICE_UNAVAILABLE": ToolErrorCategory.CAPABILITY,
    "UNKNOWN_TOOL": ToolErrorCategory.VALIDATION,
}


def category_for_code(code: str) -> ToolErrorCategory:
    """
    The category of an error code, or UNKNOWN for one nobody declared.

    Unclassified rather than guessed: an unrecognised code from a device
    or a plugin is a gap in this table, and reporting it as such is what
    lets someone find the gap and close it.
    """

    return _CATEGORY_BY_CODE.get(
        str(code or "").strip().upper(), ToolErrorCategory.UNKNOWN
    )


@dataclass(frozen=True)
class ToolError:
    """
    Why a tool call did not succeed, in fields rather than a sentence.

    `message` still exists and is still the thing a person reads. What
    changes is that nothing has to parse it: `code` identifies the fact,
    `category` says what kind of fact it is, and the two flags say what
    can be done about it.

    The distinction the contract asks for:

        recoverable            Aura could get past this on its own
        user_action_required   a human has to do something first

    They are not opposites. A missing device permission is not
    recoverable by Aura and does require the user; a rate limit is
    recoverable by waiting and requires nobody. Both default False, which
    claims nothing rather than claiming the optimistic case.
    """

    code: str = CODE_UNKNOWN
    message: str = ""
    category: ToolErrorCategory | None = None
    recoverable: bool = False
    user_action_required: bool = False
    provider: str = ""
    capability: str = ""

    def __post_init__(self):
        """
        Fill the category from the code when it was not given.

        Derived rather than required, so a call site that knows the code
        cannot forget the category or contradict it by accident. A
        deliberate override still wins - a device knows things this table
        does not.
        """

        if self.category is None:
            object.__setattr__(self, "category", category_for_code(self.code))

    def as_dict(self) -> dict:
        """
        Wire form. Present fields only.

        Empty strings and False flags are dropped: an error that says
        nothing about a provider should not appear to name one, and a
        reader can tell "not claimed" from "claimed false" only if the
        absent case is actually absent.
        """

        payload: dict = {
            "code": self.code,
            "category": (
                self.category.value
                if self.category
                else ToolErrorCategory.UNKNOWN.value
            ),
            "message": self.message,
        }

        if self.recoverable:
            payload["recoverable"] = True

        if self.user_action_required:
            payload["user_action_required"] = True

        if self.provider:
            payload["provider"] = self.provider

        if self.capability:
            payload["capability"] = self.capability

        return payload


class EvidenceKind(str, Enum):
    """
    How a claim about the world was established.

    Every value names a real mechanism that exists in this codebase
    rather than an aspiration:

    POSTCONDITION   the tool re-asked its own condition after acting
                    (ToolExecutor._verified calling tool.verify())
    OBSERVATION     fresh state was read back and stored
                    (core.observations, linked by observation_id)
    RECEIPT         the far side returned an identifier or acknowledgement
                    (a device report, a provider's message id)
    RETURN_VALUE    the call returned without raising, and nothing more

    RETURN_VALUE is included precisely because it is weak. It is the
    evidence the contract says must not be trusted as verification, and
    naming it is how a result can be honest about having only that -
    which is more useful than omitting the field and letting a reader
    assume better.
    """

    POSTCONDITION = "POSTCONDITION"
    OBSERVATION = "OBSERVATION"
    RECEIPT = "RECEIPT"
    RETURN_VALUE = "RETURN_VALUE"
    MEMORY = "MEMORY"


@dataclass(frozen=True)
class Evidence:
    """
    One reason to believe a tool did what it says.

    `verified` is a tri-state, and that is the point:

        True    the check was made and it passed
        False   the check was made and it failed
        None    no check was made

    None is not a weaker True. A result carrying
    Evidence(RETURN_VALUE, verified=None) says "the call returned and
    nobody confirmed anything", which is what most successful tool calls
    actually know. Collapsing None into False would report every
    unverified success as a contradicted one; collapsing it into True
    would be the fabrication the contract forbids.

    Never construct this for a check that did not happen.
    """

    kind: EvidenceKind
    source: str = ""
    verified: bool | None = None
    timestamp: str = ""
    reference: str = ""
    detail: str = ""

    @property
    def confirms(self) -> bool:
        """Whether this evidence positively establishes the claim."""

        return self.verified is True

    def as_dict(self) -> dict:

        payload: dict = {"kind": self.kind.value}

        if self.source:
            payload["source"] = self.source

        if self.verified is not None:
            payload["verified"] = self.verified

        if self.timestamp:
            payload["timestamp"] = self.timestamp

        if self.reference:
            payload["reference"] = self.reference

        if self.detail:
            payload["detail"] = self.detail

        return payload


# How strongly each kind of evidence establishes a claim. A stored
# observation and a re-asked postcondition are both direct checks of the
# world; a receipt is the far side's word for it; a return value is only
# that the call came back.
_EVIDENCE_WEIGHT = {
    EvidenceKind.POSTCONDITION: 3,
    EvidenceKind.OBSERVATION: 3,
    EvidenceKind.RECEIPT: 2,
    EvidenceKind.RETURN_VALUE: 1,
}


def strongest(evidence: tuple) -> Evidence | None:
    """
    The confirming evidence a caller should quote, or None.

    Only evidence that actually confirms is eligible. A result with three
    unverified notes gets None, because none of them confirms anything
    and picking the best of nothing would misrepresent it.
    """

    confirming = [item for item in evidence if item.confirms]

    if not confirming:
        return None

    return max(confirming, key=lambda item: _EVIDENCE_WEIGHT.get(item.kind, 0))


def evidence_state(evidence: tuple) -> str:
    """
    A one-word summary of the evidence, for a prompt or a trace.

    VERIFIED      something confirms it
    CONTRADICTED  a check was made and it failed
    UNVERIFIED    evidence exists, none of it confirms
    NONE          nothing was offered

    CONTRADICTED outranks UNVERIFIED because a failed check is a positive
    finding, not an absence, and a reader who sees "unverified" for a
    postcondition that came back false has been told the wrong thing.
    """

    if not evidence:
        return "NONE"

    if any(item.confirms for item in evidence):
        return "VERIFIED"

    if any(item.verified is False for item in evidence):
        return "CONTRADICTED"

    return "UNVERIFIED"
