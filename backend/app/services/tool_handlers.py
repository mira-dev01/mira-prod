"""Business logic for the 6 LLM tool functions, called from the `tool-calls`
Vapi webhook. Each handler returns a natural-language string -- this is what
gets fed back to the model as the tool result and is what it will speak to
the guest, so results are phrased for that, not as raw JSON.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.property import Property
from app.schemas.tool import (
    CheckCalendarArgs,
    DispatchTechnicianArgs,
    EscalateToHostArgs,
    GetPricingArgs,
    NegotiateRateArgs,
    SendWhatsappArgs,
)
from app.services import calendar_service, notification_service, pricing_engine, technician_service


async def _get_property(db: AsyncSession, property_id: str) -> Property | None:
    try:
        pid = uuid.UUID(property_id)
    except ValueError:
        return None
    return await db.get(Property, pid)


async def handle_check_calendar(db: AsyncSession, args: CheckCalendarArgs) -> str:
    property_ = await _get_property(db, args.property_id)
    if property_ is None:
        return "I couldn't find that property. Could you confirm which listing you're asking about?"

    if args.check_out <= args.check_in:
        return "The check-out date needs to be after check-in. Could you confirm the dates?"

    if args.num_guests is not None and args.num_guests > property_.max_guests:
        return f"{property_.name} sleeps up to {property_.max_guests} guests, which is fewer than {args.num_guests}."

    available = await calendar_service.is_available(db, property_.id, args.check_in, args.check_out)
    nights = (args.check_out - args.check_in).days

    if available:
        return (
            f"{property_.name} is AVAILABLE from {args.check_in.isoformat()} to {args.check_out.isoformat()} "
            f"({nights} night{'s' if nights != 1 else ''})."
        )

    window = await calendar_service.next_available_window(db, property_.id, args.check_in, nights)
    if window:
        return (
            f"{property_.name} is NOT available for those dates. The next open window of "
            f"{nights} night{'s' if nights != 1 else ''} is {window[0].isoformat()} to {window[1].isoformat()}."
        )
    return f"{property_.name} is NOT available for those dates, and no similar-length window opens up in the next 90 days."


async def handle_get_pricing(db: AsyncSession, args: GetPricingArgs) -> str:
    property_ = await _get_property(db, args.property_id)
    if property_ is None:
        return "I couldn't find that property to price. Could you confirm which listing you're asking about?"

    if args.check_out <= args.check_in:
        return "The check-out date needs to be after check-in. Could you confirm the dates?"

    breakdown = await pricing_engine.calculate_price(
        db, property_, args.check_in, args.check_out, apply_discounts=args.apply_discounts
    )

    parts = [
        f"For {property_.name}, {breakdown.nights} night(s): base rate ₹{breakdown.base_total:,.0f}",
        f"cleaning fee ₹{breakdown.cleaning_fee:,.0f}",
        f"taxes ₹{breakdown.tax_amount:,.0f}",
    ]
    if breakdown.discount_amount:
        parts.append(f"discount -₹{breakdown.discount_amount:,.0f} ({breakdown.discount_percent:.0f}%)")
    parts.append(f"TOTAL ₹{breakdown.total:,.0f} (≈₹{breakdown.per_night_avg:,.0f}/night)")
    return ", ".join(parts) + "."


async def handle_dispatch_technician(
    db: AsyncSession, args: DispatchTechnicianArgs, call_session_id: uuid.UUID | None
) -> str:
    property_ = await _get_property(db, args.property_id)
    if property_ is None:
        return "I couldn't find that property to dispatch a technician for."

    technician = await technician_service.find_technician(db, property_.id, args.issue_type)

    if technician is None:
        await notification_service.create_notification(
            db,
            channel="escalation",
            property_id=property_.id,
            call_session_id=call_session_id,
            urgency=args.urgency,
            message=(
                f"No {args.issue_type} technician on file for {property_.name}. Guest issue needs manual "
                f"dispatch (urgency: {args.urgency})."
            ),
        )
        return (
            f"I don't have a {args.issue_type} technician on file for {property_.name} yet, "
            f"so I've flagged this for the host to arrange directly."
        )

    await notification_service.create_notification(
        db,
        channel="escalation",
        property_id=property_.id,
        call_session_id=call_session_id,
        urgency=args.urgency,
        message=(
            f"Dispatch requested: {args.issue_type} issue at {property_.name} (urgency: {args.urgency}). "
            f"Suggested technician: {technician.name} ({technician.phone}, rating {technician.rating})."
        ),
    )
    return (
        f"I've notified {technician.name}, our {args.issue_type} technician for {property_.name}, "
        f"and flagged this to the host as {args.urgency} priority."
    )


async def handle_send_whatsapp(
    db: AsyncSession, args: SendWhatsappArgs, property_id: uuid.UUID | None, call_session_id: uuid.UUID | None
) -> str:
    await notification_service.create_notification(
        db,
        channel="whatsapp",
        property_id=property_id,
        call_session_id=call_session_id,
        urgency="low",
        message=f"To {args.phone}: {args.message}",
    )
    return f"Got it, I've queued a WhatsApp message to {args.phone}."


async def handle_escalate_to_host(
    db: AsyncSession, args: EscalateToHostArgs, call_session_id: uuid.UUID | None
) -> str:
    property_ = await _get_property(db, args.property_id)
    if property_ is None:
        return "I couldn't find that property to escalate to the host."

    message = f"Escalation for {property_.name}: {args.reason}"
    if args.call_summary:
        message += f" | Summary: {args.call_summary}"
    if args.guest_phone:
        message += f" | Guest: {args.guest_phone}"

    await notification_service.create_notification(
        db,
        channel="escalation",
        property_id=property_.id,
        call_session_id=call_session_id,
        urgency=args.urgency,
        message=message,
    )
    return f"I've escalated this to the host as {args.urgency} priority. They'll follow up shortly."


async def handle_negotiate_rate(db: AsyncSession, args: NegotiateRateArgs) -> str:
    property_ = await _get_property(db, args.property_id)
    if property_ is None:
        return "I couldn't find that property to negotiate a rate for."

    if args.check_out <= args.check_in:
        return "The check-out date needs to be after check-in. Could you confirm the dates?"

    result = await pricing_engine.negotiate_rate(
        db, property_, args.check_in, args.check_out, args.guest_offer, args.guest_loyalty
    )
    return result.message
