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
"""

from threading import RLock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.paths import DATA_DIR
from memory.models import Base

DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite:///{(DATA_DIR / 'memory.db').as_posix()}"

engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)

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
    Base.metadata.create_all(engine)
