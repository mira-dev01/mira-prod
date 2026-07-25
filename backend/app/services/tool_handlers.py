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
from app.integrations import email_client, twilio_client
from app.integrations.email_templates import build_escalation_email_html, build_photos_email_html
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
    SendPhotosArgs,
    SendWhatsappArgs,
    UpdateLeadArgs,
)
from app.services import (
    calendar_service,
    embedding_service,
    faq_service,
    lead_service,
    notification_service,
    pricing_engine,
    technician_service,
)

logger = logging.getLogger(__name__)

# A resolved price of ₹0 (or negative) is never a real rate a guest should
# hear -- it means the pricing source failed silently and fell through to a
# stale/unset base_price. Confirmed live 2026-07-23: a property whose stored
# base_price was 0 (and whose live SearchApi fetch couldn't run -- credits
# exhausted) was quoted to a guest as "zero rupees for the night". The agent
# was faithfully reading what the pricing engine returned; the fix is to
# never return a zero as if it were a quote. This is a directive to the model
# (same embedded-instruction pattern as recommend_properties' combo_note),
# not just a spoken line -- it must NOT read the number out.
_PRICE_UNAVAILABLE_MESSAGE = (
    "The system doesn't have a confirmed nightly rate for this property and these dates right now, so there is "
    "NO price to quote. Do NOT say a number, and never tell the guest it's zero, free, or complimentary -- that "
    "would be wrong. Tell them you'll confirm the exact rate with the host and follow up shortly, then call "
    "escalate_to_host so the host can provide the correct price."
)

# WhatsApp has no color/font-weight rendering beyond *bold*/_italic_, so
# urgency is signaled with an emoji instead -- a host skimming a stack of
# these needs to triage by glancing at the left edge of each message, not
# by reading the first sentence.
_URGENCY_EMOJI = {"emergency": "\U0001F6A8", "high": "\U0001F534", "medium": "\U0001F7E0", "low": "\U0001F7E1"}


def _phone_confirmation_warning(phone: str | None) -> str:
    """SendWhatsappArgs/SendPhotosArgs/UpdateLeadArgs all normalize phone
    digits via _normalize_phone (app/schemas/tool.py), which is left
    deliberately permissive -- it never rejects a number, since a guest can
    legitimately still be mid-dictation when it runs. That means an STT
    mis-hearing that drops or garbles a digit (confirmed live: a WhatsApp
    send silently going nowhere because the captured number was 9 digits,
    not 10) sails through with no signal anywhere that anything was wrong.
    Every handler that saves or messages a phone number appends this to its
    tool result when the digit count is off, so the model finds out in the
    same turn and can ask the guest to repeat it -- instead of confidently
    confirming success on a number that was never going to work.
    """
    if not phone or len(phone) == 10:
        return ""
    return (
        f" Note: that phone number has {len(phone)} digits, not 10, so it's likely incomplete or "
        "misheard -- ask the guest to repeat their full 10-digit number to confirm before relying on it."
    )


def _build_escalation_whatsapp_text(
    urgency: str, property_name: str, reason: str, call_summary: str | None, guest_phone: str | None, dashboard_url: str
) -> str:
    emoji = _URGENCY_EMOJI.get(urgency, "⚪")
    lines = [
        f"{emoji} *{urgency.upper()} ESCALATION*",
        f"*Property:* {property_name}",
        f"*Issue:* {reason}",
    ]
    if call_summary:
        lines.append(f"*Summary:* {call_summary}")
    lines.append(f"*Guest:* {guest_phone}" if guest_phone else "*Guest:* not captured")
    lines.append("")
    lines.append(dashboard_url)
    return "\n".join(lines)


