"""
Phase 4: claim -> evidence response verification.

The cases are organised the way the contract is: the required behaviour
table first, then the adversarial matrix (one response class crossed with
every evidence state), then the two things that must NOT change - ordinary
conversation, and the function-calling failure vocabulary Phase 1 built.

Every assertion here is about the verifier's own output. Nothing in this
file talks to a model, a device or a network, which is the point: the
boundary is deterministic or it is not a boundary.
"""

import pytest

from tools.outcome import Evidence, EvidenceKind, ToolStatus

from brain.verify import ResponseVerifier
from brain.verify.claims import ClaimType, extract_claims
from brain.verify.hallucinations import HallucinationType
from brain.verify.ledger import EvidenceLedger
from brain.verify.status import ClaimState, VerifierDecision


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------

def _no_capabilities(words):
    """A registry that knows nothing. Never matches, never assumes."""

    return "UNKNOWN", False, ""


def _capability(state: str, capability_id: str = "device.sms"):
    """A registry that resolves every claim to one capability state."""

    def provider(words):
        return state, True, capability_id

    return provider


@pytest.fixture
def verifier():
    return ResponseVerifier(capability_provider=_no_capabilities)


@pytest.fixture
def ledger():
    return EvidenceLedger(request_id="test-request")


def _verified_evidence():
    return (
        Evidence(
            kind=EvidenceKind.POSTCONDITION,
            source="test",
            verified=True,
            detail="re-read after acting",
        ),
    )


def _unverified_evidence():
    return (
        Evidence(
            kind=EvidenceKind.RETURN_VALUE,
            source="test",
            verified=None,
        ),
    )


def _contradicted_evidence():
    return (
        Evidence(
            kind=EvidenceKind.POSTCONDITION,
            source="test",
            verified=False,
            detail="postcondition came back false",
        ),
    )


# ----------------------------------------------------------------------
# Claim extraction
# ----------------------------------------------------------------------

class TestExtraction:

    def test_an_action_claim_needs_a_verb_and_a_world_object(self):
        """
        The scope gate. Both sentences carry a side-effect verb; only one
        of them claims a side effect on the user's world.
        """

        grounded = extract_claims("I checked your calendar.")[0]
        prose = extract_claims("I checked the docs.")[0]

        assert grounded.type == ClaimType.ACTION
        assert grounded.world is True

        assert prose.type != ClaimType.ACTION
        assert prose.world is False

    def test_plural_world_objects_are_in_scope(self):
        """
        `\\bmail\\b` cannot match inside "emails". The most common noun in
        the vocabulary must not fall out of scope by pluralising.
        """

        assert extract_claims("You have three unread emails.")[0].world
        assert extract_claims("I deleted the files.")[0].world

    def test_a_verb_inside_a_longer_word_is_not_a_verb(self):
        """
        "set" lives inside "settings" and "sent" inside "present". Word
        boundaries, never substring guessing.
        """

        claim = extract_claims("I settled on the simpler approach.")[0]

        assert claim.type != ClaimType.ACTION

    def test_ordinary_prose_is_general_and_unstated(self):
        """
        GENERAL claims keep `state is None`. "We did not evaluate this"
        and "we found no evidence" are different facts.
        """

        claims = extract_claims("That is a great question.")

        assert claims[0].type == ClaimType.GENERAL
        assert claims[0].state is None

    def test_sentences_split_without_breaking_versions_or_initials(self):
        claims = extract_claims("Python 3.13 is out. Ask Dr. Smith about it.")

        assert len(claims) == 2

    def test_vietnamese_action_claims_are_extracted(self):
        claim = extract_claims("Tôi đã gửi tin nhắn cho bạn.")[0]

        assert claim.type == ClaimType.ACTION
        assert claim.world is True


# ----------------------------------------------------------------------
# The required behaviour table
# ----------------------------------------------------------------------

