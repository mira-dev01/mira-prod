"""Routes an inbound WhatsApp reply (app/api/v1/webhooks/whatsapp.py) to the
right Busy Recovery menu option (see recovery_service.py,
scripts/create_busy_recovery_template.py's numbered menu). Reuses existing
identity and CRM primitives throughout -- no new conversation/session
entity:

- GuestProfile/Lead identity: a reply is correlated to the SAME Lead
  recovery_service.py already created for this guest, found via the
  sender's phone number (see _resolve_recovery_lead below). This IS
  "continue the existing conversation, don't create another one" -- there
  is no new thread/session object anywhere, only a lookup back into the
  Lead the guest already has.
- Property/Pricing/FAQs/Brochure content: read from the exact same
  services the voice tools already use (pricing_engine's data source,
  faq_service.list_verified_property_faq, Property.photos) -- no
  duplicated business logic, no new copy of "how MIRA answers a pricing
  question."
- "Continue conversation" and "Call host" never talk to an LLM (see the
  design decision recorded during this phase's own scoping) -- both just
  notify the host via the existing Notification table and let the host
  reply personally. No text-based AI agent exists or is built here.

Every path here is a reply to an already-outbound WhatsApp send (the
Busy Recovery menu) -- it never fires unprompted, and never creates a Lead
of its own; if no matching recovery Lead is found, this is a no-op (logged),
not an error.
"""

import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.integrations import twilio_client
from app.models.guest_profile import GuestProfile
from app.models.lead import Lead
from app.models.property import Property
from app.models.user import User
from app.services import faq_service, notification_service
from app.services.lead_service import _REUSABLE_LEAD_STATUSES

logger = logging.getLogger(__name__)

NOTIFICATION_CHANNEL_BUSY_RECOVERY_REPLY = "busy_recovery_reply"

# How long a busy-recovery reply is still considered "the same conversation"
# after the guest was rejected. Principal-review finding: _resolve_recovery_lead
# previously matched the guest's most-recently-updated recovery lead with NO
# time bound and NO status filter at all -- a guest texting anything, weeks
# after a single busy rejection, would silently reattach to that old lead
# even if the host had already marked it booked/closed (or it was simply
# stale). 72h comfortably covers a guest replying a day or two later (the
# menu message itself never expires/times out on Twilio's side) while
# ruling out "this is unrelated, ignore it" for anything genuinely old.
RECOVERY_REPLY_WINDOW = timedelta(hours=72)

# SINGLE SOURCE OF TRUTH for the busy-recovery menu -- previously this
# existed as three independent hand-copies (this parser's own keyword dict,
# recovery_service.py's plain-text fallback, and
# scripts/create_busy_recovery_template.py's real Content Template body),
# with only a code comment asking whoever edits one to also edit the other
# two. Confirmed live during review: nothing enforced that, so a wording
# change to any one would silently desync the others with no error and no
# test catching it. Now: this list is the ONLY place the six menu options
# are defined; MENU_DISPLAY_TEXT (the numbered block guests see) and
# _parse_menu_choice (what a reply routes to) are both derived from it, and
# recovery_service.py / create_busy_recovery_template.py import
# MENU_DISPLAY_TEXT directly rather than embedding their own copy.
_MENU_OPTIONS: list[tuple[str, str, tuple[str, ...]]] = [
    # (choice_key, guest-facing label, extra reply keywords beyond the number/label)
    ("property", "Property details", ("details",)),
    ("pricing", "Pricing", ("price",)),
    ("faq", "FAQs", ("faq", "questions")),
    ("brochure", "Photos", ("brochure", "pictures")),
    ("continue", "Talk to the host", ("talk", "host")),
    ("other", "Something else", ("other",)),
]

MENU_DISPLAY_TEXT = "\n".join(
    f"{i}. {label}" for i, (_choice, label, _keywords) in enumerate(_MENU_OPTIONS, start=1)
)

_STOPWORDS = {"to", "the", "a", "of", "and"}


