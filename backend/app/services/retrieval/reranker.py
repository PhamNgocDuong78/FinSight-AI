"""Pluggable Cross-Encoder & Gemini Flash-Lite Reranker for Hybrid Retrieval."""

import asyncio
import importlib
import math
import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from typing import Any

import httpx
from google import genai

from app.agents.prompts.reranker import RERANKER_SYSTEM_PROMPT, build_reranker_prompt
from app.core.config import get_settings
from app.core.logging import get_logger
from app.llm.embeddings import BM25SparseEmbedder
from app.models.enums import ChunkType
from app.schemas.retrieval import (
    RerankBatchOutput,
    RerankComparisonReport,
    RerankRankChange,
    RetrievedChunk,
)
from app.services.ingestion.financial_etl import normalize_gemini_model_name
from app.services.retrieval.hybrid_retriever import expand_query_with_metric_synonyms

logger = get_logger(__name__)

_PLACEHOLDER_API_KEYS = {"", "your-gemini-api-key-here", "changeme"}
_QUERY_STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "from", "what", "how", "why", "in", "of", "to",
        "của", "và", "các", "những", "trong", "được", "cho", "với", "theo", "tại",
        "trên", "đến", "khi", "như", "này", "là", "bao", "nhiêu", "năm", "hãy",
    }
)
_NUMERIC_OR_CODE_RE = re.compile(r"\b(?:[vV]\.\d+|\d+(?:[.,]\d+)?%?)\b")


def _sigmoid(x: float) -> float:
    """Map unbounded Cross-Encoder logit into probability interval [0.0, 1.0]."""
    return 1.0 / (1.0 + math.exp(-max(-20.0, min(20.0, float(x)))))


def _clamp_score(val: float) -> float:
    """Clamp a relevance score to [0.0, 1.0] rounded to 6 decimal places."""
    return round(max(0.0, min(1.0, float(val))), 6)


def _format_candidate_passage(chunk: RetrievedChunk, max_chars: int = 2500) -> str:
    """Format full Parent Chunk context for joint (query, passage) Cross-Encoder scoring."""
    header = (
        f"[{chunk.ticker} {chunk.fiscal_year} {chunk.period.value} - Trang {chunk.page_number}] "
        f"{chunk.section_title or chunk.doc_title}"
    )
    body = (chunk.content_markdown or chunk.child_snippet or "").strip()[:max_chars]
    return f"{header}\n{chunk.child_snippet}\n{body}".strip()


