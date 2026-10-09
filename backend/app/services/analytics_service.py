"""Aggregations behind the host Analytics page (GET /analytics/dashboard)
and the Busy Call Recovery KPIs (GET /analytics/recovery).

Read-only. Nothing here writes, and nothing sits on the live call path.

Every financial metric reads Booking.final_booking_price and nothing else
(never initial_price / negotiated_price, never an estimate). A booking
without a confirmed final price is reported as incomplete data, through
each metric's `completeness`, rather than being valued at anything.
"""

import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking import Booking
from app.models.call_session import CallSession
from app.models.lead import Lead
from app.models.notification import Notification
from app.models.price_event import PriceEvent
from app.models.property import Property
from app.models.user import User
from app.schemas.call_classification import QUALIFIED_CALL_TYPES
from app.services.call_ownership import CallOwner, InvalidCallOwnershipConfigError, resolve_effective_call_owner
from app.services.call_service import BROWSER_TEST_CALLER_NUMBER
from app.services.lead_temperature import HOT_OR_ABOVE, WARM_OR_ABOVE
from app.services.recovery_service import NOTIFICATION_CHANNEL_BUSY_RECOVERY
from app.services.whatsapp_reply_service import NOTIFICATION_CHANNEL_BUSY_RECOVERY_REPLY
from app.utils.dates import IST, today_ist


async def recovery_metrics(db: AsyncSession, user_id: uuid.UUID, since: datetime, until: datetime | None) -> dict:
    """Busy Call Recovery counts for one host and window. Moved verbatim
    from analytics.py's /analytics/recovery handler (whose docstring
    explains each query) so the Analytics page's Mira Impact section reuses
    the same numbers instead of a second copy of the queries."""
    window_filters = [Notification.created_at >= since]
    if until is not None:
        window_filters.append(Notification.created_at < until)

    # Busy Calls: one row per rejected call attempt (recovery_service.py
    # creates a fresh Notification every time, unlike Lead, which reuses an
    # existing open/contacted lead across repeat attempts from the same
    # guest -- see recovery_service.py's own docstring). Counting Lead rows
    # instead would undercount repeat-caller volume.
    busy_calls = await db.scalar(
        select(func.count())
        .select_from(Notification)
        .join(Lead, Notification.lead_id == Lead.id)
        .where(
            Notification.channel == NOTIFICATION_CHANNEL_BUSY_RECOVERY,
            Lead.user_id == user_id,
            *window_filters,
        )
    )

    # Recovered: the guest engaged back on WhatsApp at least once. Counted
    # per distinct Lead (not per reply row) -- a guest who replies twice to
    # the same busy-rejection thread is one recovered guest, not two.
    recovered = await db.scalar(
        select(func.count(func.distinct(Notification.lead_id)))
        .select_from(Notification)
        .join(Lead, Notification.lead_id == Lead.id)
        .where(
            Notification.channel == NOTIFICATION_CHANNEL_BUSY_RECOVERY_REPLY,
            Lead.user_id == user_id,
            *window_filters,
        )
    )

    # Converted / Lost: Lead.status is the one and only sales-pipeline
    # signal in this codebase (host-set via PATCH /leads/{id}, see
    # docs/api.md's leads.py entry) -- "booked"/"closed" on a recovery lead
    # (recovery_reason IS NOT NULL) is exactly what these mean, same
    # convention analytics_summary's open_leads/pipeline_value already use
    # for the general Lead funnel, filtered additionally to recovery leads.
    # Windowed by Lead.created_at (when the recovery lead was born), not
    # Notification.created_at, since a lead can convert well after the
    # window that produced the original busy_recovery notification.
    lead_window_filters = [Lead.created_at >= since]
    if until is not None:
        lead_window_filters.append(Lead.created_at < until)
    converted = await db.scalar(
        select(func.count()).where(
            Lead.user_id == user_id,
            Lead.recovery_reason.is_not(None),
            Lead.status == "booked",
            *lead_window_filters,
        )
    )
    lost = await db.scalar(
        select(func.count()).where(
            Lead.user_id == user_id,
            Lead.recovery_reason.is_not(None),
            Lead.status == "closed",
            *lead_window_filters,
        )
    )

    # Average Recovery Time: time from the busy-rejection notification to
    # this guest's FIRST reply, per lead, then averaged across leads.
    # func.min() on each side collapses repeat busy-rejections/repeat
    # replies for the same lead down to first-attempt -> first-reply, which
    # is the "how long until the guest re-engaged" question this KPI asks --
    # not every possible attempt/reply pairing.
    busy_first = (
        select(Notification.lead_id, func.min(Notification.created_at).label("busy_at"))
        .where(Notification.channel == NOTIFICATION_CHANNEL_BUSY_RECOVERY, Notification.lead_id.is_not(None))
        .group_by(Notification.lead_id)
        .subquery()
    )
    reply_first = (
        select(Notification.lead_id, func.min(Notification.created_at).label("reply_at"))
        .where(Notification.channel == NOTIFICATION_CHANNEL_BUSY_RECOVERY_REPLY, Notification.lead_id.is_not(None))
        .group_by(Notification.lead_id)
        .subquery()
    )
    avg_recovery_seconds = await db.scalar(
        select(func.avg(func.extract("epoch", reply_first.c.reply_at - busy_first.c.busy_at)))
        .select_from(busy_first)
        .join(reply_first, reply_first.c.lead_id == busy_first.c.lead_id)
        .join(Lead, Lead.id == busy_first.c.lead_id)
        .where(Lead.user_id == user_id, busy_first.c.busy_at >= since)
    )

    # Average Host Response: time from the busy_recovery notification being
    # created to the host first marking it read (Notification.responded_at,
    # set once by notification_service.mark_read -- see that field's own
    # comment on why updated_at isn't reused for this).
    avg_response_seconds = await db.scalar(
        select(func.avg(func.extract("epoch", Notification.responded_at - Notification.created_at)))
        .select_from(Notification)
        .join(Lead, Notification.lead_id == Lead.id)
        .where(
            Notification.channel == NOTIFICATION_CHANNEL_BUSY_RECOVERY,
            Notification.responded_at.is_not(None),
            Lead.user_id == user_id,
            *window_filters,
        )
    )

    return {
        "busy_calls": busy_calls or 0,
        "recovered": recovered or 0,
        "converted": converted or 0,
        "lost": lost or 0,
        "avg_recovery_seconds": avg_recovery_seconds,
        "avg_response_seconds": avg_response_seconds,
    }


