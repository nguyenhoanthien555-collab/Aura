"""
Hybrid semantic memory (AURA 2.0 contract Phase 2).

Every invariant from contract section 2 gets a test that fails the
build if it erodes:

    A  lexical retrieval keeps working (untouched classes, pinned here)
    B  semantic is optional (off by default; the pipeline is unchanged)
    C/D provider failure degrades to lexical; never fails the turn
    E  nothing is fabricated - failures return explicit empties
    G  provenance survives indexing and retrieval
    H  memory ids are the stable episodic row ids
    I  a deleted memory cannot be resurrected by its vector
    J  conflict handling stays with the user model, untouched
    K  embedding failures are observable (status + reason)
    L  lexical-only mode operates indefinitely

Everything runs against sqlite:///:memory: - data/memory.db is never
opened - and every test that depends on time pins its own clock.
"""

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.temporal import TemporalClock
from memory.embeddings import (
    EmbeddingMetadata,
    EmbeddingProvider,
    EmbeddingUnavailableError,
    HashingEmbeddingProvider,
    RemoteEmbeddingProvider,
    build_embedding_provider,
)
from memory.episodic import EpisodicStore
from memory.models import Base, EpisodicMemory, SemanticVector
from memory.pipeline import MemoryPipeline, build_memory_pipeline
from memory.retrieval import RankedRetriever, Retriever
from memory.semantic import (
    DEFAULT_SEMANTIC_WEIGHT,
    INDEXED,
    INDEX_FAILED,
    INDEX_SKIPPED,
    SemanticIndexer,
    SemanticRetriever,
    HybridRetriever,
    cosine,
    encode_vector,
    decode_vector,
)


NOW = datetime(2026, 8, 28, 12, 0, 0)


# ----------------------------------------------------------------------
# Deterministic test providers
# ----------------------------------------------------------------------

TOPIC_DIMS = {"python": 0, "rust": 1, "guitar": 2, "japan": 3}

# Word-level synonyms so a query with zero shared tokens can still light
# the same topic dimension - what lets tests exercise semantic recall
# that lexical misses (e.g. "which programming stack" vs "I prefer
# Python for scripting").
TOPIC_WORDS = {
    "python": 0, "scripting": 0, "language": 0, "coding": 0,
    "programming": 0,
    "rust": 1, "systems": 1,
    "guitar": 2, "music": 2, "instrument": 2,
    "japan": 3, "japanese": 3, "travel": 3,
}


class TopicProvider:
    """
    One dimension per topic keyword. Deterministic, inspectable, and
    honest about being a fixture: a text's vector lights the dims of
    the topics it mentions, and a text about nothing we know embeds to
    zero - which cosine scores as unrelated, exactly like a real
    model would score genuinely unrelated content low.
    """

    def __init__(self, version: str = "1"):
        self._version = version

    def metadata(self):
        return EmbeddingMetadata(
            provider="topic", model="topics-1", dimensions=4,
            version=self._version, locality="local",
        )

    def embed(self, text: str) -> list[float]:
        lowered = " ".join(str(text or "").lower().split())
        vector = [0.0] * 4

        for word in lowered.split():
            dim = TOPIC_WORDS.get(word)

            if dim is not None:
                vector[dim] = 1.0

        return vector

    def embed_batch(self, texts):
        return [self.embed(text) for text in texts]

    def health_check(self) -> bool:
        return True

    @property
    def recommended_min_similarity(self) -> float:
        # One-hot topic dimensions: a match scores ~1.0 and a miss
        # exactly 0.0, so any floor in between behaves identically.
        # 0.05 keeps these fixtures independent of the value the real
        # providers measured for themselves.
        return 0.05


class BrokenProvider(TopicProvider):
    """The shape of a provider that cannot serve."""

    def metadata(self):
        return EmbeddingMetadata(
            provider="broken", model="broken-1", dimensions=4,
            version="1", locality="local",
        )

    def embed(self, text):
        raise EmbeddingUnavailableError("unreachable: connection refused")

    def embed_batch(self, texts):
        raise EmbeddingUnavailableError("unreachable: connection refused")

    def health_check(self):
        return False


class DimMismatchProvider(TopicProvider):
    """metadata() promises 4; embed() delivers 3. The metadata lies."""

    def metadata(self):
        return EmbeddingMetadata(
            provider="mismatch", model="mismatch-1", dimensions=4,
            version="1", locality="local",
        )

    def embed(self, text):
        return [1.0, 0.0, 0.0]


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------

@pytest.fixture
def session():
    """Isolated in-memory database - never touches data/memory.db."""

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def clock():
    return TemporalClock(now=lambda: NOW)


@pytest.fixture
def episodic(session):
    return EpisodicStore(session=session)


@pytest.fixture
def indexer(session, episodic):
    return SemanticIndexer(TopicProvider(), session=session, store=episodic)


@pytest.fixture
def lexical(episodic, clock):
    return RankedRetriever(episodic, clock=clock.now, scope=100)


