"""Unit tests for Task 3.2: Deterministic Python Financial Calculator Tool."""

import uuid
from decimal import Decimal

import pytest

from app.agents.tools.calculator_tool import (
    FinancialCalculationError,
    FinancialCalculatorTool,
    calculate_cagr,
    calculate_debt_ratio,
    calculate_debt_to_assets,
    calculate_debt_to_equity,
    calculate_gross_margin,
    calculate_net_margin,
    calculate_operating_margin,
    calculate_roa,
    calculate_roe,
    calculate_structure_breakdown,
    calculate_structure_weight,
    calculate_yoy_growth,
)
from app.models.enums import (
    AccessLevel,
    FiscalPeriod,
    MetricCode,
    MetricUnit,
    StatementType,
)
from app.schemas.calculator_tool import (
    CalculationStatus,
    CalculationType,
    coerce_decimal,
    round_2dp,
)
from app.schemas.sql_tool import FinancialMetricRecord


def test_rounding_half_up_and_scalar_coercion() -> None:
    """Verify exact 2-decimal ROUND_HALF_UP behavior and invalid input rejection."""
    assert round_2dp("1.005") == Decimal("1.01")
    assert round_2dp("-1.005") == Decimal("-1.01")
    assert round_2dp(Decimal("0.125")) == Decimal("0.13")
    assert round_2dp(Decimal("-0.004")) == Decimal("0.00")
    assert str(round_2dp("-0.0001")) == "0.00"
    assert coerce_decimal(" 62849.1250 ") == Decimal("62849.1250")

    for bad_val in (None, True, False, "", "   ", "not-a-number", float("nan"), float("inf")):
        with pytest.raises((ValueError, TypeError)):
            coerce_decimal(bad_val)  # type: ignore[arg-type]


def test_yoy_growth_fpt_numbers_and_edge_cases() -> None:
    """Verify YoY growth on FPT revenue/net income, sign flips, deeper losses, and zero base."""
    assert calculate_yoy_growth(52618, 44010) == Decimal("19.56")
    assert calculate_yoy_growth(62849, 52618) == Decimal("19.44")
    assert calculate_yoy_growth(7788, 6491) == Decimal("19.98")
    assert calculate_yoy_growth(9427, 7788) == Decimal("21.05")

    assert calculate_yoy_growth(75, 100) == Decimal("-25.00")
    assert calculate_yoy_growth(-50, 100) == Decimal("-150.00")
    assert calculate_yoy_growth(50, -100, use_abs_denominator=True) == Decimal("150.00")
    assert calculate_yoy_growth(50, -100, use_abs_denominator=False) == Decimal("-150.00")
    assert calculate_yoy_growth(-150, -100, use_abs_denominator=True) == Decimal("-50.00")
    assert calculate_yoy_growth(-40, -100, use_abs_denominator=True) == Decimal("60.00")

    assert calculate_yoy_growth(100, 0) is None
    with pytest.raises(FinancialCalculationError):
        calculate_yoy_growth(100, 0, raise_on_error=True)

    tool = FinancialCalculatorTool()
    zero_res = tool.yoy_growth(150, 0)
    assert zero_res.status == CalculationStatus.DIVISION_BY_ZERO
    assert zero_res.value is None
    assert zero_res.absolute_change == Decimal("150.00")
    assert zero_res.warning is not None


def test_cagr_fpt_and_domain_edge_cases() -> None:
    """Verify multi-year CAGR, decline, total wipeout, zero base, invalid periods, and negatives."""
    assert calculate_cagr(44010, 62849, 2) == Decimal("19.50")
    assert calculate_cagr(6491, 9427, 2) == Decimal("20.51")
    assert calculate_cagr(1000, 810, 2) == Decimal("-10.00")
    assert calculate_cagr(100, 0, 3) == Decimal("-100.00")

    assert calculate_cagr(0, 200, 2) is None
    assert calculate_cagr(100, 200, 0) is None
    assert calculate_cagr(100, 200, -2) is None
    assert calculate_cagr(-100, 200, 2) is None
    assert calculate_cagr(100, -50, 2) is None
    assert calculate_cagr(-100, -20, 2) is None

    for args in ((0, 200, 2), (100, 200, 0), (-100, 200, 2)):
        with pytest.raises(FinancialCalculationError):
            calculate_cagr(*args, raise_on_error=True)

    tool = FinancialCalculatorTool()
    assert tool.cagr(0, 200, 2).status == CalculationStatus.DIVISION_BY_ZERO
    assert tool.cagr(-100, 200, 2).status == CalculationStatus.INVALID_DOMAIN
    assert tool.cagr(100, 200, 0).status == CalculationStatus.INVALID_DOMAIN


