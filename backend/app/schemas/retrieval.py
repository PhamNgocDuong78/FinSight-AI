"""Pydantic v2 schemas and RBAC filter helpers for Hybrid Retrieval (Dense + BM25 + RRF)."""

import uuid
from collections.abc import Sequence

from pydantic import BaseModel, Field

from app.models.enums import AccessLevel, ChunkType, FiscalPeriod, UserRole

# Role-Based Access Control (RBAC) matrix as defined in docs/SPEC.md Section 3
ROLE_ACCESS_MATRIX: dict[UserRole, tuple[AccessLevel, ...]] = {
    UserRole.VIEWER: (AccessLevel.PUBLIC,),
    UserRole.ANALYST: (AccessLevel.PUBLIC, AccessLevel.INTERNAL_FINANCE),
    UserRole.ADMIN: (
        AccessLevel.PUBLIC,
        AccessLevel.INTERNAL_FINANCE,
        AccessLevel.EXECUTIVE_ONLY,
    ),
}


def parse_user_role(role: UserRole | str) -> UserRole:
    """Validate and normalize a UserRole enum or case-insensitive role string."""
    if isinstance(role, UserRole):
        return role
    cleaned = str(role).strip().lower()
    try:
        return UserRole(cleaned)
    except ValueError as exc:
        valid = ", ".join(r.value for r in UserRole)
        raise ValueError(f"Invalid user_role '{role}'. Expected one of: {valid}") from exc


def parse_access_level(level: AccessLevel | str) -> AccessLevel:
    """Validate and normalize an AccessLevel enum or case-insensitive string."""
    if isinstance(level, AccessLevel):
        return level
    cleaned = str(level).strip().lower()
    try:
        return AccessLevel(cleaned)
    except ValueError as exc:
        valid = ", ".join(a.value for a in AccessLevel)
        raise ValueError(f"Invalid access_level '{level}'. Expected one of: {valid}") from exc


def resolve_allowed_access_levels(
    user_role: UserRole | str = UserRole.VIEWER,
    requested_levels: Sequence[AccessLevel | str] | None = None,
) -> list[AccessLevel]:
    """Return effective AccessLevels permitted for `user_role`, intersected with any explicit filter.

    Security invariant: A user can NEVER escalate privileges by passing `requested_levels`
    outside their `ROLE_ACCESS_MATRIX[user_role]` permissions.
    """
    normalized_role = parse_user_role(user_role)
    role_allowed = list(ROLE_ACCESS_MATRIX[normalized_role])

    if requested_levels is None:
        return role_allowed

    requested_set = {parse_access_level(lvl) for lvl in requested_levels}
    return [lvl for lvl in role_allowed if lvl in requested_set]


def _normalize_tickers(ticker_input: str | Sequence[str] | None) -> list[str]:
    """Normalize a single ticker string or sequence of tickers into unique uppercase symbols."""
    if ticker_input is None:
        return []
    raw_items = [ticker_input] if isinstance(ticker_input, str) else list(ticker_input)
    result: list[str] = []
    for item in raw_items:
        cleaned = str(item).strip().upper()
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def _normalize_years(year_input: int | Sequence[int] | None) -> list[int]:
    """Normalize a single fiscal year integer or sequence of years into unique integers."""
    if year_input is None:
        return []
    raw_items = [year_input] if isinstance(year_input, int) else list(year_input)
    result: list[int] = []
    for item in raw_items:
        year_val = int(item)
        if year_val not in result:
            result.append(year_val)
    return result


