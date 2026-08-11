"""
Memory 2.0 tests.

Three kinds of memory, one pipeline, and a ranked retriever over the
episodic half. Every test runs against sqlite:///:memory: - data/memory.db
is never opened - and every test that depends on time pins its own clock.

The properties worth failing a build over, in rough order of how much
damage the absence of each would do:

    machine turns and assistant text never enter memory at all
    temporary context never becomes permanent on its own
    an inference is never promoted to a fact except by the user
    a correction changes the stored entry, not just the reply
    recall is bounded, ranked, and never the whole database
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.temporal import TemporalClock
from memory.episodic import EpisodicStore
from memory.models import Base, EpisodicMemory, UserModelEntry
from memory.pipeline import MemoryPipeline, build_memory_pipeline
from memory.retrieval import RankedRetriever, Retriever
from memory.selection import MemorySelector, occurred_at_for
from memory.temporary import TemporaryContext
from memory.user_model import (
    COMMUNICATION,
    IDENTITY,
    PERSONALITY,
    Status,
    UserModel,
)
from memory.user_profile_seed import seed_user_model


NOW = datetime(2026, 8, 11, 14, 0, 0)


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
def model(session, clock):
    return UserModel(session=session, clock=clock.now)


@pytest.fixture
def pipeline(session, clock):
    return MemoryPipeline(session=session, clock=clock)


# ======================================================================
# Selection - what is even a candidate
# ======================================================================

@pytest.fixture
def selector():
    return MemorySelector()


@pytest.mark.parametrize(
    "text",
    [
        "I prefer tea over coffee now",
        "my name is Leo and I live in Da Nang",
        "I just finished the sqlite migration",
        "I'm working on a Minecraft Forge mod called Duality",
        "I'm learning Japanese this year",
        "remember that my deploy key expires in October",
    ],
)
def test_meaningful_statements_are_stored(selector, text):

    assert selector.evaluate("user", text).accepted, text


@pytest.mark.parametrize(
    "text",
    [
        "ok",
        "thanks bro",
        "haha",
        "yeah",
        "what time is it",
        "how does the retriever work",
        "open notepad",
        "run the tests again",
        "fix the failing test",
        "the weather is nice today",
        "python is a programming language",
    ],
)
def test_trivial_and_impersonal_text_is_not_stored(selector, text):

    assert not selector.evaluate("user", text).accepted, text


def test_assistant_replies_are_never_candidates(selector):
    """Aura's own words are not the user's life."""

    outcome = selector.evaluate("assistant", "I prefer tea over coffee now")

    assert not outcome.accepted
    assert "not a user turn" in outcome.reason


@pytest.mark.parametrize(
    "text",
    [
        '{"tool": "current_time", "arguments": {}}',
        "Traceback (most recent call last):\n  File \"x.py\", line 1",
        "def handler(request):\n    return None",
        "<?xml version='1.0'?>",
    ],
)
def test_machine_shaped_text_is_not_stored(selector, text):

    assert not selector.evaluate("user", text).accepted, text


def test_a_paste_is_too_long_to_be_a_memory(selector):

    assert not selector.evaluate("user", "I " + "x" * 800).accepted


def test_explicit_instruction_outranks_the_heuristic(selector):
    """An impersonal sentence still counts if the user asked for it."""

    outcome = selector.evaluate(
        "user", "remember that the staging database resets on Sundays"
    )

    assert outcome.accepted
    assert outcome.importance >= 0.9


def test_selection_is_deterministic(selector):
    """The same sentence always gets the same answer."""

    text = "I just finished the sqlite migration"

    first = selector.evaluate("user", text)
    second = selector.evaluate("user", text)

    assert (first.accepted, first.category, first.importance) == (
        second.accepted, second.category, second.importance
    )


def test_rejection_always_explains_itself(selector):

    assert selector.evaluate("user", "ok").reason
    assert selector.evaluate("assistant", "anything at all").reason


def test_yesterday_is_dated_to_yesterday():
    """"I finished it last night" happened last night, not now."""

    occurred = occurred_at_for("I finished the migration last night", NOW)

    assert occurred.date() == (NOW - timedelta(days=1)).date()


def test_undated_statements_are_dated_to_when_they_were_said():

    assert occurred_at_for("I prefer tea now", NOW) == NOW


# ======================================================================
# Episodic store
# ======================================================================

def test_episode_keeps_its_own_occurrence_time(episodic):

    yesterday = NOW - timedelta(days=1)

    episodic.remember("finished the migration", occurred_at=yesterday)

    stored = episodic.recent()[0]

    assert stored.occurred_at.startswith(yesterday.strftime("%Y-%m-%d"))


def test_learned_at_and_happened_at_are_separate(episodic):
    """
    Told today about last night. Only occurred_at may describe it.
    """

    episodic.remember("shipped it", occurred_at=NOW - timedelta(days=1))

    stored = episodic.recent()[0]

    assert stored.occurred_at[:10] != stored.created_at[:10]


def test_repeating_something_the_same_day_does_not_duplicate(episodic):

    episodic.remember("I finished the migration", occurred_at=NOW, importance=0.5)
    episodic.remember("I finished the migration", occurred_at=NOW, importance=0.8)

    assert len(episodic) == 1
    assert episodic.recent()[0].importance == pytest.approx(0.8)


def test_the_same_thing_on_a_different_day_is_a_new_episode(episodic):

    episodic.remember("went for a run", occurred_at=NOW)
    episodic.remember("went for a run", occurred_at=NOW - timedelta(days=1))

    assert len(episodic) == 2


def test_empty_content_is_not_stored(episodic):

    assert episodic.remember("") is None
    assert episodic.remember("   ") is None
    assert len(episodic) == 0


def test_scores_are_clamped_to_a_sane_range(episodic):

    episodic.remember("a thing", importance=99.0, confidence=-4.0)

    stored = episodic.recent()[0]

    assert stored.importance == 1.0
    assert stored.confidence == 0.0


def test_candidates_are_bounded(episodic):
    """Recall cost must not grow with the size of the database."""

    for index in range(50):
        episodic.remember(f"thing number {index}", occurred_at=NOW)

    assert len(episodic.candidates(scope=10)) == 10


def test_recent_orders_by_occurrence_not_insertion(episodic):

    episodic.remember("older event", occurred_at=NOW - timedelta(days=5))
    episodic.remember("newer event", occurred_at=NOW - timedelta(days=1))
    episodic.remember("oldest event", occurred_at=NOW - timedelta(days=9))

    assert [e.content for e in episodic.recent()][0] == "newer event"


def test_forget_and_clear(episodic):

    episode = episodic.remember("something", occurred_at=NOW)

    assert episodic.forget(episode.id) is True
    assert episodic.forget(episode.id) is False

    episodic.remember("another", occurred_at=NOW)
    episodic.clear()

    assert len(episodic) == 0


# ======================================================================
# Temporary context - the kind that must never become permanent
# ======================================================================

def test_temporary_notes_expire_on_their_own():

    ticks = {"now": NOW}

    context = TemporaryContext(ttl_seconds=3600, clock=lambda: ticks["now"])

    context.note("at a cafe right now")

    assert context.render() == ["at a cafe right now"]

    ticks["now"] = NOW + timedelta(hours=2)

    assert context.render() == []


def test_temporary_context_never_reaches_the_database(session, pipeline):
    """
    The property the whole design exists for: a passing remark must not
    appear in any table, ever.
    """

    outcome = pipeline.observe("user", "I'm at a cafe right now with my laptop")

    assert outcome.kind == "temporary"
    assert session.query(EpisodicMemory).count() == 0
    assert session.query(UserModelEntry).count() == 0


def test_promotion_is_explicit_and_never_automatic():

    context = TemporaryContext(clock=lambda: NOW)

    context.note("at the hospital right now")

    # Nothing promotes it on its own.
    assert context.render() == ["at the hospital right now"]

    promoted = context.promote("at the hospital right now")

    assert promoted == "at the hospital right now"
    assert context.render() == []


def test_promoting_an_expired_note_returns_nothing():
    """It stopped being true. That is itself an answer."""

    ticks = {"now": NOW}

    context = TemporaryContext(ttl_seconds=60, clock=lambda: ticks["now"])
    context.note("just woke up")

    ticks["now"] = NOW + timedelta(hours=1)

    assert context.promote("just woke up") == ""


def test_restating_refreshes_rather_than_stacks():

    context = TemporaryContext(clock=lambda: NOW)

    context.note("at a cafe")
    context.note("at a cafe")

    assert len(context.active()) == 1


def test_temporary_context_is_bounded():

    context = TemporaryContext(max_entries=3, clock=lambda: NOW)

    for index in range(10):
        context.note(f"note {index}")

    assert len(context.active()) == 3


def test_temporary_render_is_newest_first_and_bounded():

    context = TemporaryContext(clock=lambda: NOW)

    context.note("first")
    context.note("second")
    context.note("third")

    assert context.render(limit=2) == ["third", "second"]


# ======================================================================
# Ranked retrieval
# ======================================================================

@pytest.fixture
def retriever(episodic, clock):
    return RankedRetriever(episodic, clock=clock.now)


def test_ranked_retriever_satisfies_the_protocol(retriever):
    """The seam is the point: it must be swappable for KeywordRetriever."""

    assert isinstance(retriever, Retriever)


def test_relevance_beats_recency(episodic, retriever):
    """
    A recency-heavy ranker answers every question with whatever
    happened most recently. That is the failure this weighting prevents.
    """

    episodic.remember(
        "I finished the sqlite migration",
        occurred_at=NOW - timedelta(days=20),
        importance=0.5,
    )
    episodic.remember(
        "I ate a sandwich", occurred_at=NOW - timedelta(hours=1), importance=0.5
    )

    results = retriever.search("sqlite migration", limit=1)

    assert len(results) == 1
    assert "migration" in results[0]


def test_recency_breaks_ties_between_equally_relevant_memories(
    episodic, retriever
):

    episodic.remember(
        "working on the migration", occurred_at=NOW - timedelta(days=60)
    )
    episodic.remember(
        "working on the migration", occurred_at=NOW - timedelta(days=1)
    )

    top = retriever.rank("migration", limit=1)[0][0]

    assert top.occurred_at.startswith((NOW - timedelta(days=1)).strftime("%Y-%m-%d"))


def test_importance_breaks_ties_between_equally_relevant_memories(
    episodic, retriever
):

    episodic.remember("the deploy pipeline", occurred_at=NOW, importance=0.1)
    episodic.remember("the deploy pipeline", occurred_at=NOW, importance=0.9)

    # Same day, same text: the store folds them and keeps the stronger.
    assert episodic.recent()[0].importance == pytest.approx(0.9)


def test_recall_is_bounded_by_limit(episodic, retriever):
    """Never dump the database into a prompt."""

    for index in range(40):
        episodic.remember(f"the migration step {index}", occurred_at=NOW)

    assert len(retriever.search("migration", limit=3)) == 3


def test_irrelevant_memories_are_not_recalled(episodic, retriever):

    episodic.remember("I like pixel art", occurred_at=NOW, importance=1.0)

    assert retriever.search("kubernetes networking") == []


def test_empty_query_recalls_nothing(episodic, retriever):

    episodic.remember("I like pixel art", occurred_at=NOW, importance=1.0)

    assert retriever.search("") == []
    assert retriever.search("   the and of  ") == []


def test_recalled_lines_are_dated_in_words(episodic, retriever):
    """
    A memory the model cannot place in time gets misdescribed. This is
    why temporal context and episodic memory were built together.
    """

    episodic.remember(
        "I finished the migration", occurred_at=NOW - timedelta(days=1, hours=18)
    )

    line = retriever.search("migration", limit=1)[0]

    assert line.startswith(("yesterday", "last night", "2 days ago"))
    assert "finished the migration" in line


def test_a_future_plan_is_not_recalled_as_a_past_event(episodic, retriever):
    """Reporting a plan as done is exactly the confident wrongness to avoid."""

    episodic.remember(
        "I'm going to rewrite the retriever",
        category="event",
        occurred_at=NOW + timedelta(days=3),
    )

    assert retriever.search("rewrite retriever") == []


def test_a_future_plan_labelled_as_a_plan_is_recalled(episodic, retriever):
    """"You're planning to X" is a true sentence."""

    episodic.remember(
        "I'm going to rewrite the retriever",
        category="plan",
        occurred_at=NOW + timedelta(days=3),
    )

    results = retriever.search("rewrite retriever", limit=1)

    assert len(results) == 1
    assert "tomorrow" in results[0] or "in 3 days" in results[0]


def test_an_old_memory_still_competes_when_it_is_the_only_match(
    episodic, retriever
):
    """Recency decays, it does not filter."""

    episodic.remember(
        "I set up the Arduino weather station",
        occurred_at=NOW - timedelta(days=400),
    )

    assert retriever.search("arduino weather station", limit=1)


def test_unreadable_timestamp_does_not_break_recall(episodic, retriever, session):

    episodic.remember("the migration", occurred_at=NOW)

    stored = episodic.recent()[0]
    stored.occurred_at = "not a date"
    session.commit()

    # It is still findable, just undated.
    assert retriever.search("migration", limit=1) == ["the migration"]


# ======================================================================
# User model
# ======================================================================

def test_unknown_is_a_first_class_answer(model):

    assert model.status_of("identity.name") is Status.UNKNOWN
    assert model.get("identity.name") is None
    assert model.value_of("identity.name") == ""


def test_confirmed_and_inferred_are_distinguishable(model):

    model.confirm("identity.name", "Leo")
    model.infer("personality.curiosity", "very high", category=PERSONALITY)

    assert model.status_of("identity.name") is Status.CONFIRMED
    assert model.status_of("personality.curiosity") is Status.INFERRED


def test_an_inference_is_marked_as_one_in_the_prompt(model):
    """So Aura hedges instead of stating a guess as fact."""

    model.infer("personality.curiosity", "very high", category=PERSONALITY)

    assert model.get("personality.curiosity").render().endswith("(inferred)")


def test_a_confirmation_is_not_marked_as_inferred(model):

    model.confirm("identity.name", "Leo")

    assert "(inferred)" not in model.get("identity.name").render()


def test_nothing_promotes_an_inference_except_the_user(model):
    """
    Confidence does not promote. Repetition does not promote. Only the
    user saying so promotes.
    """

    model.infer("identity.city", "Da Nang", confidence=0.5)

    for _ in range(10):
        model.infer("identity.city", "Da Nang", confidence=0.99)

    assert model.status_of("identity.city") is Status.INFERRED

    model.confirm("identity.city", "Da Nang")

    assert model.status_of("identity.city") is Status.CONFIRMED


def test_an_inference_never_overwrites_what_the_user_said(model):

    model.confirm("identity.city", "Da Nang")
    model.infer("identity.city", "Hanoi", confidence=0.99)

    assert model.value_of("identity.city") == "Da Nang"
    assert model.status_of("identity.city") is Status.CONFIRMED


def test_a_correction_changes_the_stored_entry(model):
    """
    "You remembered that wrong" must change the entry, not produce an
    apology and leave it in place.
    """

    model.confirm("preference.drink", "coffee")

    model.correct("preference.drink", "tea")

    assert model.value_of("preference.drink") == "tea"
    assert model.status_of("preference.drink") is Status.CONFIRMED


def test_a_correction_of_something_unknown_still_records_it(model):

    model.correct("preference.drink", "tea")

    assert model.value_of("preference.drink") == "tea"


def test_correction_stamps_last_confirmed(model):

    model.infer("identity.city", "Hanoi")

    assert model.get("identity.city").last_confirmed_at is None

    model.correct("identity.city", "Da Nang")

    assert model.get("identity.city").last_confirmed_at


def test_a_time_limited_belief_stops_being_authoritative(model):

    model.confirm(
        "project.current",
        "Phase 8",
        category="project",
        valid_until=NOW - timedelta(days=1),
    )

    assert model.get("project.current") is not None      # still stored
    assert model.valid() == []                            # but not in force
    assert model.relevant("what am I working on") == []


def test_a_not_yet_valid_belief_is_not_used(model):

    model.confirm(
        "project.next", "Phase 9", valid_from=NOW + timedelta(days=7)
    )

    assert model.valid() == []


def test_a_stable_belief_has_no_expiry(model):

    model.confirm("identity.primary_language", "Vietnamese")

    belief = model.get("identity.primary_language")

    assert belief.valid_from is None
    assert belief.valid_until is None
    assert belief.valid_at(NOW + timedelta(days=3650))


def test_keys_are_normalised_so_a_fact_is_corrected_not_duplicated(model):

    model.confirm("Identity.Primary Language", "Vietnamese")
    model.correct("identity.primary_language", "Vietnamese and English")

    assert len(model) == 1
    assert model.value_of("identity.primary_language") == "Vietnamese and English"


def test_relevance_filtering_bounds_what_reaches_the_prompt(model):
    """The whole model is far too large to inject."""

    for index in range(40):
        model.confirm(f"interest.thing_{index}", f"topic number {index}")

    assert len(model.render("topic", limit=5)) == 5


def test_irrelevant_beliefs_are_not_injected(model):

    model.confirm("interest.minecraft", "plays Minecraft", category="interest")

    assert model.render("kubernetes ingress") == []


def test_render_without_a_query_gives_only_core_identity(model):

    model.confirm("identity.primary_language", "Vietnamese", category=IDENTITY)
    model.confirm("communication.tone", "casual", category=COMMUNICATION)
    model.confirm("interest.minecraft", "plays Minecraft", category="interest")
    model.infer("personality.curiosity", "very high", category=PERSONALITY)

    lines = model.render()

    assert any("Vietnamese" in line for line in lines)
    assert any("casual" in line for line in lines)
    assert not any("Minecraft" in line for line in lines)
    assert not any("curiosity" in line for line in lines)


def test_forget_and_clear_user_model(model):

    model.confirm("identity.name", "Leo")

    assert model.forget("identity.name") is True
    assert model.forget("identity.name") is False

    model.confirm("identity.name", "Leo")
    model.clear()

    assert len(model) == 0


# ======================================================================
# Initial profile seed
# ======================================================================

def test_seeding_writes_the_profile_as_structured_entries(model):

    written = seed_user_model(model)

    assert written > 30
    assert model.value_of("identity.primary_language") == "Vietnamese"
    assert "bro" in model.value_of("identity.address_style")
    assert model.status_of("personality.curiosity") is Status.CONFIRMED


def test_seeding_is_idempotent(model):

    first = seed_user_model(model)
    second = seed_user_model(model)

    assert first > 0
    assert second == 0
    assert len(model) == first


def test_seeding_never_undoes_a_correction(model):
    """A restart must not undo "actually I prefer coffee now"."""

    seed_user_model(model)

    model.correct("identity.primary_language", "English")

    seed_user_model(model)

    assert model.value_of("identity.primary_language") == "English"


def test_risk_patterns_are_inferred_not_asserted(model):
    """
    They were given as patterns, not diagnoses. Aura must hedge rather
    than tell someone what their flaws are.
    """

    seed_user_model(model)

    assert model.status_of("thinking.risks") is Status.INFERRED
    assert model.get("thinking.risks").render().endswith("(inferred)")


def test_seeded_profile_is_not_dumped_into_the_prompt(model):
    """46 entries in the database, a handful in any given prompt."""

    seed_user_model(model)

    assert len(model) > 30
    assert len(model.render("what should I work on", limit=6)) <= 6
    assert len(model.render()) <= 6


def test_both_project_profiles_are_seeded_and_selectively_recalled(model):

    seed_user_model(model)

    aura = model.render("what is aura", limit=6)
    duality = model.render("duality boss fight", limit=6)

    assert any("companion" in line for line in aura)
    assert any("Minecraft Forge" in line for line in duality)

    # Duality must not turn up when the subject is Aura.
    assert not any("Forge" in line for line in aura)


# ======================================================================
# The pipeline
# ======================================================================

def test_pipeline_stores_a_meaningful_statement(pipeline):

    outcome = pipeline.observe("user", "I just finished the sqlite migration")

    assert outcome.kind == "episodic"
    assert len(pipeline.episodic) == 1


def test_pipeline_ignores_trivia(pipeline):

    assert pipeline.observe("user", "ok").kind == ""
    assert len(pipeline.episodic) == 0


def test_pipeline_never_stores_assistant_text(pipeline):

    pipeline.observe("assistant", "I just finished the sqlite migration")

    assert len(pipeline.episodic) == 0


def test_pipeline_recalls_what_it_stored(pipeline):

    pipeline.observe("user", "I just finished the sqlite migration")

    lines = pipeline.memory_lines("how did the migration go")

    assert any("migration" in line for line in lines)


def test_pipeline_orders_stable_facts_before_passing_remarks(pipeline):
    """A truncation must drop the least trustworthy lines first."""

    pipeline.user_model.confirm(
        "identity.primary_language", "Vietnamese", category=IDENTITY
    )
    pipeline.observe("user", "I just finished the sqlite migration")
    pipeline.observe("user", "I'm at a cafe right now with my laptop")

    lines = pipeline.memory_lines("language migration cafe laptop")

    assert "Vietnamese" in lines[0]
    assert "cafe" in lines[-1]


def test_pipeline_memory_lines_are_bounded(pipeline):

    for index in range(30):
        pipeline.observe("user", f"I finished the migration step {index}")

    lines = pipeline.memory_lines(
        "migration", max_episodic=2, max_temporary=1, max_user_model=1
    )

    assert len(lines) <= 4


def test_pipeline_ensure_profile_is_idempotent(pipeline):

    first = pipeline.ensure_profile()

    assert first > 0
    assert pipeline.ensure_profile() == 0
    assert pipeline.user_model_ready is True


def test_pipeline_outcome_always_explains_itself(pipeline):

    assert pipeline.observe("user", "ok").note
    assert pipeline.observe("assistant", "hello").note
    assert pipeline.observe("user", "I prefer tea over coffee now").note


def test_build_memory_pipeline_reads_config(session):

    pipeline = build_memory_pipeline(
        {"memory": {"recall": False, "retrieval_scope": 25}}, session=session
    )

    assert pipeline.recall_enabled is False
    assert pipeline.retriever.scope == 25


def test_build_memory_pipeline_tolerates_no_config(session):

    pipeline = build_memory_pipeline(None, session=session)

    assert pipeline.recall_enabled is True


def test_pipeline_does_not_leak_between_instances(session):
    """
    Test isolation, asserted rather than assumed: two pipelines on
    separate databases must not see each other's memories.
    """

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    other_session = sessionmaker(bind=engine)()

    try:
        first = MemoryPipeline(session=session, clock=TemporalClock(now=lambda: NOW))
        second = MemoryPipeline(
            session=other_session, clock=TemporalClock(now=lambda: NOW)
        )

        first.observe("user", "I just finished the sqlite migration")

        assert len(first.episodic) == 1
        assert len(second.episodic) == 0
    finally:
        other_session.close()
