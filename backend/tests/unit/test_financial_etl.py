"""Unit tests for Financial ETL: number parsing, Gemini Structured Output, and metric accuracy."""

import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.models.enums import FiscalPeriod, MetricCode, MetricUnit, StatementType
from app.schemas.financial_etl import (
    ExtractedFinancialMetricItem,
    FinancialTableExtractionOutput,
)
from app.services.ingestion.financial_etl import (
    FinancialETLService,
    GeminiStructuredFinancialExtractor,
    classify_metric_row,
    normalize_gemini_model_name,
    parse_financial_number,
    verify_accounting_identities,
)
from app.services.ingestion.pdf_parser import LayoutAwarePDFParser
from app.services.ingestion.sample_pdf_generator import create_sample_financial_pdf


class _FakeInteractionResponse:
    """Stub representing a google.genai Interaction response."""

    def __init__(self, output_text: str) -> None:
        self.output_text = output_text


class _FakeInteractionsResource:
    """Stub capturing sync and async calls to client.interactions.create."""

    def __init__(self, payload_json: str) -> None:
        self.payload_json = payload_json
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _FakeInteractionResponse:
        self.calls.append(kwargs)
        return _FakeInteractionResponse(self.payload_json)


class _FakeAsyncInteractionsResource:
    """Async stub capturing calls to client.aio.interactions.create."""

    def __init__(self, payload_json: str) -> None:
        self.payload_json = payload_json
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _FakeInteractionResponse:
        self.calls.append(kwargs)
        return _FakeInteractionResponse(self.payload_json)


class _FakeAioNamespace:
    def __init__(self, payload_json: str) -> None:
        self.interactions = _FakeAsyncInteractionsResource(payload_json)


class _FakeGenAIClient:
    """Mock google.genai.Client for deterministic unit testing of Structured Output."""

    def __init__(self, payload_json: str) -> None:
        self.interactions = _FakeInteractionsResource(payload_json)
        self.aio = _FakeAioNamespace(payload_json)


def test_parse_financial_number_vi_and_en_conventions() -> None:
    """Verify Vietnamese thousand-dots, decimal-commas, negative parens, and English commas."""
    # Vietnamese format: dot = thousands separator, comma = decimal point
    assert parse_financial_number("62.849", language="vi") == Decimal("62849.0000")
    assert parse_financial_number("4.940", language="vi") == Decimal("4940.0000")
    assert parse_financial_number("1.234.567,89", language="vi") == Decimal("1234567.8900")
    assert parse_financial_number("28,7%", language="vi") == Decimal("28.7000")
    assert parse_financial_number("(1.250)", language="vi") == Decimal("-1250.0000")
    assert parse_financial_number("-", language="vi") is None

    # English format: comma = thousands separator, dot = decimal point
    assert parse_financial_number("62,849", language="en") == Decimal("62849.0000")
    assert parse_financial_number("62,849.75", language="en") == Decimal("62849.7500")
    assert parse_financial_number("(450.25)", language="en") == Decimal("-450.2500")


def test_classify_metric_row_ignores_total_liabilities_and_equity_duplicate() -> None:
    """Verify VAS row labels map to canonical MetricCode and ignore 'TỔNG CỘNG NGUỒN VỐN'."""
    assert classify_metric_row("1. Doanh thu thuần về bán hàng và CCDV") == MetricCode.REVENUE
    assert classify_metric_row("6. Lợi nhuận sau thuế TNDN") == MetricCode.NET_INCOME
    assert classify_metric_row("7. Lãi cơ bản trên cổ phiếu (EPS - VNĐ)") == MetricCode.EPS
    assert classify_metric_row("TỔNG CỘNG TÀI SẢN") == MetricCode.TOTAL_ASSETS
    assert classify_metric_row("D. VỐN CHỦ SỞ HỮU") == MetricCode.TOTAL_EQUITY
    assert classify_metric_row("TỔNG CỘNG NGUỒN VỐN") is None


