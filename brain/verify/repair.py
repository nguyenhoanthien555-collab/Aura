"""
Minimal response repair.

The verifier's job is to make the response say *less* than the model
wanted to claim, never *more*. Every repair below therefore either

    - replaces a claim contradicted by evidence with what the evidence
      actually said (the tool failed, was denied, or was unavailable), or
    - hedges a claim that cannot be grounded ("I can't verify ...").

Repairs are minimal by construction: only the offending sentence is
replaced, the rest of the reply is untouched, and nothing is invented.
A repair never adds evidence, never asserts a fact the ledger lacks, and
never moves a claim upward in certainty.

General-knowledge facts (plain UNSUPPORTED_FACT) are intentionally left
alone: verifying "Python 3.13 shipped in 2024" against a tool ledger is
both impossible and pointless, and the contract forbids making ordinary
conversation unusably strict. User-state, current-time and action claims
are the classes that get repaired, because those are the ones where an
unverified statement misleads.
"""

import re

from brain.verify.claims import Claim, ClaimType
from brain.verify.status import ClaimState

# Sentences that already carry an honest qualifier need no repair.
_ALREADY_HEDGED = re.compile(
    r"\b(can't verify|can’t verify|cannot verify|can't confirm|"
    r"can’t confirm|cannot confirm|am not sure|i'm not sure|probably|"
    r"i think|i believe|as far as i know|unclear|not certain|"
    r"không chắc|không thể xác nhận|không rõ)\b",
    re.IGNORECASE,
)


# "the email", "your calendar" - an article followed by one noun.
_ARTICLE_NOUN = re.compile(r"\b(?:the|your|my|a|an)\s+([a-zà-ÿ_]{2,20})")

# Fallback nouns, matched on word boundaries. Substring matching here was
# a real bug: "app" is inside "whatsapp", so "Your phone has WhatsApp
# installed" produced a hedge about "the app".
_FALLBACK_NOUNS = re.compile(
    r"\b(email|message|sms|event|meeting|appointment|file|folder|"
    r"app|application|reminder|task|operation|contact|note|photo|"
    r"screenshot|setting|notification|calendar|alarm|document)\b"
)


def _object_phrase(sentence: str) -> str:
    """
    A short noun phrase from the claim, for a natural hedge.

    "I sent the email" -> "email". No phrase found -> "that", which
    callers treat as "use the generic hedge" rather than splicing it
    into a template ("the that" was a real output before this).

    The object is looked for after the last verb first, so "The recipient
    received the message" hedges about the message rather than about the
    recipient.
    """

    lowered = sentence.lower()

    verbs = _verbs_in(sentence)

    if verbs:
        after_verb = _ARTICLE_NOUN.search(lowered[verbs[-1][0]:])

        if after_verb:
            return after_verb.group(1)

    # Two verbs and no object after the last one: every noun left in the
    # sentence belongs to a different verb. "I opened the calendar and
    # created two events" repaired to "I can't verify that the calendar
    # was actually created" - a sentence the model never wrote, asserting
    # a pairing that was never claimed. A vague hedge is the lesser harm,
    # so the sentinel is returned instead.
    if len(verbs) > 1:
        return "that"

    # One verb, object before it: the passive case. "The email was sent."
    whole = _ARTICLE_NOUN.search(lowered)

    if whole:
        return whole.group(1)

    fallback = _FALLBACK_NOUNS.search(lowered)

    if fallback:
        return fallback.group(1)

    return "that"


