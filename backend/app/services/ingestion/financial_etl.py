"""Structured Financial Metric Extractor (Financial ETL) using Google GenAI SDK and validation rules."""

import uuid
from abc import ABC, abstractmethod
from typing import Any

import httpx
from google import genai
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.prompts.financial_etl import (
    FINANCIAL_ETL_SYSTEM_PROMPT,
    build_financial_etl_prompt,
)
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.enums import FiscalPeriod, MetricCode
from app.models.financial_metric import FinancialMetric
from app.schemas.financial_etl import (
    FinancialETLResult,
    FinancialTableExtractionOutput,
    NormalizedFinancialMetric,
)
from app.schemas.ingestion import ParsedDocument, ParsedPage
from app.services.ingestion.financial_rules import (
    classify_metric_row,
    extract_page_metrics_by_rules,
    parse_financial_number,
    reconcile_llm_and_rule_metrics,
    verify_accounting_identities,
)

logger = get_logger(__name__)

_DEPRECATED_GEMINI_PREFIXES = ("gemini-2.5", "gemini-2.0", "gemini-1.5")
_DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
_PLACEHOLDER_API_KEYS = {"", "your-gemini-api-key-here", "changeme"}


def normalize_gemini_model_name(model_name: str) -> str:
    """Substitute deprecated Gemini 1.5/2.0/2.5 models with gemini-3.8-flash."""
    cleaned = model_name.strip()
    if not cleaned or cleaned.startswith(_DEPRECATED_GEMINI_PREFIXES):
        logger.info(
            "substituting_deprecated_gemini_model",
            requested_model=cleaned,
            substituted_model=_DEFAULT_GEMINI_MODEL,
        )
        return _DEFAULT_GEMINI_MODEL
    return cleaned


class BaseFinancialExtractor(ABC):
    """Abstract interface for pluggable LLM structured financial extractors."""

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """Return True if the extractor is configured and ready to call."""

    @abstractmethod
    def extract_from_markdown(
        self,
        markdown_content: str,
        *,
        ticker: str,
        default_fiscal_year: int,
        default_period: FiscalPeriod = FiscalPeriod.FY,
        source_page: int = 1,
        language: str = "vi",
    ) -> FinancialTableExtractionOutput:
        """Synchronously extract structured financial metrics from Markdown content."""

    @abstractmethod
    async def aextract_from_markdown(
        self,
        markdown_content: str,
        *,
        ticker: str,
        default_fiscal_year: int,
        default_period: FiscalPeriod = FiscalPeriod.FY,
        source_page: int = 1,
        language: str = "vi",
    ) -> FinancialTableExtractionOutput:
        """Asynchronously extract structured financial metrics from Markdown content."""