async def test_gemini_structured_extractor_uses_pydantic_json_schema() -> None:
    """Verify GeminiStructuredFinancialExtractor passes Pydantic schema to interactions.create."""
    sample_output = FinancialTableExtractionOutput(
        metrics=[
            ExtractedFinancialMetricItem(
                metric_code=MetricCode.REVENUE,
                metric_name_vi="Doanh thu thuần",
                metric_name_en="Net Revenue",
                statement_type=StatementType.INCOME_STATEMENT,
                fiscal_year=2024,
                period=FiscalPeriod.FY,
                value=62849.0,
                unit=MetricUnit.VND_BILLION,
                source_page=1,
            ),
            ExtractedFinancialMetricItem(
                metric_code=MetricCode.NET_INCOME,
                metric_name_vi="Lợi nhuận sau thuế",
                metric_name_en="Net Income",
                statement_type=StatementType.INCOME_STATEMENT,
                fiscal_year=2024,
                period=FiscalPeriod.FY,
                value=9420.0,
                unit=MetricUnit.VND_BILLION,
                source_page=1,
            ),
        ]
    )
    fake_client = _FakeGenAIClient(sample_output.model_dump_json())

    # Also verify deprecated model name is automatically upgraded to gemini-3.8-flash
    assert normalize_gemini_model_name("gemini-2.5-flash") == "gemini-3.8-flash"
    extractor = GeminiStructuredFinancialExtractor(
        model_name="gemini-2.5-flash",
        client=fake_client,  # type: ignore[arg-type]
    )
    assert extractor.model_name == "gemini-3.8-flash"
    assert extractor.is_available is True

    md_table = "| Chỉ tiêu | Năm 2024 (Tỷ VNĐ) |\n| --- | --- |\n| Doanh thu thuần | 62.849 |"
    sync_res = extractor.extract_from_markdown(
        md_table,
        ticker="FPT",
        default_fiscal_year=2024,
        source_page=1,
    )
    assert len(sync_res.metrics) == 2
    assert sync_res.metrics[0].metric_code == MetricCode.REVENUE
    assert sync_res.metrics[0].value == 62849.0

    call_kwargs = fake_client.interactions.calls[0]
    assert call_kwargs["model"] == "gemini-3.8-flash"
    assert call_kwargs["store"] is False
    assert call_kwargs["response_format"]["type"] == "text"
    assert call_kwargs["response_format"]["mime_type"] == "application/json"
    assert (
        call_kwargs["response_format"]["schema"]
        == FinancialTableExtractionOutput.model_json_schema()
    )

    async_res = await extractor.aextract_from_markdown(
        md_table,
        ticker="FPT",
        default_fiscal_year=2024,
        source_page=1,
    )
    assert len(async_res.metrics) == 2
    assert len(fake_client.aio.interactions.calls) == 1


def test_reconcile_llm_and_rule_metrics_corrects_llm_scale_drift(tmp_path: Path) -> None:
    """Verify rule-validation corrects LLM thousand-separator misreads (e.g. 62.849 -> 62849)."""
    pdf_path = create_sample_financial_pdf(tmp_path / "FPT_FS_2024.pdf")
    parsed_doc = LayoutAwarePDFParser().parse(pdf_path)
    doc_id = uuid.uuid4()

    # Simulate LLM misreading Vietnamese '62.849' as float 62.849 instead of 62849
    drifted_llm_output = FinancialTableExtractionOutput(
        metrics=[
            ExtractedFinancialMetricItem(
                metric_code=MetricCode.REVENUE,
                metric_name_vi="Doanh thu thuần",
                metric_name_en="Net Revenue",
                statement_type=StatementType.INCOME_STATEMENT,
                fiscal_year=2024,
                period=FiscalPeriod.FY,
                value=62.849,  # 1000x off due to dot-separator confusion
                unit=MetricUnit.VND_BILLION,
                source_page=1,
            ),
            ExtractedFinancialMetricItem(
                metric_code=MetricCode.GROSS_MARGIN,
                metric_name_vi="Biên lợi nhuận gộp",
                metric_name_en="Gross Profit Margin",
                statement_type=StatementType.RATIOS,
                fiscal_year=2024,
                period=FiscalPeriod.FY,
                value=38.7,
                unit=MetricUnit.PERCENT,
                source_page=1,
            ),
        ]
    )
    fake_client = _FakeGenAIClient(drifted_llm_output.model_dump_json())
    extractor = GeminiStructuredFinancialExtractor(client=fake_client)  # type: ignore[arg-type]
    service = FinancialETLService(extractor=extractor, enable_rule_validation=True)

    page1_metrics = service.extract_page_metrics(
        parsed_doc.pages[0],
        document_id=doc_id,
        ticker="FPT",
        fiscal_year=2024,
    )
    by_code_2024 = {m.metric_code: m for m in page1_metrics if m.fiscal_year == 2024}

    # Reconciler fixed 62.849 -> 62849.0000 from exact table cell AND kept LLM's GROSS_MARGIN (38.7%)
    assert by_code_2024[MetricCode.REVENUE].value == Decimal("62849.0000")
    assert by_code_2024[MetricCode.GROSS_MARGIN].value == Decimal("38.7000")


