"""Pydantic v2 schemas and Decimal helpers for the Financial Calculator Tool (Task 3.2)."""

from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import MetricUnit
from app.schemas.sql_tool import FinancialMetricRecord

QUANTIZE_2DP = Decimal("0.01")
NumericInput = Decimal | float | int | str


class CalculationType(StrEnum):
    """Supported deterministic financial calculation operations."""

    YOY_GROWTH = "yoy_growth"
    CAGR = "cagr"
    GROSS_MARGIN = "gross_margin"
    NET_MARGIN = "net_margin"
    OPERATING_MARGIN = "operating_margin"
    DEBT_TO_EQUITY = "debt_to_equity"
    DEBT_TO_ASSETS = "debt_to_assets"
    ROE = "roe"
    ROA = "roa"
    STRUCTURE_WEIGHT = "structure_weight"


class CalculationStatus(StrEnum):
    """Execution outcome status for a financial calculation."""

    OK = "ok"
    DIVISION_BY_ZERO = "division_by_zero"
    INVALID_DOMAIN = "invalid_domain"
    INVALID_INPUT = "invalid_input"


def coerce_decimal(value: NumericInput | None) -> Decimal:
    """Safely convert int, float, str, or Decimal into a finite Decimal value."""
    if value is None or isinstance(value, bool):
        raise ValueError(f"Invalid numeric input: {value!r}")
    if isinstance(value, Decimal):
        dec = value
    elif isinstance(value, int):
        dec = Decimal(value)
    elif isinstance(value, float):
        dec = Decimal(str(value))
    elif isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Empty string cannot be parsed as a number.")
        try:
            dec = Decimal(cleaned)
        except InvalidOperation as exc:
            raise ValueError(f"Invalid numeric string: {value!r}") from exc
    else:
        raise TypeError(f"Unsupported numeric type: {type(value).__name__}")

    if not dec.is_finite():
        raise ValueError(f"Non-finite numeric value is not permitted: {value!r}")
    return dec


def round_2dp(value: NumericInput) -> Decimal:
    """Quantize a numeric value to exactly 2 decimal places using ROUND_HALF_UP."""
    try:
        dec = coerce_decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Cannot round invalid numeric value {value!r}: {exc}") from exc
    rounded = dec.quantize(QUANTIZE_2DP, rounding=ROUND_HALF_UP)
    # Normalize '-0.00' to '0.00' so financial reports never render negative zero.
    return Decimal("0.00") if rounded == 0 else rounded


class FinancialCalculationResult(BaseModel):
    """Structured output for a single deterministic Python financial calculation."""

    calculation_type: CalculationType = Field(
        description="Canonical identifier of the financial formula applied.",
    )
    status: CalculationStatus = Field(
        default=CalculationStatus.OK,
        description="Calculation status (ok, division_by_zero, invalid_domain, invalid_input).",
    )
    value: Decimal | None = Field(
        default=None,
        description="Calculated result rounded to 2 decimal places (None if undefined).",
    )
    unit: str = Field(
        default=MetricUnit.PERCENT.value,
        description="Unit of the calculated value (PERCENT or RATIO).",
    )
    absolute_change: Decimal | None = Field(
        default=None,
        description="Absolute change (current - previous) rounded to 2 decimal places for YoY.",
    )
    ratio_value: Decimal | None = Field(
        default=None,
        description="Raw decimal ratio (numerator / denominator) rounded to 2 decimal places.",
    )
    formula: str = Field(
        description="Human-readable mathematical formula used for full auditability.",
    )
    inputs: dict[str, float | int | str | None] = Field(
        default_factory=dict,
        description="Exact numeric inputs passed into the deterministic calculation.",
    )
    ticker: str | None = Field(default=None, description="Company ticker symbol if scoped.")
    fiscal_year: int | None = Field(default=None, description="Target fiscal year if scoped.")
    period: str | None = Field(default=None, description="Reporting period (FY, Q1..Q4).")
    metric_code: str | None = Field(default=None, description="Related canonical MetricCode.")
    warning: str | None = Field(
        default=None,
        description="Diagnostic explanation when status != ok or edge condition applies.",
    )
    citations: list[dict[str, object]] = Field(
        default_factory=list,
        description="Source document citations supporting the underlying numbers.",
    )

    @property
    def float_value(self) -> float | None:
        """Return calculated value as float for JSON/chart serialization, or None."""
        return float(self.value) if self.value is not None else None

    def to_agent_dict(self) -> dict[str, object]:
        """Serialize into a dictionary compatible with `AgentState['calculated_metrics']`."""
        return {
            "calculation_type": self.calculation_type.value,
            "status": self.status.value,
            "value": self.float_value,
            "value_str": str(self.value) if self.value is not None else None,
            "unit": self.unit,
            "absolute_change": float(self.absolute_change) if self.absolute_change is not None else None,
            "ratio_value": float(self.ratio_value) if self.ratio_value is not None else None,
            "formula": self.formula,
            "inputs": self.inputs,
            "ticker": self.ticker,
            "fiscal_year": self.fiscal_year,
            "period": self.period,
            "metric_code": self.metric_code,
            "warning": self.warning,
            "citations": self.citations,
        }


