"""Structured SQL Query Tool (Task 3.1) with Text-to-SQL, RBAC, and Anti-SQL-Injection Guard."""

import re
import uuid
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

import httpx
from fastapi import status
from google import genai
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.prompts.financial_etl import CANONICAL_METRIC_DEFINITIONS
from app.agents.prompts.sql_tool import (
    SQL_TOOL_SYSTEM_PROMPT,
    build_sql_tool_prompt,
    extract_fiscal_years_from_text,
    extract_metric_codes_from_text,
    extract_periods_from_text,
    extract_statement_types_from_text,
    extract_tickers_from_text,
)
from app.core.config import get_settings
from app.core.exceptions import FinSightError
from app.core.logging import get_logger
from app.models.enums import (
    AccessLevel,
    FiscalPeriod,
    MetricCode,
    MetricUnit,
    StatementType,
    UserRole,
)
from app.schemas.retrieval import parse_user_role, resolve_allowed_access_levels
from app.schemas.sql_tool import (
    FinancialMetricRecord,
    LLMGeneratedSQLOutput,
    SQLQuerySpec,
    SQLToolResult,
)
from app.services.ingestion.financial_etl import normalize_gemini_model_name

logger = get_logger(__name__)

ALLOWED_SQL_TABLES: frozenset[str] = frozenset({"financial_metrics", "documents"})
_PLACEHOLDER_API_KEYS = {"", "your-gemini-api-key-here", "changeme"}

_FORBIDDEN_SQL_KEYWORDS_RE = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|REPLACE|UPSERT|MERGE|"
    r"GRANT|REVOKE|RENAME|COMMENT|VACUUM|ANALYZE|COPY|CALL|DO|EXECUTE|EXEC|"
    r"PREPARE|DEALLOCATE|SET|RESET|SHOW|BEGIN|COMMIT|ROLLBACK|SAVEPOINT|LOCK|UNLOCK|"
    r"INTO|UNION|INTERSECT|EXCEPT|RETURNING|TRIGGER|FUNCTION|PROCEDURE)\b"
    r"|\bFOR\s+(?:UPDATE|SHARE|NO\s+KEY\s+UPDATE|KEY\s+SHARE)\b",
    re.IGNORECASE,
)
_FORBIDDEN_CATALOG_OR_FUNC_RE = re.compile(
    r"\b(?:pg_catalog|information_schema|pg_toast|pg_temp|pg_[a-z0-9_]+|"
    r"lo_import|lo_export|dblink|dblink_exec|current_setting|set_config|"
    r"query_to_xml|table_to_xml|database_to_xml|users|chat_sessions|chat_messages|"
    r"parent_chunks|alembic_version)\b"
    r"|\b(?:chr|ascii|convert_from|convert_to|decode|encode)\s*\("
    r"|\bpublic\s*\."
    r"|\b0x[0-9a-fA-F]+\b",
    re.IGNORECASE,
)
_TAUTOLOGY_INJECTION_RE = re.compile(
    r"['\"][^'\"]*['\"]\s*(?:=|!=|<>)\s*['\"][^'\"]*['\"]"
    r"|\b\d+\s*(?:=|<|>|<=|>=|<>|!=)\s*\d+\b"
    r"|(?<!\.)\b([a-zA-Z_]\w*)\s*=\s*(?!\.)\1\b"
    r"|\bOR\s+(?:TRUE|1|NOT\s+FALSE)\b"
    r"|'\s*OR\s*'|\"\s*OR\s*\"",
    re.IGNORECASE,
)
_INPUT_INJECTION_RE = re.compile(
    r"(?:--|/\*|\*/|;\s*\w)"
    r"|\b(?:DROP\s+TABLE|DELETE\s+FROM|TRUNCATE\s+TABLE|INSERT\s+INTO|UPDATE\s+\w+\s+SET|"
    r"ALTER\s+TABLE|UNION\s+(?:ALL\s+)?SELECT|SELECT\s+.+\s+FROM\s+(?:users|pg_|information_schema)|"
    r"pg_sleep|pg_read_file|lo_import|information_schema)\b",
    re.IGNORECASE,
)
_TABLE_REF_RE = re.compile(r"\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_.]*)", re.IGNORECASE)


