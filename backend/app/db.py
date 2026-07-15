from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import AppConfig


_engine: Engine | None = None
_sessions: sessionmaker[Session] | None = None


def configure_database(database_url: str) -> None:
    global _engine, _sessions
    AppConfig(database_url=database_url)
    if _engine is not None:
        _engine.dispose()
    _engine = create_engine(database_url, pool_pre_ping=True)
    _sessions = sessionmaker(bind=_engine, expire_on_commit=False)


def session_factory() -> Session:
    if _sessions is None:
        raise RuntimeError("database is not configured")
    return _sessions()


def dispose_database() -> None:
    global _engine, _sessions
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _sessions = None
