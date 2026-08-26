"""
The observation engine.

An observation is a measurement of a device or environment at one instant,
and every observation says so explicitly: what it is, when it was taken,
where it came from, what it belongs to, and a content hash of exactly what
was seen.

This module exists because of how the old agent moved screen state around.
A snapshot captured for step three of one task could resurface as the
current screen of step one of another; a foreground-app answer from
minutes ago was indistinguishable from one taken now; nothing could ask
"is this observation still fresh?" because nothing knew when an
observation had been made. The failures were never lies inside the data -
they were missing identity around it.

Two rules the rest of the system depends on:

    1. An observation is immutable. The screen changed? That is a new
       observation with a new id, not an edit to this one.
    2. Stale is a property, not a verdict. `freshness()` reports age
       honestly; callers decide what staleness means for them. Nothing
       here ever relabels an old observation as current.

Current-app metadata (FOREGROUND_APP) and pixels (SCREENSHOT) are
different kinds on purpose. Answering "what app am I in" from metadata is
cheap and honest; answering "what is on my screen" needs a visual
observation taken now. Conflating them is how a package name once became
a hallucinated screen description.
"""

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field, replace

from core.ids import is_valid_id, new_observation_id


class ObservationKind:
    """
    What an observation measured.

    Strings rather than an Enum so wire payloads can carry the kind
    without a registry of legal values living in two processes that do
    not deploy together. Unknown kinds are stored and returned verbatim;
    nothing here needs to interpret them.
    """

    FOREGROUND_APP = "foreground_app"
    ACCESSIBILITY_TREE = "accessibility_tree"
    SCREENSHOT = "screenshot"
    SCREEN_TEXT = "screen_text"
    DEVICE_STATE = "device_state"
    TOOL_POSTCONDITION = "tool_postcondition"


# How long an observation may be and still be called fresh by default.
# Chosen from device behaviour, not guessed: a warm activity draws within
# about a second of a transition (measured, see the Android settle
# budgets), so ten seconds is generous for a reasoning round while still
# catching the "tree from three minutes ago" failure this module exists
# to prevent.
DEFAULT_FRESH_WINDOW_S = 10.0