class SQLValidationError(FinSightError, ValueError):
    """Raised when a SQL query violates FinSight AI read-only validation rules."""

    def __init__(self, message: str, error_code: str = "SQL_VALIDATION_ERROR") -> None:
        super().__init__(
            message=message,
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code=error_code,
        )


class SQLInjectionError(SQLValidationError):
    """Raised when a SQL query or input contains a suspected SQL Injection payload."""

    def __init__(self, message: str) -> None:
        super().__init__(message=message, error_code="SQL_INJECTION_DETECTED")


def detect_input_sql_injection(raw_input: str) -> None:
    """Inspect natural-language questions or filter inputs for SQL Injection payloads."""
    if not raw_input:
        return
    if _INPUT_INJECTION_RE.search(raw_input) or _TAUTOLOGY_INJECTION_RE.search(raw_input):
        logger.warning("input_sql_injection_blocked", payload=raw_input[:200])
        raise SQLInjectionError(
            "Rejected input containing suspected SQL Injection or unauthorized SQL payload."
        )


def validate_readonly_sql(sql: str) -> str:
    """Validate that `sql` is a safe, single-statement, read-only SELECT on `financial_metrics`."""
    if not isinstance(sql, str) or not sql.strip():
        raise SQLValidationError("SQL query must be a non-empty string.")

    cleaned = sql.strip()
    if len(cleaned) > 4000:
        raise SQLValidationError("SQL query exceeds maximum allowed length (4000 characters).")

    if "--" in cleaned or "/*" in cleaned or "*/" in cleaned or "#" in cleaned:
        raise SQLInjectionError("SQL comments (--, /* */, #) are forbidden.")

    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].strip()
    if ";" in cleaned:
        raise SQLInjectionError("Multiple SQL statements (stacked queries via ';') are forbidden.")

    if not re.match(r"^SELECT\b", cleaned, re.IGNORECASE):
        raise SQLInjectionError("Only read-only SELECT statements are permitted.")

    if len(re.findall(r"\bSELECT\b", cleaned, re.IGNORECASE)) > 1:
        raise SQLInjectionError("Nested SELECT subqueries and UNION queries are forbidden.")

    if kw_match := _FORBIDDEN_SQL_KEYWORDS_RE.search(cleaned):
        raise SQLInjectionError(
            f"Forbidden SQL keyword or operation detected: '{kw_match.group(0)}'."
        )

    if cat_match := _FORBIDDEN_CATALOG_OR_FUNC_RE.search(cleaned):
        raise SQLInjectionError(
            f"Forbidden table, function, or system catalog detected: '{cat_match.group(0)}'."
        )

    if taut_match := _TAUTOLOGY_INJECTION_RE.search(cleaned):
        raise SQLInjectionError(
            f"SQL Injection tautology pattern detected: '{taut_match.group(0)}'."
        )

    referenced_tables = [m.lower() for m in _TABLE_REF_RE.findall(cleaned)]
    if not referenced_tables or "financial_metrics" not in referenced_tables:
        raise SQLInjectionError("Query must select FROM `financial_metrics`.")

    for tbl in referenced_tables:
        if tbl not in ALLOWED_SQL_TABLES:
            raise SQLInjectionError(
                f"Unauthorized table '{tbl}'. Only `financial_metrics` and `documents` are allowed."
            )

    return cleaned


