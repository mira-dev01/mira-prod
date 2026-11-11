"""Payment links and the date holds that come with them.

A guest who agrees a price is sent a Razorpay link (the host's own account) and
the dates are held for them. This module owns that whole lifecycle:

    create ──► active ──► paid          (guest paid; booking made / host asked to confirm)
                  ├─────► expired       (hold ran out unpaid; dates released)
                  └─────► cancelled     (host or Razorpay cancelled; dates released)

Safety rules, in the order they matter:
- NO DOUBLE BOOKING. Creating a hold takes a per-property advisory lock, checks
  availability (confirmed bookings, including other holds, which are mirrored
  into `bookings`) and inserts, all in one transaction. Two guests asking for
  the same dates at once cannot both get a link. If a booking still lands on a
  held/paid slot (an iCal import, a late payment) the host is escalated at HIGH
  urgency, never silently.
- THE AMOUNT IS NEVER THE MODEL'S. Callers pass the total Mira actually quoted;
  what the link charges is derived here from the host's payment rules.
- PAID MEANS RAZORPAY SAYS PAID. Status comes from fetching the link with the
  host's own keys, not from anything a guest or the model claims.
- EACH TRANSITION HAPPENS ONCE. Every state change is one atomic
  `UPDATE ... WHERE status = <from> RETURNING`, so two workers (or a poll racing
  an expiry) cannot both act, and a message is never sent twice.
- A hold is a row in `bookings` (kind "blocked", platform "mira_hold", no
  source_uid so the iCal sync leaves it alone), so every existing availability
  check, recommendation filter and calendar view already respects it.

Never raises into the call path: the entry points used by the voice tools
convert failures into PaymentError with a message the agent can say.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import and_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.integrations import razorpay_client, twilio_client
from app.integrations.razorpay_client import RazorpayError
from app.models.booking import Booking
from app.models.lead import Lead
from app.models.payment import HoldWaiter, PaymentRequest
from app.models.property import Property
from app.models.user import User
from app.services import (
    agent_config_service,
    billing_service,
    booking_confirmation,
    calendar_service,
    capability_service,
    notification_preferences_service,
    notification_service,
    ticket_service,
)
from app.utils.secrets import SecretsError
from app.utils.throttle import Throttle

logger = logging.getLogger(__name__)

HOLD_PLATFORM = "mira_hold"
RESERVATION_PLATFORM = "mira"
BLOCKING = ("active", "paid")  # statuses whose dates nobody else may take
RECENT_CLOSED_POLL = timedelta(days=2)  # keep watching a lapsed link in case cancelling it failed
SWEEP_BATCH = 300
_error_alerts = Throttle(timedelta(hours=6))  # one bad-keys nudge per host per 6h


class PaymentError(Exception):
    """A message the agent or host can be told as it stands."""


class DatesUnavailable(PaymentError):
    """Those dates are taken by a booking."""


class DatesOnHold(PaymentError):
    """Those dates are held for another guest who has not paid yet."""

    def __init__(self, message: str, hold: PaymentRequest):
        super().__init__(message)
        self.hold = hold


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _money(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def rupees(value) -> str:
    amount = _money(value)
    return f"₹{amount:,.0f}" if amount == amount.to_integral() else f"₹{amount:,.2f}"


def _first_name(name: str | None) -> str:
    """"Asha Rao" -> "Asha". A blank or whitespace-only name is "there"."""
    parts = (name or "").split()
    return parts[0] if parts else "there"


def _day(value: date) -> str:
    return value.strftime("%d %b %Y").lstrip("0")


async def payments_enabled_for(user: User | None) -> bool:
    """Whether a call for this host may take payments: Razorpay connected AND
    the host has not switched the "Online payments" capability off. The voice
    tool and the prompt both ask this one question, so the agent is never told
    about a tool it does not have (or the reverse). The capability lookup fails
    open to the registry default (on) like every other capability check, so a
    host who has connected Razorpay keeps payments through a database blip."""
    if user is None or not billing_service.is_connected(user):
        return False
    return await capability_service.is_capability_enabled(user.id, "online_payments")


# -- planning --------------------------------------------------------------


def plan_payment(
    user: User,
    property_: Property,
    total,
    check_in: date,
    *,
    option: str | None = None,
    split_due=None,
    force_host_confirm: bool = False,
) -> dict:
    """What a link for this stay charges, under the host's rules (their
    portfolio payment settings with this property's override laid over them).

    `option` is the GUEST's choice when the villa lets them choose: "full" pays everything now, "split"
    pays `split_due` (the exact advance worked out by the bill) now and the rest before check-in. Without
    it the host's own rule decides, as it always did. `force_host_confirm` makes the host (not the payment)
    confirm the booking, e.g. a shoot, which is confirmed after a call with the team."""
    config = agent_config_service.resolve_config(user, property_)
    total = _money(total)
    split = option == "split" and split_due is not None
    if split or (option is None and config.payment.kind == "split"):
        due = _money(split_due) if split else _money(total * config.payment.split_percent / 100)
        balance_on = max(date.today(), check_in - timedelta(days=config.payment.balance_due_days_before_checkin))
        kind = "advance"
    else:
        due, balance_on, kind = total, None, "full"
    return {
        "kind": kind,
        "total": total,
        "amount_due": due,
        "balance_due_on": balance_on,
        "hold_minutes": config.payment.hold_minutes,
        "confirm_by": "host" if force_host_confirm else config.booking.confirm_by,
    }


# -- reading ---------------------------------------------------------------


def _overlap(property_id, check_in: date, check_out: date):
    return and_(PaymentRequest.property_id == property_id, PaymentRequest.check_in < check_out, PaymentRequest.check_out > check_in)


async def find_blocking_hold(db: AsyncSession, property_id: uuid.UUID, check_in: date, check_out: date) -> PaymentRequest | None:
    """The unpaid-or-paid payment request whose dates overlap these, if any."""
    return await db.scalar(
        select(PaymentRequest)
        .where(_overlap(property_id, check_in, check_out), PaymentRequest.status.in_(BLOCKING))
        .order_by(PaymentRequest.created_at)
        .limit(1)
    )


async def list_requests(db: AsyncSession, user_id: uuid.UUID, limit: int = 100) -> list[dict]:
    rows = (
        await db.execute(
            select(PaymentRequest, Property.display_name, Property.name)
            .join(Property, Property.id == PaymentRequest.property_id)
            .where(PaymentRequest.user_id == user_id)
            .order_by(PaymentRequest.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "id": r.id,
            "property_id": r.property_id,
            "property_name": display or name,
            "guest_name": r.guest_name,
            "guest_phone": r.guest_phone,
            "check_in": r.check_in,
            "check_out": r.check_out,
            "total_amount": r.total_amount,
            "amount_due": r.amount_due,
            "kind": r.kind,
            "status": r.status,
            "hold_expires_at": r.hold_expires_at,
            "paid_at": r.paid_at,
            "short_url": r.short_url if r.status == "active" else None,
            "created_at": r.created_at,
        }
        for r, display, name in rows
    ]


# -- creating --------------------------------------------------------------


async def create_payment_request(
    db: AsyncSession,
    user: User,
    property_: Property,
    *,
    check_in: date,
    check_out: date,
    total,
    guest_name: str | None,
    guest_phone: str | None,
    lead_id: uuid.UUID | None = None,
    call_session_id: uuid.UUID | None = None,
    payment_option: str | None = None,
    split_due=None,
    details: dict | None = None,
    force_host_confirm: bool = False,
) -> PaymentRequest:
    """Holds the dates and sends the guest a payment link. Raises PaymentError
    (or DatesUnavailable / DatesOnHold) with a message safe to say aloud."""
    try:
        credentials = billing_service.get_credentials(user)
    except SecretsError as exc:
        raise PaymentError(str(exc))
    if credentials is None:
        raise PaymentError("Online payment isn't set up for this host.")
    guest_name = (guest_name or "").strip() or None
    if not guest_phone:
        raise PaymentError("I need the guest's phone number to send the payment link.")
    if check_out <= check_in or check_in < date.today():
        raise PaymentError("Those dates aren't valid for a booking.")
    plan = plan_payment(
        user, property_, total, check_in, option=payment_option, split_due=split_due, force_host_confirm=force_host_confirm
    )
    if plan["total"] <= 0 or plan["amount_due"] <= 0:
        raise PaymentError("There is no price to collect for these dates yet.")

    expires = _now() + timedelta(minutes=plan["hold_minutes"])
    # One transaction per property at a time: check, then hold. The lock is
    # released on commit/rollback, so a second guest waits here for a few
    # milliseconds and then sees the first guest's hold.
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": f"hold:{property_.id}"})

    mine = await db.scalar(
        select(PaymentRequest).where(
            PaymentRequest.property_id == property_.id,
            PaymentRequest.status == "active",
            PaymentRequest.guest_phone == guest_phone,
            PaymentRequest.check_in == check_in,
            PaymentRequest.check_out == check_out,
        )
    )
    if mine is not None:  # the guest asked again: same link, same hold
        await db.commit()  # nothing pending; releases the lock and keeps `mine` readable
        return mine

    if not await calendar_service.is_available(db, property_.id, check_in, check_out):
        hold = await find_blocking_hold(db, property_.id, check_in, check_out)
        await db.commit()  # likewise: releases the lock, keeps `hold` readable for the caller
        if hold is not None:
            raise DatesOnHold("Those dates are currently on hold for another guest.", hold)
        raise DatesUnavailable("Those dates are no longer available.")

    booking = Booking(
        property_id=property_.id,
        guest_name=guest_name,
        guest_phone=guest_phone,
        check_in=check_in,
        check_out=check_out,
        platform=HOLD_PLATFORM,
        source_uid=None,  # the iCal sync deletes bookings missing from the feed; it must never see this one
        status="confirmed",
        kind="blocked",
    )
    db.add(booking)
    await db.flush()
    request = PaymentRequest(
        user_id=user.id,
        property_id=property_.id,
        lead_id=lead_id,
        call_session_id=call_session_id,
        booking_id=booking.id,
        guest_name=guest_name,
        guest_phone=guest_phone,
        check_in=check_in,
        check_out=check_out,
        total_amount=plan["total"],
        amount_due=plan["amount_due"],
        kind=plan["kind"],
        confirm_by=plan["confirm_by"],
        balance_due_on=plan["balance_due_on"],
        status="active",
        hold_expires_at=expires,
        details=details,
    )
    db.add(request)
    await db.commit()

    key_id, key_secret = credentials
    try:
        link = await razorpay_client.create_payment_link(
            key_id,
            key_secret,
            amount_rupees=plan["amount_due"],
            reference_id=request.id.hex,
            description=f"{property_.display_name or property_.name}: {_day(check_in)} to {_day(check_out)}",
            expire_by=int(max(expires, _now() + timedelta(seconds=razorpay_client.MIN_LINK_LIFETIME_SECONDS)).timestamp()),
            customer_name=guest_name,
            notes={"mira_request_id": str(request.id), "property": property_.name, "check_in": check_in, "check_out": check_out},
        )
    except RazorpayError as exc:
        await _release(db, request.id, "cancelled", error=str(exc))
        raise PaymentError(
            "I couldn't create the payment link just now."
            if not exc.bad_credentials
            else "Online payment isn't working for this host right now."
        )
    request.razorpay_link_id, request.short_url = link.id, link.short_url
    await db.commit()
    await db.refresh(request)

    await _send_request_message(db, user, property_, request, plan["hold_minutes"])
    return request


def _values(user: User, property_: Property, request: PaymentRequest, hold_minutes: int = 0) -> dict[str, str]:
    balance = request.total_amount - request.amount_due
    note = ""
    if request.kind == "advance" and balance > 0 and request.balance_due_on:
        note = f"The remaining {rupees(balance)} is due by {_day(request.balance_due_on)}."
    return {
        "guest_name": _first_name(request.guest_name),
        "property": property_.display_name or property_.name,
        "check_in": _day(request.check_in),
        "check_out": _day(request.check_out),
        "amount": rupees(request.amount_due),
        "total": rupees(request.total_amount),
        "balance_note": note,
        "link": request.short_url or "",
        "hold_minutes": str(hold_minutes),
        "host_name": user.business_name or user.name or "your host",
    }


async def _send_request_message(db: AsyncSession, user: User, property_: Property, request: PaymentRequest, hold_minutes: int) -> None:
    body = billing_service.render(billing_service.template_text(user, "payment_request"), _values(user, property_, request, hold_minutes))
    sid = await twilio_client.send_whatsapp_tracked(request.guest_phone, body)
    if sid:
        request.link_sent_at, request.link_message_sid = _now(), sid
        await db.commit()
        return
    # Not accepted at all: the guest was (or will be) told it was sent, so make sure the host sends it.
    await _link_not_delivered(db, request, "WhatsApp wouldn't take the message.")


async def _link_not_delivered(db: AsyncSession, request: PaymentRequest, why: str) -> None:
    """The guest has not got the link: the host is told, at high urgency, with
    the link itself so they can send it another way."""
    who = request.guest_name or request.guest_phone or "the guest"
    detail = (
        f"{why} WhatsApp only delivers a business's first message once the guest has messaged that number. "
        f"Send {who} this link another way ({rupees(request.amount_due)}, dates held until "
        f"{request.hold_expires_at.astimezone(timezone(timedelta(hours=5, minutes=30))):%I:%M %p} IST): {request.short_url}"
    )
    notification = await notification_service.create_notification(
        db, channel="system", message=f"The payment link didn't reach {who}. {detail}", property_id=request.property_id,
        call_session_id=request.call_session_id, urgency="high",
    )
    await ticket_service.open_ticket(
        db, kind="payment", summary=f"Send the payment link to {who}", detail=detail, urgency="high", user_id=request.user_id,
        property_id=request.property_id, call_session_id=request.call_session_id, notification_id=notification.id,
    )


# -- releasing a hold ------------------------------------------------------


async def _release(db: AsyncSession, request_id: uuid.UUID, new_status: str, *, error: str | None = None) -> PaymentRequest | None:
    """active -> expired|cancelled exactly once; frees the dates. Returns the
    request if THIS call released it, else None (someone else got there first)."""
    claimed = (
        await db.execute(
            update(PaymentRequest)
            .where(PaymentRequest.id == request_id, PaymentRequest.status == "active")
            .values(status=new_status, last_error=error)
            .returning(PaymentRequest.id, PaymentRequest.booking_id)
        )
    ).first()
    if claimed is None:
        await db.rollback()
        return None
    _, booking_id = claimed
    if booking_id is not None:
        booking = await db.get(Booking, booking_id)
        if booking is not None and booking.platform == HOLD_PLATFORM:  # never touch a real reservation
            await db.delete(booking)
    await db.commit()
    return await db.scalar(select(PaymentRequest).where(PaymentRequest.id == request_id))


async def _close_link(db: AsyncSession, request: PaymentRequest, credentials: tuple[str, str]) -> str | None:
    """Cancel the Razorpay link so it cannot be paid late. Returns the link's
    status if it turned out to be paid (so the caller can handle a late payment)."""
    if not request.razorpay_link_id or request.link_closed:
        return None
    key_id, key_secret = credentials
    try:
        link = await razorpay_client.cancel_payment_link(key_id, key_secret, request.razorpay_link_id)
        closed = link.status in ("cancelled", "expired")
    except RazorpayError:
        # Refused: it is paid, already expired, or Razorpay is unreachable. Ask.
        try:
            link = await razorpay_client.fetch_payment_link(key_id, key_secret, request.razorpay_link_id)
        except RazorpayError:
            return None
        if link.status == "paid":
            request.razorpay_payment_id = link.payment_id
            return "paid"
        closed = link.status in ("cancelled", "expired")
    if closed:
        request.link_closed = True
        await db.commit()
    return None


async def cancel_request(db: AsyncSession, user: User, request_id: uuid.UUID) -> bool:
    """The host gives up a hold (e.g. the guest changed their mind)."""
    request = await db.scalar(select(PaymentRequest).where(PaymentRequest.id == request_id, PaymentRequest.user_id == user.id))
    if request is None:
        raise PaymentError("Payment request not found.")
    if request.status != "active":
        raise PaymentError("This one is already " + request.status + ".")
    try:
        credentials = billing_service.get_credentials(user)
    except SecretsError as exc:
        raise PaymentError(str(exc))
    # Close the link first: if the guest has just paid, the host must not release the dates.
    if credentials and await _close_link(db, request, credentials) == "paid":
        await _mark_paid(db, request.id, request.razorpay_payment_id)
        raise PaymentError("The guest has just paid, so these dates are now booked.")
    released = await _release(db, request_id, "cancelled")
    if released is not None:
        await _notify_waiters(db, released)
    return released is not None


async def refresh_request(db: AsyncSession, request_id: uuid.UUID) -> tuple[PaymentRequest, str | None, bool]:
    """Asks the payment provider about ONE request right now (the poller only comes round once a minute),
    so the agent can learn mid-call that the guest has paid. A paid link becomes a confirmed booking
    here, exactly as the poller would do it. Returns (request, last failed attempt or None, provider_down).
    provider_down means the provider could not be asked: the caller must not say paid or unpaid."""
    request = await db.scalar(select(PaymentRequest).where(PaymentRequest.id == request_id))
    if request is None:
        raise PaymentError("Payment request not found.")
    if request.status != "active" or not request.razorpay_link_id:
        return request, None, False
    user = await db.get(User, request.user_id)
    try:
        credentials = billing_service.get_credentials(user) if user else None
    except SecretsError:
        credentials = None
    if credentials is None:
        return request, None, True
    try:
        link = await razorpay_client.fetch_payment_link(*credentials, request.razorpay_link_id)
    except RazorpayError:
        return request, None, True
    if link.status == "paid":
        await _mark_paid(db, request.id, link.payment_id or request.razorpay_payment_id)
    elif link.status == "cancelled":
        released = await _release(db, request.id, "cancelled")
        if released is not None:
            await _notify_waiters(db, released)
    request = await db.scalar(select(PaymentRequest).where(PaymentRequest.id == request_id).execution_options(populate_existing=True))
    return request, link.last_attempt, False


# -- waiting guests --------------------------------------------------------


async def register_waiter(
    db: AsyncSession,
    hold: PaymentRequest,
    *,
    guest_name: str | None,
    guest_phone: str,
    lead_id: uuid.UUID | None,
    call_session_id: uuid.UUID | None,
) -> bool:
    """Remembers a guest who wants held dates, so they hear if the hold lapses.
    True if newly added (asking twice is one waiter)."""
    result = await db.execute(
        pg_insert(HoldWaiter)
        .values(
            payment_request_id=hold.id,
            user_id=hold.user_id,
            guest_name=guest_name,
            guest_phone=guest_phone,
            lead_id=lead_id,
            call_session_id=call_session_id,
        )
        .on_conflict_do_nothing(constraint="uq_hold_waiter_request_phone")
        .returning(HoldWaiter.id)
    )
    added = result.first() is not None
    await db.commit()
    return added


async def escalate_hold_interest(
    db: AsyncSession, hold: PaymentRequest, property_: Property, *, guest_name: str | None, guest_phone: str | None, call_session_id: uuid.UUID | None
) -> None:
    """Tells the host, at HIGH urgency, that a second guest wants dates held for
    the first -- so they can chase the first guest's payment or book the second."""
    host = await db.get(User, hold.user_id)
    name = property_.display_name or property_.name
    first = hold.guest_name or hold.guest_phone or "another guest"
    second = guest_name or guest_phone or "a second guest"
    dates = f"{_day(hold.check_in)} to {_day(hold.check_out)}"
    until = hold.hold_expires_at.astimezone(timezone(timedelta(hours=5, minutes=30))).strftime("%I:%M %p").lstrip("0")
    message = (
        f"{second} also wants {name} for {dates}, which is on hold for {first} until {until} IST "
        f"({rupees(hold.amount_due)} unpaid). Chase {first}'s payment, or book {second}."
    )
    notification = await notification_service.create_notification(
        db, channel="escalation", message=message, property_id=hold.property_id, call_session_id=call_session_id, urgency="high"
    )
    await ticket_service.open_ticket(
        db,
        kind="payment",
        summary=f"Two guests want {name} for {dates}",
        detail=message,
        urgency="high",
        user_id=hold.user_id,
        property_id=hold.property_id,
        call_session_id=call_session_id,
        notification_id=notification.id,
    )
    phone = notification_preferences_service.host_transfer_phone(host, property_) if host else None
    if phone:
        await twilio_client.send_whatsapp_best_effort(phone, f"Mira: {message}")