# Verb -> hedge template. All are phrased to state the honest limit
# rather than invent a fact.
_HEDGES = {
    "sent": "I can't verify that the {obj} was actually sent.",
    "created": "I can't verify that the {obj} was actually created.",
    "deleted": "I can't verify that the {obj} was actually deleted.",
    "removed": "I can't verify that the {obj} was actually removed.",
    "opened": "I can't verify that the {obj} actually opened.",
    "launched": "I can't verify that the {obj} actually launched.",
    "changed": "I can't verify that the {obj} was actually changed.",
    "saved": "I can't verify that the {obj} was actually saved.",
    "modified": "I can't verify that the {obj} was actually modified.",
    "updated": "I can't verify that the {obj} was actually updated.",
    "added": "I can't verify that the {obj} was actually added.",
    "installed": "I can't verify that the {obj} was actually installed.",
    "uninstalled": "I can't verify that the {obj} was actually removed.",
    "verified": "I can't verify that the {obj} was actually confirmed.",
    "confirmed": "I can't verify that the {obj} was actually confirmed.",
    "cancelled": "I can't verify that the {obj} was actually cancelled.",
    "canceled": "I can't verify that the {obj} was actually cancelled.",
    "replied": "I can't verify that the reply actually went through.",
    "forwarded": "I can't verify that the {obj} was actually forwarded.",
    "scheduled": "I can't verify that the {obj} was actually scheduled.",
    "checked": "I can't verify what the {obj} actually shows.",
    "completed": "I can't verify that the {obj} actually completed.",
    "wrote": "I can't verify that the {obj} was actually written.",
    "received": "I can't verify whether the {obj} was actually received.",
    "delivered": "I can't verify whether the {obj} was actually delivered.",
    "set": "I can't verify that the {obj} was actually set.",
    "moved": "I can't verify that the {obj} was actually moved.",
    "renamed": "I can't verify that the {obj} was actually renamed.",
    "copied": "I can't verify that the {obj} was actually copied.",
    "downloaded": "I can't verify that the {obj} was actually downloaded.",
    "shared": "I can't verify that the {obj} was actually shared.",
    "dismissed": "I can't verify that the {obj} was actually dismissed.",
}


# The hedge verbs, matched on word boundaries.
#
# Substring matching would find "set" inside "settings" and "sent" inside
# "present", which is the exact failure `claims.py` warns about; longest
# first so "cancelled" is preferred over any shorter prefix. Built from
# `_HEDGES` so the verb table has one definition rather than two that can
# drift apart.
_HEDGE_VERB = re.compile(
    r"\b(" + "|".join(sorted(_HEDGES, key=len, reverse=True)) + r")\b"
)


def _verbs_in(sentence: str) -> list[tuple[int, str]]:
    """Every hedge verb in the sentence, in order of appearance."""

    return [
        (match.start(), match.group(1))
        for match in _HEDGE_VERB.finditer(sentence.lower())
    ]


def _hedge_sentence(claim: Claim) -> str:
    """
    The honest replacement for an ungrounded claim.

    Action claims get the verb-specific template, which reads better
    than a generic hedge and names what could not be confirmed. Anything
    else - a claim about the user's world or the current state of it -
    is turned into a question of confirmation over the same words:

        "Your phone has WhatsApp installed."
        -> "I can't confirm whether your phone has WhatsApp installed."

    That transform is deliberately dumb. It preserves the claim's exact
    content, adds no information, and removes the assertion, which is
    the whole of what repair is allowed to do.
    """

    if claim.type == ClaimType.ACTION:

        verbs = _verbs_in(claim.sentence)
        verb = verbs[-1][1] if verbs else None
        obj = _object_phrase(claim.sentence)

        if verb and obj != "that":
            return _agree(_HEDGES[verb].format(obj=obj), obj)

        return "I can't verify whether that actually happened."

    return "I can't confirm whether " + _unassert(claim.sentence)


def _agree(sentence: str, obj: str) -> str:
    """
    Fix number agreement after splicing a noun into a template.

    "the settings was actually changed" is the kind of seam that makes a
    repaired sentence read like a machine wrote it, which undermines the
    point: the user should be able to trust the hedge, not notice it.
    """

    plural = (
        obj.endswith("s")
        and not obj.endswith("ss")
        and not obj.endswith("us")
    )

    if plural:
        return sentence.replace(" was ", " were ")

    return sentence


def _unassert(sentence: str) -> str:
    """
    The claim's own words as a subordinate clause, nothing added.

    Strips one trailing sentence terminator so the clause can carry the
    outer sentence's, and lowercases a leading word unless it is one that
    must stay capitalised.
    """

    clause = sentence.strip().rstrip(".!?").strip()

    if not clause:
        return "that is the case."

    if not clause.startswith(("I ", "I'", "Tôi", "Em")):
        clause = clause[0].lower() + clause[1:]

    return clause + "."


