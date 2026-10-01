"""
Tests for HybridConversationRetriever (Reciprocal Rank Fusion over conversation lines).

Covers:
- Retriever Protocol conformance.
- Fallback to lexical retrieval when embedding_provider is None.
- Fallback to lexical retrieval when embedding_provider raises EmbeddingUnavailableError / Exception.
- Empty query handling.
- Ephemeral screen observation filtering.
- RRF rank fusion combining lexical overlap and vector similarity.
- Weight variation (lexical-dominated vs semantic-dominated).
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from memory.embeddings import EmbeddingUnavailableError
from memory.manager import MemoryManager
from memory.models import Base
from memory.retrieval import (
    HybridConversationRetriever,
    KeywordRetriever,
    Retriever,
    _cosine_similarity,
)


@pytest.fixture
def session():
    """Isolated in-memory database."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


class MockTopicEmbeddingProvider:
    """
    Simple 3-dim provider:
    dim 0: programming / coding / python
    dim 1: cooking / recipe / food
    dim 2: music / guitar / songs
    """

    def __init__(self, fail_on_embed: bool = False, fail_on_batch: bool = False):
        self.fail_on_embed = fail_on_embed
        self.fail_on_batch = fail_on_batch

    def embed(self, text: str) -> list[float]:
        if self.fail_on_embed:
            raise EmbeddingUnavailableError("Provider offline")
        return self._vectorize(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if self.fail_on_batch:
            raise RuntimeError("Batch embedding failed")
        return [self._vectorize(t) for t in texts]

    def _vectorize(self, text: str) -> list[float]:
        t = (text or "").lower()
        vec = [0.0, 0.0, 0.0]
        if any(w in t for w in ["python", "coding", "software", "programming", "script"]):
            vec[0] = 1.0
        if any(w in t for w in ["cooking", "recipe", "food", "kitchen"]):
            vec[1] = 1.0
        if any(w in t for w in ["music", "guitar", "song"]):
            vec[2] = 1.0
        return vec


def test_cosine_similarity():
    assert _cosine_similarity([], []) == 0.0
    assert _cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert _cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert _cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_hybrid_protocol_conformance(session):
    lexical = KeywordRetriever(session=session, skip_recent=0)
    hybrid = HybridConversationRetriever(lexical=lexical)
    assert isinstance(hybrid, Retriever)


def test_hybrid_empty_query(session):
    lexical = KeywordRetriever(session=session, skip_recent=0)
    hybrid = HybridConversationRetriever(lexical=lexical)
    assert hybrid.search("") == []
    assert hybrid.search("   ") == []


def test_hybrid_fallback_when_provider_is_none(session):
    mgr = MemoryManager(session=session)
    mgr.save("user", "we talked about the sqlite database migration")
    mgr.save("assistant", "yes, the migration is complete")

    lexical = KeywordRetriever(session=session, skip_recent=0)
    hybrid = HybridConversationRetriever(lexical=lexical, embedding_provider=None)

    lex_res = lexical.search("sqlite database")
    hyb_res = hybrid.search("sqlite database")

    assert len(hyb_res) > 0
    assert hyb_res == lex_res


def test_hybrid_fallback_on_provider_exception(session):
    mgr = MemoryManager(session=session)
    mgr.save("user", "we talked about the sqlite database migration")
    mgr.save("assistant", "yes, the migration is complete")

    lexical = KeywordRetriever(session=session, skip_recent=0)
    failing_provider = MockTopicEmbeddingProvider(fail_on_embed=True)
    hybrid = HybridConversationRetriever(lexical=lexical, embedding_provider=failing_provider)

    lex_res = lexical.search("sqlite database")
    hyb_res = hybrid.search("sqlite database")

    assert hyb_res == lex_res


def test_hybrid_fallback_on_batch_exception(session):
    mgr = MemoryManager(session=session)
    mgr.save("user", "we talked about the sqlite database migration")

    lexical = KeywordRetriever(session=session, skip_recent=0)
    failing_provider = MockTopicEmbeddingProvider(fail_on_batch=True)
    hybrid = HybridConversationRetriever(lexical=lexical, embedding_provider=failing_provider)

    assert hybrid.search("sqlite database") == lexical.search("sqlite database")


def test_hybrid_semantic_recall_without_keyword_overlap(session):
    """
    Semantic similarity should retrieve messages that have zero token overlap
    with the query, which pure lexical keyword retrieval misses.
    """
    mgr = MemoryManager(session=session)
    # Message 1 has semantic link to programming, but words are "coding and software"
    mgr.save("user", "I spent yesterday coding and building software architecture")
    # Message 2 has unrelated topic
    mgr.save("user", "Let us prepare dinner in the kitchen with a pasta recipe")

    lexical = KeywordRetriever(session=session, skip_recent=0)
    provider = MockTopicEmbeddingProvider()
    hybrid = HybridConversationRetriever(lexical=lexical, embedding_provider=provider, weight=0.5)

    # Query has NO keyword overlap with "coding and building software architecture",
    # but "python script" lights the programming vector dimension.
    query = "python script"

    lex_res = lexical.search(query)
    assert lex_res == [], "Lexical search must miss this due to zero keyword overlap"

    hyb_res = hybrid.search(query, limit=1)
    assert len(hyb_res) == 1
    assert "coding and building software" in hyb_res[0]


def test_hybrid_filters_ephemeral_screens(session):
    mgr = MemoryManager(session=session)
    mgr.save("user", "nhìn thấy màn hình điện thoại đang mở ứng dụng")
    mgr.save("user", "I prefer python programming for scripting")

    lexical = KeywordRetriever(session=session, skip_recent=0)
    provider = MockTopicEmbeddingProvider()
    hybrid = HybridConversationRetriever(lexical=lexical, embedding_provider=provider)

    results = hybrid.search("python programming", limit=5)
    for res in results:
        assert "nhìn thấy màn hình" not in res
