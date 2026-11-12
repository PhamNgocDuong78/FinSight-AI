"""Pydantic schemas for system and infrastructure health checks."""

from typing import Literal

from pydantic import BaseModel, Field


class ComponentHealth(BaseModel):
    """Health status and latency for an individual infrastructure service."""

    status: Literal["ok", "error"] = Field(
        description="Connection health status of the component."
    )
    latency_ms: float = Field(
        description="Round-trip check latency in milliseconds."
    )
    detail: str | None = Field(
        default=None,
        description="Optional diagnostic message or error reason.",
    )


class HealthResponse(BaseModel):
    """Aggregated health check response for FinSight AI backend."""

    status: Literal["ok", "degraded"] = Field(
        description="Overall system health status ('ok' when all dependencies are healthy)."
    )
    app_name: str = Field(description="Application name.")
    environment: str = Field(description="Current runtime environment.")
    services: dict[str, ComponentHealth] = Field(
        description="Health status map for postgres, qdrant, and redis."
    )

