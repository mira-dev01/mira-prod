"""SPACEZ ONLY. Early check-in / late check-out for a guest who ALREADY has a confirmed booking.

The flow (the guest rings back after booking):

  1. On the call the guest asks for an early check-in and/or late check-out. `request_early_late_checkout`
     (voice tool, app/voice/tools.py) lands in `handle_request`: it finds the guest's paid booking by the
     number they are calling from, reads the flat fee from the villa's Spacez terms, stores a
     SpacezStayRequest, opens a ticket for the team (kind "stay_request") and alerts them. The agent tells
     the guest the charge and that the team will check; she promises nothing.
  2. The team answers yes or no on the ticket (Leads -> Tickets):
       * `approve` -> a Razorpay link for the flat fee goes to the guest on WhatsApp;
       * `decline` -> the guest is told on WhatsApp that the team cannot do it.
  3. `sweep` (every minute, from payment_service.process_payments) watches an approved link: paid ->
     the team and the guest are told; expired/cancelled -> the team is told.

This is deliberately NOT a PaymentRequest: that table holds a date hold on the calendar, and an add-on
to a stay that is already booked must never touch the calendar. Money is still only "paid" when the
payment provider says so.

Nothing here raises into a live call: `handle_request` returns text the agent can say.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import razorpay_client, twilio_client
from app.integrations.razorpay_client import RazorpayError
from app.models.payment import PaymentRequest
from app.models.property import Property
from app.models.spacez_stay_request import SpacezStayRequest
from app.models.user import User
from app.schemas.tool import ToolBusinessError
from app.services import (
    billing_service,
    notification_preferences_service,
    notification_service,
    spacez_booking_confirmation,
    spacez_pricing_terms,
    ticket_service,
    whatsapp_templates,
)
from app.utils.dates import today_ist
from app.utils.secrets import SecretsError

logger = logging.getLogger(__name__)

TICKET_KIND = "stay_request"
OPEN_STATUSES = ("pending", "awaiting_payment")
LINK_LIFETIME = timedelta(hours=48)
SWEEP_BATCH = 100


class StayRequestError(Exception):
    """A message the host (dashboard) can be told as it stands."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _rupees(value) -> str:
    return spacez_booking_confirmation.rupees(value)


def _what(early: bool, late: bool) -> str:
    return " and ".join(x for x, on in (("early check-in", early), ("late check-out", late)) if on)


def _first_name(name: str | None) -> str:
    parts = (name or "").split()
    return parts[0] if parts else "there"


def _day(value: date) -> str:
    return value.strftime("%d %b %Y").lstrip("0")


def _phone_key(value: str | None) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())[-10:]


async def host_has_spacez_villas(db: AsyncSession, host_user_id: uuid.UUID) -> bool:
    """Whether this host has any villa with Spacez booking terms (the call then gets the early/late tool)."""
    return bool(
        await db.scalar(select(Property.id).where(Property.user_id == host_user_id, Property.pricing_terms.is_not(None)).limit(1))
    )


# -- finding the guest's booking -------------------------------------------------------------------


async def find_confirmed_booking(db: AsyncSession, host_user_id: uuid.UUID, guest_phone: str | None) -> PaymentRequest | None:
    """The guest's paid Spacez booking (made from a full bill) that has not ended yet, soonest first. Matched on
    the last 10 digits of EITHER the number the link was sent to or the number the guest was calling from when
    they booked (they can differ), scoped to this host."""
    key = _phone_key(guest_phone)
    if len(key) < 10:
        return None
    stmt = (
        select(PaymentRequest)
        .where(PaymentRequest.user_id == host_user_id, PaymentRequest.status == "paid", PaymentRequest.check_out >= today_ist())
        .order_by(PaymentRequest.check_in)
    )
    for request in (await db.scalars(stmt)).all():
        details = request.details or {}
        if details.get("bill") and key in (_phone_key(request.guest_phone), _phone_key(details.get("caller_phone"))):
            return request
    return None


# -- the guest asks (on a call) ------------------------------------------------------------------


