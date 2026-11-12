"""Parent-Child chunking strategy for financial narrative text and Markdown tables."""

import asyncio
import re
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.enums import ChunkType
from app.schemas.ingestion import (
    ChildChunkData,
    ChunkingResult,
    ExtractedTable,
    ParentChunkData,
    ParsedDocument,
    ParsedPage,
)

logger = get_logger(__name__)

_TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]", re.UNICODE)
_SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[.!?])\s+|\n+")
_MD_HEADING_PATTERN = re.compile(r"^#{1,6}\s+(.+)$")
_MD_TABLE_SEP_PATTERN = re.compile(r"^\s*\|(?:\s*:?-{2,}:?\s*\|)+\s*$")


def count_tokens(text: str) -> int:
    """Count word, number, and symbol tokens deterministically for VI/EN financial text."""
    return len(_TOKEN_PATTERN.findall(text)) if text and text.strip() else 0


def _split_long_unit_by_words(
    unit: str, max_tokens: int, overlap_tokens: int, token_counter: Callable[[str], int]
) -> list[str]:
    """Split a single oversized sentence/paragraph by whitespace words within max_tokens."""
    words, slices, start = unit.split(), [], 0
    while start < len(words):
        end = start + 1
        while end <= len(words) and token_counter(" ".join(words[start:end])) <= max_tokens:
            end += 1
        actual_end = max(start + 1, end - 1)
        slices.append(" ".join(words[start:actual_end]))
        if actual_end >= len(words):
            break
        ov_start = actual_end
        while (
            ov_start > start + 1
            and token_counter(" ".join(words[ov_start - 1 : actual_end])) <= overlap_tokens
        ):
            ov_start -= 1
        start = ov_start
    return slices


def split_text_by_tokens(
    text: str,
    max_tokens: int,
    overlap_tokens: int = 0,
    token_counter: Callable[[str], int] = count_tokens,
) -> list[str]:
    """Split narrative text into chunks of at most max_tokens while preserving sentence boundaries."""
    cleaned = text.strip()
    if not cleaned:
        return []
    if token_counter(cleaned) <= max_tokens:
        return [cleaned]

    units: list[str] = []
    for raw_u in _SENTENCE_SPLIT_PATTERN.split(cleaned):
        if not (u := raw_u.strip()):
            continue
        if token_counter(u) <= max_tokens:
            units.append(u)
        else:
            units.extend(_split_long_unit_by_words(u, max_tokens, overlap_tokens, token_counter))

    chunks: list[str] = []
    current_units: list[str] = []
    current_tokens = 0
    for unit in units:
        u_tokens = token_counter(unit)
        if current_units and (current_tokens + u_tokens > max_tokens):
            chunks.append("\n".join(current_units))
            overlap_buf, overlap_cnt = [], 0
            for prev in reversed(current_units):
                pt = token_counter(prev)
                if overlap_tokens > 0 and overlap_cnt + pt <= overlap_tokens:
                    overlap_buf.insert(0, prev)
                    overlap_cnt += pt
                else:
                    break
            current_units, current_tokens = overlap_buf, overlap_cnt
        current_units.append(unit)
        current_tokens += u_tokens

    if current_units:
        chunks.append("\n".join(current_units))
    return chunks


