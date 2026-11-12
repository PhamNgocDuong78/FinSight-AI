"""Unit tests for RBAC access matrix, Qdrant filter builder, RRF fusion, and Hybrid Retriever."""

import math
import uuid

import pytest
from qdrant_client import models

from app.models.enums import AccessLevel, ChunkType, FiscalPeriod, UserRole
from app.schemas.retrieval import (
    RetrievalFilter,
    parse_access_level,
    parse_user_role,
    resolve_allowed_access_levels,
)
from app.services.retrieval.hybrid_retriever import (
    DEFAULT_RRF_K,
    build_qdrant_filter,
    expand_query_with_metric_synonyms,
    reciprocal_rank_fusion,
)


def test_rbac_access_matrix_and_anti_privilege_escalation() -> None:
    """Verify each UserRole maps to its exact permitted AccessLevels and blocks escalation."""
    # 1. Default permissions per role (SPEC.md Section 3)
    assert resolve_allowed_access_levels(UserRole.VIEWER) == [AccessLevel.PUBLIC]
    assert resolve_allowed_access_levels("viewer") == [AccessLevel.PUBLIC]

    assert resolve_allowed_access_levels(UserRole.ANALYST) == [
        AccessLevel.PUBLIC,
        AccessLevel.INTERNAL_FINANCE,
    ]
    assert resolve_allowed_access_levels("ANALYST") == [
        AccessLevel.PUBLIC,
        AccessLevel.INTERNAL_FINANCE,
    ]

    assert resolve_allowed_access_levels(UserRole.ADMIN) == [
        AccessLevel.PUBLIC,
        AccessLevel.INTERNAL_FINANCE,
        AccessLevel.EXECUTIVE_ONLY,
    ]

    # 2. Privilege escalation attempts must be blocked (empty intersection)
    assert (
        resolve_allowed_access_levels(
            UserRole.VIEWER,
            requested_levels=[AccessLevel.INTERNAL_FINANCE],
        )
        == []
    )
    assert (
        resolve_allowed_access_levels(
            UserRole.VIEWER,
            requested_levels=[AccessLevel.EXECUTIVE_ONLY],
        )
        == []
    )
    assert (
        resolve_allowed_access_levels(
            UserRole.ANALYST,
            requested_levels=[AccessLevel.INTERNAL_FINANCE, AccessLevel.EXECUTIVE_ONLY],
        )
        == [AccessLevel.INTERNAL_FINANCE]
    )

    # 3. Invalid role or access level raises ValueError
    with pytest.raises(ValueError, match="Invalid user_role"):
        parse_user_role("super_hacker")

    with pytest.raises(ValueError, match="Invalid access_level"):
        parse_access_level("top_secret")


def test_build_qdrant_filter_enforces_rbac_ticker_and_fiscal_year() -> None:
    """Verify Qdrant Filter includes mandatory RBAC access_level plus ticker and fiscal_year."""
    doc_id = uuid.uuid4()
    rf_single = RetrievalFilter.from_inputs(
        user_role="analyst",
        ticker=" fpt ",
        fiscal_year=2024,
        period="fy",
        chunk_type="table",
        document_ids=[doc_id],
    )
    assert rf_single.user_role == UserRole.ANALYST
    assert rf_single.allowed_access_levels == [
        AccessLevel.PUBLIC,
        AccessLevel.INTERNAL_FINANCE,
    ]
    assert rf_single.tickers == ["FPT"]
    assert rf_single.fiscal_years == [2024]
    assert rf_single.period == FiscalPeriod.FY
    assert rf_single.chunk_type == ChunkType.TABLE

    q_filter = build_qdrant_filter(rf_single)
    assert isinstance(q_filter.must, list)
    by_key = {cond.key: cond.match for cond in q_filter.must}  # type: ignore[union-attr]

    assert isinstance(by_key["access_level"], models.MatchAny)
    assert by_key["access_level"].any == ["public", "internal_finance"]
    assert isinstance(by_key["ticker"], models.MatchValue)
    assert by_key["ticker"].value == "FPT"
    assert isinstance(by_key["fiscal_year"], models.MatchValue)
    assert by_key["fiscal_year"].value == 2024
    assert isinstance(by_key["period"], models.MatchValue)
    assert by_key["period"].value == "FY"
    assert isinstance(by_key["chunk_type"], models.MatchValue)
    assert by_key["chunk_type"].value == "table"
    assert isinstance(by_key["document_id"], models.MatchAny)
    assert by_key["document_id"].any == [str(doc_id)]

    # Multi-ticker and multi-year use MatchAny
    rf_multi = RetrievalFilter.from_inputs(
        user_role=UserRole.VIEWER,
        ticker=["fpt", "vnm", "FPT"],
        fiscal_year=[2023, 2024],
    )
    q_multi = build_qdrant_filter(rf_multi)
    multi_by_key = {cond.key: cond.match for cond in q_multi.must}  # type: ignore[union-attr]
    assert multi_by_key["access_level"].any == ["public"]  # type: ignore[union-attr]
    assert isinstance(multi_by_key["ticker"], models.MatchAny)
    assert multi_by_key["ticker"].any == ["FPT", "VNM"]
    assert isinstance(multi_by_key["fiscal_year"], models.MatchAny)
    assert multi_by_key["fiscal_year"].any == [2023, 2024]


