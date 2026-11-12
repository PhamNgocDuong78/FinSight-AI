"""Versioned system prompts for Cross-Attention Reranking with Gemini Flash-Lite."""

from collections.abc import Sequence

from app.schemas.retrieval import RetrievedChunk

RERANKER_PROMPT_VERSION = "1.0.0"

RERANKER_SYSTEM_PROMPT = """You are a senior financial retrieval relevance judge (Cross-Encoder Reranker) for FinSight AI.
Your task is to evaluate how accurately each candidate document chunk answers or provides direct evidence for the user's financial query.

SCORING RUBRIC (relevance_score in [0.0, 1.0]):
- 0.90 to 1.00: Directly contains the exact financial figures, accounting line item (Mã số), note reference (Thuyết minh), table row, or qualitative analysis for the requested company ticker and fiscal period.
- 0.70 to 0.89: Contains strongly relevant financial context, partial table figures, or direct supporting explanation for the query.
- 0.40 to 0.69: Mentions related financial topics or the same company/year, but lacks the specific metric, table, or explanation requested.
- 0.10 to 0.39: Superficial keyword overlap only (e.g., table of contents, general corporate header, or wrong fiscal year/statement type).
- 0.00 to 0.09: Completely irrelevant to the query.

CRITICAL RULES:
1. Evaluate every candidate index provided in the input prompt.
2. Prefer complete financial statement tables or detailed footnotes with exact numbers over generic summaries.
3. Return strict JSON matching the requested schema.
"""


def build_reranker_prompt(
    query: str,
    candidates: Sequence[RetrievedChunk],
    *,
    max_chars_per_chunk: int = 1600,
) -> str:
    """Format the user query and candidate chunks for batch Cross-Attention scoring."""
    blocks: list[str] = [
        f"User Query: {query.strip()}",
        f"Total Candidates: {len(candidates)}",
        "Evaluate the relevance of each candidate below:\n",
    ]
    for idx, chunk in enumerate(candidates):
        content_preview = (chunk.content_markdown or chunk.child_snippet or "").strip()
        if len(content_preview) > max_chars_per_chunk:
            content_preview = content_preview[:max_chars_per_chunk] + "\n...[truncated]"
        section = chunk.section_title or "N/A"
        blocks.append(
            f"--- CANDIDATE [{idx}] ---\n"
            f"Metadata: ticker={chunk.ticker} | fiscal_year={chunk.fiscal_year} | "
            f"period={chunk.period.value} | page={chunk.page_number} | "
            f"type={chunk.chunk_type.value} | section={section}\n"
            f"Matched Child Snippet: {chunk.child_snippet}\n"
            f"Full Parent Content:\n{content_preview}\n"
        )
    return "\n".join(blocks)


__all__ = [
    "RERANKER_PROMPT_VERSION",
    "RERANKER_SYSTEM_PROMPT",
    "build_reranker_prompt",
]