async def _notify_waiters(db: AsyncSession, released: PaymentRequest) -> int:
    """The hold lapsed: tell every guest who was promised an update. Each is
    claimed with one atomic UPDATE so they are told exactly once."""
    property_ = await db.get(Property, released.property_id)
    name = (property_.display_name or property_.name) if property_ else "the property"
    waiters = list((await db.scalars(select(HoldWaiter).where(HoldWaiter.payment_request_id == released.id, HoldWaiter.notified_at.is_(None)))).all())
    told = 0
    for waiter in waiters:
        claimed = (
            await db.execute(
                update(HoldWaiter).where(HoldWaiter.id == waiter.id, HoldWaiter.notified_at.is_(None)).values(notified_at=_now()).returning(HoldWaiter.id)
            )
        ).first()
        await db.commit()
        if claimed is None:
            continue
        first = _first_name(waiter.guest_name)
        body = (
            f"Hi {first}! Good news: {name} is open again for {_day(released.check_in)} to {_day(released.check_out)}. "
            "We can't hold it without payment, so call us or reply here if you'd like it."
        )
        if await twilio_client.send_whatsapp_best_effort(waiter.guest_phone, body):
            told += 1
    return told


# -- paid ------------------------------------------------------------------


async def _mark_paid(db: AsyncSession, request_id: uuid.UUID, payment_id: str | None) -> bool:
    """active -> paid exactly once, then everything that follows a payment."""
    claimed = (
        await db.execute(
            update(PaymentRequest)
            .where(PaymentRequest.id == request_id, PaymentRequest.status == "active")
            .values(status="paid", paid_at=_now(), razorpay_payment_id=payment_id, link_closed=True)
            .returning(PaymentRequest.id)
        )
    ).first()
    if claimed is None:
        await db.rollback()
        return False
    await db.commit()
    await _after_payment(db, request_id)
    return True


