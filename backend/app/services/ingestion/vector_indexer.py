"""Dual Indexing service (Dense Gemini Embedding + Sparse BM25) for Qdrant `finsight_child_chunks`."""

import uuid
from abc import ABC, abstractmethod

from qdrant_client import AsyncQdrantClient, models

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.qdrant import get_qdrant_client
from app.llm.embeddings import (
    BaseEmbeddingProvider,
    BaseSparseEmbedder,
    BM25SparseEmbedder,
    GeminiEmbeddingProvider,
)
from app.models.enums import AccessLevel, FiscalPeriod
from app.schemas.ingestion import ChildChunkData

logger = get_logger(__name__)

DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"

_PAYLOAD_INDEX_FIELDS: tuple[tuple[str, models.PayloadSchemaType], ...] = (
    ("document_id", models.PayloadSchemaType.KEYWORD),
    ("parent_chunk_id", models.PayloadSchemaType.KEYWORD),
    ("ticker", models.PayloadSchemaType.KEYWORD),
    ("fiscal_year", models.PayloadSchemaType.INTEGER),
    ("period", models.PayloadSchemaType.KEYWORD),
    ("access_level", models.PayloadSchemaType.KEYWORD),
    ("page_number", models.PayloadSchemaType.INTEGER),
    ("chunk_type", models.PayloadSchemaType.KEYWORD),
)


class BaseVectorIndexer(ABC):
    """Abstract interface for indexing Child Chunks into a vector store."""

    @abstractmethod
    async def ensure_collection(self, recreate: bool = False) -> bool:
        """Ensure the target vector collection and payload indexes exist."""

    @abstractmethod
    async def index_child_chunks(
        self,
        child_chunks: list[ChildChunkData],
        *,
        doc_title: str,
        ticker: str,
        fiscal_year: int,
        period: FiscalPeriod | str = FiscalPeriod.FY,
        access_level: AccessLevel | str = AccessLevel.PUBLIC,
        replace_existing: bool = True,
    ) -> int:
        """Embed and index Child Chunks with full metadata payload."""

    @abstractmethod
    async def delete_document_chunks(self, document_id: uuid.UUID) -> None:
        """Delete all indexed Child Chunks belonging to a specific document."""


