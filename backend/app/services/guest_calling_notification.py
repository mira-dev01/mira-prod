"""Phase 5: "guest is calling Mira right now" host notification -- fires
once, in-app + WhatsApp, the moment a real guest call has a live
CallSession and is confirmed MIRA-owned. Phase 6 addition: the WhatsApp
message also carries a signed, single-use Take Call link (see app/
services/take_call_token.py, app/api/v1/take_call.py) -- this module only
MINTS that token and includes it in the message text; it does not verify
tokens, does not claim the handoff, and has no live-call-control
capability of its own (that stays in take_call.py; still no telephony
action exists anywhere in this codebase as of Phase 6 -- claiming only
flips CallSession.handoff_status, nothing dials anyone yet).

Deliberately its own module rather than folded into recovery_service.py --
that module's docstring specifically scopes it to BUSY_RECOVERY (a call
that was REJECTED); this fires on the opposite case, a call that
successfully reached Mira. Architecturally identical shape, though, and
copies its pattern directly: called fire-and-forget via asyncio.create_task
from app/voice/pipeline.py, strictly after the pipeline's own `db` session
has closed (opens its own AsyncSessionLocal() here, same reasoning as
recovery_service.handle_busy_recovery's own docstring -- this must not
depend on a session whose lifetime is tied to the live call's websocket).
Never raises into the caller: any failure here must never be capable of
affecting the still-live guest call this notification is ABOUT.

Reuses, never reimplements: call_service.get_property_by_number is NOT
called here (the caller already has the resolved Property); Phase 2's
resolve_effective_call_owner is the sole MIRA/HOST decision; notification_
service.create_notification is the sole Notification writer; app/
integrations/twilio_client.py's send_whatsapp_template_best_effort/
send_whatsapp_best_effort is the sole WhatsApp send path (same fire-and-
forget contract every other WhatsApp send in this codebase already uses,
not reimplemented locally).
"""

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.integrations import twilio_client
from app.models.notification import Notification
from app.models.property import Property
from app.models.user import User
from app.services import call_ownership, notification_service
from app.services.take_call_token import issue_take_call_token

logger = logging.getLogger(__name__)

NOTIFICATION_CHANNEL_GUEST_CALLING = "guest_calling"


async def maybe_notify_guest_calling(
    property_id: uuid.UUID,
    call_session_id: uuid.UUID,
    caller_number: str | None,
) -> None:
    """Entry point -- the only function app/voice/pipeline.py should call,
    fired via asyncio.create_task immediately after a real CallSession is
    created for a live call. Never raises (logs, swallows) -- see module
    docstring."""
    try:
        async with AsyncSessionLocal() as db:
            await _maybe_notify_guest_calling(db, property_id, call_session_id, caller_number)
    except Exception:
        logger.exception(
            "guest_calling notification failed for property_id=%s call_session_id=%s",
            property_id,
            call_session_id,
        )