async def _after_payment(db: AsyncSession, request_id: uuid.UUID) -> None:
    """Everything that follows a payment. Converting the hold into a reservation
    can safely be repeated (the repair sweep does, if it was interrupted);
    telling people is claimed once, so nobody is messaged twice, and each
    message is attempted on its own so one failing cannot stop the others."""
    request = await db.scalar(select(PaymentRequest).where(PaymentRequest.id == request_id))
    user = await db.get(User, request.user_id)
    property_ = await db.get(Property, request.property_id)
    confirmed = request.confirm_by == "payment"

    booking = await db.get(Booking, request.booking_id) if request.booking_id else None
    if booking is None:  # the hold row went missing: the money is in, so the dates must be blocked again
        booking = Booking(property_id=request.property_id, check_in=request.check_in, check_out=request.check_out)
        db.add(booking)
    booking.kind, booking.platform, booking.status = "reservation", RESERVATION_PLATFORM, "confirmed"
    booking.guest_name, booking.guest_phone = request.guest_name, request.guest_phone
    booking.final_booking_price = request.total_amount
    booking.initial_price = request.total_amount
    booking.currency = request.currency
    booking.price_source = "mira_conversation"
    booking.price_status = "confirmed" if confirmed else "pending_confirmation"
    booking.price_confirmed_at = request.paid_at if confirmed else None
    booking.price_confirmed_by = "mira" if confirmed else None
    booking.mira_attribution_status = "confirmed"
    booking.mira_attribution_call_session_id = request.call_session_id
    booking.mira_attribution_lead_id = request.lead_id
    await db.flush()
    request.booking_id = booking.id
    if (request.details or {}).get("bill") and not (request.details or {}).get("stay_code"):
        # The guest's four-digit stay code exists from the moment the money is in, so the agent can read it
        # out when it checks the payment (and the guest can quote it on a bank transfer).
        request.details = {**request.details, "stay_code": booking_confirmation.new_stay_code()}
    if confirmed and request.lead_id:
        lead = await db.get(Lead, request.lead_id)
        if lead is not None:
            lead.status = "booked"
    await db.commit()

    told = (
        await db.execute(
            update(PaymentRequest)
            .where(PaymentRequest.id == request_id, PaymentRequest.host_notified_at.is_(None))
            .values(host_notified_at=_now())
            .returning(PaymentRequest.id)
        )
    ).first()
    await db.commit()
    if told is None:
        return  # already told (or being told by another worker)

    name = property_.display_name or property_.name
    dates = f"{_day(request.check_in)} to {_day(request.check_out)}"
    who = request.guest_name or request.guest_phone or "A guest"
    verdict = "The booking is confirmed." if confirmed else "Please confirm the booking."
    message = f"Payment received: {rupees(request.amount_due)} from {who} for {name}, {dates}. {verdict}"
    try:
        notification = await notification_service.create_notification(
            db, channel="system", message=message, property_id=request.property_id, call_session_id=request.call_session_id, urgency="high"
        )
        if not confirmed:
            await ticket_service.open_ticket(
                db, kind="payment", summary=f"Confirm the booking: {who} paid for {name}", detail=message, urgency="high",
                user_id=request.user_id, property_id=request.property_id, call_session_id=request.call_session_id,
                notification_id=notification.id,
            )
    except Exception:
        logger.exception("payment received but the host alert failed request=%s", request_id)
        await db.rollback()
    try:
        if billing_service.notify_host_on_payment(user):
            phone = notification_preferences_service.host_transfer_phone(user, property_)
            if phone:
                await twilio_client.send_whatsapp_best_effort(phone, f"Mira: {message}")
    except Exception:
        logger.exception("payment received but the host WhatsApp failed request=%s", request_id)
    try:
        full_bill = bool((request.details or {}).get("bill"))
        body = (
            booking_confirmation.confirmation_text(property_, request)
            if full_bill
            else billing_service.render(billing_service.template_text(user, "payment_success"), _values(user, property_, request))
        )
        if request.guest_phone and await twilio_client.send_whatsapp_best_effort(request.guest_phone, body):
            await db.execute(update(PaymentRequest).where(PaymentRequest.id == request_id).values(success_sent_at=_now()))
            await db.commit()
            if full_bill and (extra := booking_confirmation.stay_code_text(request)):
                await twilio_client.send_whatsapp_best_effort(request.guest_phone, extra)
    except Exception:
        logger.exception("payment received but the guest receipt failed request=%s", request_id)


