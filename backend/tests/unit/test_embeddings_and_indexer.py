"""Unit tests for Dense (Gemini / Hash) and Sparse (BM25) embedders and upload schemas."""

import math
from types import SimpleNamespace

import pytest

from app.api.v1.endpoints.documents import _sanitize_pdf_filename
from app.llm.embeddings import (
    BM25SparseEmbedder,
    DeterministicHashEmbedder,
    GeminiEmbeddingProvider,
)
from app.models.enums import AccessLevel, DocumentLanguage, FiscalPeriod
from app.schemas.document import DocumentUploadParams


def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    return sum(a * b for a, b in zip(vec_a, vec_b, strict=True))


def test_deterministic_hash_embedder_normalization_and_similarity() -> None:
    """Verify deterministic hash embedder outputs L2-normalized 768-dim vectors preserving token overlap."""
    embedder = DeterministicHashEmbedder(dimension=768)
    texts = [
        "Doanh thu thuần khối Công nghệ của FPT năm 2024 đạt 39.110 tỷ đồng, tăng trưởng 24,4%.",
        "Năm 2024, khối Công nghệ FPT ghi nhận doanh thu thuần 39.110 tỷ đồng.",
        "Bảng cân đối kế toán hợp nhất tại ngày 31 tháng 12 năm 2024.",
    ]
    vectors = embedder.embed_texts(texts)
    assert len(vectors) == 3
    for vec in vectors:
        assert len(vec) == 768
        norm = math.sqrt(sum(v * v for v in vec))
        assert math.isclose(norm, 1.0, rel_tol=1e-5)

    # Identical input produces identical vector
    assert embedder.embed_query(texts[0]) == vectors[0]

    # Similar financial sentences have higher cosine similarity than unrelated sentences
    sim_related = _cosine_similarity(vectors[0], vectors[1])
    sim_unrelated = _cosine_similarity(vectors[0], vectors[2])
    assert sim_related > sim_unrelated
    assert sim_related > 0.45


async def test_gemini_embedding_provider_calls_sdk_and_normalizes() -> None:
    """Verify GeminiEmbeddingProvider passes model and output_dimensionality to google-genai SDK."""
    captured_calls: list[dict[str, object]] = []

    def fake_embed_content(*, model: str, contents: list[str], config: object) -> object:
        captured_calls.append({"model": model, "contents": contents, "config": config})
        embeddings = [SimpleNamespace(values=[3.0, 4.0] + [0.0] * 766) for _ in contents]
        return SimpleNamespace(embeddings=embeddings)

    async def fake_aembed_content(*, model: str, contents: list[str], config: object) -> object:
        captured_calls.append({"model": model, "contents": contents, "config": config})
        embeddings = [SimpleNamespace(values=[0.0, 5.0, 12.0] + [0.0] * 765) for _ in contents]
        return SimpleNamespace(embeddings=embeddings)

    mock_client = SimpleNamespace(
        models=SimpleNamespace(embed_content=fake_embed_content),
        aio=SimpleNamespace(models=SimpleNamespace(embed_content=fake_aembed_content)),
    )

    provider = GeminiEmbeddingProvider(
        model_name="gemini-embedding-001",
        dimension=768,
        client=mock_client,  # type: ignore[arg-type]
    )
    assert provider.is_available is True

    sync_vecs = provider.embed_texts(["Doanh thu FPT 2024"])
    assert len(sync_vecs) == 1
    assert len(sync_vecs[0]) == 768
    assert math.isclose(sync_vecs[0][0], 0.6, rel_tol=1e-5)
    assert math.isclose(sync_vecs[0][1], 0.8, rel_tol=1e-5)

    async_vecs = await provider.aembed_texts(["Lợi nhuận sau thuế 2024"])
    assert len(async_vecs) == 1
    assert math.isclose(async_vecs[0][1], 5.0 / 13.0, rel_tol=1e-5)
    assert math.isclose(async_vecs[0][2], 12.0 / 13.0, rel_tol=1e-5)
    assert len(captured_calls) == 2
    assert captured_calls[0]["model"] == "gemini-embedding-001"


async def test_gemini_embedding_provider_fallback_when_no_api_key() -> None:
    """Verify GeminiEmbeddingProvider falls back cleanly to DeterministicHashEmbedder when unconfigured."""
    provider = GeminiEmbeddingProvider(api_key="", dimension=768, client=None)
    assert provider.is_available is False

    vecs = await provider.aembed_texts(["Báo cáo thường niên FPT 2024"])
    assert len(vecs) == 1
    assert len(vecs[0]) == 768


def test_bm25_sparse_embedder_preserves_financial_tokens_and_sorts_indices() -> None:
    """Verify BM25SparseEmbedder produces sorted unique indices and positive TF weights."""
    sparse_embedder = BM25SparseEmbedder()
    text = "FPT FPT năm 2024 đạt ROE 28.7% và doanh thu 62849 tỷ đồng của các cổ đông."
    sparse_vec = sparse_embedder.embed_sparse(text)

    assert len(sparse_vec.indices) > 0
    assert len(sparse_vec.indices) == len(sparse_vec.values)
    assert sparse_vec.indices == sorted(sparse_vec.indices)
    assert len(set(sparse_vec.indices)) == len(sparse_vec.indices)
    assert all(v > 0.0 for v in sparse_vec.values)

    # Repeated term 'fpt' has higher BM25 TF weight than single-occurrence term '62849'
    fpt_idx = sparse_embedder.token_to_index("fpt")
    num_idx = sparse_embedder.token_to_index("62849")
    stop_idx = sparse_embedder.token_to_index("của")

    idx_to_val = dict(zip(sparse_vec.indices, sparse_vec.values, strict=True))
    assert fpt_idx in idx_to_val
    assert num_idx in idx_to_val
    assert stop_idx not in idx_to_val
    assert idx_to_val[fpt_idx] > idx_to_val[num_idx]


def test_document_upload_params_and_filename_sanitization() -> None:
    """Verify ticker normalization and path-traversal safe PDF filename generation."""
    params = DocumentUploadParams(
        ticker="  fpt ",
        fiscal_year=2024,
        period=FiscalPeriod.FY,
        language=DocumentLanguage.VI,
        access_level=AccessLevel.INTERNAL_FINANCE,
    )
    assert params.ticker == "FPT"

    safe_name = _sanitize_pdf_filename("../../etc/passwd/FPT BCTC 2024", "FPT", 2024)
    assert ".." not in safe_name
    assert "/" not in safe_name
    assert safe_name.endswith(".pdf")

    with pytest.raises(ValueError):
        DocumentUploadParams(ticker="   ", fiscal_year=2024)
