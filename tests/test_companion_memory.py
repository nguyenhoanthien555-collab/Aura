"""
Companion memory tests.

Covers the new types added in Section 6: Fact, Goal, their stores,
and the CompanionMemory knowledge provider that composes all six
sources into prompt-ready lines.
"""

import pytest

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


# ======================================================================
# Fact
# ======================================================================

def test_fact_construction():

    fact = Fact(topic="location", value="Da Nang")

    assert fact.topic == "location"
    assert fact.value == "Da Nang"
    assert fact.note == ""


def test_fact_render_without_note():

    fact = Fact(topic="editor", value="VS Code")

    assert fact.render() == "editor: VS Code"


def test_fact_render_with_note():

    fact = Fact(
        topic="timezone",
        value="UTC+7",
        note="Vietnam Standard Time",
    )

    assert fact.render() == "timezone: UTC+7 (Vietnam Standard Time)"


def test_fact_is_frozen():

    fact = Fact(topic="name", value="Hoan")

    with pytest.raises(Exception):
        fact.topic = "other"


# ======================================================================
# Goal
# ======================================================================

def test_goal_construction():

    goal = Goal(title="Finish Aura Sprint 5")

    assert goal.title == "Finish Aura Sprint 5"
    assert goal.detail == ""
    assert goal.priority == ""


def test_goal_render_minimal():

    goal = Goal(title="Learn Rust")

    assert goal.render() == "Learn Rust"


def test_goal_render_with_detail():

    goal = Goal(
        title="Optimize CI pipeline",
        detail="shrink under 3 minutes"
    )

    assert goal.render() == "Optimize CI pipeline - shrink under 3 minutes"


def test_goal_render_with_priority():

    goal = Goal(
        title="Fix auth bug",
        priority="now",
    )

    assert goal.render() == "Fix auth bug [now]"


def test_goal_render_complete():

    goal = Goal(
        title="Deploy v2",
        detail="with zero downtime",
        priority="soon",
    )

    rendered = goal.render()

    assert "Deploy v2" in rendered
    assert "with zero downtime" in rendered
    assert "[soon]" in rendered


def test_goal_is_frozen():

    goal = Goal(title="Build plugin system")

    with pytest.raises(Exception):
        goal.title = "other"


# ======================================================================
# InMemoryFacts
# ======================================================================

def test_fact_store_empty():

    store = InMemoryFacts()

    assert len(store) == 0
    assert store.all() == []


def test_fact_store_remember():

    store = InMemoryFacts()

    store.remember("location", "Hanoi")

    assert len(store) == 1

    facts = store.all()

    assert len(facts) == 1
    assert facts[0].topic == "location"
    assert facts[0].value == "Hanoi"


def test_fact_store_normalizes_topic():

    store = InMemoryFacts()

    store.remember("  Favorite Drink  ", "coffee")

    facts = store.all()

    assert facts[0].topic == "favorite drink"


def test_fact_store_rejects_empty_topic():

    store = InMemoryFacts()

    store.remember("", "value")
    store.remember("   ", "value")

    assert len(store) == 0


def test_fact_store_rejects_empty_value():

    store = InMemoryFacts()

    store.remember("topic", "")
    store.remember("topic", "   ")

    assert len(store) == 0


def test_fact_store_overwrites_on_duplicate_topic():

    store = InMemoryFacts()

    store.remember("city", "Hanoi")
    store.remember("city", "Da Nang")

    assert len(store) == 1

    facts = store.all()

    assert facts[0].value == "Da Nang"


def test_fact_store_limit():

    store = InMemoryFacts()

    store.remember("a", "1")
    store.remember("b", "2")
    store.remember("c", "3")

    facts = store.all(limit=2)

    assert len(facts) == 2


# ======================================================================
# InMemoryGoals
# ======================================================================

def test_goal_store_empty():

    store = InMemoryGoals()

    assert len(store) == 0
    assert store.active() == []


def test_goal_store_record():

    store = InMemoryGoals()

    goal = Goal(title="Learn Rust", priority="soon")

    store.record(goal)

    assert len(store) == 1