class RetrievalFilter(BaseModel):
    """Validated metadata & RBAC filter applied to Qdrant payload and PostgreSQL queries."""

    user_role: UserRole = Field(
        default=UserRole.VIEWER,
        description="RBAC role of the querying user (defaults to least-privilege viewer).",
    )
    allowed_access_levels: list[AccessLevel] = Field(
        default_factory=lambda: [AccessLevel.PUBLIC],
        description="Effective document access levels permitted for this query.",
    )
    tickers: list[str] = Field(
        default_factory=list,
        description="Optional uppercase stock ticker symbols to filter by (e.g., ['FPT']).",
    )
    fiscal_years: list[int] = Field(
        default_factory=list,
        description="Optional fiscal years to filter by (e.g., [2024]).",
    )
    period: FiscalPeriod | None = Field(
        default=None,
        description="Optional reporting period filter (FY, Q1..Q4).",
    )
    chunk_type: ChunkType | None = Field(
        default=None,
        description="Optional chunk type filter (text, table, chart_summary).",
    )
    document_ids: list[uuid.UUID] = Field(
        default_factory=list,
        description="Optional list of specific document UUIDs to restrict search to.",
    )

    @classmethod
    def from_inputs(
        cls,
        *,
        user_role: UserRole | str = UserRole.VIEWER,
        ticker: str | Sequence[str] | None = None,
        fiscal_year: int | Sequence[int] | None = None,
        period: FiscalPeriod | str | None = None,
        chunk_type: ChunkType | str | None = None,
        access_levels: Sequence[AccessLevel | str] | None = None,
        document_ids: Sequence[uuid.UUID | str] | None = None,
    ) -> "RetrievalFilter":
        """Construct a normalized RetrievalFilter enforcing RBAC access boundaries."""
        norm_role = parse_user_role(user_role)
        effective_levels = resolve_allowed_access_levels(norm_role, access_levels)
        norm_period = (
            FiscalPeriod(str(period).strip().upper())
            if period is not None and not isinstance(period, FiscalPeriod)
            else period
        )
        norm_chunk_type = (
            ChunkType(str(chunk_type).strip().lower())
            if chunk_type is not None and not isinstance(chunk_type, ChunkType)
            else chunk_type
        )
        norm_doc_ids = [
            uid if isinstance(uid, uuid.UUID) else uuid.UUID(str(uid))
            for uid in (document_ids or [])
        ]
        return cls(
            user_role=norm_role,
            allowed_access_levels=effective_levels,
            tickers=_normalize_tickers(ticker),
            fiscal_years=_normalize_years(fiscal_year),
            period=norm_period,
            chunk_type=norm_chunk_type,
            document_ids=norm_doc_ids,
        )


class ChildChunkMatch(BaseModel):
    """A Child Chunk candidate from Qdrant scored by Reciprocal Rank Fusion (RRF)."""

    child_chunk_id: uuid.UUID = Field(description="Qdrant point UUID of the Child Chunk.")
    parent_chunk_id: uuid.UUID = Field(description="Linked Parent Chunk UUID in PostgreSQL.")
    document_id: uuid.UUID = Field(description="Source Document UUID.")
    doc_title: str = Field(default="", description="Document title from Qdrant payload.")
    ticker: str = Field(default="", description="Stock ticker symbol.")
    fiscal_year: int = Field(default=0, description="Fiscal year.")
    period: str = Field(default="FY", description="Reporting period.")
    access_level: AccessLevel = Field(
        default=AccessLevel.PUBLIC,
        description="RBAC classification level.",
    )
    page_number: int = Field(ge=1, description="1-based source PDF page number.")
    chunk_type: ChunkType = Field(default=ChunkType.TEXT, description="Chunk type.")
    section_title: str = Field(default="", description="Section heading or table title.")
    text: str = Field(default="", description="Child chunk text content.")
    rrf_score: float = Field(ge=0.0, description="Combined Reciprocal Rank Fusion score.")
    dense_score: float | None = Field(default=None, description="Cosine similarity score.")
    sparse_score: float | None = Field(default=None, description="Sparse BM25 lexical score.")
    dense_rank: int | None = Field(default=None, ge=1, description="1-based rank in Dense search.")
    sparse_rank: int | None = Field(default=None, ge=1, description="1-based rank in BM25 search.")


class RetrievedChunk(BaseModel):
    """Full-context Parent Chunk retrieved from PostgreSQL and enriched with RRF scores."""

    parent_chunk_id: uuid.UUID = Field(description="UUID of the Parent Chunk in PostgreSQL.")
    child_chunk_id: uuid.UUID = Field(description="UUID of the best-matching Child Chunk.")
    matched_child_ids: list[uuid.UUID] = Field(
        default_factory=list,
        description="All matched Child Chunk UUIDs that mapped to this Parent Chunk.",
    )
    document_id: uuid.UUID = Field(description="UUID of the source Document in PostgreSQL.")
    doc_title: str = Field(description="Full document title.")
    company_name: str = Field(default="", description="Full company name.")
    ticker: str = Field(description="Stock ticker symbol.")
    fiscal_year: int = Field(description="Fiscal year.")
    period: FiscalPeriod = Field(default=FiscalPeriod.FY, description="Reporting period.")
    access_level: AccessLevel = Field(description="RBAC access classification of the document.")
    page_number: int = Field(ge=1, description="1-based page number in the source PDF.")
    chunk_type: ChunkType = Field(description="Type of chunk (text, table, chart_summary).")
    section_title: str | None = Field(default=None, description="Section heading or table title.")
    content_markdown: str = Field(
        description="Full unsplit Markdown table or narrative text from PostgreSQL parent_chunks."
    )
    child_snippet: str = Field(
        default="",
        description="Matched child chunk snippet used to trigger retrieval.",
    )
    rrf_score: float = Field(ge=0.0, description="Reciprocal Rank Fusion (RRF) score.")
    dense_score: float | None = Field(default=None, description="Dense cosine similarity score.")
    sparse_score: float | None = Field(default=None, description="Sparse BM25 score.")
    dense_rank: int | None = Field(default=None, ge=1, description="1-based rank in Dense search.")
    sparse_rank: int | None = Field(default=None, ge=1, description="1-based rank in BM25 search.")
    rerank_score: float | None = Field(
        default=None,
        description="Cross-encoder reranker score (populated in Task 2.2).",
    )

    def format_citation_tag(self) -> str:
        """Format inline citation badge as `[TICKER_AR_YEAR, tr. PAGE]`."""
        return f"[{self.ticker}_AR_{self.fiscal_year}, tr. {self.page_number}]"

    def to_citation_dict(self) -> dict[str, object]:
        """Serialize into the `CitationItem` structure defined in `docs/ARCHITECTURE.md`."""
        return {
            "doc_id": str(self.document_id),
            "doc_title": self.doc_title,
            "ticker": self.ticker,
            "fiscal_year": self.fiscal_year,
            "page_number": self.page_number,
            "snippet": self.child_snippet or self.content_markdown[:300],
        }