async def _send_escalation_email(to_email: str, subject: str, body: str, html_body: str) -> None:
    # Runs detached via asyncio.create_task -- never awaited by the caller,
    # so exceptions here would otherwise vanish into asyncio's default
    # handler. Log instead, and never let a slow/failed SMTP send add
    # latency to the live call turn that triggered it.
    try:
        result = await email_client.send_email(to_email, subject, body, html_body=html_body)
        if result.get("status") == "skipped":
            logger.info("Escalation email to %s skipped: %s", to_email, result.get("reason"))
    except Exception:
        logger.exception("Failed to send escalation email to %s", to_email)


async def _send_whatsapp_message(to_phone: str, body: str) -> None:
    # Same detached/best-effort pattern as _send_escalation_email above --
    # never blocks the tool result on a live call for a slow/failed Twilio
    # request. "skipped" covers both Twilio not being configured and the
    # far more common sandbox case (63015: recipient never texted "join
    # <code>" to the sandbox number), which surfaces as a TwilioError, not
    # a "skipped" status -- both are logged, neither raises past here.
    try:
        result = await twilio_client.send_whatsapp_message(to_phone, body)
        if result.get("status") == "skipped":
            logger.info("WhatsApp message to %s skipped: %s", to_phone, result.get("reason"))
    except twilio_client.TwilioError:
        logger.exception("Failed to send WhatsApp message to %s", to_phone)


async def _send_escalation_whatsapp(
    to_phone: str, urgency: str, property_name: str, reason: str, call_summary: str | None, guest_phone: str | None, dashboard_url: str
) -> None:
    # Prefers the twilio/call-to-action Content Template (real "Go to
    # Dashboard" button, no raw URL, no link-preview card) when it's been
    # created -- see scripts/create_escalation_template.py. Falls back to
    # a plain-text message with a bare URL if the template hasn't been set
    # up yet, so escalations still reach the host either way.
    emoji = _URGENCY_EMOJI.get(urgency, "⚪")
    try:
        if settings.twilio_escalation_template_sid:
            result = await twilio_client.send_whatsapp_template(
                to_phone,
                settings.twilio_escalation_template_sid,
                {
                    "1": emoji,
                    "2": urgency.upper(),
                    "3": property_name,
                    "4": reason,
                    "5": call_summary or "Not provided",
                    "6": guest_phone or "Not captured",
                },
            )
        else:
            result = await twilio_client.send_whatsapp_message(
                to_phone,
                _build_escalation_whatsapp_text(urgency, property_name, reason, call_summary, guest_phone, dashboard_url),
            )
        if result.get("status") == "skipped":
            logger.info("Escalation WhatsApp to %s skipped: %s", to_phone, result.get("reason"))
    except twilio_client.TwilioError:
        logger.exception("Failed to send escalation WhatsApp to %s", to_phone)


async def _get_property(db: AsyncSession, property_id: str) -> Property | None:
    try:
        pid = uuid.UUID(property_id)
    except ValueError:
        return None
    return await db.get(Property, pid)


async def handle_check_calendar(
    db: AsyncSession,
    args: CheckCalendarArgs,
    host_user_id: uuid.UUID | None = None,
    call_session_id: uuid.UUID | None = None,
    guest_profile_id: uuid.UUID | None = None,
) -> str:
    property_ = await _get_property(db, args.property_id)
    if property_ is None:
        return "I couldn't find that property. Could you confirm which listing you're asking about?"

    if args.check_out <= args.check_in:
        return "The check-out date needs to be after check-in. Could you confirm the dates?"

    if args.num_guests is not None and args.num_guests > property_.max_guests:
        return f"{property_.name} sleeps up to {property_.max_guests} guests, which is fewer than {args.num_guests}."

    nights_requested = (args.check_out - args.check_in).days
    if nights_requested < property_.minimum_nights:
        return (
            f"{property_.name} has a minimum stay of {property_.minimum_nights} nights -- "
            f"{nights_requested} night{'s' if nights_requested != 1 else ''} is below that. "
            "Would a longer stay work?"
        )

    if host_user_id is not None:
        await lead_service.backfill_lead_from_engagement(
            db,
            host_user_id,
            call_session_id,
            property_.name,
            args.check_in,
            args.check_out,
            args.num_guests,
            guest_profile_id=guest_profile_id,
        )

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


