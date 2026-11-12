"""Layout-aware PDF parser preserving financial table structures in Markdown."""

import re
from pathlib import Path
from typing import Any

import pymupdf

from app.core.exceptions import FinSightError
from app.core.logging import get_logger
from app.schemas.ingestion import ExtractedTable, ParsedDocument, ParsedPage
from app.services.ingestion.base_parser import BaseDocumentParser

logger = get_logger(__name__)

_HEADING_PATTERN = re.compile(
    r"^(?:[IVX]+\.|[0-9]+\.[0-9]*|PHẦN\s+[IVX0-9]+|BẢNG\s+|BÁO\s+CÁO\s+|THUYẾT\s+MINH)",
    re.IGNORECASE,
)


def clean_table_cell(cell: object) -> str:
    """Normalize a single table cell string without breaking Markdown columns."""
    if cell is None:
        return ""
    text = (
        str(cell)
        .replace("\xa0", " ")
        .replace("\xad", "-")
        .replace("\r\n", " ")
        .replace("\n", " ")
        .replace("\t", " ")
    )
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace("|", r"\|")


def rows_to_markdown_table(
    raw_rows: list[list[object]],
) -> tuple[list[str], list[list[str]], str]:
    """Convert raw 2D table cells into aligned headers, rows, and GFM Markdown."""
    cleaned_rows: list[list[str]] = []
    for row in raw_rows:
        cleaned = [clean_table_cell(cell) for cell in row]
        if any(cell != "" for cell in cleaned):
            cleaned_rows.append(cleaned)

    if not cleaned_rows:
        return [], [], ""

    num_cols = max(len(r) for r in cleaned_rows)
    normalized_rows: list[list[str]] = [
        r + [""] * (num_cols - len(r)) for r in cleaned_rows
    ]

    headers = [
        col if col != "" else f"Col_{idx + 1}"
        for idx, col in enumerate(normalized_rows[0])
    ]
    data_rows = normalized_rows[1:]

    header_line = "| " + " | ".join(headers) + " |"
    separator_line = "| " + " | ".join(["---"] * num_cols) + " |"
    body_lines = ["| " + " | ".join(row) + " |" for row in data_rows]

    markdown = "\n".join([header_line, separator_line, *body_lines])
    return headers, data_rows, markdown


def _extract_table_rows_from_cells(
    page: pymupdf.Page, table: Any
) -> list[list[object]]:
    """Extract cell text using span-aware page.get_text(clip=cell) to preserve punctuation."""
    table_rows = getattr(table, "rows", None)
    if not table_rows:
        return list(table.extract())

    extracted: list[list[object]] = []
    for row in table_rows:
        row_values: list[object] = []
        for cell_rect in row.cells:
            if cell_rect is None:
                row_values.append("")
            else:
                padded_rect = pymupdf.Rect(cell_rect) + (-1.0, -1.0, 2.0, 1.0)
                cell_text = page.get_text("text", clip=padded_rect)
                row_values.append(cell_text)
        extracted.append(row_values)
    return extracted


def _is_inside_any_bbox(
    block_bbox: tuple[float, float, float, float],
    table_bboxes: list[tuple[float, float, float, float]],
    margin: float = 2.0,
) -> bool:
    """Check whether the center of a text block falls inside any table bounding box."""
    cx = (block_bbox[0] + block_bbox[2]) / 2.0
    cy = (block_bbox[1] + block_bbox[3]) / 2.0
    for tx0, ty0, tx1, ty1 in table_bboxes:
        if (tx0 - margin) <= cx <= (tx1 + margin) and (ty0 - margin) <= cy <= (
            ty1 + margin
        ):
            return True
    return False


def _detect_heading(text_line: str) -> bool:
    """Heuristically identify section headings in Vietnamese/English financial reports."""
    stripped = text_line.strip()
    if not stripped or len(stripped) > 140:
        return False
    if _HEADING_PATTERN.match(stripped):
        return True
    return stripped.isupper() and len(stripped) >= 6


