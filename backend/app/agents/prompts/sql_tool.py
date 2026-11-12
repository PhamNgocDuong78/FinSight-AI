"""Versioned system prompts and NL-to-SQL extraction rules for Task 3.1 (`sql_tool.py`)."""

import re
from collections.abc import Sequence

from app.models.enums import AccessLevel, FiscalPeriod, MetricCode, StatementType

SQL_TOOL_PROMPT_VERSION = "1.0.0"

FINANCIAL_METRICS_SCHEMA_DDL = """
Table: financial_metrics (alias: fm)
  - id (UUID, PK)
  - document_id (UUID, FK -> documents.id)
  - ticker (VARCHAR(32), indexed, uppercase e.g. 'FPT', 'VNM', 'VCB', 'HPG', 'AAPL')
  - fiscal_year (INTEGER, indexed, 4-digit year e.g. 2022, 2023, 2024)
  - period (VARCHAR(16): 'FY', 'Q1', 'Q2', 'Q3', 'Q4')
  - statement_type (VARCHAR(32): 'income_statement', 'balance_sheet', 'cash_flow', 'ratios')
  - metric_code (VARCHAR(64), indexed:
      'REVENUE', 'COGS', 'GROSS_PROFIT', 'OPERATING_PROFIT', 'PROFIT_BEFORE_TAX',
      'NET_INCOME', 'EPS', 'CURRENT_ASSETS', 'NON_CURRENT_ASSETS', 'TOTAL_ASSETS',
      'TOTAL_LIABILITIES', 'TOTAL_EQUITY', 'ROE', 'ROA', 'GROSS_MARGIN',
      'NET_MARGIN', 'OPERATING_CASH_FLOW')
  - metric_name_vi (VARCHAR(255))
  - metric_name_en (VARCHAR(255))
  - value (NUMERIC(20, 4))
  - unit (VARCHAR(32): 'VND_BILLION', 'VND_MILLION', 'VND', 'USD_BILLION', 'USD_MILLION', 'USD', 'PERCENT')
  - source_page (INTEGER, 1-based PDF page number)

Table: documents (alias: d)
  - id (UUID, PK)
  - title (VARCHAR(500))
  - company_name (VARCHAR(255))
  - ticker (VARCHAR(32))
  - fiscal_year (INTEGER)
  - period (VARCHAR(16))
  - access_level (VARCHAR(32): 'public', 'internal_finance', 'executive_only')
""".strip()

SQL_TOOL_SYSTEM_PROMPT = f"""You are a senior financial Text-to-SQL engineer for FinSight AI.
Your task is to translate quantitative financial questions (in Vietnamese or English) into a safe, read-only PostgreSQL SELECT query and structured filter metadata over `financial_metrics` joined with `documents`.

DATABASE SCHEMA:
{FINANCIAL_METRICS_SCHEMA_DDL}

STRICT SECURITY & SQL GENERATION RULES:
1. READ-ONLY SELECT ONLY: Generate ONLY a single `SELECT` statement. NEVER generate `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`, `CREATE`, `GRANT`, `UNION`, comments (`--`, `/*`), or multiple statements (`;`).
2. ALLOWED TABLES ONLY: Query ONLY `financial_metrics AS fm JOIN documents AS d ON fm.document_id = d.id`. Never query `users`, `chat_sessions`, `chat_messages`, or system catalogs.
3. MANDATORY COLUMNS: Always select `fm.id AS metric_id, fm.document_id, d.title AS doc_title, d.company_name, fm.ticker, fm.fiscal_year, fm.period, fm.statement_type, fm.metric_code, fm.metric_name_vi, fm.metric_name_en, fm.value, fm.unit, fm.source_page, d.access_level`.
4. YEAR RANGES: Expand ranges like "từ 2022 đến 2024" or "2022-2024" into `[2022, 2023, 2024]`.
5. ZERO HALLUCINATION: Never invent numbers in SQL. Only retrieve stored metrics so downstream tools can compute growth/ratios accurately.
"""

_YEAR_RANGE_RE = re.compile(
    r"(?:từ(?:\s+năm)?|giai\s+đoạn(?:\s+năm)?|from|between)\s*(19\d{2}|20\d{2})"
    r"\s*(?:đến(?:\s+năm)?|tới(?:\s+năm)?|to|and|-|–|—)\s*(19\d{2}|20\d{2})"
    r"|\b(19\d{2}|20\d{2})\s*(?:-|–|—|đến|tới|to)\s*(19\d{2}|20\d{2})\b",
    re.IGNORECASE,
)
_SINGLE_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")

