"""
What is worth remembering.

The gate in front of episodic memory, and the reason Aura's recall does
not fill up with "ok", "thanks" and pasted stack traces. Everything the
user says arrives here; almost none of it gets through.

The bar is deliberately high. A memory that should have been stored and
was not costs one forgotten detail. A memory that should not have been
stored and was costs prompt space on every future turn *and* makes the
ranked recall worse for everything else, because noise competes with
signal for a bounded number of slots. Recall is a fixed budget, so the
default is no.

Four hard exclusions come first, and none of them is a judgement call:

    not the user          assistant text, tool JSON and machine turns
                          are Aura talking to herself. Phase 7 settled
                          this for conversation memory; it holds here.
    too short             "ok", "yeah", "haha" carry nothing later
    a question            asking is not telling
    a command             "open notepad" is work, not biography

What remains is scored, not classified by a model. A local heuristic is
inspectable, free, deterministic and testable, and this is a filter -
the cost of a wrong call is one missing or one junk memory, not a wrong
answer. An LLM pass here would add latency to every single turn for a
decision that first-person markers make well enough.
"""

from dataclasses import dataclass
from datetime import datetime
import re

from core.temporal import parse_timestamp


# Below this, nothing is worth a row.
DEFAULT_THRESHOLD = 0.5

# Shorter than this and there is nothing to remember.
MIN_LENGTH = 12

# Longer than this and it is a paste, not a statement about a life.
MAX_LENGTH = 600

# Categories, in the order they are tested. First match wins, so the
# more specific patterns are listed before the general ones.
IDENTITY = "identity"
PREFERENCE = "preference"
PROJECT = "project"
EVENT = "event"
PLAN = "plan"
FEELING = "feeling"

# Marks a statement as being about the speaker rather than about the
# world. "the migration is done" is a status; "I finished the migration"
# is a memory.
FIRST_PERSON = re.compile(
    r"\b(i|i'm|im|i've|ive|i'll|my|mine|me|myself)\b", re.IGNORECASE
)

QUESTION = re.compile(r"^\s*(what|why|how|when|where|who|which|can|could|"
                      r"would|will|do|does|did|is|are|was|were|should|"
                      r"gì|sao|thế nào|khi nào|ở đâu|ai)\b", re.IGNORECASE)

# Imperative openers. Work to do, not a thing about the person.
COMMAND = re.compile(
    r"^\s*(open|run|start|stop|close|show|list|find|search|read|write|"
    r"delete|remove|create|make|fix|check|install|build|test|explain|"
    r"tell me|give me|help me|please)\b",
    re.IGNORECASE,
)

# Text that is plainly machine output rather than something a person
# typed. Cheap structural checks; anything subtler is not worth the
# false positives.
MACHINE_SHAPED = (
    re.compile(r"^\s*[\{\[]"),                        # JSON payload
    re.compile(r"^\s*(Traceback|File \")", re.MULTILINE),
    re.compile(r"^\s*(def |class |import |from \w+ import)", re.MULTILINE),
    re.compile(r"^\s*<\?xml|^\s*<!DOCTYPE", re.IGNORECASE),
)

# Signals, and what each is worth. A statement needs to accumulate
# DEFAULT_THRESHOLD to be stored at all.
SIGNALS = (
    (IDENTITY, 0.75, re.compile(
        r"\b(my name is|i am called|i'm called|call me|i am from|i'm from|"
        r"i live in|i work as|i study|i speak|tên tôi là|tên mình là|"
        r"mình là|tui là|tui tên)\b", re.IGNORECASE)),

    (PREFERENCE, 0.65, re.compile(
        r"\b(i prefer|i like|i love|i hate|i enjoy|i can't stand|"
        r"i cannot stand|i don't like|i dont like|my favou?rite|"
        r"i'd rather|i would rather|i always|i never|"
        r"tôi thích|mình thích|tui thích|tôi ghét|mình ghét)\b",
        re.IGNORECASE)),

    (PROJECT, 0.6, re.compile(
        r"\b(i'?m (?:currently )?(?:working|building|writing|making)|"
        r"i started (?:a|the|my|working)|my project|i'?m learning|"
        r"i decided to|we'?re building|đang làm|đang học)\b",
        re.IGNORECASE)),

    (EVENT, 0.6, re.compile(
        r"\b(i (?:just )?(?:finished|shipped|fixed|solved|launched|"
        r"released|completed|broke|failed|passed|got|moved|bought|"
        r"met|quit|joined|started)|i had|we (?:finished|shipped))\b",
        re.IGNORECASE)),

    (PLAN, 0.55, re.compile(
        r"\b(i'?m going to|i will|i plan to|i want to|i need to|"
        r"i'?m planning|next week i|tomorrow i|i'?ll)\b", re.IGNORECASE)),

    (FEELING, 0.5, re.compile(
        r"\b(i feel|i'?m (?:tired|exhausted|excited|worried|stressed|"
        r"frustrated|happy|sad|anxious|burnt out|burned out))\b",
        re.IGNORECASE)),
)

# The user saying, in so many words, that this matters. Enough on its
# own: an explicit instruction to remember outranks any heuristic.
EXPLICIT = re.compile(
    r"\b(remember (?:that|this)|don'?t forget|keep in mind|"
    r"for future reference|note that i|important:|nhớ (?:là|rằng))\b",
    re.IGNORECASE,
)

# Marks a statement as being about right now and nothing beyond it.
# These become temporary context instead of episodic memory: true for
# an hour, misleading for a month.
TRANSIENT = re.compile(
    r"\b(right now|at the moment|currently at|today i'?m|"
    r"this (?:morning|afternoon|evening) i'?m|i'?m (?:at|in) the|"
    r"just woke up|about to (?:eat|sleep|leave|go out))\b",
    re.IGNORECASE,
)

