"""Seeds a demo host account with the real 14-room "Pause Projects Goa"
dataset originally prototyped in ../Mira CRM/demo_calendar.py, so the new
backend/dashboard have realistic data to demo against from minute one.

Blocked date ranges are re-anchored relative to *today* (the prototype's
dates were hardcoded to June 2025 and would all be in the past by now)
rather than copied verbatim, preserving the original relative spacing.

Run: python -m scripts.seed_demo_data
"""

import asyncio
from datetime import date, timedelta

from sqlalchemy import select

from app.auth.security import hash_password
from app.database import AsyncSessionLocal
from app.models.booking import Booking
from app.models.notification import Notification
from app.models.property import Property
from app.models.technician import Technician
from app.models.user import User

DEMO_EMAIL = "demo@pauseprojectsgoa.com"
DEMO_PASSWORD = "demo12345"

# name -> avg nightly rate (INR), from the working prototype.
PROPERTIES = {
    "Terra": 4300, "Whyt": 5150, "Nile": 3900, "Olive": 4100, "Blush": 4250,
    "Limón": 4500, "Coral": 5650, "Amber": 6900, "Pine": 6150, "2BHK": 5650,
    "Hazel": 6700, "Mocha": 4565, "Nook": 4900, "Splash": 6400,
}

# Original blocked windows, day-offsets from the prototype's anchor date
# (2025-06-01), re-applied relative to today below.
_ANCHOR = date(2025, 6, 1)
_RAW_BLOCKED = {
    "Terra": [(date(2025, 6, 1), date(2025, 6, 4)), (date(2025, 6, 18), date(2025, 6, 22)), (date(2025, 7, 5), date(2025, 7, 10))],
    "Whyt": [(date(2025, 6, 3), date(2025, 6, 7)), (date(2025, 6, 25), date(2025, 6, 30)), (date(2025, 7, 12), date(2025, 7, 18))],
    "Nile": [(date(2025, 6, 8), date(2025, 6, 12)), (date(2025, 6, 20), date(2025, 6, 23)), (date(2025, 7, 1), date(2025, 7, 6))],
    "Olive": [(date(2025, 6, 1), date(2025, 6, 6)), (date(2025, 6, 22), date(2025, 6, 28)), (date(2025, 7, 15), date(2025, 7, 20))],
    "Blush": [(date(2025, 6, 5), date(2025, 6, 9)), (date(2025, 7, 3), date(2025, 7, 8))],
    "Limón": [(date(2025, 6, 10), date(2025, 6, 15)), (date(2025, 6, 28), date(2025, 7, 2))],
    "Coral": [(date(2025, 6, 2), date(2025, 6, 6)), (date(2025, 6, 19), date(2025, 6, 24)), (date(2025, 7, 8), date(2025, 7, 13))],
    "Amber": [(date(2025, 6, 11), date(2025, 6, 14)), (date(2025, 6, 29), date(2025, 7, 3)), (date(2025, 7, 20), date(2025, 7, 25))],
    "Pine": [(date(2025, 6, 4), date(2025, 6, 8)), (date(2025, 6, 21), date(2025, 6, 26)), (date(2025, 7, 10), date(2025, 7, 16))],
    "2BHK": [(date(2025, 6, 7), date(2025, 6, 12)), (date(2025, 6, 25), date(2025, 6, 29)), (date(2025, 7, 4), date(2025, 7, 9))],
    "Hazel": [(date(2025, 6, 1), date(2025, 6, 5)), (date(2025, 6, 17), date(2025, 6, 21)), (date(2025, 7, 7), date(2025, 7, 12))],
    "Mocha": [(date(2025, 6, 6), date(2025, 6, 11)), (date(2025, 6, 23), date(2025, 6, 27)), (date(2025, 7, 14), date(2025, 7, 19))],
    "Nook": [(date(2025, 6, 3), date(2025, 6, 7)), (date(2025, 6, 20), date(2025, 6, 25)), (date(2025, 7, 2), date(2025, 7, 7))],
    "Splash": [(date(2025, 6, 9), date(2025, 6, 13)), (date(2025, 6, 27), date(2025, 7, 1)), (date(2025, 7, 17), date(2025, 7, 22))],
}