def _label_words(label: str) -> tuple[str, ...]:
    # "Talk to the host" -> ("talk", "host") -- individual significant
    # words (short stopwords excluded -- "the pool is great" must never
    # startswith-match "the" and misroute to "continue"), not just the full
    # label, so a guest typing just "property" (a substring of the label
    # "Property details", not a prefix/suffix startswith match in either
    # direction) still routes correctly. Matches the original hand-written
    # _MENU_KEYWORDS' behavior exactly (confirmed by
    # test_parse_menu_choice_recognizes_numbers_and_keywords).
    return tuple(w.lower() for w in label.split() if w.lower() not in _STOPWORDS and len(w) > 2)


_MENU_KEYWORDS: dict[str, tuple[str, ...]] = {
    choice: (str(i), label.lower(), *_label_words(label), *keywords)
    for i, (choice, label, keywords) in enumerate(_MENU_OPTIONS, start=1)
}


def _parse_menu_choice(body: str) -> str:
    """Returns "other" (never None) for anything unrecognized -- a real
    guest typing a free-text question deserves the same host handoff as an
    explicit "6", not to be silently dropped."""
    normalized = body.strip().lower()
    for choice, keywords in _MENU_KEYWORDS.items():
        if normalized in keywords or any(normalized.startswith(k) for k in keywords):
            return choice
    return "other"


def _to_bare_digits(whatsapp_from: str) -> str:
    """Twilio's inbound From is 'whatsapp:+91XXXXXXXXXX' -- GuestProfile/Lead
    phone columns store the same bare-ish format outbound sends already use
    (see twilio_client._to_whatsapp_address, the inverse of this)."""
    return re.sub(r"^whatsapp:", "", whatsapp_from)


async def _resolve_recovery_lead(db: AsyncSession, phone: str) -> Lead | None:
    """Inbound Twilio gives only the sender's phone -- no host context.
    GuestProfile/Lead identity is (phone, host_id) everywhere else in this
    codebase (see call_service.get_or_create_guest_profile), which assumes
    the host is already known; here it isn't, so this resolves it THROUGH
    the reply itself: the guest's most recently touched recovery lead
    (recovery_reason IS NOT NULL) for this phone, across every host that
    phone has a GuestProfile with. Most-recent-wins is the same heuristic
    lead_service._get_or_create_lead_for_call already uses for reuse
    ordering -- if a phone number somehow has open recovery leads with two
    different hosts at once, the reply is assumed to be about whichever one
    they most recently interacted with.

    Two guards (principal-review fix -- previously neither existed):
    - Lead.status.in_(_REUSABLE_LEAD_STATUSES): same rule the voice path's
      own lead reuse already enforces (lead_service._get_or_create_lead_for_call).
      Without this, a reply could silently reopen/mutate a lead the host
      already resolved (booked/closed) via the dashboard -- a real guest
      texting "thanks!" days after being booked should not reach back into
      that closed record.
    - updated_at >= now - RECOVERY_REPLY_WINDOW: without this, ANY reply
      from a phone number that ever had a recovery lead, no matter how
      stale, silently reattaches to it -- there is no actual conversation
      continuing at that point, just two unrelated events sharing a phone
      number.
    """
    since = datetime.now(timezone.utc) - RECOVERY_REPLY_WINDOW
    stmt = (
        select(Lead)
        .join(GuestProfile, Lead.guest_profile_id == GuestProfile.id)
        .where(
            GuestProfile.phone == phone,
            Lead.recovery_reason.is_not(None),
            Lead.status.in_(_REUSABLE_LEAD_STATUSES),
            Lead.updated_at >= since,
        )
        .order_by(Lead.updated_at.desc())
        .limit(1)
    )
    return await db.scalar(stmt)


async def _resolve_property(db: AsyncSession, lead: Lead) -> Property | None:
    """Reuses GuestProfile.last_property_id -- set by recovery_service.py
    for exactly this purpose (see that module's own comment) -- rather than
    inventing a new property reference on Lead itself."""
    if lead.guest_profile_id is None:
        return None
    guest = await db.get(GuestProfile, lead.guest_profile_id)
    if guest is None or guest.last_property_id is None:
        return None
    return await db.get(Property, guest.last_property_id)