def compile_parameterized_financial_sql(spec: SQLQuerySpec) -> tuple[str, dict[str, Any]]:
    """Compile a validated `SQLQuerySpec` into a parameterized SELECT query and parameter dict."""
    for ticker_item in spec.tickers:
        detect_input_sql_injection(ticker_item)

    conditions: list[str] = ["d.access_level = ANY(:allowed_access_levels)"]
    params: dict[str, Any] = {
        "allowed_access_levels": [lvl.value for lvl in spec.allowed_access_levels],
        "limit": spec.limit,
    }

    if spec.tickers:
        conditions.append("fm.ticker = ANY(:tickers)")
        params["tickers"] = spec.tickers
    if spec.fiscal_years:
        conditions.append("fm.fiscal_year = ANY(:fiscal_years)")
        params["fiscal_years"] = spec.fiscal_years
    if spec.periods:
        conditions.append("fm.period = ANY(:periods)")
        params["periods"] = [p.value for p in spec.periods]
    if spec.metric_codes:
        conditions.append("fm.metric_code = ANY(:metric_codes)")
        params["metric_codes"] = [m.value for m in spec.metric_codes]
    if spec.statement_types:
        conditions.append("fm.statement_type = ANY(:statement_types)")
        params["statement_types"] = [s.value for s in spec.statement_types]
    if spec.document_ids:
        conditions.append("fm.document_id = ANY(:document_ids)")
        params["document_ids"] = spec.document_ids

    where_clause = "\n  AND ".join(conditions)
    sql = (
        "SELECT\n"
        "    fm.id AS metric_id,\n"
        "    fm.document_id,\n"
        "    d.title AS doc_title,\n"
        "    d.company_name,\n"
        "    fm.ticker,\n"
        "    fm.fiscal_year,\n"
        "    fm.period,\n"
        "    fm.statement_type,\n"
        "    fm.metric_code,\n"
        "    fm.metric_name_vi,\n"
        "    fm.metric_name_en,\n"
        "    fm.value,\n"
        "    fm.unit,\n"
        "    fm.source_page,\n"
        "    d.access_level\n"
        "FROM financial_metrics AS fm\n"
        "JOIN documents AS d ON fm.document_id = d.id\n"
        f"WHERE {where_clause}\n"
        "ORDER BY fm.ticker ASC, fm.fiscal_year ASC, fm.period ASC, fm.metric_code ASC\n"
        "LIMIT :limit"
    )
    return validate_readonly_sql(sql), params


class BaseSQLQueryGenerator(ABC):
    """Abstract interface for pluggable Natural-Language-to-SQL generators."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Return identifier of the active Text-to-SQL generator."""

    @abstractmethod
    def generate_spec(self, base_spec: SQLQuerySpec) -> SQLQuerySpec:
        """Synchronously enrich `base_spec` from `base_spec.question`."""

    async def agenerate_spec(self, base_spec: SQLQuerySpec) -> SQLQuerySpec:
        """Asynchronously enrich `base_spec` from `base_spec.question`."""
        return self.generate_spec(base_spec)


class RuleBasedSQLQueryGenerator(BaseSQLQueryGenerator):
    """Deterministic NL-to-SQL parser extracting tickers, year ranges, periods, and metrics."""

    @property
    def model_name(self) -> str:
        return "finsight-rule-sql-generator-v1"

    def generate_spec(self, base_spec: SQLQuerySpec) -> SQLQuerySpec:
        detect_input_sql_injection(base_spec.question)
        q = base_spec.question
        tickers = base_spec.tickers or extract_tickers_from_text(q)
        years = base_spec.fiscal_years or extract_fiscal_years_from_text(q)
        periods = base_spec.periods or extract_periods_from_text(q)
        metrics = base_spec.metric_codes or extract_metric_codes_from_text(q)
        stmts = base_spec.statement_types or extract_statement_types_from_text(q) if not metrics else base_spec.statement_types
        return base_spec.model_copy(
            update={
                "tickers": tickers,
                "fiscal_years": years,
                "periods": periods,
                "metric_codes": metrics,
                "statement_types": stmts,
            }
        )