async def call_activity(
    db: AsyncSession,
    user_id: uuid.UUID,
    property_ids: list[uuid.UUID],
    since: datetime,
    until: datetime | None,
    *,
    include_test_calls: bool,
) -> dict:
    """Call counts for one host and window -- moved verbatim from
    analytics.py's /analytics/summary handler so the Overview snapshot and
    the summary endpoint share one definition. total/completed/qualified
    are CallSession rows created in the window (browser-test calls excluded
    unless include_test_calls); escalated counts channel="escalation"
    Notifications on the host's properties (see the comment below)."""
    call_filters = [CallSession.user_id == user_id, CallSession.created_at >= since]
    if until is not None:
        call_filters.append(CallSession.created_at < until)
    if not include_test_calls:
        call_filters.append(CallSession.caller_number != BROWSER_TEST_CALLER_NUMBER)

    base = select(CallSession).where(*call_filters)

    total_calls = await db.scalar(select(func.count()).select_from(base.subquery()))
    completed_calls = await db.scalar(
        select(func.count()).select_from(base.where(CallSession.status == "completed").subquery())
    )
    # "Qualified" (BOOKING_LEAD/GUEST_SUPPORT/EXISTING_BOOKING/GENERAL_QUERY)
    # is never itself a stored call_type value -- see schemas/
    # call_classification.py -- just this derived grouping, computed here
    # the same way escalated_calls is derived from Notification.channel
    # rather than a stored boolean.
    qualified_calls = await db.scalar(
        select(func.count()).select_from(base.where(CallSession.call_type.in_(QUALIFIED_CALL_TYPES)).subquery())
    )
    # CallSession.urgency is never written anywhere in the app (escalations
    # are recorded as Notification rows, not on the CallSession itself) --
    # counting it here always returned 0, contradicting the Live Requests
    # panel on the same Overview page, which is populated from Notification
    # rows with channel="escalation". Count that instead, so this card
    # matches what the host actually sees in Live Requests.
    #
    # NOTE: like Live Requests itself, this is scoped by
    # property_id IN owned_property_ids, so a Lead Agent escalation
    # (property_id=NULL, portfolio-wide calls) won't be counted here either
    # -- pre-existing gap in Live Requests' own query, not introduced by this
    # fix. Tracked as a follow-up, not fixed here to keep this change scoped.
    escalated_filters = [
        Notification.property_id.in_(property_ids),
        Notification.channel == "escalation",
        Notification.created_at >= since,
    ]
    if not include_test_calls:
        # Same browser-test exclusion as the call counts above, so the
        # escalation count never includes a test call the totals leave out.
        escalated_filters.append(
            or_(
                Notification.call_session_id.is_(None),
                Notification.call_session_id.not_in(
                    select(CallSession.id).where(CallSession.caller_number == BROWSER_TEST_CALLER_NUMBER)
                ),
            )
        )
    if until is not None:
        escalated_filters.append(Notification.created_at < until)
    escalated_calls = await db.scalar(select(func.count()).where(*escalated_filters))
    return {
        "total_calls": total_calls or 0,
        "completed_calls": completed_calls or 0,
        "qualified_calls": qualified_calls or 0,
        "escalated_calls": escalated_calls or 0,
        "answer_rate": round((completed_calls or 0) / total_calls, 3) if total_calls else None,
    }


# ---------------------------------------------------------------------------
# Analytics page (GET /analytics/dashboard)
# ---------------------------------------------------------------------------

# Below this many data points a rate/average is shown as "not enough data
# yet" rather than a number that a couple of conversations would swing.
MIN_SAMPLE_SIZE = 5

# Calls that never reached a Mira conversation at all.
_NOT_A_CONVERSATION_CALL_TYPES = ("MISSED_AGENT_BUSY", "MISSED_SYSTEM_FAILURE")

# CallSummary.objection_tags -> guest-intent category. HOST_UNRESPONSIVE /
# GUEST_STOPPED_RESPONDING / NO_OBJECTION describe how a call went, not what
# the guest wanted, so they map to nothing.
_OBJECTION_INTENT = {
    "PRICE_TOO_HIGH": "pricing",
    "DATES_UNAVAILABLE": "availability",
    "LOCATION_MISMATCH": "location",
    "AMENITY_MISSING": "amenities",
    "POLICY_MISMATCH": "policies",
}