async def handle_request(
    db: AsyncSession,
    *,
    host_user_id: uuid.UUID,
    call_session_id: uuid.UUID | None,
    guest_phone: str | None,
    early_checkin: bool,
    late_checkout: bool,
    note: str | None = None,
) -> str:
    """Records the request, opens the team's ticket and tells the agent what to say."""
    if not (early_checkin or late_checkout):
        return ToolBusinessError("Say whether the guest wants an early check-in, a late check-out, or both.")
    booking = await find_confirmed_booking(db, host_user_id, guest_phone)
    if booking is None:
        return ToolBusinessError(
            "I can't find a confirmed booking under this number. Ask the guest for the mobile number the booking was made with "
            "and try again; if they have no confirmed booking, this is not something to offer -- tell them the team will "
            "look into it and call escalate_to_host."
        )
    property_ = await db.get(Property, booking.property_id)
    terms = spacez_pricing_terms.terms_for(property_) if property_ is not None else None
    if property_ is None or terms is None:
        return ToolBusinessError(
            "I can't price an early check-in or late check-out for this booking. Tell the guest the team will confirm the "
            "charge and call escalate_to_host so it is logged."
        )
    early_fee = terms.early_checkin_fee if early_checkin else Decimal(0)
    late_fee = terms.late_checkout_fee if late_checkout else Decimal(0)
    villa = property_.display_name or property_.name
    dates = f"{_day(booking.check_in)} to {_day(booking.check_out)}"

    open_ones = list(
        (
            await db.scalars(
                select(SpacezStayRequest).where(
                    SpacezStayRequest.payment_request_id == booking.id, SpacezStayRequest.status.in_(OPEN_STATUSES)
                )
            )
        ).all()
    )
    for existing in open_ones:
        if (early_checkin and existing.want_early_checkin) or (late_checkout and existing.want_late_checkout):
            where = (
                "The team has not answered yet."
                if existing.status == "pending"
                else "The team said yes and the payment link is already on the guest's WhatsApp."
            )
            return (
                f"This guest has already asked for this ({_what(existing.want_early_checkin, existing.want_late_checkout)}) "
                f"on {villa}. {where} Do not raise it again: say the request is with the team and they will hear on WhatsApp."
            )

    request = SpacezStayRequest(
        user_id=host_user_id,
        property_id=property_.id,
        call_session_id=call_session_id,
        payment_request_id=booking.id,
        guest_name=booking.guest_name,
        guest_phone=booking.guest_phone or guest_phone or "",
        check_in=booking.check_in,
        check_out=booking.check_out,
        want_early_checkin=early_checkin,
        want_late_checkout=late_checkout,
        note=(note or "").strip()[:300] or None,
        early_fee=early_fee,
        late_fee=late_fee,
        status="pending",
    )
    db.add(request)
    await db.commit()
    await db.refresh(request)

    total_fee = early_fee + late_fee
    who = booking.guest_name or booking.guest_phone or "A guest"
    summary = f"Early check-in / late check-out? {who} at {villa}" if early_checkin and late_checkout else (
        f"{'Early check-in' if early_checkin else 'Late check-out'}? {who} at {villa}"
    )
    detail = (
        f"{who} has a confirmed booking at {villa} ({dates}) and asks for {_what(early_checkin, late_checkout)}"
        + (f" ({request.note})" if request.note else "")
        + f". The flat charge is {_rupees(total_fee)}. Can the villa do it? Approve to send the guest a payment link "
        "on WhatsApp, or decline and the guest is told the team cannot."
    )
    notification = None
    try:
        notification = await notification_service.create_notification(
            db, channel="escalation", message=detail, property_id=property_.id, call_session_id=call_session_id, urgency="high"
        )
    except Exception:
        logger.exception("stay request alert failed request=%s", request.id)
        await db.rollback()
    ticket = await ticket_service.open_ticket(
        db,
        kind=TICKET_KIND,
        summary=summary,
        detail=detail,
        urgency="high",
        user_id=host_user_id,
        property_id=property_.id,
        call_session_id=call_session_id,
        notification_id=notification.id if notification is not None else None,
    )
    if ticket is not None:
        await db.execute(update(SpacezStayRequest).where(SpacezStayRequest.id == request.id).values(ticket_id=ticket.id))
        await db.commit()
    try:
        host = await db.get(User, host_user_id)
        phone = notification_preferences_service.host_transfer_phone(host, property_) if host else None
        if phone:
            await twilio_client.send_whatsapp_best_effort(phone, f"Mira: {detail}")
    except Exception:
        logger.exception("stay request host WhatsApp failed request=%s", request.id)

    return (
        f"Logged for the team. The flat charge is {_rupees(total_fee)} for {_what(early_checkin, late_checkout)}"
        + (f" ({_rupees(early_fee)} early check-in, {_rupees(late_fee)} late check-out)" if early_checkin and late_checkout else "")
        + f", on top of the booking at {villa}. Tell the guest that charge, then say: you will let the team check whether it is "
        "possible, and if it is you will share the payment link on WhatsApp. Never say it is confirmed or promise it. "
        "If the team cannot do it, the guest is told on WhatsApp."
    )


