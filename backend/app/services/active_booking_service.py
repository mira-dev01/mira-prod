"""Leads -> "Active bookings": the guests who are booked, where they are in their stay, how they have paid, and
whether they have asked for anything.

There is no table of bookings here. A booking is a LEAD, so it is the same row Booking requests shows:
  * a lead the host CLOSED (Booking requests -> Closed leads, which blocks the dates on the calendar) is a booking;
  * so is a lead the system closes for the host's rule (BookingConfig.active_when, set in Agent -> Payments): when
    the payment link is sent, or when it is paid -- payment_service closes the lead then;
  * and a lead a verified payment booked ("booked", the pre-existing outcome of confirm_by="payment").
A closed lead with no dates, no payment link and no calendar block is a dead-end enquiry, not a booking, and is
not listed. Whichever tab you close or reopen it in, the other follows, because it is one lead.

A stay's phase comes from the property's own check-in and check-out times (default 14:00 / 11:00, IST):
  upcoming -> before the check-in time of the first day;  in_stay -> from that moment until the check-out time of
  the last day;  completed -> after (not listed).

A request from a guest with an active booking (a service request or escalation: a notification) is tagged with the
booking's lead when it is created (`resolve_booking_lead`). Unread tagged notifications are the number on the tab and in
the sidebar, and each raises a banner. Opening a guest clears theirs.

Nothing here raises into a call: `resolve_booking_lead` logs and carries on.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking import Booking
from app.models.call_session import CallSession
from app.models.lead import Lead
from app.models.notification import Notification
from app.models.payment import PaymentRequest
from app.models.property import Property
from app.models.user import User
from app.observability import health
from app.utils.dates import today_ist

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
DEFAULT_CHECK_IN = time(14, 0)
DEFAULT_CHECK_OUT = time(11, 0)
MAX_ACTIVITIES_PER_GUEST = 5


# -- small helpers ---------------------------------------------------------------------------------


def phone_key(value: str | None) -> str:
    """The last ten digits: the same guest whether the number is written +91 98765 43210 or 9876543210."""
    digits = re.sub(r"\D", "", value or "")
    return digits[-10:] if len(digits) >= 10 else ""


def _clock(value: str | None, default: time) -> time:
    match = re.match(r"^\s*(\d{1,2}):(\d{2})", value or "")
    if match and int(match.group(1)) < 24 and int(match.group(2)) < 60:
        return time(int(match.group(1)), int(match.group(2)))
    return default


def stay_window(check_in: date, check_out: date, check_in_time: str | None, check_out_time: str | None) -> tuple[datetime, datetime]:
    """(start, end): check-in day at the check-in time, check-out day at the check-out time, both IST."""
    return (
        datetime.combine(check_in, _clock(check_in_time, DEFAULT_CHECK_IN), tzinfo=IST),
        datetime.combine(check_out, _clock(check_out_time, DEFAULT_CHECK_OUT), tzinfo=IST),
    )


def phase_of(now: datetime, start: datetime, end: datetime) -> str:
    if now < start:
        return "upcoming"
    if now < end:
        return "in_stay"
    return "completed"


# -- how the guest has paid ---------------------------------------------------------------------------

# (label, tone). Tones are the dashboard's StatusChip tones. These are the states Mira can actually tell: the
# payment provider reports a link as created / paid / expired / cancelled; the rest come from what Mira saw.
PAYMENT_STATES = {
    "no_link": ("No payment link", "neutral"),
    "link_pending": ("Link not sent yet", "pending"),
    "link_sent": ("Link sent, awaiting payment", "progress"),
    "delivery_failed": ("Link not delivered", "destructive"),
    "payment_failed": ("Payment failed, link open", "orange"),
    "part_paid": ("Part paid", "pending"),
    "paid": ("Paid", "live"),
    "expired": ("Hold expired", "destructive"),
    "cancelled": ("Cancelled", "neutral"),
}


def payment_state(request: PaymentRequest | None) -> str:
    if request is None:
        return "no_link"
    details = request.details or {}
    if request.status == "paid":
        return "part_paid" if Decimal(request.total_amount) - Decimal(request.amount_due) > 0 else "paid"
    if request.status == "active":
        if details.get("link_delivery") == "failed":
            return "delivery_failed"
        if details.get("failed_attempt_at"):
            return "payment_failed"
        return "link_sent" if request.link_sent_at else "link_pending"
    return request.status if request.status in PAYMENT_STATES else "cancelled"


def payment_view(request: PaymentRequest | None, provider_down: bool) -> dict:
    state = payment_state(request)
    label, tone = PAYMENT_STATES[state]
    waiting = state in ("link_sent", "link_pending", "payment_failed")
    out = {
        "state": state,
        "label": label,
        "tone": tone,
        # The provider is unreachable right now: a link still waiting to be paid cannot be checked.
        "payment_down": bool(provider_down and waiting),
        "amount_due": None,
        "total_amount": None,
        "balance": None,
        "balance_due_on": None,
        "paid_at": None,
        "link_sent_at": None,
        "hold_expires_at": None,
        "short_url": None,
        "stay_code": None,
        "request_id": None,
    }
    if request is None:
        return out
    balance = Decimal(request.total_amount) - Decimal(request.amount_due)
    out.update(
        amount_due=float(request.amount_due),
        total_amount=float(request.total_amount),
        balance=float(balance) if request.status == "paid" and balance > 0 else None,
        balance_due_on=request.balance_due_on.isoformat() if request.balance_due_on else None,
        paid_at=request.paid_at.isoformat() if request.paid_at else None,
        link_sent_at=request.link_sent_at.isoformat() if request.link_sent_at else None,
        hold_expires_at=request.hold_expires_at.isoformat() if request.hold_expires_at else None,
        short_url=request.short_url if request.status == "active" else None,
        stay_code=(request.details or {}).get("stay_code"),
        request_id=str(request.id),
    )
    return out


# -- finding the bookings ------------------------------------------------------------------------------


async def _requests_by_lead(db: AsyncSession, lead_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[PaymentRequest]]:
    if not lead_ids:
        return {}
    grouped: dict[uuid.UUID, list[PaymentRequest]] = {}
    rows = await db.scalars(
        select(PaymentRequest).where(PaymentRequest.lead_id.in_(lead_ids)).order_by(PaymentRequest.created_at.desc())
    )
    for request in rows.all():
        grouped.setdefault(request.lead_id, []).append(request)
    return grouped


async def _active_leads(db: AsyncSession, user_id: uuid.UUID) -> tuple[list[Lead], dict[uuid.UUID, list[PaymentRequest]]]:
    """The host's bookings whose stay has not ended (a day of slack; the phase decides exactly), with their payment
    requests. See the module note for what counts as a booking."""
    cutoff = today_ist() - timedelta(days=1)
    candidates = list(
        (
            await db.scalars(
                select(Lead).where(
                    Lead.user_id == user_id,
                    Lead.status.in_(("closed", "booked")),
                    Lead.check_in.is_not(None),
                    Lead.check_out.is_not(None),
                    Lead.check_out >= cutoff,
                )
            )
        ).all()
    )
    if not candidates:
        return [], {}
    ids = [lead.id for lead in candidates]
    requests = await _requests_by_lead(db, ids)
    on_calendar = set(
        (await db.scalars(select(Booking.mira_attribution_lead_id).where(Booking.mira_attribution_lead_id.in_(ids)))).all()
    )
    leads = []
    for lead in candidates:
        mine = requests.get(lead.id, [])
        if lead.status == "closed":
            keep = bool(mine) or lead.id in on_calendar
        else:  # "booked": only the outcome of a payment that confirmed the booking
            keep = any(r.status == "paid" and r.confirm_by == "payment" for r in mine)
        if keep:
            leads.append(lead)
    return leads, requests


def _pick_request(requests: list[PaymentRequest]) -> PaymentRequest | None:
    """The one that says where the guest stands: a paid one, else a waiting one, else the newest."""
    for wanted in ("paid", "active"):
        for request in requests:
            if request.status == wanted:
                return request
    return requests[0] if requests else None


async def find_for_phone(db: AsyncSession, user_id: uuid.UUID, phone: str | None) -> Lead | None:
    """The host's active booking (stay not over) for the guest with this phone number, if any."""
    key = phone_key(phone)
    if not key:
        return None
    leads, requests = await _active_leads(db, user_id)
    if not leads:
        return None
    now = datetime.now(IST)
    for lead in leads:
        if lead.check_in and lead.check_out and phase_of(now, *stay_window(lead.check_in, lead.check_out, None, None)) == "completed":
            continue
        keys = {phone_key(lead.phone)}
        for request in requests.get(lead.id, []):
            keys.update((phone_key(request.guest_phone), phone_key((request.details or {}).get("caller_phone"))))
        if key in keys:
            return lead
    return None


