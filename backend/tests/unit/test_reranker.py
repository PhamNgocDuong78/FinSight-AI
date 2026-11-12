"""Unit tests for Cross-Encoder (`BAAI/bge-reranker-base`) and Gemini Flash-Lite Rerankers."""

import json
import math
import uuid
from types import SimpleNamespace

import pytest

from app.models.enums import AccessLevel, ChunkType, FiscalPeriod
from app.schemas.retrieval import RerankBatchOutput, RetrievedChunk
from app.services.retrieval.reranker import (
    CrossEncoderReranker,
    FinancialCrossEncoderScorer,
    GeminiFlashLiteReranker,
    HuggingFaceCrossEncoderReranker,
)


def _make_candidate(
    *,
    page_number: int,
    rrf_score: float,
    section_title: str,
    child_snippet: str,
    content_markdown: str,
    ticker: str = "FPT",
    fiscal_year: int = 2024,
    chunk_type: ChunkType = ChunkType.TABLE,
) -> RetrievedChunk:
    """Create a synthetic RetrievedChunk representing output from QdrantHybridRetriever."""
    return RetrievedChunk(
        parent_chunk_id=uuid.uuid4(),
        child_chunk_id=uuid.uuid4(),
        matched_child_ids=[uuid.uuid4()],
        document_id=uuid.uuid4(),
        doc_title=f"Báo cáo Thường niên {ticker} {fiscal_year}",
        company_name=f"Công ty Cổ phần {ticker}",
        ticker=ticker,
        fiscal_year=fiscal_year,
        period=FiscalPeriod.FY,
        access_level=AccessLevel.PUBLIC,
        page_number=page_number,
        chunk_type=chunk_type,
        section_title=section_title,
        content_markdown=content_markdown,
        child_snippet=child_snippet,
        rrf_score=rrf_score,
        dense_score=0.75,
        sparse_score=6.0,
        dense_rank=page_number,
        sparse_rank=page_number,
    )


