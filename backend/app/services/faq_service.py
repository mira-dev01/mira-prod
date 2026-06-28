"""Host-managed FAQ knowledge base. Entries are added/verified from the
dashboard (app/api/v1/faq.py) or auto-generated on Airbnb import
(app/services/airbnb_import.py); search_faq (the voice tool) only reads.
"""

import uuid

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.faq_entry import FaqEntry
from app.models.property import Property
from app.services.airbnb_import import AUTO_FAQ_CATEGORIES


async def search_faq_entries(
    db: AsyncSession,
    user_id: uuid.UUID,
    query: str,
    property_id: uuid.UUID | None = None,
) -> list[FaqEntry]:
    pattern = f"%{query}%"
    stmt = select(FaqEntry).where(
        FaqEntry.user_id == user_id,
        FaqEntry.status == "verified",
        or_(FaqEntry.question.ilike(pattern), FaqEntry.answer.ilike(pattern), FaqEntry.category.ilike(pattern)),
    )
    if property_id is not None:
        stmt = stmt.where(or_(FaqEntry.property_id == property_id, FaqEntry.property_id.is_(None)))
    stmt = stmt.limit(3)
    return list((await db.scalars(stmt)).all())


async def search_legacy_property_faq(db: AsyncSession, property_id: uuid.UUID, query: str) -> list[dict]:
    """Fallback for the older inline Property.faq JSON field, for properties
    that haven't been migrated to structured FaqEntry rows yet."""
    property_ = await db.get(Property, property_id)
    if property_ is None or not property_.faq:
        return []
    query_lower = query.lower()
    return [
        item
        for item in property_.faq
        if query_lower in item.get("question", "").lower() or query_lower in item.get("answer", "").lower()
    ][:3]


async def list_faq_entries(db: AsyncSession, user_id: uuid.UUID) -> list[FaqEntry]:
    return list(
        (
            await db.scalars(
                select(FaqEntry).where(FaqEntry.user_id == user_id).order_by(FaqEntry.created_at.desc())
            )
        ).all()
    )


async def get_owned_faq_entry(db: AsyncSession, faq_id: uuid.UUID, user_id: uuid.UUID) -> FaqEntry | None:
    entry = await db.get(FaqEntry, faq_id)
    if entry is None or entry.user_id != user_id:
        return None
    return entry


async def sync_imported_faq_entries(
    db: AsyncSession,
    user_id: uuid.UUID,
    property_id: uuid.UUID,
    faq_entries: list[dict],
) -> int:
    """Replaces this property's auto-imported FAQ entries (categories in
    AUTO_FAQ_CATEGORIES) with a fresh batch, so re-importing a refreshed
    scrape updates the knowledge base instead of piling up duplicates. FAQ
    entries the host added by hand (any other category) are left alone."""
    await db.execute(
        delete(FaqEntry).where(
            FaqEntry.user_id == user_id,
            FaqEntry.property_id == property_id,
            FaqEntry.category.in_(AUTO_FAQ_CATEGORIES),
        )
    )
    for entry in faq_entries:
        db.add(
            FaqEntry(
                user_id=user_id,
                property_id=property_id,
                question=entry["question"],
                answer=entry["answer"],
                category=entry.get("category"),
                status="verified",
                verified_by="airbnb_import",
            )
        )
    await db.commit()
    return len(faq_entries)
