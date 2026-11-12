"""SQLAlchemy ORM models for financial documents and parent chunks."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.postgres import Base
from app.models.enums import (
    AccessLevel,
    ChunkType,
    DocumentLanguage,
    DocumentStatus,
    FiscalPeriod,
)


class Document(Base):
    """Metadata and ingestion status for an original financial PDF report."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    ticker: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    period: Mapped[str] = mapped_column(
        String(16),
        default=FiscalPeriod.FY.value,
        nullable=False,
    )
    language: Mapped[str] = mapped_column(
        String(8),
        default=DocumentLanguage.VI.value,
        nullable=False,
    )
    access_level: Mapped[str] = mapped_column(
        String(32),
        default=AccessLevel.PUBLIC.value,
        index=True,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default=DocumentStatus.PENDING.value,
        index=True,
        nullable=False,
    )
    total_pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    parent_chunks: Mapped[list["ParentChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )


class ParentChunk(Base):
    """Full-context text section or unsplit Markdown table linked from Qdrant child chunks."""

    __tablename__ = "parent_chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_type: Mapped[str] = mapped_column(
        String(32),
        default=ChunkType.TEXT.value,
        nullable=False,
    )
    content_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    section_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    document: Mapped["Document"] = relationship(back_populates="parent_chunks")
