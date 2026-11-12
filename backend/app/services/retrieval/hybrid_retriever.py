"""Hybrid Search Retriever (Dense Vector + Sparse BM25 + RRF + RBAC Filter + Parent Mapping)."""

import asyncio
import re
import uuid
from abc import ABC, abstractmethod
from collections.abc import Sequence

from qdrant_client import AsyncQdrantClient, models
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.prompts.financial_etl import CANONICAL_METRIC_DEFINITIONS
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.qdrant import get_qdrant_client
from app.llm.embeddings import (
    BaseEmbeddingProvider,
    BaseSparseEmbedder,
    BM25SparseEmbedder,
    GeminiEmbeddingProvider,
)
from app.models.document import Document, ParentChunk
from app.models.enums import AccessLevel, ChunkType, FiscalPeriod, MetricCode, UserRole
from app.schemas.retrieval import ChildChunkMatch, RetrievalFilter, RetrievedChunk
from app.services.ingestion.vector_indexer import DENSE_VECTOR_NAME, SPARSE_VECTOR_NAME

logger = get_logger(__name__)

DEFAULT_RRF_K = 60

_METRIC_TOKEN_PATTERNS: tuple[tuple[MetricCode, re.Pattern[str]], ...] = tuple(
    (code, re.compile(rf"\b{re.escape(code.value)}\b", re.IGNORECASE))
    for code in MetricCode
)


def expand_query_with_metric_synonyms(query: str) -> str:
    """Enrich queries containing standardized MetricCode identifiers with VI/EN table labels."""
    cleaned = query.strip()
    if not cleaned:
        return ""
    expansions: list[str] = []
    lower_q = cleaned.lower()
    for code, pattern in _METRIC_TOKEN_PATTERNS:
        if pattern.search(cleaned):
            name_vi, name_en, _, _ = CANONICAL_METRIC_DEFINITIONS[code]
            if name_vi.lower() not in lower_q:
                expansions.append(name_vi)
            if name_en.lower() not in lower_q:
                expansions.append(name_en)
    return f"{cleaned} ({'; '.join(expansions)})" if expansions else cleaned


def build_qdrant_filter(retrieval_filter: RetrievalFilter) -> models.Filter:
    """Build Qdrant payload filter enforcing RBAC `access_level`, `ticker`, and `fiscal_year`."""
    allowed_values = [lvl.value for lvl in retrieval_filter.allowed_access_levels]
    must_conditions: list[models.Condition] = [
        models.FieldCondition(
            key="access_level",
            match=models.MatchAny(any=allowed_values),
        )
    ]

    if len(retrieval_filter.tickers) == 1:
        must_conditions.append(
            models.FieldCondition(
                key="ticker",
                match=models.MatchValue(value=retrieval_filter.tickers[0]),
            )
        )
    elif len(retrieval_filter.tickers) > 1:
        must_conditions.append(
            models.FieldCondition(
                key="ticker",
                match=models.MatchAny(any=retrieval_filter.tickers),
            )
        )

    if len(retrieval_filter.fiscal_years) == 1:
        must_conditions.append(
            models.FieldCondition(
                key="fiscal_year",
                match=models.MatchValue(value=retrieval_filter.fiscal_years[0]),
            )
        )
    elif len(retrieval_filter.fiscal_years) > 1:
        must_conditions.append(
            models.FieldCondition(
                key="fiscal_year",
                match=models.MatchAny(any=retrieval_filter.fiscal_years),
            )
        )

    if retrieval_filter.period is not None:
        must_conditions.append(
            models.FieldCondition(
                key="period",
                match=models.MatchValue(value=retrieval_filter.period.value),
            )
        )

    if retrieval_filter.chunk_type is not None:
        must_conditions.append(
            models.FieldCondition(
                key="chunk_type",
                match=models.MatchValue(value=retrieval_filter.chunk_type.value),
            )
        )

    if retrieval_filter.document_ids:
        must_conditions.append(
            models.FieldCondition(
                key="document_id",
                match=models.MatchAny(any=[str(uid) for uid in retrieval_filter.document_ids]),
            )
        )

    return models.Filter(must=must_conditions)


