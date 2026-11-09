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

import hashlib
import logging
import math

from app.config import settings
from app.integrations import redis_client
from app.integrations import llm_clients
from app.services import usage_meter

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


# An embedding of a given text under a given model never changes, so a cached one
# is as good as a fresh one for as long as the model id is the same -- the TTL only
# bounds how long an unused entry occupies Redis.
_EMBEDDING_CACHE_TTL_SECONDS = 30 * 24 * 3600


def _embedding_cache_key(text: str) -> str:
    """Cache key for one text: case- and whitespace-insensitive (an embedding
    captures meaning, not surface formatting, so "Romantic  Getaway " and
    "romantic getaway" share an entry) and scoped to the model so changing
    EMBEDDING_MODEL can never serve vectors from the old one."""
    normalized = " ".join(text.lower().split())
    digest = hashlib.sha256(normalized.encode()).hexdigest()
    return f"embedding:{EMBEDDING_MODEL}:{digest}"


async def get_embedding(text: str) -> list[float] | None:
    """Returns None (never raises) on any failure -- semantic dedup is a
    nice-to-have UX layer on top of the FAQ Learning Engine, which already
    works correctly without it (see section 3.4). A missing embedding just
    means that one gap/entry falls back to no semantic suggestion, not a
    broken page.

    Served from the Redis cache when the same text was embedded before
    (guests ask the same questions again and again), which saves a paid,
    network-bound request -- and, on the live search_faq path, the cold-route
    latency that request can add. A cache miss, a Redis outage or no REDIS_URL
    all just fall through to the live call (redis_client fails open); a failed
    call is never cached."""
    if not settings.openrouter_api_key:
        return None
    cache_key = _embedding_cache_key(text)
    cached = await redis_client.cache_get_json(cache_key)
    if cached is not None:
        return cached
    try:
        client = llm_clients.openrouter_client(llm_clients.EMBEDDING)
        response = await client.embeddings.create(model=EMBEDDING_MODEL, input=text)
        usage_meter.record_llm_response_usage("openrouter", response, model=EMBEDDING_MODEL, purpose="embedding")
        embedding = response.data[0].embedding
    except Exception:
        logger.exception("Embedding request failed for text=%r -- semantic dedup will skip this item", text[:80])
        return None
    await redis_client.cache_set_json(cache_key, embedding, _EMBEDDING_CACHE_TTL_SECONDS)
    return embedding


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


async def backfill_property_chunk_embedding(chunk_id, text: str) -> None:
    """Same fire-and-forget pattern as backfill_faq_entry_embedding, for a
    newly-created PropertyChunk (app/models/property_chunk.py) -- fired
    from _upsert_property_from_parsed (app/api/v1/properties.py) via
    asyncio.create_task, AFTER the import HTTP response already returned,
    so an embedding API call never adds latency to an import."""
    from app.database import AsyncSessionLocal
    from app.models.property_chunk import PropertyChunk

    embedding = await get_embedding(text)
    if embedding is None:
        return
    try:
        async with AsyncSessionLocal() as db:
            row = await db.get(PropertyChunk, chunk_id)
            if row is not None:
                row.embedding = embedding
                await db.commit()
    except Exception:
        logger.exception("Failed to save embedding for property_chunk_id=%s", chunk_id)


async def backfill_property_chunk_embeddings(items: list[tuple]) -> None:
    """Embeds many (chunk_id, text) pairs -- a property's knowledge-base fact
    rows after a sync -- with a small concurrency cap so one edit can't fire
    dozens of simultaneous embedding requests. Same fire-and-forget, never-
    raises contract as backfill_property_chunk_embedding: a fact whose
    embedding fails just stays lexical-only in search_faq's retrieval."""
    import asyncio

    from app.services.property import kb_retrieval

    semaphore = asyncio.Semaphore(4)

    async def _one(chunk_id, text):
        async with semaphore:
            await backfill_property_chunk_embedding(chunk_id, text)

    try:
        await asyncio.gather(*[_one(chunk_id, text) for chunk_id, text in items], return_exceptions=True)
    finally:
        # Cached fact lists hold the embeddings loaded at the time; drop them
        # so the next search sees the freshly written vectors.
        kb_retrieval.invalidate_fact_cache()
