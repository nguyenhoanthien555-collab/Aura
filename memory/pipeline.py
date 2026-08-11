"""
The memory pipeline.

The phase called for one pipeline from conversation to prompt, in order:

    conversation -> candidate extraction -> classification
        -> importance / confidence -> timestamp -> persistence
        -> retrieval -> relevance ranking -> prompt context

Every stage above lives in one of the modules this one composes:
selection, the episodic store, the temporary context, the user model,
the profile store and the retrievers. What this module adds is the
wiring and the one decision the rest could not make on their own:
*which* of the three kinds of memory a candidate belongs in.

The three kinds, and what happens to each:

    episodic      an event, a fact, a preference, a plan - dated, kept
    temporary     true for right now and nothing beyond - held in
                  process, never written, never auto-promoted
    user model    the user corrected or confirmed something about
                  themselves - updated in place

Only the user's own turns are considered. Assistant replies, tool
results and machine turns never reach the pipeline: they are filtered
before any kind of memory is produced, and a regression test pins that
down so Phase 7's isolation rule cannot silently erode.
"""

from dataclasses import dataclass, field

from core.temporal import TemporalClock, local_now
from memory.episodic import DEFAULT_SCOPE, EpisodicStore
from memory.retrieval import RankedRetriever
from memory.selection import (
    FEELING,
    IDENTITY,
    PLAN,
    PREFERENCE,
    PROJECT,
    MemorySelector,
    occurred_at_for,
)
from memory.temporary import TemporaryContext
from memory.user_model import UserModel
from memory.user_profile_seed import seed_user_model


@dataclass(frozen=True)
class PipelineOutcome:
    """What one turn did to memory, so callers and tests can see it."""

    accepted: bool = False
    kind: str = ""            # "episodic" | "temporary" | "profile"
    category: str = ""
    text: str = ""
    note: str = ""            # why it was stored or why it was not

    @property
    def stored(self) -> bool:
        return bool(self.kind)


class MemoryPipeline:
    """
    One entry point for everything the brain does with memory.

    Constructed once per session and shared by the whole process - the
    same pipeline that is written to is the one that is read from, and
    the same selection rules apply to every turn.

    `user_model_ready` is the answer to "has the profile been seeded".
    It defaults to False because a bare pipeline has no opinion about
    whether the user wants the bundled profile at all; the composition
    root decides and flips it to True. Seeding itself is idempotent, so
    the flip can happen on any turn without harm.
    """

    def __init__(
        self,
        session=None,
        selector: MemorySelector | None = None,
        episodic: EpisodicStore | None = None,
        temporary: TemporaryContext | None = None,
        user_model: UserModel | None = None,
        clock: TemporalClock | None = None,
    ):

        self.selector = selector or MemorySelector()
        self.episodic = episodic or EpisodicStore(session=session)
        self.temporary = temporary or TemporaryContext()
        self.user_model = user_model or UserModel(session=session)
        self.clock = clock or TemporalClock()

        # Retrieval. Composed here because the pipeline is what owns the
        # connection between the two ends of memory.
        self.retriever = RankedRetriever(
            self.episodic,
            clock=self.clock.now,
            scope=DEFAULT_SCOPE,
        )

        self.user_model_ready = False

        # Whether episodic recall is consulted at all. Set from
        # `memory.recall` by the builder; an attribute rather than a
        # builder-only local so callers can read it without knowing how
        # the pipeline was constructed.
        self.recall_enabled = True

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def observe(self, role: str, content: str) -> PipelineOutcome:
        """
        Feed one turn into memory. What is stored depends on what it is.
        """

        candidate = self.selector.evaluate(role, content)

        if not candidate.accepted:
            return PipelineOutcome(
                accepted=False,
                note=candidate.reason or "not selected",
            )

        if candidate.transient:
            self.temporary.note(candidate.text, category=candidate.category)

            return PipelineOutcome(
                accepted=True,
                kind="temporary",
                category=candidate.category,
                text=candidate.text,
                note="passing remark; kept as temporary context only",
            )

        now = self.clock.now()

        episode = self.episodic.remember(
            content=candidate.text,
            category=candidate.category,
            importance=candidate.importance,
            confidence=candidate.confidence,
            occurred_at=occurred_at_for(candidate.text, now),
        )

        if episode is None:
            return PipelineOutcome(
                accepted=False, note="nothing to store"
            )

        return PipelineOutcome(
            accepted=True,
            kind="episodic",
            category=candidate.category,
            text=candidate.text,
            note=f"stored as episodic ({candidate.category})",
        )

    def remember_user_stated(
        self,
        key: str,
        value: str,
        category: str = IDENTITY,
        confidence: float = 0.9,
    ):
        """A fact the user stated, stored as CONFIRMED."""

        return self.user_model.confirm(
            key, value, category=category, confidence=confidence
        )

    def remember_user_correction(self, key: str, value: str, category: str | None = None):
        """The user corrected something about themselves. In place."""

        return self.user_model.correct(key, value, category=category)

    def ensure_profile(self) -> int:
        """Seed the initial profile if it is not there. Idempotent."""

        written = seed_user_model(self.user_model)
        self.user_model_ready = True
        return written

    # ------------------------------------------------------------------
    # Reading - the prompt side
    # ------------------------------------------------------------------

    def memory_lines(
        self,
        query: str,
        recall_episodic: bool = True,
        max_episodic: int = 3,
        max_temporary: int = 3,
        max_user_model: int = 6,
    ) -> list[str]:
        """
        Everything the prompt should know about memory, ranked together.

        Returns flat prompt lines in this order:

            user model       who the user is, relevant to this turn
            episodic         what happened, ranked by the retriever
            temporary        what is true right now

        The order is deliberate and is the ranking's job done twice: the
        most stable and most relevant facts first, the passing remarks
        last, so a truncation anywhere drops the least trustworthy lines
        first. The user model query is the same query the episodic
        retriever scores against, so both ends answer the same question.

        The sum is hard-bounded by the caller's limits; it can never be
        "everything in the database".
        """

        lines: list[str] = []

        if max_user_model > 0:
            lines.extend(self.user_model.render(query, limit=max_user_model))

        if recall_episodic and max_episodic > 0:
            lines.extend(self.retriever.search(query, limit=max_episodic))

        if max_temporary > 0:
            lines.extend(self.temporary.render(limit=max_temporary))

        return lines


def build_memory_pipeline(
    config: dict | None = None, session=None, clock=None
) -> MemoryPipeline:
    """
    Composition helper, configurable per the `memory` section.

    `recall_episodic` is read from `memory.recall` in config.yaml - the
    same key the existing keyword recall reads - so switching recall on
    or off does one thing everywhere.

    `clock` is accepted rather than always built here because the
    retriever below captures `clock.now` as a bound method. Building a
    clock internally and letting the caller reassign `pipeline.clock`
    afterwards would leave the ranking dating memories by a different
    object than the prompt dates them by - identical in production, where
    both come from the same config, and wrong the moment a caller injects
    a clock of its own. One clock in, one clock used everywhere.
    """

    from memory.episodic import DEFAULT_SCOPE as SCOPE

    settings = (config or {}).get("memory") or {}

    pipeline = MemoryPipeline(
        session=session,
        clock=clock or TemporalClock.from_config(config),
    )

    pipeline.retriever = RankedRetriever(
        pipeline.episodic,
        clock=pipeline.clock.now,
        scope=int(settings.get("retrieval_scope", SCOPE)),
    )

    pipeline.recall_enabled = bool(settings.get("recall", True))

    return pipeline
