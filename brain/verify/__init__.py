"""
Phase 4 - response-level claim -> evidence verification.

A deterministic grounding layer between the generated reply and the user:
claims are extracted, grounded against the request-scoped evidence ledger
and the live capability registry, and minimally repaired when the model
overclaimed. No second model is used; every rule is a table lookup.
"""

from brain.verify.claims import Claim, ClaimType, extract_claims
from brain.verify.hallucinations import HallucinationType
from brain.verify.ledger import EvidenceLedger, MemoryEvidence, ToolEvidence
from brain.verify.repair import repair_claims
from brain.verify.rules import verify_claim
from brain.verify.status import ClaimState, VerifierDecision
from brain.verify.verify import (
    ResponseVerifier,
    VerificationResult,
    default_capability_provider,
    verify_run_reply,
)

__all__ = [
    "Claim",
    "ClaimType",
    "ClaimState",
    "EvidenceLedger",
    "HallucinationType",
    "MemoryEvidence",
    "ResponseVerifier",
    "ToolEvidence",
    "VerificationResult",
    "VerifierDecision",
    "default_capability_provider",
    "extract_claims",
    "repair_claims",
    "verify_claim",
]