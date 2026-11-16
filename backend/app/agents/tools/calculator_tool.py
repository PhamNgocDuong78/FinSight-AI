"""Deterministic Python Financial Calculator Tool (Task 3.2) — zero-hallucination math."""

from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, NamedTuple

from fastapi import status

from app.core.exceptions import FinSightError
from app.core.logging import get_logger
from app.models.enums import MetricCode, MetricUnit
from app.schemas.calculator_tool import (
    CalculationStatus,
    CalculationType,
    FinancialCalculationResult,
    FinancialCalculatorBatchResult,
    NumericInput,
    coerce_decimal,
    dedup_citations,
    extract_metric_record,
    round_2dp,
    to_json_inputs,
)
from app.schemas.sql_tool import FinancialMetricRecord

logger = get_logger(__name__)
_PERCENT_CODES = frozenset({"ROE", "ROA", "GROSS_MARGIN", "NET_MARGIN"})
_NUM_EXCS = (InvalidOperation, ValueError, TypeError)


class FinancialCalculationError(FinSightError, ValueError):
    """Raised when a financial calculation encounters division by zero or an invalid domain."""

    def __init__(self, message: str, error_code: str = "FINANCIAL_CALCULATION_ERROR") -> None:
        super().__init__(message=message, status_code=status.HTTP_400_BAD_REQUEST, error_code=error_code)


class _CoreOutcome(NamedTuple):
    value: Decimal | None
    status: CalculationStatus
    warning: str | None
    ratio_value: Decimal | None = None
    absolute_change: Decimal | None = None


def _core_ratio(
    numerator: NumericInput, denominator: NumericInput, *, scale: Decimal = Decimal(100),
    allow_negative_den: bool = False, require_non_negative_num: bool = False,
    num_name: str = "numerator", den_name: str = "denominator", neg_den_warning: str | None = None,
) -> _CoreOutcome:
    num, den = coerce_decimal(numerator), coerce_decimal(denominator)
    if require_non_negative_num and num < 0:
        return _CoreOutcome(None, CalculationStatus.INVALID_DOMAIN, f"{num_name} cannot be negative ({num}).")
    if den == 0:
        return _CoreOutcome(None, CalculationStatus.DIVISION_BY_ZERO, f"Cannot divide by zero ({den_name} is 0).")
    if den < 0 and not allow_negative_den:
        return _CoreOutcome(None, CalculationStatus.INVALID_DOMAIN, f"{den_name} is negative ({den}); ratio is undefined.")
    return _CoreOutcome(round_2dp((num / den) * scale), CalculationStatus.OK, neg_den_warning if den < 0 else None, ratio_value=round_2dp(num / den))


