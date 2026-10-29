"""Builds (or rebuilds) the knowledge-base fact rows search_faq reads, for
every property -- or one host's / one property's.

Not required for correctness: a property with no fact rows is answered from
facts built in memory on its first question (and synced in the background),
so deploying before running this regresses nothing. Running it just front-
loads that work, and computes the embeddings the semantic half of retrieval
uses, so the first caller to each property doesn't get lexical-only matching.

Needs OPENROUTER_API_KEY for embeddings; without it, facts are still written
(lexical search works) and embeddings are skipped.

Run (from backend/):
    python -m scripts.backfill_property_facts                  # every property
    python -m scripts.backfill_property_facts --user-id <uuid>
    python -m scripts.backfill_property_facts --property-id <uuid>
"""

import argparse
import asyncio
import uuid

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.property import Property
from app.services import embedding_service
from app.services.property.chunking import FACT_CHUNK_TYPE, sync_property_facts


async def _embed_missing(property_id: uuid.UUID) -> int:
    from app.models.property_chunk import PropertyChunk

    async with AsyncSessionLocal() as db:
        rows = (
            await db.scalars(
                select(PropertyChunk).where(
                    PropertyChunk.property_id == property_id,
                    PropertyChunk.chunk_type == FACT_CHUNK_TYPE,
                    PropertyChunk.embedding.is_(None),
                )
            )
        ).all()
        items = [(row.id, row.text) for row in rows]
    await embedding_service.backfill_property_chunk_embeddings(items)
    return len(items)


async def main(user_id: uuid.UUID | None, property_id: uuid.UUID | None) -> None:
    async with AsyncSessionLocal() as db:
        query = select(Property)
        if property_id is not None:
            query = query.where(Property.id == property_id)
        if user_id is not None:
            query = query.where(Property.user_id == user_id)
        properties = list((await db.scalars(query)).all())

    print(f"{len(properties)} propert{'y' if len(properties) == 1 else 'ies'} to process")
    for property_ in properties:
        async with AsyncSessionLocal() as db:
            fresh = await db.get(Property, property_.id)
            facts = await sync_property_facts(db, fresh)
        # sync_property_facts embeds in a detached task; wait for it here
        # instead (this process exits as soon as main() returns).
        embedded = await _embed_missing(property_.id)
        print(f"  {property_.display_name or property_.name}: {facts} facts, {embedded} embedded")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=uuid.UUID)
    parser.add_argument("--property-id", type=uuid.UUID)
    args = parser.parse_args()
    asyncio.run(main(args.user_id, args.property_id))
