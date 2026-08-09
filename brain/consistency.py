"""
Character consistency.

A personality described once at the top of a prompt is a personality the
model is reading through an ever-growing transcript. Early on that is
fine. Twenty turns later the description is far away, the recent text is
all technical, and the model starts answering like whatever it has been
reading - which is itself, being increasingly formal.

That is drift, and it has five recognisable shapes. The brief names them:

    robotic            answers become mechanical, the warmth drains out
    overly formal      "I would be happy to assist you with that"
    overly emotional   gushing, exclamation marks, performed enthusiasm
    self-contradiction says the opposite of something said earlier
    lost identity      stops being Aura, becomes a generic assistant

All five are fixed the same way, and it is the way the brief insists on:
prompt construction. Nothing here inspects a reply, matches a pattern or
rewrites a word. There is no regex in this file and no post-processing
step. It produces a short block of text that is placed near the user's
message, where a model is most likely to still be following it, and the
model does the rest.

Two properties make this cheap enough to leave on:

    it says nothing early     a three message conversation has not
                              drifted, so the section is empty and costs
                              exactly zero tokens

    it grows once, not twice  the contradiction clause appears only when
                              there is enough transcript to contradict

It reads one number - how many messages are in the prompt - and nothing
else. Not their content, not the user, not the mood. A guard that had to
understand the conversation to protect it would be a second brain, and
the one thing worse than a drifting personality is two of them.

Style, mood and this are deliberately three separate lines rather than
one paragraph, in descending permanence:

    consistency   who she is, and does not stop being      (this file)
    style         how a reply of hers reads                brain/style.py
    mood          how she happens to feel right now        brain/mood.py

A model asked to hold one long instruction obeys the end of it. Asked to
hold three short ones, it obeys three.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class IdentityAnchor(Protocol):
    """
    Restates who Aura is, given how long the conversation has run.

    Narrow on purpose, and for the usual reason: this is
    `runtime_checkable`, so every name added here breaks `isinstance`
    for every implementation already written against it - including
    ones outside this repository.
    """

    def anchor(self, messages: int) -> str:
        ...


class NullAnchor:
    """Adds nothing, ever. The shape of an unconfigured guard."""

    def anchor(self, messages: int) -> str:
        return ""


# ----------------------------------------------------------------------
# Thresholds
#
# Both are message counts, not turn counts, because that is what the
# prompt actually contains and converting invites an off-by-one that
# nobody would ever notice.
# ----------------------------------------------------------------------

# Below this the personality section is still close enough to the end of
# the prompt to be doing its job. Six messages is three exchanges.
DEFAULT_ENGAGE_AFTER = 6

# Contradiction needs a transcript long enough to contain one. Twenty
# messages is ten exchanges - the point where she has committed to
# opinions she could now walk back without noticing.
DEFAULT_CONTRADICTION_AFTER = 20


# ----------------------------------------------------------------------
# The anchor text
#
# Written as instruction rather than description. personality.md already
# describes her; a description read twice is redundant, while the same
# thing in imperative form next to the user's message is a constraint.
# ----------------------------------------------------------------------

IDENTITY = (
    "You are still Aura - the same person as at the start of this "
    "conversation, not a narrator of it. Length of conversation changes "
    "nothing about who is talking."
)

# The four drift modes that do not need history to guard against.
#
# Each is stated as the failure and its correction, because "do not be
# robotic" alone leaves a model guessing at the target, and a model
# guessing at a target overcorrects into the opposite failure.
DRIFT = (
    "Do not drift into an assistant register: no formal helper phrasing, "
    "no restating the question before answering it, no offers of further "
    "assistance. Do not overcorrect the other way either - no gushing, no "
    "performed excitement, no exclamation marks doing the work that "
    "wording should, and no piling on slang. Warm, calm and direct, at "
    "the same unhurried pace you had on the first message."
)

# Held back until there is enough transcript to contradict.
#
# The instruction is to acknowledge rather than to be consistent, and the
# difference matters: told only to be consistent, a model defends a wrong
# earlier statement rather than admit it changed. Being wrong once and
# saying so is in character. Quietly pretending both answers were right
# is not.
CONTRADICTION = (
    "Stay consistent with what you already said in RECENT CONVERSATION. "
    "If you now think something you said earlier was wrong, say that "
    "plainly once and move on - do not quietly reverse it, and do not "
    "defend it because you said it."
)


class CharacterAnchor:
    """
    Aura's identity, restated late in the prompt when it needs to be.

    Stateless: it holds thresholds and text, and derives everything else
    from the number it is handed. Two conversations sharing one instance
    cannot affect each other, which is why the launcher is free to build
    exactly one.
    """

    def __init__(
        self,
        enabled: bool = True,
        engage_after: int = DEFAULT_ENGAGE_AFTER,
        contradiction_after: int = DEFAULT_CONTRADICTION_AFTER,
        text: str = "",
    ):
        """
        `text` replaces the identity line only. The drift and
        contradiction clauses are not configurable, because they are the
        guard itself rather than flavour - a config file that could
        delete them would be a config file that could switch character
        consistency off while leaving it switched on.
        """

        self.enabled = enabled
        self.engage_after = max(0, engage_after)
        self.contradiction_after = max(0, contradiction_after)
        self.text = (text or "").strip()

    def anchor(self, messages: int) -> str:
        """
        The guard for a conversation of this length.

        Empty until `engage_after`, so a short conversation pays nothing
        for a problem it does not have yet.
        """

        if not self.enabled:
            return ""

        count = _as_count(messages)

        if count < self.engage_after:
            return ""

        parts = [self.text or IDENTITY, DRIFT]

        if count >= self.contradiction_after:
            parts.append(CONTRADICTION)

        return " ".join(parts)

    def __repr__(self) -> str:
        return (
            f"CharacterAnchor(enabled={self.enabled}, "
            f"engage_after={self.engage_after}, "
            f"contradiction_after={self.contradiction_after})"
        )


# ----------------------------------------------------------------------
# Construction
# ----------------------------------------------------------------------

def build_anchor(config: dict | None = None) -> IdentityAnchor:
    """
    Build the guard from the `personality.consistency` config section.

    Disabled yields a NullAnchor rather than a disabled CharacterAnchor,
    so the off path is one attribute lookup and a constant.
    """

    settings = config or {}

    if not settings.get("enabled", True):
        return NullAnchor()

    return CharacterAnchor(
        enabled=True,
        engage_after=_as_count(
            settings.get("after_messages", DEFAULT_ENGAGE_AFTER),
            DEFAULT_ENGAGE_AFTER,
        ),
        contradiction_after=_as_count(
            settings.get("contradiction_after", DEFAULT_CONTRADICTION_AFTER),
            DEFAULT_CONTRADICTION_AFTER,
        ),
        text=settings.get("anchor", "") or "",
    )


def _as_count(value, fallback: int = 0) -> int:
    """A non-negative int, or the fallback. A bad setting is not a crash."""

    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return fallback


def anchor_of(source, messages: int) -> str:
    """
    The anchor from any source, or "" from one that has none.

    Read defensively for the same reason `hint_of` is: a user's guard
    should be three lines and a method, not an inheritance obligation,
    and a broken one must cost a prompt section rather than the turn.
    """

    if source is None:
        return ""

    getter = getattr(source, "anchor", None)

    if getter is None:
        return ""

    try:
        return getter(messages) or ""
    except Exception:
        return ""


__all__ = [
    "IdentityAnchor",
    "NullAnchor",
    "CharacterAnchor",
    "IDENTITY",
    "DRIFT",
    "CONTRADICTION",
    "DEFAULT_ENGAGE_AFTER",
    "DEFAULT_CONTRADICTION_AFTER",
    "build_anchor",
    "anchor_of",
]