class FinancialCalculatorTool:
    """Structured deterministic Python Financial Calculator for FinSight AI Agents."""

    def _wrap(
        self, calc_type: CalculationType, fn: Callable[[], _CoreOutcome], *, formula: str,
        inputs: dict[str, float | int | str | None], unit: str = MetricUnit.PERCENT.value,
        raise_on_error: bool = False, **meta: Any,
    ) -> FinancialCalculationResult:
        try:
            outcome = fn()
        except _NUM_EXCS as exc:
            outcome = _CoreOutcome(None, CalculationStatus.INVALID_INPUT, str(exc))
        if outcome.status != CalculationStatus.OK:
            logger.warning("calculator_tool_warning", calc_type=calc_type.value, status=outcome.status.value, reason=outcome.warning)
            if raise_on_error:
                raise FinancialCalculationError(outcome.warning or f"{calc_type.value} failed.")
        return FinancialCalculationResult(
            calculation_type=calc_type, status=outcome.status, value=outcome.value, unit=unit,
            absolute_change=outcome.absolute_change, ratio_value=outcome.ratio_value, formula=formula,
            inputs=inputs, ticker=meta.get("ticker"), fiscal_year=meta.get("fiscal_year"),
            period=meta.get("period"), metric_code=meta.get("metric_code"), warning=outcome.warning,
            citations=dedup_citations(list(meta.get("citations") or [])),
        )

    def _margin(self, calc_type: CalculationType, num_key: str, numerator: NumericInput, revenue: NumericInput, allow_negative_revenue: bool, raise_on_error: bool, meta: dict[str, Any]) -> FinancialCalculationResult:
        return self._wrap(calc_type, lambda: _core_ratio(numerator, revenue, allow_negative_den=allow_negative_revenue, den_name="revenue", neg_den_warning="Revenue is negative; interpret margin with caution."), formula=f"({num_key} / revenue) * 100", inputs=to_json_inputs(**{num_key: numerator, "revenue": revenue}), raise_on_error=raise_on_error, **meta)

    def _leverage(self, calc_type: CalculationType, den_key: str, total_liabilities: NumericInput, den_val: NumericInput, *, as_percentage: bool, allow_negative_den: bool, neg_warning: str | None, raise_on_error: bool, meta: dict[str, Any]) -> FinancialCalculationResult:
        scale, formula = (Decimal(100), f"(total_liabilities / {den_key}) * 100") if as_percentage else (Decimal(1), f"total_liabilities / {den_key}")
        return self._wrap(calc_type, lambda: _core_ratio(total_liabilities, den_val, scale=scale, allow_negative_den=allow_negative_den, require_non_negative_num=True, num_name="total_liabilities", den_name=den_key, neg_den_warning=neg_warning), formula=formula, inputs=to_json_inputs(total_liabilities=total_liabilities, **{den_key: den_val}), unit=MetricUnit.PERCENT.value if as_percentage else "RATIO", raise_on_error=raise_on_error, **meta)

    def _return_ratio(self, calc_type: CalculationType, net_income: NumericInput, current_base: NumericInput, previous_base: NumericInput | None, *, allow_negative_base: bool, cur_key: str, prev_key: str, raise_on_error: bool, meta: dict[str, Any]) -> FinancialCalculationResult:
        def _run() -> _CoreOutcome:
            cur = coerce_decimal(current_base)
            base = (cur + coerce_decimal(previous_base)) / Decimal(2) if previous_base is not None else cur
            return _core_ratio(net_income, base, allow_negative_den=allow_negative_base, den_name=cur_key, neg_den_warning=f"{cur_key} is negative; interpret return ratio with caution.")
        denom_lbl = f"avg_{cur_key.removeprefix('total_')}" if previous_base is not None else cur_key
        return self._wrap(calc_type, _run, formula=f"(net_income / {denom_lbl}) * 100", inputs=to_json_inputs(net_income=net_income, **{cur_key: current_base, prev_key: previous_base}), raise_on_error=raise_on_error, **meta)

    def yoy_growth(self, current_value: NumericInput, previous_value: NumericInput, *, use_abs_denominator: bool = True, raise_on_error: bool = False, **meta: Any) -> FinancialCalculationResult:
        """Compute Year-over-Year growth (%) with structured metadata."""
        def _run() -> _CoreOutcome:
            curr, prev = coerce_decimal(current_value), coerce_decimal(previous_value)
            abs_change = round_2dp(curr - prev)
            if prev == 0:
                return _CoreOutcome(None, CalculationStatus.DIVISION_BY_ZERO, "Cannot compute YoY growth when previous_value is 0.", absolute_change=abs_change)
            denom = abs(prev) if use_abs_denominator else prev
            warn = f"Previous value is negative; {'abs(previous_value)' if use_abs_denominator else 'signed previous_value'} denominator used." if prev < 0 else None
            return _CoreOutcome(round_2dp(((curr - prev) / denom) * Decimal(100)), CalculationStatus.OK, warn, absolute_change=abs_change)
        formula = "((current - previous) / abs(previous)) * 100" if use_abs_denominator else "((current - previous) / previous) * 100"
        return self._wrap(CalculationType.YOY_GROWTH, _run, formula=formula, inputs=to_json_inputs(current_value=current_value, previous_value=previous_value), raise_on_error=raise_on_error, **meta)

    def cagr(self, beginning_value: NumericInput, ending_value: NumericInput, num_periods: int | NumericInput, *, raise_on_error: bool = False, **meta: Any) -> FinancialCalculationResult:
        """Compute Compound Annual Growth Rate (%) with structured metadata."""
        def _run() -> _CoreOutcome:
            beg, end, n = coerce_decimal(beginning_value), coerce_decimal(ending_value), coerce_decimal(num_periods)
            if n <= 0:
                return _CoreOutcome(None, CalculationStatus.INVALID_DOMAIN, "num_periods must be > 0.")
            if beg == 0:
                return _CoreOutcome(None, CalculationStatus.DIVISION_BY_ZERO, "Cannot compute CAGR when beginning_value is 0.")
            if beg < 0 or end < 0:
                return _CoreOutcome(None, CalculationStatus.INVALID_DOMAIN, "CAGR is undefined in the real domain for negative values.")
            if end == 0:
                return _CoreOutcome(Decimal("-100.00"), CalculationStatus.OK, None, ratio_value=Decimal("0.00"))
            with localcontext() as ctx:
                ctx.prec = 28
                raw_ratio = end / beg
                return _CoreOutcome(round_2dp(((raw_ratio ** (Decimal(1) / n)) - Decimal(1)) * Decimal(100)), CalculationStatus.OK, None, ratio_value=round_2dp(raw_ratio))
        return self._wrap(CalculationType.CAGR, _run, formula="((ending / beginning) ** (1 / num_periods) - 1) * 100", inputs=to_json_inputs(beginning_value=beginning_value, ending_value=ending_value, num_periods=num_periods), raise_on_error=raise_on_error, **meta)

    def gross_margin(self, gross_profit: NumericInput | None = None, revenue: NumericInput = 0, *, cogs: NumericInput | None = None, allow_negative_revenue: bool = False, raise_on_error: bool = False, **meta: Any) -> FinancialCalculationResult:
        """Compute Gross Profit Margin (%) from gross_profit or (revenue - cogs)."""
        def _run() -> _CoreOutcome:
            if gross_profit is None and cogs is None:
                return _CoreOutcome(None, CalculationStatus.INVALID_INPUT, "Either gross_profit or cogs must be provided.")
            rev = coerce_decimal(revenue)
            gp = coerce_decimal(gross_profit) if gross_profit is not None else rev - coerce_decimal(cogs)
            return _core_ratio(gp, rev, allow_negative_den=allow_negative_revenue, den_name="revenue", neg_den_warning="Revenue is negative; interpret margin with caution.")
        formula = "(gross_profit / revenue) * 100" if gross_profit is not None else "((revenue - cogs) / revenue) * 100"
        return self._wrap(CalculationType.GROSS_MARGIN, _run, formula=formula, inputs=to_json_inputs(gross_profit=gross_profit, cogs=cogs, revenue=revenue), raise_on_error=raise_on_error, **meta)

    def net_margin(self, net_income: NumericInput, revenue: NumericInput, *, allow_negative_revenue: bool = False, raise_on_error: bool = False, **meta: Any) -> FinancialCalculationResult:
        """Compute Net Profit Margin (%) with structured metadata."""
        return self._margin(CalculationType.NET_MARGIN, "net_income", net_income, revenue, allow_negative_revenue, raise_on_error, meta)

    def operating_margin(self, operating_profit: NumericInput, revenue: NumericInput, *, allow_negative_revenue: bool = False, raise_on_error: bool = False, **meta: Any) -> FinancialCalculationResult:
        """Compute Operating Profit Margin (%) with structured metadata."""
        return self._margin(CalculationType.OPERATING_MARGIN, "operating_profit", operating_profit, revenue, allow_negative_revenue, raise_on_error, meta)

    def debt_to_equity(self, total_liabilities: NumericInput, total_equity: NumericInput, *, as_percentage: bool = True, allow_negative_equity: bool = True, raise_on_error: bool = False, **meta: Any) -> FinancialCalculationResult:
        """Compute Debt-to-Equity ratio (% or decimal) with insolvency warning support."""
        return self._leverage(CalculationType.DEBT_TO_EQUITY, "total_equity", total_liabilities, total_equity, as_percentage=as_percentage, allow_negative_den=allow_negative_equity, neg_warning="Total equity is negative (insolvency risk indicator).", raise_on_error=raise_on_error, meta=meta)

    def debt_to_assets(self, total_liabilities: NumericInput, total_assets: NumericInput, *, as_percentage: bool = True, raise_on_error: bool = False, **meta: Any) -> FinancialCalculationResult:
        """Compute Debt-to-Assets ratio (% or decimal) with structured metadata."""
        return self._leverage(CalculationType.DEBT_TO_ASSETS, "total_assets", total_liabilities, total_assets, as_percentage=as_percentage, allow_negative_den=False, neg_warning=None, raise_on_error=raise_on_error, meta=meta)

    def roe(self, net_income: NumericInput, total_equity: NumericInput, *, previous_equity: NumericInput | None = None, allow_negative_equity: bool = False, raise_on_error: bool = False, **meta: Any) -> FinancialCalculationResult:
        """Compute Return on Equity (%) using ending or 2-year average equity."""
        return self._return_ratio(CalculationType.ROE, net_income, total_equity, previous_equity, allow_negative_base=allow_negative_equity, cur_key="total_equity", prev_key="previous_equity", raise_on_error=raise_on_error, meta=meta)

    def roa(self, net_income: NumericInput, total_assets: NumericInput, *, previous_assets: NumericInput | None = None, allow_negative_assets: bool = False, raise_on_error: bool = False, **meta: Any) -> FinancialCalculationResult:
        """Compute Return on Assets (%) using ending or 2-year average assets."""
        return self._return_ratio(CalculationType.ROA, net_income, total_assets, previous_assets, allow_negative_base=allow_negative_assets, cur_key="total_assets", prev_key="previous_assets", raise_on_error=raise_on_error, meta=meta)

    def structure_weight(self, part_value: NumericInput, total_value: NumericInput, *, raise_on_error: bool = False, **meta: Any) -> FinancialCalculationResult:
        """Compute component percentage share (%) with structured metadata."""
        return self._wrap(CalculationType.STRUCTURE_WEIGHT, lambda: _core_ratio(part_value, total_value, allow_negative_den=True, den_name="total_value"), formula="(part_value / total_value) * 100", inputs=to_json_inputs(part_value=part_value, total_value=total_value), raise_on_error=raise_on_error, **meta)

    def compute_from_sql_records(self, records: Sequence[FinancialMetricRecord | Mapping[str, Any]], *, use_average_balance: bool = False) -> FinancialCalculatorBatchResult:
        """Automatically derive YoY growth, CAGR, margins, debt ratios, and ROE/ROA from SQL rows."""
        by_metric: dict[tuple[str, str, str], dict[int, tuple[Decimal, dict[str, object]]]] = {}
        by_year: dict[tuple[str, str, int], dict[str, tuple[Decimal, dict[str, object]]]] = {}
        all_citations: list[dict[str, object]] = []
        for rec in records:
            ticker, year, period, code, val, cit = extract_metric_record(rec)
            by_metric.setdefault((ticker, period, code), {})[year] = (val, cit)
            by_year.setdefault((ticker, period, year), {})[code] = (val, cit)
            all_citations.append(cit)
        calculations, summary = [], {}
        for (ticker, period, code), yv in sorted(by_metric.items()):
            years = sorted(yv)
            if code in _PERCENT_CODES or len(years) < 2:
                continue
            bucket = summary.setdefault(ticker, {}).setdefault(period, {})
            for i in range(1, len(years)):
                yp, yc = years[i - 1], years[i]
                res = self.yoy_growth(yv[yc][0], yv[yp][0], ticker=ticker, fiscal_year=yc, period=period, metric_code=code, citations=[yv[yp][1], yv[yc][1]])
                calculations.append(res)
                bucket.setdefault("yoy_growth", {}).setdefault(code, {})[f"{yp}-{yc}"] = res.float_value
            ys, ye = years[0], years[-1]
            cagr_res = self.cagr(yv[ys][0], yv[ye][0], ye - ys, ticker=ticker, fiscal_year=ye, period=period, metric_code=code, citations=[yv[ys][1], yv[ye][1]])
            calculations.append(cagr_res)
            bucket.setdefault("cagr", {}).setdefault(code, {})[f"{ys}-{ye}"] = cagr_res.float_value
        pair_specs = (
            ("NET_MARGIN", self.net_margin, MetricCode.NET_INCOME.value, MetricCode.REVENUE.value, MetricCode.NET_MARGIN.value),
            ("OPERATING_MARGIN", self.operating_margin, MetricCode.OPERATING_PROFIT.value, MetricCode.REVENUE.value, "OPERATING_MARGIN"),
            ("DEBT_TO_EQUITY", self.debt_to_equity, MetricCode.TOTAL_LIABILITIES.value, MetricCode.TOTAL_EQUITY.value, "DEBT_TO_EQUITY"),
            ("DEBT_TO_ASSETS", self.debt_to_assets, MetricCode.TOTAL_LIABILITIES.value, MetricCode.TOTAL_ASSETS.value, "DEBT_TO_ASSETS"),
            ("CURRENT_ASSETS_SHARE", self.structure_weight, MetricCode.CURRENT_ASSETS.value, MetricCode.TOTAL_ASSETS.value, MetricCode.CURRENT_ASSETS.value),
            ("NON_CURRENT_ASSETS_SHARE", self.structure_weight, MetricCode.NON_CURRENT_ASSETS.value, MetricCode.TOTAL_ASSETS.value, MetricCode.NON_CURRENT_ASSETS.value),
        )
        for (ticker, period, year), m in sorted(by_year.items()):
            rb = summary.setdefault(ticker, {}).setdefault(period, {}).setdefault("ratios", {}).setdefault(str(year), {})
            prev_m = by_year.get((ticker, period, year - 1), {}) if use_average_balance else {}
            items: list[tuple[str, FinancialCalculationResult]] = []
            if MetricCode.REVENUE.value in m and (MetricCode.GROSS_PROFIT.value in m or MetricCode.COGS.value in m):
                rev, gp, cogs = m[MetricCode.REVENUE.value], m.get(MetricCode.GROSS_PROFIT.value), m.get(MetricCode.COGS.value)
                items.append(("GROSS_MARGIN", self.gross_margin(gp[0] if gp else None, rev[0], cogs=cogs[0] if cogs else None, ticker=ticker, fiscal_year=year, period=period, metric_code=MetricCode.GROSS_MARGIN.value, citations=[rev[1]] + ([gp[1]] if gp else []) + ([cogs[1]] if cogs else []))))
            for r_key, fn, num_c, den_c, m_c in pair_specs:
                if num_c in m and den_c in m:
                    items.append((r_key, fn(m[num_c][0], m[den_c][0], ticker=ticker, fiscal_year=year, period=period, metric_code=m_c, citations=[m[num_c][1], m[den_c][1]])))
            for r_key, fn, base_c, prev_kw, m_c in (("ROE", self.roe, MetricCode.TOTAL_EQUITY.value, "previous_equity", MetricCode.ROE.value), ("ROA", self.roa, MetricCode.TOTAL_ASSETS.value, "previous_assets", MetricCode.ROA.value)):
                if MetricCode.NET_INCOME.value in m and base_c in m:
                    ni, base, prev_b = m[MetricCode.NET_INCOME.value], m[base_c], prev_m.get(base_c)
                    items.append((r_key, fn(ni[0], base[0], **({prev_kw: prev_b[0]} if prev_b else {}), ticker=ticker, fiscal_year=year, period=period, metric_code=m_c, citations=[ni[1], base[1]] + ([prev_b[1]] if prev_b else []))))
            for r_key, calc_res in items:
                calculations.append(calc_res)
                rb[r_key] = calc_res.float_value
        return FinancialCalculatorBatchResult(calculations=calculations, by_ticker=summary, citations=dedup_citations(all_citations))


