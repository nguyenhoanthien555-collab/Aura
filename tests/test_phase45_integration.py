"""
Phase 4.5 integration tests: does the claim -> evidence boundary
actually protect the final user-visible response in the real runtime?

The Phase 4 suite (tests/test_response_verifier.py) is unit-level: it
builds ledgers by hand. This file exercises the verifier through the
real objects the runtime uses - the real ConversationManager with a real
ToolExecutor and real ToolResults, the real streaming generator, the
real AgentRuntime transcript - with deterministic fake providers only
where a network would otherwise be. Nothing here proves real provider
behaviour; it proves the wiring.

Sections:
    1. chat integration       failed tool -> repaired reply, through the
                              real ConversationManager + ToolExecutor
    2. streaming integration  fragments stream raw; the authoritative
                              finished event carries the verified text
    3. agent runtime          the /api/agent/intent reply is verified
                              against the run's own structured envelopes
    4. adversarial matrix     the contract's eight sentences x nine
                              evidence states, deterministic expectations
    5. memory claims          provenance, conflict, semantic-overclaim
    6. general knowledge      ordinary questions stay ordinary
    7. diagnostics privacy    the verifier trace carries no content
    8. performance            extraction / verification / repair measured
"""

import json
import time

import pytest

from agent.runtime import AgentRuntime, StopReason
from brain.conversation import ConversationManager
from brain.native_fc import ModelTurn, ToolCallRequest
from brain.prompt_builder import PromptBuilder
from brain.response import Response
from brain.verify import ResponseVerifier, verify_run_reply
from brain.verify.claims import extract_claims
from brain.verify.ledger import EvidenceLedger, ledger_from_transcript
from brain.verify.repair import repair_claims
from brain.verify.rules import verify_claim
from brain.verify.status import ClaimState, VerifierDecision
from tools.base import Tool, ToolResult, ToolRisk, fail, ok
from tools.executor import ToolExecutor, ToolPolicy
from tools.outcome import Evidence, EvidenceKind, ToolStatus
from tools.registry import ToolRegistry


# ----------------------------------------------------------------------
# Doubles - deterministic by construction, no network anywhere
# ----------------------------------------------------------------------

def _no_capabilities(words):
    """A registry that knows nothing. Never matches, never assumes."""
    return "UNKNOWN", False, ""


class FakeStore:
    def __init__(self):
        self.saved = []

    def save(self, role, content):
        self.saved.append((role, content))

    def get_recent(self, limit=10):
        return []


