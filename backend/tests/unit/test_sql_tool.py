"""Unit tests for Task 3.1: Structured SQL Query Tool, NL-to-SQL, and Anti-SQL-Injection."""

import uuid
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from app.agents.tools.sql_tool import (
    GeminiSQLQueryGenerator,
    RuleBasedSQLQueryGenerator,
    SQLInjectionError,
    SQLValidationError,
    StructuredSQLQueryTool,
    detect_input_sql_injection,
    validate_readonly_sql,
)
from app.models.enums import (
    AccessLevel,
    FiscalPeriod,
    MetricCode,
    MetricUnit,
    StatementType,
    UserRole,
)
from app.schemas.sql_tool import FinancialMetricRecord, LLMGeneratedSQLOutput


def test_nl_to_sql_multi_year_range_and_multiple_metrics() -> None:
    """Verify Vietnamese multi-year quantitative query expands range and extracts exact MetricCodes."""
    tool = StructuredSQLQueryTool(generator=RuleBasedSQLQueryGenerator())
    sql, params, spec = tool.generate_sql(
        "Doanh thu và lợi nhuận sau thuế của FPT từ 2022 đến 2024",
        user_role=UserRole.ANALYST,
    )

    assert spec.tickers == ["FPT"]
    assert spec.fiscal_years == [2022, 2023, 2024]
    assert spec.metric_codes == [MetricCode.REVENUE, MetricCode.NET_INCOME]
    assert spec.allowed_access_levels == [AccessLevel.PUBLIC, AccessLevel.INTERNAL_FINANCE]

    assert sql.startswith("SELECT")
    assert "FROM financial_metrics AS fm" in sql
    assert "JOIN documents AS d ON fm.document_id = d.id" in sql
    assert "fm.source_page" in sql
    assert "fm.unit" in sql
    assert "fm.value" in sql
    assert params["tickers"] == ["FPT"]
    assert params["fiscal_years"] == [2022, 2023, 2024]
    assert params["metric_codes"] == ["REVENUE", "NET_INCOME"]
    assert params["allowed_access_levels"] == ["public", "internal_finance"]


def test_nl_to_sql_company_aliases_ratios_and_margins_disambiguation() -> None:
    """Verify company alias mapping (Vinamilk -> VNM) and margin vs. profit disambiguation."""
    tool = StructuredSQLQueryTool(generator=RuleBasedSQLQueryGenerator())

    _, _, spec_vnm = tool.generate_sql(
        "Cho biết Tổng tài sản, Vốn chủ sở hữu, ROE, ROA và EPS của Vinamilk năm 2023 và 2024",
        user_role="viewer",
    )
    assert spec_vnm.tickers == ["VNM"]
    assert spec_vnm.fiscal_years == [2023, 2024]
    assert set(spec_vnm.metric_codes) == {
        MetricCode.TOTAL_ASSETS,
        MetricCode.TOTAL_EQUITY,
        MetricCode.ROE,
        MetricCode.ROA,
        MetricCode.EPS,
    }

    _, _, spec_margin = tool.generate_sql(
        "Biên lợi nhuận gộp và biên lợi nhuận ròng của FPT quý 4 năm 2024 là bao nhiêu?",
        user_role="viewer",
    )
    assert spec_margin.tickers == ["FPT"]
    assert spec_margin.fiscal_years == [2024]
    assert spec_margin.periods == [FiscalPeriod.Q4]
    assert spec_margin.metric_codes == [MetricCode.GROSS_MARGIN, MetricCode.NET_MARGIN]


def test_nl_to_sql_english_multi_ticker_and_statement_type() -> None:
    """Verify English multi-company queries and statement-category queries."""
    tool = StructuredSQLQueryTool(generator=RuleBasedSQLQueryGenerator())

    _, _, spec_en = tool.generate_sql(
        "Compare Net Revenue, Operating Cash Flow, and Profit Before Tax of FPT and HPG from 2021 to 2023"
    )
    assert spec_en.tickers == ["FPT", "HPG"]
    assert spec_en.fiscal_years == [2021, 2022, 2023]
    assert set(spec_en.metric_codes) == {
        MetricCode.REVENUE,
        MetricCode.OPERATING_CASH_FLOW,
        MetricCode.PROFIT_BEFORE_TAX,
    }

    _, params_bs, spec_bs = tool.generate_sql(
        "Lấy các chỉ tiêu trong bảng cân đối kế toán của VCB năm 2024"
    )
    assert spec_bs.tickers == ["VCB"]
    assert spec_bs.fiscal_years == [2024]
    assert spec_bs.statement_types == [StatementType.BALANCE_SHEET]
    assert params_bs["statement_types"] == ["balance_sheet"]