_DEFAULT_TOOL = FinancialCalculatorTool()


def calculate_yoy_growth(current_value: NumericInput, previous_value: NumericInput, *, use_abs_denominator: bool = True, raise_on_error: bool = False) -> Decimal | None:
    """Compute Year-over-Year growth (%) rounded to 2 decimal places."""
    return _DEFAULT_TOOL.yoy_growth(current_value, previous_value, use_abs_denominator=use_abs_denominator, raise_on_error=raise_on_error).value


def calculate_cagr(beginning_value: NumericInput, ending_value: NumericInput, num_periods: int | NumericInput, *, raise_on_error: bool = False) -> Decimal | None:
    """Compute Compound Annual Growth Rate (%) rounded to 2 decimal places."""
    return _DEFAULT_TOOL.cagr(beginning_value, ending_value, num_periods, raise_on_error=raise_on_error).value


def calculate_gross_margin(gross_profit: NumericInput | None = None, revenue: NumericInput = 0, *, cogs: NumericInput | None = None, allow_negative_revenue: bool = False, raise_on_error: bool = False) -> Decimal | None:
    """Compute Gross Profit Margin (%) from gross_profit (or revenue - cogs) and revenue."""
    return _DEFAULT_TOOL.gross_margin(gross_profit, revenue, cogs=cogs, allow_negative_revenue=allow_negative_revenue, raise_on_error=raise_on_error).value


