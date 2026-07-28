"""The one place in this codebase that makes a synchronous embedding API
call on the live voice-call path. Every other embedding use
(embedding_service.py's own FAQ-gap dedup, the Phase 4 PropertyChunk
backfill) is either write-time fire-and-forget or read-time against
already-stored vectors -- never a call that blocks a guest-facing tool
result. This module is deliberately treated with extra caution:

- Only fires when a guest's query is genuinely subjective/unstructured
  (RecommendPropertiesArgs.purpose_of_stay -- the one existing field that's
  inherently free text, unlike budget/location/guests/amenities which are
  all already structured and SQL-filterable) AND the SQL layer under-
  returned (fewer than 3 results) -- never for a purely structured query.
- Always scoped to the SQL candidate set already resolved for this host/
  budget/location -- never a global unfiltered search, so semantic search
  can never surface a property outside the guest's stated hard
  constraints, only re-rank/supplement within them.
- Wrapped in a hard asyncio.wait_for timeout
  (settings.semantic_search_timeout_seconds). On ANY failure or timeout,
  returns [] and the caller proceeds with SQL-only results -- same
  fail-open discipline as every optional integration in this codebase
  (Bright Data unset, SMTP down, Twilio sandbox window closed, etc.).
- Gated by settings.enable_semantic_property_search, a cheap kill-switch
  requiring no redeploy if real-call latency/cost proves worse than
  expected.

Compares against PropertyChunk rows of chunk_type "overview" or "reviews"
only -- "amenities"/"location"/"house_rules" chunks aren't relevant to a
subjective purpose-of-stay query like "romantic getaway" or "workcation".
"reviews" chunks don't exist yet (see property_chunk.py) but the type is
included now so nothing here needs touching once they do.
"""

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.property import Property
from app.models.property_chunk import PropertyChunk
from app.services import embedding_service

logger = logging.getLogger(__name__)

_RELEVANT_CHUNK_TYPES = ("overview", "reviews")


async def _search(db: AsyncSession, query_text: str, candidate_ids: list) -> list[Property]:
    if not candidate_ids:
        return []

    query_embedding = await embedding_service.get_embedding(query_text)
    if query_embedding is None:
        return []

    chunks = list(
        (
            await db.scalars(
                select(PropertyChunk).where(
                    PropertyChunk.property_id.in_(candidate_ids),
                    PropertyChunk.chunk_type.in_(_RELEVANT_CHUNK_TYPES),
                    PropertyChunk.embedding.isnot(None),
                )
            )
        ).all()
    )
    if not chunks:
        return []

    best_score_by_property: dict = {}
    for chunk in chunks:
        score = embedding_service.cosine_similarity(query_embedding, chunk.embedding)
        if score >= embedding_service.SEMANTIC_MATCH_THRESHOLD:
            existing = best_score_by_property.get(chunk.property_id, -1.0)
            if score > existing:
                best_score_by_property[chunk.property_id] = score

    if not best_score_by_property:
        return []

    matched_ids = list(best_score_by_property.keys())
    properties = list((await db.scalars(select(Property).where(Property.id.in_(matched_ids)))).all())
    properties.sort(key=lambda p: best_score_by_property.get(p.id, 0.0), reverse=True)
    return properties


async def run_semantic_search(
    db: AsyncSession, query_text: str, candidate_ids: list, timeout_seconds: float | None = None
) -> list[Property]:
    """Never raises, never blocks longer than timeout_seconds -- any
    failure/timeout returns [] and the caller proceeds SQL-only."""
    if not settings.enable_semantic_property_search:
        return []
    if not query_text or not query_text.strip():
        return []

    timeout = timeout_seconds if timeout_seconds is not None else settings.semantic_search_timeout_seconds
    try:
        return await asyncio.wait_for(_search(db, query_text, candidate_ids), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("Semantic property search timed out after %.1fs -- falling back to SQL-only results", timeout)
        return []
    except Exception:
        logger.exception("Semantic property search failed -- falling back to SQL-only results")
        return []