class GeminiStructuredFinancialExtractor(BaseFinancialExtractor):
    """Financial metric extractor powered by Google GenAI SDK (`google-genai`) Structured Output."""

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
        client: genai.Client | None = None,
    ) -> None:
        settings = get_settings()
        self._api_key = (api_key if api_key is not None else settings.gemini_api_key).strip()
        self.model_name = normalize_gemini_model_name(model_name or settings.gemini_fast_model)
        self._client = client

    @property
    def is_available(self) -> bool:
        """Return True when an injected client or valid GEMINI_API_KEY is present."""
        return self._client is not None or self._api_key not in _PLACEHOLDER_API_KEYS

    def _get_client(self) -> genai.Client:
        if self._client is None:
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def _build_request_kwargs(
        self,
        markdown_content: str,
        *,
        ticker: str,
        default_fiscal_year: int,
        default_period: FiscalPeriod,
        source_page: int,
        language: str,
    ) -> dict[str, Any]:
        prompt = build_financial_etl_prompt(
            markdown_content=markdown_content,
            ticker=ticker,
            default_fiscal_year=default_fiscal_year,
            default_period=default_period,
            source_page=source_page,
            language=language,
        )
        return {
            "model": self.model_name,
            "system_instruction": FINANCIAL_ETL_SYSTEM_PROMPT,
            "input": prompt,
            "response_format": {
                "type": "text",
                "mime_type": "application/json",
                "schema": FinancialTableExtractionOutput.model_json_schema(),
            },
            "store": False,
        }

    def extract_from_markdown(
        self,
        markdown_content: str,
        *,
        ticker: str,
        default_fiscal_year: int,
        default_period: FiscalPeriod = FiscalPeriod.FY,
        source_page: int = 1,
        language: str = "vi",
    ) -> FinancialTableExtractionOutput:
        """Call Gemini Interactions API with Pydantic JSON Schema (`response_format`)."""
        if not markdown_content.strip() or not self.is_available:
            return FinancialTableExtractionOutput(metrics=[])
        kwargs = self._build_request_kwargs(
            markdown_content,
            ticker=ticker,
            default_fiscal_year=default_fiscal_year,
            default_period=default_period,
            source_page=source_page,
            language=language,
        )
        interaction = self._get_client().interactions.create(**kwargs)
        return FinancialTableExtractionOutput.model_validate_json(
            interaction.output_text or '{"metrics": []}'
        )

    async def aextract_from_markdown(
        self,
        markdown_content: str,
        *,
        ticker: str,
        default_fiscal_year: int,
        default_period: FiscalPeriod = FiscalPeriod.FY,
        source_page: int = 1,
        language: str = "vi",
    ) -> FinancialTableExtractionOutput:
        """Asynchronously call Gemini Interactions API with Pydantic JSON Schema."""
        if not markdown_content.strip() or not self.is_available:
            return FinancialTableExtractionOutput(metrics=[])
        kwargs = self._build_request_kwargs(
            markdown_content,
            ticker=ticker,
            default_fiscal_year=default_fiscal_year,
            default_period=default_period,
            source_page=source_page,
            language=language,
        )
        interaction = await self._get_client().aio.interactions.create(**kwargs)
        return FinancialTableExtractionOutput.model_validate_json(
            interaction.output_text or '{"metrics": []}'
        )


