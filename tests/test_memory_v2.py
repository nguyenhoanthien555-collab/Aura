"""
Memory expansion tests (Sprint 5).

Sprint 4's conversation memory is tested in test_pipeline.py and is not
touched here. This file covers what was added on top of it:

    ProfileStore             stable facts about the user
    KeywordRetriever         older messages relevant to this turn
    MemoryKnowledgeProvider  the single object the brain asks

Every test runs against sqlite:///:memory:, so data/memory.db is never
opened, written or read.

The two Message types stay separate, and that separation is asserted
rather than assumed: brain.message.Message is a pipeline dataclass,
memory.models.Message is a storage row.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from brain.message import Message as BrainMessage
from brain.prompt_builder import PromptBuilder
from brain.prompt_sections import MEMORY

from memory.knowledge import MemoryKnowledgeProvider
from memory.manager import MemoryManager
from memory.models import Base, UserFact
from memory.models import Message as DBMessage
from memory.profile import ProfileStore, normalise_key
from memory.retrieval import KeywordRetriever, NullRetriever, tokenize


@pytest.fixture
def session():
    """Isolated in-memory database - never touches data/memory.db."""

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def profile(session):
    return ProfileStore(session=session)


@pytest.fixture
def transcript(session):
    """A conversation long enough to retrieve from."""

    memory = MemoryManager(session=session)

    memory.save("user", "I finished the sqlite migration last night")
    memory.save("assistant", "Nice work on the migration")
    memory.save("user", "my cat is called Muối")
    memory.save("assistant", "Muối is a good name")

    for index in range(5):
        memory.save("user", f"unrelated chatter number {index}")
        memory.save("assistant", f"noted, chatter {index}")

    return memory


# ----------------------------------------------------------------------
# Key normalisation
# ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("favourite_drink", "favourite_drink"),
        ("Favourite Drink", "favourite_drink"),
        ("  Favourite   Drink!  ", "favourite_drink"),
        ("city", "city"),
    ],
)
def test_keys_normalise_to_the_same_slug(raw, expected):
    assert normalise_key(raw) == expected


def test_an_unusable_key_normalises_to_nothing():
    assert normalise_key("   ") == ""
    assert normalise_key("!!!") == ""


# ----------------------------------------------------------------------
# ProfileStore
# ----------------------------------------------------------------------

def test_remember_then_get(profile):
    profile.remember("city", "Da Nang")

    assert profile.get("city") == "Da Nang"


def test_unknown_key_returns_empty(profile):
    assert profile.get("nothing_was_ever_stored_here") == ""


def test_correcting_a_fact_overwrites_it(profile):
    """
    "Actually I moved to Da Nang" must replace the old city, not leave
    Aura holding two contradictory beliefs.
    """

    profile.remember("city", "Ha Noi")
    profile.remember("city", "Da Nang")

    assert profile.get("city") == "Da Nang"
    assert len(profile) == 1


def test_differently_written_keys_are_the_same_fact(profile):
    profile.remember("Favourite Drink", "cà phê sữa đá")
    profile.remember("favourite_drink", "trà đá")

    assert len(profile) == 1
    assert profile.get("FAVOURITE DRINK") == "trà đá"


def test_an_empty_value_is_not_stored(profile):
    assert profile.remember("city", "   ") is None
    assert len(profile) == 0


def test_an_empty_key_is_not_stored(profile):
    assert profile.remember("   ", "Da Nang") is None
    assert len(profile) == 0


def test_forget_removes_a_fact(profile):
    profile.remember("city", "Da Nang")

    assert profile.forget("city") is True
    assert profile.get("city") == ""


def test_forgetting_what_was_never_known_is_not_an_error(profile):
    assert profile.forget("city") is False


def test_clear_empties_the_profile(profile):
    profile.remember("city", "Da Nang")
    profile.remember("job", "engineer")

    profile.clear()

    assert len(profile) == 0


def test_facts_render_as_readable_lines(profile):
    profile.remember("favourite_drink", "cà phê sữa đá")

    assert profile.render() == ["favourite drink: cà phê sữa đá"]


def test_a_truncated_profile_keeps_the_newest_facts(profile):
    """Recency ordering is what makes a limit safe to apply."""

    profile.remember("first", "oldest")
    profile.remember("second", "middle")
    profile.remember("third", "newest")

    rendered = profile.render(limit=1)

    assert rendered == ["third: newest"]


def test_facts_can_be_grouped_by_category(profile):
    profile.remember("city", "Da Nang", category="profile")
    profile.remember("project", "Aura", category="work")

    assert [fact.key for fact in profile.by_category("work")] == ["project"]


def test_user_fact_is_a_storage_row_not_a_pipeline_type():
    fact = UserFact(key="city", value="Da Nang")

    assert not isinstance(fact, BrainMessage)
    assert fact.render() == "city: Da Nang"


# ----------------------------------------------------------------------
# Retrieval
# ----------------------------------------------------------------------

def test_tokenize_drops_stopwords_and_case():
    assert tokenize("The SQLite Migration") == {"sqlite", "migration"}


def test_tokenize_of_only_stopwords_is_empty():
    assert tokenize("the and of it") == set()


def test_null_retriever_finds_nothing():
    assert NullRetriever().search("anything") == []


def test_keyword_retriever_finds_a_matching_line(session, transcript):
    retriever = KeywordRetriever(session=session, skip_recent=0)

    found = retriever.search("how did the sqlite migration go", limit=3)

    assert any("sqlite migration" in line for line in found)


def test_retrieved_lines_carry_their_role(session, transcript):
    retriever = KeywordRetriever(session=session, skip_recent=0)

    found = retriever.search("sqlite migration", limit=1)

    assert found[0].startswith("user: ")


def test_an_unrelated_query_retrieves_nothing(session, transcript):
    retriever = KeywordRetriever(session=session, skip_recent=0)

    assert retriever.search("quantum chromodynamics", limit=3) == []


def test_an_empty_query_retrieves_nothing(session, transcript):
    retriever = KeywordRetriever(session=session, skip_recent=0)

    assert retriever.search("the and of", limit=3) == []


def test_recent_messages_are_skipped(session):
    """
    They are already in the HISTORY section of the prompt. Recalling them
    again spends tokens repeating what the model can already see.
    """

    memory = MemoryManager(session=session)

    memory.save("user", "zzz-alpha is the topic")
    memory.save("user", "recent one")
    memory.save("user", "recent two")

    skipping = KeywordRetriever(session=session, skip_recent=3)
    not_skipping = KeywordRetriever(session=session, skip_recent=0)

    assert skipping.search("zzz-alpha") == []
    assert not_skipping.search("zzz-alpha") != []


def test_retrieval_respects_its_limit(session, transcript):
    retriever = KeywordRetriever(session=session, skip_recent=0)

    found = retriever.search("chatter number", limit=2)

    assert len(found) == 2


def test_long_lines_are_shortened(session):
    memory = MemoryManager(session=session)
    memory.save("user", "zzz-marker " + ("padding " * 100))

    retriever = KeywordRetriever(session=session, skip_recent=0)

    found = retriever.search("zzz-marker", limit=1)

    assert found[0].endswith("...")
    assert len(found[0]) < 250


# ----------------------------------------------------------------------
# KnowledgeProvider
# ----------------------------------------------------------------------

def test_knowledge_includes_profile_facts(profile):
    profile.remember("city", "Da Nang")

    knowledge = MemoryKnowledgeProvider(profile=profile)

    assert "city: Da Nang" in knowledge.get_knowledge("where do I live")


def test_recalled_lines_are_marked_as_older(session, transcript, profile):
    knowledge = MemoryKnowledgeProvider(
        profile=profile,
        retriever=KeywordRetriever(session=session, skip_recent=0),
    )

    lines = knowledge.get_knowledge("sqlite migration")

    assert any(line.startswith("earlier - ") for line in lines)


def test_profile_facts_come_before_recalled_lines(session, transcript, profile):
    """Who the user is outranks what they once said."""

    profile.remember("city", "Da Nang")

    knowledge = MemoryKnowledgeProvider(
        profile=profile,
        retriever=KeywordRetriever(session=session, skip_recent=0),
    )

    lines = knowledge.get_knowledge("sqlite migration")

    assert lines[0] == "city: Da Nang"


def test_knowledge_respects_its_caps(profile):
    for index in range(10):
        profile.remember(f"fact_{index}", f"value {index}")

    knowledge = MemoryKnowledgeProvider(profile=profile, max_facts=3)

    assert len(knowledge.get_knowledge("anything")) == 3


def test_a_broken_retriever_costs_memory_not_the_conversation(profile):
    class BrokenRetriever:
        def search(self, query, limit=3):
            raise RuntimeError("index corrupted")

    profile.remember("city", "Da Nang")

    knowledge = MemoryKnowledgeProvider(
        profile=profile,
        retriever=BrokenRetriever(),
    )

    assert knowledge.get_knowledge("anything") == ["city: Da Nang"]


def test_a_broken_profile_is_survivable():
    class BrokenProfile:
        def render(self, limit=None):
            raise RuntimeError("database is gone")

    knowledge = MemoryKnowledgeProvider(profile=BrokenProfile())

    assert knowledge.get_knowledge("anything") == []


def test_disabled_knowledge_returns_nothing(profile):
    profile.remember("city", "Da Nang")

    knowledge = MemoryKnowledgeProvider(profile=profile, enabled=False)

    assert knowledge.get_knowledge("anything") == []


def test_knowledge_with_no_sources_is_empty():
    assert MemoryKnowledgeProvider().get_knowledge("anything") == []


# ----------------------------------------------------------------------
# Knowledge reaching the prompt
# ----------------------------------------------------------------------

def test_knowledge_becomes_the_memory_section():
    prompt = PromptBuilder().build(
        history=[],
        user_message=BrainMessage(role="user", content="where do I live"),
        knowledge=["city: Da Nang", "earlier - user: I moved last month"],
    )

    assert MEMORY in prompt
    assert "- city: Da Nang" in prompt
    assert "- earlier - user: I moved last month" in prompt


def test_no_knowledge_means_no_memory_section():
    prompt = PromptBuilder().build(
        history=[],
        user_message=BrainMessage(role="user", content="hi"),
        knowledge=[],
    )

    assert MEMORY not in prompt


def test_blank_knowledge_lines_do_not_create_an_empty_section():
    prompt = PromptBuilder().build(
        history=[],
        user_message=BrainMessage(role="user", content="hi"),
        knowledge=["   ", ""],
    )

    assert MEMORY not in prompt


# ----------------------------------------------------------------------
# The boundary Sprint 4 established, re-checked
# ----------------------------------------------------------------------

def test_the_two_message_types_are_still_distinct():
    assert BrainMessage is not DBMessage


def test_profile_facts_leave_the_package_as_strings(profile):
    """
    The brain reads rendered lines, never rows, so it never learns what
    a UserFact is.
    """

    profile.remember("city", "Da Nang")

    lines = MemoryKnowledgeProvider(profile=profile).get_knowledge("x")

    assert all(isinstance(line, str) for line in lines)