# Things people say that carry no information about them at all.
FILLER = frozenset({
    "ok", "okay", "k", "kk", "yes", "no", "yeah", "yep", "nope", "sure",
    "thanks", "thank you", "ty", "thx", "cool", "nice", "lol", "lmao",
    "haha", "hmm", "hm", "oh", "ah", "wow", "damn", "bruh", "bro",
    "good", "great", "perfect", "fine", "alright", "right", "true",
    "ừ", "uh", "vâng", "dạ", "được", "cảm ơn", "cám ơn", "ok bro",
})


@dataclass(frozen=True)
class Candidate:
    """
    A statement that might be worth storing, and the reasoning.

    `reason` is populated on a rejection as well as an acceptance. A
    selector that only explains itself when it fires cannot be tuned,
    and this one will need tuning.
    """

    text: str
    category: str = EVENT
    importance: float = 0.0
    confidence: float = 0.0
    reason: str = ""
    transient: bool = False
    occurred_at: datetime | None = None

    @property
    def accepted(self) -> bool:
        return bool(self.text) and self.importance >= DEFAULT_THRESHOLD

    @classmethod
    def rejected(cls, reason: str) -> "Candidate":
        return cls(text="", reason=reason)


def _is_machine_shaped(text: str) -> bool:
    return any(pattern.search(text) for pattern in MACHINE_SHAPED)


def _normalise(text: str) -> str:
    return " ".join(text.strip().split())


class MemorySelector:
    """
    Decides whether something the user said should become a memory.

    Stateless and deterministic: the same sentence always gets the same
    answer, which is what makes the behaviour testable and what makes a
    surprising memory explainable after the fact.
    """

    def __init__(self, threshold: float = DEFAULT_THRESHOLD):
        self.threshold = float(threshold)

    def evaluate(self, role: str, content: str) -> Candidate:
        """
        Judge one turn. The result is a Candidate whose `accepted` says
        whether it should be persisted.
        """

        if (role or "").lower() != "user":
            # Aura's own words, a tool result, or a machine turn. None of
            # it is the user's life, and Phase 7 settled that machine
            # turns do not enter memory at all.
            return Candidate.rejected(f"not a user turn (role={role!r})")

        text = _normalise(content or "")

        if not text:
            return Candidate.rejected("empty")

        if len(text) < MIN_LENGTH:
            return Candidate.rejected("too short to carry meaning")

        if text.lower().strip(".!?,") in FILLER:
            return Candidate.rejected("conversational filler")

        if len(text) > MAX_LENGTH:
            return Candidate.rejected("too long; looks pasted rather than said")

        if _is_machine_shaped(text):
            return Candidate.rejected("machine-shaped text, not a statement")

        if QUESTION.match(text):
            return Candidate.rejected("a question, not a statement")

        if COMMAND.match(text):
            return Candidate.rejected("an instruction, not a statement")

        explicit = bool(EXPLICIT.search(text))

        if not explicit and not FIRST_PERSON.search(text):
            # About the world, not about the person. The world is what a
            # search engine is for.
            return Candidate.rejected("not about the user")

        category, score = self._score(text)

        transient = bool(TRANSIENT.search(text)) and not explicit

        if transient and score < self.threshold:
            # "I'm at a cafe right now" carries no lasting signal, and
            # that is the correct reading of it: it is worth holding for
            # the next hour and worth nothing after that. Accepted at the
            # bar rather than above it, so it becomes temporary context
            # and never an episodic row.
            category = category or "context"
            score = self.threshold

        if explicit:
            # The user asked. That outranks the heuristic.
            category = category or EVENT
            score = max(score, 0.9)

        if score < self.threshold:
            return Candidate.rejected(
                f"below the bar ({score:.2f} < {self.threshold:.2f})"
            )

        return Candidate(
            text=text,
            category=category,
            importance=round(min(score, 1.0), 3),
            confidence=round(min(score, 1.0), 3),
            reason=(
                "explicitly asked to remember" if explicit
                else "passing remark about right now" if transient
                else f"{category} statement about the user"
            ),
            transient=transient,
        )

    def _score(self, text: str) -> tuple[str, float]:
        """
        The strongest matching signal, plus a little for corroboration.

        First match wins the category so the label is the most specific
        one that applied; additional matches only nudge the score, since
        a sentence that is both a preference and a plan is not twice as
        memorable.
        """

        best_category = ""
        best_weight = 0.0
        matches = 0

        for category, weight, pattern in SIGNALS:

            if pattern.search(text):

                matches += 1

                if weight > best_weight:
                    best_category, best_weight = category, weight

        if not matches:
            return "", 0.0

        return best_category, best_weight + 0.05 * (matches - 1)


def occurred_at_for(text: str, now: datetime) -> datetime:
    """
    When the event in `text` actually happened.

    Only the unambiguous cases are handled: "yesterday" and "last
    night" clearly move the event off today, and getting those right is
    most of the value. Anything vaguer is dated to when it was said,
    which is the honest default - Aura knows when she was told.
    """

    lowered = text.lower()

    if re.search(r"\b(yesterday|last night|hôm qua|tối qua)\b", lowered):
        return now.replace(hour=20, minute=0, second=0, microsecond=0) - (
            _one_day()
        )

    return now


def _one_day():
    from datetime import timedelta

    return timedelta(days=1)


def resolve_occurred_at(value, now: datetime) -> datetime:
    """Normalise whatever a caller passed as an occurrence time."""

    parsed = parse_timestamp(value)

    return parsed if parsed is not None else now