async def _handle_late_payment(db: AsyncSession, request_id: uuid.UUID, payment_id: str | None) -> None:
    """The guest paid a link whose hold had already lapsed. If the dates are
    still free the booking stands; if someone else has them, the host is told at
    once (they owe the guest a refund or an alternative)."""
    request = await db.scalar(select(PaymentRequest).where(PaymentRequest.id == request_id))
    if request is None:
        return
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": f"hold:{request.property_id}"})
    if await calendar_service.is_available(db, request.property_id, request.check_in, request.check_out):
        claimed = (
            await db.execute(
                update(PaymentRequest)
                .where(PaymentRequest.id == request_id, PaymentRequest.status.in_(("expired", "cancelled")))
                .values(status="active", razorpay_payment_id=payment_id)
                .returning(PaymentRequest.id)
            )
        ).first()
        if claimed is None:
            await db.rollback()
            return
        # Block the dates again in this same locked step, so nobody can take them
        # between "reactivated" and "booked".
        booking = Booking(
            property_id=request.property_id, guest_name=request.guest_name, guest_phone=request.guest_phone,
            check_in=request.check_in, check_out=request.check_out, platform=HOLD_PLATFORM, source_uid=None,
            status="confirmed", kind="blocked",
        )
        db.add(booking)
        await db.flush()
        await db.execute(update(PaymentRequest).where(PaymentRequest.id == request_id).values(booking_id=booking.id))
        await db.commit()
        await _mark_paid(db, request_id, payment_id)
        return
    claimed = (
        await db.execute(
            update(PaymentRequest)
            .where(PaymentRequest.id == request_id, PaymentRequest.conflict_notified_at.is_(None))
            .values(conflict_notified_at=_now(), razorpay_payment_id=payment_id, link_closed=True, last_error="Paid after the hold ended; the dates were taken.")
            .returning(PaymentRequest.id)
        )
    ).first()
    await db.commit()
    if claimed is not None:
        await _escalate_conflict(
            db,
            request_id,
            f"{request.guest_name or request.guest_phone or 'A guest'} paid {rupees(request.amount_due)} AFTER the hold on "
            f"{_day(request.check_in)} to {_day(request.check_out)} ended, and those dates are now taken. "
            "Refund them in Razorpay or find them other dates.",
        )