_METRIC_PATTERNS: tuple[tuple[MetricCode, re.Pattern[str]], ...] = (
    (
        MetricCode.GROSS_MARGIN,
        re.compile(r"\bGROSS_MARGIN\b|biên\s*lợi\s*nhuận\s*gộp|tỷ\s*suất\s*lợi\s*nhuận\s*gộp|gross\s*(?:profit\s*)?margin", re.IGNORECASE),
    ),
    (
        MetricCode.NET_MARGIN,
        re.compile(r"\bNET_MARGIN\b|biên\s*lợi\s*nhuận\s*(?:ròng|thuần|sau\s*thuế)|net\s*(?:profit\s*)?margin", re.IGNORECASE),
    ),
    (
        MetricCode.REVENUE,
        re.compile(r"\bREVENUE\b|doanh\s*thu(?:\s*thuần)?|net\s*revenue|total\s*revenue|net\s*sales", re.IGNORECASE),
    ),
    (
        MetricCode.COGS,
        re.compile(r"\bCOGS\b|giá\s*vốn(?:\s*hàng\s*bán)?|cost\s*of\s*(?:goods\s*sold|sales|revenue)", re.IGNORECASE),
    ),
    (
        MetricCode.GROSS_PROFIT,
        re.compile(r"\bGROSS_PROFIT\b|(?<!biên\s)(?<!tỷ\ssuất\s)lợi\s*nhuận\s*gộp|gross\s*profit(?!\s*margin)", re.IGNORECASE),
    ),
    (
        MetricCode.OPERATING_PROFIT,
        re.compile(r"\bOPERATING_PROFIT\b|lợi\s*nhuận\s*thuần\s*từ\s*(?:hđkd|hoạt\s*động\s*kinh\s*doanh)|operating\s*(?:profit|income)", re.IGNORECASE),
    ),
    (
        MetricCode.PROFIT_BEFORE_TAX,
        re.compile(r"\bPROFIT_BEFORE_TAX\b|lợi\s*nhuận\s*(?:kế\s*toán\s*)?trước\s*thuế|\blntt\b|profit\s*before\s*tax|\bpbt\b", re.IGNORECASE),
    ),
    (
        MetricCode.NET_INCOME,
        re.compile(r"\bNET_INCOME\b|lợi\s*nhuận\s*sau\s*thuế|(?<!biên\s)lợi\s*nhuận\s*ròng|lãi\s*ròng|\blnst\b|net\s*(?:income|profit(?!\s*margin))", re.IGNORECASE),
    ),
    (
        MetricCode.EPS,
        re.compile(r"\bEPS\b|lãi\s*cơ\s*bản\s*trên\s*cổ\s*phiếu|thu\s*nhập\s*trên\s*mỗi\s*cổ\s*phiếu|earnings\s*per\s*share", re.IGNORECASE),
    ),
    (
        MetricCode.CURRENT_ASSETS,
        re.compile(r"\bCURRENT_ASSETS\b|tài\s*sản\s*ngắn\s*hạn|current\s*assets", re.IGNORECASE),
    ),
    (
        MetricCode.NON_CURRENT_ASSETS,
        re.compile(r"\bNON_CURRENT_ASSETS\b|tài\s*sản\s*dài\s*hạn|non-current\s*assets", re.IGNORECASE),
    ),
    (
        MetricCode.TOTAL_ASSETS,
        re.compile(r"\bTOTAL_ASSETS\b|(?<!trên\s)tổng\s*(?:cộng\s*)?tài\s*sản|(?<!on\s)total\s*assets", re.IGNORECASE),
    ),
    (
        MetricCode.TOTAL_LIABILITIES,
        re.compile(r"\bTOTAL_LIABILITIES\b|nợ\s*phải\s*trả|tổng\s*nợ|total\s*liabilities", re.IGNORECASE),
    ),
    (
        MetricCode.TOTAL_EQUITY,
        re.compile(r"\bTOTAL_EQUITY\b|(?<!trên\s)vốn\s*chủ\s*sở\s*hữu|\bvcsh\b|(?<!on\s)total\s*(?:shareholders'?\s*)?equity", re.IGNORECASE),
    ),
    (
        MetricCode.ROE,
        re.compile(r"\bROE\b|return\s*on\s*equity|tỷ\s*suất\s*lợi\s*nhuận\s*trên\s*vốn\s*chủ\s*sở\s*hữu", re.IGNORECASE),
    ),
    (
        MetricCode.ROA,
        re.compile(r"\bROA\b|return\s*on\s*assets|tỷ\s*suất\s*lợi\s*nhuận\s*trên\s*tổng\s*tài\s*sản", re.IGNORECASE),
    ),
    (
        MetricCode.OPERATING_CASH_FLOW,
        re.compile(r"\bOPERATING_CASH_FLOW\b|lưu\s*chuyển\s*tiền\s*thuần\s*từ\s*(?:hđkd|hoạt\s*động\s*kinh\s*doanh)|dòng\s*tiền\s*(?:từ\s*)?hoạt\s*động\s*kinh\s*doanh|operating\s*cash\s*flow", re.IGNORECASE),
    ),
)

