"""RecoveryService: consumes a BUSY_RECOVERY decision from CallCoordinator
and turns a rejected call into something the host can actually follow up
on. This is NOT CallCoordinator and does not replace it -- CallCoordinator's
only job is answering "can this host/property accept another live call?"
(see app/services/call_coordinator.py); it returns BUSY_RECOVERY and knows
nothing beyond that. RecoveryService is the consumer triggered BY that
decision, with none of CallCoordinator's lease/concurrency concerns.

Deliberately reuses the exact same primitives a normal in-call escalation
already uses (see tool_handlers.handle_escalate_to_host, the closest
existing analog): GuestProfile via call_service.get_or_create_guest_profile,
Lead via lead_service.upsert_lead, Notification via
notification_service.create_notification, WhatsApp via
app/integrations/twilio_client.py's send_whatsapp_best_effort (the same
fire-and-forget wrapper tool_handlers.py's WhatsApp sends also use -- see
that function's own docstring; this module does not reimplement its
try/except/log contract locally). Nothing here is a new
entity -- no WaitingGuest, no BusyCall table, and NOT a new Lead.status
value either (documentation/architecture: Phase 4) -- status stays the
normal default "open" so a recovery lead flows through the existing
Kanban/reuse logic exactly like any other fresh inbound lead, per
lead_service._REUSABLE_LEAD_STATUSES. How/why this lead needed recovery is
recorded via Lead.recovery_reason="BUSY_CALL" (see app/models/lead.py's own
comment for the full lead_source/entry_channel/recovery_reason split) --
lead_source is deliberately left at its normal "voice_call" default here,
not overloaded to also mean "this was a recovery," since that's exactly
what recovery_reason now exists to say without duplicating lead_source's
original meaning. Alongside an ordinary Notification
(channel="busy_recovery"), both already visible through the existing
dashboard/SSE stream with zero new UI.

Called fire-and-forget from app/voice/pipeline.py's BUSY_RECOVERY branch,
via asyncio.create_task -- same pattern tool_handlers.py already uses for
every WhatsApp/email send (never awaited by a live call, since by
definition the guest has already been hung up on by the time this runs; a
slow/failed send here must never be capable of affecting a *different*,
still-live call). No blocking operations: every external call (DB, Twilio)
is already async, and the one genuinely slow/unreliable leg (the WhatsApp
send) is itself fired via its own asyncio.create_task, so a Twilio sandbox
hiccup can't even delay this function's own remaining DB writes.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.integrations import twilio_client
from app.models.property import Property
from app.models.user import User
from app.schemas.lead import RecoveryReason
from app.services import call_service, guest_memory_service, lead_service, notification_service
from app.services.whatsapp_reply_service import MENU_DISPLAY_TEXT

logger = logging.getLogger(__name__)

# Explains WHY this lead needed recovery, without inventing a new
# Lead.status value or overloading lead_source's original meaning -- see
# app/models/lead.py's own comment for the full lead_source/entry_channel/
# recovery_reason split (documentation/architecture: Phase 4). status stays
# "open" (the normal default), so a recovery lead is indistinguishable from
# any other fresh lead everywhere the dashboard already reads status from
# (Kanban columns, _REUSABLE_LEAD_STATUSES) -- recovery_reason is purely
# explanatory metadata, never a pipeline stage.
RECOVERY_REASON_BUSY_CALL: RecoveryReason = "BUSY_CALL"

NOTIFICATION_CHANNEL_BUSY_RECOVERY = "busy_recovery"

# Plain-text fallback for when TWILIO_BUSY_RECOVERY_TEMPLATE_SID isn't
# configured -- same "template preferred, plain text always works" contract
# as tool_handlers._build_escalation_whatsapp_text. MENU_DISPLAY_TEXT
# (imported from whatsapp_reply_service.py) is the single source of truth
# for the numbered options -- see that module's own comment for why this
# used to be three independent, driftable copies.
def _guest_recovery_whatsapp_text(property_name: str | None) -> str:
    where = property_name or "us"
    return (
        f"Hi! Sorry we missed your call to {where} just now -- our line was busy with another guest.\n\n"
        f"Reply with a number and we'll help right away:\n{MENU_DISPLAY_TEXT}"
    )


@dataclass(frozen=True)
class RecoveryMetadata:
    """What actually happened, for any future consumer (analytics,
    occupancy metrics, a recovery-queue dashboard view) that wants a
    structured answer instead of re-deriving it from the Lead/Notification
    rows. Not persisted anywhere itself -- see module docstring on why this
    stays data, not a new entity."""

    lead_id: uuid.UUID
    guest_profile_id: uuid.UUID | None
    notification_id: uuid.UUID
    host_user_id: uuid.UUID
    property_id: uuid.UUID | None
    caller_number: str | None
    rejected_at: datetime


async def handle_busy_recovery(
    host_user_id: uuid.UUID,
    property_id: uuid.UUID | None,
    caller_number: str | None,
    dialed_number: str | None,
) -> RecoveryMetadata | None:
    """Entry point -- the only function app/voice/pipeline.py should call,
    fired via asyncio.create_task from the BUSY_RECOVERY branch. Opens its
    own DB session (the pipeline's own session is already closed/closing by
    the time this runs, same reasoning as tool_handlers._send_escalation_*
    and call_coordinator's own release() -- this must not depend on a
    session whose lifetime is tied to the rejected call's websocket).

    Returns None (logs, never raises) on any failure -- a broken recovery
    flow must never be capable of taking down anything else; there is no
    live call left to protect by propagating an exception here.
    """
    try:
        async with AsyncSessionLocal() as db:
            return await _handle_busy_recovery(db, host_user_id, property_id, caller_number, dialed_number)
    except Exception:
        logger.exception(
            "Busy-recovery handling failed for host %s, property %s, caller %s",
            host_user_id,
            property_id,
            caller_number,
        )
        return None


async def _handle_busy_recovery(
    db: AsyncSession,
    host_user_id: uuid.UUID,
    property_id: uuid.UUID | None,
    caller_number: str | None,
    dialed_number: str | None,
) -> RecoveryMetadata | None:
    host = await db.get(User, host_user_id)
    if host is None:
        logger.warning("Busy-recovery: host %s not found, nothing to do", host_user_id)
        return None
    # Captured now, as a plain value, before any commit happens on `db`
    # below (get_or_create_guest_profile/upsert_lead/create_notification
    # each commit independently) -- reading an ORM attribute off an object
    # loaded on this session AFTER a same-session commit was confirmed live
    # to risk an implicit lazy-reload outside a valid greenlet context (see
    # app/voice/pipeline.py's busy_recovery_property_id, which hit exactly
    # this against a different object/session in the same call chain).
    host_phone = host.phone

    property_ = await db.get(Property, property_id) if property_id is not None else None
    property_name = (property_.display_name or property_.name) if property_ is not None else None

    # Same lookup-or-create GuestProfile call every voice-pipeline entry
    # point already uses (see call_service.get_or_create_guest_profile) --
    # scoped by (phone, host_id), so a repeat busy-rejected caller resolves
    # to the SAME guest profile a real answered call from them would have.
    # None (not an error) for an anonymous/unresolvable caller_number --
    # everything below degrades gracefully to a guest-less Lead/Notification,
    # same as escalate_to_host already tolerates.
    guest = await call_service.get_or_create_guest_profile(db, caller_number, host_user_id)

    # Phase 6: only guest_memory_service.py (a completed call's own
    # on_pipeline_finished path) sets this normally -- a busy-rejected call
    # never reaches that code, so without this a guest whose FIRST-EVER
    # contact with this host was a busy rejection would have no
    # last_property_id at all. whatsapp_reply_service.py's Property/Pricing/
    # Photos menu options resolve the property via this field (reusing
    # GuestProfile, not inventing a new property reference), so it must be
    # set here too. Goes through guest_memory_service.set_last_property
    # (that module owns this field's write rule), not a direct assignment --
    # cleanup-pass fix for what used to reach into GuestProfile's internals
    # from outside its owning service.
    if guest is not None:
        guest_memory_service.set_last_property(guest, property_.id if property_ is not None else None)

    reason = (
        f"Missed call to {property_name} -- line was busy with another guest"
        if property_name
        else f"Missed call to {dialed_number or 'a lead line'} -- line was busy with another guest"
    )

    # Reuses the guest's existing open/contacted Lead if one already exists
    # (lead_service._get_or_create_lead_for_call, called via upsert_lead) --
    # a guest rejected on a second or third attempt lands on the same Lead,
    # not a fragmented new row per attempt. call_session_id=None throughout:
    # a rejected call never gets a CallSession (see pipeline.py's
    # BUSY_RECOVERY branch, which returns before get_or_create_call_session).
    lead = await lead_service.upsert_lead(
        db,
        host_user_id,
        call_session_id=None,
        guest_profile_id=guest.id if guest is not None else None,
        phone=caller_number,
        properties_discussed=[property_name] if property_name else None,
        recovery_reason=RECOVERY_REASON_BUSY_CALL,
        conversation_summary=reason,
        next_follow_up="Call back guest -- previous call was missed due to a busy line",
    )

    notification = await notification_service.create_notification(
        db,
        channel=NOTIFICATION_CHANNEL_BUSY_RECOVERY,
        property_id=property_id,
        call_session_id=None,
        lead_id=lead.id,
        urgency="medium",
        message=reason + (f" | Guest: {caller_number}" if caller_number else " | Guest: not captured"),
    )

    # Guest-facing WhatsApp: the guest has no other way to reach anyone right
    # now (they were just hung up on), so this -- not email -- is the
    # primary channel, same as handle_send_whatsapp's own guest-facing send.
    # Detached, own asyncio.create_task, same reasoning as module docstring:
    # a slow/failed Twilio call must not delay this function's remaining
    # work or, transitively, anything awaiting handle_busy_recovery's task.
    # Same template-preferred/plain-text-fallback branching as
    # tool_handlers._send_escalation_whatsapp -- "reuse existing template
    # architecture" means this shape, not just this one template.
    if caller_number:
        asyncio.create_task(_send_guest_recovery_whatsapp(caller_number, property_name))

        if host_phone:
            asyncio.create_task(_send_host_recovery_whatsapp(host_phone, property_name, dialed_number, caller_number))

    return RecoveryMetadata(
        lead_id=lead.id,
        guest_profile_id=guest.id if guest is not None else None,
        notification_id=notification.id,
        host_user_id=host_user_id,
        property_id=property_id,
        caller_number=caller_number,
        rejected_at=datetime.now(timezone.utc),
    )


async def _send_guest_recovery_whatsapp(to_phone: str, property_name: str | None) -> None:
    if settings.twilio_busy_recovery_template_sid:
        await twilio_client.send_whatsapp_template_best_effort(
            to_phone,
            settings.twilio_busy_recovery_template_sid,
            {"1": property_name or "us"},
        )
    else:
        await twilio_client.send_whatsapp_best_effort(to_phone, _guest_recovery_whatsapp_text(property_name))


async def _send_host_recovery_whatsapp(
    to_phone: str, property_name: str | None, dialed_number: str | None, caller_number: str
) -> None:
    # Reuses the EXISTING mira_escalation template (same one
    # tool_handlers._send_escalation_whatsapp uses) rather than provisioning
    # a second host-facing template -- this message and a real escalation
    # are the same shape from the host's point of view (urgency + property +
    # issue + summary + guest, with a "Go to Dashboard" button), so a new
    # template here would duplicate, not reuse, existing template
    # architecture. Field mapping matches the template's own labels exactly
    # (scripts/create_escalation_template.py's _BODY: {{3}}=Property,
    # {{4}}=Issue, {{5}}=Summary) -- {{2}} combines with the template's own
    # literal " ESCALATION" suffix, same as a real urgency value would.
    # Falls back to the same plain-text shape as before when unconfigured.
    dashboard_url = f"{settings.frontend_base_url}/dashboard/leads"
    property_label = property_name or dialed_number or "a lead line"
    if settings.twilio_escalation_template_sid:
        await twilio_client.send_whatsapp_template_best_effort(
            to_phone,
            settings.twilio_escalation_template_sid,
            {
                "1": "\U0001F7E0",
                "2": "MISSED CALL",
                "3": property_label,
                "4": "Line was busy with another guest",
                "5": "Not provided",
                "6": caller_number,
            },
        )
    else:
        host_text = (
            f"\U0001F7E0 *MISSED CALL (line busy)*\n*Property:* {property_label}\n"
            f"*Guest:* {caller_number}\n\n{dashboard_url}"
        )
        await twilio_client.send_whatsapp_best_effort(to_phone, host_text)
