"""Custom exceptions and FastAPI exception handlers."""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.core.logging import get_logger

logger = get_logger(__name__)


class FinSightError(Exception):
    """Base exception for FinSight AI domain errors."""

    def __init__(
        self,
        message: str,
        status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
        error_code: str = "INTERNAL_ERROR",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code


def register_exception_handlers(app: FastAPI) -> None:
    """Register global exception handlers on the FastAPI app."""

    @app.exception_handler(FinSightError)
    async def finsight_error_handler(
        request: Request, exc: FinSightError
    ) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        logger.error(
            "domain_error",
            error_code=exc.error_code,
            message=exc.message,
            path=str(request.url.path),
            request_id=request_id,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": exc.error_code,
                "message": exc.message,
                "request_id": request_id,
            },
        )

