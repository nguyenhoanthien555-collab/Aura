"""
The request-scoped evidence ledger.

Phase 4's ledger is a per-request account of *what the runtime actually
established* while answering: every tool outcome, every recalled memory
line, every capability fact. The verifier reads it, never the model text
- a model sentence is the thing being tested, not a source of evidence
for itself (the one case the contract is explicit about).

Evidence records reuse the Phase 3 canonical `Evidence` primitive from
tools/outcome.py rather than inventing a parallel one. What Phase 4 adds
is the *pairing* the rules need: a ToolResult's status next to its
Evidence, a memory line next to its provenance notes.

The ledger deliberately holds no raw conversation content beyond the
short rendered lines that already entered the prompt - and even those
are recorded for matching only, never emitted in diagnostics.
"""

import uuid
import json
from dataclasses import dataclass, field

from tools.outcome import (
    Evidence,
    EvidenceKind,
    ToolErrorCategory,
    ToolStatus,
    category_for_code,
    evidence_state,
)

from brain.verify.status import ClaimState


@dataclass(frozen=True)
class ToolEvidence:
    """
    One executed tool, paired with its canonical evidence and status.

    `state` is derived from the ToolResult's own Phase 3 evidence via
    `evidence_state()` - NONE / UNVERIFIED / VERIFIED / CONTRADICTED -
    so the verifier and the executor agree by construction on what a
    result proves.
    """

    evidence_id: str
    tool: str
    status: str
    evidence: tuple[Evidence, ...] = ()
    outcome: str = ""
    capability: str = ""
    side_effect: str = ""

    @property
    def state(self) -> str:
        return evidence_state(self.evidence)

    @property
    def succeeds(self) -> bool:
        return self.status == ToolStatus.SUCCESS.value

    def as_dict(self) -> dict:
        """Diagnostics form - identity only, no outcome content."""

        return {
            "kind": "tool",
            "tool": self.tool,
            "status": self.status,
            "evidence": self.state,
            "capability": self.capability,
        }


@dataclass(frozen=True)
class MemoryEvidence:
    """
    One recalled memory line, with the provenance Phase 4 can see.

    Memory is never evidence of verified truth - it is a record, whose
    trustworthiness is a function of how recent and how confirmatory it
    is. That is exactly what `recency` and `confidence` carry; when the
    pipeline exposes neither, both stay empty and the rules read the
    line as old/unknown, never as fresh.
    """

    evidence_id: str
    line: str
    recency: str = ""        # "recent" | "old" | "" (unknown)
    confidence: str = ""     # "high" | "low" | "inferred" | "" (unknown)
    source: str = ""         # "user" | "inferred" | "profile" | pipeline label

    def as_dict(self) -> dict:
        return {
            "kind": "memory",
            "recency": self.recency or "unknown",
            "confidence": self.confidence or "unknown",
            "source": self.source,
        }


@dataclass(frozen=True)
class CapabilityEvidence:
    """
    A fact about the live capability registry, resolved at verify time.

    `state` is the CapabilityState value (AVAILABLE / UNAVAILABLE /
    UNKNOWN / ...); `matched` records whether the claim text actually
    pointed at a known capability. A capability claim about something
    that does not exist in the registry is UNKNOWN, never assumed.
    """

    evidence_id: str
    statement: str
    state: str
    matched: bool = False
    capability_id: str = ""

    def as_dict(self) -> dict:
        return {
            "kind": "capability",
            "state": self.state,
            "matched": self.matched,
        }


