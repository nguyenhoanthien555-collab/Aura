"""
Suite-wide safety net: the tests never touch `data/memory.db`.

Most suites here already inject their own in-memory session and say so
in a docstring. The ones that don't are the ones that build a whole
runtime - `ServerRuntime()` with no `memory=` argument, for instance -
and those reach `memory/sqlite.py`'s module-level engine, which points
at the user's real database.

Before Phase 8 that was close to harmless: the stores called
`init_database()`, which creates two empty tables, and machine-turn
isolation meant a test's chat turns were never saved. Phase 8 changed
the arithmetic. Building the pipeline seeds the user model, so a test
run wrote 46 profile rows into the live database - rows that would then
be read back on the user's next real conversation and treated as
confirmed facts about them.

The fix belongs here rather than in each test, because the failure mode
is a test that *forgot* to isolate itself; anything opt-in leaves the
next such test to rediscover this.

Redirection works by reconfiguring the sessionmaker in place rather than
rebinding names. Every store does `from memory.sqlite import
SessionLocal` at import time, so by the time this fixture runs those
modules already hold their own reference to that object - rebinding
`memory.sqlite.SessionLocal` would leave them all pointed at the real
database. `configure()` mutates the object they are holding.

`memory.sqlite.engine` is rebound as well, for `init_database` and
`init_pipeline_tables`: both read it as a module global at call time, so
they follow the new value.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


@pytest.fixture(scope="session", autouse=True)
def never_the_real_database():
    """
    Point the shared engine at a throwaway in-memory database.

    In-memory rather than a temporary file on purpose: it leaves nothing
    to clean up, and on Windows a SQLite file still held open by a
    pooled connection cannot be deleted, which turns teardown into a
    PermissionError.

    `StaticPool` is what makes an in-memory database usable here. The
    default pool opens a connection per checkout and every fresh
    connection to `:memory:` is a brand new empty database; StaticPool
    keeps one connection, so a table created by one store is visible to
    the next. `check_same_thread=False` matches production, and for the
    same reason - the pooled connection is legitimately used from more
    than one thread, and `db_lock` is what keeps that correct.
    """

    from memory import sqlite

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    original = sqlite.engine

    sqlite.engine = engine
    sqlite.SessionLocal.configure(bind=engine)

    yield

    sqlite.SessionLocal.configure(bind=original)
    sqlite.engine = original
    engine.dispose()
