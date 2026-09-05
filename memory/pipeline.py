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

from core.logger import logger
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

        # The semantic half, present only when the builder configured
        # it. None means lexical-only - the state everything was in
        # before semantic memory existed, and the state it falls back
        # to whenever the embedding provider cannot serve.
        self.semantic_indexer = None

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

        # Semantic indexing happens AFTER the row is committed, and can
        # never block or undo it: the memory exists lexically no matter
        # what the embedding provider does next. A failure is recorded
        # by the indexer (status + diagnostics) and shows up nowhere
        # here - this method's contract is "the turn stored the memory",
        # which stays true.
        if self.semantic_indexer is not None:
            try:
                self.semantic_indexer.index(episode)
            except Exception as error:  # noqa: BLE001 - belt and braces:
                # the indexer itself never raises, but the invariant
                # "indexing cannot break persistence" is worth more
                # than the cost of this line.
                logger.warning(
                    "Semantic indexing raised unexpectedly (%s); the "
                    "memory is stored and remains lexically retrievable",
                    type(error).__name__,
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
        recall_episodic: bool | None = None,
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

        # `None` - the default, and what the one production caller passes
        # by passing nothing - defers to the owner. An explicit argument
        # still wins, because callers that pass one mean it.
        #
        # Until this read existed, `recall_enabled` was written by
        # `build_memory_pipeline` from `memory.recall` and read by nobody,
        # so the literal `True` that used to sit in the signature decided
        # instead: the owner set the key, the store accepted it,
        # `effective` reported it back, and Aura recalled anyway. That is
        # the silent override section 2 forbids, and it is worse than an
        # unimplemented setting because it looks implemented.
        if recall_episodic is None:
            recall_episodic = self.recall_enabled

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

    `recall_enabled` is read from `memory.recall` - the same key
    `launcher/services.py` reads to choose `KeywordRetriever` over
    `NullRetriever` - so switching recall off silences both halves rather
    than only the older one.

    That the key covers *this* half was not obvious, and two places in
    the repository disagreed about it. `config.yaml` calls it "keyword
    search over the older transcript", which is the Sprint 5 mechanism
    and would scope it to the legacy retriever alone. The phone calls it
    "Use memory in replies / Look things up from past conversations while
    answering", and `PrivacySection` lists it under privacy as "turn
    recall and the profile off".

    The phone wins, for two reasons. It is the settings surface section 2
    names as the owner's, and it is the one making a *privacy* promise -
    when a capability reading and a privacy reading of the same switch
    conflict, being wrong in the privacy direction means past
    conversation content reaching a prompt after the owner said not to.
    So the phone was displaying "off" while Aura recalled: the display
    was not the thing that was wrong.

    Consequence worth stating rather than discovering: the shipped
    default is `recall: false`, so honouring it *reduces* what a current
    deployment injects. One toggle restores it, and the toggle now does
    what its label says.

    It gates the episodic search and nothing else. The user model is who
    the owner *is* and the temporary tier is what is true this minute;
    neither is a search over past conversations, which is what the label
    promises, and folding them in would make one checkbox mean three
    things.

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

    # The semantic half, only when asked for AND able to exist. Any
    # misconfiguration resolves to None and the pipeline keeps the bare
    # lexical retriever - identical behavior to before this block,
    # which is what "optional" has to mean in code, not only in prose.
    semantic_settings = settings.get("semantic") or {}

    from memory.embeddings import build_embedding_provider

    provider = build_embedding_provider({"semantic": semantic_settings})

    if provider is not None and not pipeline.recall_enabled:
        # Two switches, one of which silently wins. `memory.recall`
        # gates the whole episodic search, so with it off the vectors
        # would never be consulted no matter what `semantic.enabled`
        # says - and the owner turning semantic recall ON would see
        # nothing happen, with nothing anywhere saying why.
        #
        # The indexer is not built either, and that is deliberate
        # rather than incidental: embedding a memory means sending its
        # text to the provider, which for a REMOTE provider is the
        # exfiltration boundary. Someone who switched recall off gets
        # no embedding calls, not merely no results.
        logger.warning(
            "memory.semantic.enabled is true but memory.recall is false "
            "- semantic recall stays off, and nothing is indexed. "
            "memory.recall gates every episodic search, lexical and "
            "semantic alike."
        )

    if provider is not None and pipeline.recall_enabled:

        from memory.semantic import (
            DEFAULT_SEMANTIC_WEIGHT,
            HybridRetriever,
            SemanticIndexer,
            SemanticRetriever,
        )

        indexer = SemanticIndexer(
            provider,
            session=pipeline.episodic.session,
            batch_size=int(semantic_settings.get("batch_size", 32)),
        )

        hybrid = HybridRetriever(
            lexical=pipeline.retriever,
            semantic=SemanticRetriever(
                pipeline.episodic,
                indexer,
                clock=pipeline.clock.now,
                scope=int(settings.get("retrieval_scope", SCOPE)),
                # None means "use the provider's own measured floor".
                min_similarity=(
                    float(semantic_settings["min_similarity"])
                    if semantic_settings.get("min_similarity") is not None
                    else None
                ),
            ),
            weight=float(
                semantic_settings.get("weight", DEFAULT_SEMANTIC_WEIGHT)
            ),
        )

        pipeline.retriever = hybrid
        pipeline.semantic_indexer = indexer

    return pipeline
