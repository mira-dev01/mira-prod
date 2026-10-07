"""Read-only lookup of a guest's confirmed bookings with one host, across both
places a booking can live:

- Lead rows the host marked status="booked" (dashboard Leads page), matched
  on the Lead's own guest_name/phone or its linked GuestProfile's name/phone.
- Booking rows imported from the host's calendars (iCal), matched on
  guest_name/guest_phone. These are often blank for Airbnb imports, so this
  source mostly adds matches for hosts whose calendars do carry guest details.

Every query is scoped to host_id: a guest never sees another host's bookings.
Nothing here creates or mutates rows (unlike
call_service.get_or_create_guest_profile), and every lookup fails open to
"no match", so a DB hiccup never raises into a live call.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Literal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking import Booking
from app.models.guest_profile import GuestProfile
from app.models.lead import Lead
from app.models.property import Property
from app.services import lead_service
from app.utils.dates import today_ist

logger = logging.getLogger(__name__)

MatchedBy = Literal["phone", "name"]

# Shorter names ("Al", "Jo") would substring-match far too many unrelated rows.
_MIN_NAME_LENGTH = 3


@dataclass(frozen=True)
class BookingMatch:
    property_name: str
    check_in: date | None
    check_out: date | None
    guest_name: str | None
    matched_by: MatchedBy

    @classmethod
    def from_lead(cls, lead: Lead, matched_by: MatchedBy = "phone") -> "BookingMatch":
        # A Lead carries no property FK -- properties_discussed is the only
        # property reference, and its last entry is the one most likely to be
        # what was actually booked (see lead_service.get_active_booking).
        return cls(
            property_name=lead.properties_discussed[-1] if lead.properties_discussed else "a property",
            check_in=lead.check_in,
            check_out=lead.check_out,
            guest_name=lead.guest_name,
            matched_by=matched_by,
        )

    def is_past(self, today: date) -> bool:
        return self.check_out is not None and self.check_out < today


def _phone_key(value: str) -> str:
    """Last 10 digits -- strips +91/0 prefixes and punctuation, the same
    normalization schemas/tool.py's _normalize_phone applies to spoken numbers."""
    return "".join(ch for ch in value if ch.isdigit())[-10:]


def _sql_phone_key(column):
    return func.right(func.regexp_replace(column, r"\D", "", "g"), 10)


def _name_pattern(name: str) -> str:
    escaped = name.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def _lead_matches(
    db: AsyncSession, host_id: uuid.UUID, phone_key: str | None, name_pattern: str | None
) -> list[tuple[Lead, MatchedBy]]:
    stmt = (
        select(Lead, GuestProfile)
        .outerjoin(GuestProfile, Lead.guest_profile_id == GuestProfile.id)
        .where(Lead.user_id == host_id, Lead.status == "booked")
    )
    conditions = []
    if phone_key:
        conditions += [_sql_phone_key(Lead.phone) == phone_key, _sql_phone_key(GuestProfile.phone) == phone_key]
    if name_pattern:
        conditions += [Lead.guest_name.ilike(name_pattern), GuestProfile.name.ilike(name_pattern)]
    rows = (await db.execute(stmt.where(or_(*conditions)))).all()

    matches = []
    for lead, profile in rows:
        phone_hit = phone_key is not None and phone_key in (
            _phone_key(lead.phone or ""),
            _phone_key(profile.phone or "") if profile else "",
        )
        matches.append((lead, "phone" if phone_hit else "name"))
    return matches


async def _calendar_matches(
    db: AsyncSession, host_id: uuid.UUID, phone_key: str | None, name_pattern: str | None
) -> list[BookingMatch]:
    conditions = []
    if phone_key:
        conditions.append(_sql_phone_key(Booking.guest_phone) == phone_key)
    if name_pattern:
        conditions.append(Booking.guest_name.ilike(name_pattern))
    stmt = (
        select(Booking, Property)
        .join(Property, Booking.property_id == Property.id)
        .where(Property.user_id == host_id, Booking.status == "confirmed", or_(*conditions))
    )
    rows = (await db.execute(stmt)).all()
    return [
        BookingMatch(
            property_name=property_.display_name or property_.name,
            check_in=booking.check_in,
            check_out=booking.check_out,
            guest_name=booking.guest_name,
            matched_by="phone" if phone_key and _phone_key(booking.guest_phone or "") == phone_key else "name",
        )
        for booking, property_ in rows
    ]


async def find_bookings(
    db: AsyncSession,
    host_id: uuid.UUID,
    *,
    phone: str | None = None,
    name: str | None = None,
    property_hint: str | None = None,
) -> list[BookingMatch]:
    """Every confirmed booking (upcoming, current, or past) matching phone
    and/or name, newest check-in first. property_hint narrows to bookings
    whose property name contains it (the guest said which stay they booked).
    Returns [] when neither identifier is usable or on any DB error."""
    phone_key = _phone_key(phone) if phone else None
    if phone_key is not None and len(phone_key) < 10:
        phone_key = None
    name_pattern = _name_pattern(name) if name and len(name.strip()) >= _MIN_NAME_LENGTH else None
    if phone_key is None and name_pattern is None:
        return []

    try:
        lead_rows = await _lead_matches(db, host_id, phone_key, name_pattern)
        calendar_rows = await _calendar_matches(db, host_id, phone_key, name_pattern)
    except Exception:
        logger.exception("Guest booking lookup failed for host_id=%s -- treating as no match", host_id)
        return []

    matches = [BookingMatch.from_lead(lead, matched_by) for lead, matched_by in lead_rows] + calendar_rows
    if property_hint:
        hint = property_hint.strip().lower()
        matches = [m for m in matches if hint in m.property_name.lower()]

    # The same stay can exist as both a booked Lead and an imported calendar
    # Booking -- keep one per (property, dates), preferring a phone match.
    unique: dict[tuple, BookingMatch] = {}
    for match in sorted(matches, key=lambda m: m.matched_by != "phone"):
        unique.setdefault((match.property_name.lower(), match.check_in, match.check_out), match)
    return sorted(unique.values(), key=lambda m: m.check_in or date.min, reverse=True)


async def find_active_booking(
    db: AsyncSession, host_id: uuid.UUID, guest_profile_id: uuid.UUID | None, caller_number: str | None
) -> BookingMatch | None:
    """The caller's current/upcoming booking, recognised from the number
    they're calling from -- feeds system_prompt's _active_booking_section.
    A host-marked booked Lead wins (it's the host's own record); otherwise
    falls back to an imported calendar Booking under the caller's number.
    Past stays are never returned here -- the prompt only ever surfaces the
    stay that matters right now."""
    lead = await lead_service.get_active_booking(db, guest_profile_id, host_id)
    if lead is not None:
        return BookingMatch.from_lead(lead)
    if not caller_number:
        return None

    today = today_ist()
    upcoming = [
        match
        for match in await find_bookings(db, host_id, phone=caller_number)
        if match.matched_by == "phone" and not match.is_past(today)
    ]
    # find_bookings sorts newest first; the stay that matters is the soonest.
    return upcoming[-1] if upcoming else None