async def handle_inbound_reply(db: AsyncSession, whatsapp_from: str, body: str) -> None:
    """Entry point -- the only function app/api/v1/webhooks/whatsapp.py
    should call. Never raises (principal-review fix): the webhook route had
    no exception boundary at all, so any unhandled error here (a bad phone
    format, a DB hiccup, an edge case in menu routing) returned a 500 to
    Twilio, which retries on failure -- risking a duplicate host
    notification/WhatsApp send for the same guest reply if the first attempt
    partially succeeded before raising. Same fail-safe contract as
    recovery_service.handle_busy_recovery's own public entry point."""
    try:
        await _handle_inbound_reply(db, whatsapp_from, body)
    except Exception:
        logger.exception("Inbound WhatsApp reply handling failed for %s", whatsapp_from)


async def _handle_inbound_reply(db: AsyncSession, whatsapp_from: str, body: str) -> None:
    phone = _to_bare_digits(whatsapp_from)
    lead = await _resolve_recovery_lead(db, phone)
    if lead is None:
        logger.info("Inbound WhatsApp reply from %s has no matching recovery lead; ignoring", phone)
        return

    choice = _parse_menu_choice(body)
    property_ = await _resolve_property(db, lead)

    if choice == "property":
        await _reply_property(phone, property_)
    elif choice == "pricing":
        await _reply_pricing(phone, property_)
    elif choice == "faq":
        await _reply_faq(db, phone, property_)
    elif choice == "brochure":
        await _reply_brochure(phone, property_)
    else:
        # "continue" (talk to host) and "other" (free text) both just
        # notify the host and let them reply personally -- see module
        # docstring on why no AI auto-response exists here.
        await _notify_host_of_reply(db, lead, phone, body, choice, property_)


_NO_PROPERTY_TEXT = "Sorry, I couldn't find which property you're asking about -- could you tell us the name?"


async def _reply_property(phone: str, property_: Property | None) -> None:
    if property_ is None:
        await twilio_client.send_whatsapp_best_effort(phone, _NO_PROPERTY_TEXT)
        return
    name = property_.display_name or property_.name
    lines = [f"*{name}*"]
    if property_.usp:
        lines.append(property_.usp)
    guest_line = f"Sleeps up to {property_.max_guests} guests"
    if property_.bedroom_count:
        guest_line += f", {property_.bedroom_count} bedrooms"
    lines.append(guest_line + ".")
    lines.append(f"Check-in {property_.check_in_time}, check-out {property_.check_out_time}.")
    await twilio_client.send_whatsapp_best_effort(phone, "\n".join(lines))


async def _reply_pricing(phone: str, property_: Property | None) -> None:
    if property_ is None:
        await twilio_client.send_whatsapp_best_effort(phone, _NO_PROPERTY_TEXT)
        return
    # No dates are known for a busy-recovery reply (unlike the voice tool's
    # get_pricing, which always has guest-stated check_in/check_out) --
    # pricing_engine.calculate_price requires concrete dates, so this quotes
    # the property's on-file nightly rate directly instead of inventing
    # placeholder dates. Same non-positive-price guard as
    # tool_handlers.handle_get_pricing -- never claim a stay is free (see
    # that handler's own comment on the real ₹0-quoted-as-free incident).
    if property_.base_price <= 0:
        await twilio_client.send_whatsapp_best_effort(
            phone, "We'll confirm the exact nightly rate with the host and follow up shortly."
        )
        return
    name = property_.display_name or property_.name
    await twilio_client.send_whatsapp_best_effort(
        phone,
        f"*{name}* starts from ₹{property_.base_price:,.0f}/night. "
        "Tell us your check-in and check-out dates and we'll confirm the exact total.",
    )


_FAQ_ANSWER_MAX_CHARS = 300  # per-entry cap -- see below for why


