"""
Claim states and verifier decisions.

Phase 4 separates what a response *claims* from what the runtime
*established*, and it refuses to let the two blur. `ClaimState` is the
five-state vocabulary every claim ends in; `VerifierDecision` is what
the runtime may do about the claims it found.

The five states are deliberately not a boolean. Collapsing "we have
direct evidence", "the memory strongly supports it", "it is a
reasonable inference", "we cannot tell" and "the evidence says the
opposite" into "supported/not" is how a grounding layer produces false
certainty while claiming to be careful.
"""

from enum import Enum


class ClaimState(str, Enum):
    """
    How strongly a claim is grounded in evidence.

    VERIFIED
        Direct evidence establishes the claim: a tool postcondition or a
        fresh observation said so (Phase 3 Evidence with verified=True),
        or the user's own current statement is corroborated this turn.
        Only this state authorises the word "did/succeeded" as fact.

    SUPPORTED
        Available evidence strongly supports the claim without directly
        establishing it: a recent, high-confidence memory, or a live
        capability that is AVAILABLE for an "I can ..." claim. The claim
        is likely true; it is not proven to the user's eyes.

    INFERRED
        A reasonable inference but not established: a tool returned
        SUCCESS yet nothing verified the effect (RETURN_VALUE evidence
        only), or an old/low-confidence memory is being repeated.
        Honest wording is "probably" or "as far as I know", never a bare
        assertion.

    UNKNOWN
        Insufficient evidence: the claim matches no tool outcome, no
        memory, no capability state, or the evidence present conflicts
        with itself. Nothing may be asserted here as fact.

    CONTRADICTED
        Available evidence conflicts with the claim: the tool FAILED,
        DENIED, or was UNAVAILABLE while the text claims success; a
        postcondition came back False; or the capability registry says
        the capability is not available while the text claims it is.
        A CONTRADICTED claim must be repaired or refused.

    The ordering is monotonic in trustworthiness: VERIFIED is the only
    state that establishes fact; SUPPORTED and INFERRED may be said
    with the right qualifier; UNKNOWN and CONTRADICTED may not be said
    as fact at all.

    UNKNOWN is never converted into VERIFIED, and INFERRED is never
    disguised as SUPPORTED - the repair layer only ever moves a claim
    downward in certainty, never upward.
    """

    VERIFIED = "VERIFIED"
    SUPPORTED = "SUPPORTED"
    INFERRED = "INFERRED"
    UNKNOWN = "UNKNOWN"
    CONTRADICTED = "CONTRADICTED"

    @property
    def assertable(self) -> bool:
        """Whether the claim may be stated as plain fact."""

        return self is ClaimState.VERIFIED

    @property
    def qualifies(self) -> bool:
        """Whether the claim may stand with an honest qualifier."""

        return self in (ClaimState.VERIFIED, ClaimState.SUPPORTED)


class VerifierDecision(str, Enum):
    """
    What the verifier says the runtime may do with this response.

    PASS
        Every claim that needed grounding has it. Deliver unchanged.

    REPAIR
        At least one claim was contradicted, fabricated or ungrounded in
        a way the repair layer could fix with a minimal, evidence-honest
        replacement sentence. Deliver the repaired text.

    MARK_UNCERTAIN
        At least one action or factual claim turned out UNKNOWN and no
        conflicting evidence exists to act on. The claim is hedged
        ("I can't verify ...") rather than asserted. Deliver the hedged
        text.

    ASK_CLARIFICATION
        The user's evidence base is genuinely ambiguous (conflicting
        memories, a capability that is neither confirmed nor denied)
        and inventing either direction would mislead. The response is
        left intact and a clarification is the honest next step - the
        verifier records this so the caller can decide.

    REFUSE_UNSUPPORTED_CLAIM
        A claim is contradicted and cannot be repaired without
        inventing information, or the user explicitly asked for an
        answer the evidence forbids. The verifier removes the claim; if
        nothing sound remains, the caller must say why.

    The runtime never performs an action from a claim that is below
    SUPPORTED, and never asserts a VERIFIED outcome that lacks verified
    evidence.
    """

    PASS = "PASS"
    REPAIR = "REPAIR"
    MARK_UNCERTAIN = "MARK_UNCERTAIN"
    ASK_CLARIFICATION = "ASK_CLARIFICATION"
    REFUSE_UNSUPPORTED_CLAIM = "REFUSE_UNSUPPORTED_CLAIM"


__all__ = ["ClaimState", "VerifierDecision"]