def test_profit_margins_and_zero_or_negative_revenue() -> None:
    """Verify gross, net, and operating margins including COGS fallback, losses, and zero/neg revenue."""
    assert calculate_gross_margin(23882.62, 62849) == Decimal("38.00")
    assert calculate_gross_margin(None, 62849, cogs=38966.38) == Decimal("38.00")
    assert calculate_net_margin(9427, 62849) == Decimal("15.00")
    assert calculate_operating_margin(11000, 62849) == Decimal("17.50")

    # Negative profit (loss) with positive revenue
    assert calculate_gross_margin(-50, 200) == Decimal("-25.00")
    assert calculate_net_margin(-35.555, 100) == Decimal("-35.56")
    assert calculate_operating_margin(-15, 150) == Decimal("-10.00")

    # Zero revenue -> None / DIVISION_BY_ZERO
    assert calculate_gross_margin(100, 0) is None
    assert calculate_net_margin(100, 0) is None
    assert calculate_operating_margin(100, 0) is None
    with pytest.raises(FinancialCalculationError):
        calculate_net_margin(100, 0, raise_on_error=True)

    # Negative revenue -> None by default, computable when explicitly allowed
    assert calculate_net_margin(20, -100) is None
    assert calculate_net_margin(20, -100, allow_negative_revenue=True) == Decimal("-20.00")

    # Missing both gross_profit and cogs -> None / INVALID_INPUT
    assert calculate_gross_margin(None, 100) is None
    assert FinancialCalculatorTool().gross_margin(None, 100).status == CalculationStatus.INVALID_INPUT


def test_debt_ratios_negative_equity_and_zero_denominators() -> None:
    """Verify Debt-to-Equity and Debt-to-Assets in percentage and ratio modes plus edge cases."""
    assert calculate_debt_to_equity(32000, 36000) == Decimal("88.89")
    assert calculate_debt_to_equity(32000, 36000, as_percentage=False) == Decimal("0.89")
    assert calculate_debt_to_assets(32000, 68000) == Decimal("47.06")
    assert calculate_debt_ratio(32000, 68000, as_percentage=False) == Decimal("0.47")

    # Zero denominators -> None
    assert calculate_debt_to_equity(32000, 0) is None
    assert calculate_debt_to_assets(32000, 0) is None

    # Negative equity (insolvency warning by default, or blocked if allow_negative_equity=False)
    assert calculate_debt_to_equity(300, -100) == Decimal("-300.00")
    assert calculate_debt_to_equity(300, -100, allow_negative_equity=False) is None
    tool = FinancialCalculatorTool()
    neg_eq_res = tool.debt_to_equity(300, -100)
    assert neg_eq_res.status == CalculationStatus.OK
    assert neg_eq_res.ratio_value == Decimal("-3.00")
    assert neg_eq_res.warning is not None and "insolvency" in neg_eq_res.warning.lower()

    # Negative liabilities or negative assets -> None / INVALID_DOMAIN
    assert calculate_debt_to_equity(-50, 100) is None
    assert calculate_debt_to_assets(-50, 100) is None
    assert calculate_debt_to_assets(50, -100) is None


def test_roe_and_roa_with_averages_losses_and_double_negative_guard() -> None:
    """Verify ROE/ROA single-year, 2-year average, negative income, zero denominator, and double-negative trap."""
    assert calculate_roe(9427, 35921) == Decimal("26.24")
    assert calculate_roe(9427, 35921, previous_equity=29933) == Decimal("28.63")
    assert calculate_roa(9427, 72000) == Decimal("13.09")
    assert calculate_roa(9427, 72000, previous_assets=60349) == Decimal("14.25")

    # Net loss with positive equity / assets
    assert calculate_roe(-125, 500) == Decimal("-25.00")
    assert calculate_roa(-125, 1000) == Decimal("-12.50")

    # Zero equity / assets (including averaging to zero: (100 + -100) / 2 == 0)
    assert calculate_roe(100, 0) is None
    assert calculate_roa(100, 0) is None
    assert calculate_roe(100, 100, previous_equity=-100) is None
    assert calculate_roa(100, 100, previous_assets=-100) is None

    # Double-negative guard: loss-making company with negative equity must NOT yield +20.00% ROE
    assert calculate_roe(-100, -500) is None
    assert calculate_roa(-100, -500) is None
    assert calculate_roe(-100, -500, allow_negative_equity=True) == Decimal("20.00")


def test_structure_weight_and_breakdown() -> None:
    """Verify structure weight percentages, total=0 guard, and invalid input handling."""
    assert calculate_structure_weight(42000, 72000) == Decimal("58.33")
    assert calculate_structure_weight(30000, 72000) == Decimal("41.67")
    assert calculate_structure_weight(100, 0) is None

    breakdown = calculate_structure_breakdown({"CURRENT_ASSETS": 42000, "NON_CURRENT_ASSETS": 30000})
    assert breakdown == {"CURRENT_ASSETS": Decimal("58.33"), "NON_CURRENT_ASSETS": Decimal("41.67")}
    assert sum(v for v in breakdown.values() if v is not None) == Decimal("100.00")
    assert calculate_structure_breakdown({}) == {}
    assert calculate_structure_breakdown({"A": 0, "B": 0}) == {"A": None, "B": None}
    assert calculate_structure_breakdown({"A": "bad-num"}) == {"A": None}
    with pytest.raises(FinancialCalculationError):
        calculate_structure_breakdown({"A": "bad-num"}, raise_on_error=True)


