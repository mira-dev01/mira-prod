import logging
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.ical_client import fetch_ical
from app.models.booking import Booking
from app.models.property import Property

logger = logging.getLogger(__name__)


async def is_available(db: AsyncSession, property_id: uuid.UUID, check_in: date, check_out: date) -> bool:
    """Two ranges [check_in, check_out) overlap iff existing.check_in < new.check_out
    AND existing.check_out > new.check_in."""
    overlapping = await db.scalar(
        select(Booking).where(
            Booking.property_id == property_id,
            Booking.status == "confirmed",
            Booking.check_in < check_out,
            Booking.check_out > check_in,
        )
    )
    return overlapping is None


async def next_available_window(
    db: AsyncSession, property_id: uuid.UUID, from_date: date, nights: int, horizon_days: int = 90
) -> tuple[date, date] | None:
    """Scan forward day-by-day for the next open window of the requested length."""
    from datetime import timedelta

    candidate = from_date
    for _ in range(horizon_days):
        candidate_checkout = candidate + timedelta(days=nights)
        if await is_available(db, property_id, candidate, candidate_checkout):
            return candidate, candidate_checkout
        candidate += timedelta(days=1)
    return None


async def sync_property_ical(db: AsyncSession, property_: Property) -> int:
    """Fetch the property's iCal feed and upsert events into bookings,
    keyed by iCal UID for idempotency. Returns count of events processed."""
    if not property_.ical_url:
        return 0

    events = await fetch_ical(property_.ical_url)
    current_uids = {event.uid for event in events}

    existing_by_uid = {
        b.source_uid: b
        for b in (
            await db.scalars(
                select(Booking).where(Booking.property_id == property_.id, Booking.source_uid.isnot(None))
            )
        ).all()
    }

    for event in events:
        existing = existing_by_uid.get(event.uid)
        if existing:
            existing.check_in = event.check_in
            existing.check_out = event.check_out
            existing.status = "confirmed"
        else:
            db.add(
                Booking(
                    property_id=property_.id,
                    check_in=event.check_in,
                    check_out=event.check_out,
                    platform="airbnb",
                    source_uid=event.uid,
                    status="confirmed",
                    guest_name=event.summary,
                )
            )

    # Remove bookings whose iCal event no longer appears in the feed -- e.g.
    # the guest cancelled on Airbnb. Without this, a cancelled booking's
    # dates stayed permanently blocked in Mira even after Airbnb released
    # them, since this loop only ever upserted, never reconciled removals.
    # Confirmed live: dates Airbnb showed as open were still blocked here.
    for source_uid, existing in existing_by_uid.items():
        if source_uid not in current_uids:
            await db.delete(existing)

    await db.commit()
    return len(events)


async def sync_all_properties(db: AsyncSession) -> dict[str, int]:
    properties = (await db.scalars(select(Property).where(Property.ical_url.isnot(None)))).all()
    results: dict[str, int] = {}
    for property_ in properties:
        try:
            results[str(property_.id)] = await sync_property_ical(db, property_)
        except Exception as exc:  # noqa: BLE001 - one property's feed failing shouldn't kill the sync loop
            results[str(property_.id)] = -1
            logger.warning("iCal sync failed for property %s: %s", property_.id, exc)
    return results
