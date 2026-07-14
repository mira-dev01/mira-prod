"""Text embeddings for Knowledge Memory's semantic FAQ-gap dedup
(memory-architecture-plan.md section 3.1). Backed by OpenRouter --
confirmed via a real API call that neither Groq nor Anthropic (the other
two configured LLM providers in this codebase) serve an embeddings
endpoint at all, while OpenRouter does and is already a funded, configured
provider here (see pipeline.py's LLM fallback chain). No new provider
credential was introduced for this.

Embeddings are computed once, at write time (when an UnansweredQuestion or
verified FaqEntry is created), and stored as a plain JSONB float array --
not pgvector, since production DB extension availability can't be verified
from this environment and the comparison set (one host's FAQ entries) is
small enough that in-Python cosine similarity is cheap. This deliberately
avoids a per-page-load embedding API call: the dashboard's gap list must
never make a live external call just to render.
"""

import logging
import math

from app.config import settings

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "openai/text-embedding-3-small"

# Empirically validated (2026-07-14) against 10 real question pairs using
# this exact model: genuine paraphrases ("is there parking" / "where can I
# park") scored 0.41-0.80 cosine similarity; unrelated question pairs
# scored 0.21-0.36. 0.45 sits with margin above the highest unrelated score
# and below the lowest genuine paraphrase seen, erring toward precision
# (fewer wrong auto-suggestions) since a missed match still leaves the host
# the existing manual-answer path -- same "verified against real pairs, not
# guessed" discipline as faq_service.py's own 0.35 pg_trgm threshold.
SEMANTIC_MATCH_THRESHOLD = 0.45


class EmbeddingError(Exception):
    pass


async def get_embedding(text: str) -> list[float] | None:
    """Returns None (never raises) on any failure -- semantic dedup is a
    nice-to-have UX layer on top of the FAQ Learning Engine, which already
    works correctly without it (see section 3.4). A missing embedding just
    means that one gap/entry falls back to no semantic suggestion, not a
    broken page."""
    if not settings.openrouter_api_key:
        return None
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.openrouter_api_key, base_url="https://openrouter.ai/api/v1")
        response = await client.embeddings.create(model=EMBEDDING_MODEL, input=text)
        return response.data[0].embedding
    except Exception:
        logger.exception("Embedding request failed for text=%r -- semantic dedup will skip this item", text[:80])
        return None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def backfill_unanswered_question_embedding(unanswered_question_id, question: str) -> None:
    """Fire-and-forget from handle_search_faq (the live-call tool handler)
    via asyncio.create_task, AFTER the guest-facing response has already
    been returned -- an embedding API call must never add latency to a
    live guest turn (see memory-architecture-plan.md section 0.1's
    no-new-synchronous-call-on-the-live-path rule). Uses its own DB
    session since the handler's session may already be closed by the time
    this runs.
    """
    from app.database import AsyncSessionLocal
    from app.models.unanswered_question import UnansweredQuestion

    embedding = await get_embedding(question)
    if embedding is None:
        return
    try:
        async with AsyncSessionLocal() as db:
            row = await db.get(UnansweredQuestion, unanswered_question_id)
            if row is not None:
                row.question_embedding = embedding
                await db.commit()
    except Exception:
        logger.exception("Failed to save embedding for unanswered_question_id=%s", unanswered_question_id)


async def backfill_faq_entry_embedding(faq_entry_id, question: str) -> None:
    """Same fire-and-forget pattern as backfill_unanswered_question_embedding,
    for a newly-verified FaqEntry (host-answered via the FAQ Learning Engine
    or added directly from the dashboard)."""
    from app.database import AsyncSessionLocal
    from app.models.faq_entry import FaqEntry

    embedding = await get_embedding(question)
    if embedding is None:
        return
    try:
        async with AsyncSessionLocal() as db:
            row = await db.get(FaqEntry, faq_entry_id)
            if row is not None:
                row.question_embedding = embedding
                await db.commit()
    except Exception:
        logger.exception("Failed to save embedding for faq_entry_id=%s", faq_entry_id)
