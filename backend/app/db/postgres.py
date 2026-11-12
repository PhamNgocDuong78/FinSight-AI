"""Async SQLAlchemy 2.0 connection and session management for PostgreSQL 16."""

import time
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.health import ComponentHealth

logger = get_logger(__name__)


class Base(DeclarativeBase):
    """Declarative base class for all SQLAlchemy ORM models."""


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Return or initialize the global SQLAlchemy AsyncEngine."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.async_database_url,
            echo=False,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return or initialize the global async session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an AsyncSession per request."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        yield session


async def check_postgres_health() -> ComponentHealth:
    """Execute a lightweight SELECT 1 query to verify PostgreSQL connectivity."""
    start = time.perf_counter()
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        return ComponentHealth(status="ok", latency_ms=latency_ms)
    except (SQLAlchemyError, OSError) as exc:
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.error("postgres_health_check_failed", error=str(exc))
        return ComponentHealth(
            status="error",
            latency_ms=latency_ms,
            detail=str(exc),
        )


async def close_postgres_engine() -> None:
    """Dispose of the SQLAlchemy AsyncEngine connection pool."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None