def test_batch_compute_from_sql_records_and_citations() -> None:
    """Verify FinancialCalculatorTool batch processing over SQL FinancialMetricRecord items."""
    doc_2022, doc_2023, doc_2024 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    def _rec(doc_id: uuid.UUID, year: int, code: MetricCode, val: str, page: int) -> FinancialMetricRecord:
        stmt = (
            StatementType.BALANCE_SHEET
            if code in {MetricCode.TOTAL_ASSETS, MetricCode.TOTAL_LIABILITIES, MetricCode.TOTAL_EQUITY, MetricCode.CURRENT_ASSETS}
            else StatementType.INCOME_STATEMENT
        )
        return FinancialMetricRecord(
            document_id=doc_id,
            doc_title=f"Báo cáo Thường niên FPT {year}",
            company_name="Công ty Cổ phần FPT",
            ticker="FPT",
            fiscal_year=year,
            period=FiscalPeriod.FY,
            statement_type=stmt,
            metric_code=code,
            metric_name_vi=code.value,
            metric_name_en=code.value,
            value=Decimal(val),
            unit=MetricUnit.VND_BILLION,
            source_page=page,
            access_level=AccessLevel.PUBLIC,
        )

    records = [
        _rec(doc_2022, 2022, MetricCode.REVENUE, "44010", 42),
        _rec(doc_2023, 2023, MetricCode.REVENUE, "52618", 44),
        _rec(doc_2024, 2024, MetricCode.REVENUE, "62849", 45),
        _rec(doc_2024, 2024, MetricCode.GROSS_PROFIT, "23882.62", 45),
        _rec(doc_2022, 2022, MetricCode.NET_INCOME, "6491", 42),
        _rec(doc_2023, 2023, MetricCode.NET_INCOME, "7788", 44),
        _rec(doc_2024, 2024, MetricCode.NET_INCOME, "9427", 45),
        _rec(doc_2023, 2023, MetricCode.TOTAL_EQUITY, "29933", 48),
        _rec(doc_2024, 2024, MetricCode.TOTAL_EQUITY, "35921", 49),
        _rec(doc_2024, 2024, MetricCode.TOTAL_LIABILITIES, "36079", 49),
        _rec(doc_2024, 2024, MetricCode.TOTAL_ASSETS, "72000", 48),
        _rec(doc_2024, 2024, MetricCode.CURRENT_ASSETS, "42000", 48),
    ]

    tool = FinancialCalculatorTool()
    batch = tool.compute_from_sql_records(records)
    fpt_fy = batch.by_ticker["FPT"]["FY"]

    assert fpt_fy["yoy_growth"]["REVENUE"] == {"2022-2023": 19.56, "2023-2024": 19.44}
    assert fpt_fy["cagr"]["REVENUE"] == {"2022-2024": 19.5}
    assert fpt_fy["yoy_growth"]["NET_INCOME"] == {"2022-2023": 19.98, "2023-2024": 21.05}
    assert fpt_fy["cagr"]["NET_INCOME"] == {"2022-2024": 20.51}

    ratios_2024 = fpt_fy["ratios"]["2024"]
    assert ratios_2024["GROSS_MARGIN"] == 38.0
    assert ratios_2024["NET_MARGIN"] == 15.0
    assert ratios_2024["DEBT_TO_EQUITY"] == 100.44
    assert ratios_2024["DEBT_TO_ASSETS"] == 50.11
    assert ratios_2024["ROE"] == 26.24
    assert ratios_2024["ROA"] == 13.09
    assert ratios_2024["CURRENT_ASSETS_SHARE"] == 58.33

    # Verify average-balance mode for ROE 2024 using 2023 & 2024 equity
    avg_batch = tool.compute_from_sql_records(records, use_average_balance=True)
    assert avg_batch.by_ticker["FPT"]["FY"]["ratios"]["2024"]["ROE"] == 28.63

    # Verify dict input compatibility & citation preservation / deduplication
    dict_batch = tool.compute_from_sql_records([r.to_agent_dict() for r in records])
    assert dict_batch.to_state_dict()["by_ticker"]["FPT"]["FY"]["ratios"]["2024"]["NET_MARGIN"] == 15.0
    assert len(batch.citations) == 6  # 12 records deduplicated to 6 unique (doc_id, page_number) pairs
    assert any(c["doc_id"] == str(doc_2024) and c["page_number"] == 45 for c in batch.citations)
    assert any(c["doc_id"] == str(doc_2024) and c["page_number"] == 49 for c in batch.citations)

    calc_types = {c.calculation_type for c in batch.calculations}
    assert CalculationType.YOY_GROWTH in calc_types
    assert CalculationType.CAGR in calc_types
    assert CalculationType.ROE in calc_types
