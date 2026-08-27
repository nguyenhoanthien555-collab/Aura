import pytest
from core.capabilities import registry, permissions
from core.capabilities.models import Capability
from core.capabilities.factory import register_core_capabilities

@pytest.fixture(autouse=True, scope="function")
def _auto_register_test_capabilities():
    # Make sure core capabilities are registered
    register_core_capabilities()

    test_caps = [
        "echo", "touch", "peek", "unlabelled", "strict", "moody", "hang",
        "verbose", "which_thread", "structural", "explode", "structured",
        "danger", "take_screenshot", "describe_screen", "dummy", "my_plugin_tool",
        "verify_raises", "verifying", "deliberate", "fails_first", "secret",
        "nameless"
    ]
    for c in test_caps:
        registry.register(Capability(capability_id=c, name=c, description="test", category="test"))
        permissions.grant(c)
    
    yield

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

import os

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


@pytest.fixture(autouse=True)
def never_the_real_settings(tmp_path, monkeypatch):
    """
    Neither the credential store nor the settings overlay is the user's.

    Phase 9 added two more files under `data/`: `credentials.enc` (the
    provider API keys) and `settings.json` (the Control Hub overlay).
    Both are process-wide singletons reached through a module global, and
    both are read on construction - so a test that builds a runtime would
    otherwise load the developer's real keys and, worse, a test that
    writes a setting would persist it into their live configuration.

    Per-test rather than per-session, and this is the important part:
    `core.config.load_config` consults the overlay, so a leftover
    override from one test would silently change what every later test
    sees as its configuration. A fresh empty pair per test keeps
    `load_config()` meaning `config.yaml` unless a test says otherwise.

    The singletons are reset both before and after. Before, because an
    earlier test may have built one against a path that no longer exists;
    after, so nothing outlives the temporary directory it points into.
    """

    from core import credentials, settings_store

    # Applying a credential sets the provider's environment variable, on
    # purpose - that is how a key reaches a provider that reads
    # `os.getenv` in its constructor. It also means a key set by one test
    # is visible to every later one, and `monkeypatch.delenv` cannot undo
    # a write it did not make. Snapshot and restore the whole set.
    saved_env = {
        var: os.environ.get(var)
        for var in credentials.PROVIDER_KEYS.values()
    }

    monkeypatch.setattr(
        credentials, "CREDENTIAL_PATH", tmp_path / "credentials.enc"
    )
    monkeypatch.setattr(
        settings_store, "SETTINGS_PATH", tmp_path / "settings.json"
    )

    # No inherited secret: a real AURA_SECRET_KEY in the developer's
    # environment would make the test store durable, and its ciphertext
    # would then be readable by the next run.
    monkeypatch.delenv(credentials.SECRET_ENV_VAR, raising=False)

    credentials._store = None
    settings_store._settings = None

    yield

    credentials._store = None
    settings_store._settings = None

    # Restore the provider environment variables a test may have set via
    # CredentialStore.set/delete, so the next test sees the environment
    # the same way the previous one did.
    for var, value in saved_env.items():
        if value is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = value




