"""
SQLite-backed companion stores.

Durable implementations of the same Protocols in `memory/companion.py`
(FactStore, PreferenceStore, GoalStore, ProjectStore, CodingStyleStore,
HighlightStore). The in-memory versions vanish on restart; these persist
to the shared `companion_memory` table so Aura still knows the person she
is talking to after the process comes back.

Everything a store reads or writes is one `CompanionMemoryRecord` row: its
`kind` names the store, its `key` is the dedup key, and `payload` is the
JSON of the dataclass. Nothing here changes what a reader sees - each
method returns the exact record dataclass its in-memory twin returned, so
`CompanionMemory` and the brain are unaffected by which backing is wired.

Reads and writes hold `db_lock` (the same serialization the rest of
memory/ uses on the single SQLite connection). A write that hits a
database error is logged and dropped rather than raising into the turn -
losing one learned fact is better than failing the reply.
"""

import json

from core.logger import logger
from memory.companion import (
    CodingStyle,
    Fact,
    Goal,
    Highlight,
    Preference,
    Project,
)
from memory.models import CompanionMemoryRecord, timestamp_now
from memory.sqlite import SessionLocal, db_lock


def _tuple(value) -> tuple:
    """JSON restores lists; the dataclasses declare tuples."""
    return tuple(value) if isinstance(value, (list, tuple)) else ()


class _KindStore:
    """Shared row plumbing for one `kind` of companion record."""

    kind: str = ""

    def _upsert(self, key: str, payload: dict) -> None:
        key = (key or "").strip()
        if not key:
            return
        try:
            with db_lock:
                session = SessionLocal()
                try:
                    row = (
                        session.query(CompanionMemoryRecord)
                        .filter(
                            CompanionMemoryRecord.kind == self.kind,
                            CompanionMemoryRecord.key == key,
                        )
                        .one_or_none()
                    )
                    body = json.dumps(payload, ensure_ascii=False)
                    if row is None:
                        session.add(
                            CompanionMemoryRecord(kind=self.kind, key=key, payload=body)
                        )
                    else:
                        row.payload = body
                        row.updated_at = timestamp_now()
                    session.commit()
                finally:
                    session.close()
        except Exception as error:  # noqa: BLE001 - a lost write must not fail the turn
            logger.warning(
                "CompanionMemory %s write failed (%s); dropped",
                self.kind, type(error).__name__,
            )

    def _rows(self, newest_first: bool = False, limit: int | None = None) -> list[dict]:
        try:
            with db_lock:
                session = SessionLocal()
                try:
                    query = session.query(CompanionMemoryRecord).filter(
                        CompanionMemoryRecord.kind == self.kind
                    )
                    query = query.order_by(
                        CompanionMemoryRecord.id.desc()
                        if newest_first
                        else CompanionMemoryRecord.id.asc()
                    )
                    if limit:
                        query = query.limit(int(limit))
                    return [json.loads(r.payload or "{}") for r in query.all()]
                finally:
                    session.close()
        except Exception as error:  # noqa: BLE001 - the reader is wrapped in _safe upstream
            logger.warning(
                "CompanionMemory %s read failed (%s); empty",
                self.kind, type(error).__name__,
            )
            return []


class SqliteFacts(_KindStore):
    kind = "fact"

    def remember(self, topic: str, value: str, note: str = "") -> None:
        key = (topic or "").strip().lower()
        if not key or not (value or "").strip():
            return
        self._upsert(key, {"topic": key, "value": value.strip(), "note": note.strip()})

    def all(self, limit: int | None = None) -> list[Fact]:
        return [Fact(**d) for d in self._rows(limit=limit)]

    def __len__(self) -> int:
        return len(self._rows())


