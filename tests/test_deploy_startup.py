"""
The deploy has to boot before anything else in this repository matters.

This file exists because of one outage. Render's native Python runtime
follows its own default when nothing pins it, that default moved to
3.14, and `requirements-server.txt` still pinned `sqlalchemy==2.0.36`.
Python 3.14 implements PEP 604 by making `typing.Union` an *alias of*
`types.UnionType` rather than a distinct special form, so `Union` became
a class and `Union.__getitem__` became an unbound slot wrapper.
SQLAlchemy 2.0.36 built unions like this:

    return cast(Any, Union).__getitem__(types)

which on 3.13 was a bound call on a special-form instance and on 3.14 is
an unbound descriptor handed a tuple where an instance belongs:

    TypeError: descriptor '__getitem__' requires a 'typing.Union'
    object but received a 'tuple'

`de_optionalize_union_types` calls that helper for every optional column,
so the first `Mapped[str | None]` in the metadata took the process down at
import - `memory.models.UserModelEntry.last_confirmed_at`, which is why
the traceback named a class nobody had touched in months.

WHAT IS ACTUALLY BEING TESTED
-----------------------------
Not "does Python work". The annotation was correct and stayed correct; the
defect was a *pairing* between two pinned things, and a pairing is only
testable if both halves are read from the files that declare them. So
these tests read `requirements.txt`, `requirements-server.txt`,
`.python-version` and `Dockerfile` as data, and assert the combination is
one that boots.

That is deliberate, and it is why the interesting tests here pass on
3.11: an invariant that only fails on the interpreter that broke is an
invariant CI cannot check. `TestOptionalColumnsMap` covers the mechanism
on whatever interpreter is running; `TestDeclaredPins` covers the
combination on every interpreter.
"""

import re
import sys
from pathlib import Path

import pytest
import sqlalchemy
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from memory.models import (
    Base,
    EpisodicMemory,
    Message,
    UserFact,
    UserModelEntry,
)


ROOT = Path(__file__).resolve().parent.parent


# The lowest SQLAlchemy release that can map an optional column on Python
# 3.14. 2.0.36 cannot; 2.0.51 is the version this was verified against on
# 3.14.6, and is the pin `requirements-server.txt` carries.
#
# Deliberately not "whatever the changelog says fixed it". The changelog
# spreads 3.14 compatibility across several releases and never names this
# call, so the honest floor is the lowest version actually observed to map
# `memory/models.py` on a 3.14 interpreter.
MIN_SQLALCHEMY_FOR_PY314 = (2, 0, 51)


def _version_tuple(raw: str) -> tuple[int, ...]:
    """`"2.0.51"` -> `(2, 0, 51)`, ignoring any suffix like `.post1`."""

    parts = []
    for chunk in raw.strip().split("."):
        match = re.match(r"^(\d+)", chunk)
        if not match:
            break
        parts.append(int(match.group(1)))
    return tuple(parts)


def _requirement(path: Path, package: str) -> str:
    """
    The version specifier declared for `package`, comments stripped.

    Written against the real files rather than a parsed dependency graph:
    the point is to catch a human editing one line and not the other, and
    that is a text-level mistake.
    """

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        name = re.split(r"[<>=!~\[]", line, maxsplit=1)[0].strip().lower()
        if name == package:
            return line
    raise AssertionError(f"{path.name} declares no requirement for {package}")


# ----------------------------------------------------------------------
# The mechanism
# ----------------------------------------------------------------------

