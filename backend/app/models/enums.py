"""Domain enumerations for FinSight AI database models."""

from enum import StrEnum


class UserRole(StrEnum):
    """Role-based access control (RBAC) roles."""

    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"


class FiscalPeriod(StrEnum):
    """Financial reporting period."""

    FY = "FY"
    Q1 = "Q1"
    Q2 = "Q2"
    Q3 = "Q3"
    Q4 = "Q4"


class DocumentLanguage(StrEnum):
    """Primary language of a financial document."""

    VI = "vi"
    EN = "en"


class AccessLevel(StrEnum):
    """Document data classification level for RBAC filtering."""

    PUBLIC = "public"
    INTERNAL_FINANCE = "internal_finance"
    EXECUTIVE_ONLY = "executive_only"


class DocumentStatus(StrEnum):
    """Ingestion pipeline status for an uploaded document."""

    PENDING = "pending"
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"


class ChunkType(StrEnum):
    """Type of parent chunk extracted from a document."""

    TEXT = "text"
    TABLE = "table"
    CHART_SUMMARY = "chart_summary"


class StatementType(StrEnum):
    """Financial statement category for structured metrics."""

    INCOME_STATEMENT = "income_statement"
    BALANCE_SHEET = "balance_sheet"
    CASH_FLOW = "cash_flow"
    RATIOS = "ratios"


class MetricCode(StrEnum):
    """Standardized financial metric codes stored in financial_metrics."""

    REVENUE = "REVENUE"
    COGS = "COGS"
    GROSS_PROFIT = "GROSS_PROFIT"
    OPERATING_PROFIT = "OPERATING_PROFIT"
    PROFIT_BEFORE_TAX = "PROFIT_BEFORE_TAX"
    NET_INCOME = "NET_INCOME"
    EPS = "EPS"
    CURRENT_ASSETS = "CURRENT_ASSETS"
    NON_CURRENT_ASSETS = "NON_CURRENT_ASSETS"
    TOTAL_ASSETS = "TOTAL_ASSETS"
    TOTAL_LIABILITIES = "TOTAL_LIABILITIES"
    TOTAL_EQUITY = "TOTAL_EQUITY"
    ROE = "ROE"
    ROA = "ROA"
    GROSS_MARGIN = "GROSS_MARGIN"
    NET_MARGIN = "NET_MARGIN"
    OPERATING_CASH_FLOW = "OPERATING_CASH_FLOW"


class MetricUnit(StrEnum):
    """Standardized measurement units for financial metrics."""

    VND_BILLION = "VND_BILLION"
    VND_MILLION = "VND_MILLION"
    VND = "VND"
    USD_BILLION = "USD_BILLION"
    USD_MILLION = "USD_MILLION"
    USD = "USD"
    PERCENT = "PERCENT"


class MessageRole(StrEnum):
    """Sender role in a chat conversation."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"

