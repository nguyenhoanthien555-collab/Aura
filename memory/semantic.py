"""
Semantic indexing and retrieval, beside the lexical path.

Everything here is OPTIONAL and fails CLOSED. `RankedRetriever` in
memory/retrieval.py is the working recall behavior; nothing in this
module can degrade it. If the embedding provider is unavailable, the
query fails, the index is stale or the config is off, callers get
lexical results exactly as before - and the only observable difference
is a diagnostic line saying why.

Three pieces:

    SemanticIndexer    persist-then-index. EpisodicStore.remember has
                       already committed by the time this runs, so an
                       embedding failure costs the index a row, never
                       the memory a row.
    SemanticRetriever  cosine over the bounded candidate pool, rendered
                       through the same line format the lexical
                       retriever uses, satisfying the same Retriever
                       protocol.
    HybridRetriever    lexical + semantic, merged by reciprocal rank
                       fusion (deterministic, scale-free), reranked by
                       an isolated method confidence and importance can
                       only tie-break, never override.

Invariants this module is responsible for (contract section 2):

    provenance survives    results carry the memory row's own fields;
                           rendering goes through the episodic line
                           format, unchanged from lexical recall.
    ids stay stable        the memory id IS the episodic row id; the
                           vector only references it.
    deletion wins          candidates reach the prompt only through an
                           inner join to the live episodic row, so a
                           forgotten memory's orphaned vector cannot
                           resurrect it.
    scope is enforced      episodic memory is single-tenant by design
                           (see docs/IMPLEMENTATION_STATUS.md); the
                           enforceable scope here is the category
                           filter and the bounded candidate pool, both
                           structural, both query-side.
"""

import array
import json
import math
import time
from dataclasses import dataclass, field

from core.logger import logger
from core.temporal import local_now
from memory.embeddings import EmbeddingUnavailableError
from memory.models import EpisodicMemory, SemanticVector, timestamp_now
from memory.retrieval import RankedRetriever, tokenize
from memory.sqlite import SessionLocal, db_lock, init_database

from core.trace import emit_trace


# Statuses of one memory's semantic half. `stored` is implicit - the
# episodic row exists before any of these can be recorded; the point of
# the vocabulary is to distinguish "indexed" from "index failed" rather
# than hiding the difference behind silence.
INDEXED = "indexed"
INDEX_FAILED = "index_failed"
INDEX_SKIPPED = "index_skipped"
INDEX_STALE = "stale"

# Reciprocal Rank Fusion constant. The standard 60 damps the head of
# each ranking so the two lists contribute comparably; it is a constant
# of the technique rather than a tuned magic number.
RRF_K = 60.0

# Semantic's default share of the fused score (`memory.semantic.weight`).
# 0.5 scales both halves equally, which leaves the ordering identical to
# unweighted RRF - so the knob exists without the default moving anything.
# 0.0 is lexical-only ranking with semantic candidates still merged in
# behind it; 1.0 is the mirror image.
DEFAULT_SEMANTIC_WEIGHT = 0.5

# How deep into each ranking the fusion looks. Two lists of the top few
# candidates each is what fusion can meaningfully combine; ranking 500
# rows twice and fusing all of it would spend work on rows that could
# not reach the prompt under any fusion.
FUSION_DEPTH = 10


@dataclass
class MemoryResult:
    """
    One retrieved memory, with where it came from attached.

    `memory_id` is the episodic row id - stable, the same id
    `EpisodicStore.forget` takes. The provenance fields are the row's
    own, carried through retrieval untouched. The three scores are
    retrieval facts about THIS query, not properties of the memory.
    """

    memory_id: int
    content: str
    category: str
    source: str
    occurred_at: str
    created_at: str
    importance: float
    confidence: float

    retrieval_mode: str = ""       # lexical / semantic / hybrid
    lexical_score: float | None = None
    semantic_score: float | None = None
    final_score: float = 0.0

    def as_dict(self) -> dict:
        return {
            "memory_id": self.memory_id,
            "content": self.content,
            "category": self.category,
            "source": self.source,
            "occurred_at": self.occurred_at,
            "created_at": self.created_at,
            "importance": self.importance,
            "confidence": self.confidence,
            "retrieval_mode": self.retrieval_mode,
            "lexical_score": self.lexical_score,
            "semantic_score": self.semantic_score,
            "final_score": round(self.final_score, 6),
        }