class TestOptionalColumnsMap:
    """
    `Mapped[X | None]` has to survive SQLAlchemy's annotation scan.

    These run on whatever interpreter is present. On 3.14 with a
    SQLAlchemy older than the floor above they fail at import, which is
    the outage reproduced.
    """

    def test_the_model_that_broke_maps_its_optional_columns(self):
        table = UserModelEntry.__table__

        # All three were `Mapped[str | None]`; all three must be nullable
        # and none may have collapsed to an untyped column.
        for name in ("last_confirmed_at", "valid_from", "valid_until"):
            column = table.columns[name]
            assert column.nullable is True, f"{name} lost its nullability"
            assert isinstance(column.type, sqlalchemy.String), (
                f"{name} did not resolve to String, got {column.type!r}"
            )

    def test_required_columns_stay_required(self):
        """
        The complement, and the reason this is not just an import test.

        A fix that made every column nullable would satisfy the test above
        while quietly dropping the schema's constraints.
        """

        table = UserModelEntry.__table__

        for name in ("key", "value", "category", "status"):
            assert table.columns[name].nullable is False, (
                f"{name} became nullable"
            )

    def test_every_model_maps(self):
        for model in (Message, UserFact, EpisodicMemory, UserModelEntry):
            assert model.__table__.columns, f"{model.__name__} mapped no columns"

    def test_the_whole_schema_can_be_created(self):
        """
        Mapping is not the same as emitting DDL, and the deploy needs both.
        """

        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)

        tables = set(inspect(engine).get_table_names())

        assert {
            "messages",
            "user_facts",
            "episodic_memories",
            "user_model",
        } <= tables

    def test_a_fresh_optional_annotation_still_resolves(self):
        """
        The scan runs at class-creation time, so a model defined *now*
        exercises it now - independently of `memory/models.py` having been
        imported before this test ran.
        """

        class Scratch(DeclarativeBase):
            pass

        class Row(Scratch):
            __tablename__ = "scratch_optional"

            id: Mapped[int] = mapped_column(primary_key=True)
            required: Mapped[str] = mapped_column()
            optional: Mapped[str | None] = mapped_column(default=None)

        assert Row.__table__.columns["optional"].nullable is True
        assert Row.__table__.columns["required"].nullable is False

    def test_the_helper_from_the_traceback_handles_a_pep604_union(self):
        """
        `de_optionalize_union_types` by name.

        Pinned directly so a recurrence reads as "this function broke"
        rather than as an unexplained ImportError three layers up. The
        private import is the point: this is the frame the outage's
        traceback ended in.
        """

        from sqlalchemy.util.typing import de_optionalize_union_types

        assert de_optionalize_union_types(str | None) is str

    def test_installed_sqlalchemy_is_new_enough_for_this_interpreter(self):
        """
        The runtime half of the pin invariant.

        A developer on 3.14 with a stale virtualenv gets told which of the
        two to move, instead of an import error in an ORM they did not
        edit.
        """

        if sys.version_info < (3, 14):
            pytest.skip("the 2.0.51 floor only binds on Python 3.14+")

        installed = _version_tuple(sqlalchemy.__version__)

        assert installed >= MIN_SQLALCHEMY_FOR_PY314, (
            f"SQLAlchemy {sqlalchemy.__version__} cannot map an optional "
            f"column on Python {sys.version_info.major}."
            f"{sys.version_info.minor}; need "
            f"{'.'.join(str(part) for part in MIN_SQLALCHEMY_FOR_PY314)}+"
        )


# ----------------------------------------------------------------------
# The combination
# ----------------------------------------------------------------------

