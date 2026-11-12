"""Unit tests for ParentChildChunker on financial narrative text and Markdown tables."""

import uuid
from pathlib import Path

from app.models.enums import ChunkType
from app.schemas.ingestion import ExtractedTable, ParsedDocument, ParsedPage
from app.services.ingestion.chunker import (
    ParentChildChunker,
    count_tokens,
    split_text_by_tokens,
)
from app.services.ingestion.pdf_parser import LayoutAwarePDFParser, rows_to_markdown_table
from app.services.ingestion.sample_pdf_generator import create_sample_financial_pdf


def _assert_markdown_table_intact(md_table: str, expected_data_rows: int) -> None:
    """Assert that a GFM Markdown table has header, separator, all rows, and aligned pipes."""
    lines = [line.strip() for line in md_table.strip().splitlines() if line.strip()]
    assert len(lines) == expected_data_rows + 2, (
        f"Expected {expected_data_rows + 2} lines (header + sep + rows), got {len(lines)}"
    )
    assert lines[0].startswith("|") and lines[0].endswith("|")
    assert "---" in lines[1]
    expected_pipes = lines[0].count("|")
    assert expected_pipes >= 3
    for line in lines:
        assert line.count("|") == expected_pipes


def test_chunk_sample_financial_pdf_preserves_tables_and_links_parents(tmp_path: Path) -> None:
    """Verify PDF tables remain unsplit in Parent Chunks and all Child Chunks link to valid parents."""
    pdf_path = create_sample_financial_pdf(tmp_path / "FPT_FS_2024.pdf")
    parser = LayoutAwarePDFParser()
    parsed_doc = parser.parse(pdf_path)

    doc_uuid = uuid.uuid4()
    chunker = ParentChildChunker(parent_chunk_size=1000, child_chunk_size=300)
    result = chunker.chunk_document(parsed_doc, document_id=doc_uuid)

    assert result.document_id == doc_uuid
    assert result.total_parents >= 4  # 2 text parents + 2 table parents
    assert len(result.table_parent_chunks) == 2
    assert len(result.text_parent_chunks) >= 2

    parent_by_id = {p.id: p for p in result.parent_chunks}

    # 1. Verify NO Markdown table is broken or leaked into TEXT parent chunks
    for text_parent in result.text_parent_chunks:
        assert "| --- |" not in text_parent.content_markdown
        assert text_parent.token_count <= 1000

    # 2. Verify both financial tables are 100% intact at Parent Chunk level
    income_parent = result.table_parent_chunks[0]
    balance_parent = result.table_parent_chunks[1]

    _assert_markdown_table_intact(income_parent.content_markdown, expected_data_rows=7)
    _assert_markdown_table_intact(balance_parent.content_markdown, expected_data_rows=6)
    assert income_parent.content_markdown == parsed_doc.pages[0].tables[0].markdown
    assert balance_parent.content_markdown == parsed_doc.pages[1].tables[0].markdown

    # 3. Verify EVERY Child Chunk points to a valid Parent Chunk and inherits metadata
    assert result.total_children >= len(result.parent_chunks)
    for child in result.child_chunks:
        assert child.parent_chunk_id in parent_by_id
        linked_parent = parent_by_id[child.parent_chunk_id]
        assert child.document_id == doc_uuid
        assert child.page_number == linked_parent.page_number
        assert child.chunk_type == linked_parent.chunk_type
        assert child.token_count <= 300

    # 4. Verify Table Child Chunks contain table title + key metrics (chỉ tiêu chính)
    table_children = [c for c in result.child_chunks if c.chunk_type == ChunkType.TABLE]
    assert len(table_children) >= 2
    income_children = [c for c in table_children if c.parent_chunk_id == income_parent.id]
    assert len(income_children) >= 1
    assert "Tiêu đề bảng:" in income_children[0].text
    assert "1. Doanh thu thuần về bán hàng và CCDV" in income_children[0].key_metrics
    assert "6. Lợi nhuận sau thuế TNDN" in income_children[0].key_metrics
    assert "62.849" in income_children[0].text


