"""
FAQ content update script -- adds/updates FaqEntry rows for a real,
existing host account: breakfast, discount/OTA-comparison handling (this
part is prompt-level, see app/prompts/system_prompt.py -- not seeded here),
pool, caretaker, and parking (including a specific override for Mocha/Nook/
Chic, which have limited roadside parking unlike most Goa properties).

Idempotent: upserts by (user_id, property_id, question) -- re-running after
editing PARKING_OVERRIDE_PROPERTY_NAMES or the answer text below updates
existing rows instead of duplicating them.

Usage (run from the backend/ directory):
    DATABASE_URL=<render-db-url> HOST_EMAIL=<real host email> python seed_faq_updates.py

Or if .env already points at the right database and HOST_EMAIL is set there:
    python seed_faq_updates.py
"""

import asyncio
import os
import sys

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# Allow running from backend/ directory
sys.path.insert(0, ".")

from app.config import settings
from app.models.faq_entry import FaqEntry
from app.models.property import Property
from app.models.user import User

# Property names with a documented parking exception -- confirmed to be
# real, existing properties on the target host's account, not created here.
PARKING_OVERRIDE_PROPERTY_NAMES = ["Mocha", "Nook", "Chic"]

BREAKFAST_ANSWER = (
    "Breakfast is not included in the current room quote. We're happy to recommend nearby "
    "restaurants, and can also arrange pre-booked breakfast for ₹450 per person per day if "
    "you'd like."
)

# Pool/caretaker answers are host-specific per property (opening hours,
# whether the pool is heated, which staff member is on-site when) -- these
# are seeded as clearly-marked placeholders so they show up on the FAQ
# dashboard (frontend/src/app/dashboard/faq/page.tsx) as "pending" and the
# host can edit them with the real per-property details before verifying.
POOL_PLACEHOLDER_ANSWER = (
    "[TODO: host to confirm] Pool hours and whether it is temperature-controlled -- please edit "
    "this entry with this property's actual pool timings and heating details."
)
CARETAKER_PLACEHOLDER_ANSWER = (
    "[TODO: host to confirm] Caretaker/on-site staff availability for this property -- please edit "
    "this entry with the actual hours or contact details."
)

GOA_PARKING_ANSWER = (
    "This property has safe roadside parking with security assistance. Priority parking is "
    "available for elderly and physically challenged guests."
)
LIMITED_PARKING_ANSWER = "This property has limited roadside parking."


async def _upsert_faq_entry(
    db: AsyncSession,
    user_id,
    property_id,
    question: str,
    answer: str,
    category: str,
    status: str = "pending",
) -> bool:
    """Returns True if a new row was added, False if an existing one was updated."""
    existing = await db.scalar(
        select(FaqEntry).where(
            FaqEntry.user_id == user_id,
            FaqEntry.property_id == property_id,
            FaqEntry.question == question,
        )
    )
    if existing is not None:
        existing.answer = answer
        existing.category = category
        return False

    db.add(
        FaqEntry(
            user_id=user_id,
            property_id=property_id,
            question=question,
            answer=answer,
            category=category,
            status=status,
            verified_by="seed_faq_updates",
        )
    )
    return True


async def seed() -> None:
    host_email = os.environ.get("HOST_EMAIL")
    if not host_email:
        print("HOST_EMAIL env var is required -- the real host account's login email.")
        print("Usage: DATABASE_URL=<render-db-url> HOST_EMAIL=<host email> python seed_faq_updates.py")
        sys.exit(1)

    engine = create_async_engine(settings.database_url)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        host = await db.scalar(select(User).where(User.email == host_email))
        if host is None:
            print(f"No host found with email {host_email!r} -- nothing to do.")
            await engine.dispose()
            return

        properties = list((await db.scalars(select(Property).where(Property.user_id == host.id))).all())
        if not properties:
            print(f"Host {host_email!r} has no properties -- nothing to do.")
            await engine.dispose()
            return

        goa_properties = [p for p in properties if p.city and "goa" in p.city.lower()]
        override_properties = [
            p for p in properties if any(name.lower() in p.name.lower() for name in PARKING_OVERRIDE_PROPERTY_NAMES)
        ]
        override_ids = {p.id for p in override_properties}

        added = 0
        updated = 0

        # Breakfast: portfolio-wide (property_id=None matches every property
        # per faq_service.search_faq_entries' fallback), same answer for all.
        if await _upsert_faq_entry(
            db, host.id, None, "Is breakfast included?", BREAKFAST_ANSWER, "breakfast", status="verified"
        ):
            added += 1
        else:
            updated += 1

        # Pool / caretaker: per-property placeholders (opening hours and
        # staffing genuinely vary property to property -- see comment above).
        for prop in properties:
            for question, answer, category in [
                ("What are the pool timings? Is it temperature controlled?", POOL_PLACEHOLDER_ANSWER, "pool"),
                ("Is there a caretaker or on-site staff?", CARETAKER_PLACEHOLDER_ANSWER, "caretaker"),
            ]:
                if await _upsert_faq_entry(db, host.id, prop.id, question, answer, category, status="pending"):
                    added += 1
                else:
                    updated += 1

        # Parking: general Goa answer per Goa property (FaqEntry has no
        # city-level scoping concept, so this can't be a single global row --
        # see app/services/faq_service.py's property_id.is_(None) fallback,
        # which only means "applies to every property", not "applies to Goa
        # properties"). Mocha/Nook/Chic get an overriding, more specific
        # per-property row instead of the general Goa answer -- search_faq's
        # ordering (property_id-specific sorts first, see faq_service.py) means
        # this override is what the agent actually surfaces for those three.
        for prop in goa_properties:
            question = "Is there parking available?"
            answer = LIMITED_PARKING_ANSWER if prop.id in override_ids else GOA_PARKING_ANSWER
            if await _upsert_faq_entry(db, host.id, prop.id, question, answer, "parking", status="verified"):
                added += 1
            else:
                updated += 1

        await db.commit()

        print(f"Host: {host_email} ({len(properties)} properties, {len(goa_properties)} in Goa)")
        print(f"Parking override applied to: {[p.name for p in override_properties]}")
        print(f"FAQ entries added: {added}, updated: {updated}")
        print(
            "\nNOTE: pool and caretaker entries were seeded as 'pending' placeholders -- "
            "edit them with real per-property details from the FAQ dashboard, then verify."
        )

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