class SqlitePreferences(_KindStore):
    kind = "preference"

    def remember(self, topic: str, value: str, note: str = "") -> None:
        key = (topic or "").strip().lower()
        if not key or not (value or "").strip():
            return
        self._upsert(key, {"topic": key, "value": value.strip(), "note": note.strip()})

    def all(self, limit: int | None = None) -> list[Preference]:
        return [Preference(**d) for d in self._rows(limit=limit)]

    def __len__(self) -> int:
        return len(self._rows())


class SqliteGoals(_KindStore):
    kind = "goal"

    def record(self, goal: Goal) -> None:
        title = (goal.title or "").strip()
        if not title:
            return
        self._upsert(
            title.lower(),
            {"title": goal.title, "detail": goal.detail, "priority": goal.priority},
        )

    def all(self) -> list[Goal]:
        return [Goal(**d) for d in self._rows()]

    def active(self, limit: int | None = None) -> list[Goal]:
        found = [g for g in self.all() if g.priority in ("now", "soon")]
        return found[:limit] if limit else found

    def __len__(self) -> int:
        return len(self._rows())


class SqliteProjects(_KindStore):
    kind = "project"

    def record(self, project: Project) -> None:
        name = (project.name or "").strip()
        if not name:
            return
        self._upsert(
            name.lower(),
            {
                "name": project.name,
                "description": project.description,
                "status": project.status,
                "tags": list(project.tags),
            },
        )

    def all(self) -> list[Project]:
        out: list[Project] = []
        for d in self._rows():
            out.append(
                Project(
                    name=d.get("name", ""),
                    description=d.get("description", ""),
                    status=d.get("status", "active"),
                    tags=_tuple(d.get("tags")),
                )
            )
        return out

    def active(self, limit: int | None = None) -> list[Project]:
        found = [p for p in self.all() if p.status == "active"]
        return found[:limit] if limit else found

    def __len__(self) -> int:
        return len(self._rows())


class SqliteCodingStyle(_KindStore):
    kind = "coding_style"

    def learn(self, style: CodingStyle) -> None:
        if not style.conventions:
            return
        self._upsert(
            (style.language or "").strip().lower() or "_",
            {"language": style.language, "conventions": list(style.conventions)},
        )

    def all(self) -> list[CodingStyle]:
        return [
            CodingStyle(
                language=d.get("language", ""),
                conventions=_tuple(d.get("conventions")),
            )
            for d in self._rows()
        ]

    def for_language(self, language: str) -> CodingStyle | None:
        target = (language or "").strip().lower()
        for style in self.all():
            if (style.language or "").strip().lower() == target:
                return style
        return None

    def __len__(self) -> int:
        return len(self._rows())


class SqliteHighlights(_KindStore):
    kind = "highlight"

    def __init__(self, cap: int = 100):
        self.cap = cap
        self._seq = 0

    def keep(self, highlight: Highlight) -> None:
        if not (highlight.summary or "").strip():
            return
        # Append-only: a monotonic, sortable key so recent() ordering is stable
        # and two highlights in the same second do not collide.
        self._seq += 1
        key = f"{highlight.at}#{self._seq:06d}"
        self._upsert(
            key,
            {"summary": highlight.summary, "reason": highlight.reason, "at": highlight.at},
        )

    def recent(self, limit: int = 5) -> list[Highlight]:
        if not limit:
            return []
        return [Highlight(**d) for d in self._rows(newest_first=True, limit=limit)]

    def __len__(self) -> int:
        return len(self._rows())


def build_sqlite_companion_stores() -> dict:
    """The six durable stores, ready to hand to CompanionMemory(**stores)."""
    return {
        "facts": SqliteFacts(),
        "preferences": SqlitePreferences(),
        "goals": SqliteGoals(),
        "projects": SqliteProjects(),
        "coding_style": SqliteCodingStyle(),
        "highlights": SqliteHighlights(),
    }


__all__ = [
    "SqliteFacts",
    "SqlitePreferences",
    "SqliteGoals",
    "SqliteProjects",
    "SqliteCodingStyle",
    "SqliteHighlights",
    "build_sqlite_companion_stores",
]