class TestActionClaims:

    def test_no_evidence_is_never_a_success(self, verifier, ledger):
        result = verifier.verify("I sent the email.", ledger)

        claim = result.claims[0]

        assert claim.state is ClaimState.UNKNOWN
        assert claim.hallucination == (
            HallucinationType.UNSUPPORTED_ACTION.value
        )
        assert result.changed
        assert "can't verify" in result.repaired_text

    def test_success_with_verified_evidence_is_verified_and_untouched(
        self, verifier, ledger,
    ):
        ledger.add_tool(
            tool="email.send",
            status=ToolStatus.SUCCESS.value,
            evidence=_verified_evidence(),
            outcome="email sent to alex",
        )

        result = verifier.verify("I sent the email.", ledger)

        assert result.claims[0].state is ClaimState.VERIFIED
        assert result.decision is VerifierDecision.PASS
        assert result.repaired_text == "I sent the email."

    def test_success_without_verification_is_inferred_and_qualified(
        self, verifier, ledger,
    ):
        """
        The call returned; nothing confirmed the effect. That is INFERRED,
        and the reply says so rather than asserting the effect.
        """

        ledger.add_tool(
            tool="email.send",
            status=ToolStatus.SUCCESS.value,
            evidence=_unverified_evidence(),
            outcome="email queued",
        )

        result = verifier.verify("I sent the email.", ledger)

        assert result.claims[0].state is ClaimState.INFERRED
        assert result.changed
        assert result.repaired_text.startswith("As far as I can tell")

    @pytest.mark.parametrize(
        "status",
        [
            ToolStatus.FAILED.value,
            ToolStatus.DENIED.value,
            ToolStatus.UNAVAILABLE.value,
        ],
    )
    def test_a_failed_tool_contradicts_a_claimed_success(
        self, verifier, ledger, status,
    ):
        ledger.add_tool(
            tool="email.send",
            status=status,
            evidence=_contradicted_evidence(),
            outcome="email not sent",
        )

        result = verifier.verify("I sent the email.", ledger)

        claim = result.claims[0]

        assert claim.state is ClaimState.CONTRADICTED
        assert claim.hallucination == (
            HallucinationType.FABRICATED_TOOL_RESULT.value
        )
        assert "sent" not in result.repaired_text.lower()

    def test_partial_is_contradicted_but_not_repaired_as_a_failure(
        self, verifier, ledger,
    ):
        """
        The one contradiction where something DID happen. Repairing a
        partial success into "that did not happen" would be a false claim
        in the opposite direction.
        """

        ledger.add_tool(
            tool="email.send",
            status=ToolStatus.PARTIAL.value,
            evidence=_unverified_evidence(),
            outcome="2 of 3 emails sent",
        )

        result = verifier.verify("I sent the email.", ledger)

        assert result.claims[0].state is ClaimState.CONTRADICTED
        assert "partly" in result.repaired_text
        assert "did not happen" not in result.repaired_text

    def test_unknown_tool_status_stays_unknown(self, verifier, ledger):
        ledger.add_tool(
            tool="email.send",
            status=ToolStatus.UNKNOWN.value,
            outcome="email state unclear",
        )

        result = verifier.verify("I sent the email.", ledger)

        assert result.claims[0].state is ClaimState.UNKNOWN
        assert result.claims[0].state is not ClaimState.VERIFIED

    def test_evidence_for_one_tool_does_not_ground_another_claim(
        self, verifier, ledger,
    ):
        """
        A verified clock reading is not evidence that an email was sent.
        """

        ledger.add_tool(
            tool="clock.now",
            status=ToolStatus.SUCCESS.value,
            evidence=_verified_evidence(),
            outcome="15:04",
        )

        result = verifier.verify("I sent the email.", ledger)

        assert result.claims[0].state is ClaimState.UNKNOWN


class TestCapabilityClaims:

    def test_an_available_capability_supports_the_claim(self, ledger):
        verifier = ResponseVerifier(
            capability_provider=_capability("AVAILABLE")
        )

        result = verifier.verify("I can send SMS on your phone.", ledger)

        assert result.claims[0].state is ClaimState.SUPPORTED
        assert result.decision is VerifierDecision.PASS

    @pytest.mark.parametrize(
        "state",
        ["NOT_IMPLEMENTED", "BLOCKED_PERMISSION", "UNHEALTHY", "UNAVAILABLE"],
    )
    def test_claiming_an_unavailable_capability_is_contradicted(
        self, ledger, state,
    ):
        verifier = ResponseVerifier(capability_provider=_capability(state))

        result = verifier.verify("I can send SMS on your phone.", ledger)

        claim = result.claims[0]

        assert claim.state is ClaimState.CONTRADICTED
        assert claim.hallucination == (
            HallucinationType.FABRICATED_CAPABILITY.value
        )
        assert result.changed

    def test_denying_an_unavailable_capability_is_supported(self, ledger):
        """
        The inversion. "I can't do that" is the honest sentence when the
        registry agrees, and must not be rewritten.
        """

        verifier = ResponseVerifier(
            capability_provider=_capability("NOT_IMPLEMENTED")
        )

        result = verifier.verify(
            "I can't send SMS on your phone.", ledger
        )

        assert result.claims[0].state is ClaimState.SUPPORTED
        assert result.repaired_text == "I can't send SMS on your phone."

    def test_a_conversational_offer_is_not_a_capability_claim(
        self, verifier, ledger,
    ):
        """
        Rule 6. "I can help you with that" names nothing in the user's
        world; hedging it would make ordinary chat unusable.
        """

        result = verifier.verify("I can help you with that.", ledger)

        assert not result.changed
        assert result.decision is VerifierDecision.PASS

    def test_a_registry_outage_degrades_to_unknown(self, ledger):
        def exploding(words):
            raise RuntimeError("registry down")

        verifier = ResponseVerifier(capability_provider=exploding)

        result = verifier.verify("I can send SMS on your phone.", ledger)

        assert result.claims[0].state is ClaimState.UNKNOWN


