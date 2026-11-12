"""FastAPI endpoints for financial PDF upload, ingestion, and document metadata queries."""

import re
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import FinSightError
from app.core.logging import get_logger
from app.db.postgres import get_db_session
from app.models.document import Document
from app.models.enums import AccessLevel, DocumentLanguage, FiscalPeriod
from app.schemas.document import (
    DocumentIngestionResponse,
    DocumentRead,
    DocumentUploadParams,
)
from app.services.ingestion.pipeline import DocumentIngestionPipeline

logger = get_logger(__name__)

router = APIRouter(prefix="/documents", tags=["Documents"])

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def get_ingestion_pipeline() -> DocumentIngestionPipeline:
    """FastAPI dependency providing the DocumentIngestionPipeline service."""
    return DocumentIngestionPipeline()


DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]
IngestionPipelineDep = Annotated[DocumentIngestionPipeline, Depends(get_ingestion_pipeline)]


def _sanitize_pdf_filename(raw_filename: str | None, ticker: str, fiscal_year: int) -> str:
    """Build a safe, deterministic PDF filename preventing path traversal."""
    base = Path(raw_filename or f"{ticker}_{fiscal_year}.pdf").name
    cleaned = _SAFE_FILENAME_RE.sub("_", base).strip("._")
    if not cleaned:
        cleaned = f"{ticker}_{fiscal_year}.pdf"
    if not cleaned.lower().endswith(".pdf"):
        cleaned = f"{cleaned}.pdf"
    return cleaned


@router.post(
    "/upload",
    response_model=DocumentIngestionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a financial PDF report and run full ETL + Dual Vector Indexing",
)
async def upload_document(
    file: Annotated[UploadFile, File(description="Financial report PDF file.")],
    ticker: Annotated[str, Form(description="Stock ticker symbol (e.g., FPT, VNM, AAPL).")],
    fiscal_year: Annotated[
        int, Form(ge=1990, le=2100, description="Fiscal year (e.g., 2024).")
    ],
    session: DbSessionDep,
    pipeline: IngestionPipelineDep,
    company_name: Annotated[str, Form(description="Full company name.")] = "",
    title: Annotated[str, Form(description="Document title.")] = "",
    period: Annotated[
        FiscalPeriod, Form(description="Reporting period.")
    ] = FiscalPeriod.FY,
    language: Annotated[
        DocumentLanguage,
        Form(description="Primary language of the report (vi or en)."),
    ] = DocumentLanguage.VI,
    access_level: Annotated[
        AccessLevel,
        Form(description="RBAC access classification."),
    ] = AccessLevel.PUBLIC,
) -> DocumentIngestionResponse:
    """Receive a PDF file, persist it to storage, and run the end-to-end ingestion pipeline."""
    try:
        params = DocumentUploadParams(
            ticker=ticker,
            company_name=company_name,
            title=title,
            fiscal_year=fiscal_year,
            period=period,
            language=language,
            access_level=access_level,
        )
    except ValidationError as exc:
        raise FinSightError(
            message=f"Invalid upload metadata: {exc}",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            error_code="INVALID_UPLOAD_METADATA",
        ) from exc

    raw_bytes = await file.read()
    if not raw_bytes or not raw_bytes.lstrip().startswith(b"%PDF-"):
        raise FinSightError(
            message="Uploaded file must be a non-empty valid PDF document starting with %PDF-.",
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="INVALID_PDF_FILE",
        )

    settings = get_settings()
    target_dir = settings.raw_pdf_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    doc_uuid = uuid.uuid4()
    safe_name = _sanitize_pdf_filename(file.filename, params.ticker, params.fiscal_year)
    stored_filename = (
        f"{params.ticker}_{params.fiscal_year}_{params.period.value}_{doc_uuid.hex[:8]}_{safe_name}"
    )
    stored_path = (target_dir / stored_filename).resolve()
    stored_path.write_bytes(raw_bytes)

    resolved_title = (
        params.title.strip()
        or f"Báo cáo tài chính {params.ticker} {params.period.value} {params.fiscal_year}"
    )

    return await pipeline.ingest_pdf(
        session,
        pdf_path=stored_path,
        ticker=params.ticker,
        fiscal_year=params.fiscal_year,
        title=resolved_title,
        company_name=params.company_name,
        period=params.period,
        language=params.language,
        access_level=params.access_level,
        document_id=doc_uuid,
    )


@router.get(
    "",
    response_model=list[DocumentRead],
    summary="List all ingested financial documents",
)
async def list_documents(
    session: DbSessionDep,
) -> list[DocumentRead]:
    """Return all financial documents ordered by creation timestamp descending."""
    stmt = select(Document).order_by(Document.created_at.desc())
    rows = (await session.execute(stmt)).scalars().all()
    return [DocumentRead.model_validate(row) for row in rows]


@router.get(
    "/{document_id}",
    response_model=DocumentRead,
    summary="Get metadata and ingestion status for a specific document",
)
async def get_document(
    document_id: uuid.UUID,
    session: DbSessionDep,
) -> DocumentRead:
    """Retrieve a single Document record by its UUID."""
    doc = await session.get(Document, document_id)
    if doc is None:
        raise FinSightError(
            message=f"Document '{document_id}' not found.",
            status_code=status.HTTP_404_NOT_FOUND,
            error_code="DOCUMENT_NOT_FOUND",
        )
    return DocumentRead.model_validate(doc)