# -- the team answers (dashboard) --------------------------------------------------------------


async def _owned_by_ticket(db: AsyncSession, user: User, ticket_id: uuid.UUID) -> tuple[SpacezStayRequest, object]:
    ticket = await ticket_service.get_owned_ticket(db, ticket_id, user.id)
    request = (
        await db.scalar(select(SpacezStayRequest).where(SpacezStayRequest.ticket_id == ticket_id, SpacezStayRequest.user_id == user.id))
        if ticket is not None
        else None
    )
    if ticket is None or request is None:
        raise StayRequestError("That request was not found.")
    return request, ticket


async def _claim(db: AsyncSession, request_id: uuid.UUID, new_status: str) -> bool:
    """pending -> new_status exactly once, so two clicks (or two team members) cannot both act."""
    claimed = (
        await db.execute(
            update(SpacezStayRequest)
            .where(SpacezStayRequest.id == request_id, SpacezStayRequest.status == "pending")
            .values(status=new_status, decided_at=_now())
            .returning(SpacezStayRequest.id)
        )
    ).first()
    await db.commit()
    return claimed is not None


async def approve(db: AsyncSession, user: User, ticket_id: uuid.UUID) -> SpacezStayRequest:
    """The team says yes: the guest gets a payment link for the flat fee on WhatsApp."""
    request, ticket = await _owned_by_ticket(db, user, ticket_id)
    try:
        credentials = billing_service.get_credentials(user)
    except SecretsError as exc:
        raise StayRequestError(str(exc))
    if credentials is None:
        raise StayRequestError("Online payment isn't set up, so a payment link can't be created. Connect Razorpay in Settings, or decline.")
    if not await _claim(db, request.id, "awaiting_payment"):
        raise StayRequestError("This request has already been answered.")
    property_ = await db.get(Property, request.property_id)
    villa = (property_.display_name or property_.name) if property_ else "your stay"
    amount = Decimal(request.early_fee) + Decimal(request.late_fee)
    what = _what(request.want_early_checkin, request.want_late_checkout)
    try:
        link = await razorpay_client.create_payment_link(
            *credentials,
            amount_rupees=amount,
            reference_id=request.id.hex,
            description=f"{villa}: {what} ({_day(request.check_in)} to {_day(request.check_out)})",
            expire_by=int(max(_now() + LINK_LIFETIME, _now() + timedelta(seconds=razorpay_client.MIN_LINK_LIFETIME_SECONDS)).timestamp()),
            customer_name=request.guest_name,
            notes={"mira_stay_request_id": str(request.id), "property": villa, "what": what},
        )
    except RazorpayError as exc:
        # Nothing was sent: put it back so the team can try again.
        await db.execute(update(SpacezStayRequest).where(SpacezStayRequest.id == request.id).values(status="pending", decided_at=None, last_error=str(exc)))
        await db.commit()
        raise StayRequestError("The payment link could not be created just now. Try again in a moment.")
    await db.execute(
        update(SpacezStayRequest).where(SpacezStayRequest.id == request.id).values(razorpay_link_id=link.id, short_url=link.short_url, last_error=None)
    )
    await db.commit()
    values = [_first_name(request.guest_name), what, villa, f"{_day(request.check_in)} to {_day(request.check_out)}", _rupees(amount), link.short_url]
    sent = await whatsapp_templates.send(
        request.guest_phone, "spacez_stay_fee_link", values, fallback_text=whatsapp_templates.render("spacez_stay_fee_link", values)
    )
    if not sent:
        await notification_service.create_notification(
            db, channel="system", property_id=request.property_id, urgency="high",
            message=f"WhatsApp would not take the {what} payment link for {request.guest_name or request.guest_phone}. Send it to them yourself: {link.short_url}",
        )
    await ticket_service.set_status(db, ticket, "done")
    await db.refresh(request)
    return request