def calculate_net_margin(net_income: NumericInput, revenue: NumericInput, *, allow_negative_revenue: bool = False, raise_on_error: bool = False) -> Decimal | None:
    """Compute Net Profit Margin (%) rounded to 2 decimal places."""
    return _DEFAULT_TOOL.net_margin(net_income, revenue, allow_negative_revenue=allow_negative_revenue, raise_on_error=raise_on_error).value


def calculate_operating_margin(operating_profit: NumericInput, revenue: NumericInput, *, allow_negative_revenue: bool = False, raise_on_error: bool = False) -> Decimal | None:
    """Compute Operating Profit Margin (%) rounded to 2 decimal places."""
    return _DEFAULT_TOOL.operating_margin(operating_profit, revenue, allow_negative_revenue=allow_negative_revenue, raise_on_error=raise_on_error).value


def calculate_debt_to_equity(total_liabilities: NumericInput, total_equity: NumericInput, *, as_percentage: bool = True, allow_negative_equity: bool = True, raise_on_error: bool = False) -> Decimal | None:
    """Compute Debt-to-Equity ratio (% or decimal) rounded to 2 decimal places."""
    return _DEFAULT_TOOL.debt_to_equity(total_liabilities, total_equity, as_percentage=as_percentage, allow_negative_equity=allow_negative_equity, raise_on_error=raise_on_error).value


