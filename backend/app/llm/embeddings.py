"""Pluggable Dense (Gemini Embedding) and Sparse (BM25) vector embedding providers."""

import asyncio
import hashlib
import math
import re
import zlib
from abc import ABC, abstractmethod
from collections import Counter

import httpx
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_PLACEHOLDER_API_KEYS = {"", "your-gemini-api-key-here", "changeme"}
_TOKEN_RE = re.compile(r"[0-9A-Za-zÀ-ỹ]+(?:[._/-][0-9A-Za-zÀ-ỹ]+)*", re.UNICODE)
_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "are",
        "was",
        "were",
        "của",
        "và",
        "các",
        "những",
        "trong",
        "được",
        "cho",
        "với",
        "theo",
        "tại",
        "trên",
        "đến",
        "khi",
        "như",
        "này",
    }
)


def _l2_normalize(vector: list[float]) -> list[float]:
    """L2-normalize a dense vector for accurate cosine similarity search."""
    norm = math.sqrt(sum(v * v for v in vector))
    if norm <= 1e-12:
        return vector
    return [v / norm for v in vector]


class SparseVectorData(BaseModel):
    """Sparse vector representation with sorted unique integer indices and float weights."""

    indices: list[int] = Field(default_factory=list, description="Sorted positive 31-bit token IDs.")
    values: list[float] = Field(default_factory=list, description="BM25 term-frequency weights.")


class BaseEmbeddingProvider(ABC):
    """Abstract interface for dense semantic embedding providers."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Return the dimensionality of produced dense vectors."""

    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Synchronously embed a list of text strings into dense float vectors."""

    def embed_query(self, query: str) -> list[float]:
        """Synchronously embed a single search query string."""
        results = self.embed_texts([query])
        return results[0] if results else [0.0] * self.dimension

    async def aembed_texts(self, texts: list[str]) -> list[list[float]]:
        """Asynchronously embed a list of text strings into dense float vectors."""
        return await asyncio.to_thread(self.embed_texts, texts)

    async def aembed_query(self, query: str) -> list[float]:
        """Asynchronously embed a single search query string."""
        results = await self.aembed_texts([query])
        return results[0] if results else [0.0] * self.dimension


class DeterministicHashEmbedder(BaseEmbeddingProvider):
    """Deterministic feature-hashing dense embedder preserving token & bigram similarity offline."""

    def __init__(self, dimension: int | None = None) -> None:
        settings = get_settings()
        self._dimension = dimension or settings.embedding_dimension

    @property
    def dimension(self) -> int:
        """Return configured vector dimension."""
        return self._dimension

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Project tokenized unigrams and bigrams into an L2-normalized dense vector."""
        return [self._embed_single(text) for text in texts]

    def _embed_single(self, text: str) -> list[float]:
        vec = [0.0] * self._dimension
        tokens = [t.lower() for t in _TOKEN_RE.findall(text or "")]
        if not tokens:
            vec[0] = 1.0
            return vec

        features = list(tokens)
        features.extend(f"{tokens[i]}_{tokens[i + 1]}" for i in range(len(tokens) - 1))

        for feat, count in Counter(features).items():
            digest = hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest()
            raw_int = int.from_bytes(digest, "little")
            idx = raw_int % self._dimension
            sign = 1.0 if ((raw_int >> 32) & 1) == 0 else -1.0
            vec[idx] += sign * (1.0 + math.log1p(count))

        return _l2_normalize(vec)