class RerankCandidateScore(BaseModel):
    """Structured relevance score for a single candidate chunk from Gemini Flash-Lite reranker."""

    candidate_index: int = Field(ge=0, description="0-based index in the candidate list.")
    relevance_score: float = Field(ge=0.0, le=1.0, description="Relevance score in [0.0, 1.0].")
    rationale: str = Field(default="", description="Short justification for the assigned score.")


class RerankBatchOutput(BaseModel):
    """Structured JSON output schema for batch LLM Cross-Attention reranking."""

    scores: list[RerankCandidateScore] = Field(
        default_factory=list,
        description="List of relevance scores for evaluated candidate chunks.",
    )


class RerankRankChange(BaseModel):
    """Before-and-after ranking telemetry for a single chunk after Cross-Encoder reranking."""

    parent_chunk_id: uuid.UUID = Field(description="Parent Chunk UUID.")
    ticker: str = Field(description="Company stock ticker.")
    fiscal_year: int = Field(description="Fiscal year.")
    page_number: int = Field(ge=1, description="1-based PDF page number.")
    section_title: str | None = Field(default=None, description="Section or table title.")
    rank_before: int = Field(ge=1, description="1-based rank from Hybrid RRF before reranking.")
    rank_after: int = Field(ge=1, description="1-based rank after Cross-Encoder reranking.")
    rank_delta: int = Field(
        description="Rank improvement (rank_before - rank_after). Positive = promoted higher."
    )
    rrf_score: float = Field(ge=0.0, description="Initial Hybrid RRF score.")
    rerank_score: float = Field(ge=0.0, le=1.0, description="Cross-Encoder relevance score.")


class RerankComparisonReport(BaseModel):
    """Comprehensive comparison report of retrieval quality before vs. after reranking."""

    query: str = Field(description="Original search query.")
    reranker_model: str = Field(description="Identifier of the reranker model used.")
    input_count: int = Field(ge=0, description="Number of Hybrid RRF candidates before rerank.")
    output_count: int = Field(ge=0, description="Number of Top-K chunks retained after rerank.")
    top_1_changed: bool = Field(
        default=False,
        description="True if reranking promoted a different chunk to Rank 1.",
    )
    top_1_before_id: uuid.UUID | None = Field(default=None, description="Rank 1 UUID before rerank.")
    top_1_after_id: uuid.UUID | None = Field(default=None, description="Rank 1 UUID after rerank.")
    mean_rrf_score_before: float = Field(
        default=0.0,
        description="Mean RRF score of the initial Top-K candidates before reranking.",
    )
    mean_rerank_score_after: float = Field(
        default=0.0,
        description="Mean Cross-Encoder relevance score of the retained Top-K chunks.",
    )
    rank_changes: list[RerankRankChange] = Field(
        default_factory=list,
        description="Per-chunk rank transition details for the retained Top-K chunks.",
    )
    results: list[RetrievedChunk] = Field(
        default_factory=list,
        description="Final reranked Top-K RetrievedChunk objects with `rerank_score` populated.",
    )


__all__ = [
    "ROLE_ACCESS_MATRIX",
    "ChildChunkMatch",
    "RerankBatchOutput",
    "RerankCandidateScore",
    "RerankComparisonReport",
    "RerankRankChange",
    "RetrievalFilter",
    "RetrievedChunk",
    "parse_access_level",
    "parse_user_role",
    "resolve_allowed_access_levels",
]