def _extract_tables_and_narrative(
    page: ParsedPage,
) -> tuple[list[ExtractedTable], list[tuple[str | None, str]]]:
    """Separate intact tables from narrative text blocks with their nearest section headings."""
    remaining_md = page.markdown
    for tab in page.tables:
        if tab.markdown and tab.markdown in remaining_md:
            remaining_md = remaining_md.replace(tab.markdown, "\n\n")

    inline_tables: list[ExtractedTable] = []
    narrative_segments: list[tuple[str | None, str]] = []
    active_heading: str | None = page.section_headings[0] if page.section_headings else None
    lines, idx, text_buf = remaining_md.splitlines(), 0, []

    def _flush() -> None:
        if text_buf and (joined := "\n".join(text_buf).strip()):
            narrative_segments.append((active_heading, joined))
        text_buf.clear()

    def _split_md_row(row_line: str) -> list[str]:
        return [c.strip() for c in re.split(r"(?<!\\)\|", row_line.strip().strip("|"))]

    while idx < len(lines):
        line = lines[idx].strip()
        if not line:
            _flush()
            idx += 1
            continue
        if (
            line.startswith("|")
            and idx + 1 < len(lines)
            and _MD_TABLE_SEP_PATTERN.match(lines[idx + 1].strip())
        ):
            _flush()
            t_lines: list[str] = []
            while idx < len(lines) and lines[idx].strip().startswith("|"):
                t_lines.append(lines[idx].strip())
                idx += 1
            inline_tables.append(
                ExtractedTable(
                    page_number=page.page_number,
                    table_index=len(page.tables) + len(inline_tables),
                    headers=_split_md_row(t_lines[0]),
                    rows=[_split_md_row(r) for r in t_lines[2:] if r.strip()],
                    markdown="\n".join(t_lines),
                    section_title=active_heading,
                )
            )
            continue
        if match := _MD_HEADING_PATTERN.match(line):
            _flush()
            active_heading = match.group(1).strip()
        text_buf.append(line)
        idx += 1

    _flush()
    existing_mds = {t.markdown.strip() for t in page.tables}
    all_tables = list(page.tables) + [
        t for t in inline_tables if t.markdown.strip() not in existing_mds
    ]
    return all_tables, narrative_segments


class BaseDocumentChunker(ABC):
    """Abstract interface for pluggable document chunkers."""

    @abstractmethod
    def chunk_document(
        self, parsed_doc: ParsedDocument, document_id: uuid.UUID | None = None
    ) -> ChunkingResult:
        """Synchronously split a ParsedDocument into linked Parent and Child chunks."""

    async def achunk_document(
        self, parsed_doc: ParsedDocument, document_id: uuid.UUID | None = None
    ) -> ChunkingResult:
        """Asynchronously chunk a ParsedDocument without blocking the event loop."""
        return await asyncio.to_thread(self.chunk_document, parsed_doc, document_id)