async def list_active(db: AsyncSession, user: User) -> dict:
    leads, requests = await _active_leads(db, user.id)
    properties = {p.id: p for p in (await db.scalars(select(Property).where(Property.user_id == user.id))).all()}
    by_name = {(p.name or "").strip().lower(): p for p in properties.values()}
    for p in properties.values():
        if p.display_name:
            by_name.setdefault(p.display_name.strip().lower(), p)

    activity: dict[uuid.UUID, list[Notification]] = {}
    if leads:
        rows = await db.scalars(
            select(Notification)
            .where(Notification.booking_lead_id.in_([lead.id for lead in leads]), Notification.status == "new")
            .order_by(Notification.created_at.desc())
        )
        for notification in rows.all():
            activity.setdefault(notification.booking_lead_id, []).append(notification)

    provider_down = health.state_of("razorpay") == "down"
    now = datetime.now(IST)
    upcoming, in_stay = [], []
    for lead in leads:
        request = _pick_request(requests.get(lead.id, []))
        prop = properties.get(request.property_id) if request else None
        if prop is None:
            for name in lead.properties_discussed or []:
                prop = by_name.get(str(name).strip().lower())
                if prop is not None:
                    break
        check_in = lead.check_in or (request.check_in if request else None)
        check_out = lead.check_out or (request.check_out if request else None)
        if check_in and check_out:
            start, end = stay_window(check_in, check_out, prop.check_in_time if prop else None, prop.check_out_time if prop else None)
            phase = phase_of(now, start, end)
            if phase == "completed":
                continue
        else:
            start = end = None
            phase = "upcoming"
        mine = activity.get(lead.id, [])
        item = {
            "lead_id": str(lead.id),
            "guest_name": lead.guest_name or (request.guest_name if request else None),
            "phone": lead.phone or (request.guest_phone if request else None),
            "property_id": str(prop.id) if prop else None,
            "property_name": (prop.display_name or prop.name) if prop else ((lead.properties_discussed or [None])[0]),
            "check_in": check_in.isoformat() if check_in else None,
            "check_out": check_out.isoformat() if check_out else None,
            "check_in_at": start.isoformat() if start else None,
            "check_out_at": end.isoformat() if end else None,
            "nights": (check_out - check_in).days if check_in and check_out else None,
            "num_guests": lead.num_guests,
            "phase": phase,
            "payment": payment_view(request, provider_down),
            "activity_count": len(mine),
            "activities": [
                {"id": str(n.id), "message": n.message, "urgency": n.urgency, "created_at": n.created_at.isoformat()}
                for n in mine[:MAX_ACTIVITIES_PER_GUEST]
            ],
        }
        (in_stay if phase == "in_stay" else upcoming).append((start or datetime.max.replace(tzinfo=IST), end, item))

    upcoming.sort(key=lambda t: t[0])
    in_stay.sort(key=lambda t: t[1])  # leaving soonest first
    return {
        "active_when": _active_when(user),
        "payment_down": provider_down,
        "unread_activity": sum(len(v) for v in activity.values()),
        "in_stay": [item for _, _, item in in_stay],
        "upcoming": [item for _, _, item in upcoming],
    }


