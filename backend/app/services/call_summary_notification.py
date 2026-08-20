"""Host-facing "call summary" WhatsApp -- fires once, after a real call
finishes, alongside the existing in-app Notification/dashboard record.
Deliberately its own module rather than folded into recovery_service.py or
guest_calling_notification.py -- this fires on the OPPOSITE end of the
lifecycle those two cover (recovery_service: a call that never reached
Mira; guest_calling_notification: a call that just started), but copies
their exact shape: called fire-and-forget via asyncio.create_task from
app/voice/pipeline.py's on_pipeline_finished, opening its own
AsyncSessionLocal() since the pipeline's own `finalize_db` session has
already closed by the time this runs (same reasoning as those two
modules' own docstrings). Never raises into the caller -- a broken
notification here must never affect call teardown, which has already
happened by the time this runs anyway.

Reuses, never reimplements: CallSession.ai_summary (schemas/call_summary.py's
CallSummary, written by call_summary_service.summarize_call via
on_pipeline_finished) is the sole source of the structured booking_snapshot/
conversation_summary this message reads -- nothing here re-derives facts
from the transcript itself. "Escalation raised" is read from whether an
escalation Notification exists for this call_session_id (the same row
tool_handlers.handle_escalate_to_host already writes), not from
Lead.escalated (which can carry over from an EARLIER call on a reused
lead and would misreport this specific call). app/integrations/
twilio_client.py's send_whatsapp_template_best_effort/
send_whatsapp_best_effort is the sole WhatsApp send path.
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.integrations import twilio_client
from app.models.call_session import CallSession
from app.models.notification import Notification
from app.models.user import User
from app.schemas.call_summary import CallSummary

logger = logging.getLogger(__name__)

# Conversation_summary is already a tight 3-5 sentences (see
# call_summary_service.py's own prompt), but there is no hard length
# guarantee on it -- truncated defensively so a single long summary can
# never push the WhatsApp message past a sane length or blow out a Content
# Template's own body-length limit.
_MAX_SUMMARY_CHARS = 400


async def notify_host_of_call_summary(
    call_session_id: uuid.UUID,
    host_user_id: uuid.UUID,
    property_name: str | None,
) -> None:
    """Entry point -- the only function app/voice/pipeline.py should call,
    fired via asyncio.create_task from on_pipeline_finished once
    call_summary_service.summarize_call/call_service.set_call_summary have
    already run for this call. Never raises (logs, swallows)."""
    try:
        async with AsyncSessionLocal() as db:
            await _notify_host_of_call_summary(db, call_session_id, host_user_id, property_name)
    except Exception:
        logger.exception("Call summary notification failed for call_session_id=%s", call_session_id)


async def _notify_host_of_call_summary(
    db: AsyncSession,
    call_session_id: uuid.UUID,
    host_user_id: uuid.UUID,
    property_name: str | None,
) -> None:
    host = await db.get(User, host_user_id)
    if host is None or not host.phone:
        logger.info("call_summary_notification_skipped call_session_id=%s reason=no_host_phone", call_session_id)
        return

    call_session = await db.get(CallSession, call_session_id)
    if call_session is None or call_session.ai_summary is None:
        # Should not happen on the normal on_pipeline_finished path (this is
        # called right after set_call_summary persists it), but a call
        # summary is not something worth a host WhatsApp about if it's
        # somehow missing -- degrade silently rather than send a
        # content-free message.
        logger.info("call_summary_notification_skipped call_session_id=%s reason=no_ai_summary", call_session_id)
        return

    summary = CallSummary.model_validate(call_session.ai_summary)
    snapshot = summary.booking_snapshot

    escalated = await db.scalar(
        select(Notification.id).where(
            Notification.call_session_id == call_session_id, Notification.channel == "escalation"
        )
    )
    escalation_raised = escalated is not None

    property_label = ", ".join(snapshot.property) if snapshot.property else (property_name or "Not discussed")
    guest_name = snapshot.guest_name if snapshot.guest_name and snapshot.guest_name != "Unknown" else "Not provided"
    guests = snapshot.guests if snapshot.guests and snapshot.guests != "Unknown" else "Not provided"
    check_in = snapshot.check_in or "Not provided"
    check_out = snapshot.check_out or "Not provided"
    conversation_summary = summary.conversation_summary[:_MAX_SUMMARY_CHARS]
    call_url = f"{settings.frontend_base_url}/dashboard/calls/{call_session_id}"

    if settings.twilio_call_summary_template_sid:
        await twilio_client.send_whatsapp_template_best_effort(
            host.phone,
            settings.twilio_call_summary_template_sid,
            {
                "1": property_label,
                "2": guest_name,
                "3": guests,
                "4": check_in,
                "5": check_out,
                "6": "Yes" if escalation_raised else "No",
                "7": conversation_summary,
                "8": call_url,
            },
        )
    else:
        text = (
            "\U0001F4DE *Call summary*\n"
            f"*Property:* {property_label}\n"
            f"*Guest:* {guest_name}\n"
            f"*Guests:* {guests}\n"
            f"*Check-in:* {check_in}\n"
            f"*Check-out:* {check_out}\n"
            f"*Escalation raised:* {'Yes' if escalation_raised else 'No'}\n\n"
            f"{conversation_summary}\n\n"
            f"{call_url}"
        )
        await twilio_client.send_whatsapp_best_effort(host.phone, text)

    logger.info("call_summary_notification_sent call_session_id=%s host_user_id=%s", call_session_id, host_user_id)