class LayoutAwarePDFParser(BaseDocumentParser):
    """PDF parser that extracts page-level text and intact Markdown tables."""

    def parse(self, file_path: Path | str) -> ParsedDocument:
        """Parse a PDF file into a structured ParsedDocument with intact Markdown tables."""
        path = Path(file_path).resolve()
        if not path.exists() or not path.is_file():
            raise FinSightError(
                message=f"PDF file not found: {path}",
                status_code=404,
                error_code="PDF_NOT_FOUND",
            )

        logger.info("pdf_parse_started", file_path=str(path))

        try:
            doc = pymupdf.open(str(path))
        except Exception as exc:
            raise FinSightError(
                message=f"Failed to open PDF file '{path.name}': {exc}",
                status_code=422,
                error_code="PDF_OPEN_ERROR",
            ) from exc

        try:
            raw_meta = doc.metadata or {}
            metadata: dict[str, str] = {
                str(k): str(v)
                for k, v in raw_meta.items()
                if v is not None and str(v).strip()
            }

            parsed_pages: list[ParsedPage] = []
            for page_idx in range(len(doc)):
                page = doc[page_idx]
                parsed_page = self._parse_single_page(page, page_number=page_idx + 1)
                parsed_pages.append(parsed_page)

            parsed_doc = ParsedDocument(
                file_path=str(path),
                file_name=path.name,
                total_pages=len(parsed_pages),
                metadata=metadata,
                pages=parsed_pages,
            )
            logger.info(
                "pdf_parse_completed",
                file_name=path.name,
                total_pages=parsed_doc.total_pages,
                total_tables=parsed_doc.total_tables,
            )
            return parsed_doc
        finally:
            doc.close()

    def _parse_single_page(
        self, page: pymupdf.Page, page_number: int
    ) -> ParsedPage:
        """Extract ordered text blocks and structured Markdown tables for one page."""
        table_finder = page.find_tables()
        found_tables = list(table_finder.tables) if table_finder else []

        table_bboxes: list[tuple[float, float, float, float]] = [
            tuple(t.bbox) for t in found_tables  # type: ignore[misc]
        ]

        raw_blocks = page.get_text("blocks")
        ordered_elements: list[tuple[float, str, str]] = []
        section_headings: list[str] = []

        for block in raw_blocks:
            x0, y0, x1, y1, text_content, _block_no, block_type = block[:7]
            if block_type != 0:
                continue
            if _is_inside_any_bbox((x0, y0, x1, y1), table_bboxes):
                continue

            cleaned_block = str(text_content).replace("\xa0", " ").strip()
            if not cleaned_block:
                continue

            formatted_lines: list[str] = []
            for line in cleaned_block.splitlines():
                stripped_line = line.strip()
                if not stripped_line:
                    continue
                if _detect_heading(stripped_line):
                    section_headings.append(stripped_line)
                    formatted_lines.append(f"### {stripped_line}")
                else:
                    formatted_lines.append(stripped_line)

            if formatted_lines:
                ordered_elements.append((float(y0), "text", "\n".join(formatted_lines)))

        extracted_tables: list[ExtractedTable] = []
        for idx, tab in enumerate(found_tables):
            raw_rows = _extract_table_rows_from_cells(page, tab)
            headers, rows, md_table = rows_to_markdown_table(raw_rows)
            if not md_table:
                continue

            ty0 = float(tab.bbox[1])
            preceding_headings = [
                content.removeprefix("### ").strip()
                for y_pos, kind, content in ordered_elements
                if kind == "text" and y_pos <= ty0 and content.startswith("### ")
            ]
            nearest_heading = (
                preceding_headings[-1]
                if preceding_headings
                else (section_headings[-1] if section_headings else None)
            )

            extracted_tables.append(
                ExtractedTable(
                    page_number=page_number,
                    table_index=idx,
                    headers=headers,
                    rows=rows,
                    markdown=md_table,
                    section_title=nearest_heading,
                )
            )
            ordered_elements.append((ty0, "table", md_table))

        ordered_elements.sort(key=lambda item: item[0])
        page_markdown = "\n\n".join(item[2] for item in ordered_elements)
        raw_text = page.get_text("text").replace("\xa0", " ").strip()

        return ParsedPage(
            page_number=page_number,
            raw_text=raw_text,
            markdown=page_markdown,
            tables=extracted_tables,
            section_headings=section_headings,
        )


def export_parsed_document(
    parsed_doc: ParsedDocument, output_dir: Path | str
) -> tuple[Path, Path]:
    """Save ParsedDocument as both Markdown (.md) and structured JSON (.json)."""
    target_dir = Path(output_dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    stem = Path(parsed_doc.file_name).stem
    md_path = target_dir / f"{stem}.md"
    json_path = target_dir / f"{stem}.json"

    md_path.write_text(parsed_doc.to_full_markdown(), encoding="utf-8")
    json_path.write_text(
        parsed_doc.model_dump_json(indent=2),
        encoding="utf-8",
    )
    logger.info(
        "parsed_document_exported",
        markdown_path=str(md_path),
        json_path=str(json_path),
    )
    return md_path, json_path