class ScriptedLLM:
    """The chat-shape fake: one reply per generate call."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.replies.pop(0)


class ScriptedStreamLLM:
    """A streaming provider whose fragments are fixed."""

    def __init__(self, *fragments):
        self.fragments = list(fragments)

    def generate(self, prompt: str) -> str:
        return "".join(self.fragments)

    def stream(self, prompt: str):
        yield from self.fragments


class RecordingBus:
    """The EventPublisher port, recording."""

    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)


class FailingEmailTool(Tool):
    """A real tool that really fails, with a real Phase 3 ToolResult."""

    name = "email.send"
    capability = "test.email"
    description = "Send an email"
    risk = ToolRisk.SAFE

    def execute(self, **kwargs) -> ToolResult:
        return fail("SMTP connection refused", tool=self.name)


class OkEmailTool(Tool):
    """A real tool that succeeds - but verifies nothing."""

    name = "email.send"
    capability = "test.email"
    description = "Send an email"
    risk = ToolRisk.SAFE

    def execute(self, **kwargs) -> ToolResult:
        return ok("email handed to the mail server", tool=self.name)


def make_executor(tool):
    from core.capabilities import registry as capability_registry
    from core.capabilities.models import Capability

    capability_registry.register(
        Capability(
            capability_id="test.email",
            name="Test email",
            description="",
            category="test",
        )
    )

    registry = ToolRegistry([tool()])
    return ToolExecutor(
        registry=registry,
        policy=ToolPolicy(
            enabled=True,
            allowed=frozenset({tool.name}),
            auto_approve=frozenset({ToolRisk.SAFE}),
        ),
    )


def make_manager(llm, tools, verifier=None):
    return ConversationManager(
        memory=FakeStore(),
        builder=PromptBuilder(),
        llm=llm,
        tools=tools,
        verifier=verifier or ResponseVerifier(
            capability_provider=_no_capabilities
        ),
    )


def tool_call(name, **arguments):
    return ModelTurn(tool_calls=(
        ToolCallRequest(
            call_id="call_test00001", name=name, arguments=arguments
        ),
    ))


def text_turn(body):
    return ModelTurn(text=body)


# ----------------------------------------------------------------------
# 1. Chat integration - real manager, real executor, real ToolResult
# ----------------------------------------------------------------------

def test_a_failed_tool_cannot_reach_the_user_as_success():
    """The end-to-end shape of the original hallucination class."""

    llm = ScriptedLLM(
        json.dumps({"tool": "email.send", "arguments": {"to": "alex"}}),
        "I sent the email successfully. Python is a programming language.",
    )
    manager = make_manager(llm, make_executor(FailingEmailTool))

    response = manager.chat("send an email to alex")

    assert isinstance(response, Response)
    # The failed action claim did not survive as fact...
    assert "successfully" not in response.text
    assert "I sent the email" not in response.text
    # ...and the ordinary knowledge in the same reply was left alone.
    assert "Python is a programming language." in response.text
    # The verifier metadata says what happened, counts only.
    assert response.verifier["decision"] == VerifierDecision.REPAIR.value
    assert response.verifier["repairs"] == 1


def test_a_success_without_postcondition_is_delivered_as_inference():
    """SUCCESS + RETURN_VALUE is INFERRED - qualified, never asserted."""

    llm = ScriptedLLM(
        json.dumps({"tool": "email.send", "arguments": {"to": "alex"}}),
        "I sent the email successfully.",
    )
    manager = make_manager(llm, make_executor(OkEmailTool))

    response = manager.chat("send an email to alex")

    assert response.verifier["decision"] == VerifierDecision.REPAIR.value
    # Minimal repair: the sentence is qualified, not replaced with an
    # invention, and nothing new is claimed.
    assert "email" in response.text


def test_a_verified_tool_result_leaves_a_matching_claim_alone():
    """SUCCESS + verified postcondition is the only path to VERIFIED."""

    llm = ScriptedLLM(
        json.dumps({"tool": "email.send", "arguments": {"to": "alex"}}),
        "I sent the email successfully.",
    )

    class VerifiedEmailTool(OkEmailTool):
        def verify(self, **kwargs):
            return True

    manager = make_manager(llm, make_executor(VerifiedEmailTool))

    response = manager.chat("send an email to alex")

    assert response.text == "I sent the email successfully."
    assert response.verifier["decision"] == VerifierDecision.PASS.value


def test_without_a_verifier_the_pipeline_is_exactly_as_before():
    """`verifier=None` must restore the pre-Phase-4 behaviour."""

    llm = ScriptedLLM(
        json.dumps({"tool": "email.send", "arguments": {"to": "alex"}}),
        "I sent the email successfully.",
    )
    manager = ConversationManager(
        memory=FakeStore(),
        builder=PromptBuilder(),
        llm=llm,
        tools=make_executor(FailingEmailTool),
    )

    response = manager.chat("send an email to alex")

    assert response.text == "I sent the email successfully."
    assert response.verifier is None


# ----------------------------------------------------------------------
# 2. Streaming integration
# ----------------------------------------------------------------------

def test_streaming_sends_raw_fragments_then_an_authoritative_replacement():
    """
    The honest streaming boundary, pinned: fragments go out raw (a false
    claim CAN momentarily reach a UI), and the finished event carries the
    verified, repaired text plus verifier metadata. This is NOT verified
    streaming, and the test refuses to pretend it is.
    """

    # Fragments were yielded raw, before any verification could run.
    llm = ScriptedStreamLLM("I sent", " the email", " successfully.")
    manager = make_manager(llm, None)

    fragments = list(manager.chat_stream("send an email"))
    assert "".join(fragments) == "I sent the email successfully."

    bus = RecordingBus()
    manager = make_manager(llm, None)
    manager.events = bus
    list(manager.chat_stream("send an email"))

    finished = [
        event for event in bus.events
        if type(event).__name__ == "StreamFinishedEvent"
    ]
    assert len(finished) == 1

    event = finished[0]
    # The authoritative text is the repaired one...
    assert event.text != "I sent the email successfully."
    assert "successfully" not in event.text
    # ...and the verifier metadata rode with it, content-free.
    assert event.verifier["decision"] == VerifierDecision.REPAIR.value


# ----------------------------------------------------------------------
# 3. Agent runtime - the /api/agent/intent reply against its transcript
# ----------------------------------------------------------------------

def make_agent_runtime(turns, tool):
    from core.capabilities import registry as capability_registry
    from core.capabilities.models import Capability

    capability_registry.register(
        Capability(
            capability_id="test.email",
            name="Test email",
            description="",
            category="test",
        )
    )

    registry = ToolRegistry([tool()])
    executor = ToolExecutor(
        registry=registry,
        policy=ToolPolicy(
            enabled=True,
            allowed=frozenset({tool.name}),
            auto_approve=frozenset({ToolRisk.SAFE}),
        ),
    )

    class AgentLLM:
        def __init__(self, turns):
            self.turns = list(turns)

        def generate_with_tools(self, system, messages, tools):
            return self.turns.pop(0)

    return AgentRuntime(
        llm=AgentLLM(turns),
        executor=executor,
        registry=registry,
        system_prompt="You are Aura's device agent.",
        clock=lambda: 1000.0,
    )


def test_an_agent_run_that_failed_its_tool_cannot_claim_success():
    """
    The loop's own stop reason does not catch one failed tool under a
    success sentence (that gate is for mutating risks). Phase 4.5 closes
    exactly this gap: the reply is verified against the run's envelopes
    before it leaves the route.
    """

    runtime = make_agent_runtime(
        [
            tool_call("email.send", to="alex"),
            text_turn("I sent the email to Alex successfully."),
        ],
        FailingEmailTool,
    )

    run = runtime.run_to_completion(
        runtime.start_run("send an email", "session_phase4500001")
    )

    # The loop itself stopped on its own terms...
    assert run.stop_reason is StopReason.GOAL_VERIFIED

    reply = ""
    for message in reversed(run.messages):
        if message.get("role") == "assistant" and not message.get("tool_calls"):
            reply = message["content"].strip()
            break

    result = verify_run_reply(reply, run.messages, request_id=run.run_id)

    # ...but the reply does not get to call the failure a success.
    assert result.decision is VerifierDecision.REPAIR
    assert result.changed
    assert "successfully" not in result.repaired_text
    # And the metadata a client would see names the repair, not the text.
    assert result.counts["contradicted"] == 1


def test_the_transcript_ledger_maps_envelopes_deterministically():
    """ok/error-code/postcondition -> ToolStatus + Evidence, no guessing."""

    ledger = ledger_from_transcript(
        [
            {"role": "tool", "content": json.dumps([
                {
                    "tool": "email.send", "tool_call_id": "c1", "ok": False,
                    "error": {"code": "CAPABILITY_UNAVAILABLE", "message": "x"},
                },
                {
                    "tool": "file.delete", "tool_call_id": "c2", "ok": False,
                    "error": {"code": "PERMISSION_DENIED", "message": "x"},
                },
                {
                    "tool": "calendar.read", "tool_call_id": "c3", "ok": True,
                    "postcondition": {"verified": True},
                },
                {
                    "tool": "email.send", "tool_call_id": "c4", "ok": True,
                    "result": {"queued": True},
                },
            ])},
            {"role": "assistant", "content": "not evidence"},
            {"role": "tool", "content": "not json at all"},
        ],
        request_id="run_test",
    )

    # CAPABILITY_UNAVAILABLE is UNAVAILABLE, never FAILED and never
    # SUCCESS; a permission refusal is DENIED; a verified postcondition
    # is VERIFIED evidence; a bare return value is UNVERIFIED.
    assert ledger.tools[0].status == ToolStatus.UNAVAILABLE.value
    assert ledger.tools[1].status == ToolStatus.DENIED.value
    assert ledger.tools[2].status == ToolStatus.SUCCESS.value
    assert ledger.tools[2].state == "VERIFIED"
    assert ledger.tools[3].state == "UNVERIFIED"
    # The prose message and the unparseable one contributed nothing.
    assert len(ledger.tools) == 4
    assert len(ledger.memories) == 0


# ----------------------------------------------------------------------
# 4. Adversarial matrix - contract sentences x evidence states
# ----------------------------------------------------------------------

_EVIDENCE_STATES = (
    "none", "verified", "unverified", "unknown", "failed",
    "denied", "unavailable", "contradicted", "partial",
)


def _evidence_for(label):
    if label == "verified":
        return (
            Evidence(kind=EvidenceKind.POSTCONDITION, source="matrix",
                     verified=True, detail="re-read after acting"),
        )
    if label == "unverified":
        return (
            Evidence(kind=EvidenceKind.RETURN_VALUE, source="matrix",
                     verified=None),
        )
    if label == "contradicted":
        return (
            Evidence(kind=EvidenceKind.POSTCONDITION, source="matrix",
                     verified=False, detail="re-read said otherwise"),
        )
    return ()


def _status_for(label):
    return {
        "none": None, "verified": "SUCCESS", "unverified": "SUCCESS",
        "unknown": "UNKNOWN", "failed": "FAILED", "denied": "DENIED",
        "unavailable": "UNAVAILABLE", "contradicted": "SUCCESS",
        "partial": "PARTIAL",
    }[label]


# sentence -> (tool whose name/outcome the claim's words can match,
#              outcome line carrying the same subject)
_SENTENCES = [
    ("I sent the email successfully.", "email.send", "email to alex"),
    ("I deleted the file.", "file.delete", "file removed"),
    ("I checked your calendar.", "calendar.read", "calendar events listed"),
    ("Your phone has WhatsApp installed.", "phone.apps", "whatsapp installed"),
    ("The operation completed successfully.", "task.run", "operation ran"),
    ("The recipient received the message.", "message.send", "message delivered"),
    ("You currently have 3 appointments.", "calendar.read", "3 appointments"),
    ("I verified that the setting changed.", "settings.change", "setting updated"),
]


def _ledger_for(sentence, tool, outcome, label):
    ledger = EvidenceLedger(request_id="matrix")

    status = _status_for(label)

    if status is not None:
        ledger.add_tool(
            tool=tool,
            status=status,
            evidence=_evidence_for(label),
            outcome=outcome,
        )

    return ledger


def _expected_state(label):
    """The claim state the rules owe each evidence state. Deterministic."""

    return {
        "none": ClaimState.UNKNOWN,
        "verified": ClaimState.VERIFIED,
        "unverified": ClaimState.INFERRED,
        "unknown": ClaimState.UNKNOWN,
        "failed": ClaimState.CONTRADICTED,
        "denied": ClaimState.CONTRADICTED,
        "unavailable": ClaimState.CONTRADICTED,
        "contradicted": ClaimState.CONTRADICTED,
        "partial": ClaimState.CONTRADICTED,
    }[label]


@pytest.mark.parametrize("sentence,tool,outcome", _SENTENCES)
def test_every_contract_sentence_is_graded_deterministically(
    sentence, tool, outcome,
):
    """
    For every contract sentence, every evidence state produces exactly
    the state the rules table owes - nothing about the phrasing moves a
    claim up, and nothing about a failure lets it stand as success.
    """

    claims = extract_claims(sentence)
    assert len(claims) == 1, sentence

    for label in _EVIDENCE_STATES:
        ledger = _ledger_for(sentence, tool, outcome, label)
        claim = claims[0]
        verify_claim(claim, ledger, _no_capabilities)

        assert claim.state is _expected_state(label), (
            f"{sentence!r} under {label}: got {claim.state}"
        )

        if claim.state in (ClaimState.UNKNOWN, ClaimState.CONTRADICTED):
            assert claim.hallucination, (
                f"{sentence!r} under {label}: unclassified overclaim"
            )


@pytest.mark.parametrize("sentence,tool,outcome", _SENTENCES)
def test_matrix_repair_behavior_is_deterministic(sentence, tool, outcome):
    """
    The delivery behaviour per state: ungrounded or contradicted claims
    are repaired, verified ones are delivered unchanged, and no repair
    ever invents content that was not in the reply or the evidence.
    """

    for label in _EVIDENCE_STATES:
        ledger = _ledger_for(sentence, tool, outcome, label)

        claims = extract_claims(sentence)
        for claim in claims:
            verify_claim(claim, ledger, _no_capabilities)

        repaired, repairs = repair_claims(sentence, claims)

        expected_state = _expected_state(label)

        if expected_state is ClaimState.VERIFIED:
            assert repaired == sentence, (sentence, label)
            assert not repairs
        else:
            # An ungrounded, unverified or contradicted claim is never
            # delivered exactly as it was written.
            assert repaired != sentence, (
                f"{sentence!r} under {label} was delivered ungrounded"
            )
            assert repaired.strip()

        if label == "none":
            # Even the "no evidence" repair must not have invented an
            # outcome; the hedge is about verification, not facts.
            assert "sent successfully" not in repaired


# ----------------------------------------------------------------------
# 5. Memory claims
# ----------------------------------------------------------------------

def test_recent_high_confidence_memory_is_supported_not_verified():
    """Memory can support; only fresh evidence can verify."""

    ledger = EvidenceLedger(request_id="mem")
    ledger.add_memory(
        line="your phone has whatsapp installed",
        recency="recent",
        confidence="high",
        source="user",
    )

    claims = extract_claims("Your phone has WhatsApp installed.")
    for claim in claims:
        verify_claim(claim, ledger, _no_capabilities)

    assert all(claim.state is not ClaimState.VERIFIED for claim in claims)


def test_stale_unknown_provenance_memory_is_attributed_not_asserted():
    ledger = EvidenceLedger(request_id="mem2")
    ledger.add_memory(line="your phone has whatsapp installed")

    claims = extract_claims("Your phone has WhatsApp installed.")
    for claim in claims:
        verify_claim(claim, ledger, _no_capabilities)

    repaired, repairs = repair_claims(
        "Your phone has WhatsApp installed.", claims
    )

    assert repairs, "old memory was asserted as plain fact"
    assert "remember" in repaired.lower()


def test_conflicting_memories_surface_instead_of_picking_a_side():
    ledger = EvidenceLedger(request_id="mem3")
    ledger.add_memory(line="you have 3 appointments tomorrow")
    ledger.add_memory(line="you have 5 appointments tomorrow")

    claims = extract_claims("You currently have 3 appointments.")
    for claim in claims:
        verify_claim(claim, ledger, _no_capabilities)

    repaired, repairs = repair_claims(
        "You currently have 3 appointments.", claims
    )

    assert any(claim.conflict for claim in claims)
    # The conflict is never silently settled - but the delivered text is
    # attributed to memory rather than asserted as fact.
    assert repaired.startswith("From what I remember")
    assert repairs


def test_semantically_similar_memory_cannot_overrule_a_number():
    """Similarity is never verification; a disagreeing number is caught."""

    ledger = EvidenceLedger(request_id="mem4")
    ledger.add_memory(line="you have 5 appointments tomorrow")

    claims = extract_claims("You currently have 3 appointments.")
    for claim in claims:
        verify_claim(claim, ledger, _no_capabilities)

    assert any(
        claim.state is ClaimState.CONTRADICTED for claim in claims
    )


# ----------------------------------------------------------------------
# 6. General knowledge stays usable
# ----------------------------------------------------------------------

@pytest.mark.parametrize("answer", [
    "Python is a programming language. It was created by Guido van Rossum.",
    "HTTP is the protocol browsers use to talk to servers.",
    "Recursion is when a function calls itself until it reaches a base case.",
    "The sky is blue because air scatters shorter wavelengths of light more.",
])
def test_general_knowledge_answers_pass_through_unchanged(answer):
    ledger = EvidenceLedger(request_id="know")

    claims = extract_claims(answer)
    for claim in claims:
        verify_claim(claim, ledger, _no_capabilities)

    assert all(
        claim.state is None for claim in claims
    ), "ordinary knowledge was dragged into the evidence machinery"

    repaired, repairs = repair_claims(answer, claims)
    assert repaired == answer
    assert not repairs


# ----------------------------------------------------------------------
# 7. Diagnostics privacy
# ----------------------------------------------------------------------

def test_the_verifier_trace_line_carries_no_content(monkeypatch, tmp_path):
    """Metadata only: counts, decision, latency. Never claim text."""

    from core import trace

    path = tmp_path / "diagnostics.jsonl"
    monkeypatch.setattr(trace, "TRACE_FILE", path)
    trace._logger.handlers.clear()

    try:
        ledger = EvidenceLedger(request_id="privacy")
        ledger.add_tool(
            tool="email.send",
            status=ToolStatus.FAILED.value,
            evidence=(),
            outcome="email to alex@example.com with the API key sk-live-abc",
        )

        secret_reply = (
            "I sent the email to alex@example.com successfully. "
            "Your password is hunter2."
        )

        verifier = ResponseVerifier(capability_provider=_no_capabilities)
        verifier.verify(secret_reply, ledger)
    finally:
        for handler in list(trace._logger.handlers):
            handler.flush()
            handler.close()
            trace._logger.removeHandler(handler)

    assert path.exists()

    records = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    verifier_records = [r for r in records if r.get("kind") == "verifier"]
    assert verifier_records, "no verifier trace was written"

    blob = json.dumps(verifier_records)
    for forbidden in (
        "alex@example.com", "hunter2", "sk-live-abc",
        "I sent the email", "password",
    ):
        assert forbidden not in blob, (
            "the verifier trace leaked response or evidence content"
        )

    record = verifier_records[0]
    for field in ("decision", "claims", "latency_ms"):
        assert field in record
    assert "text" not in record


# ----------------------------------------------------------------------
# 8. Performance - measured, and still no second model
# ----------------------------------------------------------------------

def test_verification_stages_are_measured_and_cheap():
    """
    Extraction, verification and repair are timed separately on a
    representative reply. Bounds are generous (a slow CI box must not
    flake) but the stages are real timers, not claims.
    """

    reply = (
        "I sent the email successfully. Your phone has WhatsApp installed. "
        "You currently have 3 appointments. I deleted the file. "
        "Python is a programming language. The operation completed "
        "successfully."
    )

    ledger = EvidenceLedger(request_id="perf")

    start = time.perf_counter()
    claims = extract_claims(reply)
    extraction_ms = (time.perf_counter() - start) * 1000

    start = time.perf_counter()
    for claim in claims:
        verify_claim(claim, ledger, _no_capabilities)
    verify_ms = (time.perf_counter() - start) * 1000

    start = time.perf_counter()
    repair_claims(reply, claims)
    repair_ms = (time.perf_counter() - start) * 1000

    assert claims, "nothing was extracted"
    assert extraction_ms < 50, f"extraction took {extraction_ms:.1f} ms"
    assert verify_ms < 50, f"verification took {verify_ms:.1f} ms"
    assert repair_ms < 50, f"repair took {repair_ms:.1f} ms"