class GeminiSQLQueryGenerator(BaseSQLQueryGenerator):
    """Text-to-SQL generator powered by Google GenAI SDK (`google-genai`) with rule reconciliation."""

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
        client: genai.Client | None = None,
        fallback_generator: BaseSQLQueryGenerator | None = None,
    ) -> None:
        settings = get_settings()
        self._api_key = (api_key if api_key is not None else settings.gemini_api_key).strip()
        self._model_name = normalize_gemini_model_name(model_name or settings.gemini_fast_model)
        self._client = client
        self._fallback = fallback_generator or RuleBasedSQLQueryGenerator()

    @property
    def model_name(self) -> str:
        return self._model_name if self.is_available else self._fallback.model_name

    @property
    def is_available(self) -> bool:
        return self._client is not None or self._api_key not in _PLACEHOLDER_API_KEYS

    def _get_client(self) -> genai.Client:
        if self._client is None:
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def _build_kwargs(self, spec: SQLQuerySpec) -> dict[str, Any]:
        return {
            "model": self._model_name,
            "system_instruction": SQL_TOOL_SYSTEM_PROMPT,
            "input": build_sql_tool_prompt(
                spec.question,
                allowed_access_levels=spec.allowed_access_levels,
                tickers=spec.tickers,
                fiscal_years=spec.fiscal_years,
            ),
            "response_format": {
                "type": "text",
                "mime_type": "application/json",
                "schema": LLMGeneratedSQLOutput.model_json_schema(),
            },
            "store": False,
        }

    def _reconcile(self, output_text: str | None, rule_spec: SQLQuerySpec) -> SQLQuerySpec:
        parsed = LLMGeneratedSQLOutput.model_validate_json(output_text or '{"sql_query": ""}')
        if parsed.sql_query.strip():
            validate_readonly_sql(parsed.sql_query)
        return rule_spec.model_copy(
            update={
                "tickers": rule_spec.tickers or [t.strip().upper() for t in parsed.tickers if t.strip()],
                "fiscal_years": sorted(set(rule_spec.fiscal_years) | set(parsed.fiscal_years)),
                "periods": rule_spec.periods or parsed.periods,
                "metric_codes": rule_spec.metric_codes or parsed.metric_codes,
                "statement_types": rule_spec.statement_types or parsed.statement_types,
            }
        )

    def generate_spec(self, base_spec: SQLQuerySpec) -> SQLQuerySpec:
        rule_spec = self._fallback.generate_spec(base_spec)
        if not self.is_available:
            return rule_spec
        try:
            res = self._get_client().interactions.create(**self._build_kwargs(rule_spec))
            return self._reconcile(res.output_text, rule_spec)
        except SQLValidationError:
            raise
        except (ValueError, RuntimeError, OSError, httpx.HTTPError) as exc:
            logger.warning("gemini_sql_sync_fallback", model=self._model_name, error=str(exc))
            return rule_spec

    async def agenerate_spec(self, base_spec: SQLQuerySpec) -> SQLQuerySpec:
        rule_spec = await self._fallback.agenerate_spec(base_spec)
        if not self.is_available:
            return rule_spec
        try:
            res = await self._get_client().aio.interactions.create(**self._build_kwargs(rule_spec))
            return self._reconcile(res.output_text, rule_spec)
        except SQLValidationError:
            raise
        except (ValueError, RuntimeError, OSError, httpx.HTTPError) as exc:
            logger.warning("gemini_sql_async_fallback", model=self._model_name, error=str(exc))
            return rule_spec


def _row_to_metric_record(row_map: Mapping[str, Any]) -> FinancialMetricRecord:
    """Convert a SQL result mapping into a validated `FinancialMetricRecord`."""
    code = MetricCode(str(row_map["metric_code"]).strip().upper())
    def_vi, def_en, def_stmt, def_unit = CANONICAL_METRIC_DEFINITIONS[code]
    raw_period = str(row_map.get("period") or FiscalPeriod.FY.value).strip().upper()
    raw_stmt = str(row_map.get("statement_type") or def_stmt.value).strip().lower()
    raw_unit = str(row_map.get("unit") or def_unit.value).strip().upper()
    raw_access = str(row_map.get("access_level") or AccessLevel.PUBLIC.value).strip().lower()

    return FinancialMetricRecord(
        metric_id=row_map.get("metric_id") or row_map.get("id") or uuid.uuid4(),
        document_id=row_map.get("document_id") or uuid.UUID(int=0),
        doc_title=str(row_map.get("doc_title") or ""),
        company_name=str(row_map.get("company_name") or ""),
        ticker=str(row_map["ticker"]).strip().upper(),
        fiscal_year=int(row_map["fiscal_year"]),
        period=FiscalPeriod(raw_period) if raw_period in FiscalPeriod._value2member_map_ else FiscalPeriod.FY,
        statement_type=StatementType(raw_stmt) if raw_stmt in StatementType._value2member_map_ else def_stmt,
        metric_code=code,
        metric_name_vi=str(row_map.get("metric_name_vi") or def_vi),
        metric_name_en=str(row_map.get("metric_name_en") or def_en),
        value=Decimal(str(row_map["value"])),
        unit=MetricUnit(raw_unit) if raw_unit in MetricUnit._value2member_map_ else def_unit,
        source_page=max(1, int(row_map.get("source_page") or 1)),
        access_level=AccessLevel(raw_access) if raw_access in AccessLevel._value2member_map_ else AccessLevel.PUBLIC,
    )