@pytest.mark.parametrize(
    "malicious_sql",
    [
        # 1. Non-SELECT DML / DDL statements
        "DROP TABLE financial_metrics",
        "DELETE FROM financial_metrics WHERE ticker = 'FPT'",
        "UPDATE financial_metrics SET value = 999999 WHERE ticker = 'FPT'",
        "INSERT INTO financial_metrics (ticker, value) VALUES ('FPT', 100)",
        "TRUNCATE TABLE financial_metrics",
        "ALTER TABLE financial_metrics DROP COLUMN value",
        "CREATE TABLE hacked (id int)",
        "GRANT ALL ON financial_metrics TO public",
        # 2. Stacked queries (multi-statement injection)
        "SELECT * FROM financial_metrics; DROP TABLE financial_metrics;",
        "SELECT * FROM financial_metrics WHERE ticker = 'FPT'; DELETE FROM users;",
        "SELECT * FROM financial_metrics; SELECT * FROM users",
        # 3. SQL comments used to truncate WHERE / RBAC clauses
        "SELECT * FROM financial_metrics WHERE ticker = 'FPT' -- AND access_level = 'public'",
        "SELECT /* comment injection */ * FROM financial_metrics",
        "SELECT * FROM financial_metrics WHERE ticker = 'FPT' # comment",
        # 4. UNION / INTERSECT / EXCEPT data exfiltration
        "SELECT ticker, metric_code FROM financial_metrics UNION SELECT email, role FROM users",
        "SELECT ticker FROM financial_metrics UNION ALL SELECT title FROM documents",
        "SELECT ticker FROM financial_metrics INTERSECT SELECT ticker FROM documents",
        # 5. Subquery exfiltration
        "SELECT * FROM financial_metrics WHERE ticker IN (SELECT email FROM users)",
        "SELECT (SELECT count(*) FROM users) FROM financial_metrics",
        # 6. Unauthorized tables & system catalogs
        "SELECT * FROM users",
        "SELECT * FROM chat_sessions",
        "SELECT * FROM chat_messages",
        "SELECT * FROM parent_chunks",
        "SELECT * FROM financial_metrics JOIN users ON true",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM pg_catalog.pg_tables",
        "SELECT pg_sleep(10) FROM financial_metrics",
        "SELECT pg_read_file('/etc/passwd') FROM financial_metrics",
        "SELECT lo_import('/tmp/hack') FROM financial_metrics",
        "SELECT current_setting('is_superuser') FROM financial_metrics",
        # 7. Schema qualification bypass & encoding tricks
        "SELECT * FROM public.financial_metrics",
        "SELECT chr(65) FROM financial_metrics",
        "SELECT * FROM financial_metrics WHERE ticker = 0x465054",
        # 8. Classic Tautology SQL Injection payloads
        "SELECT * FROM financial_metrics WHERE ticker = 'FPT' OR 1=1",
        "SELECT * FROM financial_metrics WHERE ticker = '' OR '1'='1'",
        "SELECT * FROM financial_metrics WHERE ticker = 'FPT' OR 'a'='a'",
        "SELECT * FROM financial_metrics WHERE ticker = 'FPT' OR TRUE",
        "SELECT * FROM financial_metrics WHERE ticker = 'FPT' OR x=x",
        # 9. SELECT INTO / Locking clauses
        "SELECT * INTO new_table FROM financial_metrics",
        "SELECT * FROM financial_metrics FOR UPDATE",
    ],
)
def test_validate_readonly_sql_blocks_all_sql_injection_vectors(malicious_sql: str) -> None:
    """Verify validate_readonly_sql blocks all DDL/DML, stacked queries, comments, UNION, and tautologies."""
    with pytest.raises(SQLInjectionError):
        validate_readonly_sql(malicious_sql)


def test_validate_readonly_sql_accepts_legitimate_queries() -> None:
    """Verify legitimate SELECT queries (including valid OR between distinct column comparisons) pass."""
    valid_sql = (
        "SELECT fm.ticker, fm.fiscal_year, fm.metric_code, fm.value, fm.unit, fm.source_page "
        "FROM financial_metrics AS fm "
        "JOIN documents AS d ON fm.document_id = d.id "
        "WHERE (fm.ticker = 'FPT' OR fm.ticker = 'VNM') AND fm.fiscal_year >= 2022;"
    )
    cleaned = validate_readonly_sql(valid_sql)
    assert not cleaned.endswith(";")
    assert cleaned.startswith("SELECT")

    with pytest.raises(SQLValidationError):
        validate_readonly_sql("   ")


@pytest.mark.parametrize(
    "malicious_question",
    [
        "Doanh thu của FPT'; DROP TABLE financial_metrics; --",
        "Doanh thu FPT' OR '1'='1",
        "Doanh thu FPT OR 1=1",
        "FPT UNION SELECT email, role FROM users",
        "Doanh thu FPT /* bypass RBAC */ năm 2024",
        "SELECT * FROM users WHERE 1=1",
    ],
)
def test_input_sql_injection_blocked_in_natural_language_and_tickers(
    malicious_question: str,
) -> None:
    """Verify SQL Injection payloads embedded in natural language questions or tickers are rejected."""
    tool = StructuredSQLQueryTool(generator=RuleBasedSQLQueryGenerator())
    with pytest.raises(SQLInjectionError):
        detect_input_sql_injection(malicious_question)
    with pytest.raises(SQLInjectionError):
        tool.generate_sql(malicious_question)