class QdrantChildChunkIndexer(BaseVectorIndexer):
    """Qdrant Dual Indexer storing Dense (Semantic) + Sparse (BM25) vectors for Child Chunks."""

    def __init__(
        self,
        qdrant_client: AsyncQdrantClient | None = None,
        dense_embedder: BaseEmbeddingProvider | None = None,
        sparse_embedder: BaseSparseEmbedder | None = None,
        collection_name: str | None = None,
        embedding_dimension: int | None = None,
        upsert_batch_size: int = 64,
    ) -> None:
        settings = get_settings()
        self._client = qdrant_client
        self.collection_name = collection_name or settings.qdrant_collection_name
        self.embedding_dimension = embedding_dimension or settings.embedding_dimension
        self.dense_embedder = dense_embedder or GeminiEmbeddingProvider(
            dimension=self.embedding_dimension
        )
        self.sparse_embedder = sparse_embedder or BM25SparseEmbedder()
        self.upsert_batch_size = max(1, upsert_batch_size)

    def _get_client(self) -> AsyncQdrantClient:
        return self._client or get_qdrant_client()

    async def ensure_collection(self, recreate: bool = False) -> bool:
        """Create `finsight_child_chunks` with Dense + Sparse BM25 configs and payload indexes."""
        client = self._get_client()
        exists = await client.collection_exists(self.collection_name)

        if exists and recreate:
            await client.delete_collection(self.collection_name)
            exists = False

        created = False
        if not exists:
            await client.create_collection(
                collection_name=self.collection_name,
                vectors_config={
                    DENSE_VECTOR_NAME: models.VectorParams(
                        size=self.embedding_dimension,
                        distance=models.Distance.COSINE,
                    )
                },
                sparse_vectors_config={
                    SPARSE_VECTOR_NAME: models.SparseVectorParams(
                        modifier=models.Modifier.IDF,
                    )
                },
            )
            created = True
            logger.info(
                "qdrant_collection_created",
                collection_name=self.collection_name,
                dense_dim=self.embedding_dimension,
                sparse_modifier="idf",
            )

        for field_name, schema_type in _PAYLOAD_INDEX_FIELDS:
            await client.create_payload_index(
                collection_name=self.collection_name,
                field_name=field_name,
                field_schema=schema_type,
                wait=True,
            )

        return created

    async def delete_document_chunks(self, document_id: uuid.UUID) -> None:
        """Remove all Qdrant points matching `document_id` for idempotent re-indexing."""
        client = self._get_client()
        if not await client.collection_exists(self.collection_name):
            return
        await client.delete(
            collection_name=self.collection_name,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="document_id",
                            match=models.MatchValue(value=str(document_id)),
                        )
                    ]
                )
            ),
            wait=True,
        )

    async def count_document_chunks(self, document_id: uuid.UUID) -> int:
        """Count indexed Child Chunk points in Qdrant for a given `document_id`."""
        client = self._get_client()
        if not await client.collection_exists(self.collection_name):
            return 0
        result = await client.count(
            collection_name=self.collection_name,
            count_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="document_id",
                        match=models.MatchValue(value=str(document_id)),
                    )
                ]
            ),
            exact=True,
        )
        return int(result.count)

    async def index_child_chunks(
        self,
        child_chunks: list[ChildChunkData],
        *,
        doc_title: str,
        ticker: str,
        fiscal_year: int,
        period: FiscalPeriod | str = FiscalPeriod.FY,
        access_level: AccessLevel | str = AccessLevel.PUBLIC,
        replace_existing: bool = True,
    ) -> int:
        """Generate Dense + Sparse BM25 vectors and upsert Child Chunks into Qdrant."""
        await self.ensure_collection(recreate=False)
        if not child_chunks:
            return 0

        norm_ticker = ticker.strip().upper()
        period_str = period.value if isinstance(period, FiscalPeriod) else str(period)
        access_str = (
            access_level.value if isinstance(access_level, AccessLevel) else str(access_level)
        )

        if replace_existing:
            doc_ids = {chunk.document_id for chunk in child_chunks}
            for doc_id in doc_ids:
                await self.delete_document_chunks(doc_id)

        texts = [chunk.text for chunk in child_chunks]
        dense_vectors = await self.dense_embedder.aembed_texts(texts)
        sparse_vectors = self.sparse_embedder.embed_sparse_batch(texts)

        points: list[models.PointStruct] = []
        for chunk, dense_vec, sparse_vec in zip(
            child_chunks, dense_vectors, sparse_vectors, strict=True
        ):
            payload = chunk.to_qdrant_payload(
                doc_title=doc_title,
                ticker=norm_ticker,
                fiscal_year=fiscal_year,
                period=period_str,
                access_level=access_str,
            )
            points.append(
                models.PointStruct(
                    id=str(chunk.id),
                    vector={
                        DENSE_VECTOR_NAME: dense_vec,
                        SPARSE_VECTOR_NAME: models.SparseVector(
                            indices=sparse_vec.indices,
                            values=sparse_vec.values,
                        ),
                    },
                    payload=payload,
                )
            )

        client = self._get_client()
        for start in range(0, len(points), self.upsert_batch_size):
            batch = points[start : start + self.upsert_batch_size]
            await client.upsert(
                collection_name=self.collection_name,
                points=batch,
                wait=True,
            )

        logger.info(
            "qdrant_child_chunks_indexed",
            collection_name=self.collection_name,
            ticker=norm_ticker,
            fiscal_year=fiscal_year,
            indexed_count=len(points),
        )
        return len(points)


__all__ = [
    "DENSE_VECTOR_NAME",
    "SPARSE_VECTOR_NAME",
    "BaseVectorIndexer",
    "QdrantChildChunkIndexer",
]