def test_reranker_promotes_deep_parent_chunk_from_top_20_to_top_5() -> None:
    """Verify Top-20 Hybrid RRF candidates are reranked to Top-5 with rank promotion & metrics."""
    query = (
        "Thuyết minh V.01 cơ cấu doanh thu khối Công nghệ tại thị trường Nhật Bản "
        "và APAC năm 2024 tăng trưởng 32,4%"
    )

    candidates: list[RetrievedChunk] = []
    # Rank 1 in RRF: Shallow table-of-contents distractor with high RRF score
    candidates.append(
        _make_candidate(
            page_number=2,
            rrf_score=0.0328,
            section_title="MỤC LỤC BÁO CÁO THƯỜNG NIÊN 2024",
            child_snippet="Mục lục: Doanh thu FPT năm 2024 và thông tin chung...",
            content_markdown="Trang 2: Mục lục tổng quát Báo cáo thường niên FPT 2024.",
            chunk_type=ChunkType.TEXT,
        )
    )

    # Ranks 2..20: 19 other candidates, where Rank 6 and Rank 14 are the true deep matches
    for rank_idx in range(2, 21):
        rrf_val = round(0.0328 - (rank_idx - 1) * 0.0008, 6)
        if rank_idx == 6:
            # True primary target: Full Parent Table on Page 45
            candidates.append(
                _make_candidate(
                    page_number=45,
                    rrf_score=rrf_val,
                    section_title="THUYẾT MINH V.01 - CƠ CẤU DOANH THU KHỐI CÔNG NGHỆ THEO THỊ TRƯỜNG",
                    child_snippet="Bảng thuyết minh V.01 doanh thu khối Công nghệ năm 2024",
                    content_markdown=(
                        "| Thị trường | Mã thuyết minh | Năm 2024 (Tỷ VNĐ) | Năm 2023 (Tỷ VNĐ) | Tăng trưởng |\n"
                        "| --- | --- | --- | --- | --- |\n"
                        "| Thị trường Nhật Bản | V.01 | 12.450 | 9.403 | +32,4% |\n"
                        "| Thị trường APAC | V.01 | 6.820 | 5.310 | +28,4% |"
                    ),
                    chunk_type=ChunkType.TABLE,
                )
            )
        elif rank_idx == 14:
            # True secondary target: Analytical narrative on Page 46
            candidates.append(
                _make_candidate(
                    page_number=46,
                    rrf_score=rrf_val,
                    section_title="PHÂN TÍCH DOANH THU KHỐI CÔNG NGHỆ THỊ TRƯỜNG NHẬT BẢN VÀ APAC",
                    child_snippet="Phân tích kết quả kinh doanh quốc tế năm 2024",
                    content_markdown=(
                        "Theo Thuyết minh V.01, cơ cấu doanh thu khối Công nghệ tại thị trường Nhật Bản "
                        "năm 2024 đạt 12.450 tỷ VNĐ, tăng trưởng 32,4% nhờ nhu cầu chuyển đổi số và AI, "
                        "trong khi thị trường APAC đạt 6.820 tỷ VNĐ."
                    ),
                    chunk_type=ChunkType.TEXT,
                )
            )
        else:
            candidates.append(
                _make_candidate(
                    page_number=10 + rank_idx,
                    rrf_score=rrf_val,
                    section_title=f"THUYẾT MINH CHUNG SỐ {rank_idx}",
                    child_snippet=f"Thông tin hành chính và nhân sự đoạn {rank_idx}",
                    content_markdown=f"Nội dung thuyết minh quản trị nội bộ và chi phí văn phòng đoạn {rank_idx}.",
                    chunk_type=ChunkType.TEXT,
                )
            )

    assert len(candidates) == 20
    assert candidates[0].page_number == 2  # Before rerank: Page 2 distractor is Rank 1

    reranker = CrossEncoderReranker(provider="lexical_cross", default_top_k=5)
    assert reranker.model_name == FinancialCrossEncoderScorer().model_name
    report = reranker.rerank_with_report(query, candidates, top_k=5)

    # 1. Verify Top-20 narrowed down to Top-5
    assert report.input_count == 20
    assert report.output_count == 5
    assert len(report.results) == 5
    assert len(report.rank_changes) == 5

    # 2. Verify Rank 1 changed from Page 2 distractor to Page 45 target table
    assert report.top_1_changed is True
    assert report.top_1_before_id == candidates[0].parent_chunk_id
    assert report.top_1_after_id == candidates[5].parent_chunk_id
    assert report.results[0].page_number == 45
    assert report.results[1].page_number == 46

    # 3. Verify rank delta telemetry (Page 45 promoted from Rank 6 -> Rank 1, delta = +5)
    top_change = report.rank_changes[0]
    assert top_change.page_number == 45
    assert top_change.rank_before == 6
    assert top_change.rank_after == 1
    assert top_change.rank_delta == 5
    assert top_change.rerank_score > 0.70

    # Page 46 promoted from Rank 14 -> Rank 2, delta = +12
    second_change = report.rank_changes[1]
    assert second_change.page_number == 46
    assert second_change.rank_before == 14
    assert second_change.rank_after == 2
    assert second_change.rank_delta == 12

    # 4. Verify rerank scores are monotonically non-increasing and populated on every chunk
    rerank_scores = [c.rerank_score for c in report.results]
    assert all(s is not None and 0.0 <= s <= 1.0 for s in rerank_scores)
    assert rerank_scores == sorted(rerank_scores, reverse=True)

    # Distractor on Page 2 had lower relevance score than Page 45 and Page 46
    all_raw_scores = reranker.score_pairs(query, candidates)
    assert all_raw_scores[5] > all_raw_scores[0]
    assert all_raw_scores[13] > all_raw_scores[0]


def test_huggingface_cross_encoder_reranker_logits_and_min_score_filter() -> None:
    """Verify BAAI/bge-reranker-base Cross-Encoder logit sigmoid normalization and reordering."""
    c1 = _make_candidate(
        page_number=1,
        rrf_score=0.032,
        section_title="Trang bìa",
        child_snippet="Báo cáo FPT 2024",
        content_markdown="Giới thiệu chung FPT",
    )
    c2 = _make_candidate(
        page_number=2,
        rrf_score=0.029,
        section_title="Bảng cân đối kế toán",
        child_snippet="Tài sản ngắn hạn 46.810",
        content_markdown="| Tài sản ngắn hạn | 46.810 |",
    )
    c3 = _make_candidate(
        page_number=3,
        rrf_score=0.025,
        section_title="Chi tiết ROE và ROA năm 2024",
        child_snippet="ROE đạt 26,4% năm 2024",
        content_markdown="| Chỉ tiêu ROE | 26,4% | ROA | 13,1% |",
    )

    captured_pairs: list[tuple[str, str]] = []

    def fake_bge_predictor(pairs: list[tuple[str, str]]) -> list[float]:
        captured_pairs.extend(pairs)
        # Raw unbounded logits from BAAI/bge-reranker-base: c3 highest (+3.5), c2 medium (+0.5), c1 negative (-2.5)
        return [-2.5, 0.5, 3.5]

    reranker = HuggingFaceCrossEncoderReranker(
        model_name="BAAI/bge-reranker-base",
        predictor=fake_bge_predictor,
        default_top_k=3,
    )
    assert reranker.model_name == "BAAI/bge-reranker-base"

    report = reranker.rerank_with_report(
        "Tỷ suất sinh lời ROE và ROA năm 2024",
        [c1, c2, c3],
        top_k=2,
        min_score=0.5,
    )
    assert len(captured_pairs) == 3
    assert report.output_count == 2
    assert report.results[0].page_number == 3
    assert report.results[1].page_number == 2
    expected_top_score = 1.0 / (1.0 + math.exp(-3.5))
    assert math.isclose(report.results[0].rerank_score or 0.0, expected_top_score, rel_tol=1e-5)
    assert report.rank_changes[0].rank_before == 3
    assert report.rank_changes[0].rank_after == 1
    assert report.rank_changes[0].rank_delta == 2


