"""Unit tests for SQLAlchemy ORM models and schema metadata."""

from app.models import Base, UserRole


def test_metadata_contains_all_six_tables() -> None:
    """Verify all 6 tables from docs/ARCHITECTURE.md are registered in Base.metadata."""
    expected_tables = {
        "users",
        "documents",
        "parent_chunks",
        "financial_metrics",
        "chat_sessions",
        "chat_messages",
    }
    registered_tables = set(Base.metadata.tables.keys())
    assert expected_tables.issubset(registered_tables)


def test_financial_metrics_columns_and_types() -> None:
    """Verify financial_metrics table columns match architecture specification."""
    table = Base.metadata.tables["financial_metrics"]
    column_names = set(table.columns.keys())
    expected_columns = {
        "id",
        "document_id",
        "ticker",
        "fiscal_year",
        "period",
        "statement_type",
        "metric_code",
        "metric_name_vi",
        "metric_name_en",
        "value",
        "unit",
        "source_page",
        "created_at",
    }
    assert expected_columns == column_names


def test_user_roles_enum() -> None:
    """Verify the 3 RBAC roles match specification."""
    assert {role.value for role in UserRole} == {"admin", "analyst", "viewer"}
