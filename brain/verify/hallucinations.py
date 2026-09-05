"""
Hallucination taxonomy.

The categories below are for observability and for deterministic rule
selection, not for pretending that hallucination is fully solved. The
verifier tracks *why* a claim was downgraded so a diagnostics search can
answer "how many FABRICATED_TOOL_RESULT claims did yesterday produce?"
- and so the repair layer can pick the right, evidence-honest wording.

Every enum value names the failure class; the claim's `ClaimState` says
how severely it was grounded. A claim can carry one primary category.
"""

from enum import Enum


class HallucinationType(str, Enum):
    """
    Why a claim was not grounded.

    UNSUPPORTED_FACT
        A factual claim with no evidence behind it at all: "the file
        contains 42 records" when nothing in this turn established a
        record count. The claim is not contradicted, it is merely
        ungrounded. Repair hedges or, for a small fact, leaves it alone
        rather than inventing a reason.

    UNSUPPORTED_ACTION
        An action claim ("I sent the email") with no tool execution
        behind it in the ledger. Nothing ran; the model asserted a side
        effect. This is the class the response contract is strictest
        about.

    FABRICATED_TOOL_RESULT
        An action claim that *contradicts* the actual tool outcome: the
        text says success but the tool FAILED, was DENIED, was
        UNAVAILABLE, or produced UNKNOWN status. The response claims a
        result the ledger says did not happen.

    FABRICATED_CAPABILITY
        An "I can do X" claim contradicted by the capability registry:
        the capability is UNAVAILABLE, BLOCKED or does not exist, and
        the model said it could be used.

    MEMORY_OVERCLAIM
        A claim grounded only in memory is asserted as VERIFIED ("you
        definitely prefer tea") when memory can at best SUPPORT or
        INFER. Memory is a record, not a proof.

    CONTRADICTED_CLAIM
        A direct contradiction between the response and evidence - a
        verified postcondition or observation says the opposite of the
        claim. Distinct from FABRICATED_TOOL_RESULT in that the evidence
        is fresh and affirmative, not derived from a tool failure.

    TEMPORAL_OVERCLAIM
        A current-state claim ("the weather is ...", "your phone now
        has ...") asserted without fresh evidence this turn. Model
        knowledge and stale memory are not current truth.

    NUMERICAL_OVERCLAIM
        The claim carries a number that contradicts a verified number in
        the evidence ("3 appointments" when the calendar result said 2).

    IDENTITY_OVERCLAIM
        A claim about the user's possessions, state or identity ("your
        phone has WhatsApp installed") with no supporting memory or
        observation. The model guessed at the user's world.

    The taxonomy is assigned by deterministic rules only; no linguistic
    judgment happens here. None of these categories implies that the
    claim is false - only that it was not grounded.
    """

    UNSUPPORTED_FACT = "UNSUPPORTED_FACT"
    UNSUPPORTED_ACTION = "UNSUPPORTED_ACTION"
    FABRICATED_TOOL_RESULT = "FABRICATED_TOOL_RESULT"
    FABRICATED_CAPABILITY = "FABRICATED_CAPABILITY"
    MEMORY_OVERCLAIM = "MEMORY_OVERCLAIM"
    CONTRADICTED_CLAIM = "CONTRADICTED_CLAIM"
    TEMPORAL_OVERCLAIM = "TEMPORAL_OVERCLAIM"
    NUMERICAL_OVERCLAIM = "NUMERICAL_OVERCLAIM"
    IDENTITY_OVERCLAIM = "IDENTITY_OVERCLAIM"


__all__ = ["HallucinationType"]