_COMPANY_ALIAS_MAP: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("FPT", re.compile(r"\bFPT\b", re.IGNORECASE)),
    ("VNM", re.compile(r"\b(?:VNM|Vinamilk|Sữa\s+Việt\s+Nam)\b", re.IGNORECASE)),
    ("VCB", re.compile(r"\b(?:VCB|Vietcombank)\b", re.IGNORECASE)),
    ("HPG", re.compile(r"\b(?:HPG|Hòa\s+Phát|Hoa\s+Phat)\b", re.IGNORECASE)),
    ("MWG", re.compile(r"\b(?:MWG|Thế\s+Giới\s+Di\s+Động)\b", re.IGNORECASE)),
    ("TCB", re.compile(r"\b(?:TCB|Techcombank)\b", re.IGNORECASE)),
    ("VIC", re.compile(r"\b(?:VIC|Vingroup)\b", re.IGNORECASE)),
    ("MSN", re.compile(r"\b(?:MSN|Masan)\b", re.IGNORECASE)),
    ("AAPL", re.compile(r"\b(?:AAPL|Apple)\b", re.IGNORECASE)),
    ("NVDA", re.compile(r"\b(?:NVDA|Nvidia)\b", re.IGNORECASE)),
    ("MSFT", re.compile(r"\b(?:MSFT|Microsoft)\b", re.IGNORECASE)),
)

_UPPER_TICKER_STOPWORDS = frozenset(
    {
        "SELECT", "FROM", "WHERE", "JOIN", "INNER", "LEFT", "RIGHT", "OUTER", "GROUP", "ORDER",
        "LIMIT", "OFFSET", "HAVING", "UNION", "INSERT", "UPDATE", "DELETE", "DROP", "ALTER",
        "TRUNCATE", "CREATE", "TABLE", "INDEX", "INTO", "VALUES", "SET", "SHOW", "GRANT",
        "REVOKE", "EXEC", "EXECUTE", "NULL", "TRUE", "FALSE", "AND", "NOT", "LIKE", "ILIKE",
        "BETWEEN", "ASC", "DESC", "WITH", "AS", "ON", "IN", "IS", "OR", "BY", "ALL", "ANY",
        "ROE", "ROA", "EPS", "COGS", "YOY", "CAGR", "EBITDA", "EBIT", "LNST", "LNTT", "VCSH",
        "TNDN", "CCDV", "HĐKD", "BCĐKT", "KQKD", "LCTT", "BCTC", "HĐQT", "CTCP", "TNHH",
        "VND", "VNĐ", "USD", "EUR", "JPY", "GAAP", "IFRS", "VAS", "NAM", "NĂM", "QUÝ",
        "THE", "FOR", "WHAT", "HOW", "WHY", "WHO", "ARE", "WAS", "WERE", "HAS", "HAVE",
    }
    | {m.value for m in MetricCode}
)


def extract_metric_codes_from_text(question: str) -> list[MetricCode]:
    """Extract canonical MetricCode items from a Vietnamese or English question."""
    matched: list[MetricCode] = []
    for code, pattern in _METRIC_PATTERNS:
        if pattern.search(question) and code not in matched:
            matched.append(code)
    return matched


