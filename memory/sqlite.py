"""
SQLite database connection.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.paths import DATA_DIR
from memory.models import Base

DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite:///{(DATA_DIR / 'memory.db').as_posix()}"

engine = create_engine(DATABASE_URL, echo=False)

SessionLocal = sessionmaker(bind=engine)


def init_database():
    Base.metadata.create_all(engine)