def reciprocal_rank_fusion(
    dense_points: Sequence[models.ScoredPoint],
    sparse_points: Sequence[models.ScoredPoint],
    *,
    rrf_k: int = DEFAULT_RRF_K,
    dense_weight: float = 1.0,
    sparse_weight: float = 1.0,
    top_k: int = 20,
) -> list[ChildChunkMatch]:
    """Fuse Dense Semantic and Sparse BM25 ranked lists via Reciprocal Rank Fusion (RRF).

    Formula: RRF_Score(d) = w_dense / (k + rank_dense(d)) + w_sparse / (k + rank_sparse(d))
    """
    k_const = max(1, rrf_k)
    merged: dict[str, dict[str, object]] = {}

    for rank, pt in enumerate(dense_points, start=1):
        pt_id = str(pt.id)
        entry = merged.setdefault(
            pt_id,
            {
                "point": pt,
                "rrf_score": 0.0,
                "dense_score": None,
                "sparse_score": None,
                "dense_rank": None,
                "sparse_rank": None,
            },
        )
        entry["rrf_score"] = float(entry["rrf_score"]) + (dense_weight / (k_const + rank))
        entry["dense_score"] = float(pt.score)
        entry["dense_rank"] = rank

    for rank, pt in enumerate(sparse_points, start=1):
        pt_id = str(pt.id)
        entry = merged.setdefault(
            pt_id,
            {
                "point": pt,
                "rrf_score": 0.0,
                "dense_score": None,
                "sparse_score": None,
                "dense_rank": None,
                "sparse_rank": None,
            },
        )
        entry["rrf_score"] = float(entry["rrf_score"]) + (sparse_weight / (k_const + rank))
        entry["sparse_score"] = float(pt.score)
        entry["sparse_rank"] = rank

    matches: list[ChildChunkMatch] = []
    for pt_id, data in merged.items():
        pt_obj: models.ScoredPoint = data["point"]  # type: ignore[assignment]
        payload = pt_obj.payload or {}
        try:
            child_uuid = uuid.UUID(pt_id)
            parent_uuid = uuid.UUID(str(payload["parent_chunk_id"]))
            doc_uuid = uuid.UUID(str(payload["document_id"]))
        except (KeyError, ValueError):
            continue

        raw_access = str(payload.get("access_level", AccessLevel.PUBLIC.value))
        raw_chunk_type = str(payload.get("chunk_type", ChunkType.TEXT.value))
        matches.append(
            ChildChunkMatch(
                child_chunk_id=child_uuid,
                parent_chunk_id=parent_uuid,
                document_id=doc_uuid,
                doc_title=str(payload.get("doc_title", "")),
                ticker=str(payload.get("ticker", "")),
                fiscal_year=int(payload.get("fiscal_year", 0)),
                period=str(payload.get("period", FiscalPeriod.FY.value)),
                access_level=AccessLevel(raw_access)
                if raw_access in AccessLevel._value2member_map_
                else AccessLevel.PUBLIC,
                page_number=max(1, int(payload.get("page_number", 1))),
                chunk_type=ChunkType(raw_chunk_type)
                if raw_chunk_type in ChunkType._value2member_map_
                else ChunkType.TEXT,
                section_title=str(payload.get("section_title", "")),
                text=str(payload.get("text", "")),
                rrf_score=round(float(data["rrf_score"]), 8),
                dense_score=data["dense_score"],  # type: ignore[arg-type]
                sparse_score=data["sparse_score"],  # type: ignore[arg-type]
                dense_rank=data["dense_rank"],  # type: ignore[arg-type]
                sparse_rank=data["sparse_rank"],  # type: ignore[arg-type]
            )
        )

    matches.sort(
        key=lambda m: (
            m.rrf_score,
            (m.sparse_score or 0.0) + (m.dense_score or 0.0),
        ),
        reverse=True,
    )
    return matches[: max(1, top_k)]


class BaseHybridRetriever(ABC):
    """Abstract interface for RBAC-aware Hybrid Search and Parent Chunk retrieval."""

    @abstractmethod
    async def search_child_chunks(
        self,
        query: str,
        *,
        retrieval_filter: RetrievalFilter,
        top_k: int | None = None,
        prefetch_k: int | None = None,
    ) -> list[ChildChunkMatch]:
        """Run Dense + Sparse BM25 search on Qdrant with RBAC payload filtering and RRF fusion."""

    @abstractmethod
    async def retrieve(
        self,
        session: AsyncSession,
        query: str,
        *,
        user_role: UserRole | str = UserRole.VIEWER,
        ticker: str | Sequence[str] | None = None,
        fiscal_year: int | Sequence[int] | None = None,
        period: FiscalPeriod | str | None = None,
        chunk_type: ChunkType | str | None = None,
        access_levels: Sequence[AccessLevel | str] | None = None,
        document_ids: Sequence[uuid.UUID | str] | None = None,
        top_k: int | None = None,
        prefetch_k: int | None = None,
    ) -> list[RetrievedChunk]:
        """Retrieve full Parent Chunks from PostgreSQL mapped from top RRF Child Chunks."""


