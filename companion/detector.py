"""
Change detection.

The first and cheapest gate: has the screen actually changed?

A phone reports the same screen many times while the user reads it.
Everything downstream - relevance, the LLM, the notification - costs
something, so a repeat is dropped here, before any of it runs.

Similarity is token overlap (Jaccard), not string equality. A timestamp
ticking in a status bar, a scroll position, a notification badge - all of
these change the string and none of them change the screen.

Token overlap alone is not enough for the short ones. A clock ticking
inside a twelve-word note moves one token in thirteen, which reads as an
8% change and clears any threshold worth having. So tokens that change
by themselves - clocks, dates, counters - are collapsed to placeholders
before the comparison, and only genuinely new words move the score.
"""

from dataclasses import dataclass
import re

from vision.remote import ScreenObservation


# Above this, two screens are "the same screen".
DEFAULT_SIMILARITY = 0.85


# Tokens that change on their own, without the screen meaning anything
# different. Each collapses to a single placeholder so that a clock
# reading 10:04 and the same clock reading 10:05 compare as equal
# instead of as one token in thirteen having changed.
#
# Matched against the whole token, never a substring: "10:04" is a
# clock, "v2:04" is not, and an order id that happens to contain digits
# keeps its identity because the surrounding characters disqualify it.
_VOLATILE = (
    # 10:04, 10:04:56, 9:15pm, 9:15 p.m.
    (re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?(?:[ap]\.?m\.?)?$"), "<clock>"),
    # 2026-08-09, 09/08/2026, 9.8.26
    (re.compile(r"^\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}$"), "<date>"),
    # 3, 1,024, 45%, 12s, 350ms, 1.5gb, +12, -3
    (
        re.compile(
            r"^[+-]?\d[\d,._]*(?:%|s|ms|kb|mb|gb|tb|k|m|b)?$",
            re.IGNORECASE,
        ),
        "<count>",
    ),
)

# Stripped from each end before matching, so "(10:04)" and "10:04," are
# still recognisable as clocks. Interior punctuation is left alone -
# removing it would merge "10:04" into "1004".
_EDGE_PUNCTUATION = "()[]{}<>\"'`,;:!?…-–—*_"


def _normalise(token: str) -> str:
    """
    One token, with self-changing content replaced by a placeholder.

    Anything that does not look volatile is returned unchanged, so a
    screen made of words compares exactly as it did before.
    """

    stripped = token.strip(_EDGE_PUNCTUATION)

    if not stripped:
        return token

    for pattern, placeholder in _VOLATILE:
        if pattern.match(stripped):
            return placeholder

    return stripped


@dataclass(frozen=True)
class ScreenChange:
    """What changed between two observations."""

    changed: bool
    reason: str
    similarity: float = 1.0
    app_switched: bool = False
    previous_app: str = ""
    current_app: str = ""


def _tokens(text: str) -> set[str]:
    return {
        normalised
        for token in text.lower().split()
        if (normalised := _normalise(token))
    }


def similarity(left: str, right: str) -> float:
    """
    Jaccard overlap of the two token sets, 0.0 to 1.0.

    Two empty strings are identical (1.0). One empty and one not are
    completely different (0.0).
    """

    left_tokens = _tokens(left)
    right_tokens = _tokens(right)

    if not left_tokens and not right_tokens:
        return 1.0

    union = left_tokens | right_tokens

    if not union:
        return 1.0

    return len(left_tokens & right_tokens) / len(union)


class ChangeDetector:
    """
    Compares each observation against the last one it accepted.

    Holds one observation, not a history. The question is "is this
    different from what I last thought about", and only the most recent
    accepted screen answers it.
    """

    def __init__(self, threshold: float = DEFAULT_SIMILARITY):

        self.threshold = threshold
        self._previous: ScreenObservation | None = None

    @property
    def previous(self) -> ScreenObservation | None:
        return self._previous

    def reset(self) -> None:
        self._previous = None

    def inspect(self, observation: ScreenObservation) -> ScreenChange:
        """
        Compare without recording. Pure - safe to call twice.
        """

        previous = self._previous

        current_app = observation.package or observation.application

        if previous is None:
            return ScreenChange(
                changed=True,
                reason="first observation",
                similarity=0.0,
                current_app=current_app,
            )

        previous_app = previous.package or previous.application

        if current_app and current_app != previous_app:
            return ScreenChange(
                changed=True,
                reason=f"switched to {current_app}",
                similarity=0.0,
                app_switched=True,
                previous_app=previous_app,
                current_app=current_app,
            )

        score = similarity(previous.fingerprint(), observation.fingerprint())

        if score >= self.threshold:
            return ScreenChange(
                changed=False,
                reason=f"same screen ({score:.2f} similar)",
                similarity=score,
                previous_app=previous_app,
                current_app=current_app,
            )

        return ScreenChange(
            changed=True,
            reason=f"screen content changed ({score:.2f} similar)",
            similarity=score,
            previous_app=previous_app,
            current_app=current_app,
        )

    def accept(self, observation: ScreenObservation) -> None:
        """Record this observation as the new baseline."""

        self._previous = observation

    def check(self, observation: ScreenObservation) -> ScreenChange:
        """
        Inspect, and record when it changed.

        An unchanged screen deliberately does not become the baseline:
        drifting the baseline forward one unnoticeable step at a time is
        how a slow scroll through a long document ends up never counting
        as a change.
        """

        change = self.inspect(observation)

        if change.changed:
            self.accept(observation)

        return change
