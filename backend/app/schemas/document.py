"""Pydantic v2 request and response schemas for document upload and ingestion endpoints."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import (
    AccessLevel,
    DocumentLanguage,
    DocumentStatus,
    FiscalPeriod,
)


class DocumentUploadParams(BaseModel):
    """Validated metadata parameters accompanying a financial PDF upload."""

    ticker: str = Field(
        min_length=1,
        max_length=32,
        description="Stock ticker symbol (e.g., FPT, VNM, AAPL).",
    )
    company_name: str = Field(
        default="",
        max_length=255,
        description="Full company name (defaults to ticker if omitted).",
    )
    title: str = Field(
        default="",
        max_length=500,
        description="Human-readable report title.",
    )
    fiscal_year: int = Field(
        ge=1990,
        le=2100,
        description="Fiscal year of the report (e.g., 2024).",
    )
    period: FiscalPeriod = Field(
        default=FiscalPeriod.FY,
        description="Reporting period (FY, Q1, Q2, Q3, Q4).",
    )
    language: DocumentLanguage = Field(
        default=DocumentLanguage.VI,
        description="Primary language of the document (vi or en).",
    )
    access_level: AccessLevel = Field(
        default=AccessLevel.PUBLIC,
        description="RBAC access classification (public, internal_finance, executive_only).",
    )

    @field_validator("ticker", mode="before")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        """Trim and uppercase ticker symbols."""
        cleaned = str(value).strip().upper()
        if not cleaned:
            raise ValueError("ticker must not be empty")
        return cleaned


class DocumentIngestionResponse(BaseModel):
    """End-to-end summary returned by POST /api/v1/documents/upload."""

    model_config = ConfigDict(from_attributes=True)

    document_id: uuid.UUID = Field(description="UUID of the persisted Document in PostgreSQL.")
    title: str = Field(description="Document title.")
    file_name: str = Field(description="Stored PDF filename.")
    file_path: str = Field(description="Absolute or workspace-relative path to the stored PDF.")
    ticker: str = Field(description="Uppercase ticker symbol.")
    company_name: str = Field(description="Company name.")
    fiscal_year: int = Field(description="Fiscal year.")
    period: FiscalPeriod = Field(description="Reporting period.")
    language: DocumentLanguage = Field(description="Document language.")
    access_level: AccessLevel = Field(description="RBAC access level.")
    status: DocumentStatus = Field(description="Ingestion status (indexed when complete).")
    total_pages: int = Field(ge=0, description="Total number of pages parsed from the PDF.")
    total_tables: int = Field(ge=0, description="Total number of Markdown tables extracted.")
    parent_chunks_count: int = Field(
        ge=0,
        description="Number of Parent Chunks persisted in PostgreSQL `parent_chunks`.",
    )
    child_chunks_indexed: int = Field(
        ge=0,
        description="Number of Child Chunks indexed into Qdrant `finsight_child_chunks`.",
    )
    financial_metrics_count: int = Field(
        ge=0,
        description="Number of normalized financial metrics persisted in `financial_metrics`.",
    )
    qdrant_collection: str = Field(
        description="Name of the Qdrant collection storing the child chunk vectors.",
    )
    created_at: datetime = Field(description="Timestamp when the document record was created.")


class DocumentRead(BaseModel):
    """Metadata summary for a single stored financial document."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    file_path: str
    ticker: str
    company_name: str
    fiscal_year: int
    period: str
    language: str
    access_level: str
    status: str
    total_pages: int | None = None
    created_at: datetime


__all__ = [
    "DocumentIngestionResponse",
    "DocumentRead",
    "DocumentUploadParams",
]

