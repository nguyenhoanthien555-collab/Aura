"""
Deterministic verification rules.

`verify_claim` decides one claim's `ClaimState` from the ledger and the
capability registry. Every rule is a table lookup over structured facts
- never a language judgment - which is what makes the verifier
deterministic, testable, and incapable of inventing a reason.

The shape every rule obeys:

    evidence exists and confirms   ->  VERIFIED or SUPPORTED
    reasonable but unconfirmed     ->  INFERRED
    nothing to go on               ->  UNKNOWN
    evidence contradicts           ->  CONTRADICTED

The strict classes of action claims replicate the Phase 3 ToolStatus
contract exactly, so the executor and the verifier cannot disagree: a
FAILED, DENIED, UNAVAILABLE, PARTIAL or UNKNOWN tool result never
becomes a claimed success.
"""

import re

from tools.outcome import ToolStatus, evidence_state

from brain.verify.claims import Claim, ClaimType, is_action_claim
from brain.verify.ledger import EvidenceLedger, ToolEvidence, MemoryEvidence
from brain.verify.status import ClaimState
from brain.verify.hallucinations import HallucinationType

# Claim words that invert a capability claim ("I can't access your phone").
_NEGATIONS = frozenset(
    {"can't", "cannot", "can’t", "couldn't", "unable", "no", "not",
     "don't", "dont", "doesn't", "không", "không thể"}
)

_VERB_TOOL_FAMILIES = {
    "sent": ("send", "sends", "sent", "email", "sms", "text"),
    "created": ("create", "created", "calendar", "event", "note", "contact"),
    "deleted": ("delete", "deleted", "remove", "removed", "trash"),
    "opened": ("open", "opened", "launch", "launched", "start"),
    "changed": ("change", "changed", "set", "settings", "update", "updated"),
    "saved": ("save", "saved", "write", "wrote"),
    "modified": ("modify", "modified", "edit", "edited", "file"),
    "installed": ("install", "installed", "package", "apk"),
    "verified": ("verify", "verified", "confirm", "confirmed"),
    "scheduled": ("schedule", "scheduled", "calendar", "meeting", "event"),
    "cancelled": ("cancel", "cancelled", "canceled"),
}


def _claim_number(sentence: str) -> str | None:
    """The first plain integer in a sentence, or None."""

    match = re.search(r"\b(\d+)\b", sentence)
    return match.group(1) if match else None


def _grade_success(status: str, state: str) -> tuple[ClaimState, str]:
    """
    How a matched tool outcome grades an action claim.

    SUCCESS + verified evidence is the only VERIFIED; SUCCESS without
    verification is INFERRED (the call returned, the effect was not
    confirmed); every non-success status is never a success.

    PARTIAL is its own line rather than one of the failures. The contract
    is explicit that only the verified portion of a partial result may be
    claimed, so a sentence asserting the whole thing IS contradicted -
    but the honest repair is "part of it went through", not "it failed",
    and the note carries that difference through to the repair layer.
    """

    if status == ToolStatus.SUCCESS.value:
        if state == "VERIFIED":
            return ClaimState.VERIFIED, ""
        if state == "CONTRADICTED":
            # A postcondition that came back False is a positive finding,
            # not an absence: the tool returned, the check ran and it
            # failed. The same contradicted verdict a FAILED status gets -
            # a success envelope wrapping a failed check is still a
            # failure, and this is where Phase 4.5's matrix caught it.
            return (
                ClaimState.CONTRADICTED,
                "the tool succeeded but its own verification came back false",
            )
        return ClaimState.INFERRED, "tool succeeded but the effect was not verified"

    if status == ToolStatus.PARTIAL.value:
        return (
            ClaimState.CONTRADICTED,
            "tool reported PARTIAL; only part of it happened",
        )

    if status in (
        ToolStatus.FAILED.value,
        ToolStatus.DENIED.value,
        ToolStatus.UNAVAILABLE.value,
    ):
        return (
            ClaimState.CONTRADICTED,
            f"tool reported {status}; claiming success contradicts it",
        )

    if status == ToolStatus.UNKNOWN.value:
        return (
            ClaimState.UNKNOWN,
            "tool result is UNKNOWN; the outcome cannot be established",
        )

    return (
        ClaimState.UNKNOWN,
        f"tool status {status} does not establish the outcome",
    )


def _verify_action_claim(claim: Claim, ledger: EvidenceLedger) -> None:
    """
    The strictest rule. An action claim needs a matched tool outcome;
    nothing matches, nothing is claimed.
    """

    words = ledger.claim_words(claim.sentence)

    matched: list[ToolEvidence] = list(ledger.matching_tool(words))

    if not matched:
        claim.state = ClaimState.UNKNOWN
        claim.hallucination = HallucinationType.UNSUPPORTED_ACTION.value
        claim.notes.append("no tool evidence matches this claim")
        return

    best = matched[0]
    claim.evidence_refs.append(best.evidence_id)

    state, note = _grade_success(best.status, best.state)

    claim.state = state
    claim.notes.append(note or "matched tool evidence")

    if state is ClaimState.CONTRADICTED:
        claim.hallucination = HallucinationType.FABRICATED_TOOL_RESULT.value
        claim.notes.append(f"tool '{best.tool}' status was {best.status}")
    elif state is ClaimState.UNKNOWN:
        claim.hallucination = HallucinationType.UNSUPPORTED_ACTION.value