async def handle_get_pricing(
    db: AsyncSession,
    args: GetPricingArgs,
    host_user_id: uuid.UUID | None = None,
    call_session_id: uuid.UUID | None = None,
    guest_profile_id: uuid.UUID | None = None,
) -> str:
    property_ = await _get_property(db, args.property_id)
    if property_ is None:
        return "I couldn't find that property to price. Could you confirm which listing you're asking about?"

    if args.check_out <= args.check_in:
        return "The check-out date needs to be after check-in. Could you confirm the dates?"

    if host_user_id is not None:
        await lead_service.backfill_lead_from_engagement(
            db,
            host_user_id,
            call_session_id,
            property_.name,
            args.check_in,
            args.check_out,
            args.num_guests,
            guest_profile_id=guest_profile_id,
        )

    breakdown = await pricing_engine.calculate_price(
        db, property_, args.check_in, args.check_out, apply_discounts=args.apply_discounts
    )

    # Never quote a non-positive total as a real price -- see
    # _PRICE_UNAVAILABLE_MESSAGE. base_total is the pre-discount figure, so
    # checking it (not just total) catches the ₹0-base case regardless of any
    # discount applied on top.
    if breakdown.base_total <= 0 or breakdown.total <= 0:
        logger.warning(
            "get_pricing produced a non-positive quote for property %s (base_total=%s, total=%s) -- "
            "likely a base_price of 0 with no live price available; refusing to quote it",
            property_.id,
            breakdown.base_total,
            breakdown.total,
        )
        return _PRICE_UNAVAILABLE_MESSAGE

    # Lead with the total as one natural spoken sentence -- this string is
    # what the LLM tends to read back almost verbatim. No cleaning fee/tax
    # markup to itemize (see pricing_engine.calculate_price) -- base_total
    # only differs from total when a length-of-stay discount applies, so
    # that's the only case worth spelling out separately.
    summary = (
        f"For {property_.name}, {breakdown.nights} night(s) comes to ₹{breakdown.total:,.0f} total "
        f"(about ₹{breakdown.per_night_avg:,.0f} per night)"
    )
    if breakdown.discount_amount:
        summary += (
            f", including a {breakdown.discount_percent:.0f}% discount of ₹{breakdown.discount_amount:,.0f} "
            f"off the base rate of ₹{breakdown.base_total:,.0f}"
        )
    summary += "."
    return summary


async def handle_dispatch_technician(
    db: AsyncSession, args: DispatchTechnicianArgs, call_session_id: uuid.UUID | None
) -> str:
    property_ = await _get_property(db, args.property_id)
    if property_ is None:
        return "I couldn't find that property to dispatch a technician for."

    # "general" covers non-maintenance in-stay asks (towels, housekeeping,
    # amenity requests) routed here instead of escalate_to_host -- "our
    # general technician" reads oddly for a towel request, so use "caretaker"
    # in the guest-facing copy for that category specifically. Internal
    # notification text keeps saying "technician" either way, since that's
    # accurate for the host-facing record regardless of phrasing to the guest.
    role_label = "caretaker" if args.issue_type == "general" else f"{args.issue_type} technician"

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
            f"I don't have a {role_label} on file for {property_.name} yet, "
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
        f"I've let {technician.name}, our {role_label} for {property_.name}, know -- "
        f"they'll take care of it shortly."
        if args.issue_type == "general"
        else (
            f"I've notified {technician.name}, our {role_label} for {property_.name}, "
            f"and flagged this to the host as {args.urgency} priority."
        )
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
    asyncio.create_task(_send_whatsapp_message(args.phone, args.message))
    return f"Got it, I've queued a WhatsApp message to {args.phone}." + _phone_confirmation_warning(args.phone)


