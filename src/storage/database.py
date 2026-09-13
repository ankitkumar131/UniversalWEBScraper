"""Async database engine + session management (SQLite by default, PostgreSQL optional)."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from src.storage.models import Base

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _tune_sqlite(engine: AsyncEngine) -> None:
    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _record):  # pragma: no cover
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def init_engine(database_url: str) -> AsyncEngine:
    global _engine, _session_factory
    if _engine is not None:
        return _engine
    if database_url.startswith("sqlite"):
        Path(database_url.split("///")[-1]).parent.mkdir(parents=True, exist_ok=True)
    _engine = create_async_engine(database_url, pool_pre_ping=True)
    if database_url.startswith("sqlite"):
        _tune_sqlite(_engine)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


async def create_tables() -> None:
    assert _engine is not None, "call init_engine() first"
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


def session_factory() -> async_sessionmaker[AsyncSession]:
    assert _session_factory is not None, "call init_engine() first"
    return _session_factory