class FinancialETLService:
    """High-level service combining Gemini Structured Output, validation rules, and DB persistence."""

    def __init__(
        self,
        extractor: BaseFinancialExtractor | None = None,
        enable_rule_validation: bool = True,
    ) -> None:
        self.extractor = extractor or GeminiStructuredFinancialExtractor()
        self.enable_rule_validation = enable_rule_validation

    def _rule_metrics(
        self, page: ParsedPage, doc_id: uuid.UUID, ticker: str, year: int, period: FiscalPeriod, lang: str
    ) -> list[NormalizedFinancialMetric]:
        if not self.enable_rule_validation:
            return []
        return extract_page_metrics_by_rules(
            page, document_id=doc_id, ticker=ticker, default_fiscal_year=year, default_period=period, language=lang
        )

    def extract_page_metrics(
        self,
        page: ParsedPage,
        *,
        document_id: uuid.UUID,
        ticker: str,
        fiscal_year: int,
        period: FiscalPeriod = FiscalPeriod.FY,
        language: str = "vi",
    ) -> list[NormalizedFinancialMetric]:
        """Synchronously extract and validate financial metrics from a single ParsedPage."""
        rule_metrics = self._rule_metrics(page, document_id, ticker, fiscal_year, period, language)
        llm_items = []
        if self.extractor.is_available:
            try:
                out = self.extractor.extract_from_markdown(
                    page.markdown or page.raw_text,
                    ticker=ticker,
                    default_fiscal_year=fiscal_year,
                    default_period=period,
                    source_page=page.page_number,
                    language=language,
                )
                llm_items = out.metrics
            except (ValueError, RuntimeError, OSError, httpx.HTTPError) as exc:
                logger.warning("gemini_etl_fallback_to_rules", page=page.page_number, error=str(exc))
        return reconcile_llm_and_rule_metrics(llm_items, rule_metrics, document_id=document_id, ticker=ticker)

    async def aextract_page_metrics(
        self,
        page: ParsedPage,
        *,
        document_id: uuid.UUID,
        ticker: str,
        fiscal_year: int,
        period: FiscalPeriod = FiscalPeriod.FY,
        language: str = "vi",
    ) -> list[NormalizedFinancialMetric]:
        """Asynchronously extract and validate financial metrics from a single ParsedPage."""
        rule_metrics = self._rule_metrics(page, document_id, ticker, fiscal_year, period, language)
        llm_items = []
        if self.extractor.is_available:
            try:
                out = await self.extractor.aextract_from_markdown(
                    page.markdown or page.raw_text,
                    ticker=ticker,
                    default_fiscal_year=fiscal_year,
                    default_period=period,
                    source_page=page.page_number,
                    language=language,
                )
                llm_items = out.metrics
            except (ValueError, RuntimeError, OSError, httpx.HTTPError) as exc:
                logger.warning("gemini_etl_async_fallback_to_rules", page=page.page_number, error=str(exc))
        return reconcile_llm_and_rule_metrics(llm_items, rule_metrics, document_id=document_id, ticker=ticker)

    def extract_document_metrics(
        self,
        parsed_doc: ParsedDocument,
        *,
        document_id: uuid.UUID,
        ticker: str,
        fiscal_year: int,
        period: FiscalPeriod = FiscalPeriod.FY,
        language: str = "vi",
    ) -> list[NormalizedFinancialMetric]:
        """Synchronously extract and deduplicate metrics across all pages of a ParsedDocument."""
        deduped: dict[tuple[MetricCode, int, FiscalPeriod], NormalizedFinancialMetric] = {}
        for page in parsed_doc.pages:
            for m in self.extract_page_metrics(
                page, document_id=document_id, ticker=ticker, fiscal_year=fiscal_year, period=period, language=language
            ):
                deduped.setdefault((m.metric_code, m.fiscal_year, m.period), m)
        return list(deduped.values())

    async def aextract_document_metrics(
        self,
        parsed_doc: ParsedDocument,
        *,
        document_id: uuid.UUID,
        ticker: str,
        fiscal_year: int,
        period: FiscalPeriod = FiscalPeriod.FY,
        language: str = "vi",
    ) -> list[NormalizedFinancialMetric]:
        """Asynchronously extract and deduplicate metrics across all pages of a ParsedDocument."""
        deduped: dict[tuple[MetricCode, int, FiscalPeriod], NormalizedFinancialMetric] = {}
        for page in parsed_doc.pages:
            for m in await self.aextract_page_metrics(
                page, document_id=document_id, ticker=ticker, fiscal_year=fiscal_year, period=period, language=language
            ):
                deduped.setdefault((m.metric_code, m.fiscal_year, m.period), m)
        return list(deduped.values())

    async def run_etl(
        self,
        session: AsyncSession,
        parsed_doc: ParsedDocument,
        *,
        document_id: uuid.UUID,
        ticker: str,
        fiscal_year: int,
        period: FiscalPeriod = FiscalPeriod.FY,
        language: str = "vi",
        replace_existing: bool = True,
    ) -> FinancialETLResult:
        """Extract normalized metrics from a ParsedDocument and persist them into PostgreSQL."""
        normalized_metrics = await self.aextract_document_metrics(
            parsed_doc, document_id=document_id, ticker=ticker, fiscal_year=fiscal_year, period=period, language=language
        )
        if warnings := verify_accounting_identities(normalized_metrics):
            logger.warning("accounting_identity_warnings", ticker=ticker, warnings=warnings)

        if replace_existing:
            await session.execute(delete(FinancialMetric).where(FinancialMetric.document_id == document_id))

        orm_rows = [
            FinancialMetric(
                id=m.id,
                document_id=m.document_id,
                ticker=m.ticker,
                fiscal_year=m.fiscal_year,
                period=m.period.value,
                statement_type=m.statement_type.value,
                metric_code=m.metric_code.value,
                metric_name_vi=m.metric_name_vi,
                metric_name_en=m.metric_name_en,
                value=m.value,
                unit=m.unit.value,
                source_page=m.source_page,
            )
            for m in normalized_metrics
        ]
        session.add_all(orm_rows)
        await session.commit()

        logger.info("financial_etl_completed", document_id=str(document_id), ticker=ticker, count=len(orm_rows))
        return FinancialETLResult(
            document_id=document_id,
            ticker=ticker.strip().upper(),
            fiscal_year=fiscal_year,
            period=period,
            metrics=normalized_metrics,
            persisted_count=len(orm_rows),
        )


__all__ = [
    "BaseFinancialExtractor",
    "FinancialETLService",
    "GeminiStructuredFinancialExtractor",
    "classify_metric_row",
    "extract_page_metrics_by_rules",
    "normalize_gemini_model_name",
    "parse_financial_number",
    "reconcile_llm_and_rule_metrics",
    "verify_accounting_identities",
]
