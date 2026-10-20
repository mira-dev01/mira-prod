"""Assembles a property's PropertyChunk text bodies from fields already
extracted by the import parsers/normalizer (app/services/airbnb_import.py,
app/services/property_normalizer.py) -- no separate scrape or job, since
this data is already in hand at import time (see
_upsert_property_from_parsed in app/api/v1/properties.py).

Each function returns None (never an empty-string chunk) when the
property has nothing to say for that chunk type -- callers should skip
creating a PropertyChunk row entirely rather than embedding an empty
string.
"""

import asyncio

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

CHUNK_TYPES = ("overview", "amenities", "location", "house_rules", "reviews")


def _overview_chunk_text(property_) -> str | None:
    parts = []
    name = property_.display_name or property_.raw_name or property_.name
    descriptor_bits = [b for b in (property_.property_type, property_.property_style) if b]
    if descriptor_bits:
        parts.append(f"{name} is a {' '.join(descriptor_bits)}.")
    else:
        parts.append(f"{name}.")
    if property_.usp:
        parts.append(property_.usp)
    if property_.city:
        parts.append(f"Located in {property_.city}.")
    text = " ".join(parts).strip()
    return text or None


def _amenities_chunk_text(property_) -> str | None:
    if not property_.amenities:
        return None
    return "Amenities: " + ", ".join(property_.amenities) + "."


def _location_chunk_text(property_) -> str | None:
    parts = []
    if property_.neighborhood_info:
        parts.append(property_.neighborhood_info)
    for landmark in property_.landmarks or []:
        name = landmark.get("name")
        if not name:
            continue
        distance = landmark.get("distance_minutes")
        mode = landmark.get("mode") or ""
        if distance is not None:
            parts.append(f"{name} is {distance} minutes away{f' by {mode}' if mode else ''}.")
        else:
            parts.append(f"Near {name}.")
    text = " ".join(parts).strip()
    return text or None


def _house_rules_chunk_text(property_) -> str | None:
    return property_.house_rules or None


def build_property_chunks(property_) -> dict[str, str]:
    """Returns {chunk_type: text} for every chunk type this property has
    real content for -- "reviews" is never included here (no review text
    exists in the scrape schema yet; the chunk_type is reserved for a
    future import source, see app/models/property_chunk.py)."""
    builders = {
        "overview": _overview_chunk_text,
        "amenities": _amenities_chunk_text,
        "location": _location_chunk_text,
        "house_rules": _house_rules_chunk_text,
    }
    chunks = {}
    for chunk_type, builder in builders.items():
        text = builder(property_)
        if text:
            chunks[chunk_type] = text
    return chunks


async def sync_property_chunks(db: AsyncSession, property_) -> None:
    """Replaces this property's chunks with a fresh batch built from its
    current fields, same delete-and-recreate-on-reimport pattern as
    faq_service.sync_imported_faq_entries -- re-importing a refreshed
    scrape updates the chunk set instead of piling up duplicates.

    Embeddings are computed fire-and-forget (asyncio.create_task), AFTER
    this function's own commit, so an embedding API call never adds
    latency to the import HTTP response -- same discipline as
    embedding_service.backfill_faq_entry_embedding."""
    from app.models.property_chunk import PropertyChunk
    from app.services import embedding_service

    await db.execute(delete(PropertyChunk).where(PropertyChunk.property_id == property_.id))

    chunk_texts = build_property_chunks(property_)
    new_chunks = [
        PropertyChunk(property_id=property_.id, chunk_type=chunk_type, text=text)
        for chunk_type, text in chunk_texts.items()
    ]
    db.add_all(new_chunks)
    await db.commit()
    # No db.refresh() needed here -- PropertyChunk.id is a client-side
    # Python default (uuid.uuid4, see UUIDPkMixin), already populated in
    # memory at flush time, not a server-side default requiring a
    # round-trip to read back. Refreshing each chunk individually would be
    # an avoidable N+1 (one extra SELECT per chunk on every import).

    for chunk in new_chunks:
        asyncio.create_task(embedding_service.backfill_property_chunk_embedding(chunk.id, chunk.text))
