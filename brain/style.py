"""
Conversation style.

Aura's replies should read like Aura, not like a model with a persona
bolted on. Two mechanisms do that, and the split between them is the
whole design:

    prompt_hint()   asks the model to write in her voice
    style(text)     removes assistant boilerplate from what came back

Only the first one rewrites anything. That is deliberate.

The brief's example - turning "The error happens because the dependency
injection failed" into "Yep bro, found the culprit. The dependency
injection is basically tripping over itself here" - is a paraphrase. A
paraphrase is a language task, and the only thing here qualified to do it
without damaging the content is the model that wrote the sentence. A
regex that reached in and swapped words would eventually swap one inside
a stack trace, a file path or an identifier, and the rule for this layer
is absolute:

    tone, wording and friendliness may change; facts may not.

So `style()` is a subtractive filter. It deletes phrases from a fixed
list of corporate filler and nothing else. It never substitutes, never
reorders, never paraphrases, and never touches anything inside code.
If it is unsure, it leaves the text alone.

Two things colour the hint, and both are prompt side only, so neither can
reach the text of a reply:

    mood        how she feels right now, as a line of writing direction
    variety     which openers she has used recently, so she varies them

This lives in brain/ because it is conversation logic. It knows nothing
about voice, avatars or rendering.
"""

import re
from collections import deque
from typing import Protocol, runtime_checkable

from brain.mood import current_mood, mood_hint


@runtime_checkable
class ResponseStyler(Protocol):
    """Adjusts the wording of a reply. Never its meaning."""

    def style(self, text: str) -> str:
        ...


class NullStyler:
    """Passes text through untouched."""

    def style(self, text: str) -> str:
        return text

    def prompt_hint(self) -> str:
        return ""

    def note_reply(self, text: str) -> None:
        """Remembers nothing, so the streaming path can call it blindly."""


# ----------------------------------------------------------------------
# The filler list
#
# Every entry is a phrase that carries no information. Deleting one can
# change how a sentence sounds; it cannot change what the sentence says.
# Anything whose removal could alter meaning does not belong here.
# ----------------------------------------------------------------------

FILLER_OPENERS = (
    "certainly",
    "certainly!",
    "of course",
    "of course!",
    "sure thing",
    "absolutely",
    "great question",
    "that's a great question",
    "i understand your request",
    "i'd be happy to help",
    "i would be happy to help",
    "i apologize for the inconvenience",
    "i apologize for any confusion",
    "as an ai language model",
    "as an ai assistant",
    "as a large language model",
)

FILLER_CLOSERS = (
    "is there anything else i can help you with",
    "is there anything else you'd like to know",
    "let me know if you have any other questions",
    "let me know if you need anything else",
    "i hope this helps",
    "hope this helps",
    "feel free to ask if you have any questions",
    "please let me know if you have further questions",
)


# A fenced block, or an inline span. Matched first so that everything
# inside is held out of the filter entirely.
CODE = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)

PLACEHOLDER = "\x00CODE{index}\x00"
PLACEHOLDER_PATTERN = re.compile(r"\x00CODE(\d+)\x00")


# How many recent openers to hold. Three is enough to catch a habit and
# few enough that the prompt line stays one readable sentence.
DEFAULT_AVOID_REPEATS = 3

# Words an opener is measured over. Long enough that "Bro that's actually"
# and "Bro that's a" count as the same habit, short enough that two
# replies about the same topic are not forced apart.
OPENER_WORDS = 4


