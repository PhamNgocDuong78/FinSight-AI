"""Deterministic number parsing, VAS table classification, and accounting verification rules."""

import re
import uuid
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.agents.prompts.financial_etl import CANONICAL_METRIC_DEFINITIONS
from app.models.enums import FiscalPeriod, MetricCode, MetricUnit
from app.schemas.financial_etl import (
    QUANTIZE_4DP,
    ExtractedFinancialMetricItem,
    NormalizedFinancialMetric,
)
from app.schemas.ingestion import ExtractedTable, ParsedPage

_YEAR_PATTERN = re.compile(r"\b(20\d{2}|19\d{2})\b")
_GROWTH_COL_PATTERN = re.compile(
    r"tăng\s*trưởng|thay\s*đổi|biến\s*động|growth|change|yoy|chênh\s*lệch",
    re.IGNORECASE,
)
_MD_TABLE_SEP_RE = re.compile(r"^\s*\|(?:\s*:?-{2,}:?\s*\|)+\s*$")
_VI_THOUSAND_DOTS_RE = re.compile(r"^-?\d{1,3}(?:\.\d{3})+$")
_EN_THOUSAND_COMMAS_RE = re.compile(r"^-?\d{1,3}(?:,\d{3})+$")

# Keyword patterns mapped to MetricCode (checked in priority order)
_ROW_KEYWORD_RULES: list[tuple[MetricCode, re.Pattern[str]]] = [
    (MetricCode.REVENUE, re.compile(r"doanh\s*thu\s*thuần|net\s*revenue|total\s*revenue", re.IGNORECASE)),
    (MetricCode.COGS, re.compile(r"giá\s*vốn\s*hàng\s*bán|cost\s*of\s*(?:goods\s*sold|sales)", re.IGNORECASE)),
    (MetricCode.GROSS_PROFIT, re.compile(r"lợi\s*nhuận\s*gộp|gross\s*profit", re.IGNORECASE)),
    (
        MetricCode.OPERATING_PROFIT,
        re.compile(r"lợi\s*nhuận\s*thuần\s*từ\s*hđkd|operating\s*(?:profit|income)", re.IGNORECASE),
    ),
    (
        MetricCode.PROFIT_BEFORE_TAX,
        re.compile(r"lợi\s*nhuận\s*(?:kế\s*toán\s*)?trước\s*thuế|profit\s*before\s*tax", re.IGNORECASE),
    ),
    (
        MetricCode.NET_INCOME,
        re.compile(r"lợi\s*nhuận\s*sau\s*thuế|net\s*(?:income|profit)", re.IGNORECASE),
    ),
    (
        MetricCode.EPS,
        re.compile(r"lãi\s*cơ\s*bản\s*trên\s*cổ\s*phiếu|\beps\b|earnings\s*per\s*share", re.IGNORECASE),
    ),
    (MetricCode.CURRENT_ASSETS, re.compile(r"tài\s*sản\s*ngắn\s*hạn|current\s*assets", re.IGNORECASE)),
    (
        MetricCode.NON_CURRENT_ASSETS,
        re.compile(r"tài\s*sản\s*dài\s*hạn|non-current\s*assets", re.IGNORECASE),
    ),
    (MetricCode.TOTAL_ASSETS, re.compile(r"tổng\s*(?:cộng\s*)?tài\s*sản|total\s*assets", re.IGNORECASE)),
    (MetricCode.TOTAL_LIABILITIES, re.compile(r"nợ\s*phải\s*trả|total\s*liabilities", re.IGNORECASE)),
    (
        MetricCode.TOTAL_EQUITY,
        re.compile(r"vốn\s*chủ\s*sở\s*hữu|total\s*(?:shareholders'?\s*)?equity", re.IGNORECASE),
    ),
    (
        MetricCode.OPERATING_CASH_FLOW,
        re.compile(r"lưu\s*chuyển\s*tiền\s*thuần\s*từ\s*hđkd|operating\s*cash\s*flow", re.IGNORECASE),
    ),
    (MetricCode.ROE, re.compile(r"\broe\b|return\s*on\s*equity", re.IGNORECASE)),
    (MetricCode.ROA, re.compile(r"\broa\b|return\s*on\s*assets", re.IGNORECASE)),
]