class TestMemoryClaims:

    def test_a_recent_high_confidence_memory_supports(self, verifier, ledger):
        ledger.add_memory(
            line="Your meeting with Alex is on Friday.",
            recency="recent",
            confidence="high",
            source="user",
        )

        result = verifier.verify(
            "Your meeting with Alex is on Friday.", ledger
        )

        assert result.claims[0].state is ClaimState.SUPPORTED
        assert not result.changed

    def test_an_old_memory_only_infers_and_gets_attributed(
        self, verifier, ledger,
    ):
        ledger.add_memory(
            line="Your meeting with Alex is on Friday.",
            recency="old",
            confidence="low",
            source="inferred",
        )

        result = verifier.verify(
            "Your meeting with Alex is on Friday.", ledger
        )

        assert result.claims[0].state is ClaimState.INFERRED
        assert result.repaired_text.startswith("From what I remember")

    def test_memory_with_no_provenance_is_read_conservatively(
        self, verifier, ledger,
    ):
        """
        Empty provenance is unknown provenance, and unknown is never
        treated as fresh.
        """

        ledger.add_memory(line="Your meeting with Alex is on Friday.")

        result = verifier.verify(
            "Your meeting with Alex is on Friday.", ledger
        )

        assert result.claims[0].state is ClaimState.INFERRED

    def test_a_numeric_mismatch_with_memory_is_contradicted(
        self, verifier, ledger,
    ):
        ledger.add_memory(
            line="You have 2 meetings on Friday.",
            recency="recent",
            confidence="high",
        )

        result = verifier.verify("You have 5 meetings on Friday.", ledger)

        claim = result.claims[0]

        assert claim.state is ClaimState.CONTRADICTED
        assert claim.hallucination == (
            HallucinationType.NUMERICAL_OVERCLAIM.value
        )

    def test_conflicting_memories_ask_rather_than_pick_a_winner(
        self, verifier, ledger,
    ):
        ledger.add_memory(
            line="You have 2 meetings on Friday.",
            recency="recent", confidence="high",
        )
        ledger.add_memory(
            line="You have 4 meetings on Friday.",
            recency="recent", confidence="high",
        )

        result = verifier.verify("You have meetings on Friday.", ledger)

        claim = result.claims[0]

        assert claim.conflict is True
        assert claim.state is ClaimState.UNKNOWN
        assert result.decision in (
            VerifierDecision.ASK_CLARIFICATION, VerifierDecision.REPAIR,
        )

    def test_a_memory_echo_never_grounds_an_action_claim(
        self, verifier, ledger,
    ):
        """
        Rule 4, the sharpest edge of it. A remembered *intention* to send
        an email is not evidence that an email was sent, and must not
        route the claim away from the tool ledger.
        """

        ledger.add_memory(
            line="You wanted me to send the email to Alex.",
            recency="recent", confidence="high",
        )

        result = verifier.verify("I sent the email to Alex.", ledger)

        claim = result.claims[0]

        assert claim.type == ClaimType.ACTION
        assert claim.state is ClaimState.UNKNOWN
        assert result.changed


class TestIdentityAndTemporalClaims:

    def test_a_user_state_claim_with_no_evidence_is_hedged(
        self, verifier, ledger,
    ):
        result = verifier.verify("You have three unread emails.", ledger)

        claim = result.claims[0]

        assert claim.state is ClaimState.UNKNOWN
        assert claim.hallucination == (
            HallucinationType.IDENTITY_OVERCLAIM.value
        )
        assert result.changed

    def test_the_same_shape_outside_the_users_world_is_left_alone(
        self, verifier, ledger,
    ):
        """
        "You have three options" is the same grammar as "you have three
        unread emails" and is not a claim about anything checkable.
        """

        result = verifier.verify("You have three options.", ledger)

        assert not result.changed

    def test_a_current_state_claim_about_the_device_is_hedged(
        self, verifier, ledger,
    ):
        result = verifier.verify(
            "Your phone is currently on silent.", ledger
        )

        assert result.changed
        assert "can't confirm" in result.repaired_text

    def test_a_numeric_claim_disagreeing_with_the_outcome_is_contradicted(
        self, verifier, ledger,
    ):
        ledger.add_tool(
            tool="calendar.list",
            status=ToolStatus.SUCCESS.value,
            evidence=_verified_evidence(),
            outcome="3 events today",
        )

        result = verifier.verify("You have 7 events today.", ledger)

        claim = result.claims[0]

        assert claim.state is ClaimState.CONTRADICTED
        assert claim.hallucination == (
            HallucinationType.NUMERICAL_OVERCLAIM.value
        )


