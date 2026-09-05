"""
Semantic memory benchmark (AURA 2.0 contract, Phase 2 section 14).

A fixed, deterministic fixture set with queries that cover the ten
scenarios the contract lists (exact, paraphrase, synonym, long-distance,
unrelated, conflict, stale, scope collision, recency, confidence).
Every query names the memories it expects, so Recall@K and Precision@K
are measured on ground truth, not guessed.

Run:  .venv\\Scripts\\python.exe scripts\\benchmark_semantic.py

Repeatable by construction: the fixture set is fixed text, the hashing
provider is deterministic, the database is in-memory, and the clock is
pinned. Latency is the only non-deterministic column, and it is printed
per mode so a regression in retrieval speed shows up in the numbers
rather than being asserted away. Read it as an order of magnitude on ten
memories, not as a throughput figure: the corpus is far too small for the
per-query cost to mean anything about a real one.

The numbers printed here are real measurements of THIS fixture set, on
THIS machine, on the day the command ran - they are not claims about
other machines or other memory corpora.
"""

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from memory.embeddings import HashingEmbeddingProvider
from memory.episodic import EpisodicStore
from memory.models import Base
from memory.retrieval import RankedRetriever
from memory.semantic import (
    HybridRetriever,
    SemanticIndexer,
    SemanticRetriever,
)

NOW = datetime(2026, 8, 28, 12, 0, 0)


# ----------------------------------------------------------------------
# Fixture corpus
# ----------------------------------------------------------------------
# Each memory: (content, category, days_old, importance, confidence).
# Days old are relative to NOW; the store records occurred_at, so the
# pinned clock makes "recent vs old" deterministic.

CORPUS = [
    # topic anchors - python
    ("I prefer Python for my scripting work", "project", 30, 0.8, 0.95),
    ("The project migrated to Python", "project", 3, 0.7, 0.9),
    # rust (conflicts with the python project memories)
    ("The project moved to Rust last quarter", "project", 20, 0.7, 0.8),
    # guitar
    ("I am learning to play the guitar", "hobby", 200, 0.6, 0.9),
    # japan
    ("I started learning Japanese this year", "hobby", 90, 0.7, 0.85),
    # unrelated
    ("The garden needs watering every morning", "life", 5, 0.3, 0.7),
    ("My car is due for a service next month", "life", 2, 0.4, 0.8),
    # a plan (temporal validity nuance)
    ("I am planning a trip to Japan in the autumn", "plan", 1, 0.7, 0.9),
    # recent-vs-old same topic (both true, ranked by recency)
    ("The deploy key expires in October", "work", 60, 0.5, 0.8),
    ("The deploy key was rotated yesterday", "work", 1, 0.6, 0.9),
]

# Queries: (text, expected corpus indexes).
QUERIES = [
    # 1 exact lexical match
    ("prefer Python scripting", [0, 1]),
    # 2 paraphrase (semantic synonym path: coding/programming)
    ("which programming language for coding", [0, 1]),
    # 3 synonym (language -> python, music -> guitar)
    ("music instrument hobby", [3]),
    # 4 long-distance semantic reference (japan plan + japanese study)
    ("country I want to travel to and language study", [4, 7]),
    # 5 unrelated query - must return nothing relevant
    ("recipes for cooking pasta", []),
    # 6 conflict: both project-language memories are evidence
    ("what language does the project use", [0, 1, 2]),
    # 7 stale: a query word absent from the corpus
    ("quantum computing algorithms", []),
    # 8 scope collision: hobby query must not pull project memories
    ("learning an instrument", [3]),
    # 9 recency: same topic, newer should rank higher
    ("deploy key", [9, 8]),
    # 10 confidence: python memories outrank the rest for a python query
    ("python project", [0, 1]),
]

K = 3


def build_index(min_similarity=None):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    store = EpisodicStore(session=session)

    # CORPUS positions are NOT memory ids. The store assigns its own
    # primary keys, so ground truth has to be translated through this
    # map - comparing a corpus index against a database id silently
    # measures the wrong thing (it did, until this was fixed).
    ids_by_index = {}

    for index, (content, category, days_old, importance, confidence) in enumerate(
        CORPUS
    ):
        occurred = NOW - timedelta(days=days_old)
        episode = store.remember(
            content=content,
            category=category,
            importance=importance,
            confidence=confidence,
            occurred_at=occurred,
        )
        ids_by_index[index] = episode.id

    provider = HashingEmbeddingProvider()
    indexer = SemanticIndexer(provider, session=session, store=store)
    indexer.reindex()

    lexical = RankedRetriever(store, clock=lambda: NOW, scope=100)
    semantic = SemanticRetriever(
        store, indexer, clock=lambda: NOW, scope=100,
        min_similarity=min_similarity,
    )
    hybrid = HybridRetriever(lexical, semantic)

    return lexical, semantic, hybrid, ids_by_index


