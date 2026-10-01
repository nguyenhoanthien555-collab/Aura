"""
Memory API Endpoints for AURA Companion.

Provides authenticated CRUD and inspection endpoints for UserFacts,
Entity Knowledge Graph, and Episodic Memories.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from core.logger import logger
from memory.graph import EntityGraphStore
from memory.models import EpisodicMemory, UserFact
from memory.profile import ProfileStore, normalise_key
from memory.sanitizer import SensitiveDataSanitizer
from memory.sqlite import SessionLocal, db_lock, init_pipeline_tables, init_graph_tables
from server.auth import verify_token

router = APIRouter(prefix="/api/memory", tags=["memory"])

_profile_store: Optional[ProfileStore] = None
_graph_store: Optional[EntityGraphStore] = None


def get_profile_store() -> ProfileStore:
    global _profile_store
    if _profile_store is None:
        init_pipeline_tables()
        _profile_store = ProfileStore()
    return _profile_store


def get_graph_store() -> EntityGraphStore:
    global _graph_store
    if _graph_store is None:
        init_graph_tables()
        _graph_store = EntityGraphStore()
    return _graph_store


# ---------------------------------------------------------------------------
# Request Models
# ---------------------------------------------------------------------------

class FactUpsertRequest(BaseModel):
    key: str = Field(..., min_length=1, max_length=64)
    value: str = Field(..., min_length=1, max_length=2000)
    category: str = Field(default="profile", max_length=32)


class EntityCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    entity_type: str = Field(default="CONCEPT", max_length=32)
    description: str = Field(default="", max_length=2000)
    properties: dict[str, Any] = Field(default_factory=dict)


class RelationCreateRequest(BaseModel):
    source: str = Field(..., min_length=1, max_length=128)
    relation: str = Field(..., min_length=1, max_length=64)
    target: str = Field(..., min_length=1, max_length=128)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class PurgeRequest(BaseModel):
    target: str = Field(default="all")  # "all", "facts", "graph", "episodes"
    category: Optional[str] = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/overview")
def get_memory_overview(token: str = Depends(verify_token)) -> dict[str, Any]:
    """Summary counts of all memory stores for dashboard widgets."""
    profile = get_profile_store()
    graph = get_graph_store()

    facts = profile.all()
    categories: dict[str, int] = {}
    for f in facts:
        cat = f.category or "profile"
        categories[cat] = categories.get(cat, 0) + 1

    graph_stats = graph.stats()

    total_episodes = 0
    with db_lock:
        session = SessionLocal()
        try:
            total_episodes = session.execute(select(func.count(EpisodicMemory.id))).scalar() or 0
        except Exception:
            pass
        finally:
            session.close()

    return {
        "total_facts": len(facts),
        "total_entities": graph_stats.get("total_entities", 0),
        "total_relations": graph_stats.get("total_relations", 0),
        "total_episodes": total_episodes,
        "categories": categories,
    }


@router.get("/facts")
def list_facts(
    category: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    token: str = Depends(verify_token),
) -> dict[str, Any]:
    """List UserFacts with optional category and search filter."""
    profile = get_profile_store()
    if category:
        facts = profile.by_category(category)
    else:
        facts = profile.all(limit=limit)

    results = []
    clean_q = (q or "").strip().lower()

    for f in facts:
        if clean_q and (clean_q not in f.key.lower() and clean_q not in f.value.lower()):
            continue
        results.append({
            "id": f.id,
            "key": f.key,
            "value": f.value,
            "category": f.category,
            "source": f.source,
            "created_at": f.created_at,
            "updated_at": f.updated_at,
        })

    return {"facts": results, "count": len(results)}


@router.post("/facts")
def upsert_fact(
    req: FactUpsertRequest,
    token: str = Depends(verify_token),
) -> dict[str, Any]:
    """Manually add or update a UserFact from UI."""
    clean_key = normalise_key(req.key)
    if not clean_key:
        raise HTTPException(status_code=422, detail="Invalid key slug")

    is_safe, reason = SensitiveDataSanitizer.validate_for_storage(clean_key, req.value)
    if not is_safe:
        raise HTTPException(
            status_code=422,
            detail=f"Refused: {reason}. Credentials, passwords, and cards cannot be saved.",
        )

    profile = get_profile_store()
    fact = profile.remember(
        key=clean_key,
        value=req.value.strip(),
        category=req.category.strip().lower(),
        source="user",
    )
    if fact is None:
        raise HTTPException(status_code=500, detail="Failed to persist fact")

    return {
        "ok": True,
        "fact": {
            "id": fact.id,
            "key": fact.key,
            "value": fact.value,
            "category": fact.category,
            "updated_at": fact.updated_at,
        },
    }


@router.delete("/facts/{key}")
def delete_fact(
    key: str,
    token: str = Depends(verify_token),
) -> dict[str, Any]:
    """Delete a UserFact by key."""
    profile = get_profile_store()
    deleted = profile.forget(key)
    return {"ok": True, "deleted": deleted, "key": key}


@router.get("/graph")
def get_graph(
    q: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    token: str = Depends(verify_token),
) -> dict[str, Any]:
    """Fetch entities and relations for Knowledge Graph explorer."""
    graph = get_graph_store()
    entities = graph.list_entities(query=q, limit=limit)
    relations = graph.list_relations(limit=limit)
    return {
        "entities": entities,
        "relations": relations,
        "stats": graph.stats(),
    }


@router.post("/entities")
def create_entity(
    req: EntityCreateRequest,
    token: str = Depends(verify_token),
) -> dict[str, Any]:
    """Manually register an entity in the Knowledge Graph."""
    if SensitiveDataSanitizer.is_sensitive(req.name):
        raise HTTPException(status_code=422, detail="Entity name contains sensitive credentials")

    graph = get_graph_store()
    node = graph.add_entity(
        name=req.name,
        entity_type=req.entity_type,
        description=req.description,
        properties=req.properties,
    )
    if node is None:
        raise HTTPException(status_code=500, detail="Failed to create entity node")

    return {"ok": True, "entity": {"id": node.id, "name": node.name, "entity_type": node.entity_type}}


@router.delete("/entities/{name}")
def delete_entity(
    name: str,
    token: str = Depends(verify_token),
) -> dict[str, Any]:
    """Delete an entity and cascade its relations."""
    graph = get_graph_store()
    deleted = graph.delete_entity(name)
    return {"ok": True, "deleted": deleted, "name": name}


@router.post("/relations")
def create_relation(
    req: RelationCreateRequest,
    token: str = Depends(verify_token),
) -> dict[str, Any]:
    """Manually add an edge between two entities."""
    graph = get_graph_store()
    rel = graph.add_relation(
        source_name=req.source,
        relation=req.relation,
        target_name=req.target,
        confidence=req.confidence,
        source="user",
    )
    if rel is None:
        raise HTTPException(status_code=422, detail="Failed to create relation")

    return {"ok": True, "relation": {"id": rel.id, "relation": rel.relation}}


@router.delete("/relations")
def delete_relation(
    source: str = Query(...),
    relation: str = Query(...),
    target: str = Query(...),
    token: str = Depends(verify_token),
) -> dict[str, Any]:
    """Delete a specific relation."""
    graph = get_graph_store()
    deleted = graph.delete_relation(source, relation, target)
    return {"ok": True, "deleted": deleted}


@router.get("/episodes")
def list_episodes(
    limit: int = Query(default=50, ge=1, le=200),
    token: str = Depends(verify_token),
) -> dict[str, Any]:
    """List recent episodic memories."""
    with db_lock:
        session = SessionLocal()
        try:
            stmt = select(EpisodicMemory).order_by(EpisodicMemory.occurred_at.desc()).limit(limit)
            rows = session.execute(stmt).scalars().all()
            return {
                "episodes": [
                    {
                        "id": ep.id,
                        "content": ep.content,
                        "category": ep.category,
                        "source": ep.source,
                        "importance": ep.importance,
                        "confidence": ep.confidence,
                        "occurred_at": ep.occurred_at,
                        "created_at": ep.created_at,
                    }
                    for ep in rows
                ],
                "count": len(rows),
            }
        finally:
            session.close()


@router.delete("/episodes/{episode_id}")
def delete_episode(
    episode_id: int,
    token: str = Depends(verify_token),
) -> dict[str, Any]:
    """Delete an episodic memory."""
    with db_lock:
        session = SessionLocal()
        try:
            stmt = delete(EpisodicMemory).where(EpisodicMemory.id == episode_id)
            res = session.execute(stmt)
            session.commit()
            return {"ok": True, "deleted": res.rowcount > 0}
        finally:
            session.close()


@router.post("/purge")
def purge_memories(
    req: PurgeRequest,
    token: str = Depends(verify_token),
) -> dict[str, Any]:
    """Privacy reset: purge selected or all memory stores."""
    profile = get_profile_store()
    graph = get_graph_store()
    target = (req.target or "all").lower()

    if target in ("all", "facts"):
        if req.category:
            with db_lock:
                session = SessionLocal()
                try:
                    session.execute(delete(UserFact).where(UserFact.category == req.category.strip().lower()))
                    session.commit()
                finally:
                    session.close()
        else:
            profile.clear()

    if target in ("all", "graph"):
        graph.purge()

    if target in ("all", "episodes"):
        with db_lock:
            session = SessionLocal()
            try:
                session.execute(delete(EpisodicMemory))
                session.commit()
            finally:
                session.close()

    logger.info("Purged memories with target=%s, category=%s", target, req.category)
    return {"ok": True, "purged": target, "category": req.category}
