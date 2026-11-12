"""Business logic for the 6 LLM tool functions, called from the voice
pipeline's tool wrappers (app/voice/tools.py). Each handler returns a
natural-language string -- this is what gets fed back to the model as the
tool result and is what it will speak to the guest, so results are phrased
for that, not as raw JSON.
"""

import logging
import uuid
from datetime import date, timedelta
from typing import Callable

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.voice.conversation_state import NegotiationEvent

from app.config import settings
from app.utils import background
from app.integrations import email_client, twilio_client
from app.integrations.email_templates import build_escalation_email_html, build_photos_email_html
from app.models.call_session import CallSession
from app.models.property import Property
from app.models.ticket import Ticket
from app.models.user import User
from app.schemas.tool import (
    CheckCalendarArgs,
    DispatchTechnicianArgs,
    EscalateToHostArgs,
    GetPricingArgs,
    LookupBookingArgs,
    NegotiateRateArgs,
    RecommendPropertiesArgs,
    RequestHostTransferArgs,
    SearchFaqArgs,
    SendPhotosArgs,
    SendShortlistArgs,
    SendWhatsappArgs,
    ToolBusinessError,
    UpdateLeadArgs,
    _normalize_phone,
)
from app.database import AsyncSessionLocal
from app.services import (
    billing_service,
    spacez_booking_confirmation,
    calendar_service,
    capability_service,
    notification_preferences_service,
    guest_booking_service,
    kb_gap_service,
    lead_service,
    notification_service,
    payment_service,
    price_event_service,
    pricing_engine,
    spacez_pricing_terms,
    spacez_stay_bill,
    stay_scope,
    technician_service,
    ticket_service,
    whatsapp_templates,
)
from app.services.pricing_engine import NegotiationResult, PriceBreakdown
from app.services.property import kb_retrieval
from app.services.property.pitch_formatter import RecommendationResult
from app.services.property.retrieval import orchestrator as property_retrieval_orchestrator
from app.utils.dates import today_ist
from app.voice.handoff_signal import request_handoff

# Same routable-state constant as webhooks/exotel.py's _ACTIVE_CALL_STATUSES
# -- a handoff can only be claimed against a call that's still actually in
# progress. Duplicated (not imported) for the same circular-import reason
# _has_usable_host_phone below is duplicated rather than imported.
_ACTIVE_CALL_STATUSES = {"in_progress"}

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


