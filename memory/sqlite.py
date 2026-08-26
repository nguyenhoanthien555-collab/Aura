"""
SQLite database connection.

One engine, one database file, shared by every store.

Threading
---------
Aura's stores each hold a single long-lived Session rather than opening
one per call, and the server reaches them from more than one thread: a
request arrives on a worker thread, and a streaming reply is pumped from
a threadpool thread on top of that.

Two things have to be true for that to be safe, and both are set here
rather than left to a default:

  * `check_same_thread=False`. QueuePool hands a pooled connection to
    whichever thread asks for it, so a connection opened on one thread
    is legitimately used from another. pysqlite's guard against that
    knows nothing about the pool and raises ProgrammingError. The guard
    is a driver-level assumption, not SQLite's own - SQLite is built in
    serialised threading mode by default.

  * `db_lock`, below. Turning the guard off only makes cross-thread use
    *possible*; it does not make a SQLAlchemy Session thread-safe. The
    lock is what actually makes it correct, so the two belong together
    and neither should be removed without the other.

Schema changes
--------------
`init_database` creates only the two tables that existed before Phase 8
(`messages`, `user_facts`). The Phase 8 tables (`episodic_memories`,
`user_model`) are created by the composition root when it builds the
pipeline, guarded by the pipeline being enabled - and a pre-existing
database that predates them gets them added by the pipeline's own
`create_all`, never by `init_database`.

The reason for the split is a deployment without a migration system
(docs/DEPLOYMENT.md is explicit that there are none yet). A `create_all`
is additive and idempotent: it creates missing tables and touches no
existing rows, so running it once at upgrade time against an old
database is safe. Running it unconditionally at startup would be the
same thing - but keeping it in the composition root keeps the guard
("the pipeline is off" -> "the pipeline's tables are not created") in
the same file as the decision, and makes a server run that disables
pipeline memory leave the database exactly as it found it.
"""

from threading import RLock

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from core.paths import DATA_DIR
from memory.models import Base, EpisodicMemory, Message, UserFact, UserModelEntry

DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite:///{(DATA_DIR / 'memory.db').as_posix()}"

engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    """
    Enable Write-Ahead Logging (WAL) and optimal SQLite concurrency pragmas.
    Reduces commit latency by ~99% and eliminates reader-writer lock contention.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

# `expire_on_commit=False` is part of the locking above, not a
# convenience. By default a commit expires every loaded object, so
# reading `fact.value` after the store returned would quietly emit a
# fresh SELECT - on a session another thread may by then be using, and
# outside the lock that was supposed to cover it. Keeping committed
# objects usable means the lock covers every statement it looks like it
# covers.


# Serialises every store that talks to this database. Re-entrant, because
# a store method that already holds it may call another one.
#
# Coarse on purpose: SQLite serialises writes anyway, and the contention
# this creates is far cheaper than the corrupted identity map a Session
# used from two threads at once produces.
db_lock = RLock()


def init_database():
    """
    Create the transcript and profile tables.

    Scoped to the two pre-Phase-8 tables. See the module docstring: the
    Phase 8 tables belong to the pipeline, and are created where the
    pipeline is built.
    """
    from sqlalchemy import text

    Base.metadata.create_all(
        engine,
        tables=[Message.__table__, UserFact.__table__],
    )

    with engine.connect() as conn:
        columns = [row[1] for row in conn.execute(text("PRAGMA table_info(messages)"))]
        if "session_id" not in columns:
            conn.execute(text("ALTER TABLE messages ADD COLUMN session_id VARCHAR(128) DEFAULT 'default'"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_messages_session_id ON messages (session_id)"))
            conn.commit()




def init_pipeline_tables(bind=None):
    """
    Create the Memory 2.0 tables, if they are missing.

    Additive and idempotent - `create_all` issues CREATE TABLE IF NOT
    EXISTS and leaves existing rows alone, so this is safe to call on
    every start and safe on a database written before Phase 8.
    """

    Base.metadata.create_all(
        bind or engine,
        tables=[EpisodicMemory.__table__, UserModelEntry.__table__],
    )