def _ids_of(retriever, query, limit):
    """The returned memory ids, whichever retriever shape was passed."""

    if hasattr(retriever, "rank_full"):
        return [result.memory_id for result in retriever.rank_full(query, limit)]

    return [
        episode.id for episode, _score, _now in retriever.rank(query, limit)
    ]


def evaluate(retriever, mode, ids_by_index):
    """
    Recall@K, Precision@K, and false-positive behaviour on the queries
    that have no relevant memory at all.

    Two methodology points, both of which changed the numbers:

    * Recall and precision are averaged over the queries that HAVE a
      relevant memory. Averaging over all ten - including the two
      designed to have no answer - caps recall at 0.8 and reports a
      retriever as worse than it is.
    * Precision divides by how many results actually came back, not by
      K. A retriever that returns one correct line out of one is
      precise; charging it for the two slots it declined to fill
      measures padding, not precision.

    The no-answer queries are scored separately as `noise@K`: the mean
    number of memories returned when the honest answer is none. That is
    where a weak embedding space shows up, and it must not be hidden
    inside an average with the queries it gets right.
    """

    total_recall = 0.0
    total_precision = 0.0
    answerable = 0

    noise = 0
    noise_queries = 0

    # perf_counter, NOT monotonic: on Windows monotonic has a 15.625 ms
    # resolution, so ten sub-millisecond queries measured with it land on
    # 0.0 or 15.0 and the column reads like a difference where there is
    # only a clock tick. This was reporting lexical as 0.0 ms.
    started = time.perf_counter()

    for query, expected in QUERIES:
        ids = _ids_of(retriever, query, K)

        relevant = {ids_by_index[index] for index in expected}

        if not relevant:
            # No correct answer exists. Anything returned is noise.
            noise += len(ids)
            noise_queries += 1
            continue

        answerable += 1
        hits = len(relevant & set(ids))

        total_recall += hits / len(relevant)
        total_precision += (hits / len(ids)) if ids else 0.0

    elapsed = time.perf_counter() - started

    return {
        "mode": mode,
        "recall_at_k": round(total_recall / answerable, 3),
        "precision_at_k": round(total_precision / answerable, 3),
        "noise_at_k": round(noise / noise_queries, 2),
        "total_ms": round(elapsed * 1000, 2),
        "avg_ms": round(elapsed * 1000 / len(QUERIES), 3),
    }


def main():
    lexical, semantic, hybrid, ids_by_index = build_index()

    answerable = sum(1 for _query, expected in QUERIES if expected)

    print("AURA semantic memory benchmark (Phase 2 section 14)")
    print("=" * 68)
    print(
        f"fixtures: {len(CORPUS)} memories, {len(QUERIES)} queries "
        f"({answerable} answerable, {len(QUERIES) - answerable} "
        f"deliberately unanswerable), K={K}"
    )
    print("provider: hashing (deterministic, local, stdlib only)")
    print(
        "floor:    provider default "
        f"({HashingEmbeddingProvider().recommended_min_similarity}), "
        "i.e. the shipped configuration"
    )
    print()

    rows = [
        evaluate(lexical, "lexical", ids_by_index),
        evaluate(semantic, "semantic", ids_by_index),
        evaluate(hybrid, "hybrid", ids_by_index),
    ]

    header = (
        f"{'mode':<10}{'recall@K':>11}{'precision@K':>13}"
        f"{'noise@K':>9}{'total_ms':>10}{'avg_ms':>9}"
    )
    print(header)
    print("-" * len(header))

    for row in rows:
        print(
            f"{row['mode']:<10}{row['recall_at_k']:>11}"
            f"{row['precision_at_k']:>13}{row['noise_at_k']:>9}"
            f"{row['total_ms']:>10}{row['avg_ms']:>9}"
        )

    # The similarity floor is the one knob that decides whether the
    # hashing space returns junk for a query with no answer. Sweeping it
    # is how that trade-off gets measured instead of asserted.
    print()
    print("semantic min_similarity sweep (semantic mode only)")
    print("-" * 68)
    print(f"{'floor':>7}{'recall@K':>11}{'precision@K':>13}{'noise@K':>9}")

    default_floor = HashingEmbeddingProvider().recommended_min_similarity

    for floor in (0.0, 0.05, 0.1, 0.2, 0.22, 0.24, 0.26, 0.3, 0.4, 0.5):
        _lex, sem, _hyb, id_map = build_index(min_similarity=floor)
        row = evaluate(sem, "semantic", id_map)
        marker = "  <- default" if floor == default_floor else ""
        print(
            f"{floor:>7}{row['recall_at_k']:>11}"
            f"{row['precision_at_k']:>13}{row['noise_at_k']:>9}{marker}"
        )

    print()
    print()
    print("Read the sweep, not just the table: the floor trades recall")
    print("for silence on queries that have no answer. This corpus is")
    print("ten memories, so the knee is a small measurement - re-run")
    print("the sweep against a real provider before trusting any one")
    print("number.")


    return 0


if __name__ == "__main__":
    sys.exit(main())