class AuraStyle:
    """
    Aura's voice, applied at both ends of a turn.

    `prompt_hint` goes into the prompt and does the real work. `style`
    cleans up what the model still emitted out of habit.
    """

    def __init__(
        self,
        enabled: bool = True,
        strip_filler: bool = True,
        hint: str = "",
        mood=None,
        avoid_repeats: int = DEFAULT_AVOID_REPEATS,
    ):
        """
        `mood` is a source rather than a value - a MoodTracker, anything
        with a `.mood`, or a callable. Holding a source means the styler
        reads the mood at the moment the prompt is built instead of
        holding a copy that something has to remember to refresh.

        `avoid_repeats` is how many recent openers to keep, so she can be
        told to vary them. 0 disables the tracking entirely.
        """

        self.enabled = enabled
        self.strip_filler = strip_filler
        self._hint = hint
        self.mood = mood

        self._openers: deque[str] = deque(maxlen=max(0, avoid_repeats))

    # ------------------------------------------------------------------
    # Into the prompt
    # ------------------------------------------------------------------

    def prompt_hint(self) -> str:
        """
        A short reminder of how to write *this* reply.

        personality.md already describes who Aura is, near the top of the
        prompt. This is the same instruction restated in imperative form
        and placed next to the user's message, where a model is most
        likely to still be following it.

        Three parts, in descending permanence: who she is, how she feels
        right now, and what she has said too often lately.
        """

        if not self.enabled:
            return ""

        parts = [self._hint or DEFAULT_HINT]

        for extra in (self._mood_line(), self._variety_line()):
            if extra:
                parts.append(extra)

        return " ".join(parts)

    def _mood_line(self) -> str:
        """The current mood as writing direction, or "" for neutral."""

        try:
            return mood_hint(current_mood(self.mood))
        except Exception:
            # A broken mood source costs a prompt line, not the turn.
            return ""

    def _variety_line(self) -> str:
        """
        Recent openers, so the next reply does not reuse one.

        Naming them is what makes this work: "vary your phrasing" is
        advice a model nods at and ignores, while "you already opened
        with these" is a constraint it can actually check itself against.
        """

        if not self._openers:
            return ""

        quoted = ", ".join(f'"{opener}"' for opener in self._openers)

        return (
            f"Recent replies already opened with {quoted}. Start this one "
            "some other way, and do not fall back on a stock phrase."
        )

    # ------------------------------------------------------------------
    # Out of the model
    # ------------------------------------------------------------------

    def style(self, text: str) -> str:
        """
        Strip assistant filler, leaving everything else exactly as it was.

        Returns the original text unchanged if the result would be empty -
        a reply that was nothing but filler is still better than silence,
        and it is a signal worth seeing rather than hiding.
        """

        if not self.enabled or not self.strip_filler:
            self.note_reply(text)
            return text

        if not text or not text.strip():
            return text

        protected, spans = _hide_code(text)

        cleaned = _strip_openers(protected)
        cleaned = _strip_closers(cleaned)

        cleaned = _restore_code(cleaned, spans)

        if not cleaned.strip():
            self.note_reply(text)
            return text

        self.note_reply(cleaned)

        return cleaned

    def note_reply(self, text: str) -> None:
        """
        Remember how a reply opened.

        Called by `style` for the ordinary path, and directly by the
        streaming path, which assembles its own text. Recording only -
        it never alters anything, so calling it twice on the same reply
        costs a duplicate entry and nothing more.
        """

        if self._openers.maxlen == 0:
            return

        opener = _opener_of(text)

        if opener and opener not in self._openers:
            self._openers.append(opener)


DEFAULT_HINT = (
    "Reply as Aura. Warm, calm, composed, unhurried - a friend talking, "
    "not an assistant reporting. Moderately short sentences, commas where "
    "a speaker would actually pause, and no more length than the answer "
    "needs. No corporate filler, no apology boilerplate, no closing offer "
    "of further assistance, and no performed excitement. Light teasing is "
    "welcome where it fits; slang is seasoning, not punctuation. Write so "
    "it sounds right read aloud. Keep every technical detail exactly as "
    "accurate as it would be otherwise: style changes the wording, never "
    "the facts."
)


# ----------------------------------------------------------------------
# Code protection
# ----------------------------------------------------------------------

def _hide_code(text: str) -> tuple[str, list[str]]:
    """Replace code spans with placeholders the filter cannot match."""

    spans: list[str] = []

    def take(match: re.Match) -> str:
        spans.append(match.group(0))
        return PLACEHOLDER.format(index=len(spans) - 1)

    return CODE.sub(take, text), spans


def _restore_code(text: str, spans: list[str]) -> str:

    def give(match: re.Match) -> str:
        index = int(match.group(1))
        return spans[index] if index < len(spans) else match.group(0)

    return PLACEHOLDER_PATTERN.sub(give, text)


# ----------------------------------------------------------------------
# Filler removal
# ----------------------------------------------------------------------

