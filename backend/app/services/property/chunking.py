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
import re
from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

CHUNK_TYPES = ("overview", "amenities", "location", "house_rules", "reviews")

# Knowledge-base fact rows (see build_fact_chunks) live in the same table as
# the coarse chunks above but are a separate layer: sync_property_chunks must
# never delete them and sync_property_facts must never delete the coarse ones.
FACT_CHUNK_TYPE = "fact"


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

    await db.execute(
        delete(PropertyChunk).where(
            PropertyChunk.property_id == property_.id, PropertyChunk.chunk_type != FACT_CHUNK_TYPE
        )
    )

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


# --- Knowledge-base facts ---------------------------------------------------
#
# search_faq (app/services/tool_handlers.py) answers ONLY from these. Each
# fact is one small, self-contained statement with a known origin, so a spoken
# claim can be traced back to (or shown to have no) supporting text. Built
# from exactly the sources the host controls: verified FAQ rows, the
# property's own fields, and seasonal notes -- nothing else.

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?\u0964])\s+|\n+")
_FACT_TARGET_CHARS = 280
_LONG_ANSWER_CHARS = 400
_AMENITIES_PER_FACT = 8


@dataclass
class FactDraft:
    text: str
    source_kind: str
    source_ref: str | None = None
    meta: dict | None = field(default=None)


def _normalize_for_dedupe(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _pack_sentences(text: str, limit: int = _FACT_TARGET_CHARS) -> list[str]:
    """Splits free text into sentences, then packs consecutive sentences into
    pieces of at most ~limit characters. A single over-long sentence is kept
    whole -- never cut mid-sentence, which could change its meaning."""
    sentences = [part.strip() for part in _SENTENCE_SPLIT_RE.split(text or "") if part and part.strip()]
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        if current and len(current) + 1 + len(sentence) > limit:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


def _faq_facts(question: str, answer: str, source_ref: str) -> list[FactDraft]:
    question = (question or "").strip()
    answer = (answer or "").strip()
    if not answer:
        return []
    if len(answer) <= _LONG_ANSWER_CHARS:
        return [FactDraft(f"Q: {question} A: {answer}" if question else answer, "faq_entry", source_ref)]
    return [
        FactDraft(f"Q: {question} A: {piece}" if question else piece, "faq_entry", source_ref)
        for piece in _pack_sentences(answer)
    ]


def build_fact_chunks(property_, faq_entries) -> list[FactDraft]:
    """Every knowledge-base fact for one property, de-duplicated. faq_entries
    is the property's VERIFIED FaqEntry rows (any object with .id/.question/
    .answer); the legacy inline Property.faq JSON is folded in too. Order is
    FAQ first so a host-edited answer wins over an identical imported
    sentence."""
    drafts: list[FactDraft] = []

    for entry in faq_entries or []:
        drafts.extend(_faq_facts(entry.question, entry.answer, str(entry.id)))
    for item in property_.faq or []:
        drafts.extend(_faq_facts(item.get("question", ""), item.get("answer", ""), "legacy"))

    drafts.append(
        FactDraft(
            f"Check-in time is {property_.check_in_time}. Check-out time is {property_.check_out_time}.",
            "structured",
            "check_times",
        )
    )
    if property_.max_guests:
        drafts.append(
            FactDraft(f"This property sleeps a maximum of {property_.max_guests} guests.", "structured", "max_guests")
        )
    if (property_.minimum_nights or 1) > 1:
        drafts.append(
            FactDraft(f"The minimum stay is {property_.minimum_nights} nights.", "structured", "minimum_nights")
        )

    if property_.usp:
        for piece in _pack_sentences(property_.usp):
            drafts.append(FactDraft(f"Description: {piece}", "description", "usp"))

    if property_.house_rules:
        for piece in _pack_sentences(property_.house_rules):
            drafts.append(FactDraft(f"House rule: {piece}", "house_rules", "house_rules"))

    if property_.neighborhood_info:
        for piece in _pack_sentences(property_.neighborhood_info):
            drafts.append(FactDraft(f"Neighborhood: {piece}", "neighborhood", "neighborhood_info"))
    for landmark in property_.landmarks or []:
        name = landmark.get("name")
        if not name:
            continue
        distance = landmark.get("distance_minutes")
        mode = landmark.get("mode") or ""
        if distance is not None:
            text = f"Neighborhood: {name} is {distance} minutes away{f' by {mode}' if mode else ''}."
        else:
            text = f"Neighborhood: near {name}."
        drafts.append(FactDraft(text, "neighborhood", "landmarks"))

    amenities = [a for a in (property_.amenities or []) if a]
    for start in range(0, len(amenities), _AMENITIES_PER_FACT):
        group = amenities[start : start + _AMENITIES_PER_FACT]
        drafts.append(FactDraft("Amenities: " + ", ".join(group) + ".", "amenity", "amenities"))

    for index, entry in enumerate(property_.seasonal_notes or []):
        note = entry.get("note")
        start_month, end_month = entry.get("start_month"), entry.get("end_month")
        if not note or start_month is None or end_month is None:
            continue
        drafts.append(
            FactDraft(
                f"Seasonal note: {note}",
                "seasonal_note",
                f"seasonal_{index}",
                {"start_month": start_month, "end_month": end_month},
            )
        )

    seen: set[str] = set()
    unique: list[FactDraft] = []
    for draft in drafts:
        key = _normalize_for_dedupe(draft.text)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(draft)
    return unique


async def sync_property_facts(db: AsyncSession, property_) -> int:
    """Rebuilds this property's knowledge-base fact rows from its current
    fields and verified FAQ rows (delete-and-recreate, like
    sync_property_chunks). Call after a property import/edit and after any
    FaqEntry create/update/delete -- search_faq reads only these rows, so a
    stale set means stale answers.

    Embeddings already computed for a fact whose text is unchanged are reused;
    only genuinely new text is embedded, fire-and-forget after the commit, so
    an edit never blocks on (or pays for) re-embedding the whole property.
    Returns the number of facts written."""
    from app.models.faq_entry import FaqEntry
    from app.models.property_chunk import PropertyChunk
    from app.services import embedding_service
    from app.services.property import kb_retrieval

    entries = list(
        (
            await db.scalars(
                select(FaqEntry).where(
                    FaqEntry.property_id == property_.id,
                    FaqEntry.status == "verified",
                )
            )
        ).all()
    )
    drafts = build_fact_chunks(property_, entries)

    existing = list(
        (
            await db.scalars(
                select(PropertyChunk).where(
                    PropertyChunk.property_id == property_.id, PropertyChunk.chunk_type == FACT_CHUNK_TYPE
                )
            )
        ).all()
    )
    embedding_by_text = {row.text: row.embedding for row in existing if row.embedding}

    await db.execute(
        delete(PropertyChunk).where(
            PropertyChunk.property_id == property_.id, PropertyChunk.chunk_type == FACT_CHUNK_TYPE
        )
    )
    new_rows = [
        PropertyChunk(
            property_id=property_.id,
            chunk_type=FACT_CHUNK_TYPE,
            text=draft.text,
            source_kind=draft.source_kind,
            source_ref=draft.source_ref,
            meta=draft.meta,
            embedding=embedding_by_text.get(draft.text),
        )
        for draft in drafts
    ]
    db.add_all(new_rows)
    await db.commit()
    kb_retrieval.invalidate_fact_cache(property_.id)

    to_embed = [(row.id, row.text) for row in new_rows if row.embedding is None]
    if to_embed:
        asyncio.create_task(embedding_service.backfill_property_chunk_embeddings(to_embed))
    return len(new_rows)