def calculate_debt_to_assets(total_liabilities: NumericInput, total_assets: NumericInput, *, as_percentage: bool = True, raise_on_error: bool = False) -> Decimal | None:
    """Compute Debt-to-Assets ratio (% or decimal) rounded to 2 decimal places."""
    return _DEFAULT_TOOL.debt_to_assets(total_liabilities, total_assets, as_percentage=as_percentage, raise_on_error=raise_on_error).value


calculate_debt_ratio = calculate_debt_to_assets


def calculate_roe(net_income: NumericInput, total_equity: NumericInput, *, previous_equity: NumericInput | None = None, allow_negative_equity: bool = False, raise_on_error: bool = False) -> Decimal | None:
    """Compute Return on Equity (%) using ending or 2-year average equity."""
    return _DEFAULT_TOOL.roe(net_income, total_equity, previous_equity=previous_equity, allow_negative_equity=allow_negative_equity, raise_on_error=raise_on_error).value


def calculate_roa(net_income: NumericInput, total_assets: NumericInput, *, previous_assets: NumericInput | None = None, allow_negative_assets: bool = False, raise_on_error: bool = False) -> Decimal | None:
    """Compute Return on Assets (%) using ending or 2-year average assets."""
    return _DEFAULT_TOOL.roa(net_income, total_assets, previous_assets=previous_assets, allow_negative_assets=allow_negative_assets, raise_on_error=raise_on_error).value


def calculate_structure_weight(part_value: NumericInput, total_value: NumericInput, *, raise_on_error: bool = False) -> Decimal | None:
    """Compute percentage share (part / total * 100) rounded to 2 decimal places."""
    return _DEFAULT_TOOL.structure_weight(part_value, total_value, raise_on_error=raise_on_error).value


def calculate_structure_breakdown(components: Mapping[str, NumericInput], total_value: NumericInput | None = None, *, raise_on_error: bool = False) -> dict[str, Decimal | None]:
    """Compute percentage structure weight for every named component."""
    if not components:
        return {}
    try:
        coerced = {name: coerce_decimal(val) for name, val in components.items()}
        effective_total = coerce_decimal(total_value) if total_value is not None else sum(coerced.values(), Decimal(0))
    except _NUM_EXCS as exc:
        if raise_on_error:
            raise FinancialCalculationError(str(exc)) from exc
        return {name: None for name in components}
    return {name: calculate_structure_weight(val, effective_total, raise_on_error=raise_on_error) for name, val in coerced.items()}
