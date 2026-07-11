"""Business logic for the 6 LLM tool functions, called from the voice
pipeline's tool wrappers (app/voice/tools.py). Each handler returns a
natural-language string -- this is what gets fed back to the model as the
tool result and is what it will speak to the guest, so results are phrased
for that, not as raw JSON.
"""

import asyncio
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.integrations import email_client
from app.models.property import Property
from app.models.unanswered_question import UnansweredQuestion
from app.models.user import User
from app.schemas.tool import (
    CheckCalendarArgs,
    DispatchTechnicianArgs,
    EscalateToHostArgs,
    GetPricingArgs,
    NegotiateRateArgs,
    RecommendPropertiesArgs,
    SearchFaqArgs,
    SendWhatsappArgs,
    UpdateLeadArgs,
)
from app.services import (
    calendar_service,
    faq_service,
    lead_service,
    notification_service,
    pricing_engine,
    technician_service,
)

logger = logging.getLogger(__name__)


async def _send_escalation_email(to_email: str, subject: str, body: str) -> None:
    # Runs detached via asyncio.create_task -- never awaited by the caller,
    # so exceptions here would otherwise vanish into asyncio's default
    # handler. Log instead, and never let a slow/failed SMTP send add
    # latency to the live call turn that triggered it.
    try:
        result = await email_client.send_email(to_email, subject, body)
        if result.get("status") == "skipped":
            logger.info("Escalation email to %s skipped: %s", to_email, result.get("reason"))
    except Exception:
        logger.exception("Failed to send escalation email to %s", to_email)


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

    # Lead with the total as one natural spoken sentence -- this string is
    # what the LLM tends to read back almost verbatim, so an itemized,
    # comma-joined ledger (base rate X, cleaning fee Y, taxes Z, ...) comes
    # out sounding like the agent is reciting a spreadsheet row instead of
    # talking to the guest. Fee components are appended only as a secondary,
    # clearly-labeled "if asked" breakdown the model can draw on without it
    # being the primary thing it parrots.
    summary = (
        f"For {property_.name}, {breakdown.nights} night(s) comes to ₹{breakdown.total:,.0f} total "
        f"(about ₹{breakdown.per_night_avg:,.0f} per night)"
    )
    if breakdown.discount_amount:
        summary += f", including a {breakdown.discount_percent:.0f}% discount of ₹{breakdown.discount_amount:,.0f}"
    summary += "."
    breakdown_detail = (
        f" Breakdown if the guest asks: base rate ₹{breakdown.base_total:,.0f}, "
        f"cleaning fee ₹{breakdown.cleaning_fee:,.0f}, taxes ₹{breakdown.tax_amount:,.0f}."
    )
    return summary + breakdown_detail


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
    db: AsyncSession, args: EscalateToHostArgs, call_session_id: uuid.UUID | None, host_user_id: uuid.UUID
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

    # Don't rely on the LLM separately remembering to call update_lead too --
    # capture whatever this escalation already has (phone, summary) on the
    # lead record directly, so an escalated call is never left with an empty
    # CRM lead just because the model only made the one tool call.
    await lead_service.upsert_lead(
        db,
        host_user_id,
        call_session_id,
        phone=args.guest_phone,
        conversation_summary=args.call_summary,
        properties_discussed=[property_.name],
        escalated=True,
    )

    # In-app notification above covers hosts watching the dashboard live;
    # this email covers the far more common case of a host who isn't. Fired
    # detached (not awaited) so a slow/misconfigured SMTP server never adds
    # latency to this tool call's result -- the guest is still on the line.
    host_user = await db.get(User, host_user_id)
    if host_user is not None:
        # notification_email (Settings -> Notifications) lets a host route
        # escalations to a different inbox -- a shared front-desk address,
        # say -- without changing their login email. Unset = login email.
        asyncio.create_task(
            _send_escalation_email(
                host_user.notification_email or host_user.email,
                subject=f"[Mira] {args.urgency.title()} escalation — {property_.name}",
                body=f"{message}\n\nView in dashboard: {settings.frontend_base_url}/dashboard/leads",
            )
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


async def handle_recommend_properties(db: AsyncSession, args: RecommendPropertiesArgs, host_user_id: uuid.UUID) -> str:
    from sqlalchemy import or_

    stmt = select(Property).where(Property.user_id == host_user_id)
    if args.num_guests is not None:
        stmt = stmt.where(Property.max_guests >= args.num_guests)
    if args.budget is not None:
        stmt = stmt.where(Property.base_price <= args.budget * 1.15)
    if args.preferred_location:
        # Match against city name OR neighborhood_info so state-level queries
        # ("Kerala", "Himachal") find properties whose city is e.g. "Alleppey"
        # but whose neighborhood text mentions the broader region.
        loc = f"%{args.preferred_location}%"
        stmt = stmt.where(
            or_(
                Property.city.ilike(loc),
                Property.neighborhood_info.ilike(loc),
            )
        )
    stmt = stmt.order_by(Property.base_price.asc()).limit(3)

    properties = list((await db.scalars(stmt)).all())
    if not properties:
        return "I couldn't find a property in our portfolio matching that -- let me connect you with the host directly."

    lines = []
    for property_ in properties:
        amenities = ", ".join(property_.amenities[:4]) if property_.amenities else "no listed amenities"
        usp_part = f" -- {property_.usp}" if property_.usp else ""
        lines.append(
            f"{property_.name} in {property_.city or 'unlisted city'}: ₹{float(property_.base_price):,.0f}/night, "
            f"sleeps {property_.max_guests}, {amenities}{usp_part} (property_id: {property_.id})"
        )
    return "Here are some options: " + " | ".join(lines)


async def _resolve_property_names(db: AsyncSession, values: list[str]) -> list[str]:
    """properties_discussed is meant to be human-readable names for the
    dashboard's Leads page, but the model sometimes echoes the property_id
    it was given in its own tool-call instructions instead of the name --
    confirmed live, a lead showed a raw UUID where "Pine & Mist Cabin"
    belonged. Prompt wording alone isn't reliable (same lesson as the phone
    number normalizer above), so resolve any UUID-shaped entry against the
    DB here. Anything that isn't a valid UUID is assumed to already be a
    name and is left untouched.
    """
    ids: list[uuid.UUID] = []
    for value in values:
        try:
            ids.append(uuid.UUID(value))
        except ValueError:
            continue
    if not ids:
        return values
    properties = (await db.scalars(select(Property).where(Property.id.in_(ids)))).all()
    names_by_id = {str(p.id): p.name for p in properties}
    return [names_by_id.get(value, value) for value in values]


async def handle_update_lead(
    db: AsyncSession, args: UpdateLeadArgs, host_user_id: uuid.UUID, call_session_id: uuid.UUID | None
) -> str:
    updates = args.model_dump(exclude_unset=True)
    if updates.get("properties_discussed"):
        updates["properties_discussed"] = await _resolve_property_names(db, updates["properties_discussed"])
    await lead_service.upsert_lead(db, host_user_id, call_session_id, **updates)
    return "Saved."


async def handle_search_faq(
    db: AsyncSession,
    args: SearchFaqArgs,
    host_user_id: uuid.UUID,
    default_property_id: uuid.UUID | None,
    call_session_id: uuid.UUID | None = None,
) -> str:
    property_id = None
    if args.property_id:
        try:
            property_id = uuid.UUID(args.property_id)
        except ValueError:
            property_id = None
    property_id = property_id or default_property_id

    entries = await faq_service.search_faq_entries(db, host_user_id, args.query, property_id)
    if entries:
        return " | ".join(f"{entry.question}: {entry.answer}" for entry in entries)

    if property_id is not None:
        legacy = await faq_service.search_legacy_property_faq(db, property_id, args.query)
        if legacy:
            return " | ".join(f"{item['question']}: {item['answer']}" for item in legacy)

    # No verified answer anywhere -- log the gap for the FAQ Learning Engine
    # (app/api/v1/faq.py's /faq/gaps endpoints) so hosts can see frequently
    # unanswered questions and convert them into real FaqEntry rows. Never
    # let a logging failure break the guest-facing response.
    try:
        db.add(
            UnansweredQuestion(
                user_id=host_user_id,
                property_id=property_id,
                call_session_id=call_session_id,
                question=args.query,
                normalized_question=args.query.strip().lower(),
            )
        )
        await db.commit()
    except Exception:
        await db.rollback()

    return "I don't have verified information about that. I'll connect you with the host so you receive the correct details."