# Keyword map for Lead.questions_asked (free text the LLM records verbatim
# from the guest). Deliberately small and inspectable. A question matching
# several categories counts toward each; one matching none counts as other.
INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "availability": ("availab", "vacan", "free on", "open on", "booked on", "any room", "which dates", "these dates"),
    "pricing": ("price", "rate", "cost", "tariff", "discount", "charge", "per night", "budget", "cheap", "offer", "₹", "rupee"),
    "amenities": (
        "pool", "wifi", "wi-fi", "internet", "parking", "air condition", "ac", "kitchen", "amenit",
        "tv", "geyser", "breakfast", "meal", "bbq", "barbecue", "gym", "jacuzzi", "lift", "power backup",
    ),
    "location": ("location", "where is", "distance", "how far", "near", "beach", "airport", "station", "address", "direction"),
    "group": (
        "people", "group", "family", "kids", "child", "bedroom", "beds", "mattress", "accommodate", "how many guests",
        "extra guest", "capacity",
    ),
    "policies": (
        "check-in", "check in", "checkin", "checkout", "check-out", "check out", "cancel", "refund", "pet", "smok",
        "party", "rule", "policy", "id proof", "deposit", "early check", "late check", "unmarried", "couple",
    ),
}
INTENT_LABELS = {
    "availability": "Availability",
    "pricing": "Pricing",
    "amenities": "Amenities",
    "location": "Location",
    "group": "Guest / group requirements",
    "policies": "Policies",
    "other": "Other",
}


@dataclass
class AnalyticsScope:
    """One request's scope. start/end are inclusive calendar days; since/
    until are the matching IST-midnight datetimes for timestamp columns,
    the same day convention as app/api/v1/common.DateRange. Booking nights
    use start/end directly (dates, no timezone)."""

    host: User
    properties: list[Property]
    start: date
    end: date
    include_test_calls: bool = False
    today: date = field(default_factory=lambda: today_ist())

    @property
    def property_ids(self) -> list[uuid.UUID]:
        return [p.id for p in self.properties]

    @property
    def property_names(self) -> set[str]:
        return {p.name for p in self.properties}

    @property
    def since(self) -> datetime:
        return datetime.combine(self.start, time.min, tzinfo=IST)

    @property
    def until(self) -> datetime:
        return datetime.combine(self.end + timedelta(days=1), time.min, tzinfo=IST)

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def previous(self) -> "AnalyticsScope":
        length = timedelta(days=self.days)
        return AnalyticsScope(
            host=self.host,
            properties=self.properties,
            start=self.start - length,
            end=self.end - length,
            include_test_calls=self.include_test_calls,
            today=self.today,
        )


def _ratio(numerator: float, denominator: float, digits: int = 4) -> float | None:
    return round(numerator / denominator, digits) if denominator else None


