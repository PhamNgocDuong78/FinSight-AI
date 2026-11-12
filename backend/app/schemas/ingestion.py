"""Pydantic v2 schemas for multimodal document parsing, table extraction, and chunking."""

import uuid

from pydantic import BaseModel, Field

from app.models.enums import ChunkType


class ExtractedTable(BaseModel):
    """Structured representation of a table extracted from a document page."""

    page_number: int = Field(
        ge=1,
        description="1-based page number where the table appears.",
    )
    table_index: int = Field(
        ge=0,
        description="0-based index of the table within the page.",
    )
    headers: list[str] = Field(
        default_factory=list,
        description="Normalized header column names.",
    )
    rows: list[list[str]] = Field(
        default_factory=list,
        description="Normalized data rows matching the header column count.",
    )
    markdown: str = Field(
        description="GitHub-Flavored Markdown table string with aligned columns.",
    )
    section_title: str | None = Field(
        default=None,
        description="Nearest preceding section heading on the page, if detected.",
    )


class ParsedPage(BaseModel):
    """Extracted text, layout-preserved Markdown, and tables for a single page."""

    page_number: int = Field(
        ge=1,
        description="1-based page number.",
    )
    raw_text: str = Field(
        default="",
        description="Plain text extracted from the page.",
    )
    markdown: str = Field(
        default="",
        description="Layout-aware Markdown content with tables inline in reading order.",
    )
    tables: list[ExtractedTable] = Field(
        default_factory=list,
        description="List of structured tables extracted from this page.",
    )
    section_headings: list[str] = Field(
        default_factory=list,
        description="Detected section titles or headings on this page.",
    )


class ParsedDocument(BaseModel):
    """Unified output of any BaseDocumentParser implementation."""

    file_path: str = Field(description="Path to the source document.")
    file_name: str = Field(description="Base filename of the source document.")
    total_pages: int = Field(ge=0, description="Total number of pages parsed.")
    metadata: dict[str, str] = Field(
        default_factory=dict,
        description="Document-level metadata (title, author, subject, etc.).",
    )
    pages: list[ParsedPage] = Field(
        default_factory=list,
        description="Ordered list of parsed pages.",
    )

    @property
    def total_tables(self) -> int:
        """Return total number of tables extracted across all pages."""
        return sum(len(page.tables) for page in self.pages)

    def to_full_markdown(self) -> str:
        """Render the entire document into a single Markdown string with page markers."""
        sections: list[str] = [
            f"# {self.file_name}",
            f"- **Total Pages:** {self.total_pages}",
            f"- **Total Extracted Tables:** {self.total_tables}",
            "",
        ]
        for page in self.pages:
            sections.append(f"<!-- PAGE {page.page_number} -->")
            sections.append(f"## Page {page.page_number}")
            sections.append("")
            sections.append(page.markdown.strip())
            sections.append("")
        return "\n".join(sections).strip() + "\n"


class ParentChunkData(BaseModel):
    """Full-context parent chunk (~1000 tokens of text or an entire unsplit Markdown table)."""

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        description="Unique UUID for the parent chunk.",
    )
    document_id: uuid.UUID = Field(
        description="UUID of the source document.",
    )
    page_number: int = Field(
        ge=1,
        description="1-based page number where the parent chunk originates.",
    )
    chunk_type: ChunkType = Field(
        default=ChunkType.TEXT,
        description="Type of parent chunk (text, table, or chart_summary).",
    )
    content_markdown: str = Field(
        description="Full Markdown content (complete table or narrative text passage).",
    )
    section_title: str | None = Field(
        default=None,
        description="Nearest section heading or table title.",
    )
    token_count: int = Field(
        ge=0,
        description="Estimated token count of content_markdown.",
    )


class ChildChunkData(BaseModel):
    """Search-optimized child chunk (~300 tokens or table title + key metrics) linked to a parent chunk."""

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        description="Unique UUID for the child chunk point in Qdrant.",
    )
    parent_chunk_id: uuid.UUID = Field(
        description="UUID referencing the parent chunk in PostgreSQL.",
    )
    document_id: uuid.UUID = Field(
        description="UUID of the source document.",
    )
    page_number: int = Field(
        ge=1,
        description="1-based page number.",
    )
    chunk_type: ChunkType = Field(
        default=ChunkType.TEXT,
        description="Chunk type matching its parent chunk.",
    )
    chunk_index: int = Field(
        ge=0,
        default=0,
        description="0-based index of this child chunk within its parent chunk.",
    )
    section_title: str | None = Field(
        default=None,
        description="Section heading or table title associated with this chunk.",
    )
    text: str = Field(
        description="Searchable text (~300 tokens for narrative text, or table title + key metrics).",
    )
    token_count: int = Field(
        ge=0,
        description="Estimated token count of the child chunk text.",
    )
    key_metrics: list[str] = Field(
        default_factory=list,
        description="Extracted metric line items (chỉ tiêu chính) when chunk_type is table.",
    )

    def to_qdrant_payload(
        self,
        *,
        doc_title: str = "",
        ticker: str = "",
        fiscal_year: int = 0,
        period: str = "FY",
        access_level: str = "public",
    ) -> dict[str, object]:
        """Serialize child chunk into the Qdrant payload structure defined in ARCHITECTURE.md."""
        return {
            "document_id": str(self.document_id),
            "parent_chunk_id": str(self.parent_chunk_id),
            "doc_title": doc_title,
            "ticker": ticker,
            "fiscal_year": fiscal_year,
            "period": period,
            "access_level": access_level,
            "page_number": self.page_number,
            "chunk_type": self.chunk_type.value,
            "section_title": self.section_title or "",
            "text": self.text,
        }


class ChunkingResult(BaseModel):
    """Unified output of the Parent-Child chunking pipeline for a parsed document."""

    document_id: uuid.UUID = Field(description="UUID of the chunked document.")
    file_name: str = Field(description="Source filename of the chunked document.")
    parent_chunks: list[ParentChunkData] = Field(
        default_factory=list,
        description="Ordered list of parent chunks to persist in PostgreSQL.",
    )
    child_chunks: list[ChildChunkData] = Field(
        default_factory=list,
        description="Ordered list of child chunks to index in Qdrant.",
    )

    @property
    def total_parents(self) -> int:
        """Return total number of parent chunks."""
        return len(self.parent_chunks)

    @property
    def total_children(self) -> int:
        """Return total number of child chunks."""
        return len(self.child_chunks)

    @property
    def table_parent_chunks(self) -> list[ParentChunkData]:
        """Return only parent chunks of type TABLE."""
        return [c for c in self.parent_chunks if c.chunk_type == ChunkType.TABLE]

    @property
    def text_parent_chunks(self) -> list[ParentChunkData]:
        """Return only parent chunks of type TEXT."""
        return [c for c in self.parent_chunks if c.chunk_type == ChunkType.TEXT]