# A filler opener only counts when it is the whole of the first clause.
# "Certainly, here is the fix" opens with filler; "I would be happy to
# help once you paste the traceback" is a real sentence and stays.
OPENER = re.compile(r"^\s*([^.!?\n]{0,60}?)\s*[,.!:]+\s*", re.IGNORECASE)

CLOSER = re.compile(r"(?:^|(?<=[.!?\n]))\s*([^.!?\n]{0,80}?)\s*[.!?]*\s*$")


def _strip_openers(text: str) -> str:
    """Remove filler clauses from the front, however many there are."""

    for _ in range(3):

        match = OPENER.match(text)

        if match is None:
            break

        if _normalise(match.group(1)) not in FILLER_OPENERS:
            break

        remainder = text[match.end():]

        if not remainder.strip():
            break

        text = _recapitalise(remainder)

    return text


def _strip_closers(text: str) -> str:
    """Remove a filler sentence from the end."""

    for _ in range(2):

        match = CLOSER.search(text)

        if match is None:
            break

        if _normalise(match.group(1)) not in FILLER_CLOSERS:
            break

        remainder = text[: match.start()]

        if not remainder.strip():
            break

        text = remainder.rstrip()

    return text


def _normalise(phrase: str) -> str:
    """Lowercase, unpunctuated, single spaced - for list comparison only."""

    return re.sub(r"[^a-z0-9' ]+", "", phrase.lower()).strip()


def _recapitalise(text: str) -> str:
    """
    Capitalise the first letter, but only if it was lowercase prose.

    Deleting "Certainly, " leaves "here is the fix". Restoring the
    capital is cosmetic. It is skipped when the first token looks like
    code - `assertEquals`, `npm`, `dataclass` - because changing the case
    of an identifier changes what it means.
    """

    stripped = text.lstrip()

    if not stripped or not stripped[0].isalpha():
        return text

    first = stripped.split(maxsplit=1)[0].strip(".,;:!?")

    if first != first.lower():
        return stripped

    return stripped[0].upper() + stripped[1:]


def _opener_of(text: str) -> str:
    """
    The first few words of a reply, for repetition tracking.

    A reply that opens with code has no opener worth remembering - the
    first line of a snippet is not a phrase she chose - so those return
    "" and are never fed back into the prompt.
    """

    stripped = (text or "").lstrip()

    if not stripped or stripped.startswith(("```", "`", "#", "-", "*", ">")):
        return ""

    first_line = stripped.splitlines()[0]

    words = first_line.split()[:OPENER_WORDS]

    if not words:
        return ""

    # Trailing punctuation only; anything inside a word is left alone so
    # an opener like "npm run dev" is recorded as written.
    opener = " ".join(words).strip(" ,.;:!?-")

    return opener


# ----------------------------------------------------------------------
# Construction
# ----------------------------------------------------------------------

def build_styler(config: dict | None = None, mood=None) -> ResponseStyler:
    """
    Build a styler from the `personality.style` config section.

    Disabled, or absent, yields a NullStyler rather than a disabled
    AuraStyle, so the no-op path costs one attribute lookup.

    `mood` is injected rather than read from config: it is a live source,
    not a setting. Left out, the styler simply never adds a mood line.
    """

    settings = config or {}

    if not settings.get("enabled", True):
        return NullStyler()

    return AuraStyle(
        enabled=True,
        strip_filler=settings.get("strip_filler", True),
        hint=settings.get("hint", "") or "",
        mood=mood,
        avoid_repeats=_as_count(
            settings.get("avoid_repeats", DEFAULT_AVOID_REPEATS)
        ),
    )


def _as_count(value) -> int:
    """A non-negative int, or the default. A bad setting is not a crash."""

    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return DEFAULT_AVOID_REPEATS


def hint_of(styler) -> str:
    """
    The prompt hint of any styler, or "" for one that has none.

    `prompt_hint` is optional in the protocol - a user's three line
    styler should not have to implement it - so it is read defensively.
    """

    if styler is None:
        return ""

    getter = getattr(styler, "prompt_hint", None)

    if getter is None:
        return ""

    try:
        return getter() or ""
    except Exception:
        return ""


__all__ = [
    "ResponseStyler",
    "NullStyler",
    "AuraStyle",
    "DEFAULT_HINT",
    "DEFAULT_AVOID_REPEATS",
    "build_styler",
    "hint_of",
]