class GeminiEmbeddingProvider(BaseEmbeddingProvider):
    """Dense embedding provider using `google-genai` (`gemini-embedding-001`) with fallback."""

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
        dimension: int | None = None,
        batch_size: int = 64,
        client: genai.Client | None = None,
        fallback_embedder: BaseEmbeddingProvider | None = None,
    ) -> None:
        settings = get_settings()
        self._api_key = (api_key if api_key is not None else settings.gemini_api_key).strip()
        self.model_name = (model_name or settings.gemini_embedding_model).strip()
        self._dimension = dimension or settings.embedding_dimension
        self.batch_size = max(1, batch_size)
        self._client = client
        self._fallback = fallback_embedder or DeterministicHashEmbedder(dimension=self._dimension)

    @property
    def dimension(self) -> int:
        """Return configured output dimensionality (default 768)."""
        return self._dimension

    @property
    def is_available(self) -> bool:
        """Return True when an injected client or valid GEMINI_API_KEY is configured."""
        return self._client is not None or self._api_key not in _PLACEHOLDER_API_KEYS

    def _get_client(self) -> genai.Client:
        if self._client is None:
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Synchronously embed texts via Gemini `embed_content`, falling back on error."""
        if not texts:
            return []
        if not self.is_available:
            return self._fallback.embed_texts(texts)

        sanitized = [t if t.strip() else " " for t in texts]
        vectors: list[list[float]] = []
        try:
            client = self._get_client()
            config = types.EmbedContentConfig(output_dimensionality=self._dimension)
            for start in range(0, len(sanitized), self.batch_size):
                batch = sanitized[start : start + self.batch_size]
                response = client.models.embed_content(
                    model=self.model_name,
                    contents=batch,
                    config=config,
                )
                for emb in response.embeddings or []:
                    vals = list(emb.values or [])
                    vectors.append(_l2_normalize(vals) if vals else [0.0] * self._dimension)
            if len(vectors) == len(texts):
                return vectors
        except (ValueError, RuntimeError, OSError, httpx.HTTPError) as exc:
            logger.warning("gemini_embed_sync_fallback", model=self.model_name, error=str(exc))

        return self._fallback.embed_texts(texts)

    async def aembed_texts(self, texts: list[str]) -> list[list[float]]:
        """Asynchronously embed texts via Gemini `aio.models.embed_content`, falling back on error."""
        if not texts:
            return []
        if not self.is_available:
            return await self._fallback.aembed_texts(texts)

        sanitized = [t if t.strip() else " " for t in texts]
        vectors: list[list[float]] = []
        try:
            client = self._get_client()
            config = types.EmbedContentConfig(output_dimensionality=self._dimension)
            for start in range(0, len(sanitized), self.batch_size):
                batch = sanitized[start : start + self.batch_size]
                response = await client.aio.models.embed_content(
                    model=self.model_name,
                    contents=batch,
                    config=config,
                )
                for emb in response.embeddings or []:
                    vals = list(emb.values or [])
                    vectors.append(_l2_normalize(vals) if vals else [0.0] * self._dimension)
            if len(vectors) == len(texts):
                return vectors
        except (ValueError, RuntimeError, OSError, httpx.HTTPError) as exc:
            logger.warning("gemini_embed_async_fallback", model=self.model_name, error=str(exc))

        return await self._fallback.aembed_texts(texts)


class BaseSparseEmbedder(ABC):
    """Abstract interface for sparse lexical vector generators (e.g., BM25)."""

    @abstractmethod
    def embed_sparse(self, text: str) -> SparseVectorData:
        """Convert a single text into a sparse lexical vector."""

    def embed_sparse_batch(self, texts: list[str]) -> list[SparseVectorData]:
        """Convert a batch of texts into sparse lexical vectors."""
        return [self.embed_sparse(t) for t in texts]


class BM25SparseEmbedder(BaseSparseEmbedder):
    """BM25 term-frequency sparse vector generator optimized for VI/EN financial terms and numbers."""

    def __init__(self, k1: float = 1.2, b: float = 0.75, avg_doc_len: float = 120.0) -> None:
        self.k1 = k1
        self.b = b
        self.avg_doc_len = max(1.0, avg_doc_len)

    @staticmethod
    def tokenize(text: str) -> list[str]:
        """Extract normalized lexical tokens preserving tickers, years, numbers, and VI words."""
        if not text or not text.strip():
            return []
        raw_tokens = [tok.lower() for tok in _TOKEN_RE.findall(text)]
        return [tok for tok in raw_tokens if len(tok) > 1 or tok.isdigit()]

    @staticmethod
    def token_to_index(token: str) -> int:
        """Deterministically hash a lexical token to a non-zero 31-bit integer index."""
        hashed = zlib.crc32(token.encode("utf-8")) & 0x7FFFFFFF
        return hashed if hashed != 0 else 1

    def embed_sparse(self, text: str) -> SparseVectorData:
        """Compute BM25 saturated TF weights mapped to sorted Qdrant sparse vector indices."""
        tokens = [t for t in self.tokenize(text) if t not in _STOPWORDS]
        if not tokens:
            return SparseVectorData(indices=[], values=[])

        doc_len = float(len(tokens))
        tf_counts = Counter(tokens)
        norm_factor = self.k1 * (1.0 - self.b + self.b * (doc_len / self.avg_doc_len))

        index_weights: dict[int, float] = {}
        for token, tf in tf_counts.items():
            idx = self.token_to_index(token)
            bm25_tf = (tf * (self.k1 + 1.0)) / (tf + norm_factor)
            index_weights[idx] = round(index_weights.get(idx, 0.0) + bm25_tf, 6)

        sorted_items = sorted(index_weights.items(), key=lambda item: item[0])
        return SparseVectorData(
            indices=[idx for idx, _ in sorted_items],
            values=[val for _, val in sorted_items],
        )


__all__ = [
    "BM25SparseEmbedder",
    "BaseEmbeddingProvider",
    "BaseSparseEmbedder",
    "DeterministicHashEmbedder",
    "GeminiEmbeddingProvider",
    "SparseVectorData",
]