def _money(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


def _sampled(value: float | None, sample_size: int) -> dict:
    """A metric that's only meaningful with enough data points."""
    sufficient = sample_size >= MIN_SAMPLE_SIZE
    return {
        "value": value if sufficient else None,
        "sample_size": sample_size,
        "sufficient": sufficient,
        "min_sample_size": MIN_SAMPLE_SIZE,
    }


def _overlap_nights(check_in: date, check_out: date, start: date, end_exclusive: date) -> int:
    return max((min(check_out, end_exclusive) - max(check_in, start)).days, 0)


async def _bookings_overlapping(
    db: AsyncSession, property_ids: list[uuid.UUID], start: date, end_exclusive: date
) -> list[Booking]:
    if not property_ids:
        return []
    return list(
        (
            await db.scalars(
                select(Booking).where(
                    Booking.property_id.in_(property_ids),
                    Booking.status == "confirmed",
                    Booking.check_in < end_exclusive,
                    Booking.check_out > start,
                )
            )
        ).all()
    )


async def portfolio_performance(db: AsyncSession, scope: AnalyticsScope) -> dict:
    """Occupancy, Revenue, ADR, RevPAR for the period, plus Upcoming Revenue.

    Inventory: every in-scope property is available every night of the
    period, minus nights closed by a no-guest block (Booking.kind="blocked").
    Nights are counted per property as sets of dates, so overlapping
    calendar rows never double-count.

      Occupancy = booked nights / available nights, summed across the whole
                  portfolio (never an average of per-property percentages).
      Revenue   = SUM(final_booking_price) over reservations with a
                  confirmed price, apportioned by the share of each stay's
                  nights that fall inside the period. A stay straddling the
                  period boundary contributes only its in-period nights, so
                  Revenue, ADR and RevPAR all describe the same nights.
      ADR       = Revenue / booked nights OF PRICED STAYS. Same bookings in
                  the numerator and denominator, so an unpriced booking
                  doesn't drag ADR down.
      RevPAR    = Revenue / available nights. Unpriced stays contribute no
                  revenue, so RevPAR is a floor until prices are confirmed.
                  `completeness` says by how much.
      Upcoming Revenue = the same apportioned SUM over nights from today
                  onward (independent of the selected period).
    """
    end_exclusive = scope.end + timedelta(days=1)
    bookings = await _bookings_overlapping(db, scope.property_ids, scope.start, end_exclusive)

    booked: dict[uuid.UUID, set[date]] = {pid: set() for pid in scope.property_ids}
    blocked: dict[uuid.UUID, set[date]] = {pid: set() for pid in scope.property_ids}
    revenue = 0.0
    priced_nights = 0
    reservations = priced = 0
    for booking in bookings:
        first = max(booking.check_in, scope.start)
        last = min(booking.check_out, end_exclusive)
        nights = {first + timedelta(days=i) for i in range((last - first).days)}
        if not booking.is_reservation:
            blocked[booking.property_id] |= nights
            continue
        booked[booking.property_id] |= nights
        reservations += 1
        stay_nights = (booking.check_out - booking.check_in).days
        # stay_nights > 0 guards rows written before BookingCreate validated
        # check_out > check_in; such a row can't be apportioned per night.
        if booking.has_confirmed_price and stay_nights > 0:
            priced += 1
            revenue += float(booking.final_booking_price) * len(nights) / stay_nights
            priced_nights += len(nights)

    booked_nights = sum(len(n) for n in booked.values())
    blocked_nights = sum(len(blocked[pid] - booked[pid]) for pid in scope.property_ids)
    available_nights = len(scope.property_ids) * scope.days - blocked_nights

    upcoming = await _upcoming_revenue(db, scope)
    return {
        "occupancy": _ratio(booked_nights, available_nights),
        "revenue": _money(revenue) if priced else None,
        "adr": _money(revenue / priced_nights) if priced_nights else None,
        "revpar": _money(revenue / available_nights) if priced and available_nights else None,
        "upcoming_revenue": upcoming["value"],
        "booked_nights": booked_nights,
        "available_nights": available_nights,
        "blocked_nights": blocked_nights,
        "currency": "INR",
        "completeness": {
            "bookings_total": reservations,
            "bookings_priced": priced,
            "bookings_missing_price": reservations - priced,
            "is_complete": reservations == priced,
        },
        "upcoming_completeness": upcoming["completeness"],
    }


async def _upcoming_revenue(db: AsyncSession, scope: AnalyticsScope) -> dict:
    if not scope.property_ids:
        return {"value": None, "completeness": {"bookings_total": 0, "bookings_priced": 0, "bookings_missing_price": 0, "is_complete": True}}
    rows = (
        await db.scalars(
            select(Booking).where(
                Booking.property_id.in_(scope.property_ids),
                Booking.status == "confirmed",
                Booking.kind == "reservation",
                Booking.check_out > scope.today,
            )
        )
    ).all()
    total = 0.0
    priced = 0
    for booking in rows:
        stay_nights = (booking.check_out - booking.check_in).days
        if not booking.has_confirmed_price or stay_nights <= 0:
            continue
        priced += 1
        future_nights = (booking.check_out - max(booking.check_in, scope.today)).days
        total += float(booking.final_booking_price) * future_nights / stay_nights
    return {
        "value": _money(total) if priced else None,
        "completeness": {
            "bookings_total": len(rows),
            "bookings_priced": priced,
            "bookings_missing_price": len(rows) - priced,
            "is_complete": len(rows) == priced,
        },
    }


@dataclass
class _Conversations:
    """The period's Mira conversations, loaded once and shared by the
    funnel, impact, intent and pricing sections."""

    calls: list[CallSession]
    leads: dict[uuid.UUID, Lead]
    converted_lead_ids: set[uuid.UUID]
    escalated_call_ids: set[uuid.UUID]


def _call_in_scope(call: CallSession, lead: Lead | None, scope: AnalyticsScope, all_properties: bool) -> bool:
    if all_properties:
        return True
    if call.property_id in scope.property_ids:
        return True
    return lead is not None and bool(scope.property_names & set(lead.properties_discussed or []))


async def _load_conversations(db: AsyncSession, scope: AnalyticsScope, all_properties: bool) -> _Conversations:
    filters = [
        CallSession.user_id == scope.host.id,
        CallSession.created_at >= scope.since,
        CallSession.created_at < scope.until,
        or_(CallSession.call_type.is_(None), CallSession.call_type.not_in(_NOT_A_CONVERSATION_CALL_TYPES)),
    ]
    if not scope.include_test_calls:
        filters.append(
            or_(CallSession.caller_number.is_(None), CallSession.caller_number != BROWSER_TEST_CALLER_NUMBER)
        )
    calls = list((await db.scalars(select(CallSession).where(*filters))).all())
    lead_ids = {c.lead_id for c in calls if c.lead_id is not None}
    leads = (
        {lead.id: lead for lead in (await db.scalars(select(Lead).where(Lead.id.in_(lead_ids)))).all()}
        if lead_ids
        else {}
    )
    calls = [c for c in calls if _call_in_scope(c, leads.get(c.lead_id), scope, all_properties)]

    converted: set[uuid.UUID] = {lid for lid, lead in leads.items() if lead.status == "booked"}
    if lead_ids:
        converted |= set(
            (
                await db.scalars(
                    select(Booking.mira_attribution_lead_id).where(
                        Booking.mira_attribution_lead_id.in_(lead_ids),
                        Booking.mira_attribution_status == "confirmed",
                        Booking.status == "confirmed",
                        Booking.kind == "reservation",
                    )
                )
            ).all()
        )

    escalated: set[uuid.UUID] = set()
    call_ids = [c.id for c in calls]
    if call_ids:
        escalated = set(
            (
                await db.scalars(
                    select(Notification.call_session_id).where(
                        Notification.call_session_id.in_(call_ids), Notification.channel == "escalation"
                    )
                )
            ).all()
        )
    return _Conversations(calls=calls, leads=leads, converted_lead_ids=converted, escalated_call_ids=escalated)


def _is_booking_related(call: CallSession) -> bool:
    """call_type BOOKING_LEAD, or -- since an escalated/transferred booking
    call is relabelled ESCALATED_NO_TRANSFER/TRANSFERRED_TO_HOST at hangup
    (schemas/call_classification.py) -- an end-of-call summary whose intent
    is "New Booking"."""
    if call.call_type == "BOOKING_LEAD":
        return True
    snapshot = (call.ai_summary or {}).get("booking_snapshot") or {}
    return snapshot.get("intent") == "New Booking"


def booking_funnel(conversations: _Conversations) -> dict:
    """Six stages from persisted data only.

    1. All conversations     -- calls Mira handled in the period.
    2. Booking-related       -- of those, booking enquiries (_is_booking_related).
    3. Qualified leads       -- unique guests (Lead rows) from stage-2 calls at
                                warm or above.
    4. Hot / very hot leads  -- stage-3 guests at hot or very_hot.
    5. Booking intent        -- stage-4 guests at very_hot.
    6. Converted bookings    -- stage-5 guests whose lead the host marked
                                booked, or who have a host-confirmed Mira-
                                attributed booking.

    Stages 1-2 count conversations, 3-6 count unique guests (a guest who
    called twice is one lead). A guest who converted counts as having
    reached every earlier stage. They did book, so that's what happened, and
    it keeps every stage a subset of the one before. Rates are only between
    adjacent stages and only when the earlier stage is non-empty.
    """
    booking_calls = [c for c in conversations.calls if _is_booking_related(c)]
    guests = {c.lead_id for c in booking_calls if c.lead_id in conversations.leads}

    def reached(lead_id: uuid.UUID, levels: tuple[str, ...]) -> bool:
        return lead_id in conversations.converted_lead_ids or conversations.leads[lead_id].lead_temperature in levels

    qualified = {g for g in guests if reached(g, WARM_OR_ABOVE)}
    hot = {g for g in qualified if reached(g, HOT_OR_ABOVE)}
    intent = {g for g in hot if reached(g, ("very_hot",))}
    converted = {g for g in intent if g in conversations.converted_lead_ids}

    stages = [
        ("all_conversations", "All conversations", len(conversations.calls), "conversations"),
        ("booking_related", "Booking-related conversations", len(booking_calls), "conversations"),
        ("qualified_leads", "Qualified leads", len(qualified), "guests"),
        ("hot_leads", "Hot / very hot leads", len(hot), "guests"),
        ("booking_intent", "Booking intent", len(intent), "guests"),
        ("converted", "Converted bookings", len(converted), "guests"),
    ]
    out = []
    previous = None
    for key, label, value, unit in stages:
        out.append(
            {
                "key": key,
                "label": label,
                "value": value,
                "unit": unit,
                "rate_from_previous": _ratio(value, previous, 3) if previous else None,
            }
        )
        previous = value
    return {"stages": out}


def _after_hours(host: User, calls: list[CallSession]) -> dict:
    """Booking-related conversations that arrived outside the host's own
    call hours (User.host_call_hours_*), evaluated with the same rule the
    live call router uses (call_ownership.resolve_effective_call_owner).
    Without configured host hours, Mira answers 24/7 and "after hours" has
    no meaning, so the value is None and the UI asks the host to set them."""
    if not host.host_call_hours_enabled:
        return {"configured": False, "value": None, "share": None}
    count = 0
    try:
        for call in calls:
            stamp = call.started_at or call.created_at
            if stamp and resolve_effective_call_owner(None, host, stamp) == CallOwner.MIRA:
                count += 1
    except InvalidCallOwnershipConfigError:
        return {"configured": False, "value": None, "share": None}
    return {"configured": True, "value": count, "share": _ratio(count, len(calls), 3)}


async def mira_impact(db: AsyncSession, scope: AnalyticsScope, conversations: _Conversations) -> dict:
    """Only host-CONFIRMED attribution counts as Mira-attributed bookings or
    revenue. Probable matches are reported separately (awaiting_confirmation)
    so the host can see them, but they are never added to impact.

    Attributed bookings are counted by when they were booked (created)
    inside the period. Attributed revenue is their full final_booking_price
    (the booking's value, not apportioned by stay night), with completeness
    for attributed bookings still missing a price.
    """
    attributed: list[Booking] = []
    awaiting = 0
    if scope.property_ids:
        rows = (
            await db.scalars(
                select(Booking).where(
                    Booking.property_id.in_(scope.property_ids),
                    Booking.status == "confirmed",
                    Booking.kind == "reservation",
                    Booking.created_at >= scope.since,
                    Booking.created_at < scope.until,
                    Booking.mira_attribution_status.in_(("confirmed", "probable")),
                )
            )
        ).all()
        attributed = [b for b in rows if b.mira_attribution_status == "confirmed"]
        awaiting = sum(1 for b in rows if b.needs_attribution_review)
    priced = [b for b in attributed if b.has_confirmed_price]
    attributed_revenue = sum(float(b.final_booking_price) for b in priced)

    # Revenue recovered: confirmed Mira-attributed bookings whose lead came in
    # through Busy Call Recovery (Lead.recovery_reason set).
    recovery = await recovery_metrics(db, scope.host.id, scope.since, scope.until)
    lead_ids = {b.mira_attribution_lead_id for b in priced if b.mira_attribution_lead_id}
    recovery_lead_ids: set[uuid.UUID] = set()
    if lead_ids:
        recovery_lead_ids = set(
            (
                await db.scalars(select(Lead.id).where(Lead.id.in_(lead_ids), Lead.recovery_reason.is_not(None)))
            ).all()
        )
    recovered_bookings = [b for b in priced if b.mira_attribution_lead_id in recovery_lead_ids]

    calls = conversations.calls
    completed = [c for c in calls if c.status == "completed"]
    resolved = [
        c
        for c in completed
        if c.id not in conversations.escalated_call_ids and c.call_type not in ("TRANSFERRED_TO_HOST",)
    ]
    booking_calls = [c for c in calls if _is_booking_related(c)]
    return {
        "attributed_bookings": len(attributed),
        "attributed_revenue": _money(attributed_revenue) if priced else None,
        "attributed_completeness": {
            "bookings_total": len(attributed),
            "bookings_priced": len(priced),
            "bookings_missing_price": len(attributed) - len(priced),
            "is_complete": len(attributed) == len(priced),
        },
        "awaiting_confirmation": awaiting,
        "after_hours_opportunities": _after_hours(scope.host, booking_calls),
        "revenue_recovered": _money(sum(float(b.final_booking_price) for b in recovered_bookings))
        if recovered_bookings
        else None,
        "recovery": {
            "busy_calls": recovery["busy_calls"],
            "recovered": recovery["recovered"],
            "converted": recovery["converted"],
        },
        "resolved_by_mira": len(resolved),
        "resolved_by_mira_rate": _ratio(len(resolved), len(calls), 3),
        "host_escalations": len(conversations.escalated_call_ids),
        "conversations": len(calls),
    }


def _keyword_pattern(words: tuple[str, ...]) -> re.Pattern[str]:
    # Anchored at a word START so a stem still matches its forms ("availab"
    # -> "availability") but never the inside of another word ("rate" in
    # "separate", "pet" in "carpet"). Short words (<= 4 chars) must also END
    # the word, plural allowed, so "ac" doesn't match "accommodate".
    parts = []
    for word in words:
        part = (r"\b" if re.match(r"\w", word) else "") + re.escape(word)
        if len(word) <= 4 and re.search(r"\w$", word):
            part += r"(?:s|es)?\b"
        parts.append(part)
    return re.compile("|".join(parts))


_INTENT_PATTERNS = {category: _keyword_pattern(words) for category, words in INTENT_KEYWORDS.items()}


def _question_categories(question: str) -> set[str]:
    text = question.lower()
    return {category for category, pattern in _INTENT_PATTERNS.items() if pattern.search(text)}


async def guest_intent(db: AsyncSession, conversations: _Conversations) -> dict:
    """What guests asked about, per conversation, from structured data only:
    the call's PriceEvents (Mira quoted or negotiated = pricing), its
    end-of-call objection_tags, and its lead's recorded questions_asked
    (keyword-mapped, INTENT_KEYWORDS). A conversation counts once per
    category however many matching questions it had. Conversations with no
    such data are excluded from the base rather than counted as "other"."""
    call_ids = [c.id for c in conversations.calls]
    priced_calls: set[uuid.UUID] = set()
    if call_ids:
        priced_calls = set(
            (await db.scalars(select(PriceEvent.call_session_id).where(PriceEvent.call_session_id.in_(call_ids)))).all()
        )
    counts = {key: 0 for key in INTENT_LABELS}
    with_data = 0
    for call in conversations.calls:
        categories: set[str] = set()
        if call.id in priced_calls:
            categories.add("pricing")
        for tag in (call.ai_summary or {}).get("objection_tags") or []:
            if tag in _OBJECTION_INTENT:
                categories.add(_OBJECTION_INTENT[tag])
        lead = conversations.leads.get(call.lead_id)
        questions = [q for q in (lead.questions_asked if lead else []) or [] if isinstance(q, str) and q.strip()]
        for question in questions:
            matched = _question_categories(question)
            categories |= matched or {"other"}
        if not categories:
            continue
        with_data += 1
        for category in categories:
            counts[category] += 1
    return {
        "conversations_with_data": with_data,
        "categories": sorted(
            (
                {"key": key, "label": INTENT_LABELS[key], "count": count, "share": _ratio(count, with_data, 3)}
                for key, count in counts.items()
            ),
            key=lambda item: (item["key"] == "other", -item["count"]),
        ),
    }


def _per_night(price, nights) -> float | None:
    if price is None or not nights:
        return None
    return float(price) / nights


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


async def pricing_and_negotiation(db: AsyncSession, scope: AnalyticsScope, conversations: _Conversations) -> dict:
    """From PriceEvent history (recorded since this feature shipped) and
    confirmed booking prices. Prices are compared PER NIGHT, because stay
    lengths differ.

      Initial quoted price   avg per-night of initial_quote events.
      Negotiated price       avg per-night of each negotiated conversation's
                             LAST counter/accepted offer.
      Final booking price    avg per-night of reservations checking in during
                             the period with a confirmed final price.
      Negotiation conversion negotiated conversations whose guest converted
                             (see booking_funnel) / negotiated conversations.
      Average discount       mean of (1 - offer / undiscounted asking price)
                             over each negotiated conversation's last offer.
      Price objections       booking-related conversations tagged
                             PRICE_TOO_HIGH.

    Rates and averages need MIN_SAMPLE_SIZE data points, else they come back
    as "not enough data yet" with the sample size.
    """
    call_by_id = {c.id: c for c in conversations.calls}
    events: list[PriceEvent] = []
    if call_by_id:
        stmt = select(PriceEvent).where(
            PriceEvent.call_session_id.in_(list(call_by_id)),
            PriceEvent.price_type.in_(("initial_quote", "counter_offer", "accepted_offer")),
        )
        if scope.property_ids:
            stmt = stmt.where(or_(PriceEvent.property_id.is_(None), PriceEvent.property_id.in_(scope.property_ids)))
        events = sorted((await db.scalars(stmt)).all(), key=lambda e: e.created_at)

    quotes = [v for e in events if e.price_type == "initial_quote" and (v := _per_night(e.price, e.nights)) is not None]
    last_offer: dict[uuid.UUID, PriceEvent] = {}
    for event in events:
        if event.price_type in ("counter_offer", "accepted_offer"):
            last_offer[event.call_session_id] = event
    negotiated = [v for e in last_offer.values() if (v := _per_night(e.price, e.nights)) is not None]
    discounts = [
        1 - float(e.price) / float(e.list_price)
        for e in last_offer.values()
        if e.list_price and float(e.list_price) > 0 and float(e.price) <= float(e.list_price)
    ]
    converted = sum(
        1 for cid in last_offer if call_by_id[cid].lead_id in conversations.converted_lead_ids
    )

    end_exclusive = scope.end + timedelta(days=1)
    finals = []
    if scope.property_ids:
        rows = (
            await db.scalars(
                select(Booking).where(
                    Booking.property_id.in_(scope.property_ids),
                    Booking.status == "confirmed",
                    Booking.kind == "reservation",
                    Booking.check_in >= scope.start,
                    Booking.check_in < end_exclusive,
                    Booking.final_booking_price.is_not(None),
                    Booking.price_status == "confirmed",
                )
            )
        ).all()
        finals = [v for b in rows if (v := _per_night(b.final_booking_price, (b.check_out - b.check_in).days))]

    booking_calls = [c for c in conversations.calls if _is_booking_related(c)]
    objections = sum(1 for c in booking_calls if "PRICE_TOO_HIGH" in ((c.ai_summary or {}).get("objection_tags") or []))

    def _avg(values: list[float]) -> dict:
        return _sampled(_money(_mean(values)), len(values))

    return {
        "avg_initial_quote_per_night": _avg(quotes),
        "avg_negotiated_price_per_night": _avg(negotiated),
        "avg_final_price_per_night": _avg(finals),
        "negotiations": len(last_offer),
        "negotiation_conversion": _sampled(_ratio(converted, len(last_offer), 3), len(last_offer)),
        "avg_discount": _sampled(round(_mean(discounts), 4) if discounts else None, len(discounts)),
        "price_objections": objections,
        "price_objection_rate": _sampled(_ratio(objections, len(booking_calls), 3), len(booking_calls)),
        "currency": "INR",
    }


async def needs_confirmation_counts(db: AsyncSession, scope: AnalyticsScope) -> dict:
    """Same queue the Needs-confirmation list shows
    (booking_reconciliation_service.reconciliation_queue), scoped to the
    filtered properties."""
    from app.services.booking_reconciliation_service import reconciliation_queue

    rows = await reconciliation_queue(db, scope.property_ids)
    return {
        "total": len(rows),
        "price_missing": sum(1 for b in rows if b.needs_price_confirmation),
        "attribution_pending": sum(1 for b in rows if b.needs_attribution_review),
    }


def _delta(current: float | None, previous: float | None) -> float | None:
    """Relative change vs. the comparison period; None when either side is
    missing or the previous value is zero (no honest percentage exists)."""
    if current is None or previous in (None, 0):
        return None
    return round((current - previous) / previous, 4)


async def dashboard(db: AsyncSession, scope: AnalyticsScope, all_properties: bool, compare: bool = True) -> dict:
    conversations = await _load_conversations(db, scope, all_properties)
    portfolio = await portfolio_performance(db, scope)
    comparison = None
    if compare:
        previous = await portfolio_performance(db, scope.previous())
        comparison = {
            "start_date": scope.previous().start.isoformat(),
            "end_date": scope.previous().end.isoformat(),
            "occupancy": previous["occupancy"],
            "revenue": previous["revenue"],
            "adr": previous["adr"],
            "revpar": previous["revpar"],
            "change": {
                key: _delta(portfolio[key], previous[key]) for key in ("occupancy", "revenue", "adr", "revpar")
            },
        }
    return {
        "start_date": scope.start.isoformat(),
        "end_date": scope.end.isoformat(),
        "property_ids": [str(pid) for pid in scope.property_ids],
        "portfolio": {**portfolio, "comparison": comparison},
        "funnel": booking_funnel(conversations),
        "impact": await mira_impact(db, scope, conversations),
        "guest_intent": await guest_intent(db, conversations),
        "pricing": await pricing_and_negotiation(db, scope, conversations),
        "needs_confirmation": await needs_confirmation_counts(db, scope),
    }


# ---------------------------------------------------------------------------
# Overview page (GET /analytics/overview)
#
# "What matters right now?" -- a snapshot, not a report. Two kinds of data:
#
#   Reporting-period metrics (respect the selected start/end dates):
#     occupancy, revenue, ADR, RevPAR (portfolio_performance), booking
#     opportunities / high-intent / booking intent (booking_funnel stages
#     3-5), Mira-attributed bookings, and call activity.
#   Current-state items (deliberately NOT date-filtered -- the date picker is
#     a reporting-period filter, not a visibility filter):
#     upcoming revenue (today onward), bookings needing price/attribution
#     confirmation, and high-intent guests who haven't converted yet.
#
# include_test_calls only affects conversation-derived numbers (call
# activity, booking opportunities, high-intent guests). Bookings, prices and
# attribution never come from browser test calls, so it can't change them.
# ---------------------------------------------------------------------------

_QUOTE_PRICE_TYPES = ("initial_quote", "counter_offer", "accepted_offer")


async def _attributed_bookings(db: AsyncSession, scope: AnalyticsScope) -> int:
    """Reservations booked (created) in the period with host-CONFIRMED Mira
    attribution. Probable matches are never counted -- they surface as an
    attention item instead."""
    if not scope.property_ids:
        return 0
    return (
        await db.scalar(
            select(func.count()).where(
                Booking.property_id.in_(scope.property_ids),
                Booking.status == "confirmed",
                Booking.kind == "reservation",
                Booking.mira_attribution_status == "confirmed",
                Booking.created_at >= scope.since,
                Booking.created_at < scope.until,
            )
        )
        or 0
    )


async def high_intent_unconverted(db: AsyncSession, scope: AnalyticsScope) -> dict:
    """Current state: open/contacted leads at hot or very_hot whose stay
    hasn't started (no check-in yet, or check-in today or later) and that
    have no host-confirmed Mira booking. potential_value sums each guest's
    LATEST Mira price (quote or offer, a stay total) -- what the guest was
    last told -- over the guests that have one; valued_count says how many
    that is, so a partial sum is never presented as complete."""
    filters = [
        Lead.user_id == scope.host.id,
        Lead.status.in_(("open", "contacted")),
        Lead.lead_temperature.in_(HOT_OR_ABOVE),
        or_(Lead.check_in.is_(None), Lead.check_in >= scope.today),
    ]
    stmt = select(Lead)
    if not scope.include_test_calls:
        # A browser-test lead never carries the test number itself (the
        # pipeline withholds it from tools), so test-ness comes from the call
        # that created the lead. Leads with no originating call (e.g. Busy
        # Call Recovery) are real guests and stay in.
        stmt = stmt.outerjoin(CallSession, CallSession.id == Lead.call_session_id)
        filters.append(
            or_(CallSession.caller_number.is_(None), CallSession.caller_number != BROWSER_TEST_CALLER_NUMBER)
        )
    leads = list((await db.scalars(stmt.where(*filters))).all())
    if not leads:
        return {"count": 0, "potential_value": None, "valued_count": 0}
    lead_ids = [lead.id for lead in leads]
    converted = set(
        (
            await db.scalars(
                select(Booking.mira_attribution_lead_id).where(
                    Booking.mira_attribution_lead_id.in_(lead_ids),
                    Booking.mira_attribution_status == "confirmed",
                    Booking.status == "confirmed",
                    Booking.kind == "reservation",
                )
            )
        ).all()
    )
    remaining = [lid for lid in lead_ids if lid not in converted]
    latest: dict[uuid.UUID, float] = {}
    if remaining:
        rows = (
            await db.execute(
                select(CallSession.lead_id, PriceEvent.price)
                .join(PriceEvent, PriceEvent.call_session_id == CallSession.id)
                .where(CallSession.lead_id.in_(remaining), PriceEvent.price_type.in_(_QUOTE_PRICE_TYPES))
                .order_by(PriceEvent.created_at, PriceEvent.id)
            )
        ).all()
        for lead_id, price in rows:
            latest[lead_id] = float(price)
    return {
        "count": len(remaining),
        "potential_value": _money(sum(latest.values())) if latest else None,
        "valued_count": len(latest),
    }


async def overview(db: AsyncSession, scope: AnalyticsScope) -> dict:
    conversations = await _load_conversations(db, scope, all_properties=True)
    stages = {s["key"]: s["value"] for s in booking_funnel(conversations)["stages"]}
    portfolio = await portfolio_performance(db, scope)
    activity = await call_activity(
        db, scope.host.id, scope.property_ids, scope.since, scope.until, include_test_calls=scope.include_test_calls
    )
    confirmation = await needs_confirmation_counts(db, scope)
    return {
        "start_date": scope.start.isoformat(),
        "end_date": scope.end.isoformat(),
        "has_properties": bool(scope.property_ids),
        "portfolio": {
            "occupancy": portfolio["occupancy"],
            "revenue": portfolio["revenue"],
            "adr": portfolio["adr"],
            "revpar": portfolio["revpar"],
            "upcoming_revenue": portfolio["upcoming_revenue"],
            "booked_nights": portfolio["booked_nights"],
            "available_nights": portfolio["available_nights"],
            "completeness": portfolio["completeness"],
            "upcoming_completeness": portfolio["upcoming_completeness"],
            "currency": portfolio["currency"],
        },
        "opportunities": {
            # Booking opportunities = qualified leads (warm or above) from
            # booking-related conversations in the period; high_intent and
            # booking_intent are the hot+/very_hot subsets -- the same
            # stages the Analytics funnel shows.
            "booking_opportunities": stages["qualified_leads"],
            "high_intent": stages["hot_leads"],
            "booking_intent": stages["booking_intent"],
        },
        "mira_attributed_bookings": await _attributed_bookings(db, scope),
        "activity": activity,
        "attention": {
            "price_confirmations": confirmation["price_missing"],
            "attribution_confirmations": confirmation["attribution_pending"],
            "high_intent_unconverted": await high_intent_unconverted(db, scope),
        },
    }
