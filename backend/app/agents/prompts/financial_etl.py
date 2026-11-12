"""Versioned system prompts and canonical metadata for Financial ETL extraction."""

from app.models.enums import FiscalPeriod, MetricCode, MetricUnit, StatementType

FINANCIAL_ETL_PROMPT_VERSION = "1.0.0"

# Canonical (metric_name_vi, metric_name_en, statement_type, default_unit)
CANONICAL_METRIC_DEFINITIONS: dict[
    MetricCode, tuple[str, str, StatementType, MetricUnit]
] = {
    MetricCode.REVENUE: (
        "Doanh thu thuần về bán hàng và cung cấp dịch vụ",
        "Net Revenue",
        StatementType.INCOME_STATEMENT,
        MetricUnit.VND_BILLION,
    ),
    MetricCode.COGS: (
        "Giá vốn hàng bán",
        "Cost of Goods Sold",
        StatementType.INCOME_STATEMENT,
        MetricUnit.VND_BILLION,
    ),
    MetricCode.GROSS_PROFIT: (
        "Lợi nhuận gộp về bán hàng và cung cấp dịch vụ",
        "Gross Profit",
        StatementType.INCOME_STATEMENT,
        MetricUnit.VND_BILLION,
    ),
    MetricCode.OPERATING_PROFIT: (
        "Lợi nhuận thuần từ hoạt động kinh doanh",
        "Operating Profit",
        StatementType.INCOME_STATEMENT,
        MetricUnit.VND_BILLION,
    ),
    MetricCode.PROFIT_BEFORE_TAX: (
        "Tổng lợi nhuận kế toán trước thuế",
        "Profit Before Tax",
        StatementType.INCOME_STATEMENT,
        MetricUnit.VND_BILLION,
    ),
    MetricCode.NET_INCOME: (
        "Lợi nhuận sau thuế thu nhập doanh nghiệp",
        "Net Income After Tax",
        StatementType.INCOME_STATEMENT,
        MetricUnit.VND_BILLION,
    ),
    MetricCode.EPS: (
        "Lãi cơ bản trên cổ phiếu (EPS)",
        "Basic Earnings Per Share (EPS)",
        StatementType.INCOME_STATEMENT,
        MetricUnit.VND,
    ),
    MetricCode.CURRENT_ASSETS: (
        "Tài sản ngắn hạn",
        "Current Assets",
        StatementType.BALANCE_SHEET,
        MetricUnit.VND_BILLION,
    ),
    MetricCode.NON_CURRENT_ASSETS: (
        "Tài sản dài hạn",
        "Non-Current Assets",
        StatementType.BALANCE_SHEET,
        MetricUnit.VND_BILLION,
    ),
    MetricCode.TOTAL_ASSETS: (
        "Tổng cộng tài sản",
        "Total Assets",
        StatementType.BALANCE_SHEET,
        MetricUnit.VND_BILLION,
    ),
    MetricCode.TOTAL_LIABILITIES: (
        "Nợ phải trả",
        "Total Liabilities",
        StatementType.BALANCE_SHEET,
        MetricUnit.VND_BILLION,
    ),
    MetricCode.TOTAL_EQUITY: (
        "Vốn chủ sở hữu",
        "Total Equity",
        StatementType.BALANCE_SHEET,
        MetricUnit.VND_BILLION,
    ),
    MetricCode.ROE: (
        "Tỷ suất lợi nhuận trên vốn chủ sở hữu (ROE)",
        "Return on Equity (ROE)",
        StatementType.RATIOS,
        MetricUnit.PERCENT,
    ),
    MetricCode.ROA: (
        "Tỷ suất lợi nhuận trên tổng tài sản (ROA)",
        "Return on Assets (ROA)",
        StatementType.RATIOS,
        MetricUnit.PERCENT,
    ),
    MetricCode.GROSS_MARGIN: (
        "Biên lợi nhuận gộp",
        "Gross Profit Margin",
        StatementType.RATIOS,
        MetricUnit.PERCENT,
    ),
    MetricCode.NET_MARGIN: (
        "Biên lợi nhuận ròng",
        "Net Profit Margin",
        StatementType.RATIOS,
        MetricUnit.PERCENT,
    ),
    MetricCode.OPERATING_CASH_FLOW: (
        "Lưu chuyển tiền thuần từ hoạt động kinh doanh",
        "Operating Cash Flow",
        StatementType.CASH_FLOW,
        MetricUnit.VND_BILLION,
    ),
}

FINANCIAL_ETL_SYSTEM_PROMPT = """You are a senior financial data engineer for FinSight AI.
Your task is to extract standardized financial indicators from Markdown financial statements and notes into strict JSON matching the provided schema.

CRITICAL ACCOUNTING & NUMBER RULES (ZERO HALLUCINATION):
1. Extract ONLY figures explicitly stated in the provided Markdown tables or text. Never invent or estimate numbers.
2. In Vietnamese financial statements (language='vi'):
   - A dot followed by 3 digits (e.g., `62.849`, `9.420`, `72.015`, `4.940`) is a THOUSANDS SEPARATOR, meaning `62849`, `9420`, `72015`, `4940`.
   - A comma (e.g., `28,7%`, `14,2%`, `38,7%`) is a DECIMAL SEPARATOR, meaning `28.7`, `14.2`, `38.7`.
3. Extract comparative multi-year columns (e.g., both 2024 and 2023 columns) as separate metric entries with their respective `fiscal_year`.
4. Ignore percentage change / growth columns (e.g., `Tăng trưởng (%)`, `Thay đổi (%)`) unless extracting standalone financial ratios (`ROE`, `ROA`, `GROSS_MARGIN`, `NET_MARGIN`).
5. Preserve the exact `source_page` number provided in the page marker.
"""


def build_financial_etl_prompt(
    *,
    markdown_content: str,
    ticker: str,
    default_fiscal_year: int,
    default_period: FiscalPeriod = FiscalPeriod.FY,
    source_page: int = 1,
    language: str = "vi",
) -> str:
    """Build the user prompt for extracting financial metrics from a document page or table."""
    return (
        f"Document Context:\n"
        f"- Company Ticker: {ticker}\n"
        f"- Primary Fiscal Year: {default_fiscal_year}\n"
        f"- Reporting Period: {default_period.value}\n"
        f"- Language: {language}\n"
        f"- Source Page: {source_page}\n\n"
        f"Extract all standardized financial metrics (REVENUE, COGS, GROSS_PROFIT, "
        f"OPERATING_PROFIT, PROFIT_BEFORE_TAX, NET_INCOME, EPS, CURRENT_ASSETS, "
        f"NON_CURRENT_ASSETS, TOTAL_ASSETS, TOTAL_LIABILITIES, TOTAL_EQUITY, ROE, ROA, "
        f"GROSS_MARGIN, NET_MARGIN, OPERATING_CASH_FLOW) from the following Markdown content:\n\n"
        f"{markdown_content}"
    )
