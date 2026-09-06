from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import get_settings

Base = declarative_base()


def _build_engine():
    database_url = get_settings().database_url
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=connect_args, future=True)

    if database_url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _record):
            cursor = dbapi_conn.cursor()
            # WAL lets readers and one writer proceed concurrently; busy_timeout
            # makes concurrent writers block-and-retry instead of raising
            # "database is locked" when several worker processes write at once.
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=10000")
            cursor.close()

    return engine


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db():
    from app.db import models  # noqa: F401  (register models on Base before create_all)
    Base.metadata.create_all(bind=engine)  # type: ignore[attr-defined]
