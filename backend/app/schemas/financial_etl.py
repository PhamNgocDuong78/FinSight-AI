"""Pydantic v2 schemas for Structured Financial Metric Extraction (Financial ETL)."""

import uuid
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, Field, field_validator

from app.models.enums import FiscalPeriod, MetricCode, MetricUnit, StatementType

QUANTIZE_4DP = Decimal("0.0001")


class ExtractedFinancialMetricItem(BaseModel):
    """Single financial indicator returned by Gemini Structured Output."""

    metric_code: MetricCode = Field(
        description=(
            "Standardized metric code (REVENUE, COGS, GROSS_PROFIT, OPERATING_PROFIT, "
            "PROFIT_BEFORE_TAX, NET_INCOME, EPS, CURRENT_ASSETS, NON_CURRENT_ASSETS, "
            "TOTAL_ASSETS, TOTAL_LIABILITIES, TOTAL_EQUITY, ROE, ROA, GROSS_MARGIN, "
            "NET_MARGIN, OPERATING_CASH_FLOW)."
        ),
    )
    metric_name_vi: str = Field(
        description="Vietnamese metric name as shown in the financial report.",
    )
    metric_name_en: str = Field(
        description="Standard English metric name.",
    )
    statement_type: StatementType = Field(
        description="Financial statement category (income_statement, balance_sheet, cash_flow, ratios).",
    )
    fiscal_year: int = Field(
        ge=1990,
        le=2100,
        description="4-digit fiscal year of this specific numeric value (e.g. 2024, 2023).",
    )
    period: FiscalPeriod = Field(
        default=FiscalPeriod.FY,
        description="Reporting period (FY, Q1, Q2, Q3, Q4).",
    )
    value: float = Field(
        description=(
            "Exact numeric value without thousand separators. In Vietnamese tables, "
            "dots followed by 3 digits (e.g. 62.849) are thousand separators (= 62849)."
        ),
    )
    unit: MetricUnit = Field(
        description="Measurement unit (VND_BILLION, VND_MILLION, VND, USD_BILLION, USD_MILLION, USD, PERCENT).",
    )
    source_page: int = Field(
        ge=1,
        description="1-based page number where the table or figure appears.",
    )


class FinancialTableExtractionOutput(BaseModel):
    """Top-level Pydantic schema for Gemini Structured Output (`response_format`)."""

    metrics: list[ExtractedFinancialMetricItem] = Field(
        default_factory=list,
        description="List of standardized financial metrics extracted from the Markdown content.",
    )


class NormalizedFinancialMetric(BaseModel):
    """Validated and normalized financial metric ready for PostgreSQL persistence."""

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        description="Primary key UUID for financial_metrics row.",
    )
    document_id: uuid.UUID = Field(
        description="Foreign key UUID referencing documents.id.",
    )
    ticker: str = Field(
        min_length=1,
        max_length=32,
        description="Stock ticker symbol (e.g. FPT, VNM, AAPL).",
    )
    fiscal_year: int = Field(
        ge=1990,
        le=2100,
        description="Fiscal year of the metric.",
    )
    period: FiscalPeriod = Field(
        default=FiscalPeriod.FY,
        description="Fiscal period (FY, Q1..Q4).",
    )
    statement_type: StatementType = Field(
        description="Statement category.",
    )
    metric_code: MetricCode = Field(
        description="Standardized metric code.",
    )
    metric_name_vi: str = Field(
        min_length=1,
        max_length=255,
        description="Vietnamese name of the metric.",
    )
    metric_name_en: str = Field(
        min_length=1,
        max_length=255,
        description="English name of the metric.",
    )
    value: Decimal = Field(
        description="Exact numeric value quantized to 4 decimal places (Numeric(20, 4)).",
    )
    unit: MetricUnit = Field(
        description="Measurement unit.",
    )
    source_page: int = Field(
        ge=1,
        description="1-based page number in the source PDF.",
    )

    @field_validator("ticker", mode="before")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        """Normalize stock ticker to uppercase trimmed string."""
        return str(value).strip().upper()

    @field_validator("value", mode="before")
    @classmethod
    def quantize_decimal_value(cls, value: Decimal | float | str) -> Decimal:
        """Convert numeric input to Decimal quantized to 4 decimal places."""
        dec = Decimal(str(value))
        return dec.quantize(QUANTIZE_4DP, rounding=ROUND_HALF_UP)


class FinancialETLResult(BaseModel):
    """Summary result of running the Financial ETL pipeline on a document."""

    document_id: uuid.UUID = Field(description="UUID of the processed document.")
    ticker: str = Field(description="Company stock ticker.")
    fiscal_year: int = Field(description="Primary fiscal year of the document.")
    period: FiscalPeriod = Field(default=FiscalPeriod.FY, description="Primary reporting period.")
    metrics: list[NormalizedFinancialMetric] = Field(
        default_factory=list,
        description="All validated and normalized financial metrics extracted.",
    )
    persisted_count: int = Field(
        default=0,
        ge=0,
        description="Number of rows persisted to PostgreSQL financial_metrics.",
    )

    def get_metric(
        self,
        metric_code: MetricCode | str,
        fiscal_year: int | None = None,
        period: FiscalPeriod | str | None = None,
    ) -> NormalizedFinancialMetric | None:
        """Look up a specific metric by code and optional fiscal_year / period."""
        target_code = str(metric_code)
        target_year = fiscal_year if fiscal_year is not None else self.fiscal_year
        target_period = str(period) if period is not None else None

        for item in self.metrics:
            if item.metric_code.value != target_code:
                continue
            if item.fiscal_year != target_year:
                continue
            if target_period is not None and item.period.value != target_period:
                continue
            return item
        return None