async def _escalate_conflict(db: AsyncSession, request_id: uuid.UUID, message: str) -> None:
    request = await db.scalar(select(PaymentRequest).where(PaymentRequest.id == request_id))
    if request is None:
        return
    property_ = await db.get(Property, request.property_id)
    host = await db.get(User, request.user_id)
    notification = await notification_service.create_notification(
        db, channel="escalation", message=f"Double booking: {message}", property_id=request.property_id, call_session_id=request.call_session_id, urgency="high"
    )
    await ticket_service.open_ticket(
        db,
        kind="payment",
        summary=f"Double booking at {(property_.display_name or property_.name) if property_ else 'a property'}",
        detail=message,
        urgency="high",
        user_id=request.user_id,
        property_id=request.property_id,
        call_session_id=request.call_session_id,
        notification_id=notification.id,
    )
    phone = notification_preferences_service.host_transfer_phone(host, property_) if host else None
    if phone:
        await twilio_client.send_whatsapp_best_effort(phone, f"Mira: DOUBLE BOOKING. {message}")


# -- the sweep (runs every minute) ----------------------------------------


async def _poll(db: AsyncSession) -> int:
    """Ask Razorpay about every link that could still be paid. Works on plain
    values (a rollback expires ORM objects held across iterations), and one
    request failing never stops the rest."""
    cutoff = _now() - RECENT_CLOSED_POLL
    rows = (
        await db.execute(
            select(
                PaymentRequest.id, PaymentRequest.user_id, PaymentRequest.property_id,
                PaymentRequest.razorpay_link_id, PaymentRequest.status, PaymentRequest.razorpay_payment_id,
            )
            .where(
                PaymentRequest.razorpay_link_id.is_not(None),
                (PaymentRequest.status == "active")
                | ((PaymentRequest.status.in_(("expired", "cancelled"))) & (PaymentRequest.link_closed.is_(False)) & (PaymentRequest.created_at > cutoff)),
            )
            .order_by(PaymentRequest.created_at)
            .limit(SWEEP_BATCH)
        )
    ).all()
    paid = 0
    credentials_by_user: dict[uuid.UUID, tuple[str, str] | None] = {}
    for request_id, user_id, property_id, link_id, status, known_payment_id in rows:
        try:
            if user_id not in credentials_by_user:
                user = await db.get(User, user_id)
                try:
                    credentials_by_user[user_id] = billing_service.get_credentials(user) if user else None
                except SecretsError:
                    credentials_by_user[user_id] = None
            credentials = credentials_by_user[user_id]
            if credentials is None:
                continue
            try:
                link = await razorpay_client.fetch_payment_link(*credentials, link_id)
            except RazorpayError as exc:
                await _link_trouble(db, request_id, user_id, property_id, exc)
                continue
            if link.status == "paid":
                if status == "active":
                    paid += 1 if await _mark_paid(db, request_id, link.payment_id or known_payment_id) else 0
                else:
                    await _handle_late_payment(db, request_id, link.payment_id)
            elif link.status in ("expired", "cancelled"):
                if status == "active" and link.status == "cancelled":
                    released = await _release(db, request_id, "cancelled")  # cancelled in Razorpay's dashboard
                    if released is not None:
                        await _notify_waiters(db, released)
                await db.execute(update(PaymentRequest).where(PaymentRequest.id == request_id).values(link_closed=True))
                await db.commit()
        except Exception:
            logger.exception("payment poll failed for request=%s; carrying on with the rest", request_id)
            await db.rollback()
    return paid


