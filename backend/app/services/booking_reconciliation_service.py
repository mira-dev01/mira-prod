"""Booking reconciliation: links a calendar booking to the Mira conversation
behind it (if any), works out what Mira knows about its price, and records
the host's answers to the two questions only the host can settle cheaply:

  1. What was this booking's final price?        (Booking.final_booking_price)
  2. Was this booking actually a Mira booking?   (Booking.mira_attribution_status)

Principle: capture a missing business fact once, store it with its source
and status, and have every downstream reader (analytics, Calendar, the
"Needs confirmation" queue) use that stored value. Nothing here guesses a
price or claims a booking for Mira without evidence.

Attribution statuses
--------------------
  confirmed      -- the host said "yes, this was Mira" (Calendar "Mira match?"
                    Yes, the Needs-confirmation queue, or closing a Mira lead
                    into a calendar booking). The ONLY status that counts
                    toward Mira-attributed bookings/revenue.
  probable       -- strong matching evidence (below). Shown to the host for a
                    Yes/No; never counted as Mira impact.
  not_attributed -- the host said no, or no Mira conversation plausibly
                    matches this booking at all.
  unknown        -- bookings that predate this feature, plus weak matches
                    (e.g. same property, overlapping dates, nothing else).
                    No control is shown for these; there isn't enough to ask
                    about.

Evidence for "probable" (find_attribution_candidate)
----------------------------------------------------
A conversation's lead is scored against the booking using only persisted
data: Lead fields, CallSession rows linked by CallSession.lead_id, and
PriceEvent rows from those calls.

  same_property      the booking's property is in lead.properties_discussed,
                     a linked call was on that property, or a price was
                     quoted for it.
  exact_dates        lead dates, or a quoted price's dates, equal the
                     booking's check-in/check-out.
  overlapping_dates  dates overlap without matching exactly (weak).
  phone_match        full phone equals the booking's guest_phone.
  phone_last4_match  phone ends with the booking's last-4 digits (Airbnb).
  phone_mismatch     the booking carries an identity and this lead's phone
                     contradicts it. Disqualifies the candidate.
  hot_intent         lead_temperature is hot or very_hot.
  booking_intent     lead_temperature is very_hot.
  pricing_discussed  Mira quoted a price for this property on a linked call.
  negotiation        Mira negotiated a price for this property.
  recent             the latest linked conversation is within
                     RECENT_WINDOW of the booking being detected.

probable  =  same_property
             AND (exact_dates OR an identity match)
             AND (hot_intent OR pricing_discussed OR an identity match)
             AND no phone_mismatch
             within ATTRIBUTION_WINDOW of the booking being detected.

Same dates, or a conversation shortly before the booking, are never enough
by themselves.

Price detection (detect_prices)
-------------------------------
Uses only the matched candidate's PriceEvents for this property, for the
booking's exact dates (or, failing that, the same number of nights):
  initial_price    = earliest initial_quote (or the first negotiation's
                     asking price, if Mira never quoted separately)
  negotiated_price = latest counter_offer / accepted_offer
  final_booking_price is set automatically ONLY when the latest negotiation
  event for the booking's EXACT dates is an accepted_offer AND the booking's
  guest identity matches the conversation (phone or last-4). Anything less
  stays pending_confirmation, with the detected price offered to the host as
  a one-click option rather than re-typed.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, false, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking import Booking
from app.models.call_session import CallSession
from app.models.lead import Lead
from app.models.price_event import PriceEvent
from app.models.property import Property
from app.services.call_service import BROWSER_TEST_CALLER_NUMBER
from app.services.lead_temperature import HOT_OR_ABOVE
from app.utils.dates import today_ist

logger = logging.getLogger(__name__)

ATTRIBUTION_WINDOW = timedelta(days=30)
RECENT_WINDOW = timedelta(days=14)
# A booking older than this (by check-out) isn't put in front of the host for
# reconciliation. It still counts as unpriced in completeness metrics.
RECONCILIATION_LOOKBACK = timedelta(days=90)

PRICE_SOURCES = ("mira_conversation", "host_confirmed", "host_entered", "external_pms", "unknown")
PRICE_STATUSES = ("confirmed", "pending_confirmation", "unknown")
ATTRIBUTION_STATUSES = ("confirmed", "probable", "not_attributed", "unknown")
BOOKING_KINDS = ("reservation", "blocked")

_IDENTITY_SIGNALS = {"phone_match", "phone_last4_match"}


def classify_ical_kind(summary: str | None) -> str:
    """An iCal event's SUMMARY -> reservation | blocked. Airbnb exports
    "Reserved" for guest stays and "Airbnb (Not available)" for closed
    dates; other channels use "Blocked" / "CLOSED - Not available". Same
    patterns as the backfill in alembic revision e5b8d2f1a9c3."""
    text = (summary or "").strip().lower()
    if "not available" in text or "blocked" in text or text.startswith("closed"):
        return "blocked"
    return "reservation"


def _phone_key(value: str | None) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())[-10:]


@dataclass
class AttributionVerdict:
    status: str
    lead_id: uuid.UUID | None = None
    call_session_id: uuid.UUID | None = None
    signals: list[str] = field(default_factory=list)


@dataclass
class _Candidate:
    lead: Lead
    calls: list[CallSession]
    events: list[PriceEvent]
    signals: set[str]

    @property
    def latest_activity(self) -> datetime:
        stamps = [c.created_at for c in self.calls if c.created_at] + [self.lead.updated_at]
        return max(s for s in stamps if s is not None)

    @property
    def strength(self) -> int:
        return len(self.signals - {"overlapping_dates", "recent"})


@dataclass
class PriceDetection:
    initial_price: float | None = None
    negotiated_price: float | None = None
    confident_final_price: float | None = None


def _phones_for(candidate: _Candidate) -> list[str]:
    phones = [candidate.lead.phone] + [c.caller_number for c in candidate.calls]
    return [p for p in phones if p and p != BROWSER_TEST_CALLER_NUMBER]


def _score(candidate: _Candidate, booking: Booking, property_: Property, reference_time: datetime) -> set[str]:
    lead = candidate.lead
    signals: set[str] = set()
    property_events = [e for e in candidate.events if e.property_id == booking.property_id]

    if (
        property_.name in (lead.properties_discussed or [])
        or any(c.property_id == booking.property_id for c in candidate.calls)
        or property_events
    ):
        signals.add("same_property")

    exact = lead.check_in == booking.check_in and lead.check_out == booking.check_out
    exact = exact or any(e.check_in == booking.check_in and e.check_out == booking.check_out for e in property_events)
    if exact:
        signals.add("exact_dates")
    elif lead.check_in and lead.check_out and lead.check_in < booking.check_out and lead.check_out > booking.check_in:
        signals.add("overlapping_dates")

    phones = _phones_for(candidate)
    if booking.guest_phone and phones:
        if any(_phone_key(p) == _phone_key(booking.guest_phone) for p in phones):
            signals.add("phone_match")
        else:
            signals.add("phone_mismatch")
    elif booking.guest_phone_last4 and phones:
        if any(_phone_key(p).endswith(booking.guest_phone_last4) for p in phones):
            signals.add("phone_last4_match")
        else:
            signals.add("phone_mismatch")

    if lead.lead_temperature in HOT_OR_ABOVE:
        signals.add("hot_intent")
    if lead.lead_temperature == "very_hot":
        signals.add("booking_intent")
    if property_events:
        signals.add("pricing_discussed")
    if any(e.price_type in ("counter_offer", "accepted_offer") for e in property_events):
        signals.add("negotiation")
    if reference_time - candidate.latest_activity <= RECENT_WINDOW:
        signals.add("recent")
    return signals


def _is_probable(signals: set[str]) -> bool:
    if "phone_mismatch" in signals or "same_property" not in signals:
        return False
    has_identity = bool(signals & _IDENTITY_SIGNALS)
    if not ("exact_dates" in signals or has_identity):
        return False
    return "hot_intent" in signals or "pricing_discussed" in signals or has_identity


async def _load_candidates(
    db: AsyncSession, booking: Booking, host_id: uuid.UUID, reference_time: datetime
) -> list[_Candidate]:
    since = reference_time - ATTRIBUTION_WINDOW
    until = reference_time + timedelta(hours=1)

    calls = (
        await db.scalars(
            select(CallSession).where(
                CallSession.user_id == host_id,
                CallSession.lead_id.is_not(None),
                CallSession.created_at >= since,
                CallSession.created_at < until,
                or_(CallSession.caller_number.is_(None), CallSession.caller_number != BROWSER_TEST_CALLER_NUMBER),
            )
        )
    ).all()
    calls_by_lead: dict[uuid.UUID, list[CallSession]] = {}
    for call in calls:
        calls_by_lead.setdefault(call.lead_id, []).append(call)

    # Leads with recent activity but no in-window call (e.g. Busy Call
    # Recovery leads, which never get a CallSession) are still candidates.
    leads = (
        await db.scalars(
            select(Lead).where(
                Lead.user_id == host_id,
                or_(
                    Lead.id.in_(list(calls_by_lead)) if calls_by_lead else false(),
                    and_(Lead.updated_at >= since, Lead.created_at < until),
                ),
            )
        )
    ).all()

    call_ids = [c.id for c in calls]
    events_by_call: dict[uuid.UUID, list[PriceEvent]] = {}
    if call_ids:
        for event in (
            await db.scalars(select(PriceEvent).where(PriceEvent.call_session_id.in_(call_ids)))
        ).all():
            events_by_call.setdefault(event.call_session_id, []).append(event)

    candidates = []
    for lead in leads:
        lead_calls = calls_by_lead.get(lead.id, [])
        events = [e for c in lead_calls for e in events_by_call.get(c.id, [])]
        candidates.append(_Candidate(lead=lead, calls=lead_calls, events=events, signals=set()))
    return candidates


async def find_attribution_candidate(
    db: AsyncSession,
    booking: Booking,
    property_: Property,
    reference_time: datetime | None = None,
) -> tuple[AttributionVerdict, "_Candidate | None"]:
    """See the module docstring for the rule. reference_time is when the
    booking was detected (defaults to now; iCal sync detects a new Airbnb
    reservation within one sync interval of it being made)."""
    reference_time = reference_time or datetime.now(timezone.utc)
    candidates = await _load_candidates(db, booking, property_.user_id, reference_time)
    for candidate in candidates:
        candidate.signals = _score(candidate, booking, property_, reference_time)

    probable = [c for c in candidates if _is_probable(c.signals)]
    if probable:
        best = max(probable, key=lambda c: (c.strength, c.latest_activity))
        latest_call = max(best.calls, key=lambda c: c.created_at, default=None)
        return (
            AttributionVerdict(
                status="probable",
                lead_id=best.lead.id,
                call_session_id=latest_call.id if latest_call else best.lead.call_session_id,
                signals=sorted(best.signals),
            ),
            best,
        )

    related = [
        c
        for c in candidates
        if "phone_mismatch" not in c.signals and c.signals & {"same_property", "exact_dates", "overlapping_dates"}
    ]
    if related:
        best = max(related, key=lambda c: (c.strength, c.latest_activity))
        return AttributionVerdict(status="unknown", signals=sorted(best.signals)), None
    return AttributionVerdict(status="not_attributed", signals=["no_matching_conversation"]), None


def detect_prices(candidate: "_Candidate", booking: Booking) -> PriceDetection:
    nights = (booking.check_out - booking.check_in).days
    events = [e for e in candidate.events if e.property_id == booking.property_id]
    exact = [e for e in events if e.check_in == booking.check_in and e.check_out == booking.check_out]
    same_length = [e for e in events if e.check_in is None and e.nights == nights]
    relevant = sorted(exact or same_length, key=lambda e: e.created_at)
    if not relevant:
        return PriceDetection()

    quotes = [e for e in relevant if e.price_type == "initial_quote"]
    negotiations = [e for e in relevant if e.price_type in ("counter_offer", "accepted_offer")]
    initial = quotes[0].price if quotes else (negotiations[0].list_price if negotiations else None)
    negotiated = negotiations[-1].price if negotiations else None

    confident = None
    exact_negotiations = [e for e in sorted(exact, key=lambda e: e.created_at) if e in negotiations]
    if (
        exact_negotiations
        and exact_negotiations[-1].price_type == "accepted_offer"
        and candidate.signals & _IDENTITY_SIGNALS
    ):
        confident = exact_negotiations[-1].price

    def _f(value) -> float | None:
        return float(value) if value is not None else None

    return PriceDetection(initial_price=_f(initial), negotiated_price=_f(negotiated), confident_final_price=_f(confident))


async def _link_events(db: AsyncSession, booking: Booking, call_ids: list[uuid.UUID]) -> None:
    if not call_ids:
        return
    await db.execute(
        update(PriceEvent)
        .where(PriceEvent.call_session_id.in_(call_ids), PriceEvent.property_id == booking.property_id)
        .values(booking_id=booking.id)
    )


async def reconcile_booking(
    db: AsyncSession, booking: Booking, property_: Property, reference_time: datetime | None = None
) -> None:
    """Run on a newly detected booking (iCal sync or manual creation).
    Never commits; the caller owns the transaction. A host decision already
    on the booking is never overwritten."""
    if not booking.is_reservation:
        return
    if booking.price_status == "unknown" and booking.final_booking_price is None:
        booking.price_status = "pending_confirmation"
    if booking.mira_attribution_reviewed_at is not None:
        return

    verdict, candidate = await find_attribution_candidate(db, booking, property_, reference_time)
    booking.mira_attribution_status = verdict.status
    booking.mira_attribution_lead_id = verdict.lead_id
    booking.mira_attribution_call_session_id = verdict.call_session_id
    booking.mira_attribution_signals = verdict.signals
    if candidate is None:
        return

    await db.flush()
    await _link_events(db, booking, [c.id for c in candidate.calls])
    detection = detect_prices(candidate, booking)
    booking.initial_price = detection.initial_price
    booking.negotiated_price = detection.negotiated_price
    if detection.confident_final_price is not None and booking.price_status != "confirmed":
        _set_final_price(booking, detection.confident_final_price, source="mira_conversation", actor="mira")
        db.add(final_price_event(booking, property_.user_id, "mira_conversation"))


def _set_final_price(booking: Booking, amount: float, *, source: str, actor: str) -> None:
    now = datetime.now(timezone.utc)
    booking.final_booking_price = round(float(amount), 2)
    booking.price_source = source
    booking.price_status = "confirmed"
    booking.price_confirmed_at = now
    booking.price_confirmed_by = actor
    if actor == "host":
        booking.price_reviewed_at = now


def final_price_event(booking: Booking, user_id: uuid.UUID, source: str) -> PriceEvent:
    return PriceEvent(
        user_id=user_id,
        property_id=booking.property_id,
        call_session_id=booking.mira_attribution_call_session_id,
        booking_id=booking.id,
        price_type="final_price",
        source=source,
        price=booking.final_booking_price,
        currency=booking.currency,
        nights=(booking.check_out - booking.check_in).days,
        check_in=booking.check_in,
        check_out=booking.check_out,
    )


class ReconciliationError(ValueError):
    """Bad host input (e.g. a missing/non-positive amount) -- the API maps
    this to a 422."""


async def confirm_price(
    db: AsyncSession, booking: Booking, user_id: uuid.UUID, choice: str, amount: float | None
) -> Booking:
    """The host's answer to "what was the final price?".

    choice:
      mira_conversation  -- "Price confirmed during Mira conversation": the
                            detected price (or the host's correction of it).
                            Stored as price_source=host_confirmed.
      outside_mira       -- "Price was finalized outside Mira": the host types
                            it. price_source=host_entered.
      not_confirmed_yet  -- leave it pending. Recorded as reviewed so the
                            queue doesn't ask again; still counted as unpriced.
    """
    if choice == "not_confirmed_yet":
        booking.final_booking_price = None
        booking.price_source = "unknown"
        booking.price_status = "pending_confirmation"
        booking.price_confirmed_at = None
        booking.price_confirmed_by = None
        booking.price_reviewed_at = datetime.now(timezone.utc)
    else:
        if choice == "mira_conversation":
            value = amount if amount is not None else booking.detected_price
            source = "host_confirmed"
        elif choice == "outside_mira":
            value = amount
            source = "host_entered"
        else:
            raise ReconciliationError(f"Unknown choice {choice!r}")
        if value is None or value <= 0:
            raise ReconciliationError("Enter the final booking price")
        _set_final_price(booking, value, source=source, actor="host")
        db.add(final_price_event(booking, user_id, source))
    await db.commit()
    await db.refresh(booking)
    return booking


async def _apply_lead_evidence(
    db: AsyncSession, booking: Booking, lead: Lead, property_: Property, extra_signals: set[str] | None = None
) -> None:
    """Link `lead`'s conversations to the booking: score signals, link
    their PriceEvents for this property, and carry over the informational
    initial/negotiated prices. Never commits."""
    calls = list((await db.scalars(select(CallSession).where(CallSession.lead_id == lead.id))).all())
    events: list[PriceEvent] = []
    if calls:
        events = list(
            (await db.scalars(select(PriceEvent).where(PriceEvent.call_session_id.in_([c.id for c in calls])))).all()
        )
    candidate = _Candidate(lead=lead, calls=calls, events=events, signals=set())
    candidate.signals = _score(candidate, booking, property_, datetime.now(timezone.utc))
    booking.mira_attribution_lead_id = lead.id
    latest_call = max(calls, key=lambda c: c.created_at, default=None)
    booking.mira_attribution_call_session_id = latest_call.id if latest_call else lead.call_session_id
    booking.mira_attribution_signals = sorted(candidate.signals | (extra_signals or set()))
    await db.flush()
    await _link_events(db, booking, [c.id for c in calls])
    detection = detect_prices(candidate, booking)
    booking.initial_price = detection.initial_price
    booking.negotiated_price = detection.negotiated_price


async def decide_attribution(
    db: AsyncSession, booking: Booking, property_: Property, is_mira_match: bool
) -> Booking:
    """The host's Yes/No on "Mira match?". Only offered when the booking has
    a matched conversation (mira_attribution_lead_id), and the host may
    change their answer later.

    Yes -> confirmed. Lead.status is deliberately NOT changed: it's the
    host's own pipeline field, and a Yes that later becomes a No couldn't
    restore it. Analytics already treats a lead with a confirmed booking as
    converted (analytics_service._load_conversations), so nothing is lost.
    No  -> not_attributed. Anything reconciliation pulled from that
    conversation is withdrawn: its linked mira_conversation PriceEvents, the
    informational initial/negotiated prices and an auto-confirmed (actor
    "mira") final price, which goes back to pending_confirmation. A price
    the host entered or confirmed is the host's own fact and is kept. The
    matched lead id is kept, so a later Yes can restore the link.
    """
    if booking.mira_attribution_lead_id is None:
        raise ReconciliationError("This booking has no matching Mira conversation to confirm")
    booking.mira_attribution_reviewed_at = datetime.now(timezone.utc)
    lead = await db.get(Lead, booking.mira_attribution_lead_id)
    if is_mira_match:
        booking.mira_attribution_status = "confirmed"
        if lead is not None:
            await _apply_lead_evidence(db, booking, lead, property_)
    else:
        booking.mira_attribution_status = "not_attributed"
        await db.execute(
            update(PriceEvent)
            .where(PriceEvent.booking_id == booking.id, PriceEvent.source == "mira_conversation")
            .values(booking_id=None)
        )
        booking.initial_price = None
        booking.negotiated_price = None
        if booking.price_confirmed_by == "mira":
            booking.final_booking_price = None
            booking.price_source = "unknown"
            booking.price_status = "pending_confirmation"
            booking.price_confirmed_at = None
            booking.price_confirmed_by = None
    await db.commit()
    await db.refresh(booking)
    return booking


async def attach_host_lead(db: AsyncSession, booking: Booking, lead: Lead, property_: Property) -> None:
    """A booking the host created FROM a Mira lead ("Confirm booking" when
    closing a lead). That is the host asserting the attribution, so it's
    confirmed directly, and prices from that lead's own conversations on
    this property are carried over. Never commits."""
    booking.mira_attribution_status = "confirmed"
    booking.mira_attribution_reviewed_at = datetime.now(timezone.utc)
    await _apply_lead_evidence(db, booking, lead, property_, extra_signals={"host_closed_lead"})
    if booking.final_booking_price is None and booking.price_status == "unknown":
        booking.price_status = "pending_confirmation"


async def reconciliation_queue(
    db: AsyncSession, property_ids: list[uuid.UUID], kind: str | None = None
) -> list[Booking]:
    """Every booking needing a host answer, most recent stay first. kind
    narrows to one question: "price" (final price missing, not yet answered)
    or "attribution" (probable Mira match awaiting Yes/No). Only bookings checking out within RECONCILIATION_LOOKBACK
    of today (or later) are asked about."""
    if not property_ids:
        return []
    cutoff = today_ist() - RECONCILIATION_LOOKBACK
    needs_price = and_(Booking.final_booking_price.is_(None), Booking.price_reviewed_at.is_(None))
    needs_attribution = and_(
        Booking.mira_attribution_status == "probable", Booking.mira_attribution_reviewed_at.is_(None)
    )
    condition = {"price": needs_price, "attribution": needs_attribution}.get(kind, or_(needs_price, needs_attribution))
    stmt = select(Booking).where(
        Booking.property_id.in_(property_ids),
        Booking.kind == "reservation",
        Booking.status == "confirmed",
        Booking.check_out >= cutoff,
        condition,
    )
    return list((await db.scalars(stmt.order_by(Booking.check_in.desc()))).all())