def _repair_sentence(claim: Claim, reason: str = "") -> str:
    """One contradiction repair, phrased from the evidence or honestly."""

    lowered_reason = reason.lower()

    # PARTIAL first: it is the one contradiction where something DID
    # happen, and saying "that did not happen" about a partial success
    # would be its own false claim in the opposite direction.
    if "partial" in lowered_reason:
        return (
            "That only partly went through - I can't confirm the rest of it."
        )

    if "denied" in lowered_reason:
        return "That was blocked before anything could run."

    if "unavailable" in lowered_reason:
        return "That capability was not available, so nothing ran."

    if "failed" in lowered_reason or "not established" in lowered_reason:
        return "That did not happen - the operation failed."

    return _hedge_sentence(claim)


def repair_claims(
    text: str, claims: list[Claim],
) -> tuple[str, list[tuple]]:
    """
    Apply minimal repairs to the claims that need them.

    Returns (repaired_text, repairs) where each repair is
    (claim_id, original_sentence, replacement_sentence).

    Repair policy, deliberately narrow:

        CONTRADICTED                any claim - the hard boundary
        ACTION + INFERRED           successful but unverified side effect
        ACTION + UNKNOWN            no evidence matched
        CAPABILITY + UNKNOWN        "I can X" with X in the user's world
        MEMORY_DERIVED + INFERRED   old/uncertain memory asserted plainly
        IDENTITY/TEMPORAL + UNKNOWN user-state / current-time overclaim

    Every line except CONTRADICTED is inside the grounding scope: the
    sentence has to name something in the user's world. CONTRADICTED is
    not, because evidence that actively disagrees is the one case where
    silence would be a lie rather than merely an overclaim.

    Plain `UNSUPPORTED_FACT` claims are left alone so ordinary knowledge
    stays usable; they are still recorded in the ledger and diagnostics.
    """

    repairs: list[tuple] = []
    result = text

    for claim in claims:

        if claim.state is None or _ALREADY_HEDGED.search(claim.sentence):
            continue

        state = claim.state

        if state is ClaimState.VERIFIED or state is ClaimState.SUPPORTED:
            continue

        needs = (
            state is ClaimState.CONTRADICTED
            or (
                claim.type == ClaimType.ACTION
                and state in (ClaimState.UNKNOWN, ClaimState.INFERRED)
            )
            or (
                claim.type == ClaimType.CAPABILITY
                and claim.world
                and state in (ClaimState.UNKNOWN, ClaimState.INFERRED)
            )
            or (
                claim.type == ClaimType.MEMORY_DERIVED
                and state is ClaimState.INFERRED
            )
            or (
                claim.world
                and ClaimType.IDENTITY in claim.tags
                and state is ClaimState.UNKNOWN
            )
            or (
                claim.world
                and ClaimType.TEMPORAL in claim.tags
                and state is ClaimState.UNKNOWN
            )
            or (
                # Phase 4.5 matrix finding: a user-world factual claim
                # graded only INFERRED (the tool ran, nothing verified the
                # effect) must not be delivered as bare fact either. The
                # INFERRED qualifier is the honest wording.
                claim.world
                and claim.type == ClaimType.FACTUAL
                and state is ClaimState.INFERRED
            )
        )

        if not needs:
            continue

        reason = "; ".join(claim.notes)

        if state is ClaimState.CONTRADICTED:
            if claim.type == ClaimType.CAPABILITY:
                replacement = "I can't do that right now - that capability isn't available."
            else:
                replacement = _repair_sentence(claim, reason)
        elif claim.type == ClaimType.CAPABILITY:
            replacement = "I'm not entirely sure I can do that."
        elif claim.type == ClaimType.MEMORY_DERIVED:
            replacement = "From what I remember, " + _lower_first(
                claim.sentence
            )
        elif state is ClaimState.INFERRED:
            replacement = "As far as I can tell, " + _lower_first(
                claim.sentence
            )
        else:
            replacement = _hedge_sentence(claim)

        if replacement == claim.sentence:
            continue

        result = result.replace(claim.sentence, replacement, 1)
        claim.repair = replacement
        repairs.append((claim.id, claim.sentence, replacement))

    return result, repairs


def _lower_first(sentence: str) -> str:
    """
    'I sent the email.' -> 'I sent the email.' for a qualifier prefix.

    The first-person 'I' must stay capitalised; everything else is
    lowercased so the sentence reads naturally after a comma.
    """

    if not sentence:
        return sentence

    if sentence.startswith(("I ", "I'", "Tôi", "Em")):
        return sentence

    return sentence[0].lower() + sentence[1:]


__all__ = ["repair_claims"]