@pytest.mark.asyncio
async def test_gemini_flash_lite_reranker_structured_output_and_fallback() -> None:
    """Verify Gemini Flash-Lite reranker uses Structured Output and falls back cleanly on error."""
    c_distractor = _make_candidate(
        page_number=5,
        rrf_score=0.032,
        section_title="Thông tin cổ đông",
        child_snippet="Danh sách cổ đông lớn năm 2024",
        content_markdown="Cổ đông nội bộ và tỷ lệ sở hữu năm 2024.",
    )
    c_relevant = _make_candidate(
        page_number=18,
        rrf_score=0.026,
        section_title="BÁO CÁO LƯU CHUYỂN TIỀN TỆ HỢP NHẤT",
        child_snippet="Lưu chuyển tiền thuần từ hoạt động kinh doanh",
        content_markdown=(
            "| Chỉ tiêu | Mã số | Năm 2024 (Tỷ VNĐ) |\n"
            "| --- | --- | --- |\n"
            "| Lưu chuyển tiền thuần từ HĐKD | 20 | 11.850 |"
        ),
    )

    llm_payload = RerankBatchOutput(
        scores=[
            {"candidate_index": 0, "relevance_score": 0.12, "rationale": "Only shareholder info."},
            {
                "candidate_index": 1,
                "relevance_score": 0.96,
                "rationale": "Exact operating cash flow table row.",
            },
        ]
    )
    recorded_kwargs: list[dict[str, object]] = []

    class FakeInteractions:
        def create(self, **kwargs: object) -> object:
            recorded_kwargs.append(kwargs)
            return SimpleNamespace(output_text=json.dumps(llm_payload.model_dump()))

    class FakeAsyncInteractions:
        async def create(self, **kwargs: object) -> object:
            recorded_kwargs.append(kwargs)
            return SimpleNamespace(output_text=json.dumps(llm_payload.model_dump()))

    fake_client = SimpleNamespace(
        interactions=FakeInteractions(),
        aio=SimpleNamespace(interactions=FakeAsyncInteractions()),
    )

    reranker = GeminiFlashLiteReranker(
        model_name="gemini-3.5-flash-lite",
        client=fake_client,  # type: ignore[arg-type]
        default_top_k=2,
    )
    assert reranker.is_available is True
    assert reranker.model_name == "gemini-3.5-flash-lite"

    report = await reranker.arerank_with_report(
        "Lưu chuyển tiền thuần từ hoạt động kinh doanh OPERATING_CASH_FLOW năm 2024",
        [c_distractor, c_relevant],
        top_k=2,
    )
    assert len(recorded_kwargs) == 1
    assert recorded_kwargs[0]["model"] == "gemini-3.5-flash-lite"
    assert recorded_kwargs[0]["store"] is False
    resp_fmt: dict[str, object] = recorded_kwargs[0]["response_format"]  # type: ignore[assignment]
    assert resp_fmt["type"] == "text"
    assert resp_fmt["mime_type"] == "application/json"

    assert report.top_1_changed is True
    assert report.results[0].page_number == 18
    assert report.results[1].page_number == 5
    assert (report.results[0].rerank_score or 0.0) > (report.results[1].rerank_score or 0.0)

    # Verify fallback to FinancialCrossEncoderScorer when API raises RuntimeError
    class FailingInteractions:
        def create(self, **_kwargs: object) -> object:
            raise RuntimeError("Simulated network failure")

    failing_reranker = GeminiFlashLiteReranker(
        client=SimpleNamespace(interactions=FailingInteractions()),  # type: ignore[arg-type]
    )
    fallback_results = failing_reranker.rerank(
        "Lưu chuyển tiền thuần từ hoạt động kinh doanh OPERATING_CASH_FLOW Mã số 20 năm 2024",
        [c_distractor, c_relevant],
        top_k=2,
    )
    assert fallback_results[0].page_number == 18
    assert (fallback_results[0].rerank_score or 0.0) > (fallback_results[1].rerank_score or 0.0)