class BaseReranker(ABC):
    """Abstract interface for pluggable Cross-Encoder Rerankers (Top-20 -> Top-K)."""

    def __init__(self, default_top_k: int | None = None) -> None:
        self.default_top_k = max(1, default_top_k or get_settings().rerank_top_k)

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Return identifier of the active reranker model."""

    @abstractmethod
    def score_pairs(self, query: str, candidates: Sequence[RetrievedChunk]) -> list[float]:
        """Synchronously score each (query, candidate) pair in [0.0, 1.0]."""

    async def ascore_pairs(self, query: str, candidates: Sequence[RetrievedChunk]) -> list[float]:
        """Asynchronously score each (query, candidate) pair in [0.0, 1.0]."""
        return await asyncio.to_thread(self.score_pairs, query, candidates)

    def _build_report(
        self,
        query: str,
        candidates: Sequence[RetrievedChunk],
        scores: Sequence[float],
        *,
        top_k: int | None = None,
        min_score: float | None = None,
    ) -> RerankComparisonReport:
        resolved_k = max(1, top_k or self.default_top_k)
        if not candidates:
            return RerankComparisonReport(
                query=query, reranker_model=self.model_name, input_count=0, output_count=0
            )

        indexed = [
            (idx, chunk.model_copy(update={"rerank_score": _clamp_score(sc)}), _clamp_score(sc))
            for idx, (chunk, sc) in enumerate(zip(candidates, scores, strict=False), start=1)
            if min_score is None or _clamp_score(sc) >= min_score
        ]
        indexed.sort(key=lambda item: (item[2], item[1].rrf_score, -item[0]), reverse=True)
        retained = indexed[:resolved_k]

        before_top_k = list(candidates[:resolved_k])
        mean_rrf_before = (
            round(sum(c.rrf_score for c in before_top_k) / len(before_top_k), 6) if before_top_k else 0.0
        )
        mean_rerank_after = (
            round(sum(item[2] for item in retained) / len(retained), 6) if retained else 0.0
        )

        final_chunks = [item[1] for item in retained]
        rank_changes = [
            RerankRankChange(
                parent_chunk_id=chunk_obj.parent_chunk_id,
                ticker=chunk_obj.ticker,
                fiscal_year=chunk_obj.fiscal_year,
                page_number=chunk_obj.page_number,
                section_title=chunk_obj.section_title,
                rank_before=rank_before,
                rank_after=rank_after,
                rank_delta=rank_before - rank_after,
                rrf_score=chunk_obj.rrf_score,
                rerank_score=r_score,
            )
            for rank_after, (rank_before, chunk_obj, r_score) in enumerate(retained, start=1)
        ]

        top_1_before = candidates[0].parent_chunk_id if candidates else None
        top_1_after = final_chunks[0].parent_chunk_id if final_chunks else None
        top_1_changed = bool(top_1_before and top_1_after and top_1_before != top_1_after)

        logger.info(
            "retrieval_rerank_completed",
            reranker_model=self.model_name,
            input_count=len(candidates),
            output_count=len(final_chunks),
            top_1_changed=top_1_changed,
            mean_rrf_score_before=mean_rrf_before,
            mean_rerank_score_after=mean_rerank_after,
            rank_changes=[rc.model_dump(mode="json") for rc in rank_changes],
        )
        return RerankComparisonReport(
            query=query,
            reranker_model=self.model_name,
            input_count=len(candidates),
            output_count=len(final_chunks),
            top_1_changed=top_1_changed,
            top_1_before_id=top_1_before,
            top_1_after_id=top_1_after,
            mean_rrf_score_before=mean_rrf_before,
            mean_rerank_score_after=mean_rerank_after,
            rank_changes=rank_changes,
            results=final_chunks,
        )

    def rerank_with_report(
        self, query: str, candidates: Sequence[RetrievedChunk], *, top_k: int | None = None, min_score: float | None = None
    ) -> RerankComparisonReport:
        scores = self.score_pairs(query, candidates) if candidates else []
        return self._build_report(query, candidates, scores, top_k=top_k, min_score=min_score)

    async def arerank_with_report(
        self, query: str, candidates: Sequence[RetrievedChunk], *, top_k: int | None = None, min_score: float | None = None
    ) -> RerankComparisonReport:
        scores = await self.ascore_pairs(query, candidates) if candidates else []
        return self._build_report(query, candidates, scores, top_k=top_k, min_score=min_score)

    def rerank(
        self, query: str, candidates: Sequence[RetrievedChunk], *, top_k: int | None = None, min_score: float | None = None
    ) -> list[RetrievedChunk]:
        return self.rerank_with_report(query, candidates, top_k=top_k, min_score=min_score).results

    async def arerank(
        self, query: str, candidates: Sequence[RetrievedChunk], *, top_k: int | None = None, min_score: float | None = None
    ) -> list[RetrievedChunk]:
        return (await self.arerank_with_report(query, candidates, top_k=top_k, min_score=min_score)).results


class FinancialCrossEncoderScorer(BaseReranker):
    """Deterministic domain-aware Cross-Attention scorer evaluating (query, full Parent Chunk)."""

    @property
    def model_name(self) -> str:
        return "finsight-financial-cross-encoder-v1"

    def score_pairs(self, query: str, candidates: Sequence[RetrievedChunk]) -> list[float]:
        if not candidates:
            return []
        raw_q = BM25SparseEmbedder.tokenize(expand_query_with_metric_synonyms(query))
        q_tokens = [t for t in raw_q if t not in _QUERY_STOPWORDS] or raw_q
        if not q_tokens:
            return [_clamp_score(c.rrf_score) for c in candidates]

        q_unique = list(dict.fromkeys(q_tokens))
        q_bigrams = {f"{q_tokens[i]}_{q_tokens[i + 1]}" for i in range(len(q_tokens) - 1)}
        q_codes = {m.lower() for m in _NUMERIC_OR_CODE_RE.findall(query)}
        max_rrf = max((c.rrf_score for c in candidates), default=1.0) or 1.0

        scores: list[float] = []
        for chunk in candidates:
            title_text = f"{chunk.section_title or ''} {chunk.doc_title} {chunk.ticker} {chunk.fiscal_year}"
            full_text = f"{title_text}\n{chunk.child_snippet}\n{chunk.content_markdown}"
            doc_tokens = BM25SparseEmbedder.tokenize(full_text)
            doc_set, title_set = set(doc_tokens), set(BM25SparseEmbedder.tokenize(title_text))
            parent_set = set(BM25SparseEmbedder.tokenize(chunk.content_markdown))

            term_cov = sum(1 for t in q_unique if t in doc_set) / len(q_unique)
            parent_cov = sum(1 for t in q_unique if t in parent_set) / len(q_unique)
            title_cov = sum(1 for t in q_unique if t in title_set) / len(q_unique)
            doc_bigrams = {f"{doc_tokens[i]}_{doc_tokens[i + 1]}" for i in range(len(doc_tokens) - 1)}
            bigram_score = len(q_bigrams & doc_bigrams) / len(q_bigrams) if q_bigrams and doc_bigrams else term_cov
            code_score = sum(1 for c in q_codes if c in full_text.lower()) / len(q_codes) if q_codes else term_cov
            hit_count = sum(1 for t in doc_tokens if t in q_unique)
            density = min(1.0, math.log1p(hit_count) / math.log1p(max(4, len(q_unique) * 3)))
            rrf_prior = min(1.0, chunk.rrf_score / max_rrf)
            table_bonus = (
                1.0
                if chunk.chunk_type == ChunkType.TABLE and code_score >= 0.5 and term_cov >= 0.5
                else 0.0
            )

            scores.append(
                _clamp_score(
                    0.30 * term_cov + 0.20 * parent_cov + 0.14 * bigram_score
                    + 0.12 * title_cov + 0.10 * code_score + 0.08 * table_bonus
                    + 0.03 * density + 0.03 * rrf_prior
                )
            )
        return scores


class HuggingFaceCrossEncoderReranker(BaseReranker):
    """Cross-Encoder Reranker backed by `BAAI/bge-reranker-base` (or injected predictor)."""

    def __init__(
        self,
        model_name: str | None = None,
        predictor: Callable[[list[tuple[str, str]]], Sequence[float]] | None = None,
        fallback_scorer: BaseReranker | None = None,
        default_top_k: int | None = None,
    ) -> None:
        super().__init__(default_top_k=default_top_k)
        self._model_name = (model_name or get_settings().cross_encoder_model_name).strip()
        self._predictor = predictor
        self._load_attempted = False
        self._fallback = fallback_scorer or FinancialCrossEncoderScorer(default_top_k=default_top_k)

    @property
    def model_name(self) -> str:
        return self._model_name

    def _get_predictor(self) -> Callable[[list[tuple[str, str]]], Sequence[float]] | None:
        if self._predictor is not None or self._load_attempted:
            return self._predictor
        self._load_attempted = True
        try:
            model = importlib.import_module("sentence_transformers").CrossEncoder(self._model_name)
            self._predictor = lambda pairs: [float(x) for x in model.predict(pairs)]
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            logger.info("cross_encoder_using_domain_fallback", model=self._model_name, reason=str(exc))
        return self._predictor

    def score_pairs(self, query: str, candidates: Sequence[RetrievedChunk]) -> list[float]:
        if not candidates:
            return []
        predictor = self._get_predictor()
        if predictor is None:
            return self._fallback.score_pairs(query, candidates)
        raw_logits = predictor([(query, _format_candidate_passage(c)) for c in candidates])
        return [_clamp_score(_sigmoid(float(logit))) for logit in raw_logits]


class GeminiFlashLiteReranker(BaseReranker):
    """Cross-Attention Reranker powered by Gemini Flash-Lite (`gemini-3.5-flash-lite`) Structured Output."""

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
        client: genai.Client | None = None,
        fallback_scorer: BaseReranker | None = None,
        default_top_k: int | None = None,
    ) -> None:
        super().__init__(default_top_k=default_top_k)
        settings = get_settings()
        self._api_key = (api_key if api_key is not None else settings.gemini_api_key).strip()
        self._model_name = normalize_gemini_model_name(model_name or settings.reranker_model)
        self._client = client
        self._fallback = fallback_scorer or FinancialCrossEncoderScorer(default_top_k=default_top_k)

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def is_available(self) -> bool:
        return self._client is not None or self._api_key not in _PLACEHOLDER_API_KEYS

    def _get_client(self) -> genai.Client:
        if self._client is None:
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def _build_kwargs(self, query: str, candidates: Sequence[RetrievedChunk]) -> dict[str, Any]:
        return {
            "model": self._model_name,
            "system_instruction": RERANKER_SYSTEM_PROMPT,
            "input": build_reranker_prompt(query, candidates),
            "response_format": {
                "type": "text",
                "mime_type": "application/json",
                "schema": RerankBatchOutput.model_json_schema(),
            },
            "store": False,
        }

    def _merge(self, output_text: str | None, fallback_scores: list[float]) -> list[float]:
        parsed = RerankBatchOutput.model_validate_json(output_text or '{"scores": []}')
        score_map = {item.candidate_index: item.relevance_score for item in parsed.scores}
        return [
            _clamp_score(0.85 * float(score_map[i]) + 0.15 * fb) if i in score_map else fb
            for i, fb in enumerate(fallback_scores)
        ]

    def score_pairs(self, query: str, candidates: Sequence[RetrievedChunk]) -> list[float]:
        if not candidates:
            return []
        fb_scores = self._fallback.score_pairs(query, candidates)
        if not query.strip() or not self.is_available:
            return fb_scores
        try:
            res = self._get_client().interactions.create(**self._build_kwargs(query, candidates))
            return self._merge(res.output_text, fb_scores)
        except (ValueError, RuntimeError, OSError, httpx.HTTPError) as exc:
            logger.warning("gemini_rerank_sync_fallback", model=self._model_name, error=str(exc))
            return fb_scores

    async def ascore_pairs(self, query: str, candidates: Sequence[RetrievedChunk]) -> list[float]:
        if not candidates:
            return []
        fb_scores = self._fallback.score_pairs(query, candidates)
        if not query.strip() or not self.is_available:
            return fb_scores
        try:
            res = await self._get_client().aio.interactions.create(**self._build_kwargs(query, candidates))
            return self._merge(res.output_text, fb_scores)
        except (ValueError, RuntimeError, OSError, httpx.HTTPError) as exc:
            logger.warning("gemini_rerank_async_fallback", model=self._model_name, error=str(exc))
            return fb_scores


class CrossEncoderReranker(BaseReranker):
    """Pluggable facade routing to Cross-Encoder (`BAAI/bge-reranker-base`) or Gemini Flash-Lite."""

    def __init__(
        self,
        backend: BaseReranker | None = None,
        *,
        provider: str | None = None,
        default_top_k: int | None = None,
        gemini_client: genai.Client | None = None,
        cross_encoder_predictor: Callable[[list[tuple[str, str]]], Sequence[float]] | None = None,
    ) -> None:
        super().__init__(default_top_k=default_top_k)
        resolved = (provider or get_settings().reranker_provider).strip().lower()
        if backend is not None:
            self._backend = backend
        elif cross_encoder_predictor is not None or resolved == "cross_encoder":
            self._backend = HuggingFaceCrossEncoderReranker(
                predictor=cross_encoder_predictor, default_top_k=self.default_top_k
            )
        elif resolved == "lexical_cross":
            self._backend = FinancialCrossEncoderScorer(default_top_k=self.default_top_k)
        else:
            gemini = GeminiFlashLiteReranker(client=gemini_client, default_top_k=self.default_top_k)
            self._backend = (
                gemini
                if gemini.is_available or resolved == "gemini"
                else FinancialCrossEncoderScorer(default_top_k=self.default_top_k)
            )

    @property
    def model_name(self) -> str:
        return self._backend.model_name

    def score_pairs(self, query: str, candidates: Sequence[RetrievedChunk]) -> list[float]:
        return self._backend.score_pairs(query, candidates)

    async def ascore_pairs(self, query: str, candidates: Sequence[RetrievedChunk]) -> list[float]:
        return await self._backend.ascore_pairs(query, candidates)