def extract_fiscal_years_from_text(question: str) -> list[int]:
    """Extract 4-digit fiscal years from text, expanding any year ranges (e.g. 2022-2024)."""
    years: set[int] = set()
    for match in _YEAR_RANGE_RE.finditer(question):
        g1, g2, g3, g4 = match.groups()
        start_yr = int(g1 or g3)
        end_yr = int(g2 or g4)
        lo, hi = min(start_yr, end_yr), max(start_yr, end_yr)
        if hi - lo <= 30:
            years.update(range(lo, hi + 1))

    for match in _SINGLE_YEAR_RE.finditer(question):
        yr = int(match.group(1))
        if 1990 <= yr <= 2100:
            years.add(yr)
    return sorted(years)


def extract_periods_from_text(question: str) -> list[FiscalPeriod]:
    """Extract reporting periods (Q1..Q4, FY) when explicitly mentioned in the question."""
    periods: list[FiscalPeriod] = []
    q_patterns = (
        (FiscalPeriod.Q1, r"\b(?:Q1|quý\s*(?:1|I))\b"),
        (FiscalPeriod.Q2, r"\b(?:Q2|quý\s*(?:2|II))\b"),
        (FiscalPeriod.Q3, r"\b(?:Q3|quý\s*(?:3|III))\b"),
        (FiscalPeriod.Q4, r"\b(?:Q4|quý\s*(?:4|IV))\b"),
        (FiscalPeriod.FY, r"\b(?:FY|cả\s*năm|full\s*year)\b"),
    )
    for period_enum, pat in q_patterns:
        if re.search(pat, question, re.IGNORECASE) and period_enum not in periods:
            periods.append(period_enum)
    return periods


def extract_tickers_from_text(question: str) -> list[str]:
    """Extract stock ticker symbols and company aliases from a natural language question."""
    tickers: list[str] = []
    for symbol, pattern in _COMPANY_ALIAS_MAP:
        if pattern.search(question) and symbol not in tickers:
            tickers.append(symbol)

    for token in re.findall(r"\b[A-Z]{3,4}\b", question):
        if token not in _UPPER_TICKER_STOPWORDS and token not in tickers:
            tickers.append(token)
    return tickers


def extract_statement_types_from_text(question: str) -> list[StatementType]:
    """Extract financial statement categories if the user asks for a statement group."""
    stmts: list[StatementType] = []
    rules = (
        (StatementType.INCOME_STATEMENT, r"kết\s*quả\s*(?:hoạt\s*động\s*)?kinh\s*doanh|\bkqkd\b|income\s*statement"),
        (StatementType.BALANCE_SHEET, r"bảng\s*cân\s*đối\s*kế\s*toán|\bbcđkt\b|balance\s*sheet"),
        (StatementType.CASH_FLOW, r"lưu\s*chuyển\s*tiền\s*tệ|\blctt\b|cash\s*flow\s*statement"),
        (StatementType.RATIOS, r"chỉ\s*số\s*tài\s*chính|financial\s*ratios"),
    )
    for stmt_enum, pat in rules:
        if re.search(pat, question, re.IGNORECASE) and stmt_enum not in stmts:
            stmts.append(stmt_enum)
    return stmts


def build_sql_tool_prompt(
    question: str,
    *,
    allowed_access_levels: Sequence[AccessLevel],
    tickers: Sequence[str] | None = None,
    fiscal_years: Sequence[int] | None = None,
) -> str:
    """Format the user prompt for Gemini Text-to-SQL generation."""
    access_str = ", ".join(f"'{lvl.value}'" for lvl in allowed_access_levels)
    hint_tickers = ", ".join(tickers) if tickers else "auto-detect from question"
    hint_years = ", ".join(str(y) for y in fiscal_years) if fiscal_years else "auto-detect from question"
    return (
        f"Quantitative Question: {question.strip()}\n"
        f"RBAC Allowed Access Levels: [{access_str}]\n"
        f"Hint Tickers: {hint_tickers}\n"
        f"Hint Fiscal Years: {hint_years}\n\n"
        "Generate a safe read-only SELECT query and structured filter metadata matching the JSON schema."
    )


__all__ = [
    "FINANCIAL_METRICS_SCHEMA_DDL",
    "SQL_TOOL_PROMPT_VERSION",
    "SQL_TOOL_SYSTEM_PROMPT",
    "build_sql_tool_prompt",
    "extract_fiscal_years_from_text",
    "extract_metric_codes_from_text",
    "extract_periods_from_text",
    "extract_statement_types_from_text",
    "extract_tickers_from_text",
]
