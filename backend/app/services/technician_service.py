"""Tier 1-adjacent technician lookup. Matches the best-rated technician for a
property+specialty and raises a notification. The full Tier 2 auto-dispatch
engine (real-time availability, ETA routing, job tracking) is not built
here.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.technician import Technician


async def find_technician(db: AsyncSession, property_id: uuid.UUID, specialty: str) -> Technician | None:
    candidates = (
        await db.scalars(
            select(Technician)
            .where(Technician.property_id == property_id, Technician.specialty == specialty)
            .order_by(Technician.rating.desc())
        )
    ).all()
    if candidates:
        return candidates[0]

    # Fall back to a "general" technician if no specialist is on file.
    general = (
        await db.scalars(
            select(Technician)
            .where(Technician.property_id == property_id, Technician.specialty == "general")
            .order_by(Technician.rating.desc())
        )
    ).all()
    return general[0] if general else None