@pytest.fixture
def semantic(episodic, clock, indexer):
    return SemanticRetriever(episodic, indexer, clock=clock.now, scope=100)


def store_memory(episodic, content, category="event", **kwargs):
    """Store one memory, dated at the pinned NOW by default.

    `occurred_at` defaults to NOW because every test pins its clock to
    NOW; a memory stored against the real wall clock would read as
    future-dated to the pinned clock and be skipped by `_valid_now` -
    a test artifact, not a behaviour under test.
    """

    kwargs.setdefault("occurred_at", NOW)

    return episodic.remember(
        content=content, category=category, **kwargs
    )
# ======================================================================
# Provider layer
# ======================================================================

def test_the_hashing_provider_is_deterministic_normalized_and_honest():
    provider = HashingEmbeddingProvider(dimensions=64)

    first = provider.embed("I finished the sqlite migration")
    second = provider.embed("I finished the sqlite migration")

    assert first == second                       # deterministic
    assert len(first) == 64
    assert provider.metadata().dimensions == 64
    assert provider.metadata().locality == "local"

    norm = sum(value * value for value in first) ** 0.5
    assert abs(norm - 1.0) < 1e-5                # normalized for cosine

    assert provider.embed("") == [0.0] * 64      # nothing in, nothing out
    assert provider.health_check()


def test_an_unreachable_provider_raises_a_typed_error():
    with pytest.raises(EmbeddingUnavailableError):
        BrokenProvider().embed("hello")

    assert not BrokenProvider().health_check()


def test_the_provider_protocol_is_satisfied_by_every_implementation():
    # Structural check: the runtime Protocol exists so a future provider
    # cannot half-implement the seam and be discovered at query time.
    assert isinstance(HashingEmbeddingProvider(), EmbeddingProvider)
    assert isinstance(TopicProvider(), EmbeddingProvider)


def test_vectors_from_incompatible_spaces_are_detected_as_incompatible():
    meta_v1 = EmbeddingMetadata(
        provider="topic", model="topics-1", dimensions=4,
        version="1", locality="local",
    )
    meta_v2 = EmbeddingMetadata(
        provider="topic", model="topics-1", dimensions=4,
        version="2", locality="local",
    )
    meta_other_dims = EmbeddingMetadata(
        provider="topic", model="topics-1", dimensions=8,
        version="1", locality="local",
    )
    meta_other_provider = EmbeddingMetadata(
        provider="sentinel", model="topics-1", dimensions=4,
        version="1", locality="local",
    )
    meta_other_model = EmbeddingMetadata(
        provider="topic", model="topics-2", dimensions=4,
        version="1", locality="local",
    )

    assert meta_v1.compatible_with(meta_v1)
    assert not meta_v1.compatible_with(meta_v2)          # version changed
    assert not meta_v1.compatible_with(meta_other_dims)  # dims changed
    assert not meta_v1.compatible_with(meta_other_provider)
    assert not meta_v1.compatible_with(meta_other_model)

    # Locality is where a vector was computed, not the space it lives
    # in: the same provider/model/dims/version from a local host and a
    # remote host embed into the same space, so they ARE comparable.
    # Mixing spaces is what compatibility guards against, and mixing
    # localities is not mixing spaces.
    meta_same_space_remote = EmbeddingMetadata(
        provider="topic", model="topics-1", dimensions=4,
        version="1", locality="remote",
    )
    assert meta_v1.compatible_with(meta_same_space_remote)


def test_a_remote_provider_refuses_without_explicit_consent():
    # The exfiltration boundary, tested from the outside: no allow
    # flag, no embedding, no matter what the caller asks for. The
    # refusal is a typed unavailable error so retrieval degrades to
    # lexical instead of failing the turn.
    remote = RemoteEmbeddingProvider(
        base_url="https://api.example.com/v1",
        api_key="irrelevant-for-this-test",
        model="text-embedding-3-small",
        allow_remote=False,
    )

    with pytest.raises(EmbeddingUnavailableError, match="allow_remote"):
        remote.embed("my api key is in this memory")

    assert not remote.health_check()


def test_a_remote_provider_sends_the_batch_only_when_consent_is_given(
    monkeypatch,
):
    remote = RemoteEmbeddingProvider(
        base_url="https://api.example.com/v1",
        api_key="test-key",
        model="text-embedding-3-small",
        allow_remote=True,
    )

    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "data": [
                    {"embedding": [1.0, 0.0]},
                    {"embedding": [0.0, 1.0]},
                ]
            }).encode()

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode())
        captured["auth"] = request.headers.get("Authorization")
        return FakeResponse()

    monkeypatch.setattr(
        "memory.embeddings.urllib.request.urlopen", fake_urlopen
    )

    vectors = remote.embed_batch(["python", "rust"])

    assert vectors == [[1.0, 0.0], [0.0, 1.0]]
    assert captured["url"] == "https://api.example.com/v1/embeddings"
    assert captured["body"]["input"] == ["python", "rust"]
    # The key travels, as it must for the API to answer; what this
    # test pins is that MEMORY CONTENT travels only as `input`, and
    # only when the caller was allowed to call embed at all.
    assert captured["auth"] == "Bearer test-key"


