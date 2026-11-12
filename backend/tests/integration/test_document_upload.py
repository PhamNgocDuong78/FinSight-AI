"""End-to-end integration tests for Qdrant Dual Indexing and POST /api/v1/documents/upload."""

import uuid
from decimal import Decimal
from pathlib import Path

from httpx import ASGITransport, AsyncClient
from qdrant_client import models
from sqlalchemy import select

from app.core.config import get_settings
from app.db.postgres import get_session_factory
from app.db.qdrant import get_qdrant_client
from app.main import app
from app.models.document import Document, ParentChunk
from app.models.enums import (
    AccessLevel,
    ChunkType,
    DocumentStatus,
    MetricCode,
)
from app.models.financial_metric import FinancialMetric
from app.services.ingestion.sample_pdf_generator import create_sample_financial_pdf
from app.services.ingestion.vector_indexer import (
    DENSE_VECTOR_NAME,
    SPARSE_VECTOR_NAME,
    QdrantChildChunkIndexer,
)


async def test_upload_document_end_to_end_persists_db_and_qdrant_dual_index(
    tmp_path: Path,
) -> None:
    """Verify uploading a PDF via POST /api/v1/documents/upload populates PostgreSQL and Qdrant."""
    sample_pdf_path = create_sample_financial_pdf(tmp_path / "FPT_AR_2024_upload.pdf")
    pdf_bytes = sample_pdf_path.read_bytes()

    indexer = QdrantChildChunkIndexer()
    doc_uuid: uuid.UUID | None = None
    stored_file_path: Path | None = None

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        try:
            response = await client.post(
                "/api/v1/documents/upload",
                files={"file": ("FPT_AR_2024_upload.pdf", pdf_bytes, "application/pdf")},
                data={
                    "ticker": "fpt",
                    "company_name": "Công ty Cổ phần FPT",
                    "title": "Báo cáo Tài chính Hợp nhất FPT 2024",
                    "fiscal_year": "2024",
                    "period": "FY",
                    "language": "vi",
                    "access_level": "internal_finance",
                },
            )
            assert response.status_code == 201, response.text
            payload = response.json()

            doc_uuid_str = payload["document_id"]
            doc_uuid = uuid.UUID(doc_uuid_str)
            stored_file_path = Path(payload["file_path"])

            assert payload["ticker"] == "FPT"
            assert payload["company_name"] == "Công ty Cổ phần FPT"
            assert payload["title"] == "Báo cáo Tài chính Hợp nhất FPT 2024"
            assert payload["fiscal_year"] == 2024
            assert payload["period"] == "FY"
            assert payload["language"] == "vi"
            assert payload["access_level"] == AccessLevel.INTERNAL_FINANCE.value
            assert payload["status"] == DocumentStatus.INDEXED.value
            assert payload["total_pages"] == 2
            assert payload["total_tables"] == 2
            assert payload["parent_chunks_count"] == 4
            assert payload["child_chunks_indexed"] == 4
            assert payload["financial_metrics_count"] >= 24
            assert payload["qdrant_collection"] == "finsight_child_chunks"
            assert stored_file_path.exists()

            # Verify GET /api/v1/documents/{document_id} and GET /api/v1/documents
            get_resp = await client.get(f"/api/v1/documents/{doc_uuid_str}")
            assert get_resp.status_code == 200
            assert get_resp.json()["status"] == DocumentStatus.INDEXED.value

            list_resp = await client.get("/api/v1/documents")
            assert list_resp.status_code == 200
            assert any(item["id"] == doc_uuid_str for item in list_resp.json())

            # 1. Verify PostgreSQL records (Document, ParentChunk, FinancialMetric)
            session_factory = get_session_factory()
            async with session_factory() as session:
                doc_stmt = select(Document).where(Document.id == doc_uuid)
                doc_row = (await session.execute(doc_stmt)).scalar_one()
                assert doc_row.status == DocumentStatus.INDEXED.value
                assert doc_row.total_pages == 2

                parent_stmt = select(ParentChunk).where(ParentChunk.document_id == doc_uuid)
                parent_rows = (await session.execute(parent_stmt)).scalars().all()
                assert len(parent_rows) == payload["parent_chunks_count"]
                parent_ids = {str(p.id) for p in parent_rows}
                table_parents = [p for p in parent_rows if p.chunk_type == ChunkType.TABLE.value]
                assert len(table_parents) == 2
                assert any("62.849" in p.content_markdown for p in table_parents)

                metric_stmt = select(FinancialMetric).where(
                    FinancialMetric.document_id == doc_uuid
                )
                metric_rows = (await session.execute(metric_stmt)).scalars().all()
                assert len(metric_rows) == payload["financial_metrics_count"]
                metric_map = {(m.metric_code, m.fiscal_year): m for m in metric_rows}
                assert metric_map[(MetricCode.REVENUE.value, 2024)].value == Decimal("62849.0000")
                assert metric_map[(MetricCode.NET_INCOME.value, 2024)].value == Decimal("9420.0000")
                assert metric_map[(MetricCode.TOTAL_ASSETS.value, 2024)].value == Decimal(
                    "72015.0000"
                )

            # 2. Verify Qdrant Collection & Dual Indexed Child Chunks (Dense + Sparse BM25 + Payload)
            qdrant = get_qdrant_client()
            collection_info = await qdrant.get_collection("finsight_child_chunks")
            vectors_cfg = collection_info.config.params.vectors
            sparse_cfg = collection_info.config.params.sparse_vectors
            assert isinstance(vectors_cfg, dict)
            assert DENSE_VECTOR_NAME in vectors_cfg
            assert vectors_cfg[DENSE_VECTOR_NAME].size == 768
            assert vectors_cfg[DENSE_VECTOR_NAME].distance == models.Distance.COSINE
            assert isinstance(sparse_cfg, dict)
            assert SPARSE_VECTOR_NAME in sparse_cfg

            qdrant_count = await indexer.count_document_chunks(doc_uuid)
            assert qdrant_count == payload["child_chunks_indexed"]

            points, _next_offset = await qdrant.scroll(
                collection_name="finsight_child_chunks",
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="document_id",
                            match=models.MatchValue(value=str(doc_uuid)),
                        )
                    ]
                ),
                limit=100,
                with_payload=True,
                with_vectors=True,
            )
            assert len(points) == payload["child_chunks_indexed"]

            for pt in points:
                assert isinstance(pt.vector, dict)
                dense_vec = pt.vector.get(DENSE_VECTOR_NAME)
                sparse_vec = pt.vector.get(SPARSE_VECTOR_NAME)
                assert isinstance(dense_vec, list)
                assert len(dense_vec) == 768
                assert sparse_vec is not None
                assert len(sparse_vec.indices) > 0  # type: ignore[union-attr]
                assert len(sparse_vec.values) > 0  # type: ignore[union-attr]

                pt_payload = pt.payload or {}
                assert pt_payload["document_id"] == str(doc_uuid)
                assert pt_payload["parent_chunk_id"] in parent_ids
                assert pt_payload["doc_title"] == "Báo cáo Tài chính Hợp nhất FPT 2024"
                assert pt_payload["ticker"] == "FPT"
                assert pt_payload["fiscal_year"] == 2024
                assert pt_payload["period"] == "FY"
                assert pt_payload["access_level"] == AccessLevel.INTERNAL_FINANCE.value
                assert pt_payload["page_number"] in (1, 2)
                assert pt_payload["chunk_type"] in (ChunkType.TEXT.value, ChunkType.TABLE.value)
                assert isinstance(pt_payload["text"], str) and len(pt_payload["text"]) > 0

        finally:
            if doc_uuid is not None:
                await indexer.delete_document_chunks(doc_uuid)
                session_factory = get_session_factory()
                async with session_factory() as session:
                    doc_obj = await session.get(Document, doc_uuid)
                    if doc_obj is not None:
                        await session.delete(doc_obj)
                        await session.commit()
            if stored_file_path is not None:
                stored_file_path.unlink(missing_ok=True)
                settings = get_settings()
                stem = stored_file_path.stem
                (settings.processed_md_dir / f"{stem}.md").unlink(missing_ok=True)
                (settings.processed_md_dir / f"{stem}.json").unlink(missing_ok=True)


async def test_upload_document_rejects_non_pdf_file() -> None:
    """Verify POST /api/v1/documents/upload returns 400 INVALID_PDF_FILE for invalid file content."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/v1/documents/upload",
            files={"file": ("not_a_pdf.txt", b"Hello world not a pdf", "text/plain")},
            data={"ticker": "FPT", "fiscal_year": "2024"},
        )
        assert response.status_code == 400
        assert response.json()["error_code"] == "INVALID_PDF_FILE"