async def test_gemini_sql_generator_structured_output_and_compromised_llm_defense() -> None:
    """Verify GeminiSQLQueryGenerator parses structured output and blocks unsafe LLM-generated SQL."""
    safe_output = LLMGeneratedSQLOutput(
        sql_query=(
            "SELECT fm.id AS metric_id, fm.document_id, fm.ticker, fm.fiscal_year, "
            "fm.period, fm.statement_type, fm.metric_code, fm.metric_name_vi, "
            "fm.metric_name_en, fm.value, fm.unit, fm.source_page "
            "FROM financial_metrics AS fm WHERE fm.ticker = 'FPT'"
        ),
        tickers=["FPT"],
        fiscal_years=[2022, 2023, 2024],
        periods=[FiscalPeriod.FY],
        metric_codes=[MetricCode.REVENUE, MetricCode.NET_INCOME],
        rationale="Extracted FPT revenue and net income for 2022-2024.",
    )

    class _FakeInteractions:
        def __init__(self, payload: str) -> None:
            self._payload = payload

        def create(self, **kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(output_text=self._payload)

        async def acreate(self, **kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(output_text=self._payload)

    fake_sync = _FakeInteractions(safe_output.model_dump_json())
    fake_aio = SimpleNamespace(interactions=SimpleNamespace(create=fake_sync.acreate))
    fake_client = SimpleNamespace(interactions=fake_sync, aio=fake_aio)

    gen = GeminiSQLQueryGenerator(client=fake_client)  # type: ignore[arg-type]
    tool = StructuredSQLQueryTool(generator=gen)
    sql, params, spec = await tool.agenerate_sql("Chỉ số FPT giai đoạn 2022-2024")
    assert spec.tickers == ["FPT"]
    assert spec.fiscal_years == [2022, 2023, 2024]
    assert spec.metric_codes == [MetricCode.REVENUE, MetricCode.NET_INCOME]
    assert "FROM financial_metrics AS fm" in sql
    assert params["fiscal_years"] == [2022, 2023, 2024]

    # Compromised LLM attempting to return a UNION attack on `users` must raise SQLInjectionError
    compromised_json = (
        '{"sql_query": "SELECT ticker FROM financial_metrics UNION SELECT email FROM users", '
        '"tickers": ["FPT"], "fiscal_years": [2024], "periods": [], "metric_codes": [], '
        '"statement_types": [], "rationale": "hacked"}'
    )
    bad_sync = _FakeInteractions(compromised_json)
    bad_client = SimpleNamespace(
        interactions=bad_sync,
        aio=SimpleNamespace(interactions=SimpleNamespace(create=bad_sync.acreate)),
    )
    bad_tool = StructuredSQLQueryTool(
        generator=GeminiSQLQueryGenerator(client=bad_client)  # type: ignore[arg-type]
    )
    with pytest.raises(SQLInjectionError):
        bad_tool.generate_sql("Doanh thu FPT 2024")


def test_rbac_privilege_escalation_blocked_and_citation_serialization() -> None:
    """Verify viewer cannot escalate access_levels and FinancialMetricRecord formats citations."""
    tool = StructuredSQLQueryTool(generator=RuleBasedSQLQueryGenerator())
    _, params, spec = tool.generate_sql(
        "Doanh thu FPT năm 2024",
        user_role=UserRole.VIEWER,
        access_levels=[AccessLevel.INTERNAL_FINANCE, AccessLevel.EXECUTIVE_ONLY],
    )
    assert spec.allowed_access_levels == []
    assert params["allowed_access_levels"] == []

    doc_id = uuid.uuid4()
    rec = FinancialMetricRecord(
        document_id=doc_id,
        doc_title="Báo cáo Thường niên FPT 2024",
        company_name="Công ty Cổ phần FPT",
        ticker="FPT",
        fiscal_year=2024,
        period=FiscalPeriod.FY,
        statement_type=StatementType.INCOME_STATEMENT,
        metric_code=MetricCode.REVENUE,
        metric_name_vi="Doanh thu thuần về bán hàng và cung cấp dịch vụ",
        metric_name_en="Net Revenue",
        value=Decimal("62849.0000"),
        unit=MetricUnit.VND_BILLION,
        source_page=45,
        access_level=AccessLevel.PUBLIC,
    )
    assert rec.format_citation_tag() == "[FPT_AR_2024, tr. 45]"
    citation = rec.to_citation_dict()
    assert citation["doc_id"] == str(doc_id)
    assert citation["page_number"] == 45
    assert "62849.0000 VND_BILLION" in str(citation["snippet"])

    agent_dict = rec.to_agent_dict()
    assert agent_dict["value"] == 62849.0
    assert agent_dict["unit"] == "VND_BILLION"
    assert agent_dict["source_page"] == 45