async def handle_send_photos(
    db: AsyncSession, args: SendPhotosArgs, call_session_id: uuid.UUID | None, host_user_id: uuid.UUID
) -> str:
    property_ = await _get_property(db, args.property_id)
    if property_ is None:
        return "I couldn't find that property to send photos for."
    if not property_.photos:
        return f"I don't have photos on file for {property_.name} yet -- I'll flag that to the host."

    # One link, not N image attachments -- WhatsApp Business API bills per
    # media message, so a gallery page beats sending each photo separately.
    # Reuses the same Cloudinary URLs already on the property (populated by
    # Airbnb/Bright Data import) rather than a host-maintained Drive folder,
    # so this can never drift out of sync with what's actually on file.
    gallery_url = f"{settings.frontend_base_url}/p/{property_.id}/photos"
    await notification_service.create_notification(
        db,
        channel="whatsapp",
        property_id=property_.id,
        call_session_id=call_session_id,
        urgency="low",
        message=f"To {args.guest_phone}: Here are photos of {property_.name} -- {gallery_url}",
    )

    # Real WhatsApp send via Twilio sandbox (only reaches numbers that have
    # joined the sandbox -- see twilio_account_sid's docstring in config.py).
    asyncio.create_task(
        _send_whatsapp_message(args.guest_phone, f"Here are photos of {property_.name} -- {gallery_url}")
    )

    # Email stays as a parallel channel (not a fallback) so this is testable
    # against the host's own inbox even for guest numbers that were never
    # added to the Twilio sandbox.
    host_user = await db.get(User, host_user_id)
    if host_user is not None:
        asyncio.create_task(
            _send_escalation_email(
                host_user.notification_email or host_user.email,
                subject=f"Photos requested — {property_.name}",
                body=f"A guest ({args.guest_phone}) asked to see photos of {property_.name}.\n\n"
                f"Gallery link: {gallery_url}",
                html_body=build_photos_email_html(
                    property_name=property_.name, guest_phone=args.guest_phone, gallery_url=gallery_url
                ),
            )
        )

    return (
        f"Got it, I've sent a photo gallery link for {property_.name} to {args.guest_phone}."
        + _phone_confirmation_warning(args.guest_phone)
    )


async def handle_escalate_to_host(
    db: AsyncSession,
    args: EscalateToHostArgs,
    call_session_id: uuid.UUID | None,
    host_user_id: uuid.UUID,
    guest_profile_id: uuid.UUID | None = None,
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
        guest_profile_id=guest_profile_id,
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
                subject=f"{args.urgency.title()} escalation — {property_.name}",
                body=f"{message}\n\nView in dashboard: {settings.frontend_base_url}/dashboard/leads",
                html_body=build_escalation_email_html(
                    property_name=property_.name,
                    urgency=args.urgency,
                    reason=args.reason,
                    call_summary=args.call_summary,
                    guest_phone=args.guest_phone,
                ),
            )
        )
        # WhatsApp via Twilio Sandbox -- only reaches host_user.phone if
        # that number has joined the sandbox (see twilio_client.py). Unset
        # phone or unconfigured Twilio both no-op silently in
        # _send_escalation_whatsapp, same as the email above.
        if host_user.phone:
            asyncio.create_task(
                _send_escalation_whatsapp(
                    host_user.phone,
                    urgency=args.urgency,
                    property_name=property_.name,
                    reason=args.reason,
                    call_summary=args.call_summary,
                    guest_phone=args.guest_phone,
                    dashboard_url=f"{settings.frontend_base_url}/dashboard/leads",
                )
            )

    return f"I've escalated this to the host as {args.urgency} priority. They'll follow up shortly."