async def _link_trouble(db: AsyncSession, request_id, user_id, property_id, exc: RazorpayError) -> None:
    """Razorpay would not answer for this host. Say so once in a while."""
    logger.warning("razorpay poll failed request=%s: %s", request_id, exc)
    if not exc.bad_credentials:
        return
    send, _ = _error_alerts.take(str(user_id))
    if send:
        await notification_service.create_notification(
            db,
            channel="system",
            message="Razorpay isn't accepting your saved keys, so Mira can't see whether guests have paid. Re-enter them in Settings → Billing.",
            property_id=property_id,
            urgency="high",
        )


async def _expire_lapsed(db: AsyncSession) -> int:
    ids = list(
        (
            await db.scalars(
                select(PaymentRequest.id).where(PaymentRequest.status == "active", PaymentRequest.hold_expires_at <= _now()).limit(SWEEP_BATCH)
            )
        ).all()
    )
    expired = 0
    for request_id in ids:
        try:
            request = await db.scalar(select(PaymentRequest).where(PaymentRequest.id == request_id))
            if request is None or request.status != "active":
                continue
            user = await db.get(User, request.user_id)
            credentials = None
            try:
                credentials = billing_service.get_credentials(user) if user else None
            except SecretsError:
                pass
            if credentials is not None and request.razorpay_link_id:
                # Close the link BEFORE releasing the dates, so a payment cannot slip in between.
                if await _close_link(db, request, credentials) == "paid":
                    await _mark_paid(db, request_id, request.razorpay_payment_id)
                    continue
            released = await _release(db, request_id, "expired")
            if released is not None:
                expired += 1
                await _notify_waiters(db, released)
        except Exception:
            logger.exception("hold expiry failed for request=%s; carrying on with the rest", request_id)
            await db.rollback()
    return expired


