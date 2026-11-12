"""End-to-end multimodal document ingestion pipeline (PDF -> PostgreSQL + Qdrant Dual Index)."""

import uuid
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import FinSightError
from app.core.logging import get_logger
from app.models.document import Document, ParentChunk
from app.models.enums import (
    AccessLevel,
    DocumentLanguage,
    DocumentStatus,
    FiscalPeriod,
)
from app.schemas.document import DocumentIngestionResponse
from app.services.ingestion.base_parser import BaseDocumentParser
from app.services.ingestion.chunker import BaseDocumentChunker, ParentChildChunker
from app.services.ingestion.financial_etl import FinancialETLService
from app.services.ingestion.pdf_parser import LayoutAwarePDFParser, export_parsed_document
from app.services.ingestion.vector_indexer import BaseVectorIndexer, QdrantChildChunkIndexer

logger = get_logger(__name__)


class DocumentIngestionPipeline:
    """Orchestrates PDF parsing, Parent-Child chunking, Financial ETL, and Qdrant Dual Indexing."""

    def __init__(
        self,
        parser: BaseDocumentParser | None = None,
        chunker: BaseDocumentChunker | None = None,
        etl_service: FinancialETLService | None = None,
        vector_indexer: BaseVectorIndexer | None = None,
        export_markdown: bool = True,
    ) -> None:
        self.parser = parser or LayoutAwarePDFParser()
        self.chunker = chunker or ParentChildChunker()
        self.etl_service = etl_service or FinancialETLService()
        self.vector_indexer = vector_indexer or QdrantChildChunkIndexer()
        self.export_markdown = export_markdown

    async def ingest_pdf(
        self,
        session: AsyncSession,
        *,
        pdf_path: Path | str,
        ticker: str,
        fiscal_year: int,
        title: str = "",
        company_name: str = "",
        period: FiscalPeriod = FiscalPeriod.FY,
        language: DocumentLanguage = DocumentLanguage.VI,
        access_level: AccessLevel = AccessLevel.PUBLIC,
        document_id: uuid.UUID | None = None,
    ) -> DocumentIngestionResponse:
        """Execute full ingestion pipeline from PDF file to PostgreSQL and Qdrant."""
        settings = get_settings()
        resolved_path = Path(pdf_path).resolve()
        norm_ticker = ticker.strip().upper()
        resolved_company = company_name.strip() or norm_ticker
        resolved_title = (
            title.strip()
            or f"Báo cáo tài chính {norm_ticker} {period.value} {fiscal_year}"
        )

        doc_uuid = document_id or uuid.uuid4()
        doc = await session.get(Document, doc_uuid)
        if doc is None:
            doc = Document(
                id=doc_uuid,
                title=resolved_title,
                file_path=str(resolved_path),
                ticker=norm_ticker,
                company_name=resolved_company,
                fiscal_year=fiscal_year,
                period=period.value,
                language=language.value,
                access_level=access_level.value,
                status=DocumentStatus.PROCESSING.value,
            )
            session.add(doc)
        else:
            doc.title = resolved_title
            doc.file_path = str(resolved_path)
            doc.ticker = norm_ticker
            doc.company_name = resolved_company
            doc.fiscal_year = fiscal_year
            doc.period = period.value
            doc.language = language.value
            doc.access_level = access_level.value
            doc.status = DocumentStatus.PROCESSING.value

        await session.commit()
        await session.refresh(doc)

        logger.info(
            "document_ingestion_started",
            document_id=str(doc.id),
            ticker=norm_ticker,
            fiscal_year=fiscal_year,
            file_name=resolved_path.name,
        )

        try:
            # 1. Layout-aware PDF Parsing
            parsed_doc = await self.parser.aparse(resolved_path)
            doc.total_pages = parsed_doc.total_pages

            if self.export_markdown:
                export_parsed_document(parsed_doc, settings.processed_md_dir)

            # 2. Parent-Child Chunking (preserving tables intact in Parent Chunks)
            chunking_result = await self.chunker.achunk_document(
                parsed_doc,
                document_id=doc.id,
            )

            # 3. Persist Parent Chunks in PostgreSQL
            await session.execute(
                delete(ParentChunk).where(ParentChunk.document_id == doc.id)
            )
            parent_rows = [
                ParentChunk(
                    id=p.id,
                    document_id=doc.id,
                    page_number=p.page_number,
                    chunk_type=p.chunk_type.value,
                    content_markdown=p.content_markdown,
                    section_title=p.section_title,
                )
                for p in chunking_result.parent_chunks
            ]
            session.add_all(parent_rows)
            await session.commit()

            # 4. Structured Financial Metrics ETL into PostgreSQL `financial_metrics`
            etl_result = await self.etl_service.run_etl(
                session,
                parsed_doc,
                document_id=doc.id,
                ticker=norm_ticker,
                fiscal_year=fiscal_year,
                period=period,
                language=language.value,
                replace_existing=True,
            )

            # 5. Dual Indexing (Dense + Sparse BM25) of Child Chunks into Qdrant
            indexed_children = await self.vector_indexer.index_child_chunks(
                chunking_result.child_chunks,
                doc_title=resolved_title,
                ticker=norm_ticker,
                fiscal_year=fiscal_year,
                period=period,
                access_level=access_level,
                replace_existing=True,
            )

            # 6. Mark Document status as INDEXED
            doc.status = DocumentStatus.INDEXED.value
            await session.commit()
            await session.refresh(doc)

            collection_name = getattr(
                self.vector_indexer,
                "collection_name",
                settings.qdrant_collection_name,
            )
            logger.info(
                "document_ingestion_completed",
                document_id=str(doc.id),
                ticker=norm_ticker,
                total_pages=parsed_doc.total_pages,
                parent_chunks=len(parent_rows),
                child_chunks=indexed_children,
                financial_metrics=etl_result.persisted_count,
            )
            return DocumentIngestionResponse(
                document_id=doc.id,
                title=doc.title,
                file_name=resolved_path.name,
                file_path=doc.file_path,
                ticker=doc.ticker,
                company_name=doc.company_name,
                fiscal_year=doc.fiscal_year,
                period=FiscalPeriod(doc.period),
                language=DocumentLanguage(doc.language),
                access_level=AccessLevel(doc.access_level),
                status=DocumentStatus(doc.status),
                total_pages=parsed_doc.total_pages,
                total_tables=parsed_doc.total_tables,
                parent_chunks_count=len(parent_rows),
                child_chunks_indexed=indexed_children,
                financial_metrics_count=etl_result.persisted_count,
                qdrant_collection=str(collection_name),
                created_at=doc.created_at,
            )
        except Exception as exc:
            await session.rollback()
            doc_in_db = await session.get(Document, doc_uuid)
            if doc_in_db is not None:
                doc_in_db.status = DocumentStatus.FAILED.value
                await session.commit()
            logger.error(
                "document_ingestion_failed",
                document_id=str(doc_uuid),
                error=str(exc),
            )
            if isinstance(exc, FinSightError):
                raise
            raise FinSightError(
                message=f"Document ingestion failed for '{resolved_path.name}': {exc}",
                status_code=500,
                error_code="DOCUMENT_INGESTION_FAILED",
            ) from exc


__all__ = ["DocumentIngestionPipeline"]