def _active_when(user: User) -> str:
    from app.services import agent_config_service

    return agent_config_service.get_config(user).booking.active_when


# -- activity: requests from guests who are booked --------------------------------------------------------


async def unread_count(db: AsyncSession, user_id: uuid.UUID) -> int:
    """The number on the tab and the sidebar. It is the list's own number, so it can never show a request the host
    has no row to clear it from (a guest whose stay is over, or who is no longer booked, stops counting)."""
    user = await db.get(User, user_id)
    if user is None:
        return 0
    return int((await list_active(db, user))["unread_activity"])


async def mark_activity_read(db: AsyncSession, user_id: uuid.UUID, lead_id: uuid.UUID) -> int:
    """The host has seen this guest's requests. Returns how many were cleared; 0 for a lead that is not theirs."""
    owned = await db.scalar(select(Lead.id).where(Lead.id == lead_id, Lead.user_id == user_id))
    if owned is None:
        return 0
    now = datetime.now(timezone.utc)
    result = await db.execute(
        update(Notification)
        .where(Notification.booking_lead_id == lead_id, Notification.status == "new")
        .values(status="read", responded_at=func.coalesce(Notification.responded_at, now))
    )
    await db.commit()
    return result.rowcount or 0


async def resolve_booking_lead(
    db: AsyncSession, property_id: uuid.UUID | None, call_session_id: uuid.UUID | None, guest_phone: str | None = None
) -> uuid.UUID | None:
    """The active booking (a lead id) the guest behind a new notification holds, or None. Looks the guest up by the
    number they rang from (or `guest_phone` when the notice has no call). Called for every notification that comes
    from a guest; never raises, because a notification must be saved whatever happens here."""
    try:
        user_id = None
        if property_id is not None:
            user_id = await db.scalar(select(Property.user_id).where(Property.id == property_id))
        phone = guest_phone
        if call_session_id is not None:
            session = await db.get(CallSession, call_session_id)
            if session is not None:
                user_id = user_id or session.user_id
                phone = phone or session.caller_number
            if not phone_key(phone):
                phone = await db.scalar(select(Lead.phone).where(Lead.call_session_id == call_session_id).limit(1))
        if user_id is None or not phone_key(phone):
            return None
        lead = await find_for_phone(db, user_id, phone)
        return lead.id if lead is not None else None
    except Exception:
        logger.exception("could not match a notification to a booked guest")
        await db.rollback()
        return None