def test_a_malformed_remote_response_is_a_typed_failure(monkeypatch):
    remote = RemoteEmbeddingProvider(
        base_url="https://api.example.com/v1",
        api_key="test-key",
        model="m",
        allow_remote=True,
    )

    class BadResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"this is not json"

    monkeypatch.setattr(
        "memory.embeddings.urllib.request.urlopen",
        lambda request, timeout: BadResponse(),
    )

    with pytest.raises(EmbeddingUnavailableError):
        remote.embed("python")


def test_the_factory_returns_none_rather_than_raising_on_bad_config():
    # Startup must not fail because embeddings are unavailable - the
    # deal the factory makes, for every wrong way to configure it.
    assert build_embedding_provider({"semantic": {"enabled": False}}) is None
    assert build_embedding_provider({}) is None
    assert build_embedding_provider(
        {"semantic": {"enabled": True, "provider": "does_not_exist"}}
    ) is None
    assert build_embedding_provider(
        {"semantic": {"enabled": True, "provider": "hashing"}}
    ) is not None

# ======================================================================
# Indexing
# ======================================================================

def test_indexing_stores_a_vector_beside_the_memory(session, episodic, indexer):
    episode = store_memory(episodic, "I prefer Python", category="project")

    result = indexer.index(episode)

    assert result == INDEXED

    state = indexer.status()

    assert state["vectors_total"] == 1
    assert state["vectors_current"] == 1
    assert state["episodic_memories"] == 1
    assert state["indexed_ratio"] == 1.0


def test_a_failing_provider_indexes_nothing_but_the_memory_survives(
    session, episodic,
):
    indexer = SemanticIndexer(BrokenProvider(), session=session)

    episode = store_memory(episodic, "I finished the migration")

    result = indexer.index(episode)

    assert result == INDEX_FAILED

    state = indexer.status()

    # The memory is still there and lexically retrievable - indexing
    # failure can never destroy persistence (contract 2.C/I).
    assert state["episodic_memories"] == 1
    assert state["vectors_total"] == 0

    lexical = RankedRetriever(episodic, scope=100)

    assert lexical.search(
        "migration", limit=3
    ), "the memory must remain lexically retrievable"
    assert indexer.is_stale() is False   # nothing stale: nothing indexed


def test_a_dimension_mismatch_is_a_failure_not_a_corrupting_write(
    session, episodic,
):
    indexer = SemanticIndexer(DimMismatchProvider(), session=session)

    episode = store_memory(episodic, "python project")

    result = indexer.index(episode)

    # Never store a vector the metadata lies about.
    assert result == INDEX_FAILED
    assert indexer.status()["vectors_total"] == 0


def test_a_model_change_marks_existing_vectors_stale(session, episodic):
    indexer_v1 = SemanticIndexer(
        TopicProvider(version="1"), session=session, store=episodic
    )
    episode = store_memory(episodic, "I prefer Python")
    assert indexer_v1.index(episode) == INDEXED

    # The provider changed its model. Old vectors are in a different
    # space; semantic retrieval must report unavailable, not silently
    # mix spaces.
    indexer_v2 = SemanticIndexer(
        TopicProvider(version="2"), session=session, store=episodic
    )

    state = indexer_v2.status()

    assert state["vectors_total"] == 1
    assert state["vectors_current"] == 0
    assert state["vectors_stale"] == 1
    assert indexer_v2.is_stale() is True

    retriever = SemanticRetriever(episodic, indexer_v2, scope=100)

    found = retriever.rank("python", limit=3)

    assert found == [], "stale vectors must not be retrieved across spaces"


def test_reindex_replaces_stale_vectors_in_the_new_space(session, episodic):
    indexer_v1 = SemanticIndexer(
        TopicProvider(version="1"), session=session, store=episodic
    )
    episode = store_memory(episodic, "I prefer Python")
    assert indexer_v1.index(episode) == INDEXED

    indexer_v2 = SemanticIndexer(
        TopicProvider(version="2"), session=session, store=episodic
    )

    assert indexer_v2.is_stale() is True

    result = indexer_v2.reindex()

    assert result.get(INDEXED, 0) == 1

    state = indexer_v2.status()

    # The new-space row is what serves retrieval. The old-space row
    # stays (deliberately: if the operator switches models back, that
    # row is reused without reindexing), and it can never be retrieved
    # in the current space - `is_stale` is what gates availability.
    assert state["vectors_current"] == 1
    assert indexer_v2.is_stale() is False

    retriever = SemanticRetriever(episodic, indexer_v2, scope=100)

    found = retriever.rank("python", limit=3)

    assert len(found) == 1
    assert found[0][0].content == "I prefer Python"