def cosine(left: list[float], right: list[float]) -> float:
    """
    Cosine of two equal-length vectors, 0.0 on any mismatch.

    A dimension mismatch is a programming error upstream (the index
    checks metadata before comparing), so it scores as "no similarity"
    rather than raising through a retrieval path that must not fail.
    """

    if len(left) != len(right) or not left:
        return 0.0

    dot = sum(a * b for a, b in zip(left, right))
    norm_left = math.sqrt(sum(value * value for value in left))
    norm_right = math.sqrt(sum(value * value for value in right))

    if norm_left == 0.0 or norm_right == 0.0:
        return 0.0

    return dot / (norm_left * norm_right)


def encode_vector(vector: list[float]) -> bytes:
    """float32 little-endian blob. float64 loses nothing real here."""

    return array.array("f", [float(value) for value in vector]).tobytes()


def decode_vector(blob: bytes) -> list[float]:
    # array.frombytes mutates and returns None - build on a named array
    # or the result of the expression is None and .tolist() explodes.
    result = array.array("f")
    result.frombytes(blob)
    return result.tolist()


# ----------------------------------------------------------------------
# Indexing: persist first, embed second
# ----------------------------------------------------------------------

class SemanticIndexer:
    """
    Maintains the vector table beside the episodic store.

    Call `index` AFTER `EpisodicStore.remember` returned a row. That
    ordering is the whole safety story: memory persistence never waits
    on an embedding, and an embedding failure is recorded (status,
    diagnostic trace, log) rather than allowed to touch the write path.

    The indexer owns the metadata check. Every read of the vector table
    goes through "does this row's space match the current provider",
    which is how a model change turns into a visible stale state
    instead of silently comparable-looking garbage.
    """

    def __init__(self, provider, session=None, store=None, batch_size: int = 32):
        self.provider = provider
        self.batch_size = max(1, int(batch_size))

        if session is None:
            init_database()
            session = SessionLocal()

        self.session = session
        self.store = store

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def metadata(self):
        """The vector space this indexer builds for."""

        return self.provider.metadata()

    def status(self) -> dict:
        """
        The index state, in counts. Observable rather than assumed.
        """

        meta = self.metadata()

        with db_lock:
            total = self.session.query(SemanticVector).count()
            current = (
                self.session.query(SemanticVector)
                .filter(
                    SemanticVector.provider == meta.provider,
                    SemanticVector.model == meta.model,
                    SemanticVector.dimensions == meta.dimensions,
                    SemanticVector.version == meta.version,
                )
                .count()
            )
            memories = self.session.query(EpisodicMemory).count()

        return {
            "provider": meta.provider,
            "model": meta.model,
            "dimensions": meta.dimensions,
            "version": meta.version,
            "vectors_total": total,
            "vectors_current": current,
            "vectors_stale": total - current,
            "episodic_memories": memories,
            "indexed_ratio": (
                round(current / memories, 4) if memories else 0.0
            ),
        }

    def is_stale(self) -> bool:
        """True when current-space vectors cannot serve retrieval."""

        state = self.status()

        return state["vectors_current"] == 0 and state["vectors_total"] > 0

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def index(self, episode: EpisodicMemory) -> str:
        """
        Embed and store one memory's vector. Never raises.

        Returns one of INDEXED / INDEX_FAILED / INDEX_SKIPPED. SKIPPED
        is the policy refusal (a REMOTE provider with consent off) -
        distinguishable from a failure because it is permanent and
        expected, where a failure is diagnosable.
        """

        meta = self.metadata()

        try:
            vector = self.provider.embed(episode.content)
        except EmbeddingUnavailableError as error:
            self._record_failure(episode, meta, str(error))
            return INDEX_FAILED

        if len(vector) != meta.dimensions and meta.dimensions:
            # A provider that changes its mind about dimensionality
            # between metadata() and embed() is broken; treat it as a
            # failure, never store a vector the metadata lies about.
            self._record_failure(
                episode, meta,
                f"dimension mismatch: metadata says {meta.dimensions}, "
                f"embed returned {len(vector)}",
            )
            return INDEX_FAILED

        row = SemanticVector(
            memory_id=episode.id,
            provider=meta.provider,
            model=meta.model,
            dimensions=meta.dimensions,
            version=meta.version,
            vector=encode_vector(vector),
            created_at=timestamp_now(),
        )

        try:
            with db_lock:
                # Re-indexing the same memory in the same space
                # replaces rather than duplicates.
                self.session.query(SemanticVector).filter(
                    SemanticVector.memory_id == episode.id,
                    SemanticVector.provider == meta.provider,
                    SemanticVector.model == meta.model,
                    SemanticVector.version == meta.version,
                ).delete()

                self.session.add(row)
                self.session.commit()
        except Exception as error:  # noqa: BLE001 - indexing must not raise
            logger.warning(
                "Semantic index write failed for memory %s: %s",
                episode.id, type(error).__name__,
            )
            return INDEX_FAILED

        emit_trace(
            "semantic_index",
            memory_id=episode.id,
            provider=meta.provider,
            model=meta.model,
            dimensions=meta.dimensions,
            status=INDEXED,
            content_chars=len(episode.content or ""),
        )

        return INDEXED

    def index_batch(self, episodes: list) -> dict:
        """
        Embed a list. Returns the status counts.

        One embed failure marks only the affected row; the batch
        boundary is an efficiency fact, not a failure boundary.
        """

        counts = {INDEXED: 0, INDEX_FAILED: 0, INDEX_SKIPPED: 0}

        for episode in episodes:
            counts[self.index(episode)] += 1

        return counts

    def reindex(self, limit: int = 0) -> dict:
        """
        Re-embed everything (or the newest `limit` memories).

        The migration/reindex strategy in one method: after a provider
        or model change, `status()` reports stale rows, `is_stale()` is
        true, and semantic retrieval stays unavailable until this runs.
        Nothing is deleted first - replacement happens per row inside
        the write - so a reindex interrupted halfway leaves the old
        rows stale rather than the index empty.
        """

        query = (
            self.session.query(EpisodicMemory)
            .order_by(EpisodicMemory.id.desc())
        )

        if limit and limit > 0:
            query = query.limit(int(limit))

        episodes = query.all()

        result = self.index_batch(episodes)

        emit_trace(
            "semantic_reindex",
            attempted=len(episodes),
            indexed=result.get(INDEXED, 0),
            failed=result.get(INDEX_FAILED, 0),
        )

        return result

    def forget(self, memory_id: int) -> None:
        """Drop every vector for one memory, in every space."""

        with db_lock:
            self.session.query(SemanticVector).filter(
                SemanticVector.memory_id == int(memory_id)
            ).delete()
            self.session.commit()

    def clear(self) -> None:
        with db_lock:
            self.session.query(SemanticVector).delete()
            self.session.commit()

    # ------------------------------------------------------------------

    def _record_failure(
        self, episode: EpisodicMemory, meta, reason: str
    ) -> None:
        logger.warning(
            "Semantic indexing skipped for memory %s: %s",
            episode.id, reason,
        )

        emit_trace(
            "semantic_index",
            memory_id=episode.id,
            provider=meta.provider,
            model=meta.model,
            status=INDEX_FAILED,
            reason=reason[:200],
        )


