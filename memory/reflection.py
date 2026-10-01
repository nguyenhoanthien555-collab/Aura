"""
Episodic Reflection Worker for AURA.

Extracts entity relationships (triples) and notable episodic events from
conversations in the background, updating EntityGraphStore and EpisodicMemoryStore.
Supports rule-based heuristic extraction (zero latency/offline) and LLM-assisted reflection.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from core.logger import logger
from memory.graph import EntityGraphStore, normalize_entity_name, normalize_relation_type
from memory.models import EpisodicMemory, timestamp_now
from memory.sanitizer import SensitiveDataSanitizer
from memory.sqlite import SessionLocal, db_lock


_EXTRACTION_RULES = [
    (re.compile(r"(?:tên\s+(?:tôi|anh|em)\s+là|tôi\s+tên\s+là|my\s+name\s+is)\s+([A-Za-z0-9_À-ỹ\s]+)", re.IGNORECASE), "NAMED"),
    (re.compile(r"(?:tôi|anh)\s+thích\s+([A-Za-z0-9_À-ỹ\s]+)", re.IGNORECASE), "LIKES"),
    (re.compile(r"(?:i\s+(?:like|love|enjoy|prefer))\s+([A-Za-z0-9_\s]+)", re.IGNORECASE), "LIKES"),
    (re.compile(r"(?:tôi|anh)\s+đang\s+làm\s+(?:ở|tại|dự\s+án)\s+([A-Za-z0-9_À-ỹ\s]+)", re.IGNORECASE), "WORKS_ON"),
    (re.compile(r"(?:i\s+am\s+working\s+(?:on|at))\s+([A-Za-z0-9_\s]+)", re.IGNORECASE), "WORKS_ON"),
    (re.compile(r"(?:tôi|anh)\s+sống\s+(?:ở|tại)\s+([A-Za-z0-9_À-ỹ\s]+)", re.IGNORECASE), "LIVES_IN"),
    (re.compile(r"(?:i\s+live\s+in)\s+([A-Za-z0-9_\s]+)", re.IGNORECASE), "LIVES_IN"),
    (re.compile(r"(?:tôi|anh)\s+dùng\s+([A-Za-z0-9_À-ỹ\s]+)", re.IGNORECASE), "USES"),
    (re.compile(r"(?:i\s+use)\s+([A-Za-z0-9_\s]+)", re.IGNORECASE), "USES"),
]


class EpisodicReflectionWorker:
    """
    Background worker that digests conversational turns into structured knowledge graph triples.
    """

    def __init__(
        self,
        graph_store: Optional[EntityGraphStore] = None,
        llm_provider: Optional[Any] = None,
    ):
        self.graph_store = graph_store or EntityGraphStore()
        self.llm_provider = llm_provider

    def reflect_turn(
        self,
        user_message: str,
        assistant_reply: str,
        user_name: str = "User",
    ) -> list[dict[str, Any]]:
        """
        Analyze a conversational turn and persist newly discovered entity relations.
        Returns a list of extracted triples.
        """
        if not user_message or not isinstance(user_message, str):
            return []

        # If user message contains credentials or payment cards, do not reflect
        if SensitiveDataSanitizer.is_sensitive(user_message):
            logger.info("EpisodicReflectionWorker: skipped turn due to sensitive content")
            return []

        extracted_triples: list[dict[str, Any]] = []

        # 1. Rule-based extraction (instant, reliable)
        clean_user = user_name.strip() or "User"
        for pattern, rel_type in _EXTRACTION_RULES:
            match = pattern.search(user_message)
            if match:
                raw_target = match.group(1).strip()
                # Stop at sentence terminators or clauses
                target = re.split(r"[,.;!?\n]|( và )|( and )", raw_target)[0].strip()
                if target and len(target) <= 64 and not SensitiveDataSanitizer.is_sensitive(target):
                    rel = self.graph_store.add_relation(
                        source_name=clean_user,
                        relation=rel_type,
                        target_name=target,
                        confidence=0.9,
                        source="reflection",
                    )
                    if rel is not None:
                        extracted_triples.append({
                            "source": clean_user,
                            "relation": rel_type,
                            "target": target,
                            "confidence": 0.9,
                        })

        # 2. LLM-assisted reflection if provider is configured and available
        if self.llm_provider is not None and hasattr(self.llm_provider, "generate"):
            try:
                llm_triples = self._extract_with_llm(user_message, assistant_reply, clean_user)
                for item in llm_triples:
                    src = item.get("source") or clean_user
                    rel = item.get("relation") or "RELATED_TO"
                    tgt = item.get("target") or ""
                    if tgt and not SensitiveDataSanitizer.is_sensitive(tgt):
                        saved = self.graph_store.add_relation(
                            source_name=src,
                            relation=rel,
                            target_name=tgt,
                            confidence=float(item.get("confidence", 0.8)),
                            source="reflection",
                        )
                        if saved is not None:
                            extracted_triples.append({
                                "source": src,
                                "relation": normalize_relation_type(rel),
                                "target": normalize_entity_name(tgt),
                                "confidence": float(item.get("confidence", 0.8)),
                            })
            except Exception as e:
                logger.debug("EpisodicReflectionWorker LLM extraction failed: %s", e)

        return extracted_triples

    def _extract_with_llm(
        self,
        user_message: str,
        assistant_reply: str,
        user_name: str,
    ) -> list[dict[str, Any]]:
        """Call lightweight LLM to extract entity relations in JSON format."""
        prompt = (
            f"Extract entity relationships from the conversation below as JSON array of objects:\n"
            f"[{{\"source\": \"...\", \"relation\": \"UPPER_SNAKE_CASE\", \"target\": \"...\", \"confidence\": 0.8}}]\n"
            f"Only extract durable facts, preferences, jobs, tools, or locations about '{user_name}'.\n"
            f"Conversation:\nUser: {user_message}\nAssistant: {assistant_reply}\n\nJSON Output:"
        )
        response = self.llm_provider.generate(prompt)
        text = getattr(response, "text", str(response)).strip()

        # Parse JSON from response
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        return []