async def handle_negotiate_rate(
    db: AsyncSession,
    args: NegotiateRateArgs,
    host_user_id: uuid.UUID | None = None,
    guest_profile_id: uuid.UUID | None = None,
    call_session_id: uuid.UUID | None = None,
) -> str:
    property_ = await _get_property(db, args.property_id)
    if property_ is None:
        return "I couldn't find that property to negotiate a rate for."

    if args.check_out <= args.check_in:
        return "The check-out date needs to be after check-in. Could you confirm the dates?"

    if host_user_id is not None:
        await lead_service.backfill_lead_from_engagement(
            db,
            host_user_id,
            call_session_id,
            property_.name,
            args.check_in,
            args.check_out,
            args.num_guests,
            guest_profile_id=guest_profile_id,
        )

    result = await pricing_engine.negotiate_rate(
        db,
        property_,
        args.check_in,
        args.check_out,
        args.guest_offer,
        args.guest_loyalty,
        host_id=host_user_id,
        guest_profile_id=guest_profile_id,
    )
    # Same non-positive-price guard as handle_get_pricing -- negotiate_rate
    # derives everything (asking price, floor, counter-offer) from
    # calculate_price, so a ₹0 base means every number here is 0 too. Never
    # let that reach the guest as a "discounted" or "best" price.
    if result.asking_price <= 0 or result.counter_offer <= 0:
        logger.warning(
            "negotiate_rate produced a non-positive quote for property %s (asking=%s, counter=%s) -- "
            "likely a base_price of 0 with no live price available; refusing to quote it",
            property_.id,
            result.asking_price,
            result.counter_offer,
        )
        return _PRICE_UNAVAILABLE_MESSAGE
    return result.message


# Property.city/neighborhood_info are free text -- most properties only ever
# say "Goa" or one specific locality ("Colva", "Siolim"), never the literal
# words "North Goa"/"South Goa" (confirmed live: Azure's city is "Colva" and
# its neighborhood_info never mentions "South Goa" either, so an ILIKE match
# on those exact words silently excluded it from a "South Goa" query even
# though Colva unambiguously is South Goa). This maps the common named
# localities to their region so a region query matches on where a property
# actually is, not on whether that exact phrase happens to appear in its text.
_GOA_NORTH_LOCALITIES = [
    "siolim", "anjuna", "vagator", "chapora", "morjim", "ashwem", "mandrem",
    "calangute", "candolim", "baga", "arpora", "assagao", "mapusa",
    "sinquerim", "arambol", "querim", "reis magos",
]
_GOA_SOUTH_LOCALITIES = [
    "colva", "margao", "madgaon", "benaulim", "varca", "cavelossim", "mobor",
    "palolem", "agonda", "majorda", "cansaulim", "betalbatim", "velsao",
    "vasco", "bogmalo", "canacona",
]


def _goa_region_localities(preferred_location: str) -> list[str] | None:
    normalized = preferred_location.strip().lower()
    if "north goa" in normalized:
        return _GOA_NORTH_LOCALITIES
    if "south goa" in normalized:
        return _GOA_SOUTH_LOCALITIES
    return None