class EvidenceLedger:
    """
    One request's evidence, claim by claim.

    The claims list is the verifier's working set. Each claim carries
    `evidence_refs` naming the evidence records that grounded (or
    contradicted) it, so "did the runtime establish this?" is one lookup
    instead of a search through the transcript.

    `request_id` is the request the ledger describes - the message_id,
    run_id or session id the caller owns.
    """

    def __init__(self, request_id: str = ""):
        self.request_id = request_id or f"req_{uuid.uuid4().hex[:12]}"
        self.tools: list[ToolEvidence] = []
        self.memories: list[MemoryEvidence] = []
        self.capabilities: list[CapabilityEvidence] = []
        self.claims: list = []

    # -- recording ----------------------------------------------------

    def add_tool(
        self,
        tool: str,
        status: str,
        evidence: tuple[Evidence, ...] = (),
        outcome: str = "",
        capability: str = "",
        side_effect: str = "",
    ) -> str:
        evidence_id = f"ev{tool.replace('.', '_')}{len(self.tools) + 1}"
        self.tools.append(
            ToolEvidence(
                evidence_id=evidence_id,
                tool=tool,
                status=status,
                evidence=tuple(evidence),
                outcome=outcome,
                capability=capability,
                side_effect=side_effect,
            )
        )
        return evidence_id

    def add_memory(
        self,
        line: str,
        recency: str = "",
        confidence: str = "",
        source: str = "",
    ) -> str:
        evidence_id = f"mem{len(self.memories) + 1}"
        self.memories.append(
            MemoryEvidence(
                evidence_id=evidence_id,
                line=line,
                recency=recency,
                confidence=confidence,
                source=source,
            )
        )
        return evidence_id

    def add_capability(
        self, statement: str, state: str, matched: bool = False,
        capability_id: str = "",
    ) -> str:
        evidence_id = f"cap{len(self.capabilities) + 1}"
        self.capabilities.append(
            CapabilityEvidence(
                evidence_id=evidence_id,
                statement=statement,
                state=state,
                matched=matched,
                capability_id=capability_id,
            )
        )
        return evidence_id

    def add_claim(self, claim) -> None:
        claim.evidence_refs = []
        self.claims.append(claim)

    # -- matching -----------------------------------------------------

    def claim_words(self, sentence: str) -> set[str]:
        """
        The meaningful words of a claim sentence, lowercased.

        Stopwords are dropped so evidence matching is insensitive to
        phrasing while staying deterministic.
        """

        stopwords = {
            "i", "i've", "the", "a", "an", "and", "or", "to", "of", "in",
            "on", "at", "it", "its", "this", "that", "your", "you", "my",
            "me", "was", "were", "has", "have", "had", "been", "is", "are",
            "will", "would", "can", "could", "did", "does", "do", "not",
            "so", "for", "with", "just", "sure", "yes", "no", "then",
            "well", "tôi", "em", "đã", "của", "và", "bạn", "có", "là",
        }

        words = {
            word.strip(".,!?;:()[]'\"")
            for word in sentence.lower().split()
        }

        return {word for word in words if word and word not in stopwords}

    @staticmethod
    def _overlaps(words: set[str], text: str) -> bool:
        """Whether any claim word appears in the evidence text."""

        return bool(words & set(text.split()))

    def matching_memory(self, words: set[str]) -> list[MemoryEvidence]:
        """
        Recalled lines whose words overlap the claim. Used only to
        detect that a claim echoes memory; never to promote memory to
        VERIFIED.
        """

        return [
            memory for memory in self.memories
            if self._overlaps(words, memory.line.lower())
        ]

    def matching_tool(self, words: set[str]) -> list[ToolEvidence]:
        """
        Tool evidence whose name or outcome overlaps the claim's words.

        Matching is deliberately token-overlap, not semantic: a claim
        "sent the email" overlaps tool `email.send` through the word
        "email", and a claim about the recipient overlaps the delivered
        outcome line. A claim with no overlap at all finds nothing, and
        the rules treat nothing-found as UNKNOWN - never as success.
        """

        matched: list[ToolEvidence] = []

        for tool in self.tools:

            name = tool.tool.lower().replace(".", " ")
            content = (tool.outcome or "").lower()
            capability = tool.capability.lower()

            if (
                self._overlaps(words, name)
                or self._overlaps(words, content)
                or self._overlaps(words, capability)
            ):
                matched.append(tool)

        return matched

    def summary(self) -> dict:
        """Counts only - the diagnostics form of the ledger."""

        return {
            "request_id": self.request_id,
            "tools": len(self.tools),
            "memories": len(self.memories),
            "capabilities": len(self.capabilities),
            "claims": len(self.claims),
        }