# ----------------------------------------------------------------------
# The adversarial matrix: one action claim x every evidence state
# ----------------------------------------------------------------------

_MATRIX = [
    # (label, status, evidence, forbidden_state)
    ("none", None, (), ClaimState.VERIFIED),
    (
        "verified",
        ToolStatus.SUCCESS.value,
        "verified",
        None,
    ),
    (
        "unverified",
        ToolStatus.SUCCESS.value,
        "unverified",
        ClaimState.VERIFIED,
    ),
    (
        "contradicted-evidence",
        ToolStatus.SUCCESS.value,
        "contradicted",
        ClaimState.VERIFIED,
    ),
    (
        "failed",
        ToolStatus.FAILED.value,
        "contradicted",
        ClaimState.VERIFIED,
    ),
    (
        "denied",
        ToolStatus.DENIED.value,
        (),
        ClaimState.VERIFIED,
    ),
    (
        "unavailable",
        ToolStatus.UNAVAILABLE.value,
        (),
        ClaimState.VERIFIED,
    ),
    (
        "partial",
        ToolStatus.PARTIAL.value,
        "unverified",
        ClaimState.VERIFIED,
    ),
    (
        "unknown",
        ToolStatus.UNKNOWN.value,
        (),
        ClaimState.VERIFIED,
    ),
]

_EVIDENCE_BUILDERS = {
    "verified": _verified_evidence,
    "unverified": _unverified_evidence,
    "contradicted": _contradicted_evidence,
}


@pytest.mark.parametrize(
    "label,status,evidence,forbidden",
    _MATRIX,
    ids=[row[0] for row in _MATRIX],
)
def test_confident_phrasing_never_upgrades_a_claim(
    verifier, label, status, evidence, forbidden,
):
    """
    Rule 5. The same maximally confident sentence is run against every
    evidence state. Nothing about the phrasing may move it up.
    """

    ledger = EvidenceLedger(request_id=f"matrix-{label}")

    if status is not None:
        builder = _EVIDENCE_BUILDERS.get(evidence)
        ledger.add_tool(
            tool="email.send",
            status=status,
            evidence=builder() if builder else (),
            outcome="email to alex",
        )

    result = verifier.verify(
        "I definitely already sent the email to Alex, 100% confirmed.",
        ledger,
    )

    claim = result.claims[0]

    if forbidden is not None:
        assert claim.state is not forbidden, (
            f"{label}: confident phrasing upgraded the claim"
        )

    if claim.state in (ClaimState.UNKNOWN, ClaimState.CONTRADICTED):
        assert result.changed, f"{label}: ungrounded claim was delivered"


def test_the_model_text_is_never_its_own_evidence(verifier, ledger):
    """
    Rule 4. Repeating a claim, at length, with emphasis, adds no evidence.
    """

    result = verifier.verify(
        "I sent the email. I really did send the email. "
        "The email was definitely sent successfully.",
        ledger,
    )

    assert all(
        claim.state is not ClaimState.VERIFIED for claim in result.claims
    )
    assert result.changed


# ----------------------------------------------------------------------
# What must NOT change
# ----------------------------------------------------------------------

_ORDINARY = [
    "Python is a dynamically typed language.",
    "That is a great question.",
    "You have three options here.",
    "Your code looks correct to me.",
    "I think the answer is 42.",
    "Sure, happy to help!",
    "Let me know if you want me to send it.",
    "I can help you with that.",
    "The difference is that lists are mutable and tuples are not.",
    "Đó là một câu hỏi hay.",
]


@pytest.mark.parametrize("text", _ORDINARY)
def test_ordinary_conversation_passes_through_unchanged(verifier, text):
    """
    Rule 6, as a hard assertion. A verifier that makes normal chat
    unusable has not made Aura more trustworthy.
    """

    result = verifier.verify(text, EvidenceLedger())

    assert result.repaired_text == text
    assert result.decision is VerifierDecision.PASS


