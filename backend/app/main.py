"""FastAPI application entrypoint, lifespan management, and request tracing middleware."""

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_v1_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import get_logger, setup_logging
from app.db.postgres import close_postgres_engine
from app.db.qdrant import close_qdrant_client
from app.db.redis import close_redis_client

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage startup and graceful shutdown of database connections."""
    settings = get_settings()
    setup_logging(debug=settings.app_debug)
    logger.info(
        "application_startup",
        app_name=settings.app_name,
        environment=settings.app_env,
    )
    yield
    logger.info("application_shutdown_initiated")
    await close_postgres_engine()
    await close_qdrant_client()
    await close_redis_client()
    logger.info("application_shutdown_complete")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    settings = get_settings()
    setup_logging(debug=settings.app_debug)

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Multimodal Financial Report Analysis System (Agentic RAG & Text-to-SQL)",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    register_exception_handlers(app)
    app.include_router(api_v1_router, prefix=settings.api_v1_prefix)

    return app


app = create_app()

