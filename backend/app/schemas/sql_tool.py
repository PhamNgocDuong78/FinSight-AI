"""Pydantic v2 schemas for the Structured SQL Query Tool (Task 3.1)."""

import uuid
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, Field, field_validator

from app.models.enums import (
    AccessLevel,
    FiscalPeriod,
    MetricCode,
    MetricUnit,
    StatementType,
    UserRole,
)
from app.schemas.financial_etl import QUANTIZE_4DP
from app.schemas.retrieval import parse_user_role, resolve_allowed_access_levels


def _normalize_str_list(values: str | Sequence[str] | None) -> list[str]:
    """Normalize a string or sequence of strings into unique uppercase tokens."""
    if values is None:
        return []
    raw = [values] if isinstance(values, str) else list(values)
    result: list[str] = []
    for item in raw:
        cleaned = str(item).strip().upper()
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


class SQLQuerySpec(BaseModel):
    """Normalized, RBAC-aware structured query parameters for `financial_metrics`."""

    question: str = Field(
        min_length=1,
        description="Original natural language quantitative question.",
    )
    user_role: UserRole = Field(
        default=UserRole.VIEWER,
        description="RBAC role of the user executing the query.",
    )
    allowed_access_levels: list[AccessLevel] = Field(
        default_factory=lambda: [AccessLevel.PUBLIC],
        description="Effective document access levels permitted by RBAC.",
    )
    tickers: list[str] = Field(
        default_factory=list,
        description="Target uppercase company stock tickers (e.g. ['FPT', 'VNM']).",
    )
    fiscal_years: list[int] = Field(
        default_factory=list,
        description="Target 4-digit fiscal years sorted ascending (e.g. [2022, 2023, 2024]).",
    )
    periods: list[FiscalPeriod] = Field(
        default_factory=list,
        description="Optional reporting periods to filter by (FY, Q1..Q4).",
    )
    metric_codes: list[MetricCode] = Field(
        default_factory=list,
        description="Standardized financial metric codes to retrieve.",
    )
    statement_types: list[StatementType] = Field(
        default_factory=list,
        description="Optional financial statement types to filter by.",
    )
    document_ids: list[uuid.UUID] = Field(
        default_factory=list,
        description="Optional specific document UUIDs to restrict the query to.",
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Maximum number of rows to return.",
    )

    @classmethod
    def from_inputs(
        cls,
        question: str,
        *,
        user_role: UserRole | str = UserRole.VIEWER,
        tickers: str | Sequence[str] | None = None,
        fiscal_years: int | Sequence[int] | None = None,
        periods: FiscalPeriod | str | Sequence[FiscalPeriod | str] | None = None,
        metric_codes: MetricCode | str | Sequence[MetricCode | str] | None = None,
        statement_types: StatementType | str | Sequence[StatementType | str] | None = None,
        access_levels: Sequence[AccessLevel | str] | None = None,
        document_ids: Sequence[uuid.UUID | str] | None = None,
        limit: int = 50,
    ) -> "SQLQuerySpec":
        """Build a validated SQLQuerySpec with strict RBAC access level resolution."""
        norm_role = parse_user_role(user_role)
        allowed_levels = resolve_allowed_access_levels(norm_role, access_levels)

        raw_years = (
            []
            if fiscal_years is None
            else ([fiscal_years] if isinstance(fiscal_years, int) else list(fiscal_years))
        )
        norm_years = sorted({int(y) for y in raw_years if 1990 <= int(y) <= 2100})

        raw_periods = (
            []
            if periods is None
            else ([periods] if isinstance(periods, (str, FiscalPeriod)) else list(periods))
        )
        norm_periods: list[FiscalPeriod] = []
        for p in raw_periods:
            p_enum = p if isinstance(p, FiscalPeriod) else FiscalPeriod(str(p).strip().upper())
            if p_enum not in norm_periods:
                norm_periods.append(p_enum)

        raw_codes = (
            []
            if metric_codes is None
            else (
                [metric_codes]
                if isinstance(metric_codes, (str, MetricCode))
                else list(metric_codes)
            )
        )
        norm_codes: list[MetricCode] = []
        for c in raw_codes:
            c_enum = c if isinstance(c, MetricCode) else MetricCode(str(c).strip().upper())
            if c_enum not in norm_codes:
                norm_codes.append(c_enum)

        raw_stmts = (
            []
            if statement_types is None
            else (
                [statement_types]
                if isinstance(statement_types, (str, StatementType))
                else list(statement_types)
            )
        )
        norm_stmts: list[StatementType] = []
        for s in raw_stmts:
            s_enum = (
                s if isinstance(s, StatementType) else StatementType(str(s).strip().lower())
            )
            if s_enum not in norm_stmts:
                norm_stmts.append(s_enum)

        norm_doc_ids = [
            uid if isinstance(uid, uuid.UUID) else uuid.UUID(str(uid))
            for uid in (document_ids or [])
        ]

        return cls(
            question=question.strip(),
            user_role=norm_role,
            allowed_access_levels=allowed_levels,
            tickers=_normalize_str_list(tickers),
            fiscal_years=norm_years,
            periods=norm_periods,
            metric_codes=norm_codes,
            statement_types=norm_stmts,
            document_ids=norm_doc_ids,
            limit=max(1, min(200, int(limit))),
        )