async def _detect_conflicts(db: AsyncSession) -> int:
    """Another booking has appeared on dates a guest is holding or has paid for
    (an iCal import from Airbnb, a booking the host added by hand)."""
    rows = (
        await db.execute(
            select(
                PaymentRequest.id, PaymentRequest.property_id, PaymentRequest.check_in, PaymentRequest.check_out,
                PaymentRequest.booking_id, PaymentRequest.status, PaymentRequest.guest_name, PaymentRequest.guest_phone,
            )
            .where(PaymentRequest.status.in_(BLOCKING), PaymentRequest.conflict_notified_at.is_(None))
            .limit(SWEEP_BATCH)
        )
    ).all()
    found = 0
    for request_id, property_id, check_in, check_out, booking_id, status, guest_name, guest_phone in rows:
        try:
            other = await db.scalar(
                select(Booking.id)
                .where(
                    Booking.property_id == property_id,
                    Booking.status == "confirmed",
                    Booking.check_in < check_out,
                    Booking.check_out > check_in,
                    Booking.id != booking_id,
                )
                .limit(1)
            )
            if other is None:
                continue
            claimed = (
                await db.execute(
                    update(PaymentRequest)
                    .where(PaymentRequest.id == request_id, PaymentRequest.conflict_notified_at.is_(None))
                    .values(conflict_notified_at=_now())
                    .returning(PaymentRequest.id)
                )
            ).first()
            await db.commit()
            if claimed is not None:
                found += 1
                state = "has paid for" if status == "paid" else "is holding"
                await _escalate_conflict(
                    db,
                    request_id,
                    f"Another booking now covers {_day(check_in)} to {_day(check_out)}, but "
                    f"{guest_name or guest_phone or 'a guest'} {state} those dates. Sort out which guest keeps them.",
                )
        except Exception:
            logger.exception("conflict check failed for request=%s; carrying on with the rest", request_id)
            await db.rollback()
    return found