def _verify_capability_claim(
    claim: Claim, ledger: EvidenceLedger, capability_provider,
) -> None:
    """
    "I can do X" claims are checked against the live capability registry.

    The provider maps the claim's words to a capability and returns
    (state, matched, capability_id). The rule inverts for negative
    claims: "I can't access" is SUPPORTED when the capability is indeed
    unavailable, and CONTRADICTED when it is available.
    """

    words = ledger.claim_words(claim.sentence)

    try:
        state, matched, capability_id = capability_provider(words)
    except Exception:  # noqa: BLE001 - a registry hiccup degrades, never fails
        claim.state = ClaimState.UNKNOWN
        claim.notes.append("capability registry unavailable")
        return

    evidence_id = ledger.add_capability(
        statement=claim.sentence,
        state=state,
        matched=matched,
        capability_id=capability_id,
    )
    claim.evidence_refs.append(evidence_id)

    negated = any(word in _NEGATIONS for word in words)

    if not matched:
        claim.state = ClaimState.SUPPORTED if negated else ClaimState.UNKNOWN
        claim.notes.append("no registry entry matches the claim's words")
        return

    available = state == "AVAILABLE"

    if negated:
        claim.state = (
            ClaimState.SUPPORTED if not available
            else ClaimState.CONTRADICTED
        )
    else:
        claim.state = (
            ClaimState.SUPPORTED if available
            else ClaimState.CONTRADICTED
        )
        claim.notes.append(
            "capability is AVAILABLE" if available
            else f"capability '{capability_id}' is {state}"
        )

    if not negated and not available:
        claim.hallucination = HallucinationType.FABRICATED_CAPABILITY.value


def _memory_provenance(
    memory: MemoryEvidence,
) -> tuple[ClaimState, str]:
    """
    The most a memory line can support.

    High-confidence and recent/undated lines SUPPORT; old, low-confidence
    or inferred lines INFER; anything the pipeline did not describe is
    read conservatively as INFERRED - never as a verified fact.
    """

    if memory.confidence == "high" and memory.recency in ("", "recent"):
        return ClaimState.SUPPORTED, "recent high-confidence memory"

    if memory.recency == "old" or memory.confidence in ("low", "inferred"):
        return ClaimState.INFERRED, "older or lower-confidence memory"

    if memory.source == "inferred":
        return ClaimState.INFERRED, "inferred memory, not user-confirmed"

    return ClaimState.INFERRED, "memory provenance unknown; treated conservatively"


def _verify_memory_claim(claim: Claim, ledger: EvidenceLedger) -> None:
    """
    A claim that echoes a recalled memory is grounded by provenance.

    Memory can SUPPORT or INFER - never VERIFY. A numeric mismatch with
    the recalled line is a contradiction, and two overlapping memories
    that disagree on a fact are a conflict, which the rule reports as
    UNKNOWN rather than picking a winner.
    """

    words = ledger.claim_words(claim.sentence)

    memories = ledger.matching_memory(words)

    if not memories:
        claim.state = ClaimState.UNKNOWN
        return

    memory = memories[0]
    claim.evidence_refs.append(memory.evidence_id)

    claim_number = _claim_number(claim.sentence)

    # Conflicting memories: two recalled lines overlap the claim but do
    # not agree on its contents. No winner is picked - conflicting memory
    # is the definition of insufficient evidence for one specific fact.
    differing = [
        other for other in memories[1:]
        if _claims_conflict(memory.line, other.line)
    ]

    if differing:
        claim.state = ClaimState.UNKNOWN
        claim.conflict = True
        claim.notes.append(
            "recalled memories conflict; neither is asserted"
        )
        return

    memory_number = _claim_number(memory.line)

    if (
        claim_number is not None
        and memory_number is not None
        and claim_number != memory_number
    ):
        claim.state = ClaimState.CONTRADICTED
        claim.hallucination = HallucinationType.NUMERICAL_OVERCLAIM.value
        claim.notes.append(
            f"claim says {claim_number}, memory line says {memory_number}"
        )
        return

    claim.state, note = _memory_provenance(memory)
    claim.notes.append(note)


