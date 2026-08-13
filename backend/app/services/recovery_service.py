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
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.integrations import twilio_client
from app.models.lead import Lead
from app.models.property import Property
from app.models.user import User
from app.schemas.lead import RecoveryReason
from app.services import call_service, guest_memory_service, lead_service, notification_service
from app.services.lead_service import _REUSABLE_LEAD_STATUSES
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
NOTIFICATION_CHANNEL_AVAILABILITY = "availability_notification"

# How long a busy-recovery lead stays eligible for the "Mira is available
# now" follow-up after the busy call itself. Deliberately NOT
# whatsapp_reply_service.RECOVERY_REPLY_WINDOW (72h) -- that window answers
# a different question ("is a reply still about a real, ongoing
# conversation," where a guest replying a day or two later is completely
# normal). This window answers "is 'available now' still TRUE and useful,"
# which decays much faster: the whole premise (see BUSY_MESSAGE_TEXT,
# app/voice/ringing_audio.py -- "call back in 5 mins") is that Mira frees up
# again within minutes, not hours. No existing precedent for this specific
# question in the codebase (checked: the only other window is the 72h reply
# one, which measures something else) -- 30 minutes is a conservative
# choice: generous enough to cover Mira working through a short queue of
# several busy calls in sequence, tight enough that a guest is never told
# "available now" long after they've plausibly moved on or called someone
# else. A guest whose busy call is older than this when Mira frees up
# simply never gets the availability message -- their busy_recovery WhatsApp
# (the numbered menu, sent immediately at rejection time) is still their one
# channel, unchanged.
AVAILABILITY_WINDOW = timedelta(minutes=30)

