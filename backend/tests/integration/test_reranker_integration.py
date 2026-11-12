"""Integration test for Hybrid Retriever (Qdrant + PostgreSQL) combined with Cross-Encoder Reranker."""

import uuid

from app.db.postgres import get_session_factory
from app.models.document import Document, ParentChunk
from app.models.enums import (
    AccessLevel,
    ChunkType,
    DocumentLanguage,
    DocumentStatus,
    FiscalPeriod,
    UserRole,
)
from app.schemas.ingestion import ChildChunkData
from app.services.ingestion.vector_indexer import QdrantChildChunkIndexer
from app.services.retrieval.hybrid_retriever import QdrantHybridRetriever
from app.services.retrieval.reranker import CrossEncoderReranker


async def test_hybrid_retrieve_and_rerank_promotes_full_parent_table_over_child_keyword_stuffing() -> (
    None
):
    """Verify CrossEncoderReranker reorders live Qdrant+PostgreSQL Hybrid Search candidates."""
    indexer = QdrantChildChunkIndexer()
    retriever = QdrantHybridRetriever()
    reranker = CrossEncoderReranker(provider="lexical_cross", default_top_k=5)
    session_factory = get_session_factory()

    doc_id = uuid.uuid4()
    parent_toc_id = uuid.uuid4()
    parent_note_id = uuid.uuid4()
    child_toc_id = uuid.uuid4()
    child_note_id = uuid.uuid4()

    try:
        async with session_factory() as session:
            doc = Document(
                id=doc_id,
                title="Báo cáo Thường niên FPT 2024 (Rerank Test)",
                file_path="data/raw_pdfs/FPT_2024_rerank_test.pdf",
                ticker="FPT",
                company_name="Công ty Cổ phần FPT",
                fiscal_year=2024,
                period=FiscalPeriod.FY.value,
                language=DocumentLanguage.VI.value,
                access_level=AccessLevel.PUBLIC.value,
                status=DocumentStatus.INDEXED.value,
                total_pages=50,
            )
            # Parent 1 (Page 3): Shallow TOC entry whose Child Chunk has repeated keywords
            parent_toc = ParentChunk(
                id=parent_toc_id,
                document_id=doc_id,
                page_number=3,
                chunk_type=ChunkType.TEXT.value,
                section_title="MỤC LỤC BÁO CÁO",
                content_markdown="Trang 3: Mục lục tổng hợp báo cáo thường niên năm 2024.",
            )
            # Parent 2 (Page 45): Full detailed Parent Table + Note with exact figures
            parent_note = ParentChunk(
                id=parent_note_id,
                document_id=doc_id,
                page_number=45,
                chunk_type=ChunkType.TABLE.value,
                section_title="THUYẾT MINH V.01 - CƠ CẤU DOANH THU KHỐI CÔNG NGHỆ THỊ TRƯỜNG NHẬT BẢN",
                content_markdown=(
                    "| Khu vực thị trường | Mã thuyết minh | Năm 2024 (Tỷ VNĐ) | Tăng trưởng |\n"
                    "| --- | --- | --- | --- |\n"
                    "| Thị trường Nhật Bản | V.01 | 12.450 | +32,4% |\n\n"
                    "Thuyết minh V.01: Cơ cấu doanh thu khối Công nghệ tại thị trường Nhật Bản "
                    "đạt 12.450 tỷ VNĐ, tăng trưởng 32,4% nhờ hợp đồng chuyển đổi số AI và Cloud."
                ),
            )
            session.add_all([doc, parent_toc, parent_note])
            await session.commit()

            # Child 1 has repeated "Nhật Bản" to win initial BM25/Dense RRF Rank 1
            child_toc = ChildChunkData(
                id=child_toc_id,
                parent_chunk_id=parent_toc_id,
                document_id=doc_id,
                page_number=3,
                chunk_type=ChunkType.TEXT,
                chunk_index=0,
                section_title="MỤC LỤC BÁO CÁO",
                text="Nhật Bản Nhật Bản Nhật Bản thị trường Nhật Bản FPT 2024",
                token_count=25,
            )
            # Child 2 is a concise table summary that lands at Rank 2 in initial RRF
            child_note = ChildChunkData(
                id=child_note_id,
                parent_chunk_id=parent_note_id,
                document_id=doc_id,
                page_number=45,
                chunk_type=ChunkType.TABLE,
                chunk_index=1,
                section_title="THUYẾT MINH V.01",
                text="Bảng thuyết minh phân tích theo khu vực địa lý thị trường Nhật Bản",
                token_count=25,
            )
            await indexer.index_child_chunks(
                [child_toc, child_note],
                doc_title=doc.title,
                ticker="FPT",
                fiscal_year=2024,
                period=FiscalPeriod.FY,
                access_level=AccessLevel.PUBLIC,
            )

            query = (
                "Thuyết minh V.01 cơ cấu doanh thu khối Công nghệ tại thị trường Nhật Bản "
                "đạt 12.450 tỷ VNĐ tăng trưởng 32,4% nhờ hợp đồng chuyển đổi số AI và Cloud"
            )

            # Step 1: Retrieve via Hybrid Search (before reranking)
            hybrid_candidates = await retriever.retrieve(
                session,
                query,
                user_role=UserRole.VIEWER,
                ticker="FPT",
                fiscal_year=2024,
                document_ids=[doc_id],
                top_k=20,
            )
            assert len(hybrid_candidates) == 2

            # Step 2: Rerank candidates with CrossEncoderReranker
            report = await reranker.arerank_with_report(query, hybrid_candidates, top_k=5)
            assert report.input_count == 2
            assert report.output_count == 2
            assert report.results[0].parent_chunk_id == parent_note_id
            assert report.results[0].page_number == 45
            assert report.results[1].parent_chunk_id == parent_toc_id
            assert report.results[1].page_number == 3

            # Verify relevance score of Page 45 full Parent Table strictly exceeds Page 3 TOC
            score_note = report.results[0].rerank_score or 0.0
            score_toc = report.results[1].rerank_score or 0.0
            assert score_note > 0.75
            assert score_note > score_toc + 0.30

    finally:
        await indexer.delete_document_chunks(doc_id)
        async with session_factory() as session:
            doc_obj = await session.get(Document, doc_id)
            if doc_obj is not None:
                await session.delete(doc_obj)
                await session.commit()