# ======================================================================
# Retrieval: lexical / semantic / hybrid
# ======================================================================

def test_lexical_retrieval_still_works_unchanged(episodic, lexical, clock):
    # Contract 2.A: the existing working behavior is preserved. This is
    # the same RankedRetriever the pipeline used before Phase 2.
    store_memory(episodic, "I prefer Python for my data work")
    store_memory(episodic, "I am learning to play guitar")

    found = lexical.search("python", limit=3)

    assert any("Python" in line for line in found)
    assert len(found) <= 3


def test_semantic_retrieval_finds_by_topic_without_shared_tokens(
    episodic, semantic, indexer, clock,
):
    # "what language do you like" shares no word with "I prefer Python",
    # so lexical misses it and semantic (by topic) finds it - the whole
    # point of adding the second path.
    episode = store_memory(episodic, "I prefer Python for scripting")
    indexer.index(episode)

    # "which programming stack" shares no lexical token with the memory
    # ("prefer python scripting"), so lexical yields nothing at its
    # relevance floor; the synonym-aware fixture maps programming ->
    # python's dimension, so semantic finds it.
    found = semantic.rank("which programming stack", limit=3)

    assert found, "semantic should find the Python memory"
    assert found[0][0].id == episode.id


def test_hybrid_uses_both_rankings_and_fuses_deterministically(
    episodic, lexical, semantic, indexer, clock,
):
    # Query "python programming": the python memory matches LEXICALLY
    # ("python") and semantically (dim 0); the coding memory matches
    # only SEMANTICALLY ("coding" lights dim 0, and shares no lexical
    # token with the query); the garden memory matches neither. Hybrid
    # must fuse both signals and be deterministic run to run.
    python = store_memory(episodic, "I prefer Python for scripting")
    coding = store_memory(episodic, "I love coding")
    garden = store_memory(episodic, "The garden needs watering")

    for episode in (python, coding, garden):
        indexer.index(episode)

    hybrid = HybridRetriever(lexical, semantic)

    first = hybrid.rank_full("python programming", limit=3)
    second = hybrid.rank_full("python programming", limit=3)

    assert hybrid.last_mode == "hybrid"

    # Deterministic: two calls, the same ranked result set.
    assert [r.memory_id for r in first] == [r.memory_id for r in second]

    ids = [r.memory_id for r in first]

    assert python.id in ids       # lexically AND semantically relevant
    assert coding.id in ids       # semantically relevant only
    assert garden.id not in ids   # irrelevant to the query


def test_every_mode_reuses_the_same_stable_memory_ids(
    episodic, lexical, semantic, indexer, clock,
):
    python = store_memory(episodic, "I prefer Python")
    indexer.index(python)

    lexical_ids = {episode.id for episode, _s, _n in lexical.rank("python", 3)}
    semantic_ids = {episode.id for episode, _s, _n in semantic.rank("python", 3)}

    hybrid = HybridRetriever(lexical, semantic)
    hybrid_ids = {r.memory_id for r in hybrid.rank_full("python", 3)}

    assert python.id in lexical_ids
    assert python.id in semantic_ids
    assert python.id in hybrid_ids


# ======================================================================
# Degradation (contract sections 2 and 7)
# ======================================================================

def test_semantic_unavailable_degrades_to_lexical_without_raising(
    episodic, lexical, clock,
):
    store_memory(episodic, "I finished the migration today")

    broken_indexer = SemanticIndexer(BrokenProvider(), session=None)
    broken_retriever = SemanticRetriever(
        episodic, broken_indexer, clock=clock.now, scope=100
    )

    hybrid = HybridRetriever(lexical, broken_retriever)

    lines = hybrid.search("migration", limit=3)

    # Semantic failed; lexical answered; nothing raised; the caller can
    # see which path served the answer.
    assert lines
    assert any("migration" in line for line in lines)
    assert hybrid.last_mode == "lexical_fallback"
    assert broken_retriever.last_reason


def test_when_both_retrievals_fail_the_result_is_explicitly_empty(
    episodic, lexical, semantic, clock,
):
    # Nothing stored at all, so lexical genuinely has nothing; semantic
    # with a broken provider also has nothing. The answer must be an
    # explicit empty mode, never fabricated content.
    store_memory(episodic, "unrelated garden note")

    broken_indexer = SemanticIndexer(BrokenProvider(), session=None)
    broken_retriever = SemanticRetriever(
        episodic, broken_indexer, clock=clock.now, scope=100
    )

    hybrid = HybridRetriever(lexical, broken_retriever)

    lines = hybrid.search("flying saucers", limit=3)

    assert lines == []
    assert hybrid.last_mode == "empty"


