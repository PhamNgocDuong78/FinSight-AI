"""Async Qdrant Vector Database client and health check."""

import time

import httpx
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.health import ComponentHealth

logger = get_logger(__name__)

_qdrant_client: AsyncQdrantClient | None = None


def get_qdrant_client() -> AsyncQdrantClient:
    """Return or initialize the global AsyncQdrantClient instance."""
    global _qdrant_client
    if _qdrant_client is None:
        settings = get_settings()
        api_key = settings.qdrant_api_key or None
        _qdrant_client = AsyncQdrantClient(
            url=settings.resolved_qdrant_url,
            api_key=api_key,
            timeout=10,
            check_compatibility=False,
        )
    return _qdrant_client


async def check_qdrant_health() -> ComponentHealth:
    """Verify Qdrant connectivity by listing collections."""
    start = time.perf_counter()
    try:
        client = get_qdrant_client()
        await client.get_collections()
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        return ComponentHealth(status="ok", latency_ms=latency_ms)
    except (
        UnexpectedResponse,
        ResponseHandlingException,
        httpx.HTTPError,
        OSError,
    ) as exc:
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.error("qdrant_health_check_failed", error=str(exc))
        return ComponentHealth(
            status="error",
            latency_ms=latency_ms,
            detail=str(exc),
        )


async def close_qdrant_client() -> None:
    """Close the global AsyncQdrantClient instance."""
    global _qdrant_client
    if _qdrant_client is not None:
        await _qdrant_client.close()
        _qdrant_client = None

