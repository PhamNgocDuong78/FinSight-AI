"""SQLAlchemy ORM model for structured financial metrics (Text-to-SQL target table)."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.postgres import Base
from app.models.enums import StatementType


class FinancialMetric(Base):
    """Normalized financial indicator extracted from financial statements."""

    __tablename__ = "financial_metrics"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    ticker: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    period: Mapped[str] = mapped_column(String(16), nullable=False)
    statement_type: Mapped[str] = mapped_column(
        String(32),
        default=StatementType.INCOME_STATEMENT.value,
        nullable=False,
    )
    metric_code: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    metric_name_vi: Mapped[str] = mapped_column(String(255), nullable=False)
    metric_name_en: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    source_page: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