def test_semantic_alone_can_serve_when_lexical_has_nothing(
    episodic, lexical, semantic, indexer, clock,
):
    # A valid memory exists at the semantic layer; the query shares no
    # lexical token with it. Lexical is genuinely empty (not failed),
    # semantic answers - mode "semantic".
    episode = store_memory(episodic, "I prefer Python for scripting")
    indexer.index(episode)

    hybrid = HybridRetriever(lexical, semantic)

    # "which programming stack" shares no token with the memory, so
    # lexical (which also has a MIN_RELEVANCE floor) yields nothing.
    lines = hybrid.search("which programming stack", limit=3)

    assert any("Python" in line for line in lines)
    assert hybrid.last_mode == "semantic"


def test_retrieval_never_raises_out_of_the_turn(episodic, lexical, clock):
    # The harshest degradation of all: even the lexical half throws.
    # The hybrid must return an explicit empty, not propagate.
    class BrokenLexical(lexical.__class__):
        def rank(self, query, limit):
            raise RuntimeError("database exploded")

    broken_semantic = SemanticRetriever(
        episodic,
        SemanticIndexer(BrokenProvider(), session=None),
        clock=clock.now, scope=100,
    )

    hybrid = HybridRetriever(BrokenLexical(episodic, scope=100), broken_semantic)

    lines = hybrid.search("anything", limit=3)

    assert lines == []
    assert hybrid.last_mode == "empty"


def test_reindexing_the_same_memory_in_the_same_space_replaces_not_duplicates(
    session, episodic, indexer,
):
    episode = store_memory(episodic, "I prefer Python")
    assert indexer.index(episode) == INDEXED
    assert indexer.index(episode) == INDEXED

    state = indexer.status()

    # Re-indexing the same memory in the same space replaces the row
    # rather than duplicating it - the vector table holds one row per
    # memory per space.
    assert state["vectors_total"] == 1


# ======================================================================
# Correctness: provenance, ids, deletion, scope, conflict, privacy
# ======================================================================

def test_retrieved_results_carry_the_memorys_own_provenance(
    episodic, lexical, semantic, indexer, clock,
):
    episode = store_memory(
        episodic,
        "I prefer Python for data work",
        category="project",
        importance=0.9,
        confidence=0.95,
        source="user",
    )
    indexer.index(episode)

    hybrid = HybridRetriever(lexical, semantic)

    results = hybrid.rank_full("python", limit=3)

    assert results

    result = results[0]

    # Provenance survives indexing and retrieval untouched (contract 2.G).
    assert result.memory_id == episode.id
    assert result.content == "I prefer Python for data work"
    assert result.category == "project"
    assert result.source == "user"
    assert result.importance == 0.9
    assert result.confidence == 0.95
    assert result.occurred_at == episode.occurred_at
    assert result.created_at == episode.created_at

    # Retrieval-mode facts are attached, not confused with memory facts.
    assert result.lexical_score is not None
    assert result.semantic_score is not None
    assert result.final_score > 0.0


def test_a_deleted_memory_cannot_be_resurrected_by_its_vector(
    episodic, lexical, semantic, indexer, clock,
):
    episode = store_memory(episodic, "I prefer Python")
    indexer.index(episode)

    # Deleted from the store. The vector row is deliberately left
    # behind (no cascade), which makes this test honest about the
    # invariant: retrieval JOINs to the live episodic row, so the
    # orphaned vector is structurally unreturnable.
    episodic.forget(episode.id)

    hybrid = HybridRetriever(lexical, semantic)

    results = hybrid.rank_full("python", limit=3)

    assert results == []
    assert episode.id not in [r.memory_id for r in results]


def test_scope_isolation_is_structural_not_a_prompt_wish(
    episodic, indexer, clock,
):
    # Two memories whose VECTORS are identical but whose content differs
    # (so the store's same-day dedup does not fold them into one row).
    # A retriever scoped to "project" must return the project memory and
    # never the personal one, no matter how similar.
    project = store_memory(
        episodic, "I love python", category="project"
    )
    personal = store_memory(
        episodic, "I love python programming", category="personal"
    )

    for episode in (project, personal):
        indexer.index(episode)

    scoped = SemanticRetriever(
        episodic, indexer, clock=clock.now, scope=100,
        categories=("project",),
    )

    found = scoped.rank("python", limit=5)

    ids = [episode.id for episode, _score, _now in found]

    assert project.id in ids
    assert personal.id not in ids


def test_conflicting_memories_both_retrieve_as_evidence_and_resolution_stays_later(
    session, episodic, lexical, semantic, indexer, clock,
):
    # Two memories that contradict each other. Semantic retrieval must
    # NOT resolve them on similarity alone - both are stored evidence
    # and both are returned, leaving conflict RESOLUTION to the
    # existing machinery (the user model's correction), which this
    # layer never bypasses.
    python = store_memory(
        episodic, "The project uses Python", category="project"
    )
    rust = store_memory(
        episodic, "The project migrated to Rust", category="project"
    )

    for episode in (python, rust):
        indexer.index(episode)

    hybrid = HybridRetriever(lexical, semantic)

    results = hybrid.rank_full("project language", limit=3)

    ids = [r.memory_id for r in results]

    assert python.id in ids
    assert rust.id in ids

    # The contradiction is then handled by the user model, on the same
    # session, exactly as before Phase 2: a correction lands in place.
    pipeline = MemoryPipeline(session=session, clock=clock)
    pipeline.remember_user_stated("project_language", "Python")
    pipeline.remember_user_correction("project_language", "Rust")

    assert pipeline.user_model.value_of("project_language") == "Rust"