def test_a_provider_failure_message_survives_verification(verifier):
    """
    Phase 1 regression. The function-calling abort message is a specific,
    true statement about configuration - it must not be collapsed into a
    generic hedge, and it must not be read as a capability claim about the
    user's world.
    """

    text = (
        "No cloud provider is configured or supports function calling, "
        "so I can't run tools right now."
    )

    result = verifier.verify(text, EvidenceLedger())

    assert "function calling" in result.repaired_text
    assert "No cloud provider is configured" in result.repaired_text


def test_an_already_hedged_sentence_is_not_hedged_twice(verifier, ledger):
    text = "I can't verify that the email was actually sent."

    result = verifier.verify(text, ledger)

    assert result.repaired_text == text


def test_repair_only_replaces_the_offending_sentence(verifier, ledger):
    result = verifier.verify(
        "Python is dynamically typed. I sent the email. Anything else?",
        ledger,
    )

    assert "Python is dynamically typed." in result.repaired_text
    assert "Anything else?" in result.repaired_text
    assert "I sent the email." not in result.repaired_text


def test_a_repair_never_names_an_object_the_model_did_not_pair_with_it(
    verifier, ledger,
):
    """
    "I opened the calendar and created two events" once repaired to "the
    calendar was actually created" - a pairing the model never claimed.
    A vague hedge is the lesser harm.
    """

    result = verifier.verify(
        "I opened the calendar and created two events.", ledger
    )

    assert "calendar was actually created" not in result.repaired_text
    assert "can't verify" in result.repaired_text


def test_number_agreement_survives_the_splice(verifier, ledger):
    result = verifier.verify("I changed the settings for you.", ledger)

    assert "settings was" not in result.repaired_text
    assert "settings were" in result.repaired_text


# ----------------------------------------------------------------------
# Decisions, diagnostics, modes, cost
# ----------------------------------------------------------------------

class TestDecisionsAndModes:

    def test_observe_only_mode_classifies_without_rewriting(self, ledger):
        """
        The measurement posture. Every claim is still graded and counted;
        the text is delivered exactly as generated.
        """

        verifier = ResponseVerifier(
            capability_provider=_no_capabilities, repair=False,
        )

        result = verifier.verify("I sent the email.", ledger)

        assert result.repaired_text == "I sent the email."
        assert not result.changed
        assert result.claims[0].state is ClaimState.UNKNOWN
        assert result.counts["unknown"] == 1
        assert result.decision is VerifierDecision.MARK_UNCERTAIN

    def test_an_internal_failure_costs_the_turn_nothing(self, monkeypatch):
        """
        A grounding outage must never cost a conversation: the original
        text passes through and the decision is PASS.
        """

        verifier = ResponseVerifier(capability_provider=_no_capabilities)

        monkeypatch.setattr(
            "brain.verify.verify.extract_claims",
            lambda text: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        result = verifier.verify("I sent the email.", EvidenceLedger())

        assert result.repaired_text == "I sent the email."
        assert result.decision is VerifierDecision.PASS
        assert result.counts == {"error": True}

    def test_counts_separate_in_scope_claims_from_prose(
        self, verifier, ledger,
    ):
        result = verifier.verify(
            "Python is dynamically typed. I sent the email.", ledger
        )

        assert result.counts["claims"] == 2
        assert result.counts["in_scope"] == 1

    def test_the_result_carries_a_per_claim_audit(self, verifier, ledger):
        result = verifier.verify("I sent the email.", ledger)

        payload = result.claims[0].as_dict()

        assert payload["type"] == ClaimType.ACTION
        assert payload["state"] == ClaimState.UNKNOWN.value
        assert payload["repaired"] is True
        assert "sentence" not in payload

    def test_verification_costs_no_model_call_and_little_time(
        self, verifier,
    ):
        """
        Rule 3, measured rather than asserted. A realistic multi-sentence
        reply, verified against a populated ledger, in-process.
        """

        text = (
            "I checked your calendar and sent the email to Alex. "
            "You have three meetings tomorrow. "
            "Python is dynamically typed, which is why that worked. "
            "I can also set a reminder if you want. "
            "Anything else?"
        )

        ledger = EvidenceLedger()
        ledger.add_tool(
            tool="calendar.list",
            status=ToolStatus.SUCCESS.value,
            evidence=_verified_evidence(),
            outcome="3 events tomorrow",
        )
        ledger.add_memory(
            line="Alex is your project lead.",
            recency="recent", confidence="high",
        )

        result = verifier.verify(text, ledger)

        assert result.latency_ms < 100.0, (
            f"verification took {result.latency_ms}ms"
        )