class LLMGeneratedSQLOutput(BaseModel):
    """Structured JSON schema returned by Gemini Text-to-SQL generator."""

    sql_query: str = Field(
        description=(
            "Read-only PostgreSQL SELECT statement querying `financial_metrics` "
            "(joined with `documents` for RBAC). Must start with SELECT and never "
            "contain DDL/DML, comments, semicolons, or unauthorized tables."
        ),
    )
    tickers: list[str] = Field(
        default_factory=list,
        description="Uppercase company stock tickers extracted from the question.",
    )
    fiscal_years: list[int] = Field(
        default_factory=list,
        description="All 4-digit fiscal years extracted (expanding ranges like 2022-2024).",
    )
    periods: list[FiscalPeriod] = Field(
        default_factory=list,
        description="Reporting periods explicitly mentioned (FY, Q1..Q4).",
    )
    metric_codes: list[MetricCode] = Field(
        default_factory=list,
        description="Canonical MetricCode identifiers corresponding to requested metrics.",
    )
    statement_types: list[StatementType] = Field(
        default_factory=list,
        description="Optional statement categories when querying an entire statement.",
    )
    rationale: str = Field(
        default="",
        description="Brief explanation of how the quantitative question mapped to SQL filters.",
    )


class FinancialMetricRecord(BaseModel):
    """Single structured financial metric record with unit and source page citation."""

    metric_id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        description="UUID of the row in `financial_metrics`.",
    )
    document_id: uuid.UUID = Field(
        description="UUID of the source PDF document in `documents`.",
    )
    doc_title: str = Field(
        default="",
        description="Title of the source financial report.",
    )
    company_name: str = Field(
        default="",
        description="Full company name.",
    )
    ticker: str = Field(
        description="Uppercase company stock ticker symbol (e.g. FPT, VNM).",
    )
    fiscal_year: int = Field(
        ge=1990,
        le=2100,
        description="4-digit fiscal year.",
    )
    period: FiscalPeriod = Field(
        default=FiscalPeriod.FY,
        description="Reporting period (FY, Q1..Q4).",
    )
    statement_type: StatementType = Field(
        description="Financial statement category.",
    )
    metric_code: MetricCode = Field(
        description="Standardized financial metric code.",
    )
    metric_name_vi: str = Field(
        description="Vietnamese metric name.",
    )
    metric_name_en: str = Field(
        description="English metric name.",
    )
    value: Decimal = Field(
        description="Exact numeric value quantized to 4 decimal places (Numeric(20, 4)).",
    )
    unit: MetricUnit = Field(
        description="Standardized measurement unit (e.g. VND_BILLION, PERCENT, VND).",
    )
    source_page: int = Field(
        ge=1,
        description="1-based page number in the original PDF report.",
    )
    access_level: AccessLevel = Field(
        default=AccessLevel.PUBLIC,
        description="RBAC classification level of the source document.",
    )

    @field_validator("value", mode="before")
    @classmethod
    def quantize_value(cls, value: Decimal | float | int | str) -> Decimal:
        """Normalize numeric value to 4 decimal places."""
        return Decimal(str(value)).quantize(QUANTIZE_4DP, rounding=ROUND_HALF_UP)

    def format_citation_tag(self) -> str:
        """Format inline citation badge as `[TICKER_AR_YEAR, tr. PAGE]`."""
        return f"[{self.ticker}_AR_{self.fiscal_year}, tr. {self.source_page}]"

    def to_citation_dict(self) -> dict[str, object]:
        """Serialize into the `CitationItem` structure defined in `docs/ARCHITECTURE.md`."""
        return {
            "doc_id": str(self.document_id),
            "doc_title": self.doc_title,
            "ticker": self.ticker,
            "fiscal_year": self.fiscal_year,
            "page_number": self.source_page,
            "snippet": (
                f"{self.metric_name_vi} ({self.metric_code.value}) "
                f"{self.fiscal_year} {self.period.value}: {self.value} {self.unit.value}"
            ),
        }

    def to_agent_dict(self) -> dict[str, object]:
        """Serialize into a dictionary suitable for `AgentState['sql_results']`."""
        return {
            "metric_id": str(self.metric_id),
            "document_id": str(self.document_id),
            "doc_title": self.doc_title,
            "company_name": self.company_name,
            "ticker": self.ticker,
            "fiscal_year": self.fiscal_year,
            "period": self.period.value,
            "statement_type": self.statement_type.value,
            "metric_code": self.metric_code.value,
            "metric_name_vi": self.metric_name_vi,
            "metric_name_en": self.metric_name_en,
            "value": float(self.value),
            "value_str": str(self.value),
            "unit": self.unit.value,
            "source_page": self.source_page,
            "access_level": self.access_level.value,
            "citation": self.format_citation_tag(),
        }


class SQLToolResult(BaseModel):
    """Structured execution result returned by `StructuredSQLQueryTool`."""

    question: str = Field(description="Original quantitative question.")
    sql_query: str = Field(description="Validated read-only SELECT query executed.")
    parameters: dict[str, object] = Field(
        default_factory=dict,
        description="Bound SQL parameters applied during execution.",
    )
    generator_model: str = Field(
        default="finsight-rule-sql-generator-v1",
        description="Identifier of the Text-to-SQL generator used.",
    )
    user_role: UserRole = Field(
        default=UserRole.VIEWER,
        description="RBAC role enforced during query execution.",
    )
    allowed_access_levels: list[AccessLevel] = Field(
        default_factory=list,
        description="Effective document access levels permitted for this query.",
    )
    row_count: int = Field(
        default=0,
        ge=0,
        description="Number of financial metric records returned.",
    )
    records: list[FinancialMetricRecord] = Field(
        default_factory=list,
        description="Structured financial metric rows with value, unit, and source_page.",
    )
    citations: list[dict[str, object]] = Field(
        default_factory=list,
        description="Deduplicated citation items corresponding to the returned metrics.",
    )


__all__ = [
    "FinancialMetricRecord",
    "LLMGeneratedSQLOutput",
    "SQLQuerySpec",
    "SQLToolResult",
]