async def _send_escalation_whatsapp(
    to_phone: str, urgency: str, property_name: str, reason: str, call_summary: str | None, guest_phone: str | None, dashboard_url: str
) -> None:
    # Prefers the twilio/call-to-action Content Template (real "Go to
    # Dashboard" button, no raw URL, no link-preview card) when it's been
    # created -- see scripts/create_escalation_template.py. Falls back to
    # a plain-text message with a bare URL if the template hasn't been set
    # up yet, so escalations still reach the host either way. The actual
    # fire-and-forget "log skip/failure, never raise" contract lives in
    # twilio_client.send_whatsapp_template_best_effort/send_whatsapp_best_effort
    # (cleanup pass: this used to reimplement that contract locally --
    # consolidated to the same wrapper recovery_service.py already uses, so
    # there is exactly one place that contract is implemented).
    emoji = _URGENCY_EMOJI.get(urgency, "⚪")
    if settings.twilio_escalation_template_sid:
        await twilio_client.send_whatsapp_template_best_effort(
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
        await twilio_client.send_whatsapp_best_effort(
            to_phone,
            _build_escalation_whatsapp_text(urgency, property_name, reason, call_summary, guest_phone, dashboard_url),
        )


async def _get_property(db: AsyncSession, property_id: str) -> Property | None:
    try:
        pid = uuid.UUID(property_id)
    except ValueError:
        return None
    return await db.get(Property, pid)


def _past_dates_error(check_in: date) -> ToolBusinessError | None:
    """A stay starting before today can't be a new booking -- most often the
    guest is asking about an earlier or existing stay. Checked before
    backfill_lead_from_engagement, so no lead is ever created for it.
    Same-day check-in is allowed."""
    if check_in >= today_ist():
        return None
    return ToolBusinessError(
        f"{check_in.isoformat()} has already passed, so it can't be booked -- don't check or quote it. Ask the "
        "guest whether they're asking about a previous or existing booking, and if so, what name or phone number "
        "it was booked under, then call lookup_booking. If they actually meant a future date, confirm it first."
    )


def _minutes_until(moment) -> int:
    from datetime import datetime, timezone
    import math

    return max(1, math.ceil((moment - datetime.now(timezone.utc)).total_seconds() / 60))


async def _caller_of(db: AsyncSession, call_session_id: uuid.UUID | None) -> tuple[str | None, uuid.UUID | None]:
    """(the caller's number as a plain 10-digit string, the call's lead id).
    Browser test calls have no real number."""
    from app.services.call_service import BROWSER_TEST_CALLER_NUMBER

    if call_session_id is None:
        return None, None
    session = await db.get(CallSession, call_session_id)
    if session is None:
        return None, None
    number = session.caller_number
    if not number or number == BROWSER_TEST_CALLER_NUMBER:
        return None, session.lead_id
    return _normalize_phone(number), session.lead_id


def _is_own_hold(hold, phone: str | None, call_session_id: uuid.UUID | None) -> bool:
    """The hold belongs to the guest on this call: it was made on this very call
    (they may have asked for the link on a different number), or by the same
    number on an earlier one."""
    if call_session_id is not None and hold.call_session_id == call_session_id:
        return True
    return bool(phone and hold.guest_phone and _normalize_phone(hold.guest_phone) == phone)


async def _on_hold_answer(
    db: AsyncSession, property_: Property, check_in: date, check_out: date, call_session_id: uuid.UUID | None
) -> str | None:
    """When the dates are unavailable only because they are held for a guest who
    has not paid yet: say so, remember this guest so they hear if the hold
    lapses, and alert the host at HIGH urgency (once per waiting guest, however
    often the question is asked). None when no unpaid hold is involved."""
    hold = await payment_service.find_blocking_hold(db, property_.id, check_in, check_out)
    if hold is None or hold.status != "active":
        return None  # a paid hold is simply a booking
    phone, lead_id = await _caller_of(db, call_session_id)
    minutes = _minutes_until(hold.hold_expires_at)
    name = property_.name

    if _is_own_hold(hold, phone, call_session_id):
        return (
            f"{name} is already ON HOLD for this guest for those dates, waiting for their payment. A payment link was "
            f"sent on WhatsApp; the hold lasts about {minutes} more minute{'s' if minutes != 1 else ''}."
        )

    if phone:
        added = await payment_service.register_waiter(
            db, hold, guest_name=None, guest_phone=phone, lead_id=lead_id, call_session_id=call_session_id
        )
        if added:
            await payment_service.escalate_hold_interest(
                db, hold, property_, guest_name=None, guest_phone=phone, call_session_id=call_session_id
            )
        promise = (
            "We will message them on WhatsApp if it is released, and that can only happen within about "
            f"{minutes} minute{'s' if minutes != 1 else ''}."
        )
    else:
        promise = (
            "The host has been told and will get back to them. You do not have their number, so do not promise a message."
        )
    return (
        f"{name} is NOT available for those dates right now: another guest has them ON HOLD while their payment is "
        f"pending. Tell the guest exactly that -- a booking is on hold for these dates. {promise} "
        "Do not promise the dates and do not say who holds them. The host has been alerted."
    )


def _resolve_scope(property_: Property, stay_scope_arg: str | None, rooms_arg: int | None) -> tuple[str, int | None]:
    """(scope, rooms) a tool call is about. Anything but an explicit "rooms" is
    the entire place -- including every call about a property that is not
    rented by the room -- so existing behaviour is the default."""
    if stay_scope_arg == stay_scope.ROOMS:
        return stay_scope.ROOMS, rooms_arg or 1
    return stay_scope.ENTIRE_PLACE, None


def _room_bookings_phrase(conflicts) -> str:
    rooms = sum(c.rooms_booked or 1 for c in conflicts if c.stay_scope == stay_scope.ROOMS)
    return f"{rooms} room{'s' if rooms != 1 else ''}"


async def _double_occupancy_answer(
    db: AsyncSession,
    property_: Property,
    args: CheckCalendarArgs,
    conflicts,
    host_user_id: uuid.UUID | None,
    call_session_id: uuid.UUID | None,
    guest_profile_id: uuid.UUID | None,
) -> str:
    """The guest asked for the ENTIRE place on dates where one or more other
    guests already have ROOMS in it. That is not a plain "unavailable": the
    host may be able to move the room guests or decline the whole-place
    request, and only the host can decide. So: the guest hears there is a
    temporary hold on this booking, and the host gets one specific
    double-occupancy escalation to resolve (once per call and dates, however
    often the guest asks again), which also leaves a ticket that stays open
    until the host closes it."""
    details = "; ".join(
        f"{c.check_in:%d %b} to {c.check_out:%d %b} ({c.rooms_booked or 1} room"
        f"{'s' if (c.rooms_booked or 1) != 1 else ''}{', ' + c.guest_name if c.guest_name else ''})"
        for c in conflicts
        if c.stay_scope == stay_scope.ROOMS
    )
    phone, _ = await _caller_of(db, call_session_id)
    reason = (
        f"Double occupancy at {property_.name}: a guest wants the ENTIRE place from {args.check_in:%d %b %Y} to "
        f"{args.check_out:%d %b %Y}, but it already has room bookings on those dates -- {details}. Mira has told the "
        "guest there is a temporary hold on the whole-place booking while you resolve this. Please decide whether the "
        "room booking(s) stay (and the whole-place request is declined) or the dates can be freed, and tell the guest."
    )
    already_raised = call_session_id is not None and (
        await db.scalar(
            select(Ticket.id).where(
                Ticket.call_session_id == call_session_id,
                Ticket.kind == "escalation",
                Ticket.summary == reason[:255],
            )
        )
        is not None
    )
    if host_user_id is not None and not already_raised:
        try:
            await handle_escalate_to_host(
                db,
                EscalateToHostArgs(
                    property_id=str(property_.id),
                    reason=reason,
                    urgency="high",
                    guest_phone=phone,
                    call_summary=(
                        f"Guest asked for the entire place at {property_.name}, {args.check_in:%d %b} to "
                        f"{args.check_out:%d %b}, for {args.num_guests or 'an unspecified number of'} guests."
                    ),
                ),
                call_session_id,
                host_user_id,
                guest_profile_id=guest_profile_id,
            )
        except Exception:  # noqa: BLE001 - the guest still hears the right thing; the host alert is best-effort here
            logger.exception("double-occupancy escalation failed for property %s", property_.id)

    rooms_left = ""
    if stay_scope.offers_rooms(property_):
        one_room = await calendar_service.scope_availability(
            db, property_, stay_scope.ROOMS, args.check_in, args.check_out, 1
        )
        if one_room.available:
            rooms_left = " Individual rooms are still open for those dates if a smaller group would do."
    return (
        f"{property_.name} can't be confirmed as the ENTIRE place for those dates right now: another guest has "
        f"{_room_bookings_phrase(conflicts)} booked inside it. Tell the guest there is a TEMPORARY HOLD on this "
        "booking -- the overlap needs the host's decision, so you can't confirm the whole place yet. The host has "
        "been alerted about the double occupancy and will resolve it, and the guest will be updated with the "
        f"outcome. Do not promise the dates and do not say who booked, how, or for how much.{rooms_left}"
    )


async def _with_alternatives(
    db: AsyncSession,
    property_: Property,
    host_user_id: uuid.UUID | None,
    scope: str,
    args: CheckCalendarArgs,
    nights: int,
    rooms: int,
    base: str,
    on_alternatives: Callable[[Property, date, date, list], None] | None,
) -> str:
    """The "not available" answer plus the closest verified open dates, or an
    explicit "none found". The agent may offer ONLY what is listed here; the
    voice guard holds the spoken reply to the same set. Any failure to search
    degrades to "no dates to offer", never to a guess."""
    saturday_rule = bool(property_.saturday_minimum_stay_enabled) and nights < 2

    async def accept(start: date, end: date) -> bool:
        # A suggestion must be bookable: same minimum-stay rules as the check itself.
        if saturday_rule and any((start + timedelta(days=i)).weekday() == 5 for i in range(nights)):
            return False
        return await pricing_engine.minimum_stay_nights_violation(db, host_user_id, property_.id, start, end) is None

    try:
        windows = await calendar_service.closest_open_windows(
            db, property_, scope, args.check_in, nights, rooms, accept=accept
        )
    except Exception:
        logger.exception("closest_open_windows failed property_id=%s", property_.id)
        windows = []
        failed = True
    else:
        failed = False
    if on_alternatives is not None:
        on_alternatives(property_, args.check_in, args.check_out, windows)

    plural = "s" if nights != 1 else ""
    if windows:
        listed = "; ".join(
            f"{w.check_in.isoformat()} to {w.check_out.isoformat()} "
            f"({abs(w.shift_days)} day{'s' if abs(w.shift_days) != 1 else ''} {'later' if w.shift_days > 0 else 'earlier'})"
            for w in windows
        )
        return (
            f"{base} Closest open dates for the same {nights} night{plural}, each checked against the calendar: "
            f"{listed}. Offer ONLY these. For any other dates the guest suggests, call check_calendar for those "
            "exact dates first and say nothing about them until it answers."
        )
    if failed:
        return (
            f"{base} The search for other dates failed, so you have NO dates to offer: do not suggest any. "
            "Say you could not find open dates just now and that the team will follow up (escalate_to_host)."
        )
    return (
        f"{base} No open {nights}-night window was found within {calendar_service.ALTERNATIVE_HORIZON_DAYS} days "
        "either side of those dates. Do NOT suggest any dates: offer to look at a different property or have the "
        "team follow up (escalate_to_host)."
    )


async def handle_check_calendar(
    db: AsyncSession,
    args: CheckCalendarArgs,
    host_user_id: uuid.UUID | None = None,
    call_session_id: uuid.UUID | None = None,
    guest_profile_id: uuid.UUID | None = None,
    on_checked: Callable[[Property, bool], None] | None = None,
    on_alternatives: Callable[[Property, date, date, list], None] | None = None,
) -> str:
    """on_alternatives (alternative-dates fidelity): called only when the dates are
    NOT available, with the property, the guest's requested check-in/check-out and
    the verified alternative windows (calendar_service.OpenWindow; empty if none were
    found). It lets the voice layer hold the reply to exactly those dates.

    on_checked (Phase 4b.1, documentation/agent-conversation-improvement.md):
    same on_priced pattern as handle_get_pricing/handle_negotiate_rate -- an
    optional synchronous callback receiving the real Property and the actual
    availability bool right before it's rendered to the spoken string, called
    only once a real availability check has actually run (never on the
    not-found/invalid-dates/capacity/minimum-nights early returns above,
    since none of those represent a real yes/no answer to "is it available").
    """
    property_ = await _get_property(db, args.property_id)
    if property_ is None:
        return ToolBusinessError("I couldn't find that property. Could you confirm which listing you're asking about?")

    if args.check_out <= args.check_in:
        return ToolBusinessError("The check-out date needs to be after check-in. Could you confirm the dates?")
    if past_error := _past_dates_error(args.check_in):
        return past_error

    scope, rooms = _resolve_scope(property_, args.stay_scope, args.rooms)
    if scope == stay_scope.ROOMS:
        if error := stay_scope.validate_room_request(property_, rooms, args.num_guests):
            return error
    elif args.num_guests is not None and args.num_guests > property_.max_guests:
        return f"{property_.name} sleeps up to {property_.max_guests} guests, which is fewer than {args.num_guests}."

    nights_requested = (args.check_out - args.check_in).days
    if nights_requested < property_.minimum_nights:
        return (
            f"{property_.name} has a minimum stay of {property_.minimum_nights} nights -- "
            f"{nights_requested} night{'s' if nights_requested != 1 else ''} is below that. "
            "Would a longer stay work?"
        )

    # Weekend/Saturday minimum-stay rule -- independent of minimum_nights
    # above. Only kicks in when the requested stay actually includes a
    # Saturday night; a lone 1-night Saturday-only booking is rejected, a
    # multi-night stay that happens to include a Saturday (e.g. Fri-Sun) is
    # unaffected since it's already 2+ nights. "Includes a Saturday night"
    # means any of the stayed nights (check_in through check_out - 1 day)
    # falls on a Saturday -- Python's date.weekday() == 5. This is a simple,
    # always-on-if-toggled property-level rule -- see the note on the
    # rule-engine check right below for how this relates to that more
    # general mechanism (both are kept; neither replaces the other).
    if property_.saturday_minimum_stay_enabled and nights_requested < 2:
        stayed_nights = [args.check_in + timedelta(days=i) for i in range(nights_requested)]
        if any(night.weekday() == 5 for night in stayed_nights):
            return (
                f"{property_.name} requires a two-night minimum for stays that include a Saturday -- "
                "a Saturday-only night isn't bookable on its own. Would Saturday and Sunday together work?"
            )

    # Phase 6 (Negotiation engine): a SEPARATE, host-configured minimum-stay
    # floor (NegotiationRule rule_type="minimum_stay_nights"), on top of the
    # flat Property.minimum_nights check above and the simple Saturday
    # toggle above -- additive, never replaces either. A host with no such
    # rule configured sees zero change here (minimum_stay_nights_violation
    # returns None). Covers a more general case than the toggle above (any
    # min-nights floor, or a Friday-or-Saturday-inclusive weekend floor via
    # condition["weekend_min_nights"], not just "exactly Saturday").
    required_min_nights = await pricing_engine.minimum_stay_nights_violation(
        db, host_user_id, property_.id, args.check_in, args.check_out
    )
    if required_min_nights is not None:
        return (
            f"{property_.name} needs a minimum stay of {required_min_nights} nights for those dates -- "
            f"{nights_requested} night{'s' if nights_requested != 1 else ''} is below that. "
            "Would a longer stay work?"
        )

    if scope == stay_scope.ROOMS:
        availability = await calendar_service.scope_availability(
            db, property_, stay_scope.ROOMS, args.check_in, args.check_out, rooms
        )
        available = availability.available
    else:
        available = await calendar_service.is_available(db, property_.id, args.check_in, args.check_out)
    nights = (args.check_out - args.check_in).days

    if host_user_id is not None:
        # After the availability check, not before: whether the exact dates
        # came back available decides the temperature floor (availability
        # discussed = warm, availability confirmed = hot -- see
        # app/services/lead_temperature.py).
        await lead_service.backfill_lead_from_engagement(
            db,
            host_user_id,
            call_session_id,
            property_.name,
            args.check_in,
            args.check_out,
            args.num_guests,
            guest_profile_id=guest_profile_id,
            engagement="availability_confirmed" if available else "availability_checked",
        )

    if on_checked is not None:
        on_checked(property_, available)

    if available:
        what = f" ({stay_scope.scope_label(scope, rooms)})" if scope == stay_scope.ROOMS else ""
        return (
            f"{property_.name}{what} is AVAILABLE from {args.check_in.isoformat()} to {args.check_out.isoformat()} "
            f"({nights} night{'s' if nights != 1 else ''})."
        )

    held = await _on_hold_answer(db, property_, args.check_in, args.check_out, call_session_id)
    if held is not None:
        return held

    if scope == stay_scope.ROOMS:
        reason = (
            "the entire place is booked for part of those dates"
            if availability.reason == "entire_place_taken"
            else f"fewer than {rooms} room{'s' if rooms != 1 else ''} are free on every night"
        )
        base = f"{property_.name} has no {stay_scope.scope_label(scope, rooms)} available for those dates ({reason})."
        return await _with_alternatives(
            db, property_, host_user_id, stay_scope.ROOMS, args, nights, rooms, base, on_alternatives
        )

    # Whole place requested and the only thing in the way is other guests'
    # ROOM bookings: that is a double occupancy for the host to resolve, not a
    # plain "not available".
    occupancy = await calendar_service.scope_availability(
        db, property_, stay_scope.ENTIRE_PLACE, args.check_in, args.check_out
    )
    if occupancy.reason == "rooms_taken":
        return await _double_occupancy_answer(
            db, property_, args, occupancy.conflicts, host_user_id, call_session_id, guest_profile_id
        )

    return await _with_alternatives(
        db,
        property_,
        host_user_id,
        stay_scope.ENTIRE_PLACE,
        args,
        nights,
        1,
        f"{property_.name} is NOT available for those dates.",
        on_alternatives,
    )


_MEAL_NAMES = {"breakfast", "lunch", "dinner"}


def _quote_from_terms(
    property_: Property,
    terms: "spacez_stay_bill.Terms",
    args: GetPricingArgs,
    on_priced: Callable[[Property, PriceBreakdown], None] | None,
    host_user_id: uuid.UUID | None,
    call_session_id: uuid.UUID | None,
) -> str:
    """SPACEZ ONLY. The full booking bill for a villa that has commercial terms: stay, extra guests, meals, GST,
    and both ways to pay. Anything the terms do not state stops the quote with a plain reason; the
    agent never fills the gap. `total` handed to on_priced is what the guest pays in full."""
    meals = {m.strip().lower() for m in args.meals}
    adults = args.num_guests - args.children
    kits = tuple(k.strip().lower().replace(" ", "_") for k in args.addons)
    request = spacez_stay_bill.Request(
        check_in=args.check_in,
        check_out=args.check_out,
        adults=adults,
        children=args.children,
        pets=args.pets,
        breakfast="breakfast" in meals,
        lunch="lunch" in meals,
        dinner="dinner" in meals,
        welcome_drinks=args.welcome_drinks,
        party_platter_qty=args.party_platter_qty,
        addons=kits,
        pickup=args.pickup_drop,
        # Billed ONLY when the guest asked for it on this call (get_pricing never sets these on its own).
        early_checkin=args.requested_early_checkin,
        late_checkout=args.requested_late_checkout,
    )
    if meals - _MEAL_NAMES:
        return ToolBusinessError("Meals can only be breakfast, lunch or dinner.")
    try:
        bill = spacez_stay_bill.compute(terms, request)
    except spacez_stay_bill.BillInvalid as exc:
        return ToolBusinessError(f"{exc} Tell the guest this plainly and ask what they would like to change.")
    except spacez_stay_bill.BillUnknown as exc:
        return ToolBusinessError(
            f"I can't price this stay: {exc.what} is not on file. Tell the guest the team will confirm that, "
            "and call escalate_to_host so it is logged. Do not guess a number."
        )

    # SPACEZ ONLY -- what the agent is told to say aloud. By design the call hears just the room price and the
    # ONE total for the stay (everything that applies to the party, GST included); the line-by-line bill
    # summary goes to the guest on WhatsApp with the payment link (spacez_booking_confirmation.
    # payment_request_text). Coupons / offers are never answered on the call: the team gets a ticket.
    r = spacez_stay_bill.rupees
    nights = bill.nights
    subject = property_.name
    if bill.weekend_nights and bill.weekend_rate != bill.weeknight_rate:
        rate_phrase = (
            f"{r(bill.weeknight_rate)} a night on weeknights and {r(bill.weekend_rate)} a night on Friday and Saturday nights"
        )
    else:
        rate_phrase = f"{r(bill.weeknight_rate)} a night"
    extras = []
    if bill.early_checkin:
        extras.append("the early check-in")
    if bill.late_checkout:
        extras.append("the late check-out")
    summary = (
        f"For {subject}: the room is {rate_phrase}, for {nights} night{'s' if nights != 1 else ''}. "
        f"With everything that applies to this stay for {adults} adult{'s' if adults != 1 else ''}"
        + (f" and {args.children} child{'ren' if args.children != 1 else ''}" if args.children else "")
        + (f" (including {' and '.join(extras)})" if extras else "")
        + f", the total for the stay comes to {r(bill.full_payable)}, GST included. "
        "Say ONLY the room price and this one total -- do not read out a line-by-line breakdown. Then tell the guest "
        "you will share the payment link on WhatsApp shortly, with the bill summary. "
    )
    if terms.partner:
        summary += "This is a partner villa: payment is in full, with no split payment. "
    else:
        summary += (
            "If the guest wants to book, ask whether they would like to pay in full or split the payment half now and half "
            "before check-in, and only then use send_payment_link. "
        )
    if bill.early_checkin or bill.late_checkout:
        summary += (
            "The early check-in / late check-out is a flat charge that depends on the villa being free: say the team will "
            "confirm it. "
        )
    summary += (
        "If the guest asks about any coupon, bank offer or discount, do NOT answer it or say whether one exists: tell them "
        "the team will get back to them about it, and call escalate_to_host with the request as the reason."
    )

    breakdown = PriceBreakdown(
        nights=nights,
        base_total=float(bill.total_reservation),
        discount_percent=0.0,
        discount_amount=0.0,
        total=float(bill.full_payable),
        per_night_avg=round(float(bill.stay_total) / nights, 2),
        bill={
            **spacez_stay_bill.to_dict(bill),
            "partner": terms.partner,
            "request": {
                "adults": adults,
                "children": args.children,
                "pets": args.pets,
                "meals": sorted(meals),
                "welcome_drinks": args.welcome_drinks,
                "party_platter_qty": args.party_platter_qty,
                "addons": list(kits),
                "pickup": args.pickup_drop,
                "early_checkin": args.requested_early_checkin,
                "late_checkout": args.requested_late_checkout,
            },
        },
    )
    if on_priced is not None:
        on_priced(property_, breakdown)
    price_event_service.record_price_event_detached(
        user_id=host_user_id,
        property_id=property_.id,
        call_session_id=call_session_id,
        price_type="initial_quote",
        source="mira_conversation",
        price=breakdown.total,
        list_price=breakdown.base_total,
        nights=nights,
        check_in=args.check_in,
        check_out=args.check_out,
    )
    return summary


async def handle_get_pricing(
    db: AsyncSession,
    args: GetPricingArgs,
    host_user_id: uuid.UUID | None = None,
    call_session_id: uuid.UUID | None = None,
    guest_profile_id: uuid.UUID | None = None,
    on_priced: Callable[[Property, PriceBreakdown], None] | None = None,
    window_start: date | None = None,
) -> str:
    """on_priced (Phase 4.1, documentation/agent-conversation-improvement.md):
    optional synchronous callback receiving the real Property (for its name
    -- the caller's own property_id arg is a bare string/UUID, not something
    guest-facing) and PriceBreakdown right before the result is rendered to
    the spoken string, called only when a real quote is actually produced
    (never for the not-found/invalid-dates/zero-price early returns above).
    Lets app/voice/tools.py's wrapper record the exact quoted total into
    ConversationState without this function's own return type changing
    (still a plain str, matching every existing call site) and without
    re-parsing the rendered natural-language string back into a number --
    the same "hand over the real structured object directly" principle
    app/services/property/pitch_formatter.py's own docstring already
    explains the reasoning for (a prior version of
    property_recommendation_guard.py regex-parsed rendered text and broke
    when the format changed).

    window_start (vague-timeline pricing): the guest's own loose start-of-
    window estimate from state.slots, passed straight through to
    pricing_engine.calculate_price as a cache-only probe -- see that
    function's own docstring. Only ever used when args.check_in is unset."""
    property_ = await _get_property(db, args.property_id)
    if property_ is None:
        return ToolBusinessError(
            "I couldn't find that property to price. Could you confirm which listing you're asking about?"
        )

    has_exact_dates = args.check_in is not None and args.check_out is not None
    if has_exact_dates and args.check_out <= args.check_in:
        return ToolBusinessError("The check-out date needs to be after check-in. Could you confirm the dates?")
    if has_exact_dates and (past_error := _past_dates_error(args.check_in)):
        return past_error

    # Rent by the room: a room stay is priced by the same engine, handed the
    # room rate (stay_scope.priced_property); for the entire place `priced`
    # is the property itself, so that path is unchanged.
    scope, rooms = _resolve_scope(property_, args.stay_scope, args.rooms)
    if scope == stay_scope.ROOMS and (error := stay_scope.validate_room_request(property_, rooms, args.num_guests)):
        return ToolBusinessError(error)
    priced = stay_scope.priced_property(property_, scope, rooms)

    # Phase 6 (Negotiation engine) self-review fix: GOLDEN_RULES tells the
    # model get_pricing will surface a minimum-stay requirement -- this must
    # actually be true, not just check_calendar. Same check as
    # handle_check_calendar's own (host-configured, additive, host-opt-in;
    # a host with no minimum_stay_nights rule sees zero change here). Runs
    # for a nights-only call too -- minimum_stay_nights_violation's flat
    # min_nights floor is date-position-independent (a "3 nights minimum"
    # rule doesn't care which 3 nights), so it still applies; only its
    # weekend-specific floor is skipped without exact dates (see that
    # function's own docstring) -- that one gets its real enforcement from
    # handle_check_calendar's later re-check once the guest has exact dates.
    required_min_nights = await pricing_engine.minimum_stay_nights_violation(
        db, host_user_id, property_.id, args.check_in, args.check_out, nights=args.nights
    )
    if required_min_nights is not None:
        nights_requested = (args.check_out - args.check_in).days if has_exact_dates else args.nights
        return (
            f"{property_.name} needs a minimum stay of {required_min_nights} nights -- "
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
            engagement="pricing_requested",
        )

    # SPACEZ ONLY: a villa with booking terms is priced by the full bill (spacez_stay_bill), not the flat rate.
    terms = spacez_pricing_terms.terms_for(property_)
    if terms is not None and scope == stay_scope.ENTIRE_PLACE:
        if not has_exact_dates:
            return ToolBusinessError(
                f"{property_.name} is priced for exact dates (rates, GST and extras depend on them): ask the guest "
                "for their check-in and check-out dates before quoting."
            )
        return _quote_from_terms(property_, terms, args, on_priced, host_user_id, call_session_id)

    breakdown = await pricing_engine.calculate_price(
        db,
        priced,
        args.check_in,
        args.check_out,
        apply_discounts=args.apply_discounts,
        host_id=host_user_id,
        requested_early_checkin=args.requested_early_checkin,
        requested_late_checkout=args.requested_late_checkout,
        nights=args.nights,
        window_start=window_start,
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
        return ToolBusinessError(_PRICE_UNAVAILABLE_MESSAGE)

    # Lead with the per-night rate, then the total, as one natural spoken
    # sentence -- this string is what the LLM tends to read back almost
    # verbatim. No cleaning fee/tax markup to itemize (see
    # pricing_engine.calculate_price) -- base_total only differs from total
    # when a length-of-stay discount applies, so that's the only case worth
    # spelling out separately.
    subject = property_.name if scope == stay_scope.ENTIRE_PLACE else f"{property_.name} ({stay_scope.scope_label(scope, rooms)})"
    summary = (
        f"For {subject}, that's ₹{breakdown.per_night_avg:,.0f} per night, "
        f"₹{breakdown.total:,.0f} total for {breakdown.nights} night(s)"
    )
    if breakdown.discount_amount:
        summary += (
            f", including a {breakdown.discount_percent:.0f}% discount of ₹{breakdown.discount_amount:,.0f} "
            f"off the base rate of ₹{breakdown.base_total:,.0f}"
        )
    # Phase 6: only ever present when the guest explicitly asked (see
    # GetPricingArgs.requested_early_checkin/requested_late_checkout) --
    # never a silent addition to the total the guest didn't ask about.
    # Stated as its own separate figure, not folded into the total above,
    # so the guest hears exactly what they're being charged extra for.
    if breakdown.early_checkin_fee:
        summary += f". Early check-in is an extra ₹{breakdown.early_checkin_fee:,.0f}"
    if breakdown.late_checkout_fee:
        summary += f". Late checkout is an extra ₹{breakdown.late_checkout_fee:,.0f}"
    summary += "."
    # Vague-timeline pricing: breakdown.is_estimate is only ever True when
    # this ran off nights-only (no exact check_in/check_out) AND no cached
    # live rate covered the guest's own loose window -- see
    # pricing_engine.calculate_price's docstring. Tell the guest plainly
    # that this is a ballpark off the base rate, not a locked live-Airbnb
    # figure, and point them to the natural next step -- never leave the
    # model to imply this number is as firm as a dates-anchored quote.
    if breakdown.live_rate_unavailable:
        # Exact dates WERE given, but the live Airbnb rate could not be had
        # inside the call's time budget (pricing_engine.
        # _live_total_within_budget), so the figure above is the standard
        # rate. Say so plainly; asking again shortly is a real option because
        # the lookup keeps running and fills the cache for the next question.
        summary += (
            " Heads up -- I couldn't pull the live rate for those exact dates just now, so that's based on our "
            "standard rate and the final price may differ a little. If you'd like the precise figure, ask me to "
            "check again in a moment, or I can have the host confirm it."
        )
    elif breakdown.is_estimate:
        summary += (
            " Heads up -- since your dates aren't locked in yet, that's a ballpark based on our base rate; "
            "the actual price can run a bit higher or lower depending on demand for those dates. Call back, "
            "or let me know once you've settled on exact dates, and I can lock in the precise rate -- "
            "ideally about a week before your stay."
        )
    if on_priced is not None:
        on_priced(property_, breakdown)
    price_event_service.record_price_event_detached(
        user_id=host_user_id,
        property_id=property_.id,
        call_session_id=call_session_id,
        price_type="initial_quote",
        source="mira_conversation",
        price=breakdown.total,
        list_price=breakdown.base_total,
        nights=breakdown.nights,
        check_in=args.check_in,
        check_out=args.check_out,
    )
    return summary


async def handle_send_payment_link(
    db: AsyncSession,
    *,
    host_user_id: uuid.UUID,
    property_id: str,
    quote: dict | None,
    guest_name: str | None,
    phone: str | None,
    call_session_id: uuid.UUID | None,
    payment_option: str | None = None,
    email: str | None = None,
    purpose_of_stay: str | None = None,
    special_request: str | None = None,
) -> str:
    """Sends the guest a payment link for the price Mira last quoted and holds
    their dates. The amount and dates come from `quote` -- what Mira herself
    recorded when she quoted -- never from anything the model passes in."""
    host = await db.get(User, host_user_id)
    property_ = await _get_property(db, property_id)
    if host is None or property_ is None or property_.user_id != host_user_id:
        return ToolBusinessError("I couldn't find that property. Which one is the guest booking?")
    if not billing_service.is_connected(host):
        return ToolBusinessError(
            "Online payment isn't set up for this host. Tell the guest the host will confirm the booking and send payment details."
        )
    usable = (
        quote
        and not quote.get("is_estimate")
        and quote.get("check_in")
        and quote.get("check_out")
        and quote.get("property_name") == property_.name
        and float(quote.get("total") or 0) > 0
    )
    if not usable:
        return ToolBusinessError(
            "There is no confirmed price for this property and these exact dates yet. Get the exact dates, quote the price "
            "with get_pricing, and have the guest agree to it before sending a payment link."
        )
    if quote.get("stay_scope") == stay_scope.ROOMS:
        # Payment holds block the dates for the whole property and the payment
        # sweeper reads any overlapping booking as a clash, so a hold for a
        # room would wrongly close the other rooms and raise false double-
        # booking alerts. Room stays are confirmed by the host instead.
        return ToolBusinessError(
            "Online payment isn't available for room bookings. Tell the guest the host will confirm the room "
            "booking and send the payment details. Make sure the host knows (escalate_to_host)."
        )
    guest_phone, lead_id = await _caller_of(db, call_session_id)
    calling_from = guest_phone  # Spacez only: kept with the booking so a call back from it is recognised
    guest_phone = _normalize_phone(phone) if phone else guest_phone
    if not guest_phone:
        return ToolBusinessError("Ask the guest for the WhatsApp number to send the payment link to.")
    try:
        check_in, check_out = date.fromisoformat(quote["check_in"]), date.fromisoformat(quote["check_out"])
    except ValueError:
        return ToolBusinessError("I need the exact check-in and check-out dates before sending a payment link.")

    # SPACEZ ONLY -- a villa with a full bill: the booking needs the guest's details, and the guest chooses how
    # to pay. The WhatsApp message that carries the link then also carries the bill summary
    # (spacez_booking_confirmation.payment_request_text, picked in payment_service._send_request_message).
    bill = quote.get("bill")
    details: dict | None = None
    split_due = None
    force_host_confirm = False
    shoot = False
    if bill:
        asks = []
        if not spacez_booking_confirmation.valid_name(guest_name):
            asks.append("their full name (letters only)")
        if not spacez_booking_confirmation.valid_mobile(guest_phone):
            asks.append("a mobile number (9 to 15 digits)")
        if not spacez_booking_confirmation.valid_email(email):
            asks.append("their email address")
        purpose = spacez_booking_confirmation.map_purpose(purpose_of_stay)
        if purpose is None:
            asks.append("the purpose of the stay")
        if asks:
            return ToolBusinessError(
                "Before sending the link, ask the guest for " + ", ".join(asks) + ". Ask one at a time, then call this again."
            )
        option = (payment_option or "").strip().lower() or None
        if bill.get("partner"):
            option = "full"
        if option not in ("full", "split"):
            return ToolBusinessError(
                f"Ask the guest how they want to pay: in full ({payment_service.rupees(bill['full_payable'])}) or split "
                f"({payment_service.rupees(bill['split_now'])} now and {payment_service.rupees(bill['split_later'])} before "
                "check-in), then call this again with payment_option set to what they chose."
            )
        shoot = spacez_booking_confirmation.is_shoot(purpose)
        force_host_confirm = shoot
        split_due = bill["split_now"] if option == "split" else None
        details = {
            "bill": bill,
            "purpose": purpose,
            "email": email.strip(),
            "special_request": (special_request or "").strip()[:500] or None,
            "payment_option": option,
            "shoot": shoot,
            "caller_phone": calling_from,
        }

    # Make sure this opportunity is on file whatever else happens.
    await lead_service.backfill_lead_from_engagement(
        db, host_user_id, call_session_id, property_.name, check_in, check_out, None, engagement="availability_confirmed"
    )
    _, lead_id = await _caller_of(db, call_session_id)

    try:
        request = await payment_service.create_payment_request(
            db, host, property_, check_in=check_in, check_out=check_out, total=quote["total"],
            guest_name=guest_name, guest_phone=guest_phone, lead_id=lead_id, call_session_id=call_session_id,
            payment_option=details["payment_option"] if details else None, split_due=split_due, details=details,
            force_host_confirm=force_host_confirm,
        )
    except payment_service.DatesOnHold as exc:
        if _is_own_hold(exc.hold, guest_phone, call_session_id):
            return ToolBusinessError(
                f"This guest already has {property_.name} on hold from {exc.hold.check_in:%d %b %Y} to "
                f"{exc.hold.check_out:%d %b %Y}, with a payment link sent. A different range can't be held as well: "
                "ask them to pay for those dates, or say the host can change them."
            )
        answer = await _on_hold_answer(db, property_, check_in, check_out, call_session_id)
        return ToolBusinessError(answer or str(exc))
    except payment_service.DatesUnavailable:
        return ToolBusinessError(f"{property_.name} is no longer available for those dates. Offer other dates or another property.")
    except payment_service.PaymentError as exc:
        return ToolBusinessError(str(exc))

    minutes = _minutes_until(request.hold_expires_at)
    rest = ""
    if request.kind == "advance":
        rest = f" That is the advance; the remaining {payment_service.rupees(request.total_amount - request.amount_due)} is due by {request.balance_due_on:%d %b %Y}."
    sent = "sent on WhatsApp" if request.link_sent_at else "being sent by the host on WhatsApp shortly (WhatsApp did not accept it just now)"
    closing = (
        "Tell the guest the amount, that the link is on WhatsApp together with their bill summary, and how long the dates are held. "
        "Do not read out the link and never ask for card or UPI details on the call."
    )
    if details:
        closing += (
            " The booking is NOT confirmed yet: only a successful payment confirms it. When the guest says they have paid, "
            "or when they ask, call get_payment_status and say only what it tells you."
        )
        if shoot:
            closing += (
                " This is a shoot booking: once paid, the dates are held and the team calls to understand the crew and "
                "setup, then confirms. Never call it confirmed."
            )
    return (
        f"The payment link for {payment_service.rupees(request.amount_due)} is {sent}.{rest} "
        f"{property_.name} is held for this guest from {check_in:%d %b %Y} to {check_out:%d %b %Y} for the next {minutes} minutes. "
        + closing
    )


async def handle_get_payment_status(
    db: AsyncSession, *, host_user_id: uuid.UUID, call_session_id: uuid.UUID | None, guest_says_failed: bool = False
) -> str:
    """Whether the guest on this call has paid, asked of the payment provider right now. The only thing
    that may make the agent say a booking is confirmed."""
    from app.models.payment import PaymentRequest

    request = None
    if call_session_id is not None:
        request = await db.scalar(
            select(PaymentRequest)
            .where(PaymentRequest.user_id == host_user_id, PaymentRequest.call_session_id == call_session_id)
            .order_by(PaymentRequest.created_at.desc())
            .limit(1)
        )
    if request is None:
        return ToolBusinessError(
            "No payment link has been sent on this call, so there is no payment to check. Do not say anything about a payment."
        )
    try:
        request, last_attempt, provider_down = await payment_service.refresh_request(db, request.id)
    except payment_service.PaymentError as exc:
        return ToolBusinessError(str(exc))
    rupees = payment_service.rupees
    if provider_down:
        return ToolBusinessError(
            "I could not check the payment just now: the payment service did not answer. You do NOT know whether the guest has "
            "paid, so do not say it has or has not gone through and never say the booking is confirmed. Tell the guest you cannot "
            "see the payment right now and that the team will confirm it, then call escalate_to_host so it is logged."
        )
    details = request.details or {}
    if request.status == "paid":
        code = details.get("stay_code")
        shoot = bool(details.get("shoot"))
        ref = spacez_booking_confirmation.booking_ref(request) if details.get("bill") else None
        if shoot:
            return (
                f"PAID: {rupees(request.amount_due)} received. This is a shoot booking, so the dates are HELD, not yet confirmed: tell the "
                "guest their dates are held and the team will call to understand the crew and setup, then confirm. Never call it confirmed."
            )
        rest = ""
        if request.kind == "advance" and request.total_amount > request.amount_due:
            rest = f" The remaining {rupees(request.total_amount - request.amount_due)} is due before check-in."
        return (
            f"PAID: {rupees(request.amount_due)} received.{rest} The booking is now confirmed"
            + (f", booking id {ref}" if ref else "")
            + (f", and the guest's stay code is {code} (letters first, then the number: read it out slowly, letter by letter)" if code else "")
            + ". The confirmation with every detail is on their WhatsApp. You may now tell the guest the payment went through and the "
            "booking is confirmed."
        )
    if request.status in ("expired", "cancelled"):
        return (
            f"NOT PAID: this payment request has {'expired' if request.status == 'expired' else 'been cancelled'} and the dates are no "
            "longer held. Do NOT say the booking is confirmed. If the guest still wants the stay, check the dates again and send a new link."
        )
    minutes = _minutes_until(request.hold_expires_at)
    if last_attempt == "failed" or guest_says_failed:
        # Razorpay does not tell us about a failed attempt on a link that stays open, so the guest saying so is
        # the signal: record it (the dashboard shows it) and send the "try again" message with the same link.
        await payment_service.note_failed_attempt(db, request.id, source="provider" if last_attempt == "failed" else "guest")
        return (
            "NOT PAID: the guest's last payment attempt did not go through (they see: Payment unsuccessful. Almost there! Your villa is "
            f"held, just one more try needed to finish booking your stay). The dates stay held for about {minutes} more minute"
            f"{'s' if minutes != 1 else ''} and the same link can be tried again. Say that calmly, offer to wait while they try again, and "
            "do NOT say the booking is confirmed."
        )
    return (
        f"NOT PAID YET: the link for {rupees(request.amount_due)} was sent but no payment has come through. The dates are held for about "
        f"{minutes} more minute{'s' if minutes != 1 else ''}. Do NOT say the booking is confirmed; if the guest says they paid, ask them to "
        "wait a moment and check again."
    )


async def handle_dispatch_technician(
    db: AsyncSession, args: DispatchTechnicianArgs, call_session_id: uuid.UUID | None
) -> str:
    property_ = await _get_property(db, args.property_id)
    if property_ is None:
        return ToolBusinessError("I couldn't find that property to dispatch a technician for.")

    # "general" covers non-maintenance in-stay asks (towels, housekeeping,
    # amenity requests) routed here instead of escalate_to_host -- "our
    # general technician" reads oddly for a towel request, so use "caretaker"
    # in the guest-facing copy for that category specifically. Internal
    # notification text keeps saying "technician" either way, since that's
    # accurate for the host-facing record regardless of phrasing to the guest.
    role_label = "caretaker" if args.issue_type == "general" else f"{args.issue_type} technician"

    # Host turned technician dispatch off (Settings > Features & modules):
    # the issue is still logged and flagged to the host -- a guest's problem
    # must never just disappear -- but no technician is named or suggested.
    # The tool itself stays registered; only this outcome changes.
    if not await capability_service.is_capability_enabled(property_.user_id, "technician_dispatch"):
        notification = await notification_service.create_notification(
            db,
            channel="escalation",
            property_id=property_.id,
            call_session_id=call_session_id,
            urgency=args.urgency,
            message=(
                f"Guest reported a {args.issue_type} issue at {property_.name} (urgency: {args.urgency}). "
                "Technician dispatch is turned off -- please arrange this directly."
            ),
        )
        await ticket_service.open_ticket(
            db,
            kind="technician",
            summary=f"Arrange a {args.issue_type} fix at {property_.name}",
            detail="Technician dispatch is turned off, so the host must arrange this directly.",
            urgency=args.urgency,
            property_id=property_.id,
            call_session_id=call_session_id,
            notification_id=notification.id,
        )
        return f"I've flagged this to the host as {args.urgency} priority so they can arrange it directly."

    technician = await technician_service.find_technician(db, property_.id, args.issue_type)

    if technician is None:
        notification = await notification_service.create_notification(
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
        await ticket_service.open_ticket(
            db,
            kind="technician",
            summary=f"Arrange a {args.issue_type} fix at {property_.name}",
            detail=f"No {args.issue_type} technician is on file for this property.",
            urgency=args.urgency,
            property_id=property_.id,
            call_session_id=call_session_id,
            notification_id=notification.id,
        )
        return (
            f"I don't have a {role_label} on file for {property_.name} yet, "
            f"so I've flagged this for the host to arrange directly."
        )

    notification = await notification_service.create_notification(
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
    await ticket_service.open_ticket(
        db,
        kind="technician",
        summary=f"Confirm {technician.name} fixed the {args.issue_type} issue at {property_.name}",
        detail=f"Suggested technician: {technician.name} ({technician.phone}).",
        urgency=args.urgency,
        property_id=property_.id,
        call_session_id=call_session_id,
        notification_id=notification.id,
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


async def _send_whatsapp_or_open_ticket(
    to_phone: str, body: str, *, template: tuple[str, list] | None = None, **ticket
) -> None:
    """Sends in the background (the guest is still on the line), and if
    WhatsApp does not accept the message after the agent said it was sent,
    opens the ticket that makes sure the host sends it instead. A result of
    None (an unknown sender, e.g. a test double) is not treated as a failure.

    `template` = (key, values) sends the approved WhatsApp template of that name (whatsapp_templates.py,
    which also says where its SID is set) with `body` as the free-text fallback; a guest who has not messaged
    the business in the last 24 hours can only be reached that way."""
    try:
        if template is not None and whatsapp_templates.TEMPLATES[template[0]].sid:
            accepted = bool(await whatsapp_templates.send(to_phone, template[0], template[1], fallback_text=body))
        else:  # no template set up for this message yet: free text, exactly as before templates existed
            accepted = await twilio_client.send_whatsapp_best_effort(to_phone, body)
        if accepted is False:
            async with AsyncSessionLocal() as db:
                await ticket_service.open_ticket(db, **ticket)
    except Exception:
        logger.exception("WhatsApp send / ticket follow-up failed for %s", to_phone)


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
    # Don't claim a WhatsApp send is queued when Twilio is disabled -- the
    # in-app notification above still always fires, but there is no WhatsApp
    # fallback for this direct-message tool (unlike send_photos/
    # escalate_to_host, which have an email fallback because they have a
    # host to email); the host simply won't see this one on WhatsApp until
    # TWILIO_ENABLED is back on. See config.py's twilio_enabled.
    if settings.twilio_enabled:
        background.spawn(
            _send_whatsapp_or_open_ticket(
                args.phone,
                args.message,
                kind="whatsapp",
                summary=f"Send a WhatsApp message to {args.phone}",
                detail=f"WhatsApp did not accept Mira's message. Message: {args.message}",
                property_id=property_id,
                call_session_id=call_session_id,
            )
        )
        return f"Got it, I've queued a WhatsApp message to {args.phone}." + _phone_confirmation_warning(args.phone)
    await ticket_service.open_ticket(
        db,
        kind="whatsapp",
        summary=f"Send a WhatsApp message to {args.phone}",
        detail=f"Mira could not send it herself. Message: {args.message}",
        property_id=property_id,
        call_session_id=call_session_id,
    )
    return (
        f"I've noted that down, but I'm not able to send a WhatsApp message to {args.phone} right now."
        + _phone_confirmation_warning(args.phone)
    )


async def handle_send_photos(
    db: AsyncSession, args: SendPhotosArgs, call_session_id: uuid.UUID | None, host_user_id: uuid.UUID
) -> str:
    property_ = await _get_property(db, args.property_id)
    if property_ is None:
        return ToolBusinessError("I couldn't find that property to send photos for.")
    if not property_.photos:
        # The agent says it will flag this to the host, so something must
        # actually record it (it used to say so with nothing behind it).
        notification = await notification_service.create_notification(
            db,
            channel="whatsapp",
            property_id=property_.id,
            call_session_id=call_session_id,
            urgency="low",
            message=f"Guest {args.guest_phone} asked for photos of {property_.name}, but none are on file.",
        )
        await ticket_service.open_ticket(
            db,
            kind="photos",
            summary=f"Send photos of {property_.name} to {args.guest_phone}",
            detail="No photos are on file for this property, so Mira could not send any.",
            user_id=host_user_id,
            property_id=property_.id,
            call_session_id=call_session_id,
            notification_id=notification.id,
        )
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

    # Real WhatsApp send via Twilio -- only attempted when Twilio is enabled
    # (see config.py's twilio_enabled). Freeform (no Content Template), so
    # even when attempted this only succeeds inside Meta's 24h
    # customer-service session window -- see twilio_account_sid's docstring
    # in config.py.
    if settings.twilio_enabled:
        host = await db.get(User, host_user_id)
        background.spawn(
            _send_whatsapp_or_open_ticket(
                args.guest_phone,
                f"Here are photos of {property_.name} -- {gallery_url}",
                template=(
                    "photos",
                    [_first_name(args.guest_name), property_.display_name or property_.name, gallery_url, _host_label(host)],
                ),
                kind="photos",
                summary=f"Send photos of {property_.name} to {args.guest_phone}",
                detail=f"WhatsApp did not accept Mira's message. Gallery: {gallery_url}",
                user_id=host_user_id,
                property_id=property_.id,
                call_session_id=call_session_id,
            )
        )

    # Email/wa.me-link is a parallel channel when Twilio is enabled (so this
    # is testable against the host's own inbox even when the WhatsApp send
    # above fails, e.g. outside the 24h session window), and the ONLY
    # channel when Twilio is disabled -- build_photos_email_html includes
    # its own wa.me link built from args.guest_phone, so the host can always
    # reach the guest from the email regardless of Twilio's state.
    call_page_url = (
        f"{settings.frontend_base_url}/dashboard/calls/{call_session_id}" if call_session_id else None
    )
    host_user = await db.get(User, host_user_id)
    if host_user is not None:
        background.spawn(
            _send_escalation_email(
                host_user.notification_email or host_user.email,
                subject=f"Photos requested — {property_.name}",
                body=f"A guest ({args.guest_phone}) asked to see photos of {property_.name}.\n\n"
                f"Gallery link: {gallery_url}"
                + (f"\nCall details: {call_page_url}" if call_page_url else ""),
                html_body=build_photos_email_html(
                    property_name=property_.name,
                    guest_phone=args.guest_phone,
                    gallery_url=gallery_url,
                    call_page_url=call_page_url,
                ),
            )
        )

    if settings.twilio_enabled:
        return (
            f"Got it, I've sent a photo gallery link for {property_.name} to {args.guest_phone}."
            + _phone_confirmation_warning(args.guest_phone)
        )
    await ticket_service.open_ticket(
        db,
        kind="photos",
        summary=f"Send photos of {property_.name} to {args.guest_phone}",
        detail=f"Mira could not send them by WhatsApp. Gallery: {gallery_url}",
        user_id=host_user_id,
        property_id=property_.id,
        call_session_id=call_session_id,
    )
    return (
        f"Got it -- I've let the host know you'd like to see photos of {property_.name}, and they'll "
        f"reach out to you shortly." + _phone_confirmation_warning(args.guest_phone)
    )


def _first_name(name: str | None) -> str:
    parts = (name or "").split()
    return parts[0] if parts else "there"


def _host_label(host: User | None) -> str:
    return (host.business_name or host.name or "your host") if host is not None else "your host"


MAX_SHORTLIST = 8


async def handle_send_shortlist(
    db: AsyncSession, args: SendShortlistArgs, call_session_id: uuid.UUID | None, host_user_id: uuid.UUID
) -> str:
    """Sends the guest ONE WhatsApp message listing the properties recommended on the call, with a link to a page
    that shows each of them with its photos. Only this host's properties are ever listed."""
    ids: list[uuid.UUID] = []
    for raw in args.property_ids:
        try:
            ids.append(uuid.UUID(str(raw)))
        except ValueError:
            continue
    ids = list(dict.fromkeys(ids))[:MAX_SHORTLIST]
    if not ids:
        return ToolBusinessError("There are no properties to send yet. Recommend some first, then send the list.")
    rows = (
        await db.scalars(select(Property).where(Property.id.in_(ids), Property.user_id == host_user_id))
    ).all()
    by_id = {p.id: p for p in rows}
    properties = [by_id[i] for i in ids if i in by_id]
    if not properties:
        return ToolBusinessError("I couldn't find those properties to send.")
    link = f"{settings.frontend_base_url}/p/list?ids=" + ",".join(str(p.id) for p in properties)
    listing = " ".join(
        f"{n}) {p.display_name or p.name}" + (f", {p.city}" if p.city else "") for n, p in enumerate(properties, start=1)
    )
    host = await db.get(User, host_user_id)
    guest = _first_name(args.guest_name)
    await notification_service.create_notification(
        db,
        channel="whatsapp",
        property_id=properties[0].id,
        call_session_id=call_session_id,
        urgency="low",
        message=f"To {args.guest_phone}: the properties we recommended ({listing}) -- {link}",
    )
    if settings.twilio_enabled:
        background.spawn(
            _send_whatsapp_or_open_ticket(
                args.guest_phone,
                whatsapp_templates.render("shortlist", [guest, listing, link, _host_label(host)]),
                template=("shortlist", [guest, listing, link, _host_label(host)]),
                kind="photos",
                summary=f"Send the property list and photos to {args.guest_phone}",
                detail=f"WhatsApp did not accept Mira's message. List page: {link}",
                user_id=host_user_id,
                property_id=properties[0].id,
                call_session_id=call_session_id,
            )
        )
        return (
            f"Got it, I've sent {args.guest_phone} one WhatsApp message with the list of {len(properties)} propert"
            f"{'y' if len(properties) == 1 else 'ies'} we talked about and a link to see each with photos."
            + _phone_confirmation_warning(args.guest_phone)
        )
    await ticket_service.open_ticket(
        db,
        kind="photos",
        summary=f"Send the property list and photos to {args.guest_phone}",
        detail=f"Mira could not send them by WhatsApp. List page: {link}",
        user_id=host_user_id,
        property_id=properties[0].id,
        call_session_id=call_session_id,
    )
    return (
        "Got it -- I've let the host know you'd like the list and photos, and they'll reach out to you shortly."
        + _phone_confirmation_warning(args.guest_phone)
    )


async def handle_escalate_to_host(
    db: AsyncSession,
    args: EscalateToHostArgs,
    call_session_id: uuid.UUID | None,
    host_user_id: uuid.UUID,
    guest_profile_id: uuid.UUID | None = None,
) -> str:
    """property_id is optional -- a guest can ask to be escalated/transferred
    to the host before ever choosing a specific property (e.g. right at the
    top of a portfolio-wide Lead Agent call). host_user_id is what actually
    routes this escalation to the right host; property_ is only used to name
    which property it's about, when one is known. A property_id that doesn't
    resolve to a real row degrades the same way -- named property unknown,
    escalation still goes through -- rather than failing the whole call, so
    a stale/bad id from the model never blocks a guest from reaching the
    host."""
    property_ = await _get_property(db, args.property_id) if args.property_id else None
    property_name = property_.name if property_ is not None else None
    subject_line = property_name or "your account"

    message = f"Escalation for {subject_line}: {args.reason}"
    if args.call_summary:
        message += f" | Summary: {args.call_summary}"
    if args.guest_phone:
        message += f" | Guest: {args.guest_phone}"

    # Don't rely on the LLM separately remembering to call update_lead too --
    # capture whatever this escalation already has (phone, summary) on the
    # lead record directly, so an escalated call is never left with an empty
    # CRM lead just because the model only made the one tool call. Created
    # before the notification below (not after) so a property-less
    # escalation can pass this lead's id through -- see the lead_id comment
    # on the create_notification call.
    lead = await lead_service.upsert_lead(
        db,
        host_user_id,
        call_session_id,
        guest_profile_id=guest_profile_id,
        phone=args.guest_phone,
        conversation_summary=args.call_summary,
        properties_discussed=[property_name] if property_name else [],
        escalated=True,
    )

    notification = await notification_service.create_notification(
        db,
        channel="escalation",
        property_id=property_.id if property_ is not None else None,
        # A property-less escalation (Lead Agent line, no property chosen
        # yet) would otherwise be permanently invisible on the dashboard --
        # list_notifications' property_id.in_(...) filter can never match a
        # NULL property_id. Same lead_id -> Lead.user_id fallback already
        # relied on for busy-recovery's own property-less notifications (see
        # that function's own comment); only needed when there's no
        # property_id to match on, so property-scoped escalations keep their
        # existing (simpler) matching path.
        lead_id=lead.id if property_ is None else None,
        call_session_id=call_session_id,
        urgency=args.urgency,
        message=message,
    )
    await ticket_service.open_ticket(
        db,
        kind="escalation",
        summary=args.reason,
        detail=message,
        urgency=args.urgency,
        user_id=host_user_id,
        property_id=property_.id if property_ is not None else None,
        call_session_id=call_session_id,
        notification_id=notification.id,
    )

    # In-app notification above covers hosts watching the dashboard live;
    # this email covers the far more common case of a host who isn't. Fired
    # detached (not awaited) so a slow/misconfigured SMTP server never adds
    # latency to this tool call's result -- the guest is still on the line.
    host_user = await db.get(User, host_user_id)
    if host_user is not None:
        # Deep-links to this specific call's transcript + AI summary rather
        # than the generic leads list -- a host clicking through from an
        # escalation email almost always wants THIS call.
        call_page_url = (
            f"{settings.frontend_base_url}/dashboard/calls/{call_session_id}"
            if call_session_id
            else f"{settings.frontend_base_url}/dashboard/leads"
        )
        # The host's own lead labels (Settings > Notifications) name the
        # tier in the subject and badge; lead_temperature may be None if
        # update_lead was never called (shown as their "not qualified"
        # label). Escalation emails go to notification_email (a shared
        # front-desk inbox, say) or the login email, unless the host turned
        # them off -- the in-app notification above always stays.
        prefs = notification_preferences_service.get(host_user)
        lead_label = notification_preferences_service.lead_label(prefs, lead.lead_temperature)
        if prefs.escalation_email:
            background.spawn(
                _send_escalation_email(
                    host_user.notification_email or host_user.email,
                    subject=f"[{lead_label}] {args.urgency.title()} escalation — {subject_line}",
                    body=f"{message}\n\nView in dashboard: {call_page_url}",
                    html_body=build_escalation_email_html(
                        property_name=subject_line,
                        urgency=args.urgency,
                        reason=args.reason,
                        call_summary=args.call_summary,
                        guest_phone=args.guest_phone,
                        call_session_id=call_session_id,
                        lead_temperature=lead.lead_temperature,
                        lead_label=lead_label,
                    ),
                )
            )
        # WhatsApp via Twilio (see twilio_client.py) to the host's transfer
        # number for this property (one account number, or the property
        # group's own -- notification_preferences_service
        # .host_transfer_phone). Unset phone or unconfigured/disabled Twilio
        # no-op silently in _send_escalation_whatsapp, which is why the
        # guest-facing return string below never promises "on WhatsApp".
        host_phone = notification_preferences_service.host_transfer_phone(host_user, property_)
        if host_phone and settings.twilio_enabled:
            background.spawn(
                _send_escalation_whatsapp(
                    host_phone,
                    urgency=args.urgency,
                    property_name=subject_line,
                    reason=args.reason,
                    call_summary=args.call_summary,
                    guest_phone=args.guest_phone,
                    dashboard_url=call_page_url,
                )
            )

    return f"I've flagged this to the host as {args.urgency} priority, and they'll follow up with you shortly."


# Same digit-only, +91-defaulting normalization as webhooks/exotel.py's
# _normalize_destination_phone -- duplicated rather than imported, since
# that module is in app/api/v1/ and already imports from app/services/
# (call_ownership, call_service), so importing it back from here would be
# circular. This codebase already carries a couple of near-identical copies
# of this same normalization (see app/utils/phone.py's own docstring); this
# is one more, kept minimal and scoped to exactly what this function needs
# (a yes/no on "is this phone usable"), not the full webhook response shape.
def _has_usable_host_phone(phone: str | None) -> bool:
    if not phone or not phone.strip():
        return False
    digits = "".join(ch for ch in phone if ch.isdigit())
    return len(digits) >= 10


# When the host prefers WhatsApp over live transfers -- matches
# escalation_phrase_guard's own wording, which speaks next.
_WHATSAPP_FOLLOW_UP_REPLY = (
    "I've passed your details to the host, and they'll reach out to you on WhatsApp shortly."
)

# What the guest hears when a transfer can't be attempted -- the escalation
# behind it has already notified the host (in-app, email, WhatsApp).
_HOST_UNAVAILABLE_REPLY = (
    "The host isn't available to take the call at the moment. I've let them know, and they'll follow up with "
    "you shortly."
)


async def handle_request_host_transfer(
    db: AsyncSession,
    args: RequestHostTransferArgs,
    call_session_id: uuid.UUID | None,
    property_id: uuid.UUID | None,
    host_user_id: uuid.UUID,
    guest_profile_id: uuid.UUID | None = None,
    context_property_id: uuid.UUID | None = None,
) -> str:
    """Guest explicitly asked to be transferred/connected to the host or a
    human, right now -- distinct from escalate_to_host's general "notify the
    host, guest stays with Mira" path. Attempts a REAL live call transfer by
    reusing the existing host-initiated "Take Call" machinery
    (app/voice/handoff_signal.py, app/voice/pipeline.py's
    _wait_and_trigger_handoff, and the /webhooks/exotel/connect-routing
    webhook that resolves User.phone) -- none of those are modified here;
    this function only needs to (a) decide whether a transfer is even
    possible, and (b) if so, make the exact same atomic handoff_status claim
    take_call.py makes, then call handoff_signal.request_handoff. That
    webhook keys its routing decision purely on CallSession.handoff_status
    and CallSession.user_id, not on property_id or who requested it, so a
    guest-initiated and host-initiated handoff, and a property-scoped and
    portfolio-wide (Lead Agent) call, are all indistinguishable to it by
    design -- see exotel_connect_routing's own "user_id, not property_id"
    comment. property_id is deliberately NOT a gate here anymore: it's only
    ever used below to enrich the escalation fallback's args when present.
    """
    escalate_args = EscalateToHostArgs(
        property_id=str(property_id) if property_id is not None else None,
        reason=args.reason or "Guest asked to be transferred to the host",
        urgency="high",
    )
    host_user = await db.get(User, host_user_id)
    prefs = notification_preferences_service.get(host_user) if host_user is not None else None
    if (
        prefs is not None
        and prefs.connect_request_handling == "whatsapp"
        and prefs.stay_request_handling == "whatsapp"
    ):
        # The host wants no live transfers at all (Settings > Notifications)
        # -- the prompt already steers Mira away from offering one; this is
        # the enforcement if the tool is called anyway. When only one of
        # the two choices is "live transfer", this tool serves that one and
        # the prompt decides which requests use it (it can't tell them
        # apart here).
        await handle_escalate_to_host(db, escalate_args, call_session_id, host_user_id, guest_profile_id)
        return _WHATSAPP_FOLLOW_UP_REPLY

    # The property in scope picks the transfer number when the host uses a
    # number per group of properties: the call's own property, else the
    # one the guest settled on during a Lead Agent call.
    transfer_property_id = property_id or context_property_id
    transfer_property = await db.get(Property, transfer_property_id) if transfer_property_id else None
    destination_phone = (
        notification_preferences_service.host_transfer_phone(host_user, transfer_property)
        if host_user is not None
        else None
    )
    if not _has_usable_host_phone(destination_phone) or call_session_id is None:
        # No transfer destination Exotel could actually dial, or nothing to
        # claim a handoff against (e.g. a browser test call with no real
        # CallSession id) -- fall back to a plain escalation, so the guest
        # still reaches the host, just via notification instead of a live
        # connect.
        await handle_escalate_to_host(db, escalate_args, call_session_id, host_user_id, guest_profile_id)
        return _HOST_UNAVAILABLE_REPLY

    # The exact same atomic claim take_call.py's POST handler makes --
    # exotel_connect_routing trusts this DB state, not the caller's
    # identity, so replicating it here (rather than some other signal) is
    # what makes this handoff routable at all. Also closes the TOCTOU race
    # against a host's own "Take Call" WhatsApp tap landing at the same time.
    stmt = (
        update(CallSession)
        .where(
            CallSession.id == call_session_id,
            CallSession.handoff_status.is_(None),
            CallSession.status.in_(_ACTIVE_CALL_STATUSES),
        )
        .values(handoff_status="requested", handoff_destination_phone=destination_phone)
        # "fetch" (not False) so any CallSession instance already loaded
        # into this session's identity map (e.g. by earlier code in the
        # same request/task) gets its in-memory handoff_status corrected
        # to match what this UPDATE just wrote, instead of silently going
        # stale -- take_call.py's own equivalent claim achieves the same
        # end via an explicit db.refresh(session) right after, since it
        # already holds that object; this function never loads CallSession
        # itself, so "fetch" is the equivalent fix at the statement level.
        .execution_options(synchronize_session="fetch")
    )
    result = await db.execute(stmt)
    await db.commit()

    if result.rowcount == 0:
        # Either the call already ended, or a handoff was already claimed
        # (e.g. the host tapped Take Call at the same moment) -- either way,
        # don't attempt a second signal/claim. A generic, still-true line:
        # if a handoff really is already in flight, the deterministic
        # handoff phrase from that other trigger will speak momentarily
        # anyway; if the call already ended, this return value never
        # reaches a live guest to hear it.
        return "One moment, I'm connecting you now."

    # This transfer path is Exotel/telephony only -- deliberately does NOT
    # check settings.twilio_enabled anywhere in this function. Twilio
    # outages must never block a live call transfer.
    request_handoff(call_session_id)
    await _record_transfer_for_follow_up(db, args, call_session_id, property_id, host_user_id, guest_profile_id)

    # _wait_and_trigger_handoff (already running as a background task for
    # every property-scoped call) speaks the deterministic handoff phrase
    # once it observes this signal -- keep this tool's own returned string
    # short and non-contradictory, since the model will speak this FIRST,
    # then the deterministic phrase fires moments later.
    return "Sure, connecting you to the host now. If they can't pick up, they'll follow up with you shortly."


async def _record_transfer_for_follow_up(
    db: AsyncSession,
    args: RequestHostTransferArgs,
    call_session_id: uuid.UUID,
    property_id: uuid.UUID | None,
    host_user_id: uuid.UUID,
    guest_profile_id: uuid.UUID | None,
) -> None:
    """Once the call is handed to Exotel, Mira's leg ends -- if the host
    doesn't pick up, nothing ever comes back to this codebase. An in-app
    notification and a flagged lead are written at transfer time so a missed
    transfer still leaves the host something to follow up on. In-app only
    (no email/WhatsApp, unlike escalate_to_host): the host's phone is already
    ringing. Best-effort -- runs after request_handoff and never raises, so
    it can't delay or break the transfer itself."""
    try:
        lead = await lead_service.upsert_lead(
            db, host_user_id, call_session_id, guest_profile_id=guest_profile_id, transferred_to_host=True
        )
        notification = await notification_service.create_notification(
            db,
            channel="escalation",
            property_id=property_id,
            lead_id=lead.id if property_id is None else None,
            call_session_id=call_session_id,
            urgency="high",
            message=(
                f"Guest asked to be transferred to you: {args.reason or 'no reason given'}. "
                "If you missed the call, please follow up with them."
            ),
        )
        await ticket_service.open_ticket(
            db,
            kind="transfer",
            summary=f"Call back the guest who asked for you: {args.reason or 'no reason given'}",
            detail="Mira transferred the call. If you did not pick up, the guest is waiting to hear from you.",
            urgency="high",
            user_id=host_user_id,
            property_id=property_id,
            call_session_id=call_session_id,
            notification_id=notification.id,
        )
    except Exception:
        logger.exception("Recording transfer follow-up failed for call_session_id=%s", call_session_id)


async def handle_lookup_booking(db: AsyncSession, args: LookupBookingArgs, host_user_id: uuid.UUID) -> str:
    """A guest asking about a booking they already made (often after naming a
    date that has passed). Phone matches get the full stay details; name-only
    matches share just the property and dates -- anyone can say a name. Phone
    numbers are never read back either way. No match hands off to
    request_host_transfer, which itself falls back to a "host will follow up"
    escalation when the host can't be reached."""
    matches = await guest_booking_service.find_bookings(
        db, host_user_id, phone=args.phone, name=args.name, property_hint=args.property_name
    )
    searched_under = " / ".join(value for value in (args.name, args.phone) if value)
    if not matches:
        return ToolBusinessError(
            f"No booking found under {searched_under}. Tell the guest you couldn't find a booking under that, then "
            "call request_host_transfer so the host can help them directly."
        )

    name_only = [m for m in matches if m.matched_by == "name"]
    if len(name_only) > 3:
        return ToolBusinessError(
            f"Several bookings match the name {args.name}. Ask the guest for the phone number the booking was made "
            "with, or which property they stayed at, then call lookup_booking again."
        )

    today = today_ist()
    lines = []
    for match in matches:
        if match.check_in and match.check_out:
            dates = f"{match.check_in.isoformat()} to {match.check_out.isoformat()}"
            when = "past stay" if match.is_past(today) else ("current stay" if match.check_in <= today else "upcoming")
        else:
            dates, when = "dates not on file", "booked"
        line = f"- {match.property_name}, {dates} ({when})"
        if match.matched_by == "phone" and match.guest_name:
            line += f", under {match.guest_name}"
        if match.matched_by == "name":
            line += " [matched by name only]"
        lines.append(line)

    note = (
        "\nFor bookings matched by name only, share just the property and dates, nothing else about the booking."
        if name_only
        else ""
    )
    return "Bookings found:\n" + "\n".join(lines) + note


async def handle_negotiate_rate(
    db: AsyncSession,
    args: NegotiateRateArgs,
    host_user_id: uuid.UUID | None = None,
    guest_profile_id: uuid.UUID | None = None,
    call_session_id: uuid.UUID | None = None,
    on_priced: Callable[[Property, NegotiationResult], None] | None = None,
    prior_events: list["NegotiationEvent"] | None = None,
    window_start: date | None = None,
    apply_stay_offer: bool = False,
) -> str:
    """apply_stay_offer: negotiate from the automatic length-of-stay offer
    price (already pitched to the guest by recommend_properties) instead of
    the standard rate -- see pricing_engine.negotiate_rate's apply_discounts.

    on_priced (Phase 4b.1, documentation/agent-conversation-improvement.md):
    same pattern as handle_get_pricing's own on_priced -- an optional
    synchronous callback receiving the real Property and NegotiationResult
    right before the result is returned, called only on a real successful
    negotiation (never on an error/not-found/non-positive-price path). Lets
    app/voice/tools.py's wrapper feed property_recommendation_guard the real
    counter_offer total to verify against, without this function's return
    type changing or any existing call site being affected.

    prior_events (Phase 4D): this call's negotiation history within the
    current call, already property-scoped by ConversationState -- passed
    straight through to pricing_engine.negotiate_rate (see that function's
    own docstring). Optional/None-default so every pre-Phase-4D call site
    keeps resolving at stage 0, i.e. today's exact behavior.

    window_start (vague-timeline pricing): same cache-only probe as
    handle_get_pricing's own -- see that function's docstring."""
    property_ = await _get_property(db, args.property_id)
    if property_ is None:
        return ToolBusinessError("I couldn't find that property to negotiate a rate for.")

    has_exact_dates = args.check_in is not None and args.check_out is not None
    if has_exact_dates and args.check_out <= args.check_in:
        return ToolBusinessError("The check-out date needs to be after check-in. Could you confirm the dates?")
    if has_exact_dates and (past_error := _past_dates_error(args.check_in)):
        return past_error

    scope, rooms = _resolve_scope(property_, args.stay_scope, args.rooms)
    if scope == stay_scope.ROOMS and (error := stay_scope.validate_room_request(property_, rooms, args.num_guests)):
        return ToolBusinessError(error)

    if spacez_pricing_terms.terms_for(property_) is not None:
        # A villa with full booking terms has no discount rule on file: the price quoted stands, and
        # a discount is never invented. The team can look at it.
        # SPACEZ ONLY: coupons / bank offers / discounts are never answered on the call. The agent neither
        # confirms nor denies one exists; the team gets a ticket and gets back to the guest.
        return ToolBusinessError(
            "Do NOT answer whether any coupon, offer or discount exists or applies, and never offer or hint at a reduction. "
            "Tell the guest the team will get back to them about it, and call escalate_to_host now with the guest's "
            "coupon/offer/discount request as the reason (urgency medium)."
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
            engagement="negotiation_initiated",
        )

    result = await pricing_engine.negotiate_rate(
        db,
        stay_scope.priced_property(property_, scope, rooms),
        args.check_in,
        args.check_out,
        args.guest_offer,
        args.guest_loyalty,
        host_id=host_user_id,
        guest_profile_id=guest_profile_id,
        prior_events=prior_events,
        nights=args.nights,
        window_start=window_start,
        apply_discounts=apply_stay_offer,
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
        return ToolBusinessError(_PRICE_UNAVAILABLE_MESSAGE)
    message = result.message
    # Vague-timeline pricing: same caveat as handle_get_pricing's own --
    # result.is_estimate mirrors PriceBreakdown.is_estimate (nights-only,
    # no cached live rate covering the guest's loose window). Per user
    # decision, negotiation itself still proceeds normally off this
    # estimate (the host's own discount policy still applies) -- this only
    # tells the guest the FIGURE being negotiated is provisional, not that
    # negotiation itself is blocked.
    if result.live_rate_unavailable:
        message += (
            " Just a heads-up -- I couldn't pull the live rate for those exact dates just now, so this is worked "
            "out from our standard rate and the final price may differ a little. I can check again in a moment "
            "for the precise figure."
        )
    elif result.is_estimate:
        message += (
            " Just a heads-up -- since your dates aren't locked in yet, this is worked out from our base "
            "rate as a ballpark, so the exact live price could land a little differently. Once you're closer "
            "to your dates, call back and I can confirm the precise rate and finalize this for you."
        )
    if on_priced is not None:
        on_priced(property_, result)
    price_event_service.record_price_event_detached(
        user_id=host_user_id,
        property_id=property_.id,
        call_session_id=call_session_id,
        # accepted is also True when the guest offered nothing and Mira
        # proposed its best (floor) price -- that's Mira's counter, not the
        # guest accepting, so only a real guest_offer counts as accepted.
        price_type="accepted_offer" if result.accepted and args.guest_offer is not None else "counter_offer",
        source="mira_conversation",
        price=result.counter_offer,
        list_price=result.asking_price,
        guest_offer=args.guest_offer,
        nights=(args.check_out - args.check_in).days if has_exact_dates else args.nights,
        check_in=args.check_in,
        check_out=args.check_out,
    )
    return message


async def handle_recommend_properties(
    db: AsyncSession,
    args: RecommendPropertiesArgs,
    host_user_id: uuid.UUID,
    check_in: date | None = None,
    check_out: date | None = None,
    nights: int | None = None,
    call_session_id: uuid.UUID | None = None,
    amenity_weights: dict[str, float] | None = None,
    stay_check_in: date | None = None,
    stay_check_out: date | None = None,
    stay_window_start: date | None = None,
    budget_basis_assumed: bool = False,
) -> RecommendationResult:
    """Thin delegate to app/services/property/retrieval/orchestrator.py --
    the actual filter -> SQL search -> (conditionally) semantic search ->
    merge/rank -> build context pipeline lives there, split into focused,
    single-responsibility modules (filter_builder/sql_search/
    semantic_search/ranking/context_builder). check_in/check_out (Phase 2.4,
    documentation/agent-conversation-improvement.md) are optional,
    caller-supplied from ConversationState.slots, not part of
    RecommendPropertiesArgs itself -- see orchestrator.recommend_properties's
    own docstring. nights (Availability-first recommendations, Implementation
    3) is the same pass-through shape, used as the required stay length for
    partial-availability classification when check_in/check_out are known;
    orchestrator.recommend_properties falls back to (check_out - check_in).days
    when this is omitted. call_session_id (Phase 2.5) seeds the leading-
    candidate diversity rotation so the SAME call stays internally consistent
    while DIFFERENT calls see genuine variety. amenity_weights (attention/
    salience tracking, ConversationState.attention) is optional, same
    pass-through shape as check_in/check_out -- see orchestrator.recommend_properties.
    stay_check_in/stay_check_out/stay_window_start/budget_basis_assumed feed
    applicable-stay pricing and budget eligibility -- same pass-through."""
    return await property_retrieval_orchestrator.recommend_properties(
        db,
        args,
        host_user_id,
        check_in=check_in,
        check_out=check_out,
        nights=nights,
        call_session_id=call_session_id,
        amenity_weights=amenity_weights,
        stay_check_in=stay_check_in,
        stay_check_out=stay_check_out,
        stay_window_start=stay_window_start,
        budget_basis_assumed=budget_basis_assumed,
    )


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
    # nights/window_start/window_end have no matching Lead column -- popped
    # before **updates reaches upsert_lead, which blindly setattr()s every
    # key it's given (see UpdateLeadArgs.nights' own comment). Handled
    # separately by the caller (app/voice/tools.py's update_lead wrapper),
    # written only to ConversationState.slots, never to the DB.
    stay_length_known = updates.pop("nights", None) is not None
    updates.pop("window_start", None)
    updates.pop("window_end", None)
    # A past stay is never a new booking's dates -- keep everything else
    # (name/phone matter most for a previous-booking enquiry), drop the dates,
    # and say so: update_lead is where the model records a date the moment
    # the guest says it, usually long before any availability/pricing call,
    # so a silent "Saved." here let a past date go unchallenged (confirmed
    # live 2026-10-08: "2nd October" on 8 Oct was accepted and qualified).
    past_error = _past_dates_error(args.check_in) if args.check_in is not None else None
    if past_error:
        updates.pop("check_in", None)
        updates.pop("check_out", None)
    await lead_service.upsert_lead(
        db,
        host_user_id,
        call_session_id,
        guest_profile_id=guest_profile_id,
        stay_length_known=stay_length_known,
        **updates,
    )
    if past_error:
        return ToolBusinessError(
            f"Saved the other details, but not the dates. {past_error}"
            + _phone_confirmation_warning(updates.get("phone"))
        )
    return "Saved." + _phone_confirmation_warning(updates.get("phone"))


async def handle_search_faq(
    db: AsyncSession,
    args: SearchFaqArgs,
    host_user_id: uuid.UUID,
    default_property_id: uuid.UUID | None,
    call_session_id: uuid.UUID | None = None,
    on_answered: Callable[[Property], None] | None = None,
    on_result: Callable[[Property | None, kb_retrieval.KbSearchResult], None] | None = None,
) -> str:
    """The agent's only route to property facts. Searches the property's
    knowledge base (verified FAQ rows + its own fields + active seasonal notes,
    see app/services/property/kb_retrieval.py) and returns ONLY the facts that
    bear on the question -- never the whole property blob -- or an explicit
    "nothing on file" that the agent must relay instead of answering from its
    own knowledge. A "nothing on file" result is logged as a gap for the host
    (always with the property it was about) and raises one in-app notification
    for the call.

    on_answered (Phase 4b.3, documentation/agent-conversation-improvement.md):
    optional synchronous callback receiving the real, resolved Property (for
    its .amenities) -- called whenever a specific property was resolved
    (never for a portfolio-wide query with no property at all). Lets
    app/voice/tools.py's wrapper feed property_recommendation_guard the real
    on-file amenity list.

    on_result: optional synchronous callback receiving (property or None, the
    KbSearchResult) -- feeds the knowledge grounding guard
    (app/voice/kb_grounding_guard.py) the exact facts the agent was handed,
    which is what every spoken claim is later checked against."""
    from app.prompts.system_prompt import KB_MISS_LINE  # local: system_prompt <-> services import cycle

    property_id = None
    if args.property_id:
        try:
            property_id = uuid.UUID(args.property_id)
        except ValueError:
            property_id = None
    property_id = property_id or default_property_id

    property_ = await _get_property(db, str(property_id)) if property_id is not None else None
    property_facts = await kb_retrieval.load_property_facts(db, property_) if property_ is not None else []
    portfolio_facts = await kb_retrieval.load_portfolio_facts(db, host_user_id)
    vocabulary_extra = (
        " ".join(filter(None, [property_.name, property_.display_name, property_.spoken_name, property_.city]))
        if property_ is not None
        else ""
    )
    result = await kb_retrieval.search_facts(args.query, property_facts, portfolio_facts, vocabulary_extra)

    if property_ is not None:
        if on_answered is not None:
            on_answered(property_)
    if on_result is not None:
        try:
            on_result(property_, result)
        except Exception:
            logger.exception("search_faq on_result callback failed -- ignoring")

    if result.status == "none":
        try:
            await kb_gap_service.log_kb_gap(
                db,
                user_id=host_user_id,
                property_id=property_id,
                call_session_id=call_session_id,
                question=args.query,
                source="search_miss",
            )
        except Exception:
            # A logging failure must never break the guest-facing response.
            await db.rollback()
            logger.exception("Failed to log a knowledge-base gap for query=%r", args.query[:80])

    name = (property_.display_name or property_.name) if property_ is not None else "this host's properties"
    text = kb_retrieval.render_kb_result(name, result, KB_MISS_LINE)
    if property_ is None and result.status != "found":
        text += (
            " No specific property is selected for this question: if the guest is asking about a particular "
            "property, ask which one before answering."
        )
    return text