class StructuredSQLQueryTool:
    """Safe Read-Only Text-to-SQL Tool over `financial_metrics` with RBAC enforcement."""

    def __init__(
        self,
        generator: BaseSQLQueryGenerator | None = None,
        *,
        gemini_client: genai.Client | None = None,
    ) -> None:
        self.generator = generator or GeminiSQLQueryGenerator(client=gemini_client)

    def generate_sql(
        self,
        question: str,
        *,
        user_role: UserRole | str = UserRole.VIEWER,
        ticker: str | Sequence[str] | None = None,
        fiscal_year: int | Sequence[int] | None = None,
        period: FiscalPeriod | str | Sequence[FiscalPeriod | str] | None = None,
        metric_codes: MetricCode | str | Sequence[MetricCode | str] | None = None,
        statement_types: StatementType | str | Sequence[StatementType | str] | None = None,
        access_levels: Sequence[AccessLevel | str] | None = None,
        document_ids: Sequence[uuid.UUID | str] | None = None,
        limit: int = 50,
    ) -> tuple[str, dict[str, Any], SQLQuerySpec]:
        """Synchronously translate a quantitative question into a validated SELECT SQL query."""
        detect_input_sql_injection(question)
        base_spec = SQLQuerySpec.from_inputs(
            question,
            user_role=user_role,
            tickers=ticker,
            fiscal_years=fiscal_year,
            periods=period,
            metric_codes=metric_codes,
            statement_types=statement_types,
            access_levels=access_levels,
            document_ids=document_ids,
            limit=limit,
        )
        enriched = self.generator.generate_spec(base_spec)
        sql, params = compile_parameterized_financial_sql(enriched)
        return sql, params, enriched

    async def agenerate_sql(
        self,
        question: str,
        *,
        user_role: UserRole | str = UserRole.VIEWER,
        ticker: str | Sequence[str] | None = None,
        fiscal_year: int | Sequence[int] | None = None,
        period: FiscalPeriod | str | Sequence[FiscalPeriod | str] | None = None,
        metric_codes: MetricCode | str | Sequence[MetricCode | str] | None = None,
        statement_types: StatementType | str | Sequence[StatementType | str] | None = None,
        access_levels: Sequence[AccessLevel | str] | None = None,
        document_ids: Sequence[uuid.UUID | str] | None = None,
        limit: int = 50,
    ) -> tuple[str, dict[str, Any], SQLQuerySpec]:
        """Asynchronously translate a quantitative question into a validated SELECT SQL query."""
        detect_input_sql_injection(question)
        base_spec = SQLQuerySpec.from_inputs(
            question,
            user_role=user_role,
            tickers=ticker,
            fiscal_years=fiscal_year,
            periods=period,
            metric_codes=metric_codes,
            statement_types=statement_types,
            access_levels=access_levels,
            document_ids=document_ids,
            limit=limit,
        )
        enriched = await self.generator.agenerate_spec(base_spec)
        sql, params = compile_parameterized_financial_sql(enriched)
        return sql, params, enriched

    async def execute_readonly_sql(
        self,
        session: AsyncSession,
        sql: str,
        *,
        user_role: UserRole | str = UserRole.VIEWER,
        params: Mapping[str, Any] | None = None,
        access_levels: Sequence[AccessLevel | str] | None = None,
    ) -> list[FinancialMetricRecord]:
        """Validate `sql` against SQL Injection and execute inside an RBAC-enforced CTE."""
        validated_sql = validate_readonly_sql(sql)
        norm_role = parse_user_role(user_role)
        allowed_levels = resolve_allowed_access_levels(norm_role, access_levels)
        if not allowed_levels:
            return []

        allowed_strs = [lvl.value for lvl in allowed_levels]
        bound_params: dict[str, Any] = dict(params or {})
        bound_params["allowed_access_levels"] = allowed_strs
        bound_params["_rbac_allowed_levels"] = allowed_strs

        rbac_cte_sql = (
            "WITH financial_metrics AS (\n"
            "    SELECT fm.id, fm.document_id, fm.ticker, fm.fiscal_year, fm.period,\n"
            "           fm.statement_type, fm.metric_code, fm.metric_name_vi, fm.metric_name_en,\n"
            "           fm.value, fm.unit, fm.source_page, fm.created_at,\n"
            "           d.title AS doc_title, d.company_name AS company_name,\n"
            "           d.access_level AS access_level\n"
            "    FROM public.financial_metrics AS fm\n"
            "    JOIN public.documents AS d ON fm.document_id = d.id\n"
            "    WHERE d.access_level = ANY(:_rbac_allowed_levels)\n"
            "),\n"
            "documents AS (\n"
            "    SELECT * FROM public.documents AS d\n"
            "    WHERE d.access_level = ANY(:_rbac_allowed_levels)\n"
            ")\n"
            f"{validated_sql}"
        )
        rows = (await session.execute(text(rbac_cte_sql), bound_params)).mappings().all()
        return [_row_to_metric_record(r) for r in rows]

    async def query(
        self,
        session: AsyncSession,
        question: str,
        *,
        user_role: UserRole | str = UserRole.VIEWER,
        ticker: str | Sequence[str] | None = None,
        fiscal_year: int | Sequence[int] | None = None,
        period: FiscalPeriod | str | Sequence[FiscalPeriod | str] | None = None,
        metric_codes: MetricCode | str | Sequence[MetricCode | str] | None = None,
        statement_types: StatementType | str | Sequence[StatementType | str] | None = None,
        access_levels: Sequence[AccessLevel | str] | None = None,
        document_ids: Sequence[uuid.UUID | str] | None = None,
        limit: int = 50,
    ) -> SQLToolResult:
        """End-to-end Text-to-SQL execution returning structured records, units, and source pages."""
        sql_query, params, spec = await self.agenerate_sql(
            question,
            user_role=user_role,
            ticker=ticker,
            fiscal_year=fiscal_year,
            period=period,
            metric_codes=metric_codes,
            statement_types=statement_types,
            access_levels=access_levels,
            document_ids=document_ids,
            limit=limit,
        )
        records = await self.execute_readonly_sql(
            session,
            sql_query,
            user_role=spec.user_role,
            params=params,
            access_levels=spec.allowed_access_levels,
        )
        citations: list[dict[str, object]] = []
        seen_citations: set[tuple[str, int]] = set()
        for rec in records:
            c_key = (str(rec.document_id), rec.source_page)
            if c_key not in seen_citations:
                seen_citations.add(c_key)
                citations.append(rec.to_citation_dict())

        serializable_params = {
            k: ([str(x) for x in v] if k == "document_ids" and isinstance(v, list) else v)
            for k, v in params.items()
        }
        logger.info(
            "sql_tool_query_completed",
            user_role=spec.user_role.value,
            tickers=spec.tickers,
            fiscal_years=spec.fiscal_years,
            metric_codes=[m.value for m in spec.metric_codes],
            row_count=len(records),
        )
        return SQLToolResult(
            question=spec.question,
            sql_query=sql_query,
            parameters=serializable_params,
            generator_model=self.generator.model_name,
            user_role=spec.user_role,
            allowed_access_levels=spec.allowed_access_levels,
            row_count=len(records),
            records=records,
            citations=citations,
        )