def _status_from_error(code: str) -> str:
    """
    The ToolStatus an envelope's error code implies, deterministically.

    Envelopes carry `ok=False` plus an error code, not a status. The
    code's Phase 3 category decides: a capability refusal is UNAVAILABLE
    (nothing ran), a permission or policy refusal is DENIED, malformed
    arguments are INVALID_ARGUMENTS, a timeout is TIMEOUT - and anything
    unrecognised is FAILED, never SUCCESS, because `ok=False` already
    establishes that the action did not succeed.
    """

    category = category_for_code(code)

    if category is ToolErrorCategory.CAPABILITY:
        return ToolStatus.UNAVAILABLE.value

    if category in (ToolErrorCategory.PERMISSION, ToolErrorCategory.POLICY):
        return ToolStatus.DENIED.value

    if category is ToolErrorCategory.VALIDATION:
        return ToolStatus.INVALID_ARGUMENTS.value

    if category is ToolErrorCategory.TIMEOUT:
        return ToolStatus.TIMEOUT.value

    return ToolStatus.FAILED.value


def ledger_from_transcript(
    messages: list[dict], request_id: str = ""
) -> EvidenceLedger:
    """
    Build an evidence ledger from an agent-run transcript (Phase 4.5).

    The AgentRuntime transcript carries one `role="tool"` message per
    tool round whose content is the JSON list of structured envelopes
    (`agent.runtime._build_envelope`). This converts those envelopes
    into the same ToolEvidence records the conversation path records, so
    a run's final reply can be verified against exactly what the loop
    executed - never against prose.

    Status mapping, total and deterministic:

    - `ok: true`  -> SUCCESS, with the postcondition as tri-state
      POSTCONDITION evidence when the envelope reported one (verified
      True/False straight from the envelope), or RETURN_VALUE evidence
      with verified=None when it did not - an unverified success, which
      is exactly what the rules treat it as.
    - `ok: false` -> `_status_from_error` of the error code, and no
      evidence (nothing established, the failure itself is the fact).

    Envelopes that are not dicts, or JSON that fails to parse, are
    skipped rather than guessed at: a ledger entry invented from noise
    would be model-text evidence by another door.
    """

    ledger = EvidenceLedger(request_id=request_id)

    for message in messages or []:

        if not isinstance(message, dict) or message.get("role") != "tool":
            continue

        try:
            envelopes = json.loads(message.get("content") or "[]")
        except ValueError:
            continue

        if not isinstance(envelopes, list):
            continue

        for envelope in envelopes:

            if not isinstance(envelope, dict):
                continue

            tool = str(envelope.get("tool") or "")
            call_id = str(envelope.get("tool_call_id") or "")

            if not tool:
                continue

            if not envelope.get("ok"):
                error = envelope.get("error") or {}
                status = _status_from_error(str(error.get("code") or ""))
                ledger.add_tool(tool=tool, status=status, evidence=())
                continue

            postcondition = envelope.get("postcondition")

            if isinstance(postcondition, dict):
                evidence = (
                    Evidence(
                        kind=EvidenceKind.POSTCONDITION,
                        source="agent",
                        verified=bool(postcondition.get("verified")),
                        reference=call_id,
                    ),
                )
            else:
                evidence = (
                    Evidence(
                        kind=EvidenceKind.RETURN_VALUE,
                        source="agent",
                        verified=None,
                        reference=call_id,
                    ),
                )

            result = envelope.get("result")
            outcome = (
                json.dumps(result, ensure_ascii=False, default=str)[:240]
                if result not in (None, {})
                else ""
            )

            ledger.add_tool(
                tool=tool,
                status=ToolStatus.SUCCESS.value,
                evidence=evidence,
                outcome=outcome,
            )

    return ledger


__all__ = [
    "EvidenceLedger",
    "ToolEvidence",
    "MemoryEvidence",
    "CapabilityEvidence",
    "ledger_from_transcript",
]

__all__ = [
    "EvidenceLedger",
    "ToolEvidence",
    "MemoryEvidence",
    "CapabilityEvidence",
]