def test_diagnostics_never_carry_memory_content_or_vectors(
    episodic, indexer, clock, monkeypatch, tmp_path,
):
    import core.trace as trace_mod

    # Redirect the diagnostics sink so we can assert on what it wrote.
    path = tmp_path / "diagnostics.jsonl"
    monkeypatch.setattr(trace_mod, "TRACE_FILE", path)
    trace_mod._logger.handlers.clear()

    episode = store_memory(
        episodic,
        "SECRET-PHRASE-KV7 the api key lives in /home/me/.netrc",
    )
    indexer.index(episode)

    semantic = SemanticRetriever(
        episodic, indexer, clock=clock.now, scope=100
    )
    semantic.rank("SECRET-PHRASE-KV7", limit=3)

    trace_mod._logger.handlers.clear()

    text = path.read_text(encoding="utf-8")

    # No memory content, no credentials in the diagnostics stream - only
    # counts, ids and reasons.
    assert "SECRET-PHRASE-KV7" not in text
    assert ".netrc" not in text


# ======================================================================
# Pipeline integration + bounded output
# ======================================================================

def test_the_pipeline_swaps_in_the_hybrid_only_when_semantic_is_enabled(
    session, clock,
):
    lexical_only = build_memory_pipeline(
        {"memory": {"semantic": {"enabled": False}}},
        session=session, clock=clock,
    )

    assert isinstance(lexical_only.retriever, RankedRetriever)
    assert lexical_only.semantic_indexer is None

    semantic_on = build_memory_pipeline(
        {
            "memory": {
                "recall": True,
                "retrieval_scope": 200,
                "semantic": {"enabled": True, "provider": "hashing"},
            }
        },
        session=session, clock=clock,
    )

    assert isinstance(semantic_on.retriever, HybridRetriever)
    assert semantic_on.semantic_indexer is not None


def test_the_default_pipeline_is_lexical_only_and_unchanged(
    session, clock,
):
    # The shipped default (recall:false) and an ordinary recall-only
    # config both keep the plain RankedRetriever - the system operates
    # indefinitely in lexical-only mode, exactly as before this phase.
    default = build_memory_pipeline({}, session=session, clock=clock)
    assert isinstance(default.retriever, RankedRetriever)
    assert default.semantic_indexer is None


def test_the_pipeline_indexes_what_it_stores_after_persistence(
    session, clock,
):
    pipeline = build_memory_pipeline(
        {
            "memory": {
                "recall": True,
                "semantic": {"enabled": True, "provider": "hashing"},
            }
        },
        session=session, clock=clock,
    )

    outcome = pipeline.observe("user", "I prefer Python for scripting")

    assert outcome.accepted
    assert pipeline.semantic_indexer.status()["vectors_total"] == 1


def test_retrieval_output_is_bounded_by_limit(episodic, lexical, semantic):
    for index in range(5):
        store_memory(episodic, f"I prefer Python memory {index}")
        store_memory(episodic, f"The garden needs watering {index}")

    hybrid = HybridRetriever(lexical, semantic)

    for limit in (1, 2, 5):
        results = hybrid.rank_full("python", limit=limit)
        assert len(results) <= limit, "bounded output, always"


# ======================================================================
# Fusion weight (memory.semantic.weight)
# ======================================================================

def _weighted(episodic, lexical, semantic, weight, query="python"):
    """Ranked ids from a hybrid built at one specific weight."""

    return [
        result.memory_id
        for result in HybridRetriever(
            lexical, semantic, weight=weight
        ).rank_full(query, limit=5)
    ]


def test_the_default_weight_orders_exactly_like_unweighted_fusion(
    episodic, lexical, semantic, indexer
):
    """
    An equal split scales both halves by the same constant, so it
    cannot reorder anything. This is why 0.5 is the default: the knob
    arrives without moving any existing behaviour.
    """

    for content in (
        "I prefer Python for scripting",
        "The project moved to Rust",
        "I am learning the guitar",
    ):
        indexer.index(store_memory(episodic, content))

    assert DEFAULT_SEMANTIC_WEIGHT == 0.5

    default_ids = _weighted(episodic, lexical, semantic, DEFAULT_SEMANTIC_WEIGHT)

    # The unweighted formula, recomputed here rather than trusted: every
    # candidate earns its plain reciprocal rank from each list.
    fused = {}
    for rank, (episode, _score, _now) in enumerate(lexical.rank("python", 10)):
        fused[episode.id] = fused.get(episode.id, 0.0) + 1.0 / (60.0 + rank + 1)
    for rank, (episode, _sim, _now) in enumerate(semantic.rank("python", 10)):
        fused[episode.id] = fused.get(episode.id, 0.0) + 1.0 / (60.0 + rank + 1)

    unweighted_ids = [
        memory_id
        for memory_id, _score in sorted(
            fused.items(), key=lambda item: -item[1]
        )
    ]

    assert default_ids == unweighted_ids[: len(default_ids)]