def hash_payload(data) -> str:
    """
    A stable content hash of JSON-shaped data.

    Canonical form - sorted keys, no whitespace - so the same payload
    hashes identically regardless of dict insertion order. This is what
    makes "the screen changed between two captures" decidable: different
    content, different hash; identical content proves nothing changed,
    which is itself evidence a capture did not actually happen.
    """

    canonical = json.dumps(
        data, sort_keys=True, separators=(",", ":"), default=str
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Observation:
    """
    One measurement, with its full provenance attached.

    `data` holds the measurement itself. For an accessibility tree that
    is the serialised nodes; for a screenshot the frame's metadata plus
    whatever reference the caller agreed to carry (a path or an upload
    id, never raw bytes - observations are records, not blobs). For a
    tool postcondition it is the state the tool claimed to produce.
    """

    observation_id: str
    kind: str
    source: str
    observed_at: float
    content_hash: str
    data: dict
    task_id: str = ""
    run_id: str = ""
    session_id: str = ""
    provenance: dict = field(default_factory=dict)

    def age(self, now: float | None = None) -> float:
        """Seconds since this observation was taken."""

        moment = time.time() if now is None else now
        return max(0.0, moment - self.observed_at)

    def freshness(
        self,
        now: float | None = None,
        max_age_s: float = DEFAULT_FRESH_WINDOW_S,
    ) -> str:
        """`fresh` or `stale`, decided by age against `max_age_s`."""

        return (
            "fresh" if self.age(now) <= max_age_s else "stale"
        )

    def is_fresh(
        self,
        now: float | None = None,
        max_age_s: float = DEFAULT_FRESH_WINDOW_S,
    ) -> bool:

        return self.freshness(now=now, max_age_s=max_age_s) == "fresh"

    def with_scope(
        self,
        task_id: str | None = None,
        run_id: str | None = None,
        session_id: str | None = None,
    ) -> "Observation":
        """
        The same measurement, stamped for a task/run/session.

        Used when a device captures first and learns which run it serves
        second. Identity fields are stamped, never invented here: an
        observation whose ids were claimed by another run keeps them.
        """

        return replace(
            self,
            task_id=task_id if task_id is not None else self.task_id,
            run_id=run_id if run_id is not None else self.run_id,
            session_id=(
                session_id if session_id is not None else self.session_id
            ),
        )

    def to_dict(self) -> dict:
        """The wire shape, matching the field order of this class."""

        return {
            "observation_id": self.observation_id,
            "kind": self.kind,
            "source": self.source,
            "observed_at": self.observed_at,
            "content_hash": self.content_hash,
            "freshness": self.freshness(),
            "data": self.data,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "provenance": self.provenance,
        }


class StaleObservationError(LookupError):
    """
    Raised when a caller demands a fresh observation and only stale ones
    exist.

    A distinct exception rather than a None return, because the two cases
    mean opposite things: no observation at all usually means "capture
    one", while a stale one means "the pipeline that should have refreshed
    it did not", which is worth surfacing rather than papering over.
    """


class ObservationStore:
    """
    Where observations live once taken.

    Per-run scoping is deliberate: `latest(kind)` without a run answers
    for diagnostics, but anything feeding a model decision asks with the
    run id, so one run can never read another run's screen as its own -
    the exact cross-task leak the identifier scheme exists to prevent.
    """

    def __init__(self, clock=time.time):
        # Injectable clock for the same reason the Android settle loops
        # inject theirs: a test drives time deterministically instead of
        # sleeping.
        self._clock = clock
        self._observations: list[Observation] = []
        self._lock = threading.Lock()

    def create(
        self,
        kind: str,
        source: str,
        data: dict,
        task_id: str = "",
        run_id: str = "",
        session_id: str = "",
        provenance: dict | None = None,
        observed_at: float | None = None,
    ) -> Observation:
        """
        Record a new observation, minting everything it lacks.

        The id, timestamp and content hash are assigned here and nowhere
        else, so there is exactly one definition of what those mean.
        """

        observation = Observation(
            observation_id=new_observation_id(),
            kind=kind,
            source=source,
            observed_at=self._clock() if observed_at is None else observed_at,
            content_hash=hash_payload(data),
            data=data,
            task_id=task_id,
            run_id=run_id,
            session_id=session_id,
            provenance=dict(provenance or {}),
        )

        with self._lock:
            self._observations.append(observation)

        return observation

    def record(self, observation: Observation) -> Observation:
        """
        Accept an observation minted elsewhere - typically by a device.

        An observation arriving without an id or without a content hash
        is completed rather than rejected: the device is the authority on
        *what* it saw, and this store is the authority on bookkeeping. An
        id from a foreign scheme is refused, because accepting it would
        put an uncheckable identity into circulation.
        """

        supplied_id = observation.observation_id

        if supplied_id and not is_valid_id(supplied_id):
            raise ValueError(
                f"observation id {supplied_id!r} is not a valid id"
            )

        complete = observation

        if not supplied_id or not observation.content_hash:
            complete = replace(
                observation,
                observation_id=supplied_id or new_observation_id(),
                content_hash=(
                    observation.content_hash
                    or hash_payload(observation.data)
                ),
            )

        with self._lock:
            self._observations.append(complete)

        return complete

    def latest(
        self,
        kind: str,
        run_id: str | None = None,
    ) -> Observation | None:
        """
        The most recent observation of `kind`, newest last-write wins.

        None when none exists - never an old observation dressed up as a
        current one.
        """

        with self._lock:
            candidates = [
                observation
                for observation in reversed(self._observations)
                if observation.kind == kind
                and (run_id is None or observation.run_id == run_id)
            ]

        return candidates[0] if candidates else None

    def history(self, run_id: str | None = None) -> list[Observation]:
        """Every recorded observation, oldest first, optionally per run."""

        with self._lock:
            return [
                observation
                for observation in self._observations
                if run_id is None or observation.run_id == run_id
            ]

    def require_fresh(
        self,
        kind: str,
        run_id: str | None = None,
        max_age_s: float = DEFAULT_FRESH_WINDOW_S,
        now: float | None = None,
    ) -> Observation:
        """
        The latest observation of `kind`, and only if it is fresh.

        This is the gate an observation-first runtime walks before any
        state-dependent model call: tap requires a current tree, and this
        is where "current" stops being an adjective and becomes a check.
        """

        moment = self._clock() if now is None else now
        latest = self.latest(kind, run_id=run_id)

        if latest is None:
            raise StaleObservationError(
                f"no {kind} observation exists"
                + (f" for run {run_id}" if run_id else "")
            )

        if not latest.is_fresh(now=moment, max_age_s=max_age_s):
            raise StaleObservationError(
                f"latest {kind} observation {latest.observation_id} is "
                f"{latest.age(moment):.1f}s old (limit {max_age_s}s)"
            )

        return latest
