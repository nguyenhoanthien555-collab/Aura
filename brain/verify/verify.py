"""
The response verifier.

Phase 4 boundary in one object: `ResponseVerifier.verify(text, ledger)`
takes a generated reply and the evidence the runtime actually gathered,
and returns a `VerificationResult`: each claim classified, minimal
repairs applied when sound, and a single `VerifierDecision`.

The verifier is deterministic, stateless across calls, and never raises
- a verification problem is logged and the original text passes through
unrepaired, because truthfulness must never cost a turn. It uses no
second model: every decision is a table lookup over the ledger and the
live capability registry.

Diagnostics: one `verifier` trace line per verification, with counts and
the decision only - never the response text, never the evidence content.
"""

import time
from dataclasses import dataclass, field

from core.logger import logger
from core.trace import emit_trace

from brain.verify.claims import extract_claims
from brain.verify.ledger import EvidenceLedger
from brain.verify.repair import repair_claims
from brain.verify.rules import verify_claim
from brain.verify.status import ClaimState, VerifierDecision


@dataclass(frozen=True)
class VerificationResult:
    """
    Everything the runtime needs to know about one verification.

    `claims` carries the full per-claim audit (state, evidence refs,
    hallucination category). `repairs` is a list of
    (claim_id, original_sentence, replacement_sentence) tuples.
    `repaired_text` is unchanged when `decision` is PASS.
    """

    request_id: str
    decision: VerifierDecision
    text: str
    repaired_text: str
    claims: list = field(default_factory=list)
    repairs: list = field(default_factory=list)
    latency_ms: float = 0.0
    counts: dict = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return self.repaired_text != self.text


def default_capability_provider(words: set[str]) -> tuple[str, bool, str]:
    """
    Match the claim's words against the live capability registry.

    Returns (state, matched, capability_id). Uses the same registry the
    prompt inventory is rendered from, via resolve_capability, so the
    verifier and the model are told the same truth. A word that matches
    no known capability returns ("UNKNOWN", False, "") - never an
    assumption that the capability exists.
    """

    try:
        from core.capabilities import registry, resolve_capability
    except Exception:  # noqa: BLE001
        return "UNKNOWN", False, ""

    try:
        for capability in registry.all():

            haystack = (
                capability.capability_id.lower()
                + " "
                + capability.name.lower()
                + " "
                + (capability.description or "").lower()
            )

            if any(word in haystack for word in words if len(word) >= 3):
                state = resolve_capability(capability.capability_id)
                return state.value, True, capability.capability_id
    except Exception:  # noqa: BLE001
        return "UNKNOWN", False, ""

    return "UNKNOWN", False, ""


