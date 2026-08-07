"""
Companion memory interfaces.

What Aura should eventually remember, expressed as shapes rather than
tables:

    preferences     how he likes things done
    facts           things Aura has observed or been told
    projects        what he is building, and where each one stands
    goals           what he wants to achieve, short-term or ongoing
    coding style    conventions to follow when writing code for him
    highlights      conversations worth keeping

There is no database here, and that is deliberate. Committing to a schema
before knowing how these are read is how a migration gets written twice.
What exists now is the interface each store must satisfy and an in-memory
implementation of it, which is enough to wire the rest of the system
against and enough to test.

`memory/profile.py` already stores durable key/value facts in SQLite and
is unaffected by any of this. When these gain a real backing store, they
gain it behind these Protocols, and nothing that reads them changes.

Everything leaves this module as rendered strings, the same rule the rest
of memory/ follows, so brain/ never learns what a Project is.
"""

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from memory.models import timestamp_now


# ----------------------------------------------------------------------
# Records
#
# Frozen, primitive, and free of behaviour beyond rendering themselves.
# A future ORM row only has to be convertible to one of these.
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class Preference:
    """Something the user likes, dislikes or wants done a certain way."""

    topic: str
    value: str
    note: str = ""

    def render(self) -> str:
        line = f"{self.topic}: {self.value}"
        return f"{line} ({self.note})" if self.note else line


@dataclass(frozen=True)
class Fact:
    """Something Aura has observed or been told about the user.

    Distinct from Preference: a fact is "he uses VS Code" or "he lives
    in Da Nang"; a preference is "names are camelCase" or "keep methods
    short". Facts describe what is true; preferences describe how things
    should be.

    Also distinct from profile facts in memory/models.py — those are
    SQLite-backed key/value pairs with categories; these are learned
    observations that may come from conversation without an explicit
    "remember that X" command.
    """

    topic: str
    value: str
    note: str = ""

    def render(self) -> str:
        line = f"{self.topic}: {self.value}"
        return f"{line} ({self.note})" if self.note else line


@dataclass(frozen=True)
class Goal:
    """Something the user wants to achieve.

    Could be short-term ("debug the auth flow by Friday") or ongoing
    ("shrink the CI pipeline under 3 minutes"). Not a project — a
    project is something being built; a goal is a desired outcome.
    """

    title: str
    detail: str = ""
    priority: str = ""         # "now" | "soon" | "someday" | ""

    def render(self) -> str:
        parts = [self.title]
        if self.detail:
            parts.append(f"- {self.detail}")
        if self.priority:
            parts.append(f"[{self.priority}]")
        return " ".join(parts)


@dataclass(frozen=True)
class Project:
    """Something the user is building."""

    name: str
    description: str = ""
    status: str = "active"          # "active" | "paused" | "done"
    tags: tuple[str, ...] = ()

    def render(self) -> str:

        parts = [self.name]

        if self.description:
            parts.append(f"- {self.description}")

        if self.status and self.status != "active":
            parts.append(f"[{self.status}]")

        return " ".join(parts)


@dataclass(frozen=True)
class CodingStyle:
    """
    How the user wants code written.

    Free text rather than a schema of booleans. "Comments explain why,
    not what" is a real convention and does not fit a checkbox.
    """

    language: str = ""
    conventions: tuple[str, ...] = ()

    def render(self) -> list[str]:

        prefix = f"{self.language}: " if self.language else ""

        return [f"{prefix}{rule}" for rule in self.conventions if rule]


@dataclass(frozen=True)
class Highlight:
    """A conversation worth remembering, summarised."""

    summary: str
    reason: str = ""
    at: str = field(default_factory=timestamp_now)

    def render(self) -> str:
        return f"{self.summary} ({self.reason})" if self.reason else self.summary


# ----------------------------------------------------------------------
# Interfaces
# ----------------------------------------------------------------------

@runtime_checkable
class FactStore(Protocol):

    def remember(self, topic: str, value: str, note: str = "") -> None:
        ...

    def all(self, limit: int | None = None) -> list[Fact]:
        ...