async def _repair_paid(db: AsyncSession) -> int:
    """A payment is marked paid in one step and finished in the next (booking,
    alerts, receipt). If the process died or an error hit in between, the money
    is in but the dates still show as a hold. Finish those; the alerts inside
    are claimed once, so nobody is told twice."""
    ids = list(
        (
            await db.scalars(
                select(PaymentRequest.id)
                .outerjoin(Booking, Booking.id == PaymentRequest.booking_id)
                .where(
                    PaymentRequest.status == "paid",
                    PaymentRequest.paid_at < _now() - timedelta(seconds=90),
                    (Booking.id.is_(None)) | (Booking.platform == HOLD_PLATFORM) | (PaymentRequest.host_notified_at.is_(None)),
                )
                .limit(SWEEP_BATCH)
            )
        ).all()
    )
    repaired = 0
    for request_id in ids:
        try:
            await _after_payment(db, request_id)
            repaired += 1
        except Exception:
            logger.exception("could not finish paid request=%s; will retry", request_id)
            await db.rollback()
    return repaired


async def _check_link_delivery(db: AsyncSession) -> int:
    """Twilio accepting a WhatsApp message is not the guest receiving it: a
    freeform message to someone who has not messaged the business in the last
    24 hours is accepted and then fails. Look at what became of each link
    message; if it did not arrive, the host is told while the hold is still on."""
    rows = (
        await db.execute(
            select(PaymentRequest.id, PaymentRequest.link_message_sid, PaymentRequest.link_sent_at)
            .where(
                PaymentRequest.status == "active",
                PaymentRequest.link_message_sid.is_not(None),
                PaymentRequest.link_delivery_checked_at.is_(None),
                PaymentRequest.link_sent_at < _now() - timedelta(seconds=20),
            )
            .limit(SWEEP_BATCH)
        )
    ).all()
    failures = 0
    for request_id, sid, sent_at in rows:
        try:
            try:
                status, error_code = await twilio_client.fetch_message_status(sid)
            except Exception:
                logger.warning("could not check delivery of payment link message request=%s", request_id)
                continue
            if status in ("delivered", "read"):
                outcome = "ok"
            elif status in ("failed", "undelivered", "canceled"):
                outcome = "failed"
            elif sent_at < _now() - timedelta(minutes=5):
                outcome = "ok"  # still on its way (e.g. phone off); stop watching, it will arrive
            else:
                continue
            claimed = (
                await db.execute(
                    update(PaymentRequest)
                    .where(PaymentRequest.id == request_id, PaymentRequest.link_delivery_checked_at.is_(None))
                    .values(link_delivery_checked_at=_now())
                    .returning(PaymentRequest.id)
                )
            ).first()
            await db.commit()
            if outcome == "failed" and claimed is not None:
                failures += 1
                request = await db.scalar(select(PaymentRequest).where(PaymentRequest.id == request_id))
                await _link_not_delivered(db, request, f"WhatsApp reported the message as {status}" + (f" (error {error_code})." if error_code else "."))
        except Exception:
            logger.exception("delivery check failed for request=%s; carrying on with the rest", request_id)
            await db.rollback()
    return failures


async def process_payments() -> dict:
    """The once-a-minute sweep. Never raises, and no step can stop another: each
    runs in its own session, so one failing leaves the rest working."""
    result = {"paid": 0, "expired": 0, "conflicts": 0, "repaired": 0, "undelivered": 0}
    steps = (
        ("paid", _poll),
        ("expired", _expire_lapsed),
        ("conflicts", _detect_conflicts),
        ("repaired", _repair_paid),
        ("undelivered", _check_link_delivery),
    )
    for key, step in steps:
        try:
            async with AsyncSessionLocal() as db:
                result[key] = await step(db)
        except Exception:
            logger.exception("payment sweep step %s failed; the other steps still ran", key)
    return result