class ParentChildChunker(BaseDocumentChunker):
    """Parent-Child chunker preserving full financial tables and hierarchical text context."""

    def __init__(
        self,
        parent_chunk_size: int | None = None,
        parent_chunk_overlap: int | None = None,
        child_chunk_size: int | None = None,
        child_chunk_overlap: int | None = None,
        token_counter: Callable[[str], int] = count_tokens,
    ) -> None:
        settings = get_settings()
        self.parent_chunk_size = parent_chunk_size or settings.parent_chunk_size
        self.parent_chunk_overlap = (
            parent_chunk_overlap if parent_chunk_overlap is not None else settings.parent_chunk_overlap
        )
        self.child_chunk_size = child_chunk_size or settings.child_chunk_size
        self.child_chunk_overlap = (
            child_chunk_overlap if child_chunk_overlap is not None else settings.child_chunk_overlap
        )
        self.token_counter = token_counter

    def chunk_document(
        self, parsed_doc: ParsedDocument, document_id: uuid.UUID | None = None
    ) -> ChunkingResult:
        """Split a ParsedDocument into intact Table/Text Parent Chunks and linked Child Chunks."""
        doc_uuid = document_id or uuid.uuid4()
        parents, children = [], []
        for page in parsed_doc.pages:
            p_list, c_list = self._chunk_single_page(page, doc_uuid)
            parents.extend(p_list)
            children.extend(c_list)

        logger.info(
            "document_chunking_completed",
            file_name=parsed_doc.file_name,
            document_id=str(doc_uuid),
            total_parents=len(parents),
            total_children=len(children),
        )
        return ChunkingResult(
            document_id=doc_uuid,
            file_name=parsed_doc.file_name,
            parent_chunks=parents,
            child_chunks=children,
        )

    def _chunk_single_page(
        self, page: ParsedPage, document_id: uuid.UUID
    ) -> tuple[list[ParentChunkData], list[ChildChunkData]]:
        """Process narrative text and tables on a single page into Parent and Child chunks."""
        tables, narrative_segments = _extract_tables_and_narrative(page)
        parents: list[ParentChunkData] = []
        children: list[ChildChunkData] = []

        if full_narrative := "\n\n".join(seg for _, seg in narrative_segments).strip():
            fallback_heading = next((h for h, _ in narrative_segments if h), None)
            p_texts = split_text_by_tokens(
                full_narrative, self.parent_chunk_size, self.parent_chunk_overlap, self.token_counter
            )
            for p_text in p_texts:
                heading = self._extract_first_heading(p_text) or fallback_heading
                parent = ParentChunkData(
                    document_id=document_id,
                    page_number=page.page_number,
                    chunk_type=ChunkType.TEXT,
                    content_markdown=p_text,
                    section_title=heading,
                    token_count=self.token_counter(p_text),
                )
                parents.append(parent)
                c_texts = split_text_by_tokens(
                    p_text, self.child_chunk_size, self.child_chunk_overlap, self.token_counter
                )
                for idx, c_text in enumerate(c_texts):
                    children.append(
                        ChildChunkData(
                            parent_chunk_id=parent.id,
                            document_id=document_id,
                            page_number=page.page_number,
                            chunk_type=ChunkType.TEXT,
                            chunk_index=idx,
                            section_title=heading,
                            text=c_text,
                            token_count=self.token_counter(c_text),
                        )
                    )

        for table in tables:
            if table.markdown.strip():
                t_parent, t_children = self._chunk_table(table, document_id)
                parents.append(t_parent)
                children.extend(t_children)

        return parents, children

    def _chunk_table(
        self, table: ExtractedTable, document_id: uuid.UUID
    ) -> tuple[ParentChunkData, list[ChildChunkData]]:
        """Keep full Markdown table as Parent Chunk and create Child Chunk(s) with title + key metrics."""
        table_md = table.markdown.strip()
        title = (
            table.section_title
            or f"Bảng số liệu tài chính (Trang {table.page_number}, Bảng {table.table_index + 1})"
        )
        parent = ParentChunkData(
            document_id=document_id,
            page_number=table.page_number,
            chunk_type=ChunkType.TABLE,
            content_markdown=table_md,
            section_title=title,
            token_count=self.token_counter(table_md),
        )
        header_prefix = (
            f"Tiêu đề bảng: {title}\n"
            f"Các cột: {', '.join(table.headers) if table.headers else 'N/A'}\n"
            "Các chỉ tiêu chính:"
        )
        prefix_tokens = self.token_counter(header_prefix)

        metric_entries: list[tuple[str, str]] = []
        for row in table.rows:
            if not (non_empty := [c.strip() for c in row if c.strip()]):
                continue
            metric_name = row[0].strip() or non_empty[0]
            vals = [v.strip() for v in row[1:] if v.strip()]
            line = f"- {metric_name}: {' | '.join(vals)}" if vals else f"- {metric_name}"
            metric_entries.append((metric_name, line))

        def _build_child(idx: int, lines: list[str], metrics: list[str]) -> ChildChunkData:
            c_text = f"{header_prefix}\n" + "\n".join(lines) if lines else header_prefix
            return ChildChunkData(
                parent_chunk_id=parent.id,
                document_id=document_id,
                page_number=table.page_number,
                chunk_type=ChunkType.TABLE,
                chunk_index=idx,
                section_title=title,
                text=c_text,
                token_count=self.token_counter(c_text),
                key_metrics=metrics,
            )

        if not metric_entries:
            return parent, [_build_child(0, [], [])]

        children, b_metrics, b_lines, b_tokens = [], [], [], prefix_tokens
        for m_name, m_line in metric_entries:
            l_tokens = self.token_counter(m_line)
            if b_lines and (b_tokens + l_tokens > self.child_chunk_size):
                children.append(_build_child(len(children), b_lines, list(b_metrics)))
                b_metrics, b_lines, b_tokens = [], [], prefix_tokens
            b_metrics.append(m_name)
            b_lines.append(m_line)
            b_tokens += l_tokens

        if b_lines:
            children.append(_build_child(len(children), b_lines, list(b_metrics)))
        return parent, children

    @staticmethod
    def _extract_first_heading(markdown_text: str) -> str | None:
        """Extract the first Markdown heading from a text passage, if present."""
        for line in markdown_text.splitlines():
            if match := _MD_HEADING_PATTERN.match(line.strip()):
                return match.group(1).strip()
        return None
