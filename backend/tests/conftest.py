"""Shared pytest fixtures for unit and integration tests."""

from collections.abc import AsyncGenerator

import pytest

from app.db.postgres import close_postgres_engine
from app.db.qdrant import close_qdrant_client
from app.db.redis import close_redis_client


@pytest.fixture(autouse=True)
async def cleanup_db_clients() -> AsyncGenerator[None, None]:
    """Ensure async database clients are closed cleanly after each test."""
    yield
    await close_postgres_engine()
    await close_qdrant_client()
    await close_redis_client()

