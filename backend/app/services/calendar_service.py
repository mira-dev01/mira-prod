import logging
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

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


AvailabilityStatus = Literal["full", "partial", "none"]


@dataclass(frozen=True)
class AvailabilityWindowResult:
    """Availability-first recommendations, Implementation 3: answers "which
    parts of THIS specific requested window are free", distinct from
    is_available's plain in/out boolean and from next_available_window's
    forward scan for the next open window OUTSIDE the requested range (see
    that function's own docstring -- this is not a replacement for it).

    status design decision (see Task 3.1's own writeup in
    docs/tasks/availability-first-recommendations.md for the full reasoning
    this codifies): "full" means the ENTIRE window has zero overlapping
    confirmed bookings -- no caveat needed, any dates within it work.
    "partial" means at least one real conflict exists in the window, but at
    least one nights-length free sub-range still remains somewhere in it --
    the property might still work, but conflicting_bookings must be spoken
    so the guest isn't misled into thinking it's a clean match. "none" means
    no nights-length free sub-range exists anywhere in the window at all.
    Deliberately NOT "full = at least one sub-range works, ignore the rest of
    the window" -- that reading would let a property with a real,
    guest-visible conflict render as an unqualified recommendation, which is
    the exact bug this task list exists to fix."""

    status: AvailabilityStatus
    conflicting_bookings: list[tuple[date, date]] = field(default_factory=list)


async def _conflicting_bookings_in_window(
    db: AsyncSession, property_ids: list[uuid.UUID], window_start: date, window_end: date
) -> dict[uuid.UUID, list[tuple[date, date]]]:
    """Shared query for partial_availability/partial_availability_for_candidates
    below -- one batched query regardless of candidate-set size (an "IN(...)
    query, not N round trips" discipline). Same overlap predicate as
    is_available above (status == "confirmed", existing.check_in <
    window_end AND existing.check_out > window_start) -- not a second,
    divergent definition of "booked". Returns EVERY overlapping booking per
    property, sorted by check_in, so the caller can compute free gaps within
    the window."""
    if not property_ids:
        return {}
    rows = (
        await db.execute(
            select(Booking.property_id, Booking.check_in, Booking.check_out).where(
                Booking.property_id.in_(property_ids),
                Booking.status == "confirmed",
                Booking.check_in < window_end,
                Booking.check_out > window_start,
            )
        )
    ).all()
    by_property: dict[uuid.UUID, list[tuple[date, date]]] = {pid: [] for pid in property_ids}
    for property_id, check_in, check_out in rows:
        by_property[property_id].append((check_in, check_out))
    for bookings in by_property.values():
        bookings.sort(key=lambda b: b[0])
    return by_property


def _classify_window(
    bookings: list[tuple[date, date]], window_start: date, window_end: date, nights: int
) -> AvailabilityWindowResult:
    """Pure function, no DB access -- given a property's bookings that
    overlap the window (already queried/sorted by the caller) plus the
    window bounds and required stay length, walks the gaps BETWEEN
    consecutive bookings (and before the first / after the last) looking for
    one at least `nights` days long. See AvailabilityWindowResult's own
    docstring for the status decision this implements."""
    if not bookings:
        return AvailabilityWindowResult(status="full", conflicting_bookings=[])

    has_sufficient_gap = False
    cursor = window_start
    for check_in, check_out in bookings:
        # check_in/check_out come from a query already filtered to bookings
        # overlapping the window, but a booking can still START before
        # window_start or END after window_end (partial overlap at either
        # edge) -- clamp BOTH ends to the window before measuring the gap,
        # not just gap_start, or a booking starting before window_start
        # produces a gap_end < gap_start (a negative-length "gap") that only
        # happens to compare correctly by coincidence of Python's date
        # subtraction, not by explicit design.
        gap_start = max(cursor, window_start)
        gap_end = max(min(check_in, window_end), gap_start)
        if (gap_end - gap_start).days >= nights:
            has_sufficient_gap = True
        cursor = max(cursor, check_out)
    # The gap after the last booking, up to the window's own end.
    gap_start = max(cursor, window_start)
    if (window_end - gap_start).days >= nights:
        has_sufficient_gap = True

    return AvailabilityWindowResult(
        status="partial" if has_sufficient_gap else "none",
        conflicting_bookings=[(b[0], b[1]) for b in bookings],
    )


async def partial_availability(
    db: AsyncSession, property_id: uuid.UUID, window_start: date, window_end: date, nights: int
) -> AvailabilityWindowResult:
    """Single-property primitive -- see AvailabilityWindowResult's own
    docstring for the status semantics. check_calendar's wrapper can call
    this directly to produce a spoken partial-availability line for one
    already-selected property; partial_availability_for_candidates below is
    the batched version orchestrator.recommend_properties actually uses for
    an entire candidate set in one query."""
    by_property = await _conflicting_bookings_in_window(db, [property_id], window_start, window_end)
    return _classify_window(by_property.get(property_id, []), window_start, window_end, nights)


async def partial_availability_for_candidates(
    db: AsyncSession, property_ids: list[uuid.UUID], window_start: date, window_end: date, nights: int
) -> dict[uuid.UUID, AvailabilityWindowResult]:
    """Batched version of partial_availability for a small candidate set --
    one query for the whole set, not N round trips. Every property_id in the
    input is present in the returned dict, defaulting to status="full" when
    it has no overlapping bookings at all."""
    by_property = await _conflicting_bookings_in_window(db, property_ids, window_start, window_end)
    return {
        property_id: _classify_window(bookings, window_start, window_end, nights)
        for property_id, bookings in by_property.items()
    }


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