def test_goal_store_active_filters_priority():

    store = InMemoryGoals()

    store.record(Goal(title="Now task", priority="now"))
    store.record(Goal(title="Soon task", priority="soon"))
    store.record(Goal(title="Someday task", priority="someday"))
    store.record(Goal(title="No priority"))

    active = store.active()

    assert len(active) == 2

    titles = {g.title for g in active}

    assert "Now task" in titles
    assert "Soon task" in titles
    assert "Someday task" not in titles


def test_goal_store_rejects_empty_title():

    store = InMemoryGoals()

    store.record(Goal(title="", priority="now"))
    store.record(Goal(title="   ", priority="now"))

    assert len(store) == 0


def test_goal_store_overwrites_on_duplicate_title():

    store = InMemoryGoals()

    store.record(Goal(title="Finish Sprint 5", priority="now"))
    store.record(Goal(title="Finish Sprint 5", priority="soon"))

    assert len(store) == 1

    goals = store.all()

    assert goals[0].priority == "soon"


def test_goal_store_limit():

    store = InMemoryGoals()

    store.record(Goal(title="A", priority="now"))
    store.record(Goal(title="B", priority="soon"))
    store.record(Goal(title="C", priority="now"))

    active = store.active(limit=2)

    assert len(active) == 2


# ======================================================================
# CompanionMemory integration
# ======================================================================

def test_companion_memory_empty():

    companion = CompanionMemory()

    lines = companion.get_knowledge("test query")

    assert lines == []


def test_companion_memory_renders_facts():

    facts = InMemoryFacts()
    facts.remember("location", "Da Nang")
    facts.remember("editor", "VS Code")

    companion = CompanionMemory(facts=facts)

    lines = companion.get_knowledge("")

    assert len(lines) == 2
    assert any("location: Da Nang" in line for line in lines)
    assert any("editor: VS Code" in line for line in lines)


def test_companion_memory_renders_preferences():

    prefs = InMemoryPreferences()
    prefs.remember("naming", "camelCase")

    companion = CompanionMemory(preferences=prefs)

    lines = companion.get_knowledge("")

    assert len(lines) == 1
    assert "prefers" in lines[0]
    assert "naming: camelCase" in lines[0]


def test_companion_memory_renders_goals():

    goals = InMemoryGoals()
    goals.record(Goal(title="Finish Sprint 5", priority="now"))
    goals.record(Goal(title="Someday task", priority="someday"))

    companion = CompanionMemory(goals=goals)

    lines = companion.get_knowledge("")

    assert len(lines) == 1
    assert "goal -" in lines[0]
    assert "Finish Sprint 5" in lines[0]


def test_companion_memory_renders_projects():

    projects = InMemoryProjects()
    projects.record(Project(name="Aura", status="active"))

    companion = CompanionMemory(projects=projects)

    lines = companion.get_knowledge("")

    assert len(lines) == 1
    assert "project -" in lines[0]
    assert "Aura" in lines[0]


def test_companion_memory_renders_coding_style():

    styles = InMemoryCodingStyle()
    styles.learn(
        CodingStyle(
            language="python",
            conventions=("use type hints", "keep functions short")
        )
    )

    companion = CompanionMemory(coding_style=styles)

    lines = companion.get_knowledge("")

    assert len(lines) == 2
    assert any("code style -" in line for line in lines)


def test_companion_memory_renders_highlights():

    highlights = InMemoryHighlights()
    highlights.keep(Highlight(summary="Fixed the streaming bug"))
    highlights.keep(Highlight(summary="Discussed architecture"))

    companion = CompanionMemory(highlights=highlights, max_highlights=2)

    lines = companion.get_knowledge("")

    assert len(lines) == 2
    assert all("past -" in line for line in lines)


