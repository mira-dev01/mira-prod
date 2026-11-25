"""Seeds the sample host behind the marketing site's public "test the agent" call.

The host and everything on it is invented: a fictional stay group with a villa, a cottage and a hostel, so a visitor
can ask about availability, prices, rules and nearby places and get real answers without touching any real host's data.
The account has no password and no Clerk identity, so nobody can sign in to it.

Idempotent: running it again changes nothing that already exists.

Run:  DEMO_HOST_EMAIL=demo-host@example.com python -m scripts.seed_landing_demo_host
(or set DEMO_HOST_EMAIL in .env, the same variable the API reads). The email does not need to be a real inbox.
"""

import asyncio
from datetime import date, timedelta

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.booking import Booking
from app.models.property import Property
from app.models.user import User

BUSINESS = "Palm Court Stays"

PROPERTIES = [
    {
        "name": "Palm Court Villa",
        "property_type": "villa",
        "city": "Goa",
        "locality": "Assagao",
        "bedroom_count": 4,
        "max_guests": 10,
        "base_price": 14000,
        "minimum_nights": 2,
        "usp": "A private four-bedroom villa with a pool, ten minutes from the beaches.",
        "house_rules": "No parties or loud music after 10 PM. No smoking indoors. Pets are welcome with notice.",
        "neighborhood_info": "Quiet lane in Assagao, a short drive from Vagator beach and the cafes on Anjuna road.",
        "landmarks": ["Vagator beach, 10 minutes", "Anjuna flea market, 15 minutes", "Dabolim airport, 55 minutes"],
        "amenities": ["Private pool", "WiFi", "Air conditioning", "Fully equipped kitchen", "Free parking", "Caretaker on site"],
        "faq": [
            {"question": "Is breakfast included?", "answer": "A home-cooked breakfast can be arranged by the caretaker for an extra charge."},
            {"question": "Is the pool heated?", "answer": "No, the pool is not heated. It is open from 7 AM to 9 PM."},
            {"question": "Can we bring a pet?", "answer": "Small and medium pets are welcome if you tell us before you arrive."},
        ],
        "booked": [(5, 9), (21, 26)],
    },
    {
        "name": "Lotus Hill Cottage",
        "property_type": "cottage",
        "city": "Coorg",
        "locality": "Madikeri",
        "bedroom_count": 2,
        "max_guests": 5,
        "base_price": 7500,
        "minimum_nights": 1,
        "usp": "A two-bedroom hill cottage with a coffee-estate view and a wood-fired bonfire pit.",
        "house_rules": "Quiet hours from 10 PM. Bonfire is available on request until 11 PM.",
        "neighborhood_info": "On a coffee estate road above Madikeri town, with walking trails starting at the gate.",
        "landmarks": ["Abbey Falls, 20 minutes", "Raja's Seat, 15 minutes", "Madikeri town, 10 minutes"],
        "amenities": ["WiFi", "Bonfire pit", "Kitchen", "Free parking", "Hot water", "Estate walks"],
        "faq": [
            {"question": "Can you arrange a bonfire?", "answer": "Yes, the caretaker can set up a bonfire in the evening if you ask earlier in the day."},
            {"question": "Is there a kitchen?", "answer": "Yes. Guests can cook, or the caretaker can arrange local meals."},
        ],
        "booked": [(3, 6), (14, 18)],
    },
    {
        "name": "Pine Ridge Hostel",
        "property_type": "hostel",
        "city": "Rishikesh",
        "locality": "Tapovan",
        "bedroom_count": 6,
        "max_guests": 24,
        "base_price": 900,
        "minimum_nights": 1,
        "usp": "A social hostel with mixed and female dorms, private rooms and a rooftop with river views.",
        "house_rules": "Check-in after 1 PM. Quiet hours from 10 PM. No outside alcohol in the dorms.",
        "neighborhood_info": "In Tapovan, a short walk from the cafes, yoga studios and the Ganga ghats.",
        "landmarks": ["Laxman Jhula, 10 minutes' walk", "Ganga ghats, 8 minutes' walk", "Dehradun airport, 45 minutes"],
        "amenities": ["WiFi", "Lockers in every dorm", "Common kitchen", "Rooftop lounge", "Hot water", "Luggage storage"],
        "faq": [
            {"question": "Are there lockers?", "answer": "Yes, every bed has a locker. Bring your own padlock or buy one at reception."},
            {"question": "Do you have female-only dorms?", "answer": "Yes, there is a female-only dorm and a mixed dorm, plus private rooms."},
        ],
        "booked": [(2, 4), (10, 12)],
    },
]


async def seed() -> None:
    email = settings.demo_host_email.strip()
    if not email:
        raise SystemExit("Set DEMO_HOST_EMAIL first (any address; it does not need to be a real inbox).")

    async with AsyncSessionLocal() as db:
        host = await db.scalar(select(User).where(User.email == email))
        if host is None:
            host = User(email=email, name=BUSINESS, business_name=BUSINESS, tier="tier_1", agent_voice_gender="female")
            db.add(host)
            await db.commit()
            await db.refresh(host)
            print(f"Created the demo host {email}")
        else:
            print(f"Demo host {email} already exists")

        today = date.today()
        for index, spec in enumerate(PROPERTIES, start=1):
            existing = await db.scalar(select(Property).where(Property.user_id == host.id, Property.name == spec["name"]))
            if existing is not None:
                print(f"  {spec['name']}: already there")
                continue
            fields = {k: v for k, v in spec.items() if k != "booked"}
            property_ = Property(
                user_id=host.id,
                exophone=f"+918000900{index:03d}",  # placeholder: never dialled, only unique
                check_in_time="14:00",
                check_out_time="11:00",
                **fields,
            )
            db.add(property_)
            await db.commit()
            await db.refresh(property_)
            for start, end in spec["booked"]:
                db.add(
                    Booking(
                        property_id=property_.id,
                        check_in=today + timedelta(days=start),
                        check_out=today + timedelta(days=end),
                        platform="manual",
                        guest_name="Sample booking",
                        status="confirmed",
                    )
                )
            await db.commit()
            print(f"  {spec['name']}: created with {len(spec['booked'])} sample bookings")


if __name__ == "__main__":
    asyncio.run(seed())
