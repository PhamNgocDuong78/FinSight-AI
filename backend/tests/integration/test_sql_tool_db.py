"""Integration tests for Task 3.1: Structured SQL Query Tool on PostgreSQL 16 with RBAC & Anti-SQLi."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tools.sql_tool import (
    RuleBasedSQLQueryGenerator,
    SQLInjectionError,
    StructuredSQLQueryTool,
)
from app.db.postgres import get_session_factory
from app.models.document import Document
from app.models.enums import (
    AccessLevel,
    DocumentLanguage,
    DocumentStatus,
    FiscalPeriod,
    MetricCode,
    MetricUnit,
    StatementType,
    UserRole,
)
from app.models.financial_metric import FinancialMetric


async def _seed_doc_with_metrics(
    session: AsyncSession,
    *,
    title: str,
    ticker: str,
    company_name: str,
    fiscal_year: int,
    access_level: AccessLevel,
    metrics: list[tuple[MetricCode, str, str, StatementType, Decimal, MetricUnit, int]],
) -> uuid.UUID:
    """Create a Document and associated FinancialMetric rows in PostgreSQL."""
    doc_id = uuid.uuid4()
    doc = Document(
        id=doc_id,
        title=title,
        file_path=f"data/raw_pdfs/{ticker}_{fiscal_year}_{access_level.value}.pdf",
        ticker=ticker,
        company_name=company_name,
        fiscal_year=fiscal_year,
        period=FiscalPeriod.FY.value,
        language=DocumentLanguage.VI.value,
        access_level=access_level.value,
        status=DocumentStatus.INDEXED.value,
        total_pages=60,
    )
    session.add(doc)
    for code, name_vi, name_en, stmt_type, val, unit, page in metrics:
        session.add(
            FinancialMetric(
                id=uuid.uuid4(),
                document_id=doc_id,
                ticker=ticker,
                fiscal_year=fiscal_year,
                period=FiscalPeriod.FY.value,
                statement_type=stmt_type.value,
                metric_code=code.value,
                metric_name_vi=name_vi,
                metric_name_en=name_en,
                value=val,
                unit=unit.value,
                source_page=page,
            )
        )
    await session.commit()
    return doc_id


async def test_sql_tool_multi_year_execution_rbac_and_sql_injection_protection() -> None:
    """Verify end-to-end SQL tool query on PostgreSQL 16 returns exact figures, units, pages, and enforces RBAC."""
    session_factory = get_session_factory()
    tool = StructuredSQLQueryTool(generator=RuleBasedSQLQueryGenerator())
    created_doc_ids: list[uuid.UUID] = []

    try:
        async with session_factory() as session:
            # 1. Seed FPT 2022, 2023, 2024 as PUBLIC
            fpt_2022_id = await _seed_doc_with_metrics(
                session,
                title="Báo cáo Thường niên FPT 2022",
                ticker="FPT",
                company_name="Công ty Cổ phần FPT",
                fiscal_year=2022,
                access_level=AccessLevel.PUBLIC,
                metrics=[
                    (
                        MetricCode.REVENUE,
                        "Doanh thu thuần về bán hàng và cung cấp dịch vụ",
                        "Net Revenue",
                        StatementType.INCOME_STATEMENT,
                        Decimal("44010.0000"),
                        MetricUnit.VND_BILLION,
                        42,
                    ),
                    (
                        MetricCode.NET_INCOME,
                        "Lợi nhuận sau thuế thu nhập doanh nghiệp",
                        "Net Income After Tax",
                        StatementType.INCOME_STATEMENT,
                        Decimal("6491.0000"),
                        MetricUnit.VND_BILLION,
                        42,
                    ),
                ],
            )
            created_doc_ids.append(fpt_2022_id)

            fpt_2023_id = await _seed_doc_with_metrics(
                session,
                title="Báo cáo Thường niên FPT 2023",
                ticker="FPT",
                company_name="Công ty Cổ phần FPT",
                fiscal_year=2023,
                access_level=AccessLevel.PUBLIC,
                metrics=[
                    (
                        MetricCode.REVENUE,
                        "Doanh thu thuần về bán hàng và cung cấp dịch vụ",
                        "Net Revenue",
                        StatementType.INCOME_STATEMENT,
                        Decimal("52618.0000"),
                        MetricUnit.VND_BILLION,
                        44,
                    ),
                    (
                        MetricCode.NET_INCOME,
                        "Lợi nhuận sau thuế thu nhập doanh nghiệp",
                        "Net Income After Tax",
                        StatementType.INCOME_STATEMENT,
                        Decimal("7788.0000"),
                        MetricUnit.VND_BILLION,
                        44,
                    ),
                ],
            )
            created_doc_ids.append(fpt_2023_id)

            fpt_2024_id = await _seed_doc_with_metrics(
                session,
                title="Báo cáo Thường niên FPT 2024",
                ticker="FPT",
                company_name="Công ty Cổ phần FPT",
                fiscal_year=2024,
                access_level=AccessLevel.PUBLIC,
                metrics=[
                    (
                        MetricCode.REVENUE,
                        "Doanh thu thuần về bán hàng và cung cấp dịch vụ",
                        "Net Revenue",
                        StatementType.INCOME_STATEMENT,
                        Decimal("62849.0000"),
                        MetricUnit.VND_BILLION,
                        45,
                    ),
                    (
                        MetricCode.NET_INCOME,
                        "Lợi nhuận sau thuế thu nhập doanh nghiệp",
                        "Net Income After Tax",
                        StatementType.INCOME_STATEMENT,
                        Decimal("9420.0000"),
                        MetricUnit.VND_BILLION,
                        45,
                    ),
                    (
                        MetricCode.ROE,
                        "Tỷ suất lợi nhuận trên vốn chủ sở hữu (ROE)",
                        "Return on Equity (ROE)",
                        StatementType.RATIOS,
                        Decimal("28.7000"),
                        MetricUnit.PERCENT,
                        48,
                    ),
                ],
            )
            created_doc_ids.append(fpt_2024_id)

            # 2. Seed VNM 2024 as INTERNAL_FINANCE
            vnm_internal_id = await _seed_doc_with_metrics(
                session,
                title="Báo cáo Tài chính Nội bộ Vinamilk 2024",
                ticker="VNM",
                company_name="Công ty Cổ phần Sữa Việt Nam",
                fiscal_year=2024,
                access_level=AccessLevel.INTERNAL_FINANCE,
                metrics=[
                    (
                        MetricCode.REVENUE,
                        "Doanh thu thuần",
                        "Net Revenue",
                        StatementType.INCOME_STATEMENT,
                        Decimal("61824.0000"),
                        MetricUnit.VND_BILLION,
                        12,
                    ),
                ],
            )
            created_doc_ids.append(vnm_internal_id)

            # 3. Seed VCB 2024 as EXECUTIVE_ONLY
            vcb_exec_id = await _seed_doc_with_metrics(
                session,
                title="Tài liệu Mật HĐQT Vietcombank 2024",
                ticker="VCB",
                company_name="Ngân hàng TMCP Ngoại thương Việt Nam",
                fiscal_year=2024,
                access_level=AccessLevel.EXECUTIVE_ONLY,
                metrics=[
                    (
                        MetricCode.PROFIT_BEFORE_TAX,
                        "Tổng lợi nhuận kế toán trước thuế",
                        "Profit Before Tax",
                        StatementType.INCOME_STATEMENT,
                        Decimal("42236.0000"),
                        MetricUnit.VND_BILLION,
                        9,
                    ),
                ],
            )
            created_doc_ids.append(vcb_exec_id)

            # =================================================================
            # CRITERION 1: Multi-year quantitative query returns exact figures, units, source_page
            # =================================================================
            res = await tool.query(
                session,
                "Doanh thu và lợi nhuận sau thuế của FPT từ 2022 đến 2024",
                user_role=UserRole.VIEWER,
                document_ids=created_doc_ids,
            )
            assert res.row_count == 6
            by_year_code = {(r.fiscal_year, r.metric_code): r for r in res.records}
            assert by_year_code[(2022, MetricCode.REVENUE)].value == Decimal("44010.0000")
            assert by_year_code[(2022, MetricCode.REVENUE)].unit == MetricUnit.VND_BILLION
            assert by_year_code[(2022, MetricCode.REVENUE)].source_page == 42

            assert by_year_code[(2023, MetricCode.NET_INCOME)].value == Decimal("7788.0000")
            assert by_year_code[(2023, MetricCode.NET_INCOME)].source_page == 44

            assert by_year_code[(2024, MetricCode.REVENUE)].value == Decimal("62849.0000")
            assert by_year_code[(2024, MetricCode.NET_INCOME)].value == Decimal("9420.0000")
            assert by_year_code[(2024, MetricCode.NET_INCOME)].source_page == 45
            assert len(res.citations) == 3

            # =================================================================
            # CRITERION 2: RBAC Isolation at Database Level (viewer vs analyst vs admin)
            # =================================================================
            viewer_vnm = await tool.query(
                session,
                "Doanh thu thuần của Vinamilk năm 2024",
                user_role=UserRole.VIEWER,
                document_ids=created_doc_ids,
            )
            assert viewer_vnm.row_count == 0

            analyst_vnm = await tool.query(
                session,
                "Doanh thu thuần của Vinamilk năm 2024",
                user_role=UserRole.ANALYST,
                document_ids=created_doc_ids,
            )
            assert analyst_vnm.row_count == 1
            assert analyst_vnm.records[0].value == Decimal("61824.0000")
            assert analyst_vnm.records[0].unit == MetricUnit.VND_BILLION
            assert analyst_vnm.records[0].source_page == 12

            analyst_vcb = await tool.query(
                session,
                "Lợi nhuận trước thuế của VCB năm 2024",
                user_role=UserRole.ANALYST,
                document_ids=created_doc_ids,
            )
            assert analyst_vcb.row_count == 0

            admin_vcb = await tool.query(
                session,
                "Lợi nhuận trước thuế của VCB năm 2024",
                user_role=UserRole.ADMIN,
                document_ids=created_doc_ids,
            )
            assert admin_vcb.row_count == 1
            assert admin_vcb.records[0].value == Decimal("42236.0000")
            assert admin_vcb.records[0].source_page == 9

            # Even a raw SELECT * without explicit access_level filter is restricted by the RBAC CTE
            raw_viewer_rows = await tool.execute_readonly_sql(
                session,
                "SELECT * FROM financial_metrics WHERE ticker = 'VNM' AND fiscal_year = 2024",
                user_role=UserRole.VIEWER,
            )
            assert all(r.document_id != vnm_internal_id for r in raw_viewer_rows)

            # =================================================================
            # CRITERION 3: Anti-SQL-Injection on live session
            # =================================================================
            with pytest.raises(SQLInjectionError):
                await tool.execute_readonly_sql(
                    session,
                    "SELECT * FROM financial_metrics WHERE ticker = 'FPT'; DROP TABLE financial_metrics;",
                )
            with pytest.raises(SQLInjectionError):
                await tool.query(
                    session,
                    "Doanh thu FPT' OR 1=1 --",
                )

    finally:
        async with session_factory() as session:
            for doc_id in created_doc_ids:
                doc_obj = await session.get(Document, doc_id)
                if doc_obj is not None:
                    await session.delete(doc_obj)
            await session.commit()