def parse_financial_number(
    raw: str | float | Decimal | None,
    language: str = "vi",
) -> Decimal | None:
    """Parse a financial number string (VI or EN format) into a 4-decimal quantized Decimal."""
    if raw is None:
        return None
    if isinstance(raw, (int, float, Decimal)):
        return Decimal(str(raw)).quantize(QUANTIZE_4DP, rounding=ROUND_HALF_UP)

    text = str(raw).strip()
    if not text or text in {"-", "—", "N/A", "n/a", ""}:
        return None

    is_negative = (text.startswith("(") and text.endswith(")")) or text.startswith("-")
    cleaned = re.sub(r"[^\d.,]", "", text)
    if not cleaned:
        return None

    if "." in cleaned and "," in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "." in cleaned:
        if language.lower() == "vi" and _VI_THOUSAND_DOTS_RE.match(cleaned) or cleaned.count(".") > 1:
            cleaned = cleaned.replace(".", "")
    elif "," in cleaned:
        if language.lower() == "en" and _EN_THOUSAND_COMMAS_RE.match(cleaned) or cleaned.count(",") > 1:
            cleaned = cleaned.replace(",", "")
        else:
            cleaned = cleaned.replace(",", ".")

    try:
        val = Decimal(cleaned)
        if is_negative and val > 0:
            val = -val
        return val.quantize(QUANTIZE_4DP, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None


def classify_metric_row(label: str) -> MetricCode | None:
    """Map a financial table row label (VI or EN) to a canonical MetricCode."""
    normalized = label.strip()
    if not normalized or re.search(r"tổng\s*cộng\s*nguồn\s*vốn", normalized, re.IGNORECASE):
        return None
    for code, pattern in _ROW_KEYWORD_RULES:
        if pattern.search(normalized):
            return code
    return None


def _detect_unit_from_text(text: str, metric_code: MetricCode) -> MetricUnit:
    """Infer MetricUnit from row/column header text or fall back to canonical unit."""
    lower = text.lower()
    if metric_code in {MetricCode.ROE, MetricCode.ROA, MetricCode.GROSS_MARGIN, MetricCode.NET_MARGIN}:
        return MetricUnit.PERCENT
    if metric_code == MetricCode.EPS:
        return MetricUnit.USD if "usd" in lower or "$" in lower else MetricUnit.VND
    if "tỷ" in lower or "billion" in lower:
        return MetricUnit.USD_BILLION if "usd" in lower else MetricUnit.VND_BILLION
    if "triệu" in lower or "million" in lower:
        return MetricUnit.USD_MILLION if "usd" in lower else MetricUnit.VND_MILLION
    if "usd" in lower or "$" in lower:
        return MetricUnit.USD_MILLION
    if "%" in lower:
        return MetricUnit.PERCENT
    return CANONICAL_METRIC_DEFINITIONS[metric_code][3]


def _extract_tables_from_page(page: ParsedPage) -> list[ExtractedTable]:
    """Return structured tables on the page, including any inline Markdown tables."""
    if page.tables:
        return page.tables
    tables: list[ExtractedTable] = []
    lines = page.markdown.splitlines()
    idx = 0
    while idx < len(lines):
        line = lines[idx].strip()
        if (
            line.startswith("|")
            and idx + 1 < len(lines)
            and _MD_TABLE_SEP_RE.match(lines[idx + 1].strip())
        ):
            t_lines: list[str] = []
            while idx < len(lines) and lines[idx].strip().startswith("|"):
                t_lines.append(lines[idx].strip())
                idx += 1
            split_row = lambda r: [c.strip() for c in re.split(r"(?<!\\)\|", r.strip().strip("|"))]
            tables.append(
                ExtractedTable(
                    page_number=page.page_number,
                    table_index=len(tables),
                    headers=split_row(t_lines[0]),
                    rows=[split_row(r) for r in t_lines[2:] if r.strip()],
                    markdown="\n".join(t_lines),
                )
            )
        else:
            idx += 1
    return tables


def extract_page_metrics_by_rules(
    page: ParsedPage,
    *,
    document_id: uuid.UUID,
    ticker: str,
    default_fiscal_year: int,
    default_period: FiscalPeriod = FiscalPeriod.FY,
    language: str = "vi",
) -> list[NormalizedFinancialMetric]:
    """Deterministically extract financial metrics from tables and narrative on a page."""
    results: list[NormalizedFinancialMetric] = []
    seen_keys: set[tuple[MetricCode, int, FiscalPeriod]] = set()

    for table in _extract_tables_from_page(page):
        col_specs: list[tuple[int, int, str]] = []
        for col_idx, header in enumerate(table.headers):
            if col_idx == 0 or _GROWTH_COL_PATTERN.search(header):
                continue
            if year_match := _YEAR_PATTERN.search(header):
                col_specs.append((col_idx, int(year_match.group(1)), header))

        for row in table.rows:
            if not row:
                continue
            metric_code = classify_metric_row(row[0])
            if metric_code is None:
                continue
            name_vi, name_en, stmt_type, _ = CANONICAL_METRIC_DEFINITIONS[metric_code]
            for col_idx, col_year, col_header in col_specs:
                if col_idx >= len(row):
                    continue
                val = parse_financial_number(row[col_idx], language=language)
                if val is None:
                    continue
                key = (metric_code, col_year, default_period)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                unit = _detect_unit_from_text(f"{row[0]} {col_header}", metric_code)
                results.append(
                    NormalizedFinancialMetric(
                        document_id=document_id,
                        ticker=ticker,
                        fiscal_year=col_year,
                        period=default_period,
                        statement_type=stmt_type,
                        metric_code=metric_code,
                        metric_name_vi=name_vi,
                        metric_name_en=name_en,
                        value=val,
                        unit=unit,
                        source_page=page.page_number,
                    )
                )

    text_body = page.markdown or page.raw_text
    ratio_patterns: list[tuple[MetricCode, re.Pattern[str]]] = [
        (MetricCode.ROE, re.compile(r"\bROE\b[^0-9\n]{0,35}?(\d+[.,]\d+|\d+)\s*%", re.IGNORECASE)),
        (MetricCode.ROA, re.compile(r"\bROA\b[^0-9\n]{0,35}?(\d+[.,]\d+|\d+)\s*%", re.IGNORECASE)),
    ]
    for r_code, r_pat in ratio_patterns:
        if match := r_pat.search(text_body):
            val = parse_financial_number(match.group(1), language=language)
            key = (r_code, default_fiscal_year, default_period)
            if val is not None and key not in seen_keys:
                seen_keys.add(key)
                name_vi, name_en, stmt_type, unit = CANONICAL_METRIC_DEFINITIONS[r_code]
                results.append(
                    NormalizedFinancialMetric(
                        document_id=document_id,
                        ticker=ticker,
                        fiscal_year=default_fiscal_year,
                        period=default_period,
                        statement_type=stmt_type,
                        metric_code=r_code,
                        metric_name_vi=name_vi,
                        metric_name_en=name_en,
                        value=val,
                        unit=unit,
                        source_page=page.page_number,
                    )
                )
    return results


def reconcile_llm_and_rule_metrics(
    llm_items: list[ExtractedFinancialMetricItem],
    rule_metrics: list[NormalizedFinancialMetric],
    *,
    document_id: uuid.UUID,
    ticker: str,
) -> list[NormalizedFinancialMetric]:
    """Cross-validate LLM Structured Output against rule-based table cells to prevent scale errors."""
    rule_map = {(m.metric_code, m.fiscal_year, m.period): m for m in rule_metrics}
    merged: dict[tuple[MetricCode, int, FiscalPeriod], NormalizedFinancialMetric] = {}

    for item in llm_items:
        key = (item.metric_code, item.fiscal_year, item.period)
        name_vi, name_en, canonical_stmt, canonical_unit = CANONICAL_METRIC_DEFINITIONS[
            item.metric_code
        ]
        llm_dec = Decimal(str(item.value)).quantize(QUANTIZE_4DP, rounding=ROUND_HALF_UP)

        if rule_match := rule_map.get(key):
            # If LLM misread Vietnamese dot-thousands separator (off by 1000x) or drifted, trust exact table cell
            if llm_dec != rule_match.value:
                llm_dec = rule_match.value
            unit = rule_match.unit
            source_page = rule_match.source_page
            stmt_type = rule_match.statement_type
        else:
            unit = item.unit or canonical_unit
            source_page = item.source_page
            stmt_type = item.statement_type or canonical_stmt

        merged[key] = NormalizedFinancialMetric(
            document_id=document_id,
            ticker=ticker,
            fiscal_year=item.fiscal_year,
            period=item.period,
            statement_type=stmt_type,
            metric_code=item.metric_code,
            metric_name_vi=item.metric_name_vi.strip() or name_vi,
            metric_name_en=item.metric_name_en.strip() or name_en,
            value=llm_dec,
            unit=unit,
            source_page=source_page,
        )

    for key, rule_metric in rule_map.items():
        if key not in merged:
            merged[key] = rule_metric

    return list(merged.values())


def verify_accounting_identities(metrics: list[NormalizedFinancialMetric]) -> list[str]:
    """Verify core accounting identities (Assets = Liabilities + Equity; Gross Profit = Revenue - COGS)."""
    warnings: list[str] = []
    by_year_period: dict[tuple[int, FiscalPeriod], dict[MetricCode, Decimal]] = {}
    for m in metrics:
        by_year_period.setdefault((m.fiscal_year, m.period), {})[m.metric_code] = m.value

    for (year, period), m_map in sorted(by_year_period.items()):
        if all(k in m_map for k in (MetricCode.REVENUE, MetricCode.COGS, MetricCode.GROSS_PROFIT)):
            expected_gp = m_map[MetricCode.REVENUE] - m_map[MetricCode.COGS]
            if abs(expected_gp - m_map[MetricCode.GROSS_PROFIT]) > Decimal("1.0"):
                warnings.append(
                    f"[{year} {period.value}] GROSS_PROFIT ({m_map[MetricCode.GROSS_PROFIT]}) "
                    f"!= REVENUE - COGS ({expected_gp})"
                )
        if all(
            k in m_map
            for k in (MetricCode.TOTAL_ASSETS, MetricCode.TOTAL_LIABILITIES, MetricCode.TOTAL_EQUITY)
        ):
            expected_assets = m_map[MetricCode.TOTAL_LIABILITIES] + m_map[MetricCode.TOTAL_EQUITY]
            if abs(expected_assets - m_map[MetricCode.TOTAL_ASSETS]) > Decimal("1.0"):
                warnings.append(
                    f"[{year} {period.value}] TOTAL_ASSETS ({m_map[MetricCode.TOTAL_ASSETS]}) "
                    f"!= TOTAL_LIABILITIES + TOTAL_EQUITY ({expected_assets})"
                )
    return warnings