# How long a "processing" claim is allowed to sit unresolved before being
# treated as abandoned (worker crash/restart) and reclaimable by a later
# run -- see process_availability_recovery's claim query. Comfortably above
# how long one WhatsApp send should ever take (send_whatsapp_best_effort's
# own httpx timeout is 15s), short enough that a genuine crash doesn't
# leave a guest un-notified for long.
STALE_CLAIM_THRESHOLD = timedelta(minutes=2)

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
    # busy_recovery_availability_status="pending" + busy_recovery_at=now:
    # arms this lead for process_availability_recovery (fired when
    # CallCoordinator's lease for this host/property is next actually
    # released -- see app/voice/pipeline.py's _run_pipeline finally block).
    # Deliberately re-armed to "pending" + a fresh timestamp on EVERY busy
    # rejection, including a guest's second/third attempt reusing the same
    # Lead (see upsert_lead's reuse logic above) -- a guest who calls again
    # while still busy is still waiting, so a stale "already notified" from
    # an earlier rejection must not suppress a real, current follow-up; the
    # freshest busy_recovery_at is also what AVAILABILITY_WINDOW should
    # measure against, not the first rejection's. Separate from
    # Lead.status (sales lifecycle) and recovery_reason (why, not
    # follow-up state) -- see app/models/lead.py's own comment.
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
        busy_recovery_availability_status="pending",
        busy_recovery_at=datetime.now(timezone.utc),
    )
    logger.info(
        "busy_recovery_created host_user_id=%s property_id=%s lead_id=%s",
        host_user_id,
        property_id,
        lead.id,
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


# Set as Lead.next_follow_up by handle_busy_recovery above -- reused here
# (not a new field) as the "has the guest replied since we last messaged
# them" signal: whatsapp_reply_service._notify_host_of_reply overwrites
# next_follow_up the moment a real inbound reply comes in (see that
# module's own comment). If it no longer matches this exact string, a
# reply happened and the guest is being actively handled -- sending
# "Mira is available now" on top of that would be a redundant, confusing
# duplicate, not a new conversation opener.
_AWAITING_CALLBACK_FOLLOW_UP = "Call back guest -- previous call was missed due to a busy line"

_AVAILABILITY_WHATSAPP_TEXT = "Hi! \U0001F44B Mira is available now.\n\nYou can call back whenever you're ready."


async def process_availability_recovery(host_user_id: uuid.UUID, property_id: uuid.UUID | None) -> None:
    """Entry point -- the only function app/voice/pipeline.py should call
    once a CallCoordinator lease for (host_user_id, property_id) has
    actually been released (see _run_pipeline's finally block: this fires
    ONLY when call_coordinator.release() returns True, i.e. THIS call's
    token really did free the lease -- never on a no-op/stale-token
    release, and never speculatively on every call teardown). CallCoordinator
    itself never calls this and never imports this module -- see that
    module's own docstring; it has no idea this function exists.

    Finds every pending busy-recovery Lead for this exact (host, property)
    still within AVAILABILITY_WINDOW, atomically claims each one (pending ->
    processing, or a stale processing -> processing, see the claim query
    below), and sends its WhatsApp message -- AWAITED, not its own nested
    asyncio.create_task, unlike handle_busy_recovery's guest/host sends --
    deliberately: this function needs to know whether the send actually
    succeeded before marking a lead "notified" (see _notify_availability),
    which a fire-and-forget send can never report back. This function
    ITSELF is still the detached unit of work: the caller (_run_pipeline's
    finally block) fires process_availability_recovery via
    _spawn_background_task, never awaits it, so however long this takes
    (one WhatsApp round trip per eligible guest, sequentially) never delays
    call teardown -- it only delays how soon THIS already-independent
    background task finishes, exactly like every other spawned task in
    app/voice/pipeline.py.

    Opens its own DB session, same reasoning as handle_busy_recovery: the
    caller's own session may already be closed/closing by the time this
    runs. Never raises -- logs and returns on any failure, matching every
    other detached recovery entry point in this module.
    """
    try:
        async with AsyncSessionLocal() as db:
            await _process_availability_recovery(db, host_user_id, property_id)
    except Exception:
        logger.exception(
            "Availability recovery processing failed for host %s, property %s", host_user_id, property_id
        )


async def _process_availability_recovery(
    db: AsyncSession, host_user_id: uuid.UUID, property_id: uuid.UUID | None
) -> None:
    logger.info("availability_processing_started host_user_id=%s property_id=%s", host_user_id, property_id)

    now = datetime.now(timezone.utc)
    earliest_eligible = now - AVAILABILITY_WINDOW
    stale_claim_before = now - STALE_CLAIM_THRESHOLD

    # Candidate scope: any lead for this host/property that could plausibly
    # still need the message. This SELECT does not itself claim anything
    # (no FOR UPDATE, no state change) -- it is only used to know which
    # ids to attempt to claim below; the atomic UPDATE...RETURNING per id
    # is what actually enforces exactly-one-claimant, so a stale/racing
    # read here is harmless (see _claim_lead's own comment).
    candidates_stmt = select(Lead.id).where(
        Lead.user_id == host_user_id,
        Lead.recovery_reason.is_not(None),
        Lead.status.in_(_REUSABLE_LEAD_STATUSES),
        Lead.busy_recovery_at.is_not(None),
        Lead.busy_recovery_at >= earliest_eligible,
        Lead.busy_recovery_availability_status.in_(("pending", "processing")),
    )
    if property_id is not None:
        # Lead has no property_id column of its own -- property is recorded
        # on GuestProfile.last_property_id instead (see handle_busy_recovery,
        # which sets it via guest_memory_service.set_last_property at
        # rejection time, precisely so this join has something reliable to
        # read). Deliberately NOT a join through Notification.property_id
        # (an earlier version of this code used that): a guest reusing the
        # same open Lead across busy rejections for two DIFFERENT properties
        # (see upsert_lead's reuse -- matched by guest_profile_id only, not
        # property) accumulates one busy_recovery Notification per property,
        # so a Notification-based filter could match a stale property from
        # an earlier rejection instead of the guest's CURRENT one.
        # GuestProfile.last_property_id is overwritten on every rejection
        # (see set_last_property), so it always reflects the most recent
        # busy call -- the same one busy_recovery_at was just refreshed
        # for -- with no multi-row ambiguity. None (Lead Agent / portfolio-
        # wide line, no single property) skips this filter entirely -- every
        # pending lead for the host qualifies, same as how CallCoordinator's
        # own NIL_PROPERTY_ID lease scoping already treats a None property
        # as "the whole host line," not one property among many.
        from app.models.guest_profile import GuestProfile

        candidates_stmt = candidates_stmt.join(GuestProfile, Lead.guest_profile_id == GuestProfile.id).where(
            GuestProfile.last_property_id == property_id
        )

    candidate_ids = list((await db.scalars(candidates_stmt)).all())

    for lead_id in candidate_ids:
        lead = await _claim_lead(db, lead_id, stale_claim_before)
        if lead is None:
            continue  # lost the claim race, or no longer eligible -- not an error
        logger.info(
            "availability_candidate_found host_user_id=%s property_id=%s lead_id=%s",
            host_user_id,
            property_id,
            lead_id,
        )
        await _notify_availability(db, lead, property_id)


async def _claim_lead(db: AsyncSession, lead_id: uuid.UUID, stale_claim_before: datetime) -> Lead | None:
    """The actual concurrency-safety mechanism: ONE atomic
    UPDATE ... WHERE ... RETURNING, not a SELECT-then-UPDATE (which would
    let two concurrent callers both read "pending" and both proceed to
    send). Postgres only ever matches/updates/returns a row for whichever
    transaction's UPDATE commits first for that row; a second concurrent
    UPDATE against the same lead_id with the same WHERE clause simply
    matches zero rows once the first has committed the status flip away
    from "pending"/stale-"processing" -- there is no window where both
    could see it as claimable. This is the same class of guarantee
    call_coordinator.py's Lua-script compare-and-swap gives for Redis, via
    Postgres's own row-level locking instead (see that module's docstring
    for the analogous reasoning).

    Matches "pending" OR a "processing" row whose claimed_at is older than
    stale_claim_before -- the crash-recovery case: a worker that claimed
    this row and died before sending leaves it stuck in "processing"
    otherwise, forever. Re-claiming a stale "processing" row is exactly as
    safe as claiming a fresh "pending" one -- both go through this same
    atomic UPDATE, so a truly-still-in-flight (non-stale) "processing" row
    can never be double-claimed.

    Returns the freshly-claimed Lead (already re-read after the UPDATE) on
    success, or None if the claim was lost (another worker got there
    first) or the row no longer matches the WHERE clause at all (e.g. the
    host closed/booked it since the candidate SELECT ran, or a reply
    already arrived -- see _notify_availability's own additional guest-
    state check for the reply case specifically, since that one can't be
    expressed as a single busy_recovery_availability_status value).
    """
    stmt = (
        update(Lead)
        .where(
            Lead.id == lead_id,
            (Lead.busy_recovery_availability_status == "pending")
            | (
                (Lead.busy_recovery_availability_status == "processing")
                & (Lead.busy_recovery_claimed_at < stale_claim_before)
            ),
        )
        .values(busy_recovery_availability_status="processing", busy_recovery_claimed_at=datetime.now(timezone.utc))
        .execution_options(synchronize_session=False)
    )
    result = await db.execute(stmt)
    await db.commit()
    if result.rowcount == 0:
        return None
    return await db.get(Lead, lead_id)


async def _notify_availability(db: AsyncSession, lead: Lead, property_id: uuid.UUID | None) -> None:
    """Sends the "Mira is available now" WhatsApp to a lead this process
    has already exclusively claimed (see _claim_lead) -- final guest-state
    checks happen here, AFTER the claim, since they need the freshest
    possible read of fields the claim's own WHERE clause can't express
    (next_follow_up, phone resolution) and a claim already guarantees no
    other worker is concurrently evaluating this same lead.
    """
    if lead.next_follow_up != _AWAITING_CALLBACK_FOLLOW_UP:
        # The guest already replied on WhatsApp (whatsapp_reply_service.py
        # overwrote next_follow_up) or a host/other flow touched it --
        # either way, this is no longer "guest silently waiting," so
        # sending an unprompted "available now" would be a confusing
        # duplicate on top of a conversation already in progress. Release
        # the claim back to a terminal, non-retried state rather than
        # leaving it stuck "processing" -- reusing "notified" for this
        # (not a new status value) since the practical effect is identical:
        # nothing should attempt this lead again.
        lead.busy_recovery_availability_status = "notified"
        lead.busy_recovery_claimed_at = None
        await db.commit()
        logger.info("availability_notification_skipped lead_id=%s reason=guest_already_engaged", lead.id)
        return

    phone = lead.phone
    if not phone:
        lead.busy_recovery_availability_status = "notified"
        lead.busy_recovery_claimed_at = None
        await db.commit()
        logger.info("availability_notification_skipped lead_id=%s reason=no_phone_on_lead", lead.id)
        return

    # Deliberately the RAISING variants (send_whatsapp_message /
    # send_whatsapp_template), NOT send_whatsapp_best_effort /
    # send_whatsapp_template_best_effort -- those already swallow a Twilio
    # failure internally (log, return, never raise; see twilio_client.py's
    # own docstrings), which would make it impossible for this function to
    # ever know a send failed and honor "never mark notified before
    # WhatsApp actually accepts the message." This is the one guest-facing
    # WhatsApp send in this module that needs to observe success/failure,
    # unlike handle_busy_recovery's own guest/host sends (those are truly
    # fire-and-forget with nothing to roll back if they fail).
    try:
        if settings.twilio_availability_template_sid:
            result = await twilio_client.send_whatsapp_template(
                phone, settings.twilio_availability_template_sid, {}
            )
        else:
            result = await twilio_client.send_whatsapp_message(phone, _AVAILABILITY_WHATSAPP_TEXT)
    except twilio_client.TwilioError:
        lead.busy_recovery_availability_status = "pending"
        lead.busy_recovery_claimed_at = None
        await db.commit()
        logger.exception("availability_notification_failed lead_id=%s", lead.id)
        return

    if result.get("status") == "skipped":
        # Twilio not configured at all (no account_sid/auth_token) -- not a
        # transient failure worth retrying every run, but also not a real
        # send, so "notified" would be a lie. Left "processing" is wrong
        # too (nothing will ever un-stick it without config changing) --
        # back to "pending" is the honest state: config gets fixed, next
        # trigger tries again, same as a real failure.
        lead.busy_recovery_availability_status = "pending"
        lead.busy_recovery_claimed_at = None
        await db.commit()
        logger.info("availability_notification_skipped lead_id=%s reason=twilio_not_configured", lead.id)
        return

    # Marked "notified" only now -- AFTER send_whatsapp_message/
    # send_whatsapp_template has returned a real "sent" result without
    # raising. This is the actual "WhatsApp successfully accepted the
    # message" confirmation, not an assumption.
    lead.busy_recovery_availability_status = "notified"
    lead.busy_recovery_claimed_at = None
    await db.commit()
    logger.info("availability_notification_sent lead_id=%s", lead.id)

    # Ordinary Notification (channel="availability_notification"), same
    # "already visible through the existing dashboard/SSE stream with zero
    # new UI" contract as the original busy_recovery Notification (see
    # module docstring) -- this is what lets the dashboard eventually show
    # the full funnel (Busy recovery -> WhatsApp sent -> Waiting for
    # availability -> Availability message sent), same read path
    # (list_notifications/GET /notifications/stream) every other channel
    # already uses. property_id passed through as-is (can be None for a
    # Lead Agent/portfolio-wide line) -- same as _handle_busy_recovery's own
    # Notification above; list_notifications's lead_id-owned-by-user OR
    # fallback (see that function's own comment) already covers a NULL
    # property_id correctly, same as every other recovery notification.
    await notification_service.create_notification(
        db,
        channel=NOTIFICATION_CHANNEL_AVAILABILITY,
        property_id=property_id,
        call_session_id=None,
        lead_id=lead.id,
        urgency="low",
        message="Mira is available again -- sent the guest a follow-up WhatsApp",
    )
