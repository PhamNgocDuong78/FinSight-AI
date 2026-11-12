"""Unit tests for LayoutAwarePDFParser and Markdown table formatting."""

from pathlib import Path

import pytest

from app.core.exceptions import FinSightError
from app.services.ingestion.pdf_parser import (
    LayoutAwarePDFParser,
    clean_table_cell,
    export_parsed_document,
    rows_to_markdown_table,
)
from app.services.ingestion.sample_pdf_generator import create_sample_financial_pdf


def test_clean_table_cell_and_rows_to_markdown() -> None:
    """Verify multiline cells, pipe characters, and uneven rows are normalized cleanly."""
    assert clean_table_cell("Doanh thu\nthuần | Net") == r"Doanh thu thuần \| Net"
    assert clean_table_cell(None) == ""

    raw_rows: list[list[object]] = [
        ["Chỉ tiêu", "Năm 2024", "Năm 2023"],
        ["Doanh thu thuần", "62.849"],  # Short row (missing 3rd col)
        ["", "", ""],  # Empty row (should be dropped)
        ["Lợi nhuận sau thuế", "9.420", "7.788"],
    ]
    headers, data_rows, md = rows_to_markdown_table(raw_rows)

    assert headers == ["Chỉ tiêu", "Năm 2024", "Năm 2023"]
    assert len(data_rows) == 2
    assert data_rows[0] == ["Doanh thu thuần", "62.849", ""]
    assert data_rows[1] == ["Lợi nhuận sau thuế", "9.420", "7.788"]

    md_lines = md.splitlines()
    assert len(md_lines) == 4
    # Every line in the GFM table must have the exact same number of unescaped pipe delimiters
    for line in md_lines:
        assert line.count("|") == 4


def test_layout_aware_pdf_parser_preserves_financial_tables(tmp_path: Path) -> None:
    """Verify multi-page PDF parsing keeps Income Statement and Balance Sheet columns intact."""
    pdf_path = create_sample_financial_pdf(tmp_path / "FPT_FS_2024_sample.pdf")
    parser = LayoutAwarePDFParser()

    parsed_doc = parser.parse(pdf_path)

    assert parsed_doc.total_pages == 2
    assert parsed_doc.total_tables == 2

    # Page 1: Income Statement
    page1 = parsed_doc.pages[0]
    assert page1.page_number == 1
    assert len(page1.tables) == 1
    income_table = page1.tables[0]
    assert len(income_table.headers) == 6
    assert len(income_table.rows) == 7
    assert income_table.rows[0][0].startswith("1. Doanh thu thuần")
    assert income_table.rows[0][3] == "62.849"
    assert income_table.rows[0][4] == "52.618"
    assert income_table.rows[5][3] == "9.420"

    # Verify every row in Markdown table has 7 pipe characters (6 columns)
    for line in income_table.markdown.splitlines():
        assert line.count("|") == 7

    # Page 2: Balance Sheet
    page2 = parsed_doc.pages[1]
    assert page2.page_number == 2
    assert len(page2.tables) == 1
    balance_table = page2.tables[0]
    assert len(balance_table.headers) == 5
    assert len(balance_table.rows) == 6
    assert balance_table.rows[2][0] == "TỔNG CỘNG TÀI SẢN"
    assert balance_table.rows[2][2] == "72.015"
    assert balance_table.rows[4][0] == "D. VỐN CHỦ SỞ HỮU"
    assert balance_table.rows[4][2] == "35.600"

    for line in balance_table.markdown.splitlines():
        assert line.count("|") == 6


async def test_async_parse_and_export_markdown(tmp_path: Path) -> None:
    """Verify async parsing (aparse) and exporting to .md and .json."""
    pdf_path = create_sample_financial_pdf(tmp_path / "FPT_2024.pdf")
    out_dir = tmp_path / "processed_md"
    parser = LayoutAwarePDFParser()

    parsed_doc = await parser.aparse(pdf_path)
    md_path, json_path = export_parsed_document(parsed_doc, out_dir)

    assert md_path.exists()
    assert json_path.exists()

    md_content = md_path.read_text(encoding="utf-8")
    assert "<!-- PAGE 1 -->" in md_content
    assert "<!-- PAGE 2 -->" in md_content
    assert "| 1. Doanh thu thuần về bán hàng và CCDV | 10 | V.01 | 62.849 | 52.618 | +19,4% |" in md_content
    assert "| TỔNG CỘNG TÀI SẢN | 270 | 72.015 | 60.283 | +19,5% |" in md_content


def test_parse_missing_pdf_raises_finsight_error(tmp_path: Path) -> None:
    """Verify missing PDF file raises FinSightError with 404 status code."""
    parser = LayoutAwarePDFParser()
    with pytest.raises(FinSightError) as exc_info:
        parser.parse(tmp_path / "non_existent.pdf")

    assert exc_info.value.status_code == 404
    assert exc_info.value.error_code == "PDF_NOT_FOUND"