class FinancialCalculatorBatchResult(BaseModel):
    """Aggregated calculation results derived from SQL financial metric records."""

    calculations: list[FinancialCalculationResult] = Field(
        default_factory=list,
        description="Ordered list of all computed growth rates and financial ratios.",
    )
    by_ticker: dict[str, dict[str, object]] = Field(
        default_factory=dict,
        description="Nested summary grouped by ticker -> period -> metric/ratio.",
    )
    citations: list[dict[str, object]] = Field(
        default_factory=list,
        description="Deduplicated list of citations from all source SQL records.",
    )

    def to_state_dict(self) -> dict[str, object]:
        """Format batch output directly for `AgentState['calculated_metrics']`."""
        return {
            "calculations": [item.to_agent_dict() for item in self.calculations],
            "by_ticker": self.by_ticker,
            "citations": self.citations,
        }


def dedup_citations(items: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    """Deduplicate citation dicts by (doc_id, page_number) preserving first-seen order."""
    seen: set[tuple[str, int]] = set()
    unique: list[dict[str, object]] = []
    for item in items:
        key = (str(item.get("doc_id", "")), int(item.get("page_number", 0) or 0))
        if key not in seen:
            seen.add(key)
            unique.append(dict(item))
    return unique


def extract_metric_record(
    rec: FinancialMetricRecord | Mapping[str, Any],
) -> tuple[str, int, str, str, Decimal, dict[str, object]]:
    """Extract normalized (ticker, year, period, code, value, citation) from SQL rows/dicts."""
    if isinstance(rec, FinancialMetricRecord):
        return (
            rec.ticker, rec.fiscal_year, rec.period.value,
            rec.metric_code.value, rec.value, rec.to_citation_dict(),
        )
    ticker, year = str(rec["ticker"]).strip().upper(), int(rec["fiscal_year"])
    period = str(rec.get("period") or "FY").strip().upper()
    code = str(rec["metric_code"]).strip().upper()
    val = coerce_decimal(rec["value"])
    unit = str(rec.get("unit") or MetricUnit.VND_BILLION.value).strip().upper()
    page = int(rec.get("source_page") or rec.get("page_number") or 1)
    citation: dict[str, object] = {
        "doc_id": str(rec.get("document_id") or rec.get("doc_id") or ""),
        "doc_title": str(rec.get("doc_title") or ""),
        "ticker": ticker,
        "fiscal_year": year,
        "page_number": page,
        "snippet": f"{rec.get('metric_name_vi', code)} ({code}) {year} {period}: {val} {unit}",
    }
    return ticker, year, period, code, val, citation


def to_json_inputs(**kwargs: NumericInput | None) -> dict[str, float | int | str | None]:
    """Convert NumericInput kwargs into JSON-serializable scalar primitives."""
    return {
        k: (v if v is None or isinstance(v, (int, float, str)) else float(v))
        for k, v in kwargs.items()
    }


__all__ = [
    "QUANTIZE_2DP",
    "CalculationStatus",
    "CalculationType",
    "FinancialCalculationResult",
    "FinancialCalculatorBatchResult",
    "NumericInput",
    "coerce_decimal",
    "dedup_citations",
    "extract_metric_record",
    "round_2dp",
    "to_json_inputs",
]