class QdrantHybridRetriever(BaseHybridRetriever):
    """Hybrid Retriever combining Qdrant Dense + Sparse BM25 via RRF and PostgreSQL Parent mapping."""

    def __init__(
        self,
        qdrant_client: AsyncQdrantClient | None = None,
        dense_embedder: BaseEmbeddingProvider | None = None,
        sparse_embedder: BaseSparseEmbedder | None = None,
        collection_name: str | None = None,
        default_top_k: int | None = None,
        rrf_k: int = DEFAULT_RRF_K,
        dense_weight: float = 1.0,
        sparse_weight: float = 1.0,
    ) -> None:
        settings = get_settings()
        self._client = qdrant_client
        self.collection_name = collection_name or settings.qdrant_collection_name
        self.default_top_k = default_top_k or settings.hybrid_search_top_k
        self.rrf_k = max(1, rrf_k)
        self.dense_weight = dense_weight
        self.sparse_weight = sparse_weight
        self.dense_embedder = dense_embedder or GeminiEmbeddingProvider(
            dimension=settings.embedding_dimension
        )
        self.sparse_embedder = sparse_embedder or BM25SparseEmbedder()

    def _get_client(self) -> AsyncQdrantClient:
        return self._client or get_qdrant_client()

    async def search_child_chunks(
        self,
        query: str,
        *,
        retrieval_filter: RetrievalFilter,
        top_k: int | None = None,
        prefetch_k: int | None = None,
    ) -> list[ChildChunkMatch]:
        """Execute parallel Dense and Sparse BM25 queries on Qdrant and fuse ranks with RRF."""
        if not query or not query.strip() or not retrieval_filter.allowed_access_levels:
            return []

        client = self._get_client()
        if not await client.collection_exists(self.collection_name):
            return []

        resolved_top_k = max(1, top_k or self.default_top_k)
        branch_limit = max(resolved_top_k * 2, prefetch_k or resolved_top_k * 2)
        qdrant_filter = build_qdrant_filter(retrieval_filter)
        expanded_query = expand_query_with_metric_synonyms(query)

        dense_vec = await self.dense_embedder.aembed_query(expanded_query)
        sparse_vec = self.sparse_embedder.embed_sparse(expanded_query)

        dense_coro = client.query_points(
            collection_name=self.collection_name,
            query=dense_vec,
            using=DENSE_VECTOR_NAME,
            query_filter=qdrant_filter,
            limit=branch_limit,
            with_payload=True,
        )

        if sparse_vec.indices and self.sparse_weight > 0.0:
            sparse_coro = client.query_points(
                collection_name=self.collection_name,
                query=models.SparseVector(
                    indices=sparse_vec.indices,
                    values=sparse_vec.values,
                ),
                using=SPARSE_VECTOR_NAME,
                query_filter=qdrant_filter,
                limit=branch_limit,
                with_payload=True,
            )
            dense_resp, sparse_resp = await asyncio.gather(dense_coro, sparse_coro)
            sparse_points = sparse_resp.points
        else:
            dense_resp = await dense_coro
            sparse_points = []

        return reciprocal_rank_fusion(
            dense_resp.points if self.dense_weight > 0.0 else [],
            sparse_points,
            rrf_k=self.rrf_k,
            dense_weight=self.dense_weight,
            sparse_weight=self.sparse_weight,
            top_k=resolved_top_k,
        )

    async def retrieve(
        self,
        session: AsyncSession,
        query: str,
        *,
        user_role: UserRole | str = UserRole.VIEWER,
        ticker: str | Sequence[str] | None = None,
        fiscal_year: int | Sequence[int] | None = None,
        period: FiscalPeriod | str | None = None,
        chunk_type: ChunkType | str | None = None,
        access_levels: Sequence[AccessLevel | str] | None = None,
        document_ids: Sequence[uuid.UUID | str] | None = None,
        top_k: int | None = None,
        prefetch_k: int | None = None,
    ) -> list[RetrievedChunk]:
        """Run RBAC-filtered Hybrid Search on Qdrant and fetch full Parent Chunks from PostgreSQL."""
        retrieval_filter = RetrievalFilter.from_inputs(
            user_role=user_role,
            ticker=ticker,
            fiscal_year=fiscal_year,
            period=period,
            chunk_type=chunk_type,
            access_levels=access_levels,
            document_ids=document_ids,
        )
        if not retrieval_filter.allowed_access_levels:
            logger.warning(
                "hybrid_retrieval_rbac_denied_all",
                user_role=retrieval_filter.user_role.value,
            )
            return []

        resolved_top_k = max(1, top_k or self.default_top_k)
        child_limit = max(resolved_top_k * 2, prefetch_k or resolved_top_k * 2)
        child_matches = await self.search_child_chunks(
            query,
            retrieval_filter=retrieval_filter,
            top_k=child_limit,
            prefetch_k=child_limit,
        )
        if not child_matches:
            return []

        best_child_by_parent: dict[uuid.UUID, ChildChunkMatch] = {}
        child_ids_by_parent: dict[uuid.UUID, list[uuid.UUID]] = {}
        ordered_parent_ids: list[uuid.UUID] = []

        for match in child_matches:
            p_id = match.parent_chunk_id
            if p_id not in best_child_by_parent:
                best_child_by_parent[p_id] = match
                ordered_parent_ids.append(p_id)
            child_ids_by_parent.setdefault(p_id, []).append(match.child_chunk_id)

        allowed_level_strs = [lvl.value for lvl in retrieval_filter.allowed_access_levels]
        stmt = (
            select(ParentChunk, Document)
            .join(Document, ParentChunk.document_id == Document.id)
            .where(
                ParentChunk.id.in_(ordered_parent_ids),
                Document.access_level.in_(allowed_level_strs),
            )
        )
        if retrieval_filter.tickers:
            stmt = stmt.where(Document.ticker.in_(retrieval_filter.tickers))
        if retrieval_filter.fiscal_years:
            stmt = stmt.where(Document.fiscal_year.in_(retrieval_filter.fiscal_years))

        rows = (await session.execute(stmt)).all()
        db_map: dict[uuid.UUID, tuple[ParentChunk, Document]] = {
            parent_row.id: (parent_row, doc_row) for parent_row, doc_row in rows
        }

        results: list[RetrievedChunk] = []
        for p_id in ordered_parent_ids:
            if p_id not in db_map:
                continue
            parent_row, doc_row = db_map[p_id]
            best_child = best_child_by_parent[p_id]
            results.append(
                RetrievedChunk(
                    parent_chunk_id=parent_row.id,
                    child_chunk_id=best_child.child_chunk_id,
                    matched_child_ids=child_ids_by_parent[p_id],
                    document_id=doc_row.id,
                    doc_title=doc_row.title,
                    company_name=doc_row.company_name,
                    ticker=doc_row.ticker,
                    fiscal_year=doc_row.fiscal_year,
                    period=FiscalPeriod(doc_row.period)
                    if doc_row.period in FiscalPeriod._value2member_map_
                    else FiscalPeriod.FY,
                    access_level=AccessLevel(doc_row.access_level),
                    page_number=parent_row.page_number,
                    chunk_type=ChunkType(parent_row.chunk_type)
                    if parent_row.chunk_type in ChunkType._value2member_map_
                    else ChunkType.TEXT,
                    section_title=parent_row.section_title,
                    content_markdown=parent_row.content_markdown,
                    child_snippet=best_child.text,
                    rrf_score=best_child.rrf_score,
                    dense_score=best_child.dense_score,
                    sparse_score=best_child.sparse_score,
                    dense_rank=best_child.dense_rank,
                    sparse_rank=best_child.sparse_rank,
                )
            )
            if len(results) >= resolved_top_k:
                break

        logger.info(
            "hybrid_retrieval_completed",
            user_role=retrieval_filter.user_role.value,
            allowed_access_levels=allowed_level_strs,
            tickers=retrieval_filter.tickers,
            fiscal_years=retrieval_filter.fiscal_years,
            child_candidates=len(child_matches),
            parent_chunks_returned=len(results),
        )
        return results

