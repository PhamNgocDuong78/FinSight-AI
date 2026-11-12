"""Async Redis client and health check for caching and background queues."""

import time

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.health import ComponentHealth

logger = get_logger(__name__)

_redis_client: aioredis.Redis | None = None


def get_redis_client() -> aioredis.Redis:
    """Return or initialize the global async Redis client."""
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        _redis_client = aioredis.from_url(
            settings.resolved_redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis_client


async def check_redis_health() -> ComponentHealth:
    """Verify Redis connectivity by sending a PING command."""
    start = time.perf_counter()
    try:
        client = get_redis_client()
        pong = await client.ping()
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        if pong:
            return ComponentHealth(status="ok", latency_ms=latency_ms)
        return ComponentHealth(
            status="error",
            latency_ms=latency_ms,
            detail="Redis PING returned False",
        )
    except (RedisError, OSError) as exc:
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.error("redis_health_check_failed", error=str(exc))
        return ComponentHealth(
            status="error",
            latency_ms=latency_ms,
            detail=str(exc),
        )


async def close_redis_client() -> None:
    """Close the global async Redis client connection pool."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None