@runtime_checkable
class GoalStore(Protocol):

    def record(self, goal: Goal) -> None:
        ...

    def active(self, limit: int | None = None) -> list[Goal]:
        ...


@runtime_checkable
class PreferenceStore(Protocol):

    def remember(self, topic: str, value: str, note: str = "") -> None:
        ...

    def all(self, limit: int | None = None) -> list[Preference]:
        ...


@runtime_checkable
class ProjectStore(Protocol):

    def record(self, project: Project) -> None:
        ...

    def active(self, limit: int | None = None) -> list[Project]:
        ...


@runtime_checkable
class CodingStyleStore(Protocol):

    def learn(self, style: CodingStyle) -> None:
        ...

    def all(self) -> list[CodingStyle]:
        ...


@runtime_checkable
class HighlightStore(Protocol):

    def keep(self, highlight: Highlight) -> None:
        ...

    def recent(self, limit: int = 5) -> list[Highlight]:
        ...


# ----------------------------------------------------------------------
# In-memory implementations
#
# Real enough to use, small enough to throw away when a durable store
# replaces them. Nothing persists past the process.
# ----------------------------------------------------------------------

class InMemoryFacts:

    def __init__(self):
        self._by_topic: dict[str, Fact] = {}

    def remember(self, topic: str, value: str, note: str = "") -> None:

        key = (topic or "").strip().lower()

        if not key or not (value or "").strip():
            return

        self._by_topic[key] = Fact(
            topic=key,
            value=value.strip(),
            note=note.strip(),
        )

    def all(self, limit: int | None = None) -> list[Fact]:

        values = list(self._by_topic.values())

        return values[:limit] if limit else values

    def __len__(self) -> int:
        return len(self._by_topic)


class InMemoryGoals:

    def __init__(self):
        self._by_title: dict[str, Goal] = {}

    def record(self, goal: Goal) -> None:

        title = (goal.title or "").strip()

        if not title:
            return

        self._by_title[title.lower()] = goal

    def active(self, limit: int | None = None) -> list[Goal]:

        found = [
            g for g in self._by_title.values()
            if g.priority in ("now", "soon")
        ]

        return found[:limit] if limit else found

    def all(self) -> list[Goal]:
        return list(self._by_title.values())

    def __len__(self) -> int:
        return len(self._by_title)


class InMemoryPreferences:

    def __init__(self):
        self._by_topic: dict[str, Preference] = {}

    def remember(self, topic: str, value: str, note: str = "") -> None:

        key = (topic or "").strip().lower()

        if not key or not (value or "").strip():
            return

        # Newest wins, like ProfileStore. Two contradictory preferences
        # are worse than one stale one.
        self._by_topic[key] = Preference(
            topic=key,
            value=value.strip(),
            note=note.strip(),
        )

    def all(self, limit: int | None = None) -> list[Preference]:

        values = list(self._by_topic.values())

        return values[:limit] if limit else values

    def __len__(self) -> int:
        return len(self._by_topic)


class InMemoryProjects:

    def __init__(self):
        self._by_name: dict[str, Project] = {}

    def record(self, project: Project) -> None:

        name = (project.name or "").strip()

        if not name:
            return

        self._by_name[name.lower()] = project

    def active(self, limit: int | None = None) -> list[Project]:

        found = [p for p in self._by_name.values() if p.status == "active"]

        return found[:limit] if limit else found

    def all(self) -> list[Project]:
        return list(self._by_name.values())

    def __len__(self) -> int:
        return len(self._by_name)


class InMemoryCodingStyle:

    def __init__(self):
        self._by_language: dict[str, CodingStyle] = {}

    def learn(self, style: CodingStyle) -> None:

        if not style.conventions:
            return

        self._by_language[style.language.lower()] = style

    def all(self) -> list[CodingStyle]:
        return list(self._by_language.values())

    def for_language(self, language: str) -> CodingStyle | None:
        return self._by_language.get((language or "").strip().lower())

    def __len__(self) -> int:
        return len(self._by_language)