def _reanchor(today: date) -> dict[str, list[tuple[date, date]]]:
    return {
        name: [(today + (start - _ANCHOR), today + (end - _ANCHOR)) for start, end in windows]
        for name, windows in _RAW_BLOCKED.items()
    }


SAMPLE_FAQ = [
    {"question": "Is there free parking?", "answer": "Yes, free private parking is available on-site."},
    {"question": "Do you allow pets?", "answer": "Pets are welcome with prior notice to the host."},
    {"question": "Is breakfast included?", "answer": "Breakfast is not included but can be arranged for an extra fee."},
]
SAMPLE_AMENITIES = ["WiFi", "AC", "Kitchen", "Free parking", "Pool access", "Hot water"]
SAMPLE_HOUSE_RULES = "No smoking indoors. No loud music after 11 PM. Check-in is self-service with a smart lock code sent by WhatsApp."


async def get_or_create_demo_user(db) -> User:
    user = await db.scalar(select(User).where(User.email == DEMO_EMAIL))
    if user is not None:
        return user
    user = User(email=DEMO_EMAIL, hashed_password=hash_password(DEMO_PASSWORD), name="Pause Projects Goa", tier="tier_1")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def get_or_create_property(db, user: User, index: int, name: str, base_price: int) -> Property:
    existing = await db.scalar(select(Property).where(Property.user_id == user.id, Property.name == name))
    if existing is not None:
        return existing

    property_ = Property(
        user_id=user.id,
        name=name,
        city="Goa",
        exophone=f"+9180000{index:05d}",  # placeholder DID -- replace with the real ExoPhone once provisioned
        base_price=base_price,
        house_rules=SAMPLE_HOUSE_RULES,
        faq=SAMPLE_FAQ,
        amenities=SAMPLE_AMENITIES,
        check_in_time="14:00",
        check_out_time="11:00",
        max_guests=4,
    )
    db.add(property_)
    await db.commit()
    await db.refresh(property_)
    return property_


async def seed_bookings(db, property_: Property, windows: list[tuple[date, date]]) -> None:
    existing = await db.scalar(select(Booking).where(Booking.property_id == property_.id))
    if existing is not None:
        return
    for check_in, check_out in windows:
        db.add(
            Booking(
                property_id=property_.id,
                check_in=check_in,
                check_out=check_out,
                platform="airbnb",
                guest_name="Seeded demo booking",
                status="confirmed",
            )
        )
    await db.commit()


async def seed_technicians(db, property_: Property) -> None:
    existing = await db.scalar(select(Technician).where(Technician.property_id == property_.id))
    if existing is not None:
        return
    db.add_all(
        [
            Technician(property_id=property_.id, name="Ramesh (General Handyman)", specialty="general", phone="+919800000001", rating=4.7),
            Technician(property_id=property_.id, name="Suresh (Electrician/AC)", specialty="electrical", phone="+919800000002", rating=4.5),
            Technician(property_id=property_.id, name="Goa Wifi Support", specialty="wifi", phone="+919800000003", rating=4.8),
        ]
    )
    await db.commit()


async def seed_sample_notifications(db, properties: list[Property]) -> None:
    existing = await db.scalar(select(Notification))
    if existing is not None:
        return
    sample = properties[0]
    db.add_all(
        [
            Notification(property_id=sample.id, channel="escalation", urgency="high", message=f"Escalation for {sample.name}: AC not cooling, guest is uncomfortable.", status="new"),
            Notification(property_id=sample.id, channel="whatsapp", urgency="low", message="To +919900000099: Your check-in code is 4471#.", status="new"),
        ]
    )
    await db.commit()


async def main() -> None:
    today = date.today()
    reanchored = _reanchor(today)

    async with AsyncSessionLocal() as db:
        user = await get_or_create_demo_user(db)
        print(f"Demo host: {DEMO_EMAIL} / {DEMO_PASSWORD}")

        properties = []
        for index, (name, base_price) in enumerate(PROPERTIES.items(), start=1):
            property_ = await get_or_create_property(db, user, index, name, base_price)
            await seed_bookings(db, property_, reanchored[name])
            await seed_technicians(db, property_)
            properties.append(property_)
            print(f"  seeded property: {property_.name} (₹{base_price}/night, {property_.exophone})")

        await seed_sample_notifications(db, properties)
        print(f"Seeded {len(properties)} properties.")


if __name__ == "__main__":
    asyncio.run(main())