def test_weight_shifts_which_half_decides_the_order(
    episodic, lexical, semantic, indexer
):
    """
    A memory only the semantic half can find must rise as the weight
    rises. This is the knob doing its job - not a gate, a tilt.
    """

    # Lexical finds this one: it shares the query token.
    indexer.index(store_memory(episodic, "python is my scripting language"))

    # Only the semantic half finds this one: TopicProvider maps
    # "coding"/"programming" onto the same topic dimension as "python",
    # while sharing no query token at all.
    semantic_only = store_memory(episodic, "my coding and programming work")
    indexer.index(semantic_only)

    at_zero = _weighted(episodic, lexical, semantic, 0.0)
    at_one = _weighted(episodic, lexical, semantic, 1.0)

    assert semantic_only.id in at_zero, (
        "weight 0.0 tilts toward lexical; it must not DROP semantic "
        "candidates - the contract calls for degradation, not gating"
    )

    assert at_one.index(semantic_only.id) <= at_zero.index(semantic_only.id), (
        "raising the semantic weight must not push a semantic-only "
        "memory further down"
    )


def test_an_out_of_range_weight_is_clamped_rather_than_fatal(
    episodic, lexical, semantic, indexer
):
    """
    A bad config value must not be able to break recall. Clamping is
    the whole posture of this subsystem: degrade, never raise.
    """

    indexer.index(store_memory(episodic, "I prefer Python for scripting"))

    assert HybridRetriever(lexical, semantic, weight=42.0).weight == 1.0
    assert HybridRetriever(lexical, semantic, weight=-5.0).weight == 0.0

    for bad in (42.0, -5.0):
        results = HybridRetriever(
            lexical, semantic, weight=bad
        ).rank_full("python", limit=3)
        assert results, "a clamped weight still retrieves"


def test_reported_scores_stay_the_retrievers_own_not_fusion_artifacts(
    episodic, lexical, semantic, indexer
):
    """
    Provenance rule: `semantic_score` must be the real cosine and
    `lexical_score` the real reciprocal rank, whatever the weight does
    to `final_score`. A caller inspecting why a memory surfaced must
    see what each retriever actually said.
    """

    # Lexical and semantic both rank this one first.
    indexer.index(store_memory(episodic, "I prefer Python for scripting"))

    # Semantic-only: "coding" lights python's topic dimension but
    # shares no token with the query, so it has a semantic rank and no
    # lexical one. Its fused score is therefore weight-dependent.
    semantic_only = store_memory(episodic, "my coding work")
    indexer.index(semantic_only)

    def at(weight):
        return {
            result.memory_id: result
            for result in HybridRetriever(
                lexical, semantic, weight=weight
            ).rank_full("python", limit=5)
        }

    baseline = at(0.5)
    tilted = at(0.9)

    # The provenance rule: each half's reported score is what that half
    # actually said, identical at every weight.
    for memory_id, result in baseline.items():
        assert result.semantic_score == tilted[memory_id].semantic_score
        assert result.lexical_score == tilted[memory_id].lexical_score

    # A memory only one half found DOES move, which is what makes the
    # weight a real knob rather than a stored number.
    assert (
        baseline[semantic_only.id].final_score
        != tilted[semantic_only.id].final_score
    )


def test_the_pipeline_passes_the_configured_weight_through(session, clock):
    """Config reaches the retriever - otherwise the knob is decoration."""

    pipeline = build_memory_pipeline(
        {
            "memory": {
                "recall": True,
                "semantic": {
                    "enabled": True, "provider": "hashing", "weight": 0.8,
                },
            }
        },
        session=session, clock=clock,
    )

    assert pipeline.retriever.weight == 0.8


def test_an_omitted_weight_falls_back_to_the_documented_default(
    session, clock
):
    pipeline = build_memory_pipeline(
        {
            "memory": {
                "recall": True,
                "semantic": {"enabled": True, "provider": "hashing"},
            }
        },
        session=session, clock=clock,
    )

    assert pipeline.retriever.weight == DEFAULT_SEMANTIC_WEIGHT


# ======================================================================
# The similarity floor (memory.semantic.min_similarity)
# ======================================================================

