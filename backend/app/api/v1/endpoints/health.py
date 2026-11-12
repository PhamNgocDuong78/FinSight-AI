"""System health check endpoint verifying PostgreSQL, Qdrant, and Redis."""

import asyncio

from fastapi import APIRouter, Response, status

from app.core.config import get_settings
from app.db.postgres import check_postgres_health
from app.db.qdrant import check_qdrant_health
from app.db.redis import check_redis_health
from app.schemas.health import HealthResponse

router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Check connectivity to PostgreSQL, Qdrant, and Redis",
)
async def health_check(response: Response) -> HealthResponse:
    """Run concurrent health checks against all core infrastructure services."""
    settings = get_settings()
    pg_health, qdrant_health, redis_health = await asyncio.gather(
        check_postgres_health(),
        check_qdrant_health(),
        check_redis_health(),
    )

    services = {
        "postgres": pg_health,
        "qdrant": qdrant_health,
        "redis": redis_health,
    }

    all_ok = all(item.status == "ok" for item in services.values())
    overall_status = "ok" if all_ok else "degraded"

    if not all_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(
        status=overall_status,
        app_name=settings.app_name,
        environment=settings.app_env,
        services=services,
    )