# ----------------------------------------------------------------------
# Retrieval
# ----------------------------------------------------------------------

class SemanticRetriever:
    """
    Cosine over the bounded episodic pool, satisfying the same
    Retriever protocol as RankedRetriever.

    Fails closed. Every failure mode - provider refused, provider
    unreachable, malformed vector, stale index, empty query - returns
    an empty candidate list with the reason recorded in `last_reason`
    and a diagnostic trace line. Nothing here raises into the
    conversation, because a broken embedding provider must cost Aura
    its semantic half only, never the turn.
    """

    def __init__(
        self,
        store,
        indexer: SemanticIndexer,
        clock=None,
        scope: int = 400,
        min_similarity: float | None = None,
        categories: tuple = (),
    ):
        self.store = store
        self.indexer = indexer
        self.scope = int(scope)

        # The cosine floor. None means "ask the provider", because the
        # useful floor is a property of the embedding space, not of
        # this class: hashed n-grams collide and score everything above
        # zero, a trained model separates cleanly. The benchmark's
        # sweep is what turned this from a guess into a measurement.
        #
        # An explicit value (from `memory.semantic.min_similarity`)
        # always wins; a provider that declares nothing falls back to
        # the permissive value this parameter used to hardcode.
        if min_similarity is None:
            min_similarity = getattr(
                indexer.provider, "recommended_min_similarity", 0.05
            )

        self.min_similarity = float(min_similarity)

        # Scope isolation, structurally. Aura's episodic memory is
        # single-tenant by design (docs/IMPLEMENTATION_STATUS.md), so
        # the enforceable scope here is the category dimension: an
        # empty tuple means every category, and a non-empty tuple
        # becomes a SQL filter - a memory outside the requested
        # categories cannot be returned no matter how similar its
        # vector is, because the query shape excludes it before
        # similarity is ever computed.
        self.categories = tuple(categories)
        self.last_reason = ""

        if clock is None:
            clock = local_now

        self.clock = clock

    def metadata(self):
        return self.indexer.metadata()

    def rank(self, query: str, limit: int = 3) -> list[tuple]:
        """
        (episode, similarity, now) triples, best first.

        Same shape as RankedRetriever.rank, so the hybrid fuses two
        rankings of the same kind of thing.
        """

        self.last_reason = ""

        started = time.monotonic()

        meta = self.metadata()

        session = self.indexer.session

        try:
            query_vector = self.indexer.provider.embed(str(query or ""))
        except EmbeddingUnavailableError as error:
            self.last_reason = str(error)[:200]
            self._trace(query, 0, started, self.last_reason)
            return []

        # The candidate pool: vectors in the CURRENT space only, inner
        # joined to live episodic rows. Stale rows (other provider,
        # model, version) are invisible here by construction, and a
        # memory deleted since indexing has no live row, so its vector
        # - however similar - cannot reach the result. Both rules are
        # the query shape, not a cleanup job's promise.
        with db_lock:
            candidate_query = (
                session.query(SemanticVector, EpisodicMemory)
                .join(
                    EpisodicMemory,
                    SemanticVector.memory_id == EpisodicMemory.id,
                )
                .filter(
                    SemanticVector.provider == meta.provider,
                    SemanticVector.model == meta.model,
                    SemanticVector.dimensions == meta.dimensions,
                    SemanticVector.version == meta.version,
                )
            )

            if self.categories:
                candidate_query = candidate_query.filter(
                    EpisodicMemory.category.in_(self.categories)
                )

            rows = (
                candidate_query.order_by(SemanticVector.memory_id.desc())
                .limit(max(1, int(self.scope)))
                .all()
            )

        now = self.clock()

        scored: list[tuple] = []

        for vector_row, episode in rows:

            if not RankedRetriever._valid_now(episode, now):
                # A future-dated memory is not recalled as though it
                # had happened - the same temporal rule lexical recall
                # applies. Reaching past it semantically would break
                # an invariant the lexical path already enforces.
                continue

            similarity = cosine(decode_vector(vector_row.vector), query_vector)

            if similarity < self.min_similarity:
                continue

            scored.append((similarity, episode.id, episode))

        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)

        self._trace(query, len(scored), started, "")

        return [
            (episode, round(similarity, 6), now)
            for similarity, _id, episode in scored[: max(0, int(limit))]
        ]

    def search(self, query: str, limit: int = 3) -> list[str]:
        """Rendered lines, best first. Satisfies Retriever."""

        now = self.clock()

        return [
            RankedRetriever._render(episode, now)
            for episode, _score, _now in self.rank(query, limit)
        ]

    def _trace(self, query: str, candidates: int, started: float, reason: str):
        meta = self.metadata()

        emit_trace(
            "semantic_query",
            provider=meta.provider,
            model=meta.model,
            candidates=candidates,
            query_chars=len(query or ""),
            latency_ms=round((time.monotonic() - started) * 1000, 2),
            reason=reason or None,
        )