async def _maybe_notify_guest_calling(
    db: AsyncSession,
    property_id: uuid.UUID,
    call_session_id: uuid.UUID,
    caller_number: str | None,
) -> None:
    # Idempotency: one guest_calling notification per CallSession, ever.
    # No new table for this -- the Notification row itself is the dedup
    # source of truth (an existence check on (call_session_id, channel)
    # before any write), same "reuse what's already persisted, don't add
    # infrastructure for a one-row check" instinct as everywhere else in
    # this phase. Guards against a reconnect/retry re-entering the pipeline
    # for the same exotel_call_id -- get_or_create_call_session is already
    # idempotent on that, returning the SAME session either way, so a
    # naive "always notify after session creation" would double-send.
    already_sent = await db.scalar(
        select(Notification.id).where(
            Notification.call_session_id == call_session_id,
            Notification.channel == NOTIFICATION_CHANNEL_GUEST_CALLING,
        )
    )
    if already_sent is not None:
        logger.info(
            "guest_calling notification already sent for call_session_id=%s -- skipping", call_session_id
        )
        return

    property_ = await db.get(Property, property_id)
    if property_ is None:
        logger.warning("guest_calling: property %s not found -- skipping", property_id)
        return

    # Single source of truth for the MIRA/HOST decision (Phase 2) -- not
    # reimplemented here. Re-checked at this point (not assumed from "the
    # pipeline is running, therefore MIRA") because Phase 4's Exotel
    # Passthru routing is not yet live in the account (pending manual
    # console configuration, see Phase 4's own report) -- until that's
    # wired, every call still reaches this code path regardless of
    # call_handling_mode, so skipping this check would send guest_calling
    # notifications for HOST-owned properties too. Once Passthru IS live,
    # this becomes a fast, always-MIRA no-op belt-and-suspenders check --
    # never removed, since a defensive re-check costs one function call and
    # protects against exactly the kind of silent architecture-assumption
    # drift this whole feature is built to avoid.
    try:
        owner = call_ownership.resolve_effective_call_owner(property_, datetime.now(timezone.utc))
    except call_ownership.InvalidCallOwnershipConfigError:
        logger.exception(
            "guest_calling: invalid call-ownership configuration for property_id=%s -- skipping", property_id
        )
        return

    if owner is not call_ownership.CallOwner.MIRA:
        logger.info(
            "guest_calling: property_id=%s call_session_id=%s resolved to %s, not MIRA -- skipping",
            property_id,
            call_session_id,
            owner,
        )
        return

    host = await db.get(User, property_.user_id)
    if host is None:
        logger.warning("guest_calling: host for property_id=%s not found -- skipping", property_id)
        return

    property_name = property_.spoken_name or property_.display_name or property_.name
    guest_label = caller_number or "Unknown number"

    await notification_service.create_notification(
        db,
        channel=NOTIFICATION_CHANNEL_GUEST_CALLING,
        property_id=property_.id,
        call_session_id=call_session_id,
        urgency="low",
        message=f"Guest is calling Mira for {property_name} -- guest: {guest_label}",
    )

    # In-app notification above covers hosts watching the dashboard live
    # (existing SSE stream, unchanged -- see app/api/v1/notifications.py);
    # WhatsApp covers a host who isn't. Best-effort only, fired detached so
    # a slow/misconfigured Twilio call never adds latency to -- or is
    # even on the same call stack as -- the still-live guest call this
    # notification is about. host.phone is User.phone, the one canonical
    # host contact number this codebase already has (see Phase 5's own
    # report) -- reused as-is, not duplicated onto Property.
    if not host.phone:
        logger.info("guest_calling: host %s has no phone set -- skipping WhatsApp send", host.id)
        return

    # Phase 6: the signed, single-use Take Call link -- see
    # app/services/take_call_token.py's own docstring for why this is a
    # new, narrow token type rather than a Clerk session. Minted fresh for
    # every notification (not cached/reused), scoped to exactly this
    # call_session_id/property_id/host_user_id triple. The claim itself
    # (app/api/v1/take_call.py) is the only place this URL leads to --
    # nothing here performs or promises any telephony action.
    take_call_token = issue_take_call_token(call_session_id, property_.id, host.id)
    take_call_url = f"{settings.backend_base_url}/api/v1/take-call?token={take_call_token}"

    if settings.twilio_guest_calling_template_sid:
        await twilio_client.send_whatsapp_template_best_effort(
            host.phone,
            settings.twilio_guest_calling_template_sid,
            {"1": property_name, "2": guest_label, "3": take_call_url},
        )
    else:
        await twilio_client.send_whatsapp_best_effort(
            host.phone,
            f"📞 Guest is calling Mira\nProperty: {property_name}\nGuest: {guest_label}\n\n"
            f"Mira is currently handling the call.\n\nTake the call yourself: {take_call_url}",
        )