async def handle_recommend_properties(db: AsyncSession, args: RecommendPropertiesArgs, host_user_id: uuid.UUID) -> str:
    from sqlalchemy import or_

    base_stmt = select(Property).where(Property.user_id == host_user_id)
    if args.budget is not None:
        base_stmt = base_stmt.where(Property.base_price <= args.budget * 1.15)
    if args.preferred_location:
        # Match against city name OR neighborhood_info so state-level queries
        # ("Kerala", "Himachal") find properties whose city is e.g. "Alleppey"
        # but whose neighborhood text mentions the broader region. For Goa
        # specifically, also expand "north/south goa" into the actual
        # localities in that region (see _goa_region_localities above).
        loc = f"%{args.preferred_location}%"
        location_filter = or_(Property.city.ilike(loc), Property.neighborhood_info.ilike(loc))
        localities = _goa_region_localities(args.preferred_location)
        if localities:
            location_filter = or_(
                location_filter,
                *[
                    or_(Property.city.ilike(f"%{locality}%"), Property.neighborhood_info.ilike(f"%{locality}%"))
                    for locality in localities
                ],
            )
        base_stmt = base_stmt.where(location_filter)

    stmt = base_stmt
    if args.num_guests is not None:
        stmt = stmt.where(Property.max_guests >= args.num_guests)
    stmt = stmt.order_by(Property.base_price.asc()).limit(3)
    properties = list((await db.scalars(stmt)).all())

    combo_note = ""
    if not properties and args.num_guests is not None:
        # No single property sleeps the whole group -- fall back to smaller
        # units (same location/budget filters, just without the guest-count
        # cutoff) instead of a flat "nothing found", so the model can suggest
        # booking two units together to cover the group. Hosts with several
        # small 1BHKs at the same property (e.g. the Pause Project in Siolim)
        # routinely accommodate larger groups exactly this way.
        fallback_stmt = base_stmt.order_by(Property.base_price.asc()).limit(4)
        properties = list((await db.scalars(fallback_stmt)).all())
        if properties:
            combo_note = (
                f" None of these sleep all {args.num_guests} guests alone -- since they're separate units, "
                "suggest the guest book two of them together to cover the group."
            )

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
    return "Here are some options: " + " | ".join(lines) + combo_note


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
    db: AsyncSession,
    args: UpdateLeadArgs,
    host_user_id: uuid.UUID,
    call_session_id: uuid.UUID | None,
    guest_profile_id: uuid.UUID | None = None,
) -> str:
    updates = args.model_dump(exclude_unset=True)
    if updates.get("properties_discussed"):
        updates["properties_discussed"] = await _resolve_property_names(db, updates["properties_discussed"])
    await lead_service.upsert_lead(db, host_user_id, call_session_id, guest_profile_id=guest_profile_id, **updates)
    return "Saved." + _phone_confirmation_warning(updates.get("phone"))


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

    parts: list[str] = []

    entries = await faq_service.search_faq_entries(db, host_user_id, args.query, property_id)
    if entries:
        parts.append(" | ".join(f"{entry.question}: {entry.answer}" for entry in entries))
    elif property_id is not None:
        legacy = await faq_service.search_legacy_property_faq(db, property_id, args.query)
        if legacy:
            parts.append(" | ".join(f"{item['question']}: {item['answer']}" for item in legacy))

    if property_id is not None:
        property_ = await _get_property(db, str(property_id))
        if property_ is not None:
            # Always included, even when a verified FaqEntry already matched
            # above -- a verified entry can match the query's *wording*
            # (trigram similarity) without actually covering what the guest
            # asked. Confirmed live: "is it on the ground floor" got "I
            # don't have that specific detail" even though the property's
            # own description states the floor explicitly, because a
            # loosely-matching verified entry short-circuited before this
            # ever ran. Handing the model everything on file alongside
            # whatever specific entry matched lets it pick the real answer
            # itself rather than trusting a similarity score to have judged
            # relevance correctly -- see full_property_context's docstring.
            parts.append(f"{property_.name}: {faq_service.full_property_context(property_)}")

    if parts:
        return " | ".join(parts)

    # No verified answer anywhere -- log the gap for the FAQ Learning Engine
    # (app/api/v1/faq.py's /faq/gaps endpoints) so hosts can see frequently
    # unanswered questions and convert them into real FaqEntry rows. Never
    # let a logging failure break the guest-facing response.
    try:
        gap = UnansweredQuestion(
            user_id=host_user_id,
            property_id=property_id,
            call_session_id=call_session_id,
            question=args.query,
            normalized_question=args.query.strip().lower(),
        )
        db.add(gap)
        await db.commit()
        await db.refresh(gap)
        # Knowledge Memory (memory-architecture-plan.md section 3.1):
        # embedding computed fire-and-forget, AFTER the DB commit above and
        # never awaited here -- an embedding API call must never add
        # latency to this live guest turn. Uses its own DB session (see
        # embedding_service.py), so it's safe even after this handler
        # returns.
        asyncio.create_task(embedding_service.backfill_unanswered_question_embedding(gap.id, gap.question))
    except Exception:
        await db.rollback()

    return "I don't have verified information about that. I'll connect you with the host so you receive the correct details."
