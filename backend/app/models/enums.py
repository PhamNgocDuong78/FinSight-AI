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


class MessageRole(StrEnum):
    """Sender role in a chat conversation."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"