def test_reciprocal_rank_fusion_combines_dense_and_sparse_ranks_accurately() -> None:
    """Verify RRF formula: score(d) = 1/(k + rank_dense) + 1/(k + rank_sparse)."""
    doc_id = str(uuid.uuid4())
    parent_a = str(uuid.uuid4())
    parent_b = str(uuid.uuid4())
    parent_c = str(uuid.uuid4())

    child_a = str(uuid.uuid4())  # Rank 2 in Dense, Rank 1 in Sparse (both branches!)
    child_b = str(uuid.uuid4())  # Rank 1 in Dense only
    child_c = str(uuid.uuid4())  # Rank 2 in Sparse only

    def _make_point(pt_id: str, parent_id: str, score: float, page: int, text: str) -> models.ScoredPoint:
        return models.ScoredPoint(
            id=pt_id,
            version=1,
            score=score,
            payload={
                "document_id": doc_id,
                "parent_chunk_id": parent_id,
                "doc_title": "Báo cáo FPT 2024",
                "ticker": "FPT",
                "fiscal_year": 2024,
                "period": "FY",
                "access_level": "public",
                "page_number": page,
                "chunk_type": "table",
                "section_title": f"Bảng trang {page}",
                "text": text,
            },
        )

    dense_list = [
        _make_point(child_b, parent_b, 0.92, 1, "Thuyết minh chung"),
        _make_point(child_a, parent_a, 0.88, 2, "Bảng cân đối kế toán: Mã số 270 Tổng tài sản 72.015"),
    ]
    sparse_list = [
        _make_point(child_a, parent_a, 14.5, 2, "Bảng cân đối kế toán: Mã số 270 Tổng tài sản 72.015"),
        _make_point(child_c, parent_c, 9.1, 1, "Báo cáo kết quả kinh doanh"),
    ]

    fused = reciprocal_rank_fusion(
        dense_list,
        sparse_list,
        rrf_k=DEFAULT_RRF_K,
        top_k=10,
    )

    assert len(fused) == 3
    # child_a appears in both (dense_rank=2, sparse_rank=1) -> highest RRF score
    assert str(fused[0].child_chunk_id) == child_a
    expected_rrf_a = (1.0 / (60 + 2)) + (1.0 / (60 + 1))
    assert math.isclose(fused[0].rrf_score, expected_rrf_a, rel_tol=1e-6)
    assert fused[0].dense_rank == 2
    assert fused[0].sparse_rank == 1
    assert fused[0].dense_score == 0.88
    assert fused[0].sparse_score == 14.5
    assert fused[0].page_number == 2

    # child_b is rank 1 in dense -> RRF = 1 / 61
    assert str(fused[1].child_chunk_id) == child_b
    assert math.isclose(fused[1].rrf_score, 1.0 / 61.0, rel_tol=1e-6)
    assert fused[1].dense_rank == 1
    assert fused[1].sparse_rank is None

    # child_c is rank 2 in sparse -> RRF = 1 / 62
    assert str(fused[2].child_chunk_id) == child_c
    assert math.isclose(fused[2].rrf_score, 1.0 / 62.0, rel_tol=1e-6)
    assert fused[2].dense_rank is None
    assert fused[2].sparse_rank == 2


