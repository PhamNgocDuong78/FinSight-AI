"""Hybrid Retrieval (Dense + Sparse BM25 + RRF), Reranking, and RBAC filtering services."""

from app.services.retrieval.hybrid_retriever import (
    DEFAULT_RRF_K,
    BaseHybridRetriever,
    QdrantHybridRetriever,
    build_qdrant_filter,
    expand_query_with_metric_synonyms,
    reciprocal_rank_fusion,
)
from app.services.retrieval.reranker import (
    BaseReranker,
    CrossEncoderReranker,
    FinancialCrossEncoderScorer,
    GeminiFlashLiteReranker,
    HuggingFaceCrossEncoderReranker,
)

__all__ = [
    "DEFAULT_RRF_K",
    "BaseHybridRetriever",
    "BaseReranker",
    "CrossEncoderReranker",
    "FinancialCrossEncoderScorer",
    "GeminiFlashLiteReranker",
    "HuggingFaceCrossEncoderReranker",
    "QdrantHybridRetriever",
    "build_qdrant_filter",
    "expand_query_with_metric_synonyms",
    "reciprocal_rank_fusion",
]