async def decline(db: AsyncSession, user: User, ticket_id: uuid.UUID) -> SpacezStayRequest:
    """The team says no: the guest is told on WhatsApp that it is not possible."""
    request, ticket = await _owned_by_ticket(db, user, ticket_id)
    if not await _claim(db, request.id, "declined"):
        raise StayRequestError("This request has already been answered.")
    property_ = await db.get(Property, request.property_id)
    villa = (property_.display_name or property_.name) if property_ else "your stay"
    what = _what(request.want_early_checkin, request.want_late_checkout)
    values = [_first_name(request.guest_name), what, villa, f"{_day(request.check_in)} to {_day(request.check_out)}"]
    if not await whatsapp_templates.send(
        request.guest_phone, "spacez_stay_request_declined", values, fallback_text=whatsapp_templates.render("spacez_stay_request_declined", values)
    ):
        await notification_service.create_notification(
            db, channel="system", property_id=request.property_id, urgency="high",
            message=f"WhatsApp would not take the 'we can't do {what}' message for {request.guest_name or request.guest_phone}. Please tell them yourself.",
        )
    await ticket_service.set_status(db, ticket, "done")
    await db.refresh(request)
    return request


# -- watching the approved links (runs with the once-a-minute payment sweep) ----------------------------


async def sweep(db: AsyncSession) -> int:
    """Ask the payment provider about every approved-but-unpaid fee link. Returns how many were found paid.
    One failing never stops the rest."""
    rows = (
        await db.execute(
            select(SpacezStayRequest.id, SpacezStayRequest.user_id, SpacezStayRequest.razorpay_link_id)
            .where(SpacezStayRequest.status == "awaiting_payment", SpacezStayRequest.razorpay_link_id.is_not(None))
            .order_by(SpacezStayRequest.created_at)
            .limit(SWEEP_BATCH)
        )
    ).all()
    paid = 0
    credentials_by_user: dict[uuid.UUID, tuple[str, str] | None] = {}
    for request_id, user_id, link_id in rows:
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
            except RazorpayError:
                continue
            if link.status == "paid":
                paid += 1 if await _mark_paid(db, request_id, link.payment_id) else 0
            elif link.status in ("expired", "cancelled"):
                await _mark_expired(db, request_id, link.status)
        except Exception:
            logger.exception("stay request sweep failed for request=%s; carrying on with the rest", request_id)
            await db.rollback()
    return paid


async def _transition(db: AsyncSession, request_id: uuid.UUID, new_status: str, **values) -> SpacezStayRequest | None:
    claimed = (
        await db.execute(
            update(SpacezStayRequest)
            .where(SpacezStayRequest.id == request_id, SpacezStayRequest.status == "awaiting_payment")
            .values(status=new_status, **values)
            .returning(SpacezStayRequest.id)
        )
    ).first()
    await db.commit()
    if claimed is None:
        return None
    return await db.scalar(select(SpacezStayRequest).where(SpacezStayRequest.id == request_id).execution_options(populate_existing=True))


async def _mark_paid(db: AsyncSession, request_id: uuid.UUID, payment_id: str | None) -> bool:
    request = await _transition(db, request_id, "paid", paid_at=_now(), razorpay_payment_id=payment_id)
    if request is None:
        return False
    property_ = await db.get(Property, request.property_id)
    villa = (property_.display_name or property_.name) if property_ else "the villa"
    what = _what(request.want_early_checkin, request.want_late_checkout)
    amount = Decimal(request.early_fee) + Decimal(request.late_fee)
    who = request.guest_name or request.guest_phone
    try:
        await notification_service.create_notification(
            db, channel="system", property_id=request.property_id, urgency="medium",
            message=f"{who} paid {_rupees(amount)} for {what} at {villa} ({_day(request.check_in)} to {_day(request.check_out)}).",
        )
    except Exception:
        logger.exception("stay request paid but the host alert failed request=%s", request_id)
        await db.rollback()
    values = [_first_name(request.guest_name), _rupees(amount), what, villa, f"{_day(request.check_in)} to {_day(request.check_out)}"]
    await whatsapp_templates.send(
        request.guest_phone, "spacez_stay_fee_received", values, fallback_text=whatsapp_templates.render("spacez_stay_fee_received", values)
    )
    return True


async def _mark_expired(db: AsyncSession, request_id: uuid.UUID, why: str) -> None:
    request = await _transition(db, request_id, "expired", last_error=f"link {why}")
    if request is None:
        return
    what = _what(request.want_early_checkin, request.want_late_checkout)
    await notification_service.create_notification(
        db, channel="system", property_id=request.property_id, urgency="low",
        message=f"The {what} payment link for {request.guest_name or request.guest_phone} {why} unpaid.",
    )
