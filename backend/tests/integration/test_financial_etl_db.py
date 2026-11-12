"""Integration tests for persisting Financial ETL metrics into PostgreSQL financial_metrics."""

from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

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
)
from app.models.financial_metric import FinancialMetric
from app.services.ingestion.financial_etl import FinancialETLService
from app.services.ingestion.pdf_parser import LayoutAwarePDFParser
from app.services.ingestion.sample_pdf_generator import create_sample_financial_pdf


async def test_financial_etl_persists_accurate_metrics_in_postgres(tmp_path: Path) -> None:
    """Verify full PDF -> FinancialETLService -> PostgreSQL financial_metrics persistence and idempotency."""
    pdf_path = create_sample_financial_pdf(tmp_path / "FPT_FS_2024_sample.pdf")
    parsed_doc = await LayoutAwarePDFParser().aparse(pdf_path)

    session_factory = get_session_factory()
    async with session_factory() as session:
        doc = Document(
            title="Báo cáo Tài chính Hợp nhất FPT Năm 2024 (Integration Test)",
            file_path=str(pdf_path),
            ticker="FPT",
            company_name="Công ty Cổ phần FPT",
            fiscal_year=2024,
            period=FiscalPeriod.FY.value,
            language=DocumentLanguage.VI.value,
            access_level=AccessLevel.PUBLIC.value,
            status=DocumentStatus.PROCESSING.value,
            total_pages=parsed_doc.total_pages,
        )
        session.add(doc)
        await session.commit()
        doc_id = doc.id

        try:
            etl_service = FinancialETLService()
            etl_result = await etl_service.run_etl(
                session,
                parsed_doc,
                document_id=doc_id,
                ticker="FPT",
                fiscal_year=2024,
                period=FiscalPeriod.FY,
                language="vi",
            )
            assert etl_result.persisted_count >= 24

            # Re-run with replace_existing=True to verify idempotency (no duplicate rows)
            second_result = await etl_service.run_etl(
                session,
                parsed_doc,
                document_id=doc_id,
                ticker="FPT",
                fiscal_year=2024,
                period=FiscalPeriod.FY,
                language="vi",
                replace_existing=True,
            )
            assert second_result.persisted_count == etl_result.persisted_count

            stmt = select(FinancialMetric).where(FinancialMetric.document_id == doc_id)
            db_rows = (await session.execute(stmt)).scalars().all()
            assert len(db_rows) == etl_result.persisted_count

            row_map = {(r.metric_code, r.fiscal_year): r for r in db_rows}

            # Verify core metrics in PostgreSQL match exact PDF figures and source pages
            rev_2024 = row_map[(MetricCode.REVENUE.value, 2024)]
            assert rev_2024.value == Decimal("62849.0000")
            assert rev_2024.unit == MetricUnit.VND_BILLION.value
            assert rev_2024.statement_type == StatementType.INCOME_STATEMENT.value
            assert rev_2024.source_page == 1

            gp_2024 = row_map[(MetricCode.GROSS_PROFIT.value, 2024)]
            assert gp_2024.value == Decimal("24322.0000")
            assert gp_2024.source_page == 1

            ni_2024 = row_map[(MetricCode.NET_INCOME.value, 2024)]
            assert ni_2024.value == Decimal("9420.0000")
            assert ni_2024.source_page == 1

            eps_2024 = row_map[(MetricCode.EPS.value, 2024)]
            assert eps_2024.value == Decimal("4940.0000")
            assert eps_2024.unit == MetricUnit.VND.value
            assert eps_2024.source_page == 1

            assets_2024 = row_map[(MetricCode.TOTAL_ASSETS.value, 2024)]
            assert assets_2024.value == Decimal("72015.0000")
            assert assets_2024.statement_type == StatementType.BALANCE_SHEET.value
            assert assets_2024.source_page == 2

            equity_2024 = row_map[(MetricCode.TOTAL_EQUITY.value, 2024)]
            assert equity_2024.value == Decimal("35600.0000")
            assert equity_2024.source_page == 2

            roe_2024 = row_map[(MetricCode.ROE.value, 2024)]
            assert roe_2024.value == Decimal("28.7000")
            assert roe_2024.unit == MetricUnit.PERCENT.value
            assert roe_2024.statement_type == StatementType.RATIOS.value
            assert roe_2024.source_page == 2

            roa_2024 = row_map[(MetricCode.ROA.value, 2024)]
            assert roa_2024.value == Decimal("14.2000")
            assert roa_2024.unit == MetricUnit.PERCENT.value
            assert roa_2024.source_page == 2
        finally:
            await session.delete(doc)
            await session.commit()