def test_extract_fpt_sample_pdf_full_accuracy_and_accounting_identities(tmp_path: Path) -> None:
    """Verify exact values, units, and source_page for all core metrics in FPT 2024 sample PDF."""
    pdf_path = create_sample_financial_pdf(tmp_path / "FPT_FS_2024_sample.pdf")
    parsed_doc = LayoutAwarePDFParser().parse(pdf_path)
    doc_id = uuid.uuid4()

    service = FinancialETLService()
    metrics = service.extract_document_metrics(
        parsed_doc,
        document_id=doc_id,
        ticker="FPT",
        fiscal_year=2024,
        period=FiscalPeriod.FY,
        language="vi",
    )

    # Verify zero accounting identity discrepancies across 2024 and 2023
    assert verify_accounting_identities(metrics) == []

    m_map = {(m.metric_code, m.fiscal_year): m for m in metrics}

    # --- Page 1: Income Statement (FY 2024 & FY 2023) ---
    assert m_map[(MetricCode.REVENUE, 2024)].value == Decimal("62849.0000")
    assert m_map[(MetricCode.REVENUE, 2024)].unit == MetricUnit.VND_BILLION
    assert m_map[(MetricCode.REVENUE, 2024)].source_page == 1
    assert m_map[(MetricCode.REVENUE, 2023)].value == Decimal("52618.0000")

    assert m_map[(MetricCode.COGS, 2024)].value == Decimal("38527.0000")
    assert m_map[(MetricCode.GROSS_PROFIT, 2024)].value == Decimal("24322.0000")
    assert m_map[(MetricCode.OPERATING_PROFIT, 2024)].value == Decimal("11071.0000")
    assert m_map[(MetricCode.PROFIT_BEFORE_TAX, 2024)].value == Decimal("11070.0000")

    assert m_map[(MetricCode.NET_INCOME, 2024)].value == Decimal("9420.0000")
    assert m_map[(MetricCode.NET_INCOME, 2024)].source_page == 1
    assert m_map[(MetricCode.NET_INCOME, 2023)].value == Decimal("7788.0000")

    assert m_map[(MetricCode.EPS, 2024)].value == Decimal("4940.0000")
    assert m_map[(MetricCode.EPS, 2024)].unit == MetricUnit.VND
    assert m_map[(MetricCode.EPS, 2024)].source_page == 1
    assert m_map[(MetricCode.EPS, 2023)].value == Decimal("4650.0000")

    # --- Page 2: Balance Sheet & Profitability Ratios ---
    assert m_map[(MetricCode.CURRENT_ASSETS, 2024)].value == Decimal("46810.0000")
    assert m_map[(MetricCode.NON_CURRENT_ASSETS, 2024)].value == Decimal("25205.0000")

    assert m_map[(MetricCode.TOTAL_ASSETS, 2024)].value == Decimal("72015.0000")
    assert m_map[(MetricCode.TOTAL_ASSETS, 2024)].source_page == 2
    assert m_map[(MetricCode.TOTAL_ASSETS, 2023)].value == Decimal("60283.0000")

    assert m_map[(MetricCode.TOTAL_LIABILITIES, 2024)].value == Decimal("36415.0000")
    assert m_map[(MetricCode.TOTAL_EQUITY, 2024)].value == Decimal("35600.0000")
    assert m_map[(MetricCode.TOTAL_EQUITY, 2024)].source_page == 2
    assert m_map[(MetricCode.TOTAL_EQUITY, 2023)].value == Decimal("29933.0000")

    assert m_map[(MetricCode.ROE, 2024)].value == Decimal("28.7000")
    assert m_map[(MetricCode.ROE, 2024)].unit == MetricUnit.PERCENT
    assert m_map[(MetricCode.ROE, 2024)].source_page == 2

    assert m_map[(MetricCode.ROA, 2024)].value == Decimal("14.2000")
    assert m_map[(MetricCode.ROA, 2024)].unit == MetricUnit.PERCENT
    assert m_map[(MetricCode.ROA, 2024)].source_page == 2
