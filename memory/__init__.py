"""
Memory.

    MemoryManager           the conversation, unchanged since Sprint 4
    ProfileStore            durable facts about the user
    KeywordRetriever        finds older messages worth recalling
    MemoryKnowledgeProvider what the brain actually asks
    CompanionMemory         companion context: facts, preferences, goals,
                            projects, coding style, highlights

The conversation store and the knowledge provider are separate objects
on purpose. One is the transcript; the other is what was learned from
it. Clearing the first must not erase the second.
"""

from memory.manager import MemoryManager
from memory.models import Base, Message, UserFact
from memory.profile import ProfileStore
from memory.retrieval import (
    KeywordRetriever,
    NullRetriever,
    Retriever,
    tokenize,
)
from memory.knowledge import MemoryKnowledgeProvider
from memory.companion import (
    CodingStyle,
    CompanionMemory,
    Fact,
    Goal,
    Highlight,
    InMemoryCodingStyle,
    InMemoryFacts,
    InMemoryGoals,
    InMemoryHighlights,
    InMemoryPreferences,
    InMemoryProjects,
    Preference,
    Project,
)

__all__ = [
    "MemoryManager",
    "Base",
    "Message",
    "UserFact",
    "ProfileStore",
    "Retriever",
    "KeywordRetriever",
    "NullRetriever",
    "tokenize",
    "MemoryKnowledgeProvider",
    "CompanionMemory",
    "Fact",
    "Goal",
    "Preference",
    "Project",
    "CodingStyle",
    "Highlight",
    "InMemoryFacts",
    "InMemoryGoals",
    "InMemoryPreferences",
    "InMemoryProjects",
    "InMemoryCodingStyle",
    "InMemoryHighlights",
]
