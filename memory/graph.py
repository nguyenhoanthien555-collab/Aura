"""
Entity Knowledge Graph Store for AURA Memory.

Manages personalized knowledge graph entities and relations (triples) in SQLite.
Thread-safe, durable, and supports 1-hop subgraph retrieval for prompt injection.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session, aliased

from core.logger import logger
from memory.models import EntityNode, EntityRelation, timestamp_now
from memory.sanitizer import SensitiveDataSanitizer
from memory.sqlite import SessionLocal, db_lock, init_graph_tables


def normalize_entity_name(name: str) -> str:
    """Normalize entity name while preserving natural capitalization."""
    if not name or not isinstance(name, str):
        return ""
    return name.strip()


def normalize_relation_type(rel: str) -> str:
    """Normalize relation type to UPPER_SNAKE_CASE."""
    if not rel or not isinstance(rel, str):
        return "RELATED_TO"
    clean = rel.strip().replace(" ", "_").replace("-", "_").upper()
    return clean or "RELATED_TO"


class EntityGraphStore:
    """
    Durable SQLite-backed store for personalized entity relations.
    """

    def __init__(self, session: Optional[Session] = None):
        init_graph_tables()
        self._session = session
        self._owns_session = session is None

    def _get_session(self) -> Session:
        if self._session is not None:
            return self._session
        return SessionLocal()

    # ------------------------------------------------------------------
    # Entity Mutations
    # ------------------------------------------------------------------

    def add_entity(
        self,
        name: str,
        entity_type: str = "CONCEPT",
        description: str = "",
        properties: Optional[dict[str, Any]] = None,
    ) -> Optional[EntityNode]:
        """Add or update an entity node."""
        clean_name = normalize_entity_name(name)
        if not clean_name:
            return None

        # Sanitize sensitive data
        safe_name = SensitiveDataSanitizer.redact(clean_name)
        safe_desc = SensitiveDataSanitizer.redact(description or "")

        with db_lock:
            session = self._get_session()
            try:
                stmt = select(EntityNode).where(func.lower(EntityNode.name) == clean_name.lower())
                node = session.execute(stmt).scalar_one_or_none()

                props_json = json.dumps(properties or {}, ensure_ascii=False)

                if node is None:
                    node = EntityNode(
                        name=safe_name,
                        entity_type=(entity_type or "CONCEPT").strip().upper(),
                        description=safe_desc,
                        properties_json=props_json,
                        created_at=timestamp_now(),
                        updated_at=timestamp_now(),
                    )
                    session.add(node)
                else:
                    if entity_type:
                        node.entity_type = entity_type.strip().upper()
                    if safe_desc:
                        node.description = safe_desc
                    if properties:
                        node.properties_json = props_json
                    node.updated_at = timestamp_now()

                session.commit()
                session.refresh(node)
                return node
            except Exception as e:
                session.rollback()
                logger.error("EntityGraphStore.add_entity failed: %s", e)
                return None
            finally:
                if self._owns_session:
                    session.close()

    def get_entity(self, name: str) -> Optional[EntityNode]:
        """Fetch entity by case-insensitive name."""
        clean_name = normalize_entity_name(name)
        if not clean_name:
            return None

        with db_lock:
            session = self._get_session()
            try:
                stmt = select(EntityNode).where(func.lower(EntityNode.name) == clean_name.lower())
                return session.execute(stmt).scalar_one_or_none()
            finally:
                if self._owns_session:
                    session.close()

    def delete_entity(self, name: str) -> bool:
        """Delete an entity and cascade all connected relations."""
        clean_name = normalize_entity_name(name)
        if not clean_name:
            return False

        with db_lock:
            session = self._get_session()
            try:
                stmt = select(EntityNode).where(func.lower(EntityNode.name) == clean_name.lower())
                node = session.execute(stmt).scalar_one_or_none()
                if node is None:
                    return False

                # Delete relations involving node
                session.execute(
                    delete(EntityRelation).where(
                        or_(
                            EntityRelation.source_id == node.id,
                            EntityRelation.target_id == node.id,
                        )
                    )
                )
                session.delete(node)
                session.commit()
                return True
            except Exception as e:
                session.rollback()
                logger.error("EntityGraphStore.delete_entity failed: %s", e)
                return False
            finally:
                if self._owns_session:
                    session.close()

    # ------------------------------------------------------------------
    # Relation Mutations
    # ------------------------------------------------------------------

    def add_relation(
        self,
        source_name: str,
        relation: str,
        target_name: str,
        confidence: float = 1.0,
        source: str = "user",
    ) -> Optional[EntityRelation]:
        """Add or update a relation between two entities."""
        src = normalize_entity_name(source_name)
        tgt = normalize_entity_name(target_name)
        rel_type = normalize_relation_type(relation)

        if not src or not tgt or src.lower() == tgt.lower():
            return None

        # Check for sensitive text
        if SensitiveDataSanitizer.is_sensitive(src) or SensitiveDataSanitizer.is_sensitive(tgt):
            logger.warning("EntityGraphStore: rejected relation with sensitive payload")
            return None

        with db_lock:
            session = self._get_session()
            try:
                # Ensure source entity exists
                stmt_src = select(EntityNode).where(func.lower(EntityNode.name) == src.lower())
                src_node = session.execute(stmt_src).scalar_one_or_none()
                if src_node is None:
                    src_node = EntityNode(name=src, entity_type="CONCEPT", created_at=timestamp_now(), updated_at=timestamp_now())
                    session.add(src_node)
                    session.flush()

                # Ensure target entity exists
                stmt_tgt = select(EntityNode).where(func.lower(EntityNode.name) == tgt.lower())
                tgt_node = session.execute(stmt_tgt).scalar_one_or_none()
                if tgt_node is None:
                    tgt_node = EntityNode(name=tgt, entity_type="CONCEPT", created_at=timestamp_now(), updated_at=timestamp_now())
                    session.add(tgt_node)
                    session.flush()

                # Check if relation exists
                stmt_rel = select(EntityRelation).where(
                    EntityRelation.source_id == src_node.id,
                    EntityRelation.relation == rel_type,
                    EntityRelation.target_id == tgt_node.id,
                )
                rel_obj = session.execute(stmt_rel).scalar_one_or_none()

                if rel_obj is None:
                    rel_obj = EntityRelation(
                        source_id=src_node.id,
                        relation=rel_type,
                        target_id=tgt_node.id,
                        confidence=max(0.0, min(1.0, float(confidence))),
                        source=source,
                        created_at=timestamp_now(),
                    )
                    session.add(rel_obj)
                else:
                    rel_obj.confidence = max(0.0, min(1.0, float(confidence)))
                    rel_obj.source = source

                session.commit()
                session.refresh(rel_obj)
                return rel_obj
            except Exception as e:
                session.rollback()
                logger.error("EntityGraphStore.add_relation failed: %s", e)
                return None
            finally:
                if self._owns_session:
                    session.close()

    def delete_relation(self, source_name: str, relation: str, target_name: str) -> bool:
        """Delete a specific relation between two entities."""
        src = normalize_entity_name(source_name)
        tgt = normalize_entity_name(target_name)
        rel_type = normalize_relation_type(relation)

        with db_lock:
            session = self._get_session()
            try:
                stmt_src = select(EntityNode.id).where(func.lower(EntityNode.name) == src.lower())
                src_id = session.execute(stmt_src).scalar_one_or_none()
                stmt_tgt = select(EntityNode.id).where(func.lower(EntityNode.name) == tgt.lower())
                tgt_id = session.execute(stmt_tgt).scalar_one_or_none()

                if not src_id or not tgt_id:
                    return False

                stmt_del = delete(EntityRelation).where(
                    EntityRelation.source_id == src_id,
                    EntityRelation.relation == rel_type,
                    EntityRelation.target_id == tgt_id,
                )
                res = session.execute(stmt_del)
                session.commit()
                return res.rowcount > 0
            except Exception as e:
                session.rollback()
                logger.error("EntityGraphStore.delete_relation failed: %s", e)
                return False
            finally:
                if self._owns_session:
                    session.close()

    # ------------------------------------------------------------------
    # Query & Subgraph Retrieval (1-Hop)
    # ------------------------------------------------------------------

    def query_subgraph(
        self,
        entity_names: list[str],
        max_hops: int = 1,
        limit: int = 15,
    ) -> list[dict[str, Any]]:
        """
        Retrieve 1-hop connected triples for the given entity names.
        Returns a list of dictionaries with keys:
          - source: str
          - relation: str
          - target: str
          - confidence: float
        """
        if not entity_names:
            return []

        clean_names = [normalize_entity_name(n).lower() for n in entity_names if n and n.strip()]
        if not clean_names:
            return []

        with db_lock:
            session = self._get_session()
            try:
                # Find matching seed nodes
                stmt_nodes = select(EntityNode).where(func.lower(EntityNode.name).in_(clean_names))
                seed_nodes = session.execute(stmt_nodes).scalars().all()
                if not seed_nodes:
                    return []

                seed_ids = {node.id for node in seed_nodes}

                src_node = aliased(EntityNode, name="sub_src")
                tgt_node = aliased(EntityNode, name="sub_tgt")

                # Find edges where seed is either source or target, resolving names in one query
                stmt_edges = (
                    select(
                        EntityRelation,
                        src_node.name.label("source_name"),
                        tgt_node.name.label("target_name"),
                    )
                    .join(src_node, EntityRelation.source_id == src_node.id)
                    .join(tgt_node, EntityRelation.target_id == tgt_node.id)
                    .where(
                        or_(
                            EntityRelation.source_id.in_(seed_ids),
                            EntityRelation.target_id.in_(seed_ids),
                        )
                    )
                    .limit(limit)
                )

                edges = session.execute(stmt_edges).all()

                return [
                    {
                        "source": src_name,
                        "relation": rel.relation,
                        "target": tgt_name,
                        "confidence": rel.confidence,
                    }
                    for rel, src_name, tgt_name in edges
                ]
            except Exception as e:
                logger.error("EntityGraphStore.query_subgraph failed: %s", e)
                return []
            finally:
                if self._owns_session:
                    session.close()

    def list_entities(
        self,
        query: Optional[str] = None,
        entity_type: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List entities with optional filters."""
        with db_lock:
            session = self._get_session()
            try:
                stmt = select(EntityNode)
                if query:
                    clean_q = f"%{query.strip().lower()}%"
                    stmt = stmt.where(
                        or_(
                            func.lower(EntityNode.name).like(clean_q),
                            func.lower(EntityNode.description).like(clean_q),
                        )
                    )
                if entity_type:
                    stmt = stmt.where(EntityNode.entity_type == entity_type.strip().upper())

                stmt = stmt.order_by(EntityNode.updated_at.desc()).limit(limit)
                nodes = session.execute(stmt).scalars().all()

                return [
                    {
                        "id": node.id,
                        "name": node.name,
                        "entity_type": node.entity_type,
                        "description": node.description,
                        "properties": json.loads(node.properties_json or "{}"),
                        "created_at": node.created_at,
                        "updated_at": node.updated_at,
                    }
                    for node in nodes
                ]
            finally:
                if self._owns_session:
                    session.close()

    def list_relations(self, limit: int = 100) -> list[dict[str, Any]]:
        """List relations with resolved source and target names via single SQL join."""
        with db_lock:
            session = self._get_session()
            try:
                src_node = aliased(EntityNode, name="rel_src")
                tgt_node = aliased(EntityNode, name="rel_tgt")

                stmt = (
                    select(
                        EntityRelation,
                        src_node.name.label("src_name"),
                        tgt_node.name.label("tgt_name"),
                    )
                    .join(src_node, EntityRelation.source_id == src_node.id)
                    .join(tgt_node, EntityRelation.target_id == tgt_node.id)
                    .order_by(EntityRelation.created_at.desc())
                    .limit(limit)
                )
                rows = session.execute(stmt).all()

                return [
                    {
                        "id": rel.id,
                        "source": src_name,
                        "relation": rel.relation,
                        "target": tgt_name,
                        "confidence": rel.confidence,
                        "source_type": rel.source,
                        "created_at": rel.created_at,
                    }
                    for rel, src_name, tgt_name in rows
                ]
            finally:
                if self._owns_session:
                    session.close()

    def stats(self) -> dict[str, Any]:
        """Summary statistics of the knowledge graph."""
        with db_lock:
            session = self._get_session()
            try:
                total_nodes = session.execute(select(func.count(EntityNode.id))).scalar() or 0
                total_edges = session.execute(select(func.count(EntityRelation.id))).scalar() or 0

                types_query = (
                    select(EntityNode.entity_type, func.count(EntityNode.id))
                    .group_by(EntityNode.entity_type)
                )
                by_type = dict(session.execute(types_query).all())

                return {
                    "total_entities": total_nodes,
                    "total_relations": total_edges,
                    "by_type": by_type,
                }
            finally:
                if self._owns_session:
                    session.close()

    def purge(self) -> int:
        """Purge all entities and relations."""
        with db_lock:
            session = self._get_session()
            try:
                session.execute(delete(EntityRelation))
                res = session.execute(delete(EntityNode))
                session.commit()
                return res.rowcount
            except Exception as e:
                session.rollback()
                logger.error("EntityGraphStore.purge failed: %s", e)
                return 0
            finally:
                if self._owns_session:
                    session.close()
