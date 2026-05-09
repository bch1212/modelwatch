"""Async SQLAlchemy engine + session factory (lazy init)."""

from typing import Optional

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


_engine: Optional[AsyncEngine] = None
_async_session: Optional[async_sessionmaker] = None


def _normalize_async_url(url: str) -> str:
    """Force the asyncpg driver for Postgres URLs.

    Railway sets DATABASE_URL to `postgresql://...` which SQLAlchemy maps to
    psycopg2 (a sync driver). The app uses async SQLAlchemy + asyncpg, so we
    rewrite to `postgresql+asyncpg://`. SQLite URLs already include
    `+aiosqlite` in the test config, so this is a no-op for them.
    """
    if url.startswith("postgresql+"):
        return url
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://"):]
    return url


def _get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        from app.config import get_settings
        settings = get_settings()
        url = _normalize_async_url(settings.database_url)
        kwargs = {}
        # pool_size/max_overflow not supported on SQLite
        if "sqlite" not in url:
            kwargs["pool_size"] = 10
            kwargs["max_overflow"] = 20
        _engine = create_async_engine(
            url,
            echo=settings.debug,
            **kwargs,
        )
    return _engine


def _get_session_factory() -> async_sessionmaker:
    global _async_session
    if _async_session is None:
        _async_session = async_sessionmaker(
            _get_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _async_session


# Public accessors
@property
def engine_prop():
    return _get_engine()


def get_engine() -> AsyncEngine:
    return _get_engine()


def get_session_factory() -> async_sessionmaker:
    return _get_session_factory()


# Backwards-compatible module-level names (used by main.py, scheduler.py)
class _EngineProxy:
    """Lazy proxy so `from app.models.database import engine` works."""
    def __getattr__(self, name):
        return getattr(_get_engine(), name)

engine = _EngineProxy()
async_session = property(lambda self: _get_session_factory())


async def get_db():
    """FastAPI dependency — yields an async session."""
    factory = _get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