def test_the_floor_comes_from_the_provider_when_config_says_nothing(
    episodic, indexer
):
    """
    The useful cosine cutoff is a property of the embedding space, so
    the provider is what declares it. Hashed n-grams collide and need a
    real floor; the one-hot test space does not.
    """

    retriever = SemanticRetriever(episodic, indexer, clock=lambda: NOW)

    assert retriever.min_similarity == indexer.provider.recommended_min_similarity

    hashing = SemanticIndexer(
        HashingEmbeddingProvider(), session=indexer.session, store=episodic
    )

    assert SemanticRetriever(
        episodic, hashing, clock=lambda: NOW
    ).min_similarity == 0.24, (
        "the hashing provider's floor is the measured 0.24, not a "
        "shared constant - see scripts/benchmark_semantic.py"
    )


def test_an_explicit_floor_overrides_the_providers_recommendation(
    episodic, indexer
):
    retriever = SemanticRetriever(
        episodic, indexer, clock=lambda: NOW, min_similarity=0.9
    )

    assert retriever.min_similarity == 0.9


def test_a_provider_declaring_no_floor_still_works(episodic, session):
    """
    `recommended_min_similarity` is read defensively, so a duck-typed
    provider from outside this module cannot break retrieval by
    omitting it.
    """

    class Minimal:
        def metadata(self):
            return EmbeddingMetadata(
                provider="minimal", model="m", dimensions=4,
                version="1", locality="LOCAL",
            )

        def embed(self, text):
            return [1.0, 0.0, 0.0, 0.0]

        def embed_batch(self, texts):
            return [self.embed(text) for text in texts]

        def health_check(self):
            return True

    indexer = SemanticIndexer(Minimal(), session=session, store=episodic)

    retriever = SemanticRetriever(episodic, indexer, clock=lambda: NOW)

    assert retriever.min_similarity == 0.05

    indexer.index(store_memory(episodic, "anything at all"))

    assert retriever.rank("anything", limit=3), "retrieval still serves"


def test_the_floor_excludes_weak_matches_rather_than_ranking_them_low(
    episodic, session
):
    """
    The contract's rule that low-confidence similarity must not sway
    the answer is enforced by exclusion, not by hoping a low score
    sinks. Below the floor a memory is not a candidate at all.
    """

    class GradedProvider:
        """Similarity is read straight off a marker in the text."""

        def metadata(self):
            return EmbeddingMetadata(
                provider="graded", model="g", dimensions=2,
                version="1", locality="LOCAL",
            )

        def embed(self, text):
            if "QUERY" in text:
                return [1.0, 0.0]
            # "strong" sits near the query vector, "weak" far from it.
            return [0.9, 0.436] if "strong" in text else [0.1, 0.995]

        def embed_batch(self, texts):
            return [self.embed(text) for text in texts]

        def health_check(self):
            return True

        @property
        def recommended_min_similarity(self):
            return 0.5

    indexer = SemanticIndexer(GradedProvider(), session=session, store=episodic)

    strong = store_memory(episodic, "a strong match")
    weak = store_memory(episodic, "a weak match")
    indexer.index(strong)
    indexer.index(weak)

    retriever = SemanticRetriever(episodic, indexer, clock=lambda: NOW)

    found = [episode.id for episode, _sim, _now in retriever.rank("QUERY", 10)]

    assert strong.id in found
    assert weak.id not in found, (
        "a sub-floor match must be absent, not merely last"
    )


def test_the_pipeline_passes_a_configured_floor_through(session, clock):
    pipeline = build_memory_pipeline(
        {
            "memory": {
                "recall": True,
                "semantic": {
                    "enabled": True, "provider": "hashing",
                    "min_similarity": 0.42,
                },
            }
        },
        session=session, clock=clock,
    )

    assert pipeline.retriever.semantic.min_similarity == 0.42


def test_an_unset_floor_leaves_the_provider_in_charge(session, clock):
    pipeline = build_memory_pipeline(
        {
            "memory": {
                "recall": True,
                "semantic": {"enabled": True, "provider": "hashing"},
            }
        },
        session=session, clock=clock,
    )

    assert pipeline.retriever.semantic.min_similarity == 0.24

def test_semantic_on_with_recall_off_stays_off_and_says_which_switch(
    session, clock, caplog,
):
    # Two switches, and `memory.recall` is the one that wins: it gates
    # every episodic search, so semantic vectors would never be
    # consulted. Turning semantic ON here has to be a no-op - but a
    # SILENT no-op is the bug, because the owner has no way to tell a
    # gated feature from a broken one.
    with caplog.at_level("WARNING", logger="Aura"):
        pipeline = build_memory_pipeline(
            {
                "memory": {
                    "recall": False,
                    "semantic": {"enabled": True, "provider": "hashing"},
                }
            },
            session=session, clock=clock,
        )

    assert isinstance(pipeline.retriever, RankedRetriever)
    assert not isinstance(pipeline.retriever, HybridRetriever)

    # No indexer means no embedding calls at all. For a REMOTE provider
    # that is the difference between "no results" and "memory content
    # left the machine anyway".
    assert pipeline.semantic_indexer is None

    # The warning has to name the switch actually holding it closed,
    # not just report that semantic is off.
    assert "memory.recall" in caplog.text