def test_expand_query_with_metric_synonyms_enriches_metric_codes() -> None:
    """Verify queries mentioning standardized MetricCodes get enriched with VI and EN labels."""
    expanded = expand_query_with_metric_synonyms("Truy vấn TOTAL_ASSETS và NET_INCOME năm 2024")
    assert "Tổng cộng tài sản" in expanded
    assert "Total Assets" in expanded
    assert "Lợi nhuận sau thuế thu nhập doanh nghiệp" in expanded
    assert "Net Income After Tax" in expanded

    plain_query = "Chiến lược chuyển đổi số tại thị trường Nhật Bản"
    assert expand_query_with_metric_synonyms(plain_query) == plain_query


async def test_qdrant_hybrid_retriever_deduplicates_children_per_parent() -> None:
    """Verify multiple Child Chunks pointing to the same Parent Chunk are deduplicated keeping max RRF."""
    from types import SimpleNamespace

    from app.models.document import Document, ParentChunk
    from app.services.retrieval.hybrid_retriever import QdrantHybridRetriever

    doc_uuid = uuid.uuid4()
    parent_uuid = uuid.uuid4()
    child_1 = uuid.uuid4()
    child_2 = uuid.uuid4()

    pt_1 = models.ScoredPoint(
        id=str(child_1),
        version=1,
        score=0.95,
        payload={
            "document_id": str(doc_uuid),
            "parent_chunk_id": str(parent_uuid),
            "doc_title": "BCTC FPT 2024",
            "ticker": "FPT",
            "fiscal_year": 2024,
            "period": "FY",
            "access_level": "public",
            "page_number": 2,
            "chunk_type": "table",
            "section_title": "Bảng cân đối kế toán",
            "text": "Phần 1: Tài sản ngắn hạn 46.810",
        },
    )
    pt_2 = models.ScoredPoint(
        id=str(child_2),
        version=1,
        score=0.85,
        payload={
            "document_id": str(doc_uuid),
            "parent_chunk_id": str(parent_uuid),
            "doc_title": "BCTC FPT 2024",
            "ticker": "FPT",
            "fiscal_year": 2024,
            "period": "FY",
            "access_level": "public",
            "page_number": 2,
            "chunk_type": "table",
            "section_title": "Bảng cân đối kế toán",
            "text": "Phần 2: Tổng cộng tài sản 72.015",
        },
    )

    class FakeQdrant:
        async def collection_exists(self, _name: str) -> bool:
            return True

        async def query_points(self, **_kwargs: object) -> object:
            return SimpleNamespace(points=[pt_1, pt_2])

    parent_row = ParentChunk(
        id=parent_uuid,
        document_id=doc_uuid,
        page_number=2,
        chunk_type="table",
        content_markdown="| Chỉ tiêu | 2024 |\n| --- | --- |\n| Tổng tài sản | 72.015 |",
        section_title="Bảng cân đối kế toán",
    )
    doc_row = Document(
        id=doc_uuid,
        title="BCTC FPT 2024",
        file_path="FPT_2024.pdf",
        ticker="FPT",
        company_name="CTCP FPT",
        fiscal_year=2024,
        period="FY",
        language="vi",
        access_level="public",
        status="indexed",
    )

    class FakeSession:
        async def execute(self, _stmt: object) -> object:
            return SimpleNamespace(all=lambda: [(parent_row, doc_row)])

    retriever = QdrantHybridRetriever(qdrant_client=FakeQdrant())  # type: ignore[arg-type]
    results = await retriever.retrieve(
        FakeSession(),  # type: ignore[arg-type]
        "Tổng tài sản FPT 2024",
        user_role=UserRole.VIEWER,
        ticker="FPT",
        fiscal_year=2024,
    )

    assert len(results) == 1
    assert results[0].parent_chunk_id == parent_uuid
    assert results[0].child_chunk_id == child_1
    assert results[0].matched_child_ids == [child_1, child_2]
    assert "| Tổng tài sản | 72.015 |" in results[0].content_markdown