async def _reply_faq(db: AsyncSession, phone: str, property_: Property | None) -> None:
    if property_ is None:
        await twilio_client.send_whatsapp_best_effort(phone, _NO_PROPERTY_TEXT)
        return
    entries = await faq_service.list_verified_property_faq(db, property_.id)
    if not entries:
        await twilio_client.send_whatsapp_best_effort(
            phone, "We don't have FAQs on file for this property yet -- the host will follow up directly."
        )
        return
    # FaqEntry.answer is an unbounded Text column -- host-authored answers
    # are normally short, but nothing stops one from being long, and
    # WhatsApp's own message limit is ~4096 characters. Capping COUNT alone
    # (entries[:5]) doesn't bound total LENGTH; this caps each answer too,
    # so five real entries can never together risk a silent oversized-send
    # failure (send_whatsapp_best_effort swallows a Twilio error with no
    # guest-visible fallback if that happens).
    lines = [f"*Frequently asked -- {property_.display_name or property_.name}*"]
    for entry in entries[:5]:
        answer = entry.answer
        if len(answer) > _FAQ_ANSWER_MAX_CHARS:
            answer = answer[:_FAQ_ANSWER_MAX_CHARS].rstrip() + "…"
        lines.append(f"\n*Q: {entry.question}*\n{answer}")
    await twilio_client.send_whatsapp_best_effort(phone, "\n".join(lines))


async def _reply_brochure(phone: str, property_: Property | None) -> None:
    if property_ is None or not property_.photos:
        await twilio_client.send_whatsapp_best_effort(
            phone, "We don't have photos on file for this property yet -- the host will follow up directly."
        )
        return
    # Same gallery URL tool_handlers.handle_send_photos already sends during
    # a live call -- one link, not N image attachments, reusing the
    # Cloudinary URLs already on the property (see that handler's own
    # comment on why).
    gallery_url = f"{settings.frontend_base_url}/p/{property_.id}/photos"
    name = property_.display_name or property_.name
    await twilio_client.send_whatsapp_best_effort(phone, f"Here are photos of *{name}* -- {gallery_url}")


async def _notify_host_of_reply(
    db: AsyncSession, lead: Lead, phone: str, body: str, choice: str, property_: Property | None
) -> None:
    # Notification (channel="busy_recovery_reply") carries lead_id so
    # Recovery Analytics (app/api/v1/analytics.py) can join this reply back
    # to the busy_recovery notification that started it, via the shared
    # Lead -- see app/models/notification.py's lead_id comment. What
    # actually keeps this tied to "the same conversation, not a new one" is
    # lead.next_follow_up below, updated on the EXISTING Lead row
    # _resolve_recovery_lead found -- no new Lead is ever created for a
    # reply. Host follows up personally via WhatsApp; nothing here generates
    # a reply.
    #
    # property_id MUST be passed (not None) even though Notification.
    # property_id is nullable at the column level: both real consumers,
    # GET /notifications and /notifications/stream (app/api/v1/
    # notifications.py), always filter with
    # `Notification.property_id.in_(user_property_ids)` -- a NULL never
    # matches an IN-list, so a None here would make this notification
    # invisible on the dashboard permanently, not just missing context.
    # Confirmed live: RecoveryService's own notifications already always
    # pass a real property_id for exactly this reason.
    stated = body.strip()
    property_label = f" about {property_.display_name or property_.name}" if property_ is not None else ""
    label = f"wants to talk to the host{property_label}" if choice == "continue" else f'replied{property_label}: "{stated}"'
    message = f"Guest {label} | Guest: {phone}"
    await notification_service.create_notification(
        db,
        channel=NOTIFICATION_CHANNEL_BUSY_RECOVERY_REPLY,
        property_id=property_.id if property_ is not None else None,
        call_session_id=None,
        lead_id=lead.id,
        urgency="medium",
        message=message,
    )

    lead.next_follow_up = "Guest replied on WhatsApp -- call or message them back"
    await db.commit()

    host = await db.get(User, lead.user_id)
    if host is not None and host.phone:
        await twilio_client.send_whatsapp_best_effort(
            host.phone, f"\U0001F7E0 *GUEST REPLIED*\n{message}\n\n{settings.frontend_base_url}/dashboard/leads"
        )
