"""The host and properties every eval scenario talks to.

A fixed, deliberately small set, kept apart from `seed_demo.py` so a change to the
demo account can never move a scenario's expectation. It is written to the eval
database only (see app/voice/eval_mode.py's guard), never to a database that holds
a real host.

The properties copy the shape of the real Spacez data that broke on 2026-11-13: the
neighbourhood is in the property NAME ("... in Indiranagar"), the city column holds only
"Bengaluru", and there is no state and no coordinates. Since Task 12 the area is also
stored in `locality` (what scripts/spacez_set_localities.py writes for the real data), and
search matches on that. If the way a locality is stored changes again, this is the one
file that changes with it.

`python -m eval.seed` creates the schema if needed and makes the host. The eval bot
calls `ensure_eval_host()` itself at start-up, so the seed is always current.
"""

import asyncio

from sqlalchemy import select, text

from app.database import AsyncSessionLocal, Base, engine
from app.models.property import Property
from app.models.user import User
from app.services.property.locality import set_locality

# Held while the schema and host are created: the suite starts several bots at once, and
# two of them creating the same tables or the same host would fail on a unique constraint.
_SEED_LOCK_KEY = 727_001

EVAL_HOST_EMAIL = "eval-host@example.invalid"
EVAL_HOST_NAME = "Eval Host"

# (name, city, locality, base_price, max_guests). Every locality a scenario asks for appears once;
# "Juhu" is deliberately absent, as is Mumbai, so scenario 2 has a real miss to hit.
EVAL_PROPERTIES = [
    ("3BHK Garden Loft in Indiranagar", "Bengaluru", "Indiranagar", 9500, 6),
    ("2BHK Courtyard Home in Koramangala", "Bengaluru", "Koramangala", 7800, 4),
    ("4BHK Pool Villa in Whitefield", "Bengaluru", "Whitefield", 14500, 10),
    ("1BHK Studio in HSR Layout", "Bengaluru", "HSR Layout", 4200, 2),
    ("3BHK Beach Villa in Assagao", "Goa", "Assagao", 16000, 8),
    ("2BHK Cottage in Saligao", "Goa", "Saligao", 8800, 4),
]


async def ensure_eval_host() -> User:
    """Create the schema, the eval host and its properties if missing. Idempotent and
    safe to call from several processes at once."""
    import app.models  # noqa: F401 -- registers every table on Base.metadata

    async with engine.connect() as lock_connection:
        await lock_connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": _SEED_LOCK_KEY})
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            return await _upsert_host_and_properties()
        finally:
            await lock_connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _SEED_LOCK_KEY})


async def _upsert_host_and_properties() -> User:
    async with AsyncSessionLocal() as db:
        host = await db.scalar(select(User).where(User.email == EVAL_HOST_EMAIL))
        if host is None:
            host = User(email=EVAL_HOST_EMAIL, name=EVAL_HOST_NAME, business_name="Eval Stays")
            db.add(host)
            await db.flush()
        existing = {p.name: p for p in (await db.scalars(select(Property).where(Property.user_id == host.id))).all()}
        for name, city, locality, price, guests in EVAL_PROPERTIES:
            prop = existing.get(name)
            if prop is None:
                prop = Property(user_id=host.id, name=name, city=city, base_price=price, max_guests=guests)
                db.add(prop)
            set_locality(prop, locality)  # also fixes a row seeded before localities existed
        await db.commit()
        await db.refresh(host)
        return host


if __name__ == "__main__":
    asyncio.run(ensure_eval_host())
    print(f"Eval host {EVAL_HOST_EMAIL} ready with {len(EVAL_PROPERTIES)} properties.")
