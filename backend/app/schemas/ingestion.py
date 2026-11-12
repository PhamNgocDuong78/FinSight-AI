"""Pydantic v2 schemas for multimodal document parsing and table extraction."""

from pydantic import BaseModel, Field


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
