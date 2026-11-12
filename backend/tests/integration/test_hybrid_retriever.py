"""Integration tests for Qdrant + PostgreSQL Hybrid Search (Dense + BM25 RRF) and RBAC security."""

import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
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
from app.services.ingestion.pipeline import DocumentIngestionPipeline
from app.services.ingestion.sample_pdf_generator import create_sample_financial_pdf
from app.services.ingestion.vector_indexer import QdrantChildChunkIndexer
from app.services.retrieval.hybrid_retriever import QdrantHybridRetriever


async def _seed_custom_document(
    session: AsyncSession,
    indexer: QdrantChildChunkIndexer,
    *,
    title: str,
    ticker: str,
    company_name: str,
    fiscal_year: int,
    access_level: AccessLevel,
    page_number: int,
    chunk_type: ChunkType,
    section_title: str,
    parent_markdown: str,
    child_text: str,
) -> uuid.UUID:
    """Persist a synthetic Document + ParentChunk in PostgreSQL and index ChildChunk in Qdrant."""
    doc_id = uuid.uuid4()
    parent_id = uuid.uuid4()
    child_id = uuid.uuid4()

    doc = Document(
        id=doc_id,
        title=title,
        file_path=f"data/raw_pdfs/{ticker}_{fiscal_year}_{access_level.value}.pdf",
        ticker=ticker,
        company_name=company_name,
        fiscal_year=fiscal_year,
        period=FiscalPeriod.FY.value,
        language=DocumentLanguage.VI.value,
        access_level=access_level.value,
        status=DocumentStatus.INDEXED.value,
        total_pages=10,
    )
    parent = ParentChunk(
        id=parent_id,
        document_id=doc_id,
        page_number=page_number,
        chunk_type=chunk_type.value,
        content_markdown=parent_markdown,
        section_title=section_title,
    )
    session.add(doc)
    session.add(parent)
    await session.commit()

    child = ChildChunkData(
        id=child_id,
        parent_chunk_id=parent_id,
        document_id=doc_id,
        page_number=page_number,
        chunk_type=chunk_type,
        chunk_index=0,
        section_title=section_title,
        text=child_text,
        token_count=40,
    )
    await indexer.index_child_chunks(
        [child],
        doc_title=title,
        ticker=ticker,
        fiscal_year=fiscal_year,
        period=FiscalPeriod.FY,
        access_level=access_level,
    )
    return doc_id