class TestDeclaredPins:
    """
    What the deploy will install, read off the files that declare it.

    Every test here is interpreter-independent, which is the whole reason
    they exist: the outage happened on 3.14 and CI runs on 3.11, so an
    assertion that only fires on 3.14 would never have run.
    """

    def test_the_server_pin_supports_the_newest_supported_python(self):
        declared = _requirement(ROOT / "requirements-server.txt", "sqlalchemy")

        assert declared.startswith("sqlalchemy=="), (
            "the deployed set is pinned, not floored; keep it that way "
            f"(found {declared!r})"
        )

        pinned = _version_tuple(declared.split("==", 1)[1])

        assert pinned >= MIN_SQLALCHEMY_FOR_PY314, (
            f"requirements-server.txt pins SQLAlchemy {pinned}, which cannot "
            "map a Mapped[str | None] column on Python 3.14. This is the "
            "exact pairing that stopped the Render deploy booting."
        )

    def test_the_development_floor_admits_no_broken_version(self):
        declared = _requirement(ROOT / "requirements.txt", "sqlalchemy")

        assert ">=" in declared, (
            f"requirements.txt should carry a floor, found {declared!r}"
        )

        floor = _version_tuple(declared.split(">=", 1)[1])

        assert floor >= MIN_SQLALCHEMY_FOR_PY314, (
            "a bare or low `sqlalchemy` line is what let a 3.14 interpreter "
            "resolve a release that predates 3.14"
        )

    def test_the_floor_and_the_pin_agree(self):
        """
        Two files, one dependency. The pinned deploy must satisfy the
        floor the source tree claims, or the thing that is tested and the
        thing that is shipped are different programs.
        """

        floor = _version_tuple(
            _requirement(ROOT / "requirements.txt", "sqlalchemy").split(">=", 1)[1]
        )
        pinned = _version_tuple(
            _requirement(ROOT / "requirements-server.txt", "sqlalchemy").split("==", 1)[1]
        )

        assert pinned >= floor, (
            f"requirements-server.txt pins {pinned} but requirements.txt "
            f"requires >= {floor}"
        )

    def test_the_interpreter_is_pinned_at_all(self):
        """
        The root cause, stated as an invariant.

        Pinned dependencies under a floating interpreter is not a
        reproducible deploy. Render reads this file; deleting it hands the
        choice back to the platform's default, which is what moved.
        """

        pin = ROOT / ".python-version"

        assert pin.exists(), (
            ".python-version is missing - Render will pick its own default "
            "Python again, which is how the 3.14 outage started"
        )

        content = pin.read_text(encoding="utf-8").strip()

        assert re.fullmatch(r"\d+\.\d+(\.\d+)?", content), (
            "pyenv and Render read this whole line as a version, so it must "
            f"hold nothing but one, found {content!r}"
        )

    def test_the_pinned_interpreter_matches_the_container(self):
        """
        Two deploy paths, one Python.

        `Dockerfile` and Render's native runtime are both production. When
        they disagree, one of them is running a combination nobody tested -
        and it is the one that is not on your laptop.
        """

        pinned = _version_tuple(
            (ROOT / ".python-version").read_text(encoding="utf-8").strip()
        )

        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

        images = re.findall(r"^FROM python:(\d+\.\d+)", dockerfile, re.MULTILINE)

        assert images, "Dockerfile no longer pins a python base image"

        for image in images:
            assert _version_tuple(image) == pinned[:2], (
                f"Dockerfile builds on python:{image} but .python-version "
                f"pins {'.'.join(str(part) for part in pinned)}"
            )

    def test_the_container_copies_site_packages_from_the_pinned_version(self):
        """
        The Dockerfile hardcodes the interpreter's site-packages path when
        it copies dependencies between stages. A base-image bump that
        missed that line would produce an image with no dependencies at
        all, and it would build cleanly.
        """

        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

        pinned = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
        series = ".".join(str(part) for part in _version_tuple(pinned)[:2])

        for path in re.findall(r"/usr/local/lib/python(\d+\.\d+)/", dockerfile):
            assert path == series, (
                f"Dockerfile copies site-packages from python{path} while "
                f".python-version pins {series}"
            )


# ----------------------------------------------------------------------
# The entry point
# ----------------------------------------------------------------------

class TestStartCommand:
    """
    `python -m server.main` is Render's start command. Importing it is
    where the outage surfaced, so importing it is what gets tested.
    """

    def test_the_start_module_imports(self):
        import server.main

        assert server.main.app is not None

    def test_importing_it_pulls_in_the_orm(self):
        """
        Guards against the regression being 'fixed' by making the models
        lazy. The crash was at import of the ORM *through* the server; if
        that edge disappears, this file stops protecting the deploy and
        nothing would say so.
        """

        import server.main  # noqa: F401

        assert "memory.models" in sys.modules