class InMemoryHighlights:

    def __init__(self, cap: int = 100):
        self._kept: list[Highlight] = []
        self.cap = cap

    def keep(self, highlight: Highlight) -> None:

        if not (highlight.summary or "").strip():
            return

        self._kept.append(highlight)

        if len(self._kept) > self.cap:
            del self._kept[: len(self._kept) - self.cap]

    def recent(self, limit: int = 5) -> list[Highlight]:
        return list(reversed(self._kept[-limit:])) if limit else []

    def __len__(self) -> int:
        return len(self._kept)


# ----------------------------------------------------------------------
# The bundle the brain would ask
# ----------------------------------------------------------------------

class CompanionMemory:
    """
    Every companion store behind one object, rendered as prompt lines.

    Satisfies the same KnowledgeProvider shape that
    MemoryKnowledgeProvider does - `get_knowledge(query) -> list[str]` -
    so it can be handed to the brain the moment it has anything worth
    saying. Today it returns whatever was put into it; it does not yet
    rank by the query, and it says so rather than pretending to.

    Source order is deliberate and is the priority order when max_lines
    truncates: who he is and what he wants outrank what he is currently
    building, which outranks how code should look, which outranks what
    was said last week.
    """

    def __init__(
        self,
        facts: FactStore | None = None,
        preferences: PreferenceStore | None = None,
        goals: GoalStore | None = None,
        projects: ProjectStore | None = None,
        coding_style: CodingStyleStore | None = None,
        highlights: HighlightStore | None = None,
        max_lines: int = 10,
        max_highlights: int = 3,
    ):

        self.facts = facts or InMemoryFacts()
        self.preferences = preferences or InMemoryPreferences()
        self.goals = goals or InMemoryGoals()
        self.projects = projects or InMemoryProjects()
        self.coding_style = coding_style or InMemoryCodingStyle()
        self.highlights = highlights or InMemoryHighlights()
        self.max_lines = max_lines
        self.max_highlights = max_highlights

    def get_knowledge(self, query: str) -> list[str]:

        lines: list[str] = []

        lines.extend(self._safe(self._fact_lines))
        lines.extend(self._safe(self._preference_lines))
        lines.extend(self._safe(self._goal_lines))
        lines.extend(self._safe(self._project_lines))
        lines.extend(self._safe(self._style_lines))
        lines.extend(self._safe(self._highlight_lines))

        return lines[: self.max_lines]

    # Each source is wrapped, so one broken store costs its own lines
    # rather than the whole memory section.

    @staticmethod
    def _safe(reader) -> list[str]:

        try:
            return reader()
        except Exception:
            return []

    def _fact_lines(self) -> list[str]:
        return [f"knows {f.render()}" for f in self.facts.all()]

    def _preference_lines(self) -> list[str]:
        return [f"prefers {p.render()}" for p in self.preferences.all()]

    def _goal_lines(self) -> list[str]:
        return [f"goal - {g.render()}" for g in self.goals.active()]

    def _project_lines(self) -> list[str]:
        return [f"project - {p.render()}" for p in self.projects.active()]

    def _style_lines(self) -> list[str]:

        lines: list[str] = []

        for style in self.coding_style.all():
            lines.extend(f"code style - {rule}" for rule in style.render())

        return lines

    def _highlight_lines(self) -> list[str]:
        highlights = self.highlights.recent(limit=self.max_highlights)
        return [f"past - {h.render()}" for h in highlights]


__all__ = [
    "Fact",
    "Goal",
    "Preference",
    "Project",
    "CodingStyle",
    "Highlight",
    "FactStore",
    "GoalStore",
    "PreferenceStore",
    "ProjectStore",
    "CodingStyleStore",
    "HighlightStore",
    "InMemoryFacts",
    "InMemoryGoals",
    "InMemoryPreferences",
    "InMemoryProjects",
    "InMemoryCodingStyle",
    "InMemoryHighlights",
    "CompanionMemory",
]
