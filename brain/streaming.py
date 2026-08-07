"""
Streaming.

A reply that arrives all at once has already made the user wait for its
last token. Streaming turns that into a first word after a beat and a
mouth that starts moving while the rest is still being written, which is
most of the difference between a chatbot and a companion.

Three pieces, none of which know what consumes them:

    StreamingLLM        the optional capability, as a protocol
    stream_of           any provider as a stream, streaming or not
    SentenceAggregator  fragments in, whole sentences out

The last one exists for the voice. Text can appear on screen a token at
a time, but speech cannot: synthesising "The dep" and then "endency" is
two clips of nonsense. So the same stream feeds two consumers at two
granularities - the UI takes fragments, the voice takes sentences - and
neither has to know about the other.

Nothing here imports voice, avatar or UI code. The stream is published as
events; who listens is not this module's business.

Adding streaming does not deprecate `generate`. A provider that cannot
stream is not second class - `stream_of` presents it as a single chunk
stream, so every consumer downstream has exactly one code path.
"""

import re
from typing import Iterable, Iterator, Protocol, runtime_checkable


@runtime_checkable
class StreamingLLM(Protocol):
    """
    A provider that can yield a reply in pieces.

    Deliberately separate from the LLM protocol rather than added to it.
    LLM is the contract every provider meets; this is a capability some
    of them have, and a provider that only implements `generate` must
    stay a valid LLM. `isinstance(llm, StreamingLLM)` is the check.
    """

    def stream(self, prompt: str) -> Iterator[str]:
        ...


def can_stream(llm) -> bool:
    """True when a provider can produce a real token stream."""

    return callable(getattr(llm, "stream", None))


def stream_of(llm, prompt: str) -> Iterator[str]:
    """
    Any provider as a stream of fragments.

    A streaming provider is used as one. A plain provider yields its
    whole reply as a single fragment - correct rather than a fallback:
    the reply did arrive in one piece, and pretending otherwise by
    chopping it into fake tokens would add latency to imitate the thing
    latency causes.
    """

    if can_stream(llm):
        yield from llm.stream(prompt)
        return

    text = llm.generate(prompt)

    if text:
        yield text


# ----------------------------------------------------------------------
# Sentence aggregation
# ----------------------------------------------------------------------

# A sentence ends at .!? followed by space or end of text. The lookbehind
# and lookahead keep it away from the cases that look like endings and
# are not.
SENTENCE_END = re.compile(
    r"""
    (?<![A-Z])            # not an initial: "J. Smith"
    (?<!\d)               # not a decimal or version: "3.14", "v1.2"
    [.!?]+
    (?=\s|$)              # followed by whitespace or the end
    (?!\s*\d)             # and not by a number: "step 1. 2 comes next"
    """,
    re.VERBOSE,
)

# Endings that are punctuation but not sentences.
ABBREVIATIONS = frozenset(
    {
        "e.g.", "i.e.", "etc.", "vs.", "mr.", "mrs.", "ms.", "dr.",
        "st.", "approx.", "fig.", "no.", "vol.", "cf.", "al.",
    }
)

FENCE = "```"


class SentenceAggregator:
    """
    Turns a fragment stream into a sentence stream.

    Holds a buffer, releases whole sentences, and keeps the remainder
    until `flush`. Code fences suspend release entirely: a fenced block
    is one unit no matter how many full stops are inside it, and cutting
    one in half would hand a consumer a snippet that does not parse.

    Pure text in, pure text out. No clock, no thread, no I/O.
    """

    def __init__(self, min_chars: int = 0):
        """
        `min_chars` holds back sentences shorter than it, joining them to
        the next one. "Yeah." followed by "So the fix is X." reads fine
        on screen but sounds clipped as two separate utterances; raising
        this trades a little latency for smoother speech. 0 releases
        every sentence as soon as it completes, which is lowest latency
        and the right default for text.
        """

        self.min_chars = max(0, min_chars)

        self._buffer = ""
        self._fenced = False

    @property
    def pending(self) -> str:
        """Text held but not yet released."""

        return self._buffer

    def feed(self, fragment: str) -> list[str]:
        """
        Add a fragment. Returns whatever sentences it completed.

        Usually empty - most fragments land mid sentence - so a caller
        can iterate the result unconditionally and do nothing most of the
        time.
        """

        if not fragment:
            return []

        self._buffer += fragment

        if self._crosses_fence(fragment):
            # A fence just opened or closed. Nothing is released while
            # one is open, and the block is released whole when it shuts.
            if self._fenced:
                return []

        if self._fenced:
            return []

        return self._take_sentences()

    def flush(self) -> str:
        """
        Everything still held, and empty the buffer.

        Called when the stream ends. The tail is usually a sentence with
        no trailing punctuation, and an unfenced fence is closed here by
        simply releasing what there is.
        """

        remainder = self._buffer.strip()

        self._buffer = ""
        self._fenced = False

        return remainder

    # ------------------------------------------------------------------

    def _crosses_fence(self, fragment: str) -> bool:
        """Track fence parity. Returns True when the state flipped."""

        count = fragment.count(FENCE)

        if count == 0:
            return False

        if count % 2:
            self._fenced = not self._fenced
            return True

        return False

    def _take_sentences(self) -> list[str]:

        sentences: list[str] = []

        while True:

            match = self._next_end()

            if match is None:
                break

            cut = match.end()

            candidate = self._buffer[:cut]

            if len(candidate.strip()) < self.min_chars:
                # Too short to stand alone. Leave it in the buffer and
                # let it merge with whatever comes next.
                break

            sentences.append(candidate.strip())

            self._buffer = self._buffer[cut:].lstrip()

        return sentences

    def _next_end(self):
        """The next real sentence end, skipping abbreviations."""

        start = 0

        while True:

            match = SENTENCE_END.search(self._buffer, start)

            if match is None:
                return None

            if not _is_abbreviation(self._buffer, match.end()):
                return match

            start = match.end()

    def __repr__(self) -> str:
        return (
            f"SentenceAggregator(pending={len(self._buffer)} chars, "
            f"fenced={self._fenced})"
        )


def _is_abbreviation(text: str, end: int) -> bool:
    """True when the punctuation at `end` closes a known abbreviation."""

    tail = text[:end].rsplit(maxsplit=1)

    if not tail:
        return False

    return tail[-1].lower() in ABBREVIATIONS


def sentences_of(fragments: Iterable[str], min_chars: int = 0) -> Iterator[str]:
    """
    A whole fragment stream as sentences, tail included.

    The convenience form, for a consumer that wants sentences and does
    not need to interleave anything between them.
    """

    aggregator = SentenceAggregator(min_chars=min_chars)

    for fragment in fragments:
        yield from aggregator.feed(fragment)

    tail = aggregator.flush()

    if tail:
        yield tail


__all__ = [
    "StreamingLLM",
    "can_stream",
    "stream_of",
    "SentenceAggregator",
    "sentences_of",
]
