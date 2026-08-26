"""
The user model.

A structured, long-term picture of the person Aura is talking to. Not
episodic memory - nothing here is an event with a date. Not the flat
profile store either, which holds what the user typed with no notion of
how sure Aura is about it.

The distinction this module exists to enforce:

    confirmed   the user said so
    inferred    Aura worked it out
    unknown     no row, and Aura says so

**Nothing promotes an inference to a confirmation except the user.**
There is no code path that raises `status` on its own - not high
confidence, not repeated corroboration, not age. An inference that has
been right a hundred times is still an inference, and the moment Aura
starts stating her guesses as facts about someone's personality she is
lying with a straight face. `confirm()` is the only door and it is only
called when the user actually said it.

Corrections are the other half. "You remembered that wrong" must change
the stored entry, not produce an apology and leave the entry in place.
`correct()` overwrites the value, sets the status to confirmed - a
correction is by definition the user speaking - and stamps
`last_confirmed_at`.

Time-sensitivity is `valid_until`. A trait has none. "Currently working
on Phase 8" has one, and once it passes the entry stops being
authoritative without anyone having to remember to delete it.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from core.temporal import local_now, parse_timestamp
from memory.models import UserModelEntry, timestamp_now
from memory.profile import normalise_key
from memory.retrieval import tokenize
from memory.sqlite import SessionLocal, db_lock, init_database


MAX_KEY = 96
MAX_VALUE = 2000


class Status(str, Enum):
    """
    How Aura came to believe something.

    UNKNOWN is never stored - it is what `status_of` returns when there
    is no row, so "Aura does not know" is a first-class answer rather
    than an empty string that reads like a value.
    """

    CONFIRMED = "confirmed"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


# Categories. Kept as constants so a typo is an ImportError rather than
# a silently unqueryable row.
IDENTITY = "identity"
PERSONALITY = "personality"
COMMUNICATION = "communication"
VALUES = "values"
FEEDBACK = "feedback"
DECISION = "decision"
THINKING = "thinking"
MOTIVATION = "motivation"
INTEREST = "interest"
PROJECT = "project"

# The same ten as a set, for callers that have to *check* a category
# rather than pass one.
#
# The comment above is true only for callers that import these names. A
# category can also arrive as free text from a language model choosing an
# argument for the `remember` tool, and that caller cannot get an
# ImportError - `category="notes"` would write a row that `all(category=)`
# and `valid(category=)` can never return, which is the silently
# unqueryable row the constants exist to prevent. Anything taking a
# category from outside Python checks it against this.
CATEGORIES = frozenset(
    {
        IDENTITY,
        PERSONALITY,
        COMMUNICATION,
        VALUES,
        FEEDBACK,
        DECISION,
        THINKING,
        MOTIVATION,
        INTEREST,
        PROJECT,
    }
)


@dataclass(frozen=True)
class Belief:
    """
    One thing Aura believes about the user, with its provenance.

    A read model. The row stays in the session; this is what callers
    reason about, and it cannot be accidentally mutated into the
    database.
    """

    key: str
    value: str
    category: str = IDENTITY
    status: Status = Status.INFERRED
    confidence: float = 0.5
    source: str = "user"
    updated_at: str = ""
    last_confirmed_at: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None

    @property
    def confirmed(self) -> bool:
        return self.status is Status.CONFIRMED

    def valid_at(self, moment: datetime) -> bool:
        """Whether this belief is in force at `moment`."""

        start = parse_timestamp(self.valid_from)

        if start is not None and moment < start:
            return False

        end = parse_timestamp(self.valid_until)

        if end is not None and moment >= end:
            return False

        return True

    def render(self) -> str:
        """
        One prompt line.

        An inference is marked as one. This is the whole honesty
        contract made visible: the model is told which of these the user
        said and which Aura guessed, so it can hedge on the guesses
        instead of asserting them.
        """

        label = self.key.split(".", 1)[-1].replace("_", " ")

        if self.status is Status.CONFIRMED:
            return f"{label}: {self.value}"

        return f"{label}: {self.value} (inferred)"


def _clamp(value, default: float = 0.5) -> float:

    try:
        number = float(value)
    except (TypeError, ValueError):
        return default

    return max(0.0, min(1.0, number))


def _as_text(moment) -> str | None:

    if moment is None:
        return None

    if isinstance(moment, datetime):
        return moment.isoformat(timespec="seconds")

    return str(moment)


class UserModel:
    """
    Storage and querying for the long-term model.

    Keys are namespaced slugs - "identity.primary_language",
    "personality.curiosity" - so a category can be selected without a
    join and a value can be corrected in place rather than accumulating
    contradictions.
    """

    def __init__(self, session=None, clock=local_now):

        if session is None:
            init_database()
            session = SessionLocal()

        self.session = session
        self.clock = clock

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def _normalise(self, key: str) -> str:
        """Namespaced slug: the dot survives, everything else is slugged."""

        text = str(key or "").strip().lower()

        if "." in text:
            head, tail = text.split(".", 1)
            return f"{normalise_key(head)}.{normalise_key(tail)}"[:MAX_KEY]

        return normalise_key(text)[:MAX_KEY]

    def _write(
        self,
        key: str,
        value: str,
        status: Status,
        category: str,
        confidence: float,
        source: str,
        valid_from=None,
        valid_until=None,
        confirmed: bool = False,
    ) -> Belief | None:

        slug = self._normalise(key)

        if not slug:
            return None

        text = str(value or "").strip()[:MAX_VALUE]

        if not text:
            return None

        stamp = timestamp_now()

        # Read-then-write under the lock, so two threads cannot each find
        # no entry and each insert one against a unique key.
        with db_lock:

            entry = (
                self.session.query(UserModelEntry)
                .filter(UserModelEntry.key == slug)
                .one_or_none()
            )

            if entry is None:

                entry = UserModelEntry(
                    key=slug,
                    value=text,
                    category=str(category or IDENTITY)[:32],
                    status=status.value,
                    confidence=_clamp(confidence),
                    source=str(source or "user")[:24],
                    created_at=stamp,
                    updated_at=stamp,
                    last_confirmed_at=stamp if confirmed else None,
                    valid_from=_as_text(valid_from),
                    valid_until=_as_text(valid_until),
                )

                self.session.add(entry)

            else:
                entry.value = text
                entry.category = str(category or entry.category)[:32]
                entry.status = status.value
                entry.confidence = _clamp(confidence)
                entry.source = str(source or entry.source)[:24]
                entry.updated_at = stamp

                if confirmed:
                    entry.last_confirmed_at = stamp

                if valid_from is not None:
                    entry.valid_from = _as_text(valid_from)

                if valid_until is not None:
                    entry.valid_until = _as_text(valid_until)

            self.session.commit()

            return self._belief(entry)

    def confirm(
        self,
        key: str,
        value: str,
        category: str = IDENTITY,
        confidence: float = 1.0,
        source: str = "user",
        valid_from=None,
        valid_until=None,
    ) -> Belief | None:
        """
        Record something the user actually said.

        The only way an entry becomes CONFIRMED. Called when the user
        stated it, corrected it, or explicitly agreed with it - never
        because Aura became more sure of her own guess.
        """

        return self._write(
            key,
            value,
            status=Status.CONFIRMED,
            category=category,
            confidence=confidence,
            source=source,
            valid_from=valid_from,
            valid_until=valid_until,
            confirmed=True,
        )

    def infer(
        self,
        key: str,
        value: str,
        category: str = IDENTITY,
        confidence: float = 0.5,
        source: str = "inference",
        valid_from=None,
        valid_until=None,
    ) -> Belief | None:
        """
        Record something Aura worked out.

        An inference never overwrites a confirmation. The user's own
        word outranks a guess no matter how confident the guess is, and
        without this rule a chain of plausible inferences can quietly
        erase something the user actually said.
        """

        slug = self._normalise(key)

        existing = self.get(slug)

        if existing is not None and existing.confirmed:
            return existing

        return self._write(
            key,
            value,
            status=Status.INFERRED,
            category=category,
            confidence=confidence,
            source=source,
            valid_from=valid_from,
            valid_until=valid_until,
            confirmed=False,
        )

    def correct(
        self,
        key: str,
        value: str,
        category: str | None = None,
        source: str = "correction",
    ) -> Belief | None:
        """
        The user says the stored value is wrong.

        Overwrites and confirms in one step. A correction is the user
        speaking, so the result is CONFIRMED regardless of what the
        entry was before - including when it replaces an earlier
        confirmation, because people change and the newer statement
        wins.

        Corrects an unknown key by creating it. Someone saying "I don't
        like that any more" about something Aura never recorded is still
        telling her something true.
        """

        existing = self.get(key)

        return self.confirm(
            key,
            value,
            category=category or (existing.category if existing else IDENTITY),
            confidence=1.0,
            source=source,
        )

    def forget(self, key: str) -> bool:

        slug = self._normalise(key)

        with db_lock:

            entry = (
                self.session.query(UserModelEntry)
                .filter(UserModelEntry.key == slug)
                .one_or_none()
            )

            if entry is None:
                return False

            self.session.delete(entry)
            self.session.commit()

        return True

    def clear(self) -> None:

        with db_lock:
            self.session.query(UserModelEntry).delete()
            self.session.commit()

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    @staticmethod
    def _belief(entry: UserModelEntry) -> Belief:

        try:
            status = Status(entry.status)
        except ValueError:
            status = Status.INFERRED

        return Belief(
            key=entry.key,
            value=entry.value,
            category=entry.category,
            status=status,
            confidence=entry.confidence,
            source=entry.source,
            updated_at=entry.updated_at,
            last_confirmed_at=entry.last_confirmed_at,
            valid_from=entry.valid_from,
            valid_until=entry.valid_until,
        )

    def get(self, key: str) -> Belief | None:
        """The belief, or None when Aura does not know."""

        slug = self._normalise(key)

        with db_lock:
            entry = (
                self.session.query(UserModelEntry)
                .filter(UserModelEntry.key == slug)
                .one_or_none()
            )

        return self._belief(entry) if entry is not None else None

    def status_of(self, key: str) -> Status:
        """CONFIRMED, INFERRED, or UNKNOWN. Never guesses."""

        belief = self.get(key)

        return belief.status if belief is not None else Status.UNKNOWN

    def value_of(self, key: str) -> str:
        """The value, or "" when unknown. Convenience for callers."""

        belief = self.get(key)

        return belief.value if belief is not None else ""

    def all(self, category: str | None = None) -> list[Belief]:

        with db_lock:

            query = self.session.query(UserModelEntry)

            if category:
                query = query.filter(UserModelEntry.category == category)

            entries = query.order_by(UserModelEntry.key.asc()).all()

        return [self._belief(entry) for entry in entries]

    def valid(self, category: str | None = None) -> list[Belief]:
        """Only what is in force right now."""

        now = self.clock()

        return [
            belief for belief in self.all(category) if belief.valid_at(now)
        ]

    # ------------------------------------------------------------------
    # Prompt integration
    # ------------------------------------------------------------------

    def relevant(self, query: str, limit: int = 6) -> list[Belief]:
        """
        The beliefs worth spending prompt space on for this turn.

        The whole model is far too large to inject - that is the failure
        mode this method exists to prevent. Selection is lexical overlap
        against the key and value, with confidence and confirmation
        breaking ties, and the result is hard-bounded by `limit`.

        Beliefs that are not currently valid are excluded outright: a
        stale entry is worse than a missing one, because the model will
        use it.
        """

        wanted = tokenize(query)

        if not wanted:
            return []

        now = self.clock()

        scored = []

        for belief in self.all():

            if not belief.valid_at(now):
                continue

            # The key is split on separators as well as dots: a query
            # asking about "language" must match
            # "identity.primary_language", or a namespaced key can never
            # be found by the words inside it.
            tokens = tokenize(belief.key.replace(".", " ").replace("_", " ")) | (
                tokenize(belief.value)
            )

            overlap = len(wanted & tokens)

            if not overlap:
                continue

            scored.append(
                (
                    overlap / len(wanted),
                    1 if belief.confirmed else 0,
                    belief.confidence,
                    belief,
                )
            )

        scored.sort(key=lambda item: item[:3], reverse=True)

        return [belief for *_scores, belief in scored[: max(0, int(limit))]]

    def render(self, query: str = "", limit: int = 6) -> list[str]:
        """
        Prompt lines for this turn.

        With a query, only relevant beliefs. Without one, the confirmed
        identity and communication basics - the things that shape how
        Aura talks regardless of subject, and nothing else. Injecting
        the full model on every turn would be exactly the prompt bloat
        this design is meant to avoid.
        """

        if query:
            return [belief.render() for belief in self.relevant(query, limit)]

        now = self.clock()

        core = [
            belief
            for belief in self.all()
            if belief.category in (IDENTITY, COMMUNICATION)
            and belief.confirmed
            and belief.valid_at(now)
        ]

        return [belief.render() for belief in core[: max(0, int(limit))]]

    def __len__(self) -> int:
        with db_lock:
            return self.session.query(UserModelEntry).count()