def test_oversized_financial_table_never_split_in_parent_chunk() -> None:
    """Prove a 45-row table (>1200 tokens) stays 100% intact in Parent Chunk while splitting children."""
    raw_rows: list[list[object]] = [
        ["Chỉ tiêu thuyết minh chi tiết", "Mã số", "Năm 2024 (Tỷ VNĐ)", "Năm 2023 (Tỷ VNĐ)", "Biến động (%)"]
    ]
    for i in range(1, 46):
        raw_rows.append(
            [
                f"Chỉ tiêu tài chính hợp nhất khoản mục số {i:02d} thuộc khối Công nghệ và Viễn thông",
                f"{100 + i}",
                f"{10_000 + i * 125:,}".replace(",", "."),
                f"{9_000 + i * 110:,}".replace(",", "."),
                "+11,5%",
            ]
        )

    headers, data_rows, md_table = rows_to_markdown_table(raw_rows)
    large_table = ExtractedTable(
        page_number=12,
        table_index=0,
        headers=headers,
        rows=data_rows,
        markdown=md_table,
        section_title="THUYẾT MINH CHI TIẾT DOANH THU VÀ CHI PHÍ THEO BỘ PHẬN",
    )

    page = ParsedPage(
        page_number=12,
        raw_text="Thuyết minh báo cáo tài chính hợp nhất",
        markdown=f"### THUYẾT MINH CHI TIẾT DOANH THU VÀ CHI PHÍ THEO BỘ PHẬN\n\n{md_table}",
        tables=[large_table],
        section_headings=["THUYẾT MINH CHI TIẾT DOANH THU VÀ CHI PHÍ THEO BỘ PHẬN"],
    )
    doc = ParsedDocument(
        file_path="FPT_AR_2024.pdf",
        file_name="FPT_AR_2024.pdf",
        total_pages=1,
        pages=[page],
    )

    chunker = ParentChildChunker(parent_chunk_size=1000, child_chunk_size=300)
    result = chunker.chunk_document(doc)

    # Even though the table is >1200 tokens, there must be ONLY 1 Table Parent Chunk!
    assert len(result.table_parent_chunks) == 1
    table_parent = result.table_parent_chunks[0]
    assert table_parent.token_count > 1000
    _assert_markdown_table_intact(table_parent.content_markdown, expected_data_rows=45)

    # Child chunks are split into ~300-token batches, all pointing to the single table_parent.id
    table_children = [c for c in result.child_chunks if c.chunk_type == ChunkType.TABLE]
    assert len(table_children) >= 3
    all_recovered_metrics: list[str] = []
    for idx, child in enumerate(table_children):
        assert child.parent_chunk_id == table_parent.id
        assert child.chunk_index == idx
        assert child.token_count <= 300
        assert "Tiêu đề bảng: THUYẾT MINH CHI TIẾT DOANH THU VÀ CHI PHÍ THEO BỘ PHẬN" in child.text
        all_recovered_metrics.extend(child.key_metrics)

    assert len(all_recovered_metrics) == 45


async def test_narrative_text_parent_1000_and_child_300_tokens_and_qdrant_payload() -> None:
    """Verify long narrative text splits into ~1000-token Parents and ~300-token Children."""
    sentences = [
        (
            f"Câu phân tích số {i}: Trong năm tài chính 2024, Tập đoàn FPT ghi nhận mức tăng trưởng "
            f"ấn tượng ở thị trường nước ngoài nhờ nhu cầu chuyển đổi số và dịch vụ AI tăng mạnh."
        )
        for i in range(1, 75)
    ]
    long_narrative = "### ĐÁNH GIÁ CỦA BAN ĐIỀU HÀNH\n\n" + " ".join(sentences)
    assert count_tokens(long_narrative) > 2000

    inline_md_table = (
        "| Khu vực thị trường | Doanh thu 2024 | Tăng trưởng |\n"
        "| --- | --- | --- |\n"
        "| Nhật Bản | 12.300 | +32,2% |\n"
        "| Châu Á - Thái Bình Dương | 8.450 | +34,8% |"
    )

    page = ParsedPage(
        page_number=5,
        raw_text=long_narrative,
        markdown=f"{long_narrative}\n\n{inline_md_table}",
        tables=[],  # Deliberately empty to test inline GFM table auto-detection
        section_headings=["ĐÁNH GIÁ CỦA BAN ĐIỀU HÀNH"],
    )
    doc = ParsedDocument(
        file_path="FPT_2024.pdf",
        file_name="FPT_2024.pdf",
        total_pages=1,
        pages=[page],
    )

    chunker = ParentChildChunker(
        parent_chunk_size=1000,
        parent_chunk_overlap=100,
        child_chunk_size=300,
        child_chunk_overlap=50,
    )
    result = await chunker.achunk_document(doc)

    # 1. Inline table was automatically detected and kept intact as a TABLE parent chunk
    assert len(result.table_parent_chunks) == 1
    _assert_markdown_table_intact(result.table_parent_chunks[0].content_markdown, expected_data_rows=2)

    # 2. Long narrative (>2000 tokens) was split into multiple ~1000-token Parent Chunks
    assert len(result.text_parent_chunks) >= 3
    parent_ids = {p.id for p in result.parent_chunks}
    for p_chunk in result.text_parent_chunks:
        assert p_chunk.token_count <= 1000
        assert "| --- |" not in p_chunk.content_markdown

    # 3. Every Child Chunk stays <= 300 tokens, links to a valid Parent, and serializes to Qdrant payload
    text_children = [c for c in result.child_chunks if c.chunk_type == ChunkType.TEXT]
    assert len(text_children) >= len(result.text_parent_chunks) * 2
    for c_chunk in result.child_chunks:
        assert c_chunk.parent_chunk_id in parent_ids
        assert c_chunk.token_count <= 300
        payload = c_chunk.to_qdrant_payload(
            doc_title="Báo cáo thường niên FPT 2024",
            ticker="FPT",
            fiscal_year=2024,
            period="FY",
            access_level="public",
        )
        assert payload["parent_chunk_id"] == str(c_chunk.parent_chunk_id)
        assert payload["document_id"] == str(result.document_id)
        assert payload["ticker"] == "FPT"
        assert payload["page_number"] == 5


def test_split_text_by_tokens_handles_single_huge_sentence() -> None:
    """Verify word-level fallback when a single sentence without punctuation exceeds max_tokens."""
    huge_sentence = " ".join(f"token_{i}" for i in range(650))
    chunks = split_text_by_tokens(huge_sentence, max_tokens=300, overlap_tokens=30)
    assert len(chunks) >= 3
    for chunk in chunks:
        assert count_tokens(chunk) <= 300