async def test_hybrid_retriever_rbac_security_and_table_parent_mapping(
    tmp_path: Path,
) -> None:
    """Verify RBAC isolation across viewer/analyst/admin and exact table retrieval by metric/year."""
    sample_pdf = create_sample_financial_pdf(tmp_path / "FPT_2024_Internal.pdf")
    pipeline = DocumentIngestionPipeline()
    indexer = QdrantChildChunkIndexer()
    retriever = QdrantHybridRetriever()
    session_factory = get_session_factory()

    created_doc_ids: list[uuid.UUID] = []

    try:
        async with session_factory() as session:
            # 1. Ingest FPT 2024 2-page PDF as INTERNAL_FINANCE (Page 1: KQKD, Page 2: BCĐKT)
            fpt_2024_resp = await pipeline.ingest_pdf(
                session,
                pdf_path=sample_pdf,
                ticker="FPT",
                fiscal_year=2024,
                title="Báo cáo Tài chính Nội bộ FPT 2024",
                company_name="Công ty Cổ phần FPT",
                period=FiscalPeriod.FY,
                language=DocumentLanguage.VI,
                access_level=AccessLevel.INTERNAL_FINANCE,
            )
            fpt_2024_id = fpt_2024_resp.document_id
            created_doc_ids.append(fpt_2024_id)

            # 2. Seed FPT 2023 report as PUBLIC (Page 8: Bảng doanh thu lịch sử 2023)
            fpt_2023_table_md = (
                "| Chỉ tiêu | Mã số | Năm 2023 (Tỷ VNĐ) | Năm 2022 (Tỷ VNĐ) |\n"
                "| --- | --- | --- | --- |\n"
                "| 1. Doanh thu thuần về bán hàng và CCDV | 10 | 52.618 | 44.010 |\n"
                "| 6. Lợi nhuận sau thuế TNDN | 60 | 7.788 | 6.491 |"
            )
            fpt_2023_id = await _seed_custom_document(
                session,
                indexer,
                title="Báo cáo Thường niên Công khai FPT 2023",
                ticker="FPT",
                company_name="Công ty Cổ phần FPT",
                fiscal_year=2023,
                access_level=AccessLevel.PUBLIC,
                page_number=8,
                chunk_type=ChunkType.TABLE,
                section_title="BẢNG KẾT QUẢ KINH DOANH NĂM 2023",
                parent_markdown=fpt_2023_table_md,
                child_text=(
                    "Tiêu đề bảng: BẢNG KẾT QUẢ KINH DOANH NĂM 2023\n"
                    "- 1. Doanh thu thuần về bán hàng và CCDV: Mã số 10 | Năm 2023: 52.618 tỷ đồng"
                ),
            )
            created_doc_ids.append(fpt_2023_id)

            # 3. Seed VNM 2024 Confidential M&A report as EXECUTIVE_ONLY (Page 15)
            vnm_exec_id = await _seed_custom_document(
                session,
                indexer,
                title="Tài liệu Mật HĐQT Vinamilk 2024",
                ticker="VNM",
                company_name="Công ty Cổ phần Sữa Việt Nam",
                fiscal_year=2024,
                access_level=AccessLevel.EXECUTIVE_ONLY,
                page_number=15,
                chunk_type=ChunkType.TEXT,
                section_title="NGHỊ QUYẾT THâu TÓM M&A ĐỘC QUYỀN 2024",
                parent_markdown=(
                    "Mật: Kế hoạch ngân sách M&A vùng Đông Nam Á của VNM năm 2024 là 18.500 tỷ đồng "
                    "dành riêng cho Ban Tổng Giám đốc và Hội đồng Quản trị."
                ),
                child_text="Kế hoạch ngân sách M&A vùng Đông Nam Á của VNM năm 2024 là 18.500 tỷ đồng",
            )
            created_doc_ids.append(vnm_exec_id)

            # =====================================================================
            # TEST CRITERION 1: RBAC Security Enforcement (viewer vs analyst vs admin)
            # =====================================================================

            # (1a) Viewer querying internal_finance FPT 2024 MUST NOT see any internal_finance chunk
            viewer_fpt_2024 = await retriever.retrieve(
                session,
                "Tổng cộng tài sản Mã số 270 là 72.015 tỷ VNĐ năm 2024",
                user_role=UserRole.VIEWER,
                ticker="FPT",
                fiscal_year=2024,
                document_ids=created_doc_ids,
            )
            assert viewer_fpt_2024 == [], (
                "Security violation: Viewer must NOT retrieve internal_finance FPT 2024 chunks!"
            )

            # (1b) Viewer querying across all created docs only receives PUBLIC (FPT 2023)
            viewer_all = await retriever.retrieve(
                session,
                "Doanh thu thuần và tổng tài sản FPT VNM 72.015 18.500",
                user_role="viewer",
                document_ids=created_doc_ids,
            )
            assert len(viewer_all) >= 1
            for chunk in viewer_all:
                assert chunk.access_level == AccessLevel.PUBLIC
                assert chunk.document_id == fpt_2023_id
                assert chunk.document_id not in (fpt_2024_id, vnm_exec_id)

            # (1c) Viewer attempting explicit privilege escalation via access_levels parameter is blocked
            viewer_escalation = await retriever.retrieve(
                session,
                "Doanh thu FPT 2024",
                user_role=UserRole.VIEWER,
                access_levels=[AccessLevel.INTERNAL_FINANCE, AccessLevel.EXECUTIVE_ONLY],
                document_ids=created_doc_ids,
            )
            assert viewer_escalation == []

            # (1d) Analyst CAN retrieve PUBLIC and INTERNAL_FINANCE, but NOT EXECUTIVE_ONLY
            analyst_vnm_secret = await retriever.retrieve(
                session,
                "Kế hoạch ngân sách M&A vùng Đông Nam Á của VNM 18.500 tỷ đồng",
                user_role=UserRole.ANALYST,
                document_ids=created_doc_ids,
            )
            assert all(c.access_level != AccessLevel.EXECUTIVE_ONLY for c in analyst_vnm_secret)
            assert all(c.document_id != vnm_exec_id for c in analyst_vnm_secret)

            # (1e) Admin CAN retrieve EXECUTIVE_ONLY document
            admin_vnm_secret = await retriever.retrieve(
                session,
                "Kế hoạch ngân sách M&A vùng Đông Nam Á của VNM 18.500 tỷ đồng",
                user_role=UserRole.ADMIN,
                ticker="VNM",
                fiscal_year=2024,
                document_ids=created_doc_ids,
            )
            assert len(admin_vnm_secret) == 1
            assert admin_vnm_secret[0].document_id == vnm_exec_id
            assert admin_vnm_secret[0].access_level == AccessLevel.EXECUTIVE_ONLY
            assert admin_vnm_secret[0].page_number == 15
            assert "18.500 tỷ đồng" in admin_vnm_secret[0].content_markdown

            # =====================================================================
            # TEST CRITERION 2: Metric Code & Specific Year -> Exact Table & Page
            # =====================================================================

            # (2a) Query with Balance Sheet metric code (TOTAL_ASSETS / Mã số 270) in 2024
            # Must return Page 2 Balance Sheet Parent Table with full unsplit Markdown!
            bs_results = await retriever.retrieve(
                session,
                "Chỉ tiêu TOTAL_ASSETS Mã số 270 Tổng cộng tài sản tại 31/12/2024",
                user_role=UserRole.ANALYST,
                ticker="FPT",
                fiscal_year=2024,
                document_ids=created_doc_ids,
                top_k=4,
            )
            assert len(bs_results) >= 1
            top_bs = bs_results[0]
            assert top_bs.document_id == fpt_2024_id
            assert top_bs.ticker == "FPT"
            assert top_bs.fiscal_year == 2024
            assert top_bs.page_number == 2
            assert top_bs.chunk_type == ChunkType.TABLE
            assert "| TỔNG CỘNG TÀI SẢN | 270 | 72.015 | 60.283 | +19,5% |" in top_bs.content_markdown
            assert "| D. VỐN CHỦ SỞ HỮU | 400 | 35.600 | 29.933 | +18,9% |" in top_bs.content_markdown
            assert top_bs.format_citation_tag() == "[FPT_AR_2024, tr. 2]"
            assert top_bs.to_citation_dict()["page_number"] == 2

            # (2b) Query with Income Statement metric code (NET_INCOME / Mã số 60 / V.06) in 2024
            # Must return Page 1 Income Statement Parent Table with full unsplit Markdown!
            is_results = await retriever.retrieve(
                session,
                "Chỉ tiêu NET_INCOME Mã số 60 Thuyết minh V.06 Lợi nhuận sau thuế TNDN năm 2024",
                user_role=UserRole.ANALYST,
                ticker="FPT",
                fiscal_year=2024,
                document_ids=created_doc_ids,
                top_k=4,
            )
            assert len(is_results) >= 1
            top_is = is_results[0]
            assert top_is.document_id == fpt_2024_id
            assert top_is.page_number == 1
            assert top_is.chunk_type == ChunkType.TABLE
            assert "| 6. Lợi nhuận sau thuế TNDN | 60 | V.06 | 9.420 | 7.788 | +21,0% |" in (
                top_is.content_markdown
            )
            assert "| 1. Doanh thu thuần về bán hàng và CCDV | 10 | V.01 | 62.849 |" in (
                top_is.content_markdown
            )
            assert top_is.sparse_rank == 1
            assert top_is.rrf_score > 0.0

            # (2c) Query filtered by fiscal_year=2023 retrieves Page 8 table of FPT 2023 report
            fpt_2023_results = await retriever.retrieve(
                session,
                "Doanh thu thuần Mã số 10 năm 2023",
                user_role=UserRole.VIEWER,
                ticker="FPT",
                fiscal_year=2023,
                document_ids=created_doc_ids,
            )
            assert len(fpt_2023_results) == 1
            assert fpt_2023_results[0].document_id == fpt_2023_id
            assert fpt_2023_results[0].fiscal_year == 2023
            assert fpt_2023_results[0].page_number == 8
            assert "44.010" in fpt_2023_results[0].content_markdown

    finally:
        for doc_id in created_doc_ids:
            await indexer.delete_document_chunks(doc_id)
        async with session_factory() as session:
            for doc_id in created_doc_ids:
                doc_obj = await session.get(Document, doc_id)
                if doc_obj is not None:
                    await session.delete(doc_obj)
            await session.commit()

        settings = get_settings()
        stem = sample_pdf.stem
        (settings.processed_md_dir / f"{stem}.md").unlink(missing_ok=True)
        (settings.processed_md_dir / f"{stem}.json").unlink(missing_ok=True)