def test_companion_memory_source_order():
    """
    Facts, preferences, goals, projects, style, highlights.

    Source order is priority order when max_lines truncates.
    """

    facts = InMemoryFacts()
    facts.remember("location", "Da Nang")

    prefs = InMemoryPreferences()
    prefs.remember("naming", "camelCase")

    goals = InMemoryGoals()
    goals.record(Goal(title="Finish Sprint 5", priority="now"))

    projects = InMemoryProjects()
    projects.record(Project(name="Aura"))

    styles = InMemoryCodingStyle()
    styles.learn(CodingStyle(conventions=("use type hints",)))

    highlights = InMemoryHighlights()
    highlights.keep(Highlight(summary="Fixed bug"))

    companion = CompanionMemory(
        facts=facts,
        preferences=prefs,
        goals=goals,
        projects=projects,
        coding_style=styles,
        highlights=highlights,
        max_lines=100,
    )

    lines = companion.get_knowledge("")

    # All sources should be present and in order
    assert len(lines) == 6

    # Facts first
    assert "knows" in lines[0]

    # Preferences second
    assert "prefers" in lines[1]

    # Goals third
    assert "goal -" in lines[2]

    # Projects fourth
    assert "project -" in lines[3]

    # Style fifth
    assert "code style -" in lines[4]

    # Highlights last
    assert "past -" in lines[5]


def test_companion_memory_max_lines_truncates():

    facts = InMemoryFacts()
    facts.remember("a", "1")
    facts.remember("b", "2")
    facts.remember("c", "3")

    prefs = InMemoryPreferences()
    prefs.remember("x", "y")
    prefs.remember("y", "z")

    companion = CompanionMemory(
        facts=facts,
        preferences=prefs,
        max_lines=3,
    )

    lines = companion.get_knowledge("")

    assert len(lines) == 3


def test_companion_memory_broken_store_does_not_crash():
    """One broken store costs its own lines, not the whole section."""

    class BrokenStore:
        def all(self):
            raise RuntimeError("Simulated failure")

    companion = CompanionMemory(facts=BrokenStore())

    # Should not raise
    lines = companion.get_knowledge("")

    assert lines == []


def test_companion_memory_satisfies_knowledge_provider_protocol():
    """CompanionMemory can be handed to ConversationManager as knowledge."""

    from brain.ports import KnowledgeProvider

    companion = CompanionMemory()

    # Structurally satisfies the protocol
    assert hasattr(companion, "get_knowledge")
    assert callable(companion.get_knowledge)

    # Returns the right shape
    lines = companion.get_knowledge("test query")

    assert isinstance(lines, list)
    assert all(isinstance(line, str) for line in lines)


# ======================================================================
# Prompt format verification
# ======================================================================

def test_fact_lines_start_with_knows():

    facts = InMemoryFacts()
    facts.remember("location", "Da Nang")

    companion = CompanionMemory(facts=facts)

    lines = companion.get_knowledge("")

    assert lines[0].startswith("knows")


def test_preference_lines_start_with_prefers():

    prefs = InMemoryPreferences()
    prefs.remember("naming", "camelCase")

    companion = CompanionMemory(preferences=prefs)

    lines = companion.get_knowledge("")

    assert lines[0].startswith("prefers")


def test_goal_lines_start_with_goal_dash():

    goals = InMemoryGoals()
    goals.record(Goal(title="Finish Sprint 5", priority="now"))

    companion = CompanionMemory(goals=goals)

    lines = companion.get_knowledge("")

    assert lines[0].startswith("goal -")


def test_project_lines_start_with_project_dash():

    projects = InMemoryProjects()
    projects.record(Project(name="Aura"))

    companion = CompanionMemory(projects=projects)

    lines = companion.get_knowledge("")

    assert lines[0].startswith("project -")


def test_style_lines_start_with_code_style_dash():

    styles = InMemoryCodingStyle()
    styles.learn(CodingStyle(conventions=("use type hints",)))

    companion = CompanionMemory(coding_style=styles)

    lines = companion.get_knowledge("")

    assert lines[0].startswith("code style -")


def test_highlight_lines_start_with_past_dash():

    highlights = InMemoryHighlights()
    highlights.keep(Highlight(summary="Fixed bug"))

    companion = CompanionMemory(highlights=highlights)

    lines = companion.get_knowledge("")

    assert lines[0].startswith("past -")