def _claims_conflict(left: str, right: str) -> bool:
    """
    Whether two memory lines contradict rather than merely differ.

    Specific: both carry a number and the numbers disagree, or one
    asserts presence (has/is/contains) and the other absence (no/none/
    không) of the same word.
    """

    left_number = _claim_number(left)
    right_number = _claim_number(right)

    if (
        left_number is not None
        and right_number is not None
        and left_number != right_number
    ):
        return True

    lowered_left = left.lower()
    lowered_right = right.lower()

    negation = ("no ", "none", "not installed", "uninstalled", "không")

    present_word = _present_word(lowered_left, lowered_right)

    if present_word is None:
        return False

    left_asserts = any(token in lowered_left for token in negation)
    right_asserts = any(token in lowered_right for token in negation)

    return left_asserts != right_asserts


def _present_word(left: str, right: str) -> str | None:
    """
    A content word both lines mention - the subject they disagree on.
    """

    left_words = {word.strip(".,!?") for word in left.split() if len(word) > 3}
    right_words = {word.strip(".,!?") for word in right.split() if len(word) > 3}

    common = sorted(left_words & right_words)

    return common[0] if common else None


def _verify_factual_claim(
    claim: Claim, ledger: EvidenceLedger, capability_provider=None,
) -> None:
    """
    Factual, identity, temporal and numeric claims that are NOT memory
    echoes are grounded in tool outcomes when they match; otherwise they
    have no evidence in the ledger and stay UNKNOWN (repair may hedge,
    never invent).
    """

    words = ledger.claim_words(claim.sentence)

    matched = ledger.matching_tool(words)

    if matched:
        best = matched[0]
        claim.evidence_refs.append(best.evidence_id)
        claim.state, note = _grade_success(best.status, best.state)
        claim.notes.append(note or "grounded in matched tool evidence")

        if ClaimType.NUMERICAL in claim.tags:
            claim_number = _claim_number(claim.sentence)
            outcome_number = _claim_number(best.outcome or "")
            if (
                claim_number is not None
                and outcome_number is not None
                and claim_number != outcome_number
            ):
                claim.state = ClaimState.CONTRADICTED
                claim.hallucination = (
                    HallucinationType.NUMERICAL_OVERCLAIM.value
                )
                claim.notes.append(
                    f"claim says {claim_number}, evidence says {outcome_number}"
                )
        return

    # No tool evidence behind a user-state or current-time claim.
    #
    # Both branches require the claim to be inside the grounding scope -
    # to name something in the user's world. "You currently have 3
    # appointments" is a claim Aura must not make unchecked; "you have
    # three options" is a turn of phrase with the same shape, and
    # hedging it would be exactly the over-strictness the contract
    # forbids.
    if ClaimType.IDENTITY in claim.tags and claim.world:
        claim.state = ClaimState.UNKNOWN
        claim.hallucination = HallucinationType.IDENTITY_OVERCLAIM.value
        claim.notes.append("no memory or observation supports this about the user")
        return

    if ClaimType.TEMPORAL in claim.tags and claim.world:
        claim.state = ClaimState.UNKNOWN
        claim.hallucination = HallucinationType.TEMPORAL_OVERCLAIM.value
        claim.notes.append("no fresh evidence for a current-state claim")
        return

    claim.state = ClaimState.UNKNOWN
    claim.hallucination = HallucinationType.UNSUPPORTED_FACT.value
    claim.notes.append("no evidence in the ledger matches this claim")


def verify_claim(claim: Claim, ledger: EvidenceLedger,
                 capability_provider=None) -> None:
    """
    Decide one claim, filling `state`, `evidence_refs`, `hallucination`
    and `notes`. Never raises; a failing capability provider degrades the
    claim to UNKNOWN.

    A GENERAL sentence is left with `state=None` - deliberately not
    UNKNOWN. "We did not evaluate this" and "we looked and found no
    evidence" are different facts, and recording ordinary prose as an
    ungrounded claim would inflate every unknown count in diagnostics
    with sentences that were never claims.
    """

    if claim.type == ClaimType.GENERAL:
        return

    # Memory-echo annotation happens before the per-type rules.
    #
    # ACTION and CAPABILITY are exempt: a memory line that happens to
    # share words with "I sent the email" or "I can send SMS" is not
    # evidence about either, and letting it reclassify the claim would
    # route it away from the tool ledger and the capability registry -
    # the only two things that can actually answer them.
    words = ledger.claim_words(claim.sentence)
    if ledger.matching_memory(words) and claim.type not in (
        ClaimType.ACTION, ClaimType.CAPABILITY,
    ):
        claim.type = ClaimType.MEMORY_DERIVED

    if is_action_claim(claim):
        _verify_action_claim(claim, ledger)
    elif claim.type == ClaimType.CAPABILITY:
        _verify_capability_claim(claim, ledger, capability_provider)
    elif claim.type == ClaimType.MEMORY_DERIVED:
        _verify_memory_claim(claim, ledger)
    else:
        _verify_factual_claim(claim, ledger, capability_provider)


__all__ = ["verify_claim"]