class HybridRetriever:
    """
    Lexical and semantic recall, fused deterministically.

    The fusion is Reciprocal Rank Fusion: each candidate earns
    sum(1 / (RRF_K + rank)) over every ranking it appears in. RRF was
    chosen over score fusion because the two inputs live in different
    score spaces (lexical overlap in 0..1, cosine similarity in
    -1..1) and no normalization of those spaces existed here to reuse
    - rank positions are the one scale both already share, and RRF is
    parameter-free beyond the standard constant. The choice is pinned
    by the benchmark, which measures all three modes on the same
    fixtures rather than asserting RRF is better in the abstract.

    `weight` (`memory.semantic.weight`) is semantic's share of the fused
    score; lexical takes 1 - weight. It tilts the fusion, it does not
    gate it: at weight 0.0 semantic candidates still appear, ranked
    behind every lexical hit, and at 1.0 the reverse. Neither extreme
    can make a retrieval fail, and neither changes the degradation
    rules below.

    Degradation, by construction:

        semantic fails    -> lexical ranking, last_mode "lexical"
        lexical empty     -> semantic ranking, last_mode "semantic"
        both fail         -> empty, last_mode "empty" (never fabricated)

    `last_mode` after every call records which path served the answer:
        hybrid / lexical / semantic / lexical_fallback /
        semantic_fallback / empty
    """

    def __init__(
        self,
        lexical: RankedRetriever,
        semantic: SemanticRetriever,
        weight: float = DEFAULT_SEMANTIC_WEIGHT,
    ):
        self.lexical = lexical
        self.semantic = semantic

        # `weight` is semantic's share of the fused score; lexical
        # takes the remaining (1 - weight). Clamped rather than
        # validated-and-raised, because a bad config value must not be
        # able to break recall - the whole point of this subsystem is
        # that it degrades instead of failing.
        #
        # The default 0.5 scales BOTH halves by the same constant, so
        # it produces exactly the ordering unweighted RRF produced
        # before this parameter existed. Changing the default is a
        # behavior change; changing the config is the user's choice.
        self.weight = min(1.0, max(0.0, float(weight)))

        self.last_mode = ""

    # ------------------------------------------------------------------
    # The Retriever protocol
    # ------------------------------------------------------------------

    def search(self, query: str, limit: int = 3) -> list[str]:
        """Rendered lines, best first. Satisfies Retriever."""

        results = self.rank_full(query, limit)

        now = self._now()

        # MemoryResult carries the fields the episodic renderer needs
        # (content, occurred_at), so the same line format lexical recall
        # produces is reused here untouched.
        return [
            RankedRetriever._render(result, now)
            for result in results
        ]

    def rank(self, query: str, limit: int = 3) -> list[tuple]:
        """
        (episode, final_score, now) triples, the fused ranking.

        Same triple shape the lexical and semantic rankers return, so
        a caller of either can hold a hybrid without knowing.
        """

        results = self.rank_full(query, limit)

        now = self._now()

        return [(result.episode, result.final_score, now)
                for result in results]

    # ------------------------------------------------------------------
    # The full form, with provenance and scores attached
    # ------------------------------------------------------------------

    def rank_full(self, query: str, limit: int = 3) -> list[MemoryResult]:
        """
        MemoryResults, fused and reranked, with every score attached.
        """

        started = time.monotonic()

        now = self._now()

        lexical_ranked = self._safe_lexical(query, limit * 2)
        semantic_ranked = self._safe_semantic(query, limit * 2)

        # Degradation bookkeeping. The order of these checks IS the
        # policy: hybrid when both contributed, the working half when
        # one failed, empty - never a fabricated line - when neither
        # produced anything.
        if lexical_ranked and semantic_ranked:
            self.last_mode = "hybrid"
        elif lexical_ranked and not semantic_ranked:
            self.last_mode = (
                "lexical" if not self.semantic.last_reason
                else "lexical_fallback"
            )
        elif semantic_ranked and not lexical_ranked:
            self.last_mode = "semantic"
        else:
            self.last_mode = "empty"

        results = self.fuse(lexical_ranked, semantic_ranked, now)

        results = self.rerank(results)

        results = results[: max(0, int(limit))]

        for result in results:
            result.retrieval_mode = self.last_mode

        emit_trace(
            "hybrid_query",
            mode=self.last_mode,
            lexical_candidates=len(lexical_ranked),
            semantic_candidates=len(semantic_ranked),
            returned=len(results),
            query_chars=len(query or ""),
            latency_ms=round((time.monotonic() - started) * 1000, 2),
            semantic_reason=self.semantic.last_reason or None,
        )

        return results

    # ------------------------------------------------------------------
    # Fusion and reranking - the replaceable ranking strategy
    # ------------------------------------------------------------------

    def fuse(
        self,
        lexical_ranked: list[tuple],
        semantic_ranked: list[tuple],
        now=None,
    ) -> list[MemoryResult]:
        """
        Reciprocal Rank Fusion over the two rankings.

        Isolated so a different strategy can be tested against it
        without touching the retrievers or the degradation logic.
        """

        fused: dict[int, MemoryResult] = {}

        lexical_weight = 1.0 - self.weight
        semantic_weight = self.weight

        for rank, (episode, _score, _now) in enumerate(lexical_ranked):
            result = fused.setdefault(episode.id, self._result(episode))
            # `lexical_score` keeps the UNWEIGHTED reciprocal rank, so
            # provenance reports what the lexical ranker actually said
            # about this memory; the weight is applied only where the
            # scores are combined.
            result.lexical_score = 1.0 / (RRF_K + rank + 1)
            result.final_score += lexical_weight * result.lexical_score

        for rank, (episode, similarity, _now) in enumerate(semantic_ranked):
            result = fused.setdefault(episode.id, self._result(episode))
            # Likewise: the reported semantic score is the real cosine
            # similarity, not a fusion artifact.
            result.semantic_score = similarity
            result.final_score += semantic_weight * (1.0 / (RRF_K + rank + 1))

        return sorted(
            fused.values(),
            key=lambda result: (-result.final_score, self._tiebreak(result)),
        )

    def rerank(self, results: list[MemoryResult]) -> list[MemoryResult]:
        """
        The deterministic lightweight rerank pass.

        Confidence and importance act ONLY as tie-breakers on equal
        fusion scores - they can settle a tie between two memories but
        cannot promote a low-confidence match over a stronger one. A
        real reranker (cross-encoder, LLM scorer) replaces this method,
        nothing else.
        """

        return sorted(
            results,
            key=lambda result: (
                -result.final_score,
                -(result.confidence + result.importance) / 2.0,
                -result.memory_id,
            ),
        )

    # ------------------------------------------------------------------

    def _safe_lexical(self, query: str, limit: int) -> list[tuple]:
        """
        The lexical ranking, or an empty ranking on failure.

        The retrieval failure contract says a failure of one half must
        never fail the turn, and lexical is the half that must keep
        working when the other breaks - so if even IT fails, the answer
        is an empty ranking, not an exception.
        """

        try:
            return self.lexical.rank(query, limit)
        except Exception as error:  # noqa: BLE001 - retrieval never raises
            logger.warning(
                "Lexical retrieval failed (%s); hybrid degrades",
                type(error).__name__,
            )
            return []

    def _safe_semantic(self, query: str, limit: int) -> list[tuple]:
        try:
            return self.semantic.rank(query, limit)
        except Exception as error:  # noqa: BLE001 - retrieval never raises
            logger.warning(
                "Semantic retrieval failed (%s); hybrid degrades",
                type(error).__name__,
            )
            self.semantic.last_reason = (
                self.semantic.last_reason or type(error).__name__
            )
            return []

    def _result(self, episode) -> MemoryResult:
        return MemoryResult(
            memory_id=episode.id,
            content=episode.content,
            category=episode.category,
            source=episode.source,
            occurred_at=episode.occurred_at,
            created_at=episode.created_at,
            importance=float(episode.importance or 0.0),
            confidence=float(episode.confidence or 0.0),
        )

    def _tiebreak(self, result: MemoryResult) -> str:
        # Newest first among equal fusion scores, via the ISO string
        # ordering the store itself uses for occurred_at.
        return str(result.occurred_at or "")

    def _now(self):
        try:
            return self.lexical.clock()
        except Exception:  # noqa: BLE001
            return local_now()