class ResponseVerifier:
    """
    Deterministic, request-scoped claim -> evidence verification.

    `capability_provider` is a callable(words) -> (state, matched, id).
    Tests inject a fake; production uses `default_capability_provider`.

    `repair` false is observe-only mode: every claim is still classified,
    counted and traced, and the decision still says what the evidence
    warranted, but the text is delivered exactly as generated. That is
    the honest way to measure this layer against real traffic before
    letting it rewrite anything, and it is a separate switch from
    `enabled` for precisely that reason.
    """

    def __init__(self, capability_provider=None, repair: bool = True):
        self._capability_provider = (
            capability_provider or default_capability_provider
        )
        self._repair = repair

    def verify(
        self,
        text: str,
        ledger: EvidenceLedger | None = None,
    ) -> VerificationResult:
        """
        Classify, ground and minimally repair one reply.

        Never raises. On an internal failure the text passes through
        unrepaired and `decision` is PASS - a grounding outage must not
        cost a conversation.
        """

        ledger = ledger or EvidenceLedger()

        started = time.perf_counter()

        try:
            return self._verify(text, ledger)
        except Exception as error:  # noqa: BLE001
            logger.warning(
                "Verifier failed for %s: %s", ledger.request_id, error
            )
            return VerificationResult(
                request_id=ledger.request_id,
                decision=VerifierDecision.PASS,
                text=text,
                repaired_text=text,
                claims=[],
                latency_ms=round(
                    (time.perf_counter() - started) * 1000, 1
                ),
                counts={"error": True},
            )

    # ------------------------------------------------------------------

    def _verify(self, text: str, ledger: EvidenceLedger) -> VerificationResult:
        started = time.perf_counter()

        claims = extract_claims(text)

        for claim in claims:
            ledger.add_claim(claim)
            verify_claim(claim, ledger, self._capability_provider)

        if self._repair:
            repaired_text, repairs = repair_claims(text, claims)
        else:
            repaired_text, repairs = text, []

        decision = self._decide(claims, repairs)

        latency_ms = round((time.perf_counter() - started) * 1000, 1)

        result = VerificationResult(
            request_id=ledger.request_id,
            decision=decision,
            text=text,
            repaired_text=repaired_text,
            claims=claims,
            repairs=repairs,
            latency_ms=latency_ms,
            counts=self._counts(claims, repairs),
        )

        self._emit_diagnostics(ledger, result)

        return result

    # ------------------------------------------------------------------

    @staticmethod
    def _decide(claims, repairs) -> VerifierDecision:
        """
        The single decision card for this response.

        Order is severity, not convenience. A repair actually happened,
        so REPAIR is reported even when something was also contradicted -
        the contradiction is what the repair fixed. A contradiction that
        survived unrepaired is the worst remaining outcome. Conflicting
        evidence outranks a plain unknown because it is answerable: the
        honest next move is a question, not a hedge.

        Only IN-SCOPE unknowns reach MARK_UNCERTAIN. An ungrounded
        general-knowledge sentence is recorded in the counts and left
        alone, and calling that "uncertain" would make the decision field
        mean nothing - almost every reply contains one.
        """

        if repairs:
            return VerifierDecision.REPAIR

        contradicted = [
            claim for claim in claims
            if claim.state is ClaimState.CONTRADICTED
        ]

        if contradicted:
            return VerifierDecision.REFUSE_UNSUPPORTED_CLAIM

        if any(claim.conflict for claim in claims):
            return VerifierDecision.ASK_CLARIFICATION

        unknown = [
            claim for claim in claims
            if claim.state is ClaimState.UNKNOWN
            and claim.in_scope
        ]

        if unknown:
            return VerifierDecision.MARK_UNCERTAIN

        return VerifierDecision.PASS

    @staticmethod
    def _counts(claims, repairs) -> dict:
        """Counts by state + hallucination + repairs, for one trace."""

        counts = {
            "claims": len(claims),
            "verified": 0,
            "supported": 0,
            "inferred": 0,
            "unknown": 0,
            "contradicted": 0,
            "unsupported": 0,
            "in_scope": 0,
            "repairs": len(repairs),
        }

        for claim in claims:

            if claim.in_scope:
                counts["in_scope"] += 1

            if claim.state is None:
                continue

            key = claim.state.value.lower()
            counts[key] = counts.get(key, 0) + 1

            if claim.hallucination:
                counts["unsupported"] += 1

        return counts

    @staticmethod
    def _emit_diagnostics(ledger: EvidenceLedger, result) -> None:
        """
        One structured trace line. Counts and decision only - never text.
        """

        try:
            emit_trace(
                "verifier",
                request_id=ledger.request_id,
                decision=result.decision.value,
                latency_ms=result.latency_ms,
                **result.counts,
            )
        except Exception:  # noqa: BLE001
            logger.debug("Verifier trace failed for %s", ledger.request_id)


def verify_run_reply(
    text: str,
    messages: list[dict],
    request_id: str = "",
    verifier: "ResponseVerifier | None" = None,
):
    """
    Phase 4.5: verify an agent-run reply against its own transcript.

    The AgentRuntime transcript is the run's evidence of record: every
    tool round is a `role="tool"` message holding the structured
    envelopes the loop actually executed. `ledger_from_transcript`
    converts those into ledger evidence, so the final answer the
    /api/agent/intent route returns is checked against what the run did
    - the same boundary the conversation path gets, at the point the
    reply leaves the runtime.

    Never raises: a verification problem passes the text through
    unrepaired, exactly like ResponseVerifier.verify.
    """

    from brain.verify.ledger import ledger_from_transcript

    ledger = ledger_from_transcript(messages, request_id=request_id)

    return (verifier or ResponseVerifier()).verify(text, ledger)


__all__ = [
    "ResponseVerifier",
    "VerificationResult",
    "default_capability_provider",
    "verify_run_reply",
]