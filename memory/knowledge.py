"""
Knowledge provider.

The single object the brain asks "what should I know for this turn". It
satisfies brain.ports.KnowledgeProvider, so ConversationManager can pull
in long term memory without importing SQLAlchemy, a retriever, or this
module.

Two sources, combined:

    profile   stable facts, always included
    recall    older messages that match the current query

Profile first, because who the user is outranks what they once said.
"""

from core.logger import logger
from memory.graph import EntityGraphStore
from memory.profile import ProfileStore
from memory.retrieval import NullRetriever, Retriever


DEFAULT_MAX_FACTS = 8
DEFAULT_MAX_RECALLED = 3
DEFAULT_MAX_GRAPH = 6


class MemoryKnowledgeProvider:

    def __init__(
        self,
        profile: ProfileStore | None = None,
        retriever: Retriever | None = None,
        graph_store: EntityGraphStore | None = None,
        max_facts: int = DEFAULT_MAX_FACTS,
        max_recalled: int = DEFAULT_MAX_RECALLED,
        max_graph: int = DEFAULT_MAX_GRAPH,
        enabled: bool = True,
    ):

        self.profile = profile
        self.retriever = retriever or NullRetriever()
        self.graph_store = graph_store
        self.max_facts = max_facts
        self.max_recalled = max_recalled
        self.max_graph = max_graph
        self.enabled = enabled

    def get_knowledge(self, query: str) -> list[str]:
        """
        Prompt ready lines for this turn.

        Never raises: a broken retriever costs Aura its memory of the
        conversation, not the conversation itself.
        """

        if not self.enabled:
            return []

        lines: list[str] = []

        lines.extend(self._facts())
        lines.extend(self._graph_relations(query))
        lines.extend(self._recalled(query))

        return lines

    # ------------------------------------------------------------------

    def _facts(self) -> list[str]:

        if self.profile is None or self.max_facts <= 0:
            return []

        try:
            return self.profile.render(limit=self.max_facts)

        except Exception as error:
            logger.debug("Profile lookup failed: %s", error)
            return []

    def _recalled(self, query: str) -> list[str]:

        if self.max_recalled <= 0:
            return []

        try:
            found = self.retriever.search(query, limit=self.max_recalled)

        except Exception as error:
            logger.debug("Recall failed: %s", error)
            return []

        return [f"earlier - {line}" for line in found]

    def _graph_relations(self, query: str) -> list[str]:
        if self.graph_store is None or self.max_graph <= 0 or not query:
            return []

        try:
            import re
            words = [w.strip() for w in re.findall(r"[\wÀ-ỹ]{2,}", query) if w.strip()]
            if not words:
                return []

            triples = self.graph_store.query_subgraph(words, max_hops=1, limit=self.max_graph)
            return [
                f"knowledge - {r['source']} {r['relation'].lower().replace('_', ' ')} {r['target']}"
                for r in triples
            ]
        except Exception as error:
            logger.debug("Graph relation lookup failed: %s", error